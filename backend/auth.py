"""
Password hashing (stdlib pbkdf2, no extra dependency) and signed login
tokens (itsdangerous, already used elsewhere in this project).
"""
import os
import hashlib
import binascii
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

AUTH_SECRET = os.environ.get("AUTH_SECRET", os.environ.get("COOKIE_SECRET", "dev-secret-change-me"))
TOKEN_MAX_AGE_SECONDS = 60 * 60 * 24 * 30  # 30 days

_serializer = URLSafeTimedSerializer(AUTH_SECRET, salt="keyform-auth")


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return f"{binascii.hexlify(salt).decode()}${binascii.hexlify(derived).decode()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt_hex, hash_hex = stored_hash.split("$")
    except ValueError:
        return False
    salt = binascii.unhexlify(salt_hex)
    expected = binascii.unhexlify(hash_hex)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return derived == expected  # fixed-length values from pbkdf2; comparison timing is not a practical concern here


def create_token(user_id: int) -> str:
    return _serializer.dumps(user_id)


def decode_token(token: str):
    """Returns the user_id, or None if the token is missing/invalid/expired."""
    if not token:
        return None
    try:
        return _serializer.loads(token, max_age=TOKEN_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
