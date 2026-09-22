import os

def _env(name, default=""):
    value = os.getenv(name)
    return default if value is None else value

def _bool_env(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "enable", "y"}

DATABASE_URI = _env("DATABASE_URI")
DATABASE_URI2 = _env("DATABASE_URI2")
DATABASE_NAME = _env("DATABASE_NAME", "Cluster0")
COLLECTION_NAME = _env("COLLECTION_NAME", "Sandy_files")
MULTIPLE_DB = _bool_env("MULTIPLE_DB", True)

BOT_TOKEN = _env("BOT_TOKEN")
try:
    API_ID = int(_env("API_ID", "0"))
except ValueError:
    API_ID = 0
API_HASH = _env("API_HASH")

SESSION_NAME = _env("MOVIE_SITE_SESSION", "movie_site")
SITE_SECRET = _env("SITE_SECRET")

TMDB_API_KEY = _env("TMDB_API_KEY").strip()
ADMIN_USERNAME = _env("ADMIN_USERNAME", "admin").strip()
ADMIN_PASSWORD = _env("ADMIN_PASSWORD")
ADMIN_PASSWORD_HASH = _env("ADMIN_PASSWORD_HASH")

PUBLIC_URL = _env("PUBLIC_URL")
try:
    CATALOG_TTL = max(0, int(_env("CATALOG_TTL", "60")))
except ValueError:
    CATALOG_TTL = 60

try:
    CATALOG_MAX_DOCS = max(100, int(_env("CATALOG_MAX_DOCS", "300")))
except ValueError:
    CATALOG_MAX_DOCS = 300

# Search is intentionally separate from the small homepage catalog cap.
# Auto Filter-style search must be able to collect all variants/episodes for a
# matching title instead of stopping at the first 500 records.
try:
    SEARCH_MAX_DOCS = max(500, int(_env("SEARCH_MAX_DOCS", "1500")))
except ValueError:
    SEARCH_MAX_DOCS = 1500

HOST = _env("HOST", "0.0.0.0")
try:
    PORT = int(_env("PORT", "8080"))
except ValueError:
    PORT = 8080

STREAM_TOKEN_TTL = max(60, int(_env("STREAM_TOKEN_TTL", "300")))

def missing_core_settings():
    missing = []
    if not DATABASE_URI:
        missing.append("DATABASE_URI")
    if not DATABASE_URI2:
        missing.append("DATABASE_URI2")
    if not SITE_SECRET:
        missing.append("SITE_SECRET")
    return missing

def telegram_ready():
    return bool(BOT_TOKEN and API_ID and API_HASH)

try:
    TMDB_CACHE_MAX = max(16, int(_env("TMDB_CACHE_MAX", "128")))
except ValueError:
    TMDB_CACHE_MAX = 128

try:
    TRANSCODE_CONCURRENCY = max(1, int(_env("TRANSCODE_CONCURRENCY", "1")))
except ValueError:
    TRANSCODE_CONCURRENCY = 1

# Optional website premium-plan store/payment settings. This is separate from the
# read-only AutoFilter media MongoDB. Leave PAYMENT_PROVIDER=manual to use admin grants.
PAYMENT_PROVIDER = _env("PAYMENT_PROVIDER", "manual").strip().lower()
RAZORPAY_KEY_ID = _env("RAZORPAY_KEY_ID").strip()
RAZORPAY_KEY_SECRET = _env("RAZORPAY_KEY_SECRET").strip()
RAZORPAY_WEBHOOK_SECRET = _env("RAZORPAY_WEBHOOK_SECRET").strip()
def _plans():
    raw=_env("PREMIUM_PLANS", "7day|7 Days|29,15day|15 Days|49,30day|30 Days|79,60day|60 Days|129")
    out={}
    for item in raw.split(','):
        try:
            pid,name,price=item.split('|',2); days=int(pid.replace('day','')); out[pid]={"name":name,"days":days,"price_inr":max(1,int(price))}
        except Exception: pass
    return out or {"30day":{"name":"30 Days","days":30,"price_inr":79}}
PREMIUM_PLANS = _plans()
PREMIUM_PRICE_INR = min(v["price_inr"] for v in PREMIUM_PLANS.values())
PREMIUM_DAYS = min(v["days"] for v in PREMIUM_PLANS.values())
MANUAL_PAYMENT_INSTRUCTIONS = _env("MANUAL_PAYMENT_INSTRUCTIONS", "Pay using the configured UPI/bank method, then upload the payment screenshot for admin approval.")
MANUAL_PAYMENT_QR = _env("MANUAL_PAYMENT_QR", "")
# DATABASE_URI is reserved for persistent website state (users, payments,
# premium, settings, history, etc.). DATABASE_URI2 is the read-only Devil
# AutoFilter media source used by backend/database.py.
WEBSITE_SETTINGS_COLLECTION = _env("WEBSITE_SETTINGS_COLLECTION", "vyra_settings").strip()
WEBSITE_CATALOG_COLLECTION = _env("WEBSITE_CATALOG_COLLECTION", "vyra_catalog_index").strip()
