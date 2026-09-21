"""Website verification/access gate.

Verification is one master access gate.  The configured shortener stages form
one sequential verification cycle:

    stage 1 -> stage 2 -> stage 3 -> full verification validity

Only enabled stages participate.  A user resumes from the stage they last
completed while the current cycle is alive.  Once the cycle expires, the next
attempt starts again at the first enabled stage.  The AutoFilter media data is
never modified by this module.
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


def _enabled_stages(settings=None):
    settings = settings or _settings()
    shorteners = settings.get("shorteners") or {}
    return [
        number
        for number in (1, 2, 3)
        if bool((shorteners.get(str(number)) or {}).get("enabled", False))
    ]


def _stage_gap(settings, stage: int) -> int:
    if stage == 2:
        return max(0, int(settings.get("verification_time_2", 0) or 0))
    if stage == 3:
        return max(0, int(settings.get("verification_time_3", 0) or 0))
    return 0


def _stage_label(stage: int, settings=None) -> str:
    settings = settings or _settings()
    item = (settings.get("shorteners") or {}).get(str(stage), {})
    name = str(item.get("name") or "").strip()
    return name or f"Shortener {stage}"


def _validity_hours(settings=None) -> int:
    settings = settings or _settings()
    return max(1, int(settings.get("validity_hours", 24) or 24))


def _format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds == 0:
        return "now"
    if seconds % 86400 == 0:
        days = seconds // 86400
        return f"{days} day" if days == 1 else f"{days} days"
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours} hour" if hours == 1 else f"{hours} hours"
    if seconds % 60 == 0:
        minutes = seconds // 60
        return f"{minutes} minute" if minutes == 1 else f"{minutes} minutes"
    return f"{seconds} seconds"


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


def required_stage(stage: int, verified_at: float, now: float | None = None) -> int:
    """Return the currently required stage in the active verification cycle.

    The first enabled stage starts the cycle.  After a stage is completed, the
    configured gap for the next enabled stage controls when that next stage is
    required.  A zero gap means the next stage is available immediately.
    """
    settings = _settings()
    now = now or time.time()
    enabled = _enabled_stages(settings)
    if not enabled:
        return 0

    current = int(stage or 0)
    if not verified_at or current not in enabled:
        return enabled[0]

    try:
        position = enabled.index(current)
    except ValueError:
        return enabled[0]

    if position >= len(enabled) - 1:
        return current

    next_stage = enabled[position + 1]
    gap = _stage_gap(settings, next_stage)
    if now - verified_at >= gap:
        return next_stage
    return current


def _verification_expiry(now: float) -> float:
    return now + _validity_hours() * 3600


def _base_url(request: web.Request) -> str:
    configured = PUBLIC_URL.rstrip("/") if PUBLIC_URL else ""
    return configured or str(request.url.origin())


async def _premium_bypasses(request: web.Request) -> bool:
    """Premium bypasses the complete verification/shortlink cycle."""
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

    enabled = _enabled_stages(settings)
    if not enabled:
        return {
            "verification_required": True,
            "verification_url": "",
            "tutorial_url": "",
            "stage": 0,
            "validity_hours": _validity_hours(settings),
            "verification_error": "Verification is enabled, but no verification shortener stage is enabled in Admin.",
        }

    row = await _load_cookie_row(request)
    now = time.time()
    stage = int(row.get("stage", 0) or 0) if row else 0
    verified_at = float(row.get("verified_at", 0) or 0) if row else 0.0
    required = required_stage(stage, verified_at, now) if row else enabled[0]

    # If the user has completed the currently due stage and its configured gap
    # has not elapsed, they are temporarily free to watch.  The final stage is
    # valid until the verification expiry.
    if row and bool(row.get("verified", False)) and verified_at and required == stage:
        return None

    from .web_store import ensure_indexes, verification_tokens

    await ensure_indexes()
    await init_settings_store()

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
            "shortener_name": _stage_label(required, settings),
            "validity_hours": _validity_hours(settings),
            "verification_error": str(exc),
        }

    tutorial = str(settings.get(f"tutorial_{required}") or "").strip()
    user = await current_user(request)
    user_id = user["user_id"] if user else ""
    issued = time.time()

    # Partial cycles get one fixed deadline.  Completing a later stage does not
    # silently extend the unfinished cycle.  If the user reaches the final
    # enabled stage, that completion starts the normal full validity window.
    if row and float(row.get("cycle_expires_at", 0) or 0) > issued:
        cycle_expires = float(row.get("cycle_expires_at"))
        cycle_started = float(row.get("cycle_started_at", issued) or issued)
    else:
        cycle_started = issued
        cycle_expires = _verification_expiry(issued)

    await verification_tokens.insert_one({
        "code": code,
        "user_id": user_id,
        "file_id": str(file_id),
        "stage": required,
        "verified_at": 0,
        "verified": False,
        "issued_at": issued,
        "expires": cycle_expires,
        "cycle_started_at": cycle_started,
        "cycle_expires_at": cycle_expires,
        "used": False,
        "short_url": short_url,
    })

    return {
        "verification_required": True,
        "verification_url": short_url,
        "direct_verification_url": destination,
        "tutorial_url": tutorial,
        "stage": required,
        "shortener_name": _stage_label(required, settings),
        "validity_hours": _validity_hours(settings),
        "cycle_expires_at": cycle_expires,
        "stage_gap_seconds": _stage_gap(settings, required),
    }


async def status(request: web.Request):
    settings = _settings()
    row = await _load_cookie_row(request)
    stage = int(row.get("stage", 0) or 0) if row else 0
    verified_at = float(row.get("verified_at", 0) or 0) if row else 0.0
    valid = bool(row and row.get("verified", False) and verified_at)
    if valid:
        valid = stage >= required_stage(stage, verified_at)
    enabled = _enabled_stages(settings)
    next_stage = required_stage(stage, verified_at) if valid else (enabled[0] if enabled else 0)
    return {
        "verification_enabled": bool(settings.get("enabled", False)),
        "verified": valid,
        "verification_stage": stage,
        "next_verification_stage": next_stage,
        "verification_shortener_name": _stage_label(next_stage, settings) if next_stage else "",
        "verified_at": verified_at,
        "validity_hours": _validity_hours(settings),
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

    completed_stage = int(row.get("stage", 0) or 0)
    settings = _settings()
    enabled = _enabled_stages(settings)
    if completed_stage not in enabled:
        raise web.HTTPBadRequest(text="This verification stage is no longer enabled. Please request a new verification link.")

    position = enabled.index(completed_stage)
    is_final = position == len(enabled) - 1
    cycle_started = float(row.get("cycle_started_at", row.get("issued_at", now)) or now)
    cycle_expires = float(row.get("cycle_expires_at", row.get("expires", now)) or now)

    if is_final:
        # The full verification period begins when the complete cycle finishes.
        expires = now + _validity_hours(settings) * 3600
    else:
        expires = cycle_expires
        if expires <= now:
            raise web.HTTPBadRequest(text="This verification cycle expired. Please start verification again.")

    result = await verification_tokens.update_one(
        {"code": code, "used": {"$ne": True}},
        {"$set": {
            "verified_at": now,
            "verified": True,
            "used": True,
            "expires": expires,
            "cycle_started_at": cycle_started,
            "cycle_expires_at": cycle_expires,
            "final_verified": is_final,
        }},
    )
    if result.modified_count != 1:
        raise web.HTTPBadRequest(text="Verification link has already been used. Please request a new verification link.")

    base = _base_url(request)
    next_stage = enabled[position + 1] if not is_final else 0
    completed_name = _stage_label(completed_stage, settings)

    if is_final:
        title = "✓ Verification completed"
        message = (
            f"All {len(enabled)} verification step{'s' if len(enabled) != 1 else ''} are complete. "
            f"You are verified for {_validity_hours(settings)} hours."
        )
        detail = "You can now return to VYRA and press Watch again."
    else:
        gap = _stage_gap(settings, next_stage)
        next_name = _stage_label(next_stage, settings)
        title = f"✓ Step {completed_stage} completed"
        if gap > 0:
            message = (
                f"{completed_name} verification is complete. You are free for {_format_duration(gap)}. "
                f"After that, Step {next_stage} ({next_name}) will be required."
            )
        else:
            message = (
                f"{completed_name} verification is complete. "
                f"Step {next_stage} ({next_name}) is ready now."
            )
        detail = "Return to VYRA and press Watch again when you are ready to continue."

    html = f"""<!doctype html>
<html lang="en"><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>body{{font-family:system-ui;background:#07111f;color:#fff;display:grid;place-items:center;min-height:100vh;margin:0}}main{{max-width:560px;text-align:center;padding:28px}}a{{display:inline-block;margin-top:18px;padding:12px 18px;border-radius:10px;background:#fff;color:#07111f;text-decoration:none;font-weight:800}}.muted{{opacity:.75;line-height:1.55}}</style>
</head><body><main><h1>{title}</h1><p>{message}</p><p class="muted">{detail}</p><a href="{base}/">Return to VYRA</a></main></body></html>"""
    response = web.Response(text=html, content_type="text/html")
    cookie_seconds = max(1, int(expires - now))
    response.set_cookie(
        COOKIE_NAME,
        code,
        httponly=True,
        secure=True,
        samesite="Lax",
        max_age=cookie_seconds,
        path="/",
    )
    return response
