import os
from fastapi import FastAPI, UploadFile, File, Request, Header, HTTPException, Response, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from itsdangerous import URLSafeSerializer, BadSignature
import io

from extract import extract_text
from parser import parse_questions
from qti_generator import build_qti_package
from ratelimit import rate_limit
import billing

FREE_LIMIT = int(os.environ.get("FREE_EXPORT_LIMIT", "3"))
COOKIE_SECRET = os.environ.get("COOKIE_SECRET", "dev-secret-change-me")
COOKIE_NAME = "qti_usage"
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "10"))
SITE_ACCESS_CODE = os.environ.get("SITE_ACCESS_CODE", "")

# Tune these via Railway env vars if you need to loosen/tighten them later
RATE_LIMIT_PARSE = int(os.environ.get("RATE_LIMIT_PARSE", "20"))     # per hour, per visitor
RATE_LIMIT_EXPORT = int(os.environ.get("RATE_LIMIT_EXPORT", "15"))   # per hour, per visitor
RATE_LIMIT_WINDOW = 60 * 60

serializer = URLSafeSerializer(COOKIE_SECRET)

app = FastAPI(title="Doc-to-QTI API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("ALLOWED_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


def _read_usage(request: Request) -> int:
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        return 0
    try:
        return int(serializer.loads(raw))
    except BadSignature:
        return 0


def _write_usage(response: Response, count: int):
    response.set_cookie(
        COOKIE_NAME,
        serializer.dumps(str(count)),
        max_age=60 * 60 * 24 * 365,
        httponly=True,
        samesite="lax",
    )


def require_site_code(x_site_code: str = Header(default=None)):
    """No-op unless SITE_ACCESS_CODE is set on Railway — lets you keep the
    app fully open, or lock it to only people you've shared the code with."""
    if SITE_ACCESS_CODE and x_site_code != SITE_ACCESS_CODE:
        raise HTTPException(401, "Missing or incorrect access code.")


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/config")
def get_config():
    """Public info the frontend needs before it can call the protected
    endpoints — just whether a code is required, never the code itself."""
    return {"access_code_required": bool(SITE_ACCESS_CODE)}


@app.post("/api/parse", dependencies=[
    Depends(require_site_code),
    Depends(rate_limit("parse", RATE_LIMIT_PARSE, RATE_LIMIT_WINDOW)),
])
async def parse_file(file: UploadFile = File(...)):
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
    Depends(require_site_code),
    Depends(rate_limit("export", RATE_LIMIT_EXPORT, RATE_LIMIT_WINDOW)),
])
async def export_qti(
    request: Request,
    response: Response,
    x_license_key: str = Header(default=None),
):
    body = await request.json()
    questions = body.get("questions", [])
    version = body.get("qti_version", "2.2")
    if version not in ("2.1", "2.2"):
        version = "2.2"
    if not questions:
        raise HTTPException(400, "No questions provided.")

    has_license = billing.is_license_valid(x_license_key)
    usage = _read_usage(request)

    if not has_license and usage >= FREE_LIMIT:
        raise HTTPException(
            402,
            "Free export limit reached. Upgrade to keep exporting QTI packages.",
        )

    zip_bytes = build_qti_package(questions, version=version)

    if not has_license:
        _write_usage(response, usage + 1)

    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=qti-{version}-package.zip"},
    )


@app.get("/api/usage", dependencies=[Depends(require_site_code)])
def get_usage(request: Request, x_license_key: str = Header(default=None)):
    has_license = billing.is_license_valid(x_license_key)
    usage = _read_usage(request)
    return {
        "used": usage,
        "limit": FREE_LIMIT,
        "unlimited": has_license,
        "remaining": None if has_license else max(0, FREE_LIMIT - usage),
    }


@app.post("/api/create-checkout-session", dependencies=[
    Depends(rate_limit("checkout", 10, RATE_LIMIT_WINDOW)),
])
def create_checkout_session(body: dict = None):
    email = (body or {}).get("email")
    try:
        url = billing.create_checkout_session(customer_email=email)
    except RuntimeError as e:
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
