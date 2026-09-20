"""Website verification/access gate.

This is the web adapter for the relevant Ultron AutoFilter verification flow.
It stores verification state in the dedicated website Mongo collections and
never writes to the AutoFilter media collection.
"""
from __future__ import annotations

import asyncio
import secrets
import time
from urllib.parse import quote

from aiohttp import web

from .admin_settings import get_settings, init_settings_store
from .config import PUBLIC_URL, TELEGRAM_BOT_USERNAME, TELEGRAM_VERIFY_URL


def _settings():
    return get_settings().get("verification", {})


async def _shorten(url: str, stage: int) -> str:
    try:
        from shortzy import Shortzy
    except Exception:
        return url
    settings = _settings()
    if str(settings.get("shortlink_mode", "enabled")) != "enabled":
        return url
    item = (settings.get("shorteners") or {}).get(str(stage), {})
    if not bool(item.get("enabled", False)):
        return url
    site = str(item.get("name") or "").strip()
    api = str(item.get("api") or "").strip()
    if not site or not api:
        return url
    try:
        shortener = Shortzy(api, site)
        try:
            return await asyncio.wait_for(shortener.convert(url), timeout=4)
        except Exception:
            return await asyncio.wait_for(shortener.get_quick_link(url), timeout=4)
    except Exception:
        return url


async def state(cookie: str):
    from .web_store import ensure_indexes, verification_tokens
    if not cookie or verification_tokens is None:
        return 0, 0.0
    await ensure_indexes()
    await init_settings_store()
    row = await verification_tokens.find_one({"code": cookie})
    if not row or float(row.get("expires", 0) or 0) <= time.time():
        return 0, 0.0
    return int(row.get("stage", 0) or 0), float(row.get("verified_at", 0) or 0)


def required_stage(stage: int, verified_at: float, now: float | None = None) -> int:
    s = _settings()
    now = now or time.time()
    required = 1
    gap2 = int(s.get("verification_time_2", 0) or 0)
    gap3 = int(s.get("verification_time_3", 0) or 0)
    if stage >= 1 and gap2 > 0 and now - verified_at >= gap2:
        required = 2
    if stage >= 2 and gap3 > 0 and now - verified_at >= gap3:
        required = 3
    return required


def _verification_expiry(now: float) -> float:
    hours = max(1, int(_settings().get("validity_hours", 24) or 24))
    return now + hours * 3600


def telegram_url(code: str = "") -> str:
    configured = TELEGRAM_VERIFY_URL.strip()
    if configured:
        if code and "{code}" in configured:
            return configured.replace("{code}", quote(code, safe=""))
        return configured
    if TELEGRAM_BOT_USERNAME:
        return f"https://t.me/{TELEGRAM_BOT_USERNAME}?start=verify_{quote(code, safe='')}" if code else f"https://t.me/{TELEGRAM_BOT_USERNAME}"
    return ""


async def requirement(request: web.Request, file_id: str):
    s = _settings()
    if not bool(s.get("enabled", False)):
        return None

    cookie = request.cookies.get("vyra_verify", "")
    stage, verified_at = await state(cookie)
    now = time.time()
    required = required_stage(stage, verified_at, now) if verified_at else 1
    if stage >= required and verified_at:
        return None

    from .web_store import ensure_indexes, verification_tokens
    await ensure_indexes()
    code = secrets.token_urlsafe(24)
    await verification_tokens.insert_one({
        "code": code,
        "file_id": str(file_id),
        "stage": required,
        "verified_at": 0,
        "expires": _verification_expiry(now),
        "created_at": now,
    })
    base = PUBLIC_URL.rstrip("/") if PUBLIC_URL else str(request.url.origin())
    local = f"{base}/api/verify?code={quote(code, safe='')}"
    tutorial = str(s.get(f"tutorial_{required}") or s.get("tutorial_1") or "").strip()
    return {
        "verification_required": True,
        "verification_url": await _shorten(local, required),
        "direct_verification_url": local,
        "tutorial_url": tutorial,
        "telegram_url": telegram_url(code),
        "stage": required,
        "validity_hours": max(1, int(s.get("validity_hours", 24) or 24)),
    }


async def status(request: web.Request):
    s = _settings()
    cookie = request.cookies.get("vyra_verify", "")
    stage, verified_at = await state(cookie)
    valid = bool(stage and verified_at)
    if valid:
        valid = stage >= required_stage(stage, verified_at)
    return {
        "verification_enabled": bool(s.get("enabled", False)),
        "verified": valid,
        "verification_stage": stage,
        "verified_at": verified_at,
        "validity_hours": max(1, int(s.get("validity_hours", 24) or 24)),
    }


async def complete(request: web.Request):
    from .web_store import ensure_indexes, verification_tokens
    code = request.query.get("code", "").strip()
    await ensure_indexes()
    row = await verification_tokens.find_one({"code": code}) if code else None
    if not row or float(row.get("expires", 0) or 0) <= time.time():
        raise web.HTTPBadRequest(text="Verification link expired. Please request a new verification link.")

    now = time.time()
    await verification_tokens.update_one(
        {"code": code},
        {"$set": {"verified_at": now, "verified": True}},
    )
    base = PUBLIC_URL.rstrip("/") if PUBLIC_URL else str(request.url.origin())
    html = f'''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Verification complete</title><style>body{{font-family:system-ui;background:#07111f;color:#fff;display:grid;place-items:center;min-height:100vh;margin:0}}main{{max-width:520px;text-align:center;padding:28px}}a{{display:inline-block;margin-top:18px;padding:12px 18px;border-radius:10px;background:#fff;color:#07111f;text-decoration:none;font-weight:800}}</style></head><body><main><h1>✓ Verification complete</h1><p>Your verification is active. Return to VYRA and press Watch or Download again.</p><a href="{base}/">Return to VYRA</a></main></body></html>'''
    response = web.Response(text=html, content_type="text/html")
    response.set_cookie("vyra_verify", code, httponly=True, secure=True, samesite="Lax", max_age=max(3600, int(_settings().get("validity_hours", 24) or 24) * 3600), path="/")
    return response
