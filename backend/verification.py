"""Website verification/access gate.

This is the web adaptation of the relevant Ultron verification behavior:
1st/2nd/3rd shortener stages, configurable gaps, tutorial links and a
Mongo-backed verification lifetime.  The website is the primary UI; Telegram
is not required for verification.
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


def _settings():
    return get_settings().get("verification", {})


async def state(request: web.Request):
    from .web_store import ensure_indexes, verification_tokens

    cookie = request.cookies.get("vyra_verify", "")
    if not cookie or verification_tokens is None:
        return 0, 0.0, ""
    await ensure_indexes()
    await init_settings_store()
    row = await verification_tokens.find_one({"code": cookie})
    if not row or float(row.get("expires", 0) or 0) <= time.time():
        return 0, 0.0, ""
    return (
        int(row.get("stage", 0) or 0),
        float(row.get("verified_at", 0) or 0),
        str(row.get("code", "")),
    )


def required_stage(stage: int, verified_at: float, now: float | None = None) -> int:
    """Match Ultron's delayed second/third verification behavior."""
    settings = _settings()
    now = now or time.time()
    required = max(1, min(3, int(stage or 0) or 1))
    shorteners = settings.get("shorteners") or {}
    stage2_enabled = bool((shorteners.get("2") or {}).get("enabled", False))
    stage3_enabled = bool((shorteners.get("3") or {}).get("enabled", False))
    gap2 = max(0, int(settings.get("verification_time_2", 0) or 0))
    gap3 = max(0, int(settings.get("verification_time_3", 0) or 0))
    if stage >= 1 and stage2_enabled and now - verified_at >= gap2:
        required = 2
    if stage >= 2 and stage3_enabled and now - verified_at >= gap3:
        required = 3
    return required


def _verification_expiry(now: float) -> float:
    hours = max(1, int(_settings().get("validity_hours", 24) or 24))
    return now + hours * 3600


def _base_url(request: web.Request) -> str:
    configured = PUBLIC_URL.rstrip("/") if PUBLIC_URL else ""
    return configured or str(request.url.origin())


async def requirement(request: web.Request, file_id: str):
    settings = _settings()
    if not bool(settings.get("enabled", False)):
        return None

    # Premium can independently bypass the shortener gate when the Admin
    # setting is enabled. This is separate from the verification-bypass flag.
    if bool(get_value("payments", "premium_bypass_shortener", default=True)):
        try:
            from .users import current_user
            from .premium import get_status
            user = await current_user(request)
            if user and (await get_status(user["user_id"])).get("premium"):
                return None
        except Exception:
            pass

    stage, verified_at, _ = await state(request)
    now = time.time()
    required = required_stage(stage, verified_at, now) if verified_at else 1
    if stage >= required and verified_at:
        return None

    from .web_store import ensure_indexes, verification_tokens

    await ensure_indexes()
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
    tutorial = str(
        settings.get(f"tutorial_{required}")
        or settings.get("tutorial_1")
        or ""
    ).strip()

    now = time.time()
    user = await current_user(request)
    user_id = user["user_id"] if user else ""
    await verification_tokens.insert_one(
        {
            "code": code,
            "user_id": user_id,
            "file_id": str(file_id),
            "stage": required,
            "verified_at": 0,
            "issued_at": now,
            "expires": _verification_expiry(now),
            "used": False,
            "short_url": short_url,
        }
    )

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
    stage, verified_at, _ = await state(request)
    valid = bool(stage and verified_at)
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
    """Consume a verification destination and establish the browser state."""
    from .web_store import ensure_indexes, verification_tokens

    code = request.query.get("code", "").strip()
    requested_stage = int(request.query.get("stage", "0") or 0)
    await ensure_indexes()
    row = await verification_tokens.find_one({"code": code}) if code else None
    user = await current_user(request)
    current_user_id = user["user_id"] if user else ""
    now = time.time()

    if (
        not row
        or float(row.get("expires", 0) or 0) <= now
        or bool(row.get("used", False))
        or requested_stage not in {0, int(row.get("stage", 0) or 0)}
        or (row.get("user_id") and current_user_id and row.get("user_id") != current_user_id)
    ):
        raise web.HTTPBadRequest(
            text="Verification link expired or invalid. Please request a new verification link."
        )

    await verification_tokens.update_one(
        {"code": code, "used": {"$ne": True}},
        {
            "$set": {
                "verified_at": now,
                "verified": True,
                "used": True,
            }
        },
    )

    base = _base_url(request)
    validity = max(1, int(_settings().get("validity_hours", 24) or 24))
    html = f"""<!doctype html>
<html lang=\"en\"><head><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>Verification complete</title>
<style>body{{font-family:system-ui;background:#07111f;color:#fff;display:grid;place-items:center;min-height:100vh;margin:0}}main{{max-width:520px;text-align:center;padding:28px}}a{{display:inline-block;margin-top:18px;padding:12px 18px;border-radius:10px;background:#fff;color:#07111f;text-decoration:none;font-weight:800}}</style>
</head><body><main><h1>✓ Verification complete</h1><p>Your verification is active for up to {validity} hours.</p><p>Return to VYRA and press Watch or Download again.</p><a href=\"{base}/\">Return to VYRA</a></main></body></html>"""
    response = web.Response(text=html, content_type="text/html")
    response.set_cookie(
        "vyra_verify",
        code,
        httponly=True,
        secure=True,
        samesite="Lax",
        max_age=max(3600, validity * 3600),
        path="/",
    )
    return response
