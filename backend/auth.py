import base64
import hashlib
import hmac
import secrets
import time

from .config import SITE_SECRET, STREAM_TOKEN_TTL

def _sign(payload):
    if not SITE_SECRET:
        raise RuntimeError("SITE_SECRET is not configured")
    return hmac.new(
        SITE_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

def make_stream_token(file_id, ttl=None):
    ttl = STREAM_TOKEN_TTL if ttl is None else max(1, int(ttl))
    exp = int(time.time()) + ttl
    payload = f"{file_id}:{exp}"
    token = f"{payload}:{_sign(payload)}"
    return base64.urlsafe_b64encode(token.encode("utf-8")).decode("ascii").rstrip("=")

def validate_stream_token(token, file_id):
    try:
        raw = base64.urlsafe_b64decode(
            token + "=" * (-len(token) % 4)
        ).decode("utf-8")
        fid, exp, signature = raw.split(":", 2)
        payload = f"{fid}:{exp}"
        return (
            hmac.compare_digest(signature, _sign(payload))
            and hmac.compare_digest(fid, str(file_id))
            and int(exp) >= int(time.time())
        )
    except Exception:
        return False

def hash_admin_password(password, iterations=210000):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        str(password).encode("utf-8"),
        salt,
        iterations,
    ).hex()
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest}"

def verify_admin_password(password, stored):
    """Support the configured plaintext password or the helper's PBKDF2 hash."""
    try:
        password = str(password or "")
        stored = str(stored or "")
        if not stored:
            return False

        if stored.startswith("pbkdf2_sha256$"):
            scheme, iterations, salt_hex, expected = stored.split("$", 3)
            if scheme != "pbkdf2_sha256":
                return False
            iterations = int(iterations)
            salt = bytes.fromhex(salt_hex)
            actual = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                salt,
                iterations,
            ).hex()
            return hmac.compare_digest(actual, expected)

        return hmac.compare_digest(password.encode("utf-8"), stored.encode("utf-8"))
    except Exception:
        return False

def make_admin_session(ttl=43200):
    exp = int(time.time()) + ttl
    nonce = secrets.token_urlsafe(24)
    payload = f"admin:{exp}:{nonce}"
    return base64.urlsafe_b64encode(
        f"{payload}:{_sign(payload)}".encode("utf-8")
    ).decode("ascii").rstrip("=")

def validate_admin_session(token):
    try:
        raw = base64.urlsafe_b64decode(
            str(token or "") + "=" * (-len(str(token or "")) % 4)
        ).decode("utf-8")
        role, exp, nonce, signature = raw.split(":", 3)
        payload = f"{role}:{exp}:{nonce}"
        return (
            role == "admin"
            and int(exp) >= int(time.time())
            and hmac.compare_digest(signature, _sign(payload))
        )
    except Exception:
        return False
