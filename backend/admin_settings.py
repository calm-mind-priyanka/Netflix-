"""Ultron-compatible website settings.

This module mirrors the important settings keys used by the supplied Ultron
AutoFilter project, but stores them outside the AutoFilter MongoDB.  The
website never writes to the bot's media database.
"""
from __future__ import annotations

import asyncio
import json
import os
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_SETTINGS = {
    # Names intentionally follow the website UI, while ``ultron`` below keeps
    # the exact AutoFilter setting names available for compatibility/auditing.
    "verification": {
        "enabled": False,
        "shorteners": {
            "1": {"name": "", "api": ""},
            "2": {"name": "", "api": ""},
            "3": {"name": "", "api": ""},
        },
        "verification_time_2": 0,
        "verification_time_3": 0,
        "tutorial_1": "",
        "tutorial_2": "",
        "tutorial_3": "",
        "file_mode": "file",
        "file_mode_type": "single",
        "shortlink_mode": "enabled",
    },
    "search": {
        "max_results": 10,
        "result_mode": "buttons",
        "imdb_poster": False,
        "fuzzy_fallback": True,
        "external_correction": True,
        "candidate_limit": 120,
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
        "tmdb_enabled": True,
        "poster_fallback": True,
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
PATH = Path(os.getenv("WEBSITE_SETTINGS_FILE", "/tmp/streambox_settings.json"))
STATE = None


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
    try:
        if PATH.exists():
            with PATH.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
        else:
            raw = {}
    except Exception:
        raw = {}
    STATE = _merge(DEFAULT_SETTINGS, raw)
    # Upgrade old settings files that predate the flat Ultron compatibility map.
    _sync_ultron_aliases(STATE)
    return deepcopy(STATE)


def _write_sync(state):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = PATH.with_suffix(PATH.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, ensure_ascii=False)
    temp.replace(PATH)


def _sync_ultron_aliases(state):
    v = state["verification"]
    s = state["search"]
    f = state["files"]
    u = state["ultron"]
    u.update({
        "is_verify": bool(v["enabled"]),
        "button": s["result_mode"] == "buttons",
        "max_btn": int(s["max_results"]),
        "file_secure": bool(f["file_secure"]),
        "auto_delete": bool(f["auto_delete"]),
        "auto_del_time": int(f["auto_delete_seconds"]),
        "welcome": bool(f["welcome"]),
        "imdb": bool(s["imdb_poster"]),
        "log": f["log_channel"],
        "fsub_id": list(f["fsub_channels"]),
        "caption": f["custom_caption"],
        "shortner": v["shorteners"]["1"]["name"],
        "api": v["shorteners"]["1"]["api"],
        "shortner_two": v["shorteners"]["2"]["name"],
        "api_two": v["shorteners"]["2"]["api"],
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
    if s["result_mode"] not in {"buttons", "links"}:
        raise ValueError("search.result_mode must be buttons or links")
    s["max_results"] = max(1, min(50, int(s["max_results"])))
    s["candidate_limit"] = max(20, min(500, int(s["candidate_limit"])))
    s["search_cache_ttl"] = max(1, min(600, int(s["search_cache_ttl"])))
    s["search_cache_max"] = max(16, min(2048, int(s["search_cache_max"])))
    s["search_concurrency"] = max(1, min(16, int(s["search_concurrency"])))
    f["auto_delete_seconds"] = max(1, min(86400, int(f["auto_delete_seconds"])))
    v["verification_time_2"] = max(0, min(86400, int(v["verification_time_2"])))
    v["verification_time_3"] = max(0, min(86400, int(v["verification_time_3"])))
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
    short_map = {"1": ("shortner", "api"), "2": ("shortner_two", "api_two"), "3": ("shortner_three", "api_three")}
    for num, (name_key, api_key) in short_map.items():
        if name_key in u: v["shorteners"][num]["name"] = u[name_key]
        if api_key in u: v["shorteners"][num]["api"] = u[api_key]


async def update_settings(patch):
    global STATE
    async with LOCK:
        current = get_settings()
        _apply_legacy_patch(current, patch or {})
        # Nested schema wins when supplied.
        current = _merge(current, patch or {})
        _sync_ultron_aliases(current)
        _validate(current)
        _write_sync(current)
        STATE = current
        return deepcopy(STATE)


async def remove_setting(path):
    global STATE
    async with LOCK:
        current = get_settings()
        keys = str(path).split(".")
        obj = current
        for key in keys[:-1]:
            if not isinstance(obj, dict) or key not in obj:
                return deepcopy(current)
            obj = obj[key]
        if isinstance(obj, dict) and keys:
            key = keys[-1]
            # Remove-to-default mirrors Ultron's delete_group_setting semantics.
            defaults = DEFAULT_SETTINGS
            for part in keys:
                defaults = defaults.get(part, {}) if isinstance(defaults, dict) else {}
            obj[key] = deepcopy(defaults) if key in obj else obj.get(key)
        _sync_ultron_aliases(current)
        _validate(current)
        _write_sync(current)
        STATE = current
        return deepcopy(STATE)


async def reset_settings():
    global STATE
    async with LOCK:
        STATE = deepcopy(DEFAULT_SETTINGS)
        _sync_ultron_aliases(STATE)
        _write_sync(STATE)
        return deepcopy(STATE)


def get_public_settings():
    """Return settings with shortener API secrets redacted."""
    value = get_settings()
    for path in ("verification.shorteners.1.api", "verification.shorteners.2.api", "verification.shorteners.3.api"):
        obj = value
        keys = path.split(".")
        for key in keys[:-1]:
            obj = obj[key]
        if obj.get(keys[-1]):
            obj[keys[-1]] = None
    for key in ("api", "api_two", "api_three"):
        if value["ultron"].get(key):
            value["ultron"][key] = None
    return value
