import logging
import os
import time
from pathlib import Path

from aiohttp import ClientSession, web

from .auth import (
    make_admin_session,
    make_stream_token,
    validate_admin_session,
    validate_stream_token,
    verify_admin_password,
)
from .config import (
    ADMIN_PASSWORD,
    ADMIN_PASSWORD_HASH,
    ADMIN_USERNAME,
    CATALOG_MAX_DOCS,
    CATALOG_TTL,
    COLLECTION_NAME,
    DATABASE_NAME,
    DATABASE_URI,
    DATABASE_URI2,
    MULTIPLE_DB,
    PORT,
    SITE_SECRET,
    HOST,
    TMDB_API_KEY,
    telegram_ready,
)
from .database import (
    collection_counts,
    find_media,
    iter_media,
    search_media,
)
from .parser import normalize, normalize_for_search, normalize_query, search_title_score
from .stream import Streamer, create_client

LOGGER = logging.getLogger("streambox")
BASE = Path(__file__).resolve().parent.parent

META_CACHE = {}
CATALOG_CACHE = {"at": 0.0, "items": []}

# Maintenance is deliberately kept in memory. The website must not create or
# modify a collection inside the Auto Filter Bot's MongoDB database.
MAINTENANCE = False


async def all_titles(force=False):
    now = time.time()
    if (
        not force
        and CATALOG_CACHE["items"]
        and now - CATALOG_CACHE["at"] < CATALOG_TTL
    ):
        return CATALOG_CACHE["items"]

    if not DATABASE_URI:
        raise RuntimeError("DATABASE_URI is not configured")

    projection = {
        "_id": 1,
        "file_id": 1,
        "file_ref": 1,
        "file_name": 1,
        "file_size": 1,
        "file_type": 1,
        "mime_type": 1,
        "caption": 1,
    }
    # Do not cap the catalog here. A capped read can split a title across pages
    # and make a perfectly valid search result impossible to resolve later.
    docs = [doc async for doc in iter_media(projection=projection)]
    items = normalize(docs)
    CATALOG_CACHE.update(at=now, items=items)
    return items


async def tmdb_meta(title, kind, year=None):
    if not TMDB_API_KEY:
        return {}

    key = (kind, title.casefold(), year)
    cached = META_CACHE.get(key)
    if cached and time.time() - cached[0] < 86400:
        return cached[1]

    endpoint = "tv" if kind == "series" else "movie"
    url = f"https://api.themoviedb.org/3/search/{endpoint}"
    try:
        async with ClientSession() as session:
            async with session.get(
                url,
                params={
                    "api_key": TMDB_API_KEY,
                    "query": title,
                    "include_adult": "false",
                    **({"year": year} if year and kind == "movie" else {}),
                    **({"first_air_date_year": year} if year and kind == "series" else {}),
                },
                timeout=8,
            ) as response:
                if response.status != 200:
                    return {}
                data = await response.json()

        results = data.get("results") or []
        wanted = normalize_for_search(title)
        result = next(
            (
                candidate for candidate in results
                if normalize_for_search(candidate.get("title") or candidate.get("name")) == wanted
            ),
            results[0] if results else None,
        )
        if not result:
            return {}

        date = result.get("first_air_date") or result.get("release_date") or ""
        output = {
            "poster": (
                f"https://image.tmdb.org/t/p/w500{result['poster_path']}"
                if result.get("poster_path") else None
            ),
            "backdrop": (
                f"https://image.tmdb.org/t/p/w1280{result['backdrop_path']}"
                if result.get("backdrop_path") else None
            ),
            "description": result.get("overview"),
            "year": int(date[:4]) if date[:4].isdigit() else None,
            "rating": result.get("vote_average"),
        }
        META_CACHE[key] = (time.time(), output)
        return output
    except Exception:
        LOGGER.exception("TMDB lookup failed for %s", title)
        return {}


async def enrich(title):
    output = dict(title)
    metadata = await tmdb_meta(title["title"], title["type"], title.get("year"))
    # Never overwrite a poster already attached to a real media record.
    for key, value in metadata.items():
        if value is not None and (key != "poster" or not output.get("poster")):
            output[key] = value
    return output


def _search_score(item, query_title):
    return search_title_score(item["title"], query_title)


async def home(request):
    items = await all_titles()
    enriched = []
    for item in items[:100]:
        enriched.append(await enrich(item))
    return web.json_response({"ok": True, "items": enriched, "count": len(items)})


async def search(request):
    query = request.query.get("q", "").strip()
    if not query:
        return web.json_response({"ok": True, "items": [], "count": 0})

    parsed = normalize_query(query)
    items = await all_titles()
    candidates = []

    for item in items:
        if parsed["year"] is not None:
            years = set(item.get("years") or [])
            if item.get("year"):
                years.add(item["year"])
            if parsed["year"] not in years:
                continue
        if parsed["season"] is not None:
            if item["type"] != "series" or not any(
                season["season"] == parsed["season"] for season in item.get("seasons", [])
            ):
                continue
        if parsed["episode"] is not None:
            if item["type"] != "series" or not any(
                ep["episode"] == parsed["episode"]
                for season in item.get("seasons", [])
                if parsed["season"] is None or season["season"] == parsed["season"]
                for ep in season.get("episodes", [])
            ):
                continue

        score = _search_score(item, parsed["title"])
        if score >= 0.72:
            candidates.append((score, item))

    candidates.sort(key=lambda pair: (-pair[0], pair[1]["title"].casefold()))
    selected = []
    for _, item in candidates[:100]:
        copy = dict(item)
        if parsed["season"] is not None:
            copy["search_season"] = parsed["season"]
        if parsed["episode"] is not None:
            copy["search_episode"] = parsed["episode"]
        selected.append(copy)
    enriched = [await enrich(item) for item in selected]
    return web.json_response({"ok": True, "items": enriched, "count": len(selected)})


async def title(request):
    title_id = request.match_info["id"]
    for item in await all_titles():
        if item["id"] == title_id:
            return web.json_response({"ok": True, **await enrich(item)})
    raise web.HTTPNotFound(text="Title not found")


async def token(request):
    file_id = request.match_info["file_id"]
    doc = await find_media(file_id, projection={"_id": 1})
    if doc is None:
        raise web.HTTPNotFound(text="Media not found in Auto Filter Bot database")
    return web.json_response(
        {"ok": True, "token": make_stream_token(file_id)}
    )


async def stream(request):
    file_id = request.match_info["file_id"]
    token_value = request.query.get("token", "")
    if not validate_stream_token(token_value, file_id):
        raise web.HTTPForbidden(text="Invalid or expired stream token")
    streamer = request.app.get("streamer")
    if streamer is None:
        raise web.HTTPServiceUnavailable(text="Telegram streaming is not available")
    return await streamer.stream(request, file_id)


async def stream_compatible(request):
    file_id = request.match_info["file_id"]
    token_value = request.query.get("token", "")
    if not validate_stream_token(token_value, file_id):
        raise web.HTTPForbidden(text="Invalid or expired stream token")
    streamer = request.app.get("streamer")
    if streamer is None:
        raise web.HTTPServiceUnavailable(text="Telegram streaming is not available")
    return await streamer.transcode(request, file_id)


async def download(request):
    file_id = request.match_info["file_id"]
    token_value = request.query.get("token", "")
    if not validate_stream_token(token_value, file_id):
        raise web.HTTPForbidden(text="Invalid or expired download token")
    streamer = request.app.get("streamer")
    if streamer is None:
        raise web.HTTPServiceUnavailable(text="Telegram streaming is not available")
    return await streamer.stream(request, file_id, attachment=True)


async def health(request):
    return web.json_response({"ok": True})


def _set_status(request, name, configured):
    return {
        "name": name,
        "status": "SET" if configured else "NOT SET",
    }


async def diagnostics(request):
    require_admin(request)

    counts = {"primary": 0, "secondary": 0}
    db_error = None
    try:
        counts = await collection_counts()
    except Exception as exc:
        db_error = str(exc)
        LOGGER.exception("Diagnostics database check failed")

    return web.json_response(
        {
            "ok": db_error is None,
            "database": DATABASE_NAME,
            "collection": COLLECTION_NAME,
            "multiple_db": MULTIPLE_DB,
            "database_uri": "SET" if DATABASE_URI else "NOT SET",
            "database_uri2": "SET" if DATABASE_URI2 else "NOT SET",
            "telegram": {
                "bot_token": "SET" if os.getenv("BOT_TOKEN") else "NOT SET",
                "api_id": "SET" if os.getenv("API_ID") else "NOT SET",
                "api_hash": "SET" if os.getenv("API_HASH") else "NOT SET",
                "client_running": request.app.get("telegram_ready", False),
            },
            "admin_username": _set_status(
                request, "ADMIN_USERNAME", bool(ADMIN_USERNAME)
            )["status"],
            "admin_password": "SET"
            if (ADMIN_PASSWORD or ADMIN_PASSWORD_HASH)
            else "NOT SET",
            "site_secret": "SET" if SITE_SECRET else "NOT SET",
            "tmdb_api_key": "SET" if TMDB_API_KEY else "NOT SET",
            "counts": counts,
            "database_error": db_error,
        }
    )


def is_admin(request):
    return validate_admin_session(request.cookies.get("admin_session", ""))


def require_admin(request):
    if not is_admin(request):
        raise web.HTTPUnauthorized(text="Admin login required")


async def admin_login(request):
    if request.method == "GET":
        return web.Response(text=ADMIN_HTML, content_type="text/html")

    data = await request.post()
    configured_password = ADMIN_PASSWORD_HASH or ADMIN_PASSWORD

    if (
        not ADMIN_USERNAME
        or data.get("username") != ADMIN_USERNAME
        or not verify_admin_password(data.get("password", ""), configured_password)
    ):
        raise web.HTTPUnauthorized(text="Invalid admin credentials")

    response = web.json_response({"ok": True})
    response.set_cookie(
        "admin_session",
        make_admin_session(),
        httponly=True,
        secure=True,
        samesite="Lax",
        max_age=43200,
        path="/",
    )
    return response


async def admin_logout(request):
    require_admin(request)
    response = web.json_response({"ok": True})
    response.del_cookie("admin_session", path="/")
    return response


async def admin_status(request):
    require_admin(request)
    items = await all_titles()
    return web.json_response(
        {
            "authenticated": True,
            "maintenance": MAINTENANCE,
            "titles": len(items),
            "movies": sum(item["type"] == "movie" for item in items),
            "series": sum(item["type"] == "series" for item in items),
            "catalog_cache_age": (
                round(time.time() - CATALOG_CACHE["at"], 1)
                if CATALOG_CACHE["at"]
                else None
            ),
        }
    )


async def admin_toggle_maintenance(request):
    global MAINTENANCE
    require_admin(request)

    try:
        data = await request.json()
    except Exception as exc:
        raise web.HTTPBadRequest(text="Invalid JSON body") from exc

    MAINTENANCE = bool(data.get("maintenance"))
    return web.json_response({"ok": True, "maintenance": MAINTENANCE})


async def admin_refresh(request):
    require_admin(request)
    META_CACHE.clear()
    items = await all_titles(force=True)
    return web.json_response({"ok": True, "count": len(items)})


@web.middleware
async def api_error_middleware(request, handler):
    try:
        return await handler(request)
    except web.HTTPException as exc:
        if request.path.startswith("/api/"):
            return web.json_response(
                {
                    "ok": False,
                    "error": exc.text or exc.reason,
                },
                status=exc.status,
            )
        raise
    except Exception as exc:
        LOGGER.exception("Unhandled request failure: %s", exc)
        if request.path.startswith("/api/"):
            return web.json_response(
                {
                    "ok": False,
                    "error": "Backend failure",
                    "detail": str(exc),
                },
                status=500,
            )
        raise


@web.middleware
async def maintenance_middleware(request, handler):
    if request.path.startswith("/admin") or request.path == "/health":
        return await handler(request)

    if MAINTENANCE:
        if request.path.startswith("/api/"):
            return web.json_response(
                {
                    "ok": False,
                    "maintenance": True,
                    "message": "Website is temporarily under maintenance.",
                },
                status=503,
            )
        return web.Response(
            text=MAINTENANCE_HTML,
            content_type="text/html",
            status=503,
        )

    return await handler(request)


async def startup(app):
    missing = []
    if not DATABASE_URI:
        missing.append("DATABASE_URI")
    if MULTIPLE_DB and not DATABASE_URI2:
        missing.append("DATABASE_URI2")
    if not SITE_SECRET:
        missing.append("SITE_SECRET")

    if missing:
        LOGGER.error("Missing required website settings: %s", ", ".join(missing))

    telegram = await create_client()
    app["tg"] = telegram
    app["streamer"] = Streamer(telegram) if telegram else None
    app["telegram_ready"] = telegram is not None

    if telegram:
        LOGGER.info("Telegram streaming client started")
    else:
        LOGGER.error(
            "Telegram streaming client is unavailable. "
            "The catalog can still be served, but playback/download is disabled."
        )


async def cleanup(app):
    telegram = app.get("tg")
    if telegram is not None:
        try:
            await telegram.stop()
        except Exception:
            LOGGER.exception("Telegram client shutdown failed")

    # Close both Motor clients without ever writing to the bot collections.
    from . import database
    for mongo_client in (database.client, database.client2):
        if mongo_client is not None:
            mongo_client.close()


def create_app():
    app = web.Application(
        client_max_size=1024 * 1024,
        middlewares=[api_error_middleware, maintenance_middleware],
    )

    app.router.add_get("/health", health)
    app.router.add_get("/api/diagnostics", diagnostics)
    app.router.add_get("/api/home", home)
    app.router.add_get("/api/search", search)
    app.router.add_get("/api/title/{id}", title)
    app.router.add_get("/api/stream-token/{file_id}", token)
    app.router.add_get("/api/stream/{file_id}", stream)
    app.router.add_get("/api/stream-compatible/{file_id}", stream_compatible)
    app.router.add_get("/api/download/{file_id}", download)

    app.router.add_get("/admin", admin_login)
    app.router.add_post("/admin/login", admin_login)
    app.router.add_post("/admin/logout", admin_logout)
    app.router.add_get("/admin/api/status", admin_status)
    app.router.add_post("/admin/api/maintenance", admin_toggle_maintenance)
    app.router.add_post("/admin/api/refresh", admin_refresh)

    async def frontend_index(request):
        return web.FileResponse(BASE / "frontend" / "index.html")

    app.router.add_get("/", frontend_index)
    app.router.add_static("/", BASE / "frontend", show_index=False)
    app.router.add_static("/admin/", BASE / "admin", show_index=True)

    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    return app


ADMIN_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Admin Login</title>
<style>
body{margin:0;background:#080808;color:#fff;font:16px system-ui;display:grid;place-items:center;min-height:100vh}
.box{width:min(380px,90vw);padding:28px;background:#151515;border-radius:18px}
input,button{width:100%;box-sizing:border-box;padding:13px;margin-top:10px;border-radius:10px;border:1px solid #333;background:#0d0d0d;color:#fff}
button{background:#fff;color:#000;font-weight:700;cursor:pointer}
#msg{margin-top:12px;color:#f88}
</style>
</head>
<body>
<div class="box">
<h1>Website Admin</h1>
<form id="f">
<input name="username" placeholder="Username" required autocomplete="username">
<input name="password" type="password" placeholder="Password" required autocomplete="current-password">
<button>Sign in</button>
</form>
<div id="msg"></div>
</div>
<script>
const form=document.getElementById("f");
const msg=document.getElementById("msg");
form.onsubmit=async event=>{
  event.preventDefault();
  msg.textContent="";
  try{
    const response=await fetch("/admin/login",{method:"POST",body:new FormData(form)});
    if(response.ok){
      location.href="/admin/";
      return;
    }
    const data=await response.json().catch(()=>({}));
    msg.textContent=data.error||"Invalid username or password";
  }catch(error){
    msg.textContent="Unable to contact the server";
  }
};
</script>
</body>
</html>"""

MAINTENANCE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Maintenance</title>
<style>
body{margin:0;background:#080808;color:#fff;display:grid;place-items:center;min-height:100vh;font:18px system-ui;text-align:center}
main{padding:30px}h1{font-size:42px}
</style>
</head>
<body>
<main>
<div style="font-size:60px">🛠️</div>
<h1>We'll be back soon</h1>
<p>This website is temporarily under maintenance.</p>
</main>
</body>
</html>"""


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    web.run_app(create_app(), host=HOST, port=PORT)
