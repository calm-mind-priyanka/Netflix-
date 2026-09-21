"""Website account and identity layer.

The public eight-digit VYRA User ID is a permanent identifier, not an
authentication credential. Authentication uses a separate high-entropy
account key and a signed HttpOnly session cookie. MongoDB is the source of
truth for accounts and session invalidation state.
"""
from __future__ import annotations

import secrets
import time
from aiohttp import web

from .auth import (
    hash_user_secret,
    verify_user_secret,
    make_user_session,
    validate_user_session,
)
from .web_store import users, history

SESSION_COOKIE = "vyra_session"
SESSION_TTL = 60 * 60 * 24 * 30

async def _ready():
    from .web_store import ensure_indexes
    await ensure_indexes()


def _clean_nickname(value: str) -> str:
    value = " ".join(str(value or "").strip().split())
    if len(value) < 2 or len(value) > 40:
        raise ValueError("Nickname must be 2 to 40 characters.")
    return value


def _new_user_id() -> str:
    return str(secrets.randbelow(90000000) + 10000000)


def _new_account_key() -> str:
    # 20 URL-safe chars are intentionally separate from the public User ID.
    return secrets.token_urlsafe(15)

async def _allocate_user_id() -> str:
    for _ in range(20):
        candidate = _new_user_id()
        if users is None:
            raise RuntimeError("Website user storage is unavailable")
        if not await users.find_one({"user_id": candidate}, {"_id": 1}):
            return candidate
    raise RuntimeError("Could not allocate a unique User ID")

async def _record(user_id: str, event_type: str, metadata=None, actor="user"):
    if history is None:
        return
    await history.insert_one({
        "event_id": secrets.token_urlsafe(12),
        "user_id": user_id,
        "event_type": event_type,
        "metadata": metadata or {},
        "actor": actor,
        "created_at": int(time.time()),
    })


def _set_session(response, user_doc, request=None):
    token = make_user_session(
        user_doc["user_id"],
        int(user_doc.get("session_version", 1)),
        ttl=SESSION_TTL,
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="Lax",
        secure=bool(request and (request.secure or request.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().lower() == "https")),
        max_age=SESSION_TTL,
        path="/",
    )


def clear_session(response):
    response.del_cookie(SESSION_COOKIE, path="/")

async def current_user(request):
    token = request.cookies.get(SESSION_COOKIE, "")
    parsed = validate_user_session(token)
    if not parsed or users is None:
        return None
    await _ready()
    doc = await users.find_one({"user_id": parsed["user_id"], "status": "active"})
    if not doc:
        return None
    if int(doc.get("session_version", 1)) != parsed["version"]:
        return None
    return doc

async def require_user(request):
    user = await current_user(request)
    if not user:
        raise web.HTTPUnauthorized(text="Website account login required")
    return user

async def create_account(request):
    try:
        body = await request.json()
        nickname = _clean_nickname(body.get("nickname", ""))
    except ValueError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid account data"}, status=400)

    await _ready()
    if users is None:
        return web.json_response({"ok": False, "error": "Account storage is unavailable"}, status=503)

    user_id = await _allocate_user_id()
    account_key = _new_account_key()
    now = int(time.time())
    doc = {
        "user_id": user_id,
        "nickname": nickname,
        "status": "active",
        "created_at": now,
        "updated_at": now,
        "last_login_at": now,
        "session_version": 1,
        "account_key_hash": hash_user_secret(account_key),
    }
    try:
        await users.insert_one(doc)
    except Exception as exc:
        # A unique-index collision is extremely unlikely; retry through the
        # normal allocator rather than silently creating a duplicate identity.
        if "duplicate" in str(exc).lower() or "e11000" in str(exc).lower():
            return web.json_response({"ok": False, "error": "Please try creating the account again."}, status=409)
        raise

    await _record(user_id, "ACCOUNT_CREATED", {"nickname": nickname})
    response = web.json_response({
        "ok": True,
        "user": {"user_id": user_id, "nickname": nickname},
        "account_key": account_key,
        "message": "Account created. Save your Account Key; it is required if you log in again after logout.",
    })
    _set_session(response, doc, request)
    return response

async def login(request):
    try:
        body = await request.json()
        user_id = str(body.get("user_id", "")).strip()
        account_key = str(body.get("account_key", "")).strip()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid login data"}, status=400)

    if not user_id.isdigit() or len(user_id) != 8 or not account_key:
        return web.json_response({"ok": False, "error": "Enter your 8-digit User ID and Account Key."}, status=400)

    await _ready()
    doc = await users.find_one({"user_id": user_id, "status": "active"}) if users is not None else None
    if not doc or not verify_user_secret(account_key, doc.get("account_key_hash", "")):
        return web.json_response({"ok": False, "error": "Invalid User ID or Account Key."}, status=401)

    now = int(time.time())
    await users.update_one({"user_id": user_id}, {"$set": {"last_login_at": now, "updated_at": now}})
    doc["last_login_at"] = now
    await _record(user_id, "LOGIN", {})
    response = web.json_response({"ok": True, "user": {"user_id": user_id, "nickname": doc.get("nickname", "")}})
    _set_session(response, doc, request)
    return response

async def me(request):
    user = await current_user(request)
    if not user:
        return web.json_response({"ok": True, "authenticated": False})
    from .premium import get_status
    premium = await get_status(user["user_id"])
    return web.json_response({
        "ok": True,
        "authenticated": True,
        "user": {
            "user_id": user["user_id"],
            "nickname": user.get("nickname", ""),
            "created_at": int(user.get("created_at", 0) or 0),
        },
        "premium": premium,
    })

async def logout(request):
    user = await current_user(request)
    response = web.json_response({"ok": True})
    clear_session(response)
    if user and users is not None:
        await users.update_one(
            {"user_id": user["user_id"]},
            {"$inc": {"session_version": 1}, "$set": {"updated_at": int(time.time())}},
        )
        await _record(user["user_id"], "LOGOUT", {})
    return response

async def update_nickname(request):
    user = await require_user(request)
    try:
        nickname = _clean_nickname((await request.json()).get("nickname", ""))
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    now = int(time.time())
    await users.update_one({"user_id": user["user_id"]}, {"$set": {"nickname": nickname, "updated_at": now}})
    await _record(user["user_id"], "PROFILE_UPDATED", {"nickname": nickname})
    return web.json_response({"ok": True, "nickname": nickname})

async def admin_users_list(request):
    from .premium import get_status
    from .auth import validate_admin_session
    if not validate_admin_session(request.cookies.get("admin_session", "")):
        raise web.HTTPUnauthorized(text="Admin login required")
    await _ready()
    q = str(request.query.get("q", "")).strip()
    limit = min(100, max(1, int(request.query.get("limit", "50") or 50)))
    query = {}
    if q:
        import re
        query = {"$or": [
            {"user_id": q},
            {"nickname": {"$regex": re.escape(q), "$options": "i"}},
        ]}
    rows = []
    async for doc in users.find(query, {"account_key_hash": 0, "session_version": 0}).sort("created_at", -1).limit(limit):
        uid = doc["user_id"]
        doc.pop("_id", None)
        doc["premium"] = await get_status(uid)
        rows.append(doc)
    return web.json_response({"ok": True, "users": rows})

async def admin_user_detail(request):
    from .premium import get_status
    from .auth import validate_admin_session
    if not validate_admin_session(request.cookies.get("admin_session", "")):
        raise web.HTTPUnauthorized(text="Admin login required")
    uid = request.match_info["user_id"]
    await _ready()
    user = await users.find_one({"user_id": uid}, {"account_key_hash": 0, "session_version": 0}) if users is not None else None
    if not user:
        return web.json_response({"ok": False, "error": "Website user not found"}, status=404)
    user.pop("_id", None)
    premium = await get_status(uid)
    from .web_store import premium_users, premium_orders, premium_manual, payments, history
    premium_doc = await premium_users.find_one({"user_id": uid}) if premium_users is not None else None
    for d in (premium_doc,):
        if d: d.pop("_id", None)
    payment_rows = []
    if payments is not None:
        async for p in payments.find({"user_id": uid}).sort("created_at", -1).limit(100):
            p.pop("_id", None); payment_rows.append(p)
    history_rows = []
    if history is not None:
        async for h in history.find({"user_id": uid}).sort("created_at", -1).limit(200):
            h.pop("_id", None); history_rows.append(h)
    manual_rows = []
    if premium_manual is not None:
        async for r in premium_manual.find({"user_id": uid}, {"proof": 0}).sort("created_at", -1).limit(100):
            r.pop("_id", None); manual_rows.append(r)
    return web.json_response({
        "ok": True,
        "user": user,
        "premium": premium,
        "premium_record": premium_doc,
        "payments": payment_rows,
        "manual_requests": manual_rows,
        "history": history_rows,
    })

async def admin_payments(request):
    from .auth import validate_admin_session
    if not validate_admin_session(request.cookies.get("admin_session", "")):
        raise web.HTTPUnauthorized(text="Admin login required")
    await _ready()
    status = str(request.query.get("status", "")).strip()
    uid = str(request.query.get("user_id", "")).strip()
    query = {}
    if status: query["status"] = status
    if uid: query["user_id"] = uid
    rows = []
    if payments is not None:
        async for p in payments.find(query).sort("created_at", -1).limit(500):
            p.pop("_id", None); rows.append(p)
    return web.json_response({"ok": True, "payments": rows})

async def admin_history(request):
    from .auth import validate_admin_session
    if not validate_admin_session(request.cookies.get("admin_session", "")):
        raise web.HTTPUnauthorized(text="Admin login required")
    await _ready()
    uid = str(request.query.get("user_id", "")).strip()
    query = {"user_id": uid} if uid else {}
    rows = []
    if history is not None:
        async for h in history.find(query).sort("created_at", -1).limit(500):
            h.pop("_id", None); rows.append(h)
    return web.json_response({"ok": True, "history": rows})
