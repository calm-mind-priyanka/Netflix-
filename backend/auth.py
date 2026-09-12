import base64, hashlib, hmac, os, secrets, time
from .config import SITE_SECRET

def _sign(payload):
    return hmac.new(SITE_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()

def make_stream_token(file_id, ttl=300):
    exp=int(time.time())+ttl; payload=f"{file_id}:{exp}"
    return base64.urlsafe_b64encode(f"{payload}:{_sign(payload)}".encode()).decode().rstrip("=")

def validate_stream_token(token,file_id):
    try:
        raw=base64.urlsafe_b64decode(token+"="*(-len(token)%4)).decode(); fid,exp,sig=raw.split(":",2); payload=f"{fid}:{exp}"
        return hmac.compare_digest(sig,_sign(payload)) and fid==file_id and int(exp)>=int(time.time())
    except Exception:return False

def hash_admin_password(password, iterations=210000):
    salt=secrets.token_hex(16)
    digest=hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), iterations).hex()
    return f"pbkdf2_sha256${iterations}${salt}${digest}"

def verify_admin_password(password, stored):
    try:
        algo,it,salt,digest=stored.split("$",3)
        if algo!="pbkdf2_sha256": return False
        calc=hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(it)).hex()
        return hmac.compare_digest(calc,digest)
    except Exception:return False

def make_admin_session(ttl=43200):
    exp=int(time.time())+ttl
    nonce=secrets.token_urlsafe(24)
    payload=f"admin:{exp}:{nonce}"
    return base64.urlsafe_b64encode(f"{payload}:{_sign(payload)}".encode()).decode().rstrip("=")

def validate_admin_session(token):
    try:
        raw=base64.urlsafe_b64decode(token+"="*(-len(token)%4)).decode()
        role,exp,nonce,sig=raw.split(":",3); payload=f"{role}:{exp}:{nonce}"
        return role=="admin" and int(exp)>=int(time.time()) and hmac.compare_digest(sig,_sign(payload))
    except Exception:return False
