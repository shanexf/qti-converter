"""
User accounts and credit balances, backed by SQLite.

Uses Python's built-in sqlite3 — no extra dependency, so it can't break the
Railway build. For this app's scale (a handful to a few hundred teachers),
SQLite is genuinely fine as long as the database FILE lives on a persistent
Railway Volume (see README) — without a volume, the file resets on every
redeploy and everyone's accounts/credits would be wiped.

Three account roles:
  - "standard"     — signs up publicly, gets a small free trial (SIGNUP_FREE_CREDITS),
                      then must buy credit packs. This is everyone by default.
  - "free_monthly" — a fixed number of questions free every calendar month
                      (does not roll over), falls back to purchased credits
                      once the monthly quota is used up. For invited teachers.
  - "admin"        — unlimited, no deduction at all. For you.

Only "standard" accounts are created via public signup. "admin" and
"free_monthly" accounts are created once via create_account.py (see README).
"""
import os
import sqlite3
import time
import datetime
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", "./keyform.db")
SIGNUP_FREE_CREDITS = int(os.environ.get("SIGNUP_FREE_CREDITS", "10"))


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _current_period() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m")


def _ensure_column(conn, table: str, column: str, column_def: str):
    """Adds a column if it doesn't already exist — safe to call on a fresh
    database or one that already has real accounts in it (e.g. production)."""
    existing = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_def}")


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'standard',
                credits INTEGER NOT NULL DEFAULT 0,
                monthly_quota_limit INTEGER NOT NULL DEFAULT 0,
                monthly_quota_used INTEGER NOT NULL DEFAULT 0,
                monthly_quota_period TEXT NOT NULL DEFAULT '',
                full_name TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                occupation TEXT NOT NULL DEFAULT '',
                institution TEXT NOT NULL DEFAULT '',
                marketing_consent INTEGER NOT NULL DEFAULT 0,
                privacy_accepted INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            )
        """)
        # Added after the initial release — safe on both fresh and existing databases
        _ensure_column(conn, "users", "country", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "users", "bundesland", "TEXT NOT NULL DEFAULT ''")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS credit_purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                pack TEXT NOT NULL,
                credits_added INTEGER NOT NULL,
                stripe_session_id TEXT UNIQUE NOT NULL,
                created_at REAL NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS admin_adjustments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                reason TEXT NOT NULL,
                admin_email TEXT NOT NULL,
                created_at REAL NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)


def create_user(email: str, password_hash: str, role: str = "standard",
                 credits: int = None, monthly_quota_limit: int = 0,
                 full_name: str = "", phone: str = "", country: str = "",
                 bundesland: str = "", occupation: str = "", institution: str = "",
                 marketing_consent: bool = False, privacy_accepted: bool = False):
    if credits is None:
        credits = SIGNUP_FREE_CREDITS if role == "standard" else 0
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, role, credits, monthly_quota_limit, "
            "monthly_quota_used, monthly_quota_period, full_name, phone, country, bundesland, "
            "occupation, institution, marketing_consent, privacy_accepted, created_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (email.lower().strip(), password_hash, role, credits, monthly_quota_limit,
             _current_period(), full_name.strip(), phone.strip(), country.strip(),
             bundesland.strip(), occupation, institution.strip(), int(bool(marketing_consent)),
             int(bool(privacy_accepted)), time.time()),
        )
        return cur.lastrowid


def get_user_by_email(email: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email.lower().strip(),)
        ).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def list_users():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, email, role, credits, monthly_quota_limit, monthly_quota_used, "
            "monthly_quota_period, full_name, phone, country, bundesland, occupation, "
            "institution, marketing_consent, created_at FROM users ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]


def _roll_quota_if_new_period(conn, user: dict):
    """Resets the monthly quota if we've entered a new calendar month.
    Must be called with an open connection so the reset is part of the same
    transaction as whatever charge is about to happen."""
    period = _current_period()
    if user["monthly_quota_period"] != period:
        conn.execute(
            "UPDATE users SET monthly_quota_used = 0, monthly_quota_period = ? WHERE id = ?",
            (period, user["id"]),
        )
        user["monthly_quota_used"] = 0
        user["monthly_quota_period"] = period


def get_status(user_id: int):
    """Returns a display-friendly summary of what this account can still use,
    after rolling the monthly quota over if a new month has started."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return None
        user = dict(row)
        _roll_quota_if_new_period(conn, user)
        remaining_quota = max(0, user["monthly_quota_limit"] - user["monthly_quota_used"])
        return {
            "role": user["role"],
            "credits": user["credits"],
            "unlimited": user["role"] == "admin",
            "monthly_quota_limit": user["monthly_quota_limit"],
            "monthly_quota_remaining": remaining_quota,
        }


def charge_export(user_id: int, question_count: int):
    """Attempts to charge an export against this account, in priority order:
    admin (free) -> monthly quota -> purchased credits. Returns (allowed, info)
    where info explains what happened (useful for error messages)."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return False, "User not found."
        user = dict(row)

        if user["role"] == "admin":
            return True, "admin (unlimited)"

        _roll_quota_if_new_period(conn, user)
        remaining_quota = max(0, user["monthly_quota_limit"] - user["monthly_quota_used"])
        from_quota = min(remaining_quota, question_count)
        from_credits = question_count - from_quota

        if from_credits > user["credits"]:
            return False, (
                f"Not enough balance: this export needs {question_count} question(s), "
                f"you have {remaining_quota} free this month and {user['credits']} purchased credit(s)."
            )

        if from_quota:
            conn.execute(
                "UPDATE users SET monthly_quota_used = monthly_quota_used + ? WHERE id = ?",
                (from_quota, user_id),
            )
        if from_credits:
            conn.execute(
                "UPDATE users SET credits = credits - ? WHERE id = ?",
                (from_credits, user_id),
            )
        return True, f"charged {from_quota} from monthly quota, {from_credits} from credits"


def update_password(user_id: int, new_password_hash: str):
    with get_conn() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_password_hash, user_id))


def delete_user_by_email(email: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM users WHERE email = ?", (email.lower().strip(),))
        return cur.rowcount > 0


def add_credits(user_id: int, amount: int, pack: str, stripe_session_id: str) -> bool:
    """Idempotent: if this Stripe session was already processed (e.g. a
    webhook retry), this is a no-op and returns False."""
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO credit_purchases (user_id, pack, credits_added, stripe_session_id, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, pack, amount, stripe_session_id, time.time()),
            )
        except sqlite3.IntegrityError:
            return False  # already processed this session
        conn.execute("UPDATE users SET credits = credits + ? WHERE id = ?", (amount, user_id))
        return True


def get_purchases_for_user(user_id: int):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM credit_purchases WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def list_all_purchases():
    """Purchase records joined with the buyer's email, for export/reporting."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT credit_purchases.id, users.email, credit_purchases.pack,
                   credit_purchases.credits_added, credit_purchases.stripe_session_id,
                   credit_purchases.created_at
            FROM credit_purchases
            JOIN users ON users.id = credit_purchases.user_id
            ORDER BY credit_purchases.created_at DESC
        """).fetchall()
        return [dict(r) for r in rows]


def get_account_detail(user_id: int):
    """Full troubleshooting view of one account: profile + status + purchase
    and manual-adjustment history. Never includes the password hash."""
    user = get_user_by_id(user_id)
    if not user:
        return None
    user = dict(user)
    user.pop("password_hash", None)
    with get_conn() as conn:
        adjustments = conn.execute(
            "SELECT * FROM admin_adjustments WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
        ).fetchall()
    return {
        "user": user,
        "status": get_status(user_id),
        "purchases": get_purchases_for_user(user_id),
        "adjustments": [dict(r) for r in adjustments],
    }


def adjust_credits(user_id: int, amount: int, reason: str, admin_email: str):
    """Manually grants (positive amount) or removes (negative amount) credits,
    for support/troubleshooting — e.g. compensating a bug, processing a refund
    outside Stripe. Every adjustment is permanently logged with who did it and
    why. Refuses to push a balance below zero."""
    with get_conn() as conn:
        row = conn.execute("SELECT credits FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return False, "User not found."
        new_balance = row["credits"] + amount
        if new_balance < 0:
            return False, f"That would take the balance below zero (current: {row['credits']})."
        conn.execute("UPDATE users SET credits = ? WHERE id = ?", (new_balance, user_id))
        conn.execute(
            "INSERT INTO admin_adjustments (user_id, amount, reason, admin_email, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, amount, reason, admin_email, time.time()),
        )
        return True, f"New balance: {new_balance}"
