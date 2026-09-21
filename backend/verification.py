"""Website verification/access gate.

Verification is one master access gate.  When it is enabled, the configured
verification shortener stages are used to issue a fresh link only when the
current browser has not completed the required stage.  A successful stage is
stored in MongoDB and represented by an HttpOnly browser cookie.
"""
from __future__ import annotations

import secrets
import time
from urllib.parse import quote

from aiohttp import web

from .admin_settings import get_settings, get_value, init_settings_store
from .config import PUBLIC_URL
from .verification_provider import shorten, stage_destination
from .users import current_user

COOKIE_NAME = "vyra_verify"


def _settings():
    return get_settings().get("verification", {})


async def _load_cookie_row(request):
    from .web_store import ensure_indexes, verification_tokens

    cookie = request.cookies.get(COOKIE_NAME, "").strip()
    if not cookie or verification_tokens is None:
        return None
    await ensure_indexes()
    await init_settings_store()
    row = await verification_tokens.find_one({"code": cookie})
    if not row:
        return None
    if float(row.get("expires", 0) or 0) <= time.time():
        return None
    return row


async def state(request: web.Request):
    row = await _load_cookie_row(request)
    if not row:
        return 0, 0.0, ""
    return (
        int(row.get("stage", 0) or 0),
        float(row.get("verified_at", 0) or 0),
        str(row.get("code", "")),
    )


def required_stage(stage: int, verified_at: float, now: float | None = None) -> int:
    """Return the next configured verification stage that is due."""
    settings = _settings()
    now = now or time.time()
    current = max(0, min(3, int(stage or 0)))
    if current <= 0 or not verified_at:
        return 1

    shorteners = settings.get("shorteners") or {}
    # A later stage is due only when that stage is enabled and its configured
    # delay has elapsed. A zero delay means it is due immediately, matching the
    # explicit admin configuration rather than silently skipping the stage.
    for number, gap_key in ((2, "verification_time_2"), (3, "verification_time_3")):
        if current >= number:
            continue
        enabled = bool((shorteners.get(str(number)) or {}).get("enabled", False))
        if not enabled:
            continue
        gap = max(0, int(settings.get(gap_key, 0) or 0))
        if now - verified_at >= gap:
            return number
        break
    return current


def _verification_expiry(now: float) -> float:
    hours = max(1, int(_settings().get("validity_hours", 24) or 24))
    return now + hours * 3600


def _base_url(request: web.Request) -> str:
    configured = PUBLIC_URL.rstrip("/") if PUBLIC_URL else ""
    return configured or str(request.url.origin())


async def _premium_bypasses(request: web.Request) -> bool:
    """Premium is a single access decision: if configured to bypass verification,
    it bypasses the whole verification/shortlink gate."""
    if not bool(get_value("payments", "premium_bypass_verification", default=True)):
        return False
    try:
        from .premium import get_status
        user = await current_user(request)
        if not user:
            return False
        return bool((await get_status(user["user_id"])).get("premium"))
    except Exception:
        return False


async def requirement(request: web.Request, file_id: str):
    settings = _settings()
    if not bool(settings.get("enabled", False)):
        return None

    if await _premium_bypasses(request):
        return None

    row = await _load_cookie_row(request)
    now = time.time()
    stage = int(row.get("stage", 0) or 0) if row else 0
    verified_at = float(row.get("verified_at", 0) or 0) if row else 0.0
    required = required_stage(stage, verified_at, now) if verified_at else 1

    if row and bool(row.get("verified", False)) and stage >= required and verified_at:
        return None

    from .web_store import ensure_indexes, verification_tokens

    await ensure_indexes()
    await init_settings_store()

    # A new attempt always receives a new opaque token and a new shortlink.
    # This prevents an old URL from being reused for another Watch attempt.
    code = secrets.token_urlsafe(32)
    destination = stage_destination(_base_url(request), code, required)
    try:
        short_url = await shorten(destination, required)
    except RuntimeError as exc:
        return {
            "verification_required": True,
            "verification_url": "",
            "tutorial_url": "",
            "stage": required,
            "validity_hours": max(1, int(settings.get("validity_hours", 24) or 24)),
            "verification_error": str(exc),
        }

    tutorial = str(settings.get(f"tutorial_{required}") or settings.get("tutorial_1") or "").strip()
    user = await current_user(request)
    user_id = user["user_id"] if user else ""
    issued = time.time()
    expires = _verification_expiry(issued)

    await verification_tokens.insert_one({
        "code": code,
        "user_id": user_id,
        # Verification is account/browser scoped, not movie scoped.  The
        # selected file is retained for audit/debugging only.
        "file_id": str(file_id),
        "stage": required,
        "verified_at": 0,
        "verified": False,
        "issued_at": issued,
        "expires": expires,
        "used": False,
        "short_url": short_url,
    })

    return {
        "verification_required": True,
        "verification_url": short_url,
        "direct_verification_url": destination,
        "tutorial_url": tutorial,
        "stage": required,
        "validity_hours": max(1, int(settings.get("validity_hours", 24) or 24)),
    }


async def status(request: web.Request):
    settings = _settings()
    row = await _load_cookie_row(request)
    stage = int(row.get("stage", 0) or 0) if row else 0
    verified_at = float(row.get("verified_at", 0) or 0) if row else 0.0
    valid = bool(row and row.get("verified", False) and verified_at)
    if valid:
        valid = stage >= required_stage(stage, verified_at)
    return {
        "verification_enabled": bool(settings.get("enabled", False)),
        "verified": valid,
        "verification_stage": stage,
        "verified_at": verified_at,
        "validity_hours": max(1, int(settings.get("validity_hours", 24) or 24)),
    }


async def complete(request: web.Request):
    """Consume a generated verification destination and establish browser state."""
    from .web_store import ensure_indexes, verification_tokens

    code = request.query.get("code", "").strip()
    requested_stage = int(request.query.get("stage", "0") or 0)
    await ensure_indexes()
    row = await verification_tokens.find_one({"code": code}) if code else None
    user = await current_user(request)
    current_user_id = user["user_id"] if user else ""
    now = time.time()

    valid = (
        bool(row)
        and float(row.get("expires", 0) or 0) > now
        and not bool(row.get("used", False))
        and requested_stage in {0, int(row.get("stage", 0) or 0)}
        and (not row.get("user_id") or row.get("user_id") == current_user_id)
    )
    if not valid:
        raise web.HTTPBadRequest(text="Verification link expired or invalid. Please request a new verification link.")

    result = await verification_tokens.update_one(
        {"code": code, "used": {"$ne": True}},
        {"$set": {"verified_at": now, "verified": True, "used": True}},
    )
    if result.modified_count != 1:
        raise web.HTTPBadRequest(text="Verification link has already been used. Please request a new verification link.")

    base = _base_url(request)
    validity = max(1, int(_settings().get("validity_hours", 24) or 24))
    html = f"""<!doctype html>
<html lang=\"en\"><head><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>Verification complete</title>
<style>body{{font-family:system-ui;background:#07111f;color:#fff;display:grid;place-items:center;min-height:100vh;margin:0}}main{{max-width:520px;text-align:center;padding:28px}}a{{display:inline-block;margin-top:18px;padding:12px 18px;border-radius:10px;background:#fff;color:#07111f;text-decoration:none;font-weight:800}}</style>
</head><body><main><h1>✓ Verification complete</h1><p>Your verification is active for up to {validity} hours.</p><p>Return to VYRA and press Watch again.</p><a href=\"{base}/\">Return to VYRA</a></main></body></html>"""
    response = web.Response(text=html, content_type="text/html")
    response.set_cookie(
        COOKIE_NAME,
        code,
        httponly=True,
        secure=True,
        samesite="Lax",
        max_age=max(3600, validity * 3600),
        path="/",
    )
    return response
