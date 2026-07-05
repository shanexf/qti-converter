import os
import re
import io
from fastapi import FastAPI, UploadFile, File, Request, Header, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from extract import extract_text
from parser import parse_questions
from qti_generator import build_qti_package
from ratelimit import rate_limit
import db
import auth
import billing

MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "10"))

# Tune these via Railway env vars if you need to loosen/tighten them later
RATE_LIMIT_PARSE = int(os.environ.get("RATE_LIMIT_PARSE", "20"))     # per hour, per visitor
RATE_LIMIT_EXPORT = int(os.environ.get("RATE_LIMIT_EXPORT", "15"))   # per hour, per visitor
RATE_LIMIT_AUTH = int(os.environ.get("RATE_LIMIT_AUTH", "10"))       # per hour, per visitor (signup/login)
RATE_LIMIT_WINDOW = 60 * 60

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

db.init_db()

app = FastAPI(title="Doc-to-QTI API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("ALLOWED_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_current_user(authorization: str = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Please log in to continue.")
    token = authorization.removeprefix("Bearer ").strip()
    user_id = auth.decode_token(token)
    if user_id is None:
        raise HTTPException(401, "Your session has expired — please log in again.")
    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(401, "Account not found — please log in again.")
    return user


def require_admin(user=Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "Admin access only.")
    return user


@app.get("/api/health")
def health():
    return {"status": "ok"}


# --- Auth ---

@app.post("/api/auth/signup", dependencies=[
    Depends(rate_limit("auth", RATE_LIMIT_AUTH, RATE_LIMIT_WINDOW)),
])
def signup(body: dict):
    email = (body or {}).get("email", "").strip()
    password = (body or {}).get("password", "")

    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Please enter a valid email address.")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")
    if db.get_user_by_email(email):
        raise HTTPException(409, "An account with that email already exists — try logging in instead.")

    user_id = db.create_user(email, auth.hash_password(password), role="standard")
    token = auth.create_token(user_id)
    return {"token": token, "status": db.get_status(user_id)}


@app.post("/api/auth/login", dependencies=[
    Depends(rate_limit("auth", RATE_LIMIT_AUTH, RATE_LIMIT_WINDOW)),
])
def login(body: dict):
    email = (body or {}).get("email", "").strip()
    password = (body or {}).get("password", "")

    user = db.get_user_by_email(email)
    if not user or not auth.verify_password(password, user["password_hash"]):
        raise HTTPException(401, "Incorrect email or password.")

    token = auth.create_token(user["id"])
    return {"token": token, "status": db.get_status(user["id"])}


@app.get("/api/me")
def me(user=Depends(get_current_user)):
    return {"email": user["email"], "status": db.get_status(user["id"])}


@app.post("/api/auth/change-password")
def change_password(body: dict, user=Depends(get_current_user)):
    current_password = (body or {}).get("current_password", "")
    new_password = (body or {}).get("new_password", "")

    if not auth.verify_password(current_password, user["password_hash"]):
        raise HTTPException(401, "Current password is incorrect.")
    if len(new_password) < 8:
        raise HTTPException(400, "New password must be at least 8 characters.")

    db.update_password(user["id"], auth.hash_password(new_password))
    return {"updated": True}


# --- Core conversion features ---

@app.post("/api/parse", dependencies=[
    Depends(rate_limit("parse", RATE_LIMIT_PARSE, RATE_LIMIT_WINDOW)),
])
async def parse_file(file: UploadFile = File(...), user=Depends(get_current_user)):
    contents = await file.read()
    if len(contents) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"File too large (max {MAX_UPLOAD_MB} MB).")
    try:
        text = extract_text(file.filename, contents)
    except ValueError as e:
        raise HTTPException(400, str(e))
    questions = parse_questions(text)
    return {"questions": questions, "count": len(questions)}


@app.post("/api/export", dependencies=[
    Depends(rate_limit("export", RATE_LIMIT_EXPORT, RATE_LIMIT_WINDOW)),
])
async def export_qti(request: Request, user=Depends(get_current_user)):
    body = await request.json()
    questions = body.get("questions", [])
    version = body.get("qti_version", "2.2")
    if version not in ("2.1", "2.2"):
        version = "2.2"
    if not questions:
        raise HTTPException(400, "No questions provided.")

    allowed, info = db.charge_export(user["id"], len(questions))
    if not allowed:
        raise HTTPException(402, info)

    zip_bytes = build_qti_package(questions, version=version)

    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=qti-{version}-package.zip"},
    )


# --- Billing ---

@app.post("/api/create-checkout-session", dependencies=[
    Depends(rate_limit("checkout", 10, RATE_LIMIT_WINDOW)),
])
def create_checkout_session(body: dict, user=Depends(get_current_user)):
    pack = (body or {}).get("pack", "")
    try:
        url = billing.create_checkout_session(user["id"], pack)
    except (RuntimeError, ValueError) as e:
        raise HTTPException(400, str(e))
    return {"url": url}


@app.post("/api/stripe-webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(default=None)):
    payload = await request.body()
    try:
        billing.handle_webhook_event(payload, stripe_signature)
    except Exception as e:
        raise HTTPException(400, f"Webhook error: {e}")
    return {"received": True}


# --- Admin ---

@app.get("/api/admin/users")
def admin_list_users(admin=Depends(require_admin)):
    return {"users": db.list_users()}


# --- One-time account setup (for creating admin/free_monthly accounts on the
# live server, since `railway run` executes locally, not inside the actual
# container where the database volume lives) ---

SETUP_SECRET = os.environ.get("SETUP_SECRET", "")


@app.post("/api/setup/create-account")
def setup_create_account(body: dict, x_setup_secret: str = Header(default=None)):
    if not SETUP_SECRET or x_setup_secret != SETUP_SECRET:
        raise HTTPException(403, "Setup is disabled or the secret is wrong.")

    email = (body or {}).get("email", "").strip()
    password = (body or {}).get("password", "")
    role = (body or {}).get("role", "standard")
    monthly_quota_limit = int((body or {}).get("monthly_quota_limit", 0))

    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Please provide a valid email address.")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")
    if role not in ("admin", "free_monthly", "standard"):
        raise HTTPException(400, "role must be admin, free_monthly, or standard.")
    if db.get_user_by_email(email):
        raise HTTPException(409, "An account with that email already exists.")

    user_id = db.create_user(
        email, auth.hash_password(password), role=role,
        monthly_quota_limit=monthly_quota_limit,
    )
    user = db.get_user_by_id(user_id)
    user.pop("password_hash", None)
    return {"created": True, "user": user}


@app.post("/api/setup/delete-account")
def setup_delete_account(body: dict, x_setup_secret: str = Header(default=None)):
    """For fixing mistakes (e.g. an account created with placeholder/wrong
    details) — not exposed anywhere in the UI, deliberately."""
    if not SETUP_SECRET or x_setup_secret != SETUP_SECRET:
        raise HTTPException(403, "Setup is disabled or the secret is wrong.")
    email = (body or {}).get("email", "").strip()
    if not email:
        raise HTTPException(400, "email is required.")
    deleted = db.delete_user_by_email(email)
    return {"deleted": deleted}
