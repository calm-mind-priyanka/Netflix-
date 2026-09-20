"""Shortener/provider adapter for website verification.

The website version keeps the user-facing verification flow in the browser.
This module only creates the stage URL through the configured shortener and
records which stage/link was issued.  It does not write to the AutoFilter media
collection and it does not require Telegram.

A shortener's API is used only to create the short link. Completion happens
when the user reaches the generated destination URL; providers that expose a
server-side completion/callback API can be added here without changing the
access gate.
"""
from __future__ import annotations

import asyncio
from urllib.parse import quote

from .admin_settings import get_settings, init_settings_store


async def shorten(url: str, stage: int) -> str:
    """Create a short link for a verification stage.

    If an enabled provider is misconfigured or unavailable, fail closed rather
    than silently turning a verification requirement into a direct link.
    """
    await init_settings_store()
    settings = get_settings().get("verification", {})
    if str(settings.get("shortlink_mode", "enabled")) != "enabled":
        return url

    item = (settings.get("shorteners") or {}).get(str(stage), {})
    if not bool(item.get("enabled", False)):
        return url

    site = str(item.get("name") or "").strip()
    api = str(item.get("api") or "").strip()
    if not site or not api:
        raise RuntimeError(f"Verification shortener stage {stage} is enabled but its site/API is not configured.")

    try:
        from shortzy import Shortzy
    except Exception as exc:
        raise RuntimeError("The shortener provider library is unavailable.") from exc

    try:
        shortener = Shortzy(api, site)
        try:
            result = await asyncio.wait_for(shortener.convert(url), timeout=6)
        except Exception:
            result = await asyncio.wait_for(shortener.get_quick_link(url), timeout=6)
        return str(result or url)
    except Exception as exc:
        raise RuntimeError(f"Verification shortener stage {stage} could not create a link.") from exc


def stage_destination(base_url: str, code: str, stage: int) -> str:
    """Build the signed/opaque website completion destination."""
    return (
        f"{base_url.rstrip('/')}/api/verify?code={quote(code, safe='')}&stage={int(stage)}"
    )
