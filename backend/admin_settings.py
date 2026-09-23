"""Ultron-compatible website settings.

This module mirrors the important settings keys used by the supplied Ultron
AutoFilter project. Website settings are stored in a dedicated MongoDB
collection using the existing website MongoDB configuration; the AutoFilter
media collection is never written to.
"""
from __future__ import annotations

import asyncio
import json
import os
from copy import deepcopy
from urllib.parse import urlparse

from .config import MANUAL_PAYMENT_INSTRUCTIONS, MANUAL_PAYMENT_QR, PAYMENT_PROVIDER

DEFAULT_SETTINGS = {
    # Names intentionally follow the website UI, while ``ultron`` below keeps
    # the exact AutoFilter setting names available for compatibility/auditing.
    "verification": {
        "enabled": False,
        "shorteners": {
            "1": {"enabled": False, "name": "", "api": ""},
            "2": {"enabled": False, "name": "", "api": ""},
            "3": {"enabled": False, "name": "", "api": ""},
        },
        "verification_time_2": 0,
        "verification_time_3": 0,
        "validity_hours": 24,
        "tutorial_1": "",
        "tutorial_2": "",
        "tutorial_3": "",
        "file_mode": "file",
        "file_mode_type": "single",
        "shortlink_mode": "enabled",
    },
    "search": {
        "max_results": 1500,
        "results_per_page": 10,
        "result_mode": "buttons",
        "imdb_poster": False,
        # Devil parity: strict real-file search first. Local fuzzy matching can
        # manufacture a title grouping that the bot itself would not return.
        "fuzzy_fallback": False,
        "external_correction": True,
        "spell_check": True,
        "candidate_limit": 1500,
        "search_cache_ttl": 30,
        "search_cache_max": 256,
        "search_concurrency": 3,
    },
    "files": {
        "file_secure": False,
        "auto_delete": False,
        "auto_delete_seconds": 60,
        "custom_caption": "",
        "fsub_channels": [],
        "log_channel": "",
        "welcome": False,
    },
    "metadata": {
        "tmdb_enabled": False,
        "poster_fallback": True,
    },
    "payments": {
        "activation_mode": "environment",
        "premium_bypass_verification": True,
        "premium_bypass_shortener": True,
        "manual_enabled": PAYMENT_PROVIDER in {"manual", "both"},
        "upi_id": "",
        "manual_instructions": MANUAL_PAYMENT_INSTRUCTIONS,
        "manual_qr": MANUAL_PAYMENT_QR,
    },
    "site": {
        "maintenance": False,
    },
    "ultron": {
        "is_verify": False,
        "button": True,
        "max_btn": 10,
        "file_secure": False,
        "auto_delete": False,
        "auto_del_time": 60,
        "welcome": False,
        "imdb": False,
        "log": "",
        "fsub_id": [],
        "caption": "",
        "shortner": "",
        "api": "",
        "shortner_two": "",
        "api_two": "",
        "shortner_three": "",
        "api_three": "",
        "verify_time": 0,
        "third_verify_time": 0,
        "tutorial": "",
        "tutorial_2": "",
        "tutorial_3": "",
    },
}

SECRET_PATHS = {
    "verification.shorteners.1.api": "ultron.api",
    "verification.shorteners.2.api": "ultron.api_two",
    "verification.shorteners.3.api": "ultron.api_three",
}

LOCK = asyncio.Lock()
STATE = None
MONGO_SETTINGS_READY = False


def _merge(default, value):
    if isinstance(default, dict):
        result = deepcopy(default)
        if isinstance(value, dict):
            for key, item in value.items():
                if key in result:
                    result[key] = _merge(result[key], item)
        return result
    return deepcopy(value) if value is not None else deepcopy(default)


def _load_sync():
    global STATE
    # Website settings are Mongo-backed only. There is no /tmp state path, so
    # restarts and Koyeb instance replacement cannot silently lose settings.
    STATE = _merge(DEFAULT_SETTINGS, {})
    _sync_ultron_aliases(STATE)
    return deepcopy(STATE)

async def init_settings_store():
    """Load persistent website settings from the existing MongoDB database.

    The AutoFilter media collection is never written. A separate website
    collection stores all admin settings; no local /tmp persistence is used.
    """
    global STATE, MONGO_SETTINGS_READY
    from .web_store import ensure_indexes, load_settings_document, save_settings_document
    await ensure_indexes()
    async with LOCK:
        doc = await load_settings_document()
        if doc and isinstance(doc.get("settings"), dict):
            STATE = _merge(DEFAULT_SETTINGS, doc["settings"])
        else:
            STATE = _load_sync()
            await save_settings_document(STATE)
        _sync_ultron_aliases(STATE)
        _validate(STATE)
        MONGO_SETTINGS_READY = True
        return deepcopy(STATE)

def _sync_ultron_aliases(state):
    v = state["verification"]
    s = state["search"]
    f = state["files"]
    u = state["ultron"]
    u.update({
        "is_verify": bool(v["enabled"]),
        "button": s["result_mode"] == "buttons",
        "max_btn": int(s["max_results"]),
        "spell_check": bool(s.get("spell_check", True)),
        "file_secure": bool(f["file_secure"]),
        "auto_delete": bool(f["auto_delete"]),
        "auto_del_time": int(f["auto_delete_seconds"]),
        "welcome": bool(f["welcome"]),
        "imdb": bool(s["imdb_poster"]),
        "log": f["log_channel"],
        "fsub_id": list(f["fsub_channels"]),
        "caption": f["custom_caption"],
        "shortner_enabled": bool(v["shorteners"]["1"].get("enabled", False)),
        "shortner": v["shorteners"]["1"]["name"],
        "api": v["shorteners"]["1"]["api"],
        "shortner_two_enabled": bool(v["shorteners"]["2"].get("enabled", False)),
        "shortner_two": v["shorteners"]["2"]["name"],
        "api_two": v["shorteners"]["2"]["api"],
        "shortner_three_enabled": bool(v["shorteners"]["3"].get("enabled", False)),
        "shortner_three": v["shorteners"]["3"]["name"],
        "api_three": v["shorteners"]["3"]["api"],
        "verify_time": int(v["verification_time_2"]),
        "third_verify_time": int(v["verification_time_3"]),
        "tutorial": v["tutorial_1"],
        "tutorial_2": v["tutorial_2"],
        "tutorial_3": v["tutorial_3"],
    })


def get_settings():
    if STATE is None:
        return _load_sync()
    return deepcopy(STATE)


def get_value(*path, default=None):
    value = get_settings()
    for part in path:
        if not isinstance(value, dict) or part not in value:
            return default
        value = value[part]
    return default if value is None else value


def _valid_url(value):
    if not value:
        return True
    parsed = urlparse(str(value).strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _validate(state):
    v, s, f = state["verification"], state["search"], state["files"]
    if v["file_mode"] not in {"file", "allfiles", "notcopy"}:
        raise ValueError("verification.file_mode must be file, allfiles, or notcopy")
    if v["file_mode_type"] not in {"single", "group", "both"}:
        raise ValueError("verification.file_mode_type must be single, group, or both")
    if v["shortlink_mode"] not in {"enabled", "disabled"}:
        raise ValueError("verification.shortlink_mode must be enabled or disabled")
    payments = state.get("payments", {})
    if payments.get("activation_mode", "environment") not in {"environment", "auto", "manual"}:
        raise ValueError("payments.activation_mode must be environment, auto, or manual")
    # Website verification is browser-based. Telegram is not a required
    # verification dependency; each enabled stage must have a provider name
    # and API key before it is allowed to generate a short link.
    if s["result_mode"] not in {"buttons", "links"}:
        raise ValueError("search.result_mode must be buttons or links")
    s["max_results"] = max(1, min(200, int(s["max_results"])))
    s["results_per_page"] = max(10, min(50, int(s.get("results_per_page", 20))))
    s["candidate_limit"] = max(20, min(500, int(s["candidate_limit"])))
    s["search_cache_ttl"] = max(1, min(600, int(s["search_cache_ttl"])))
    s["search_cache_max"] = max(16, min(2048, int(s["search_cache_max"])))
    s["search_concurrency"] = max(1, min(16, int(s["search_concurrency"])))
    f["auto_delete_seconds"] = max(1, min(86400, int(f["auto_delete_seconds"])))
    v["verification_time_2"] = max(0, min(86400, int(v["verification_time_2"])))
    v["verification_time_3"] = max(0, min(86400, int(v["verification_time_3"])))
    v["validity_hours"] = max(1, min(720, int(v.get("validity_hours", 24))))
    for key in ("tutorial_1", "tutorial_2", "tutorial_3"):
        if v[key] and not _valid_url(v[key]):
            raise ValueError(f"{key} must be an http(s) URL")
    for number in ("1", "2", "3"):
        name = str(v["shorteners"][number]["name"] or "").strip()
        if name and (len(name) > 100):
            raise ValueError(f"shortener {number} name is too long")
        v["shorteners"][number]["name"] = name


def _apply_legacy_patch(state, patch):
    """Accept exact Ultron keys as well as the nested website schema."""
    u = patch.get("ultron") if isinstance(patch, dict) else None
    if not isinstance(u, dict):
        return
    v, s, f = state["verification"], state["search"], state["files"]
    mapping = {
        "is_verify": (v, "enabled"), "button": (s, "result_mode"),
        "file_secure": (f, "file_secure"), "auto_delete": (f, "auto_delete"),
        "auto_del_time": (f, "auto_delete_seconds"), "welcome": (f, "welcome"),
        "imdb": (s, "imdb_poster"), "log": (f, "log_channel"),
        "fsub_id": (f, "fsub_channels"), "caption": (f, "custom_caption"),
        "verify_time": (v, "verification_time_2"), "third_verify_time": (v, "verification_time_3"),
        "tutorial": (v, "tutorial_1"), "tutorial_2": (v, "tutorial_2"), "tutorial_3": (v, "tutorial_3"),
        "max_btn": (s, "max_results"),
    }
    for key, (target, field) in mapping.items():
        if key in u:
            value = u[key]
            if key == "button": value = "buttons" if value else "links"
            target[field] = value
    short_map = {"1": ("shortner", "api", "shortner_enabled"), "2": ("shortner_two", "api_two", "shortner_two_enabled"), "3": ("shortner_three", "api_three", "shortner_three_enabled")}
    for num, (name_key, api_key, enabled_key) in short_map.items():
        if name_key in u: v["shorteners"][num]["name"] = u[name_key]
        if api_key in u: v["shorteners"][num]["api"] = u[api_key]
        if enabled_key in u: v["shorteners"][num]["enabled"] = bool(u[enabled_key])


async def update_settings(patch):
    global STATE
    async with LOCK:
        current = get_settings()
        _apply_legacy_patch(current, patch or {})
        # Nested schema wins when supplied.
        current = _merge(current, patch or {})
        _sync_ultron_aliases(current)
        _validate(current)
        from .web_store import save_settings_document
        await save_settings_document(current)
        STATE = current
        return deepcopy(STATE)


async def remove_setting(path):
    global STATE
    async with LOCK:
        current = get_settings()
        keys = [k for k in str(path).split(".") if k]
        if not keys:
            return deepcopy(current)
        obj = current
        default_obj = DEFAULT_SETTINGS
        for key in keys[:-1]:
            if not isinstance(obj, dict) or key not in obj:
                return deepcopy(current)
            obj = obj[key]
            default_obj = default_obj.get(key, {}) if isinstance(default_obj, dict) else {}
        leaf = keys[-1]
        if isinstance(obj, dict) and leaf in obj:
            default_value = default_obj.get(leaf) if isinstance(default_obj, dict) else None
            obj[leaf] = deepcopy(default_value)
        _sync_ultron_aliases(current)
        _validate(current)
        from .web_store import save_settings_document
        await save_settings_document(current)
        STATE = current
        return deepcopy(current)


async def reset_settings():
    global STATE
    async with LOCK:
        STATE = deepcopy(DEFAULT_SETTINGS)
        _sync_ultron_aliases(STATE)
        from .web_store import save_settings_document
        await save_settings_document(STATE)
        return deepcopy(STATE)


def get_public_settings():
    """Return settings with shortener API secrets redacted."""
    value = get_settings()
    for number in ("1", "2", "3"):
        item = value.get("verification", {}).get("shorteners", {}).get(number, {})
        # The browser must never receive the secret itself, but it does need a
        # safe signal so the admin UI can distinguish "not configured" from
        # "configured but intentionally hidden".
        item["api_configured"] = bool(item.get("api"))
        if item.get("api"):
            item["api"] = None
    for key in ("api", "api_two", "api_three"):
        if value["ultron"].get(key):
            value["ultron"][key] = None
    return value
