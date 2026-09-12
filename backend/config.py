import os

DATABASE_URI = os.environ["DATABASE_URI"]
DATABASE_URI2 = os.getenv("DATABASE_URI2", "")
DATABASE_NAME = os.getenv("DATABASE_NAME", "Cluster0")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "Sandy_files")
MULTIPLE_DB = os.getenv("MULTIPLE_DB", "True").lower() in {"1", "true", "yes", "on"}
BOT_TOKEN = os.environ["BOT_TOKEN"]
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_NAME = os.getenv("MOVIE_SITE_SESSION", "movie_site")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8080"))
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
SITE_SECRET = os.environ["SITE_SECRET"]
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
# Backward-compatible internal name used by app.py. No password hash generation is required.
ADMIN_PASSWORD_HASH = ADMIN_PASSWORD
PUBLIC_URL = os.getenv("PUBLIC_URL", "")
CATALOG_TTL = int(os.getenv("CATALOG_TTL", "60"))
