import asyncio
import logging
import os
import re
import time
import secrets
from collections import OrderedDict
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, web

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
    SEARCH_MAX_DOCS,
    COLLECTION_NAME,
    DATABASE_NAME,
    DATABASE_URI,
    DATABASE_URI2,
    MULTIPLE_DB,
    PORT,
    SITE_SECRET,
    HOST,
    TMDB_API_KEY,
    TMDB_CACHE_MAX,
    telegram_ready,
)
from .admin_settings import get_settings as get_admin_settings, get_public_settings, get_value as get_admin_setting, update_settings as update_admin_settings, reset_settings as reset_admin_settings, remove_setting as remove_admin_setting, init_settings_store
from .premium import status as premium_status, create_order as premium_create_order, verify_payment as premium_verify_payment, webhook as premium_webhook, admin_grant as premium_admin_grant, admin_revoke as premium_admin_revoke, admin_extend as premium_admin_extend, admin_premium_users as premium_admin_users, get_status as get_premium_status, manual_submit as premium_manual_submit, my_manual as premium_my_manual, admin_manual_list as premium_admin_manual_list, admin_manual_proof as premium_admin_manual_proof, admin_manual_decide as premium_admin_manual_decide, plan_list as premium_plan_list
from .database import (
    collection_counts,
    find_media,
    iter_media,
    search_media,
    search_media_by_title,
    fuzzy_search_media,
    search_media_with_filters,
    build_search_filter,
    _SEARCH_PROJECTION,
    _normalize_id,
    media,
    media2,
)
from .parser import (
    normalize,
    normalize_async,
    normalize_for_search,
    normalize_query,
    parse_doc,
    search_title_score,
    stable_id,
    _CatalogBuilder,
)
from .stream import Streamer, create_client
from .ultron_search import UltronSearchEngine

LOGGER = logging.getLogger("vyra")
BASE = Path(__file__).resolve().parent.parent

META_CACHE = OrderedDict()
TMDB_CACHE_TTL = 86400
TMDB_SEMAPHORE = None
TMDB_SESSION = None

# Small in-process caches are important on Koyeb Free: repeated searches from
# many users should not repeat the same MongoDB parsing work. These caches are
# read-only and never write to the Auto Filter database.
SEARCH_CACHE = OrderedDict()
SEARCH_CACHE_TTL = 30
SEARCH_CACHE_MAX = 256
SEARCH_INFLIGHT = {}
# Cache the complete logical title group so every Season/Episode/Language/Quality
# click does not re-query and re-parse the same MongoDB records.
GROUP_CACHE = OrderedDict()
GROUP_CACHE_TTL = 120
GROUP_CACHE_MAX = 64
GROUP_INFLIGHT = {}
SEARCH_SEMAPHORE = asyncio.Semaphore(3)
SEARCH_CANDIDATE_LIMIT = min(max(120, int(os.getenv("SEARCH_CANDIDATE_LIMIT", "250"))), 500)
HOME_CACHE = None
HOME_CACHE_TIME = 0.0
ULTRON_SEARCH = UltronSearchEngine()
ULTRON_SEARCH_CONCURRENCY = 3
from .web_store import ensure_indexes as ensure_web_indexes, get_catalog_identity
from .verification import requirement as verification_requirement, status as verification_status, complete as verification_complete
from .users import create_account as user_create_account, login as user_login, logout as user_logout, me as user_me, update_nickname as user_update_nickname, admin_users_list, admin_user_detail, admin_payments, admin_history, admin_history_delete, admin_history_clear

# Keep homepage catalog work bounded. The website only needs enough recent
# media records to build the visible homepage; it must never materialize the
# entire bot collection in RAM on a small Koyeb instance.
HOME_DOC_LIMIT = 300
HOME_TITLE_LIMIT = 100
HOME_ENRICH_LIMIT = 100
SEARCH_ENRICH_LIMIT = 1
# Never let an environment value such as 10000 turn one HTTP request into a
# huge in-memory MongoDB result set. The exact title can still have many real
# variants; 300 is the default/safety ceiling for a single web request on Koyeb Free.
TITLE_VARIANT_LIMIT = min(max(300, int(SEARCH_MAX_DOCS)), 1500)

# Maintenance is deliberately kept in memory. The website must not create or
# modify a collection inside the Auto Filter Bot's MongoDB database.
MAINTENANCE = bool(get_admin_setting("site", "maintenance", default=False))


async def all_titles(limit=None):
    """Build the bounded homepage catalog, cached briefly in this process."""
    global HOME_CACHE, HOME_CACHE_TIME
    if not DATABASE_URI2:
        raise RuntimeError("DATABASE_URI2 media database is not configured")

    requested = HOME_DOC_LIMIT if limit is None else int(limit)
    bounded = max(1, min(requested, CATALOG_MAX_DOCS, HOME_DOC_LIMIT))
    now = time.time()
    if bounded == HOME_DOC_LIMIT and HOME_CACHE is not None and now - HOME_CACHE_TIME < max(5, CATALOG_TTL):
        return HOME_CACHE

    projection = {
        "_id": 1, "file_id": 1, "file_ref": 1, "file_name": 1,
        "file_size": 1, "file_type": 1, "mime_type": 1, "caption": 1,
        "tmdb_id": 1, "tmdbId": 1, "tmdb": 1,
    }
    result = await normalize_async(iter_media(projection=projection, limit=bounded))
    if bounded == HOME_DOC_LIMIT:
        HOME_CACHE, HOME_CACHE_TIME = result, now
    return result


async def _tmdb_session():
    global TMDB_SESSION, TMDB_SEMAPHORE
    if TMDB_SESSION is None or TMDB_SESSION.closed:
        TMDB_SESSION = ClientSession(timeout=ClientTimeout(total=4))
    if TMDB_SEMAPHORE is None:
        TMDB_SEMAPHORE = asyncio.Semaphore(8)
    return TMDB_SESSION, TMDB_SEMAPHORE


def _tmdb_cache_get(key):
    cached = META_CACHE.get(key)
    if not cached:
        return None
    if time.time() - cached[0] >= TMDB_CACHE_TTL:
        META_CACHE.pop(key, None)
        return None
    META_CACHE.move_to_end(key)
    return cached[1]


def _tmdb_cache_put(key, value):
    META_CACHE[key] = (time.time(), value)
    META_CACHE.move_to_end(key)
    while len(META_CACHE) > TMDB_CACHE_MAX:
        META_CACHE.popitem(last=False)


def _tmdb_image_url(path, size):
    # TMDB returns image paths such as "/abc123.jpg". Keep the browser-facing
    # URL absolute and use a moderate size so mobile clients do not pull
    # unnecessarily large images.
    if not path:
        return None
    value = str(path).strip()
    if not value.startswith("/"):
        value = "/" + value
    return f"https://image.tmdb.org/t/p/{size}{value}"


async def tmdb_meta(title, kind, year=None):
    if not TMDB_API_KEY or not (bool(get_admin_setting("metadata", "tmdb_enabled", default=True)) or bool(TMDB_API_KEY)):
        return {}

    key = (kind, title.casefold(), year)
    cached = _tmdb_cache_get(key)
    if cached is not None:
        return cached

    endpoint = "tv" if kind == "series" else "movie"
    url = f"https://api.themoviedb.org/3/search/{endpoint}"
    session, semaphore = await _tmdb_session()
    try:
        async with semaphore:
            async with session.get(
                url,
                params={
                    "api_key": TMDB_API_KEY,
                    "query": title,
                    "include_adult": "false",
                    **({"year": year} if year and kind == "movie" else {}),
                    **({"first_air_date_year": year} if year and kind == "series" else {}),
                },
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    LOGGER.warning(
                        "TMDB search returned HTTP %s for %r: %s",
                        response.status,
                        title,
                        body[:180],
                    )
                    return {}
                data = await response.json(content_type=None)

        results = data.get("results") or []
        wanted = normalize_for_search(title)
        result = next(
            (candidate for candidate in results
             if normalize_for_search(candidate.get("title") or candidate.get("name")) == wanted),
            results[0] if results else None,
        )
        if not result:
            return {}

        date = result.get("first_air_date") or result.get("release_date") or ""
        output = {
            "poster": _tmdb_image_url(result.get("poster_path"), "w342"),
            "backdrop": _tmdb_image_url(result.get("backdrop_path"), "w780"),
            "description": result.get("overview"),
            "year": int(date[:4]) if date[:4].isdigit() else None,
            "rating": result.get("vote_average"),
        }
        _tmdb_cache_put(key, output)
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


async def tmdb_season_meta(title, season, year=None):
    """Return season-specific TMDB artwork/metadata when the request targets Sxx."""
    if not TMDB_API_KEY or season is None:
        return {}
    try:
        base = await tmdb_meta(title, "series", year)
        # tmdb_meta does not expose the TMDB id, so search the TV endpoint again
        # only for the season-specific poster. This remains metadata-only; real
        # Telegram/AutoFilter files stay the source of truth.
        session, semaphore = await _tmdb_session()
        async with semaphore:
            async with session.get(
                "https://api.themoviedb.org/3/search/tv",
                params={"api_key": TMDB_API_KEY, "query": title, "include_adult": "false"},
            ) as response:
                if response.status != 200:
                    return {}
                data = await response.json(content_type=None)
        results = data.get("results") or []
        wanted = normalize_for_search(title)
        result = next(
            (candidate for candidate in results
             if normalize_for_search(candidate.get("name")) == wanted),
            results[0] if results else None,
        )
        if not result or not result.get("id"):
            return {}
        async with semaphore:
            async with session.get(
                f"https://api.themoviedb.org/3/tv/{int(result['id'])}/season/{int(season)}",
                params={"api_key": TMDB_API_KEY},
            ) as response:
                if response.status != 200:
                    return {}
                data = await response.json(content_type=None)
        return {
            "poster": _tmdb_image_url(data.get("poster_path"), "w500"),
            "description": data.get("overview") or base.get("description"),
            "rating": data.get("vote_average") if data.get("vote_average") is not None else base.get("rating"),
        }
    except Exception:
        LOGGER.exception("TMDB season lookup failed for %s S%s", title, season)
        return {}


def _search_score(item, query_title):
    return search_title_score(item["title"], query_title)


async def home(request):
    # The public home is intentionally text-first. It never calls TMDB and never
    # renders a poster catalog; this keeps the site close to Ultron's lightweight
    # AutoFilter feel and makes Koyeb Free much cheaper to serve.
    items = (await all_titles(limit=HOME_DOC_LIMIT))[:HOME_TITLE_LIMIT]
    clean = []
    for item in items:
        clean.append({
            "id": item.get("id"),
            "title": item.get("title"),
            "type": item.get("type", "movie"),
            "year": item.get("year"),
            "languages": item.get("languages") or item.get("audio_languages") or [],
        })
    return web.json_response({"ok": True, "items": clean, "count": len(clean)})


async def access_status(request):
    from .users import current_user
    state = await verification_status(request)
    user = await current_user(request)
    premium = await get_premium_status(user["user_id"]) if user else {"premium": False, "expires_at": 0}
    return web.json_response({
        "ok": True,
        **state,
        "authenticated": bool(user),
        "user_id": user.get("user_id", "") if user else "",
        "premium": bool(premium.get("premium")),
        "expires_at": int(premium.get("expires_at", 0) or 0),
    })


def _norm_setting(value):
    return re.sub(r"[- .]+", "", str(value or "")).casefold()


def _variant_matches_request(variant, parsed):
    if parsed.get("year") is not None and variant.get("year") != parsed["year"]:
        return False
    if parsed.get("season") is not None and variant.get("season") != parsed["season"]:
        return False
    if parsed.get("episode") is not None and variant.get("episode") != parsed["episode"]:
        return False
    wanted_quality = parsed.get("quality")
    if wanted_quality:
        got = re.sub(r"\s+", "", str(variant.get("quality") or "")).casefold().rstrip("p")
        want = re.sub(r"\s+", "", wanted_quality).casefold().rstrip("p")
        if got != want:
            return False
    wanted_source = parsed.get("source")
    if wanted_source and _norm_setting(variant.get("source")) != _norm_setting(wanted_source):
        return False
    wanted_languages = parsed.get("languages") or ([parsed.get("language")] if parsed.get("language") else [])
    if wanted_languages:
        languages = {str(x).casefold() for x in (variant.get("audio_languages") or variant.get("languages") or [])}
        if any(str(w).casefold() not in languages for w in wanted_languages):
            return False
    return True


def _title_has_matching_variant(item, parsed):
    if item.get("type") == "series":
        return any(
            _variant_matches_request(variant, parsed)
            for season in (item.get("seasons") or [])
            for episode in (season.get("episodes") or [])
            for variant in (episode.get("variants") or [])
        )
    return any(_variant_matches_request(v, parsed) for v in (item.get("variants") or []))



# Keep this list identical to the IGNORE_WORDS used by the supplied Ultron bot.
ULTRON_IGNORE_WORDS = [
    "movies", "movie", "episode", "episodes", "south indian", "south indian movie",
    "south movie", "web-series", "web series", "webseries", "hindi me bhejo", "ful",
    ",", "!", "kro", "jaldi", "audio", "language", "mkv", "mp4", "web", "series",
    "hollywood", "all", "bollywood", "south", "hd", "karo", "upload", "bhejo",
    "fullepisode", "please", "plz", "send", "link", "dabbed", "dubbed", "season",
]

def _autofilter_prepare_query(query):
    """Use the same pre-search cleanup as the supplied Ultron bot."""
    value = str(query or "").strip().lower()
    # Ultron's replace_words removes whole ignore words, longest first.
    words = sorted((w for w in ULTRON_IGNORE_WORDS if w), key=len, reverse=True)
    if words:
        pattern = r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b"
        value = re.sub(pattern, "", value, flags=re.IGNORECASE)
    value = value.replace("-", " ").replace(":", "").replace("'", "")
    return re.sub(r"\s+", " ", value).strip()


def _build_selected_query(base_query, season=None, episode=None, language=None, quality=None):
    """Build a cumulative Auto Filter query without duplicating metadata tokens."""
    parsed = normalize_query(base_query)
    title = parsed.get("title") or base_query
    parts = [title]
    if parsed.get("year") is not None:
        parts.append(str(parsed["year"]))
    s = season if season is not None else parsed.get("season")
    e = episode if episode is not None else parsed.get("episode")
    if s is not None:
        parts.append(f"S{int(s):02d}" + (f"E{int(e):02d}" if e is not None else ""))
    elif e is not None:
        parts.append(f"E{int(e):02d}")
    if language:
        parts.append(str(language))
    if quality:
        parts.append(str(quality))
    return " ".join(str(x) for x in parts if str(x).strip())


def _parsed_files(docs):
    return [x for x in (parse_doc(doc) for doc in docs) if x.get("file_id") and not x.get("_parse_error")]


def _file_sort_key(item):
    match = re.search(r"\d+", str(item.get("quality") or ""))
    quality = int(match.group(0)) if match else 0
    return (quality, str(item.get("file_name") or "").casefold())


async def _raw_autofilter_files(query, limit=None):
    prepared = _autofilter_prepare_query(query)
    if not prepared:
        return []
    bounded = int(get_admin_setting("search", "candidate_limit", default=SEARCH_CANDIDATE_LIMIT)) if limit is None else min(max(1, int(limit)), 1500)
    async with SEARCH_SEMAPHORE:
        docs = await search_media(prepared, limit=bounded)
        if not docs:
            parsed_query = normalize_query(prepared)
            title = parsed_query.get("title") or prepared
            docs = await search_media(title, limit=bounded)
            if (not docs and parsed_query.get("year") is None and
                bool(get_admin_setting("search", "fuzzy_fallback", default=True))):
                docs = await fuzzy_search_media(title, limit=min(40, bounded))
        return await asyncio.to_thread(_parsed_files, docs)


async def _search_uncached(query):
    """Resolve a query to logical title groups, then load the complete group.

    Auto Filter is used only to locate real stored records.  Season/episode
    tokens are search context, not part of the logical title identity.  Once a
    real title is identified, the complete title pool is loaded so an episode
    query such as ``Reacher S04E07`` still opens all real Reacher seasons and
    episodes.
    """
    prepared_query = _autofilter_prepare_query(query)
    if not prepared_query:
        return []

    parsed = normalize_query(prepared_query)
    search_title = parsed.get("title") or prepared_query
    base_parts = [search_title]
    if parsed.get("year") is not None:
        base_parts.append(str(parsed["year"]))
    base_query = " ".join(base_parts).strip()

    async with SEARCH_SEMAPHORE:
        docs = await search_media(base_query, limit=int(get_admin_setting("search", "candidate_limit", default=SEARCH_CANDIDATE_LIMIT)))
        if not docs and search_title.casefold() != base_query.casefold():
            docs = await search_media(search_title, limit=int(get_admin_setting("search", "candidate_limit", default=SEARCH_CANDIDATE_LIMIT)))
        if (not docs and parsed.get("year") is None and
            bool(get_admin_setting("search", "fuzzy_fallback", default=True))):
            fuzzy_docs = await fuzzy_search_media(search_title, limit=min(40, int(get_admin_setting("search", "candidate_limit", default=SEARCH_CANDIDATE_LIMIT))))
            if fuzzy_docs:
                fuzzy_parsed = parse_doc(fuzzy_docs[0])
                corrected_title = fuzzy_parsed.get("title") or search_title
                docs = await search_media(corrected_title, limit=SEARCH_CANDIDATE_LIMIT)

    raw_items = await asyncio.to_thread(_parsed_files, docs)
    if not raw_items:
        return []

    # Score logical identities, not individual files.  An exact title wins over
    # a longer title such as "Toxic Love", while still allowing fuzzy fallback.
    groups = {}
    for item in raw_items:
        title = str(item.get("title") or "Untitled").strip()
        kind = str(item.get("type") or "movie").casefold()
        year = item.get("year")
        key = (normalize_for_search(title), kind, year)
        groups.setdefault(key, {
            "title": title, "type": kind, "year": year,
            "count": 0, "season": item.get("season"), "episode": item.get("episode")
        })
        groups[key]["count"] += 1

    ranked = []
    for group in groups.values():
        score = search_title_score(group["title"], search_title)
        # When a year was explicitly requested, don't silently prefer a
        # different known release year.
        if parsed.get("year") is not None and group.get("year") not in (None, parsed["year"]):
            score -= 0.20
        ranked.append((score, group))
    ranked.sort(key=lambda pair: (-pair[0], -pair[1]["count"], pair[1]["title"].casefold()))

    if not ranked or ranked[0][0] < 0.72:
        return []

    # If an exact logical title exists, suppress broader title matches. This is
    # the important distinction between "Toxic" and "Toxic Love", for example.
    exact = [pair for pair in ranked if normalize_for_search(pair[1]["title"]) == normalize_for_search(search_title)]
    chosen = exact[0][1] if exact else ranked[0][1]

    # Load the complete real group, not the small search candidate window.
    async with SEARCH_SEMAPHORE:
        full = await _load_grouped_title(
            chosen["title"],
            None,
            year_hint=parsed.get("year") if parsed.get("year") is not None else chosen.get("year"),
        )
    if not full:
        return []

    # Keep query context only as selection state. It never removes seasons or
    # episodes from the returned logical title.
    full["search_season"] = parsed.get("season")
    full["search_episode"] = parsed.get("episode")
    full["search_language"] = parsed.get("language")
    full["search_quality"] = parsed.get("quality")

    if TMDB_API_KEY:
        try:
            full = await enrich(full)
        except Exception:
            LOGGER.debug("TMDB enrichment failed for search result", exc_info=True)
    return [full]


async def search(request):
    query = request.query.get("q", "").strip()
    if not query:
        return web.json_response({"ok": True, "items": [], "count": 0, "total": 0, "page": 0, "page_size": 10})

    # Website behavior is fixed to ten visible results per page.  This is the
    # web equivalent of Devil's configurable result window: page 1 shows 1-10,
    # and the next page continues with the remaining real matches.
    max_results = max(10, min(SEARCH_MAX_DOCS, int(SEARCH_MAX_DOCS)))
    page_size = 10
    try:
        page = max(0, int(request.query.get("page", "0")))
    except ValueError:
        page = 0
    candidate_limit = max(10, min(SEARCH_MAX_DOCS, int(SEARCH_MAX_DOCS)))
    # Strict real-file search is the default.  If it returns nothing, the
    # Devil-style spell-check path may try an external title correction, but
    # only the corrected title is accepted if real AutoFilter files exist.
    spell_check = bool(get_admin_setting("search", "spell_check", default=True))
    # Keep the primary website result set strict like Devil.  Local fuzzy
    # matching is deliberately disabled; spell-check correction is allowed only
    # after strict search returns nothing, exactly as the bot's fallback path.
    fuzzy = False
    external = spell_check
    ttl = max(1, int(get_admin_setting("search", "search_cache_ttl", default=30)))
    global ULTRON_SEARCH_CONCURRENCY
    desired_concurrency = max(1, min(16, int(get_admin_setting("search", "search_concurrency", default=3))))
    if desired_concurrency != ULTRON_SEARCH_CONCURRENCY:
        ULTRON_SEARCH_CONCURRENCY = desired_concurrency
        ULTRON_SEARCH.semaphore = asyncio.Semaphore(desired_concurrency)

    key = ULTRON_SEARCH._key(query)
    cached = ULTRON_SEARCH.cache.get(key)
    if cached and time.time() - cached[0] < ttl:
        ULTRON_SEARCH.cache.move_to_end(key)
        selected = cached[1][:max_results]
        total = len(selected)
        start = page * page_size
        return web.json_response({
            "ok": True,
            "items": selected[start:start + page_size],
            "count": len(selected[start:start + page_size]),
            "total": total,
            "files": sum(int(i.get("count") or 0) for i in selected),
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
            "cached": True,
        })

    # Fetch the complete configured search set once, then paginate it in the
    # HTTP response. UltronSearchEngine still uses bounded Mongo queries and
    # single-flight concurrency, so this does not create one DB query per page.
    selected, cached_flag = await ULTRON_SEARCH.search(
        query,
        candidate_limit=candidate_limit,
        max_results=max_results,
        fuzzy=fuzzy,
        external_correction=external,
    )

    if selected and TMDB_API_KEY and (bool(get_admin_setting("metadata", "tmdb_enabled", default=True)) or bool(TMDB_API_KEY)):
        targets = [i for i, item in enumerate(selected) if not item.get("poster")][:2]
        if targets and bool(get_admin_setting("metadata", "poster_fallback", default=True)):
            values = await asyncio.gather(*(enrich(selected[i]) for i in targets), return_exceptions=True)
            for i, value in zip(targets, values):
                if not isinstance(value, Exception):
                    selected[i] = value

    if selected:
        ULTRON_SEARCH.cache[key] = (time.time(), selected)
        ULTRON_SEARCH.cache.move_to_end(key)
        cache_max = max(16, int(get_admin_setting("search", "search_cache_max", default=256)))
        while len(ULTRON_SEARCH.cache) > cache_max:
            ULTRON_SEARCH.cache.popitem(last=False)

    total = len(selected)
    start = page * page_size
    visible = selected[start:start + page_size]
    return web.json_response({
        "ok": True,
        "items": visible,
        "count": len(visible),
        "total": total,
        "files": sum(int(i.get("count") or 0) for i in selected),
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "cached": cached_flag,
    })


async def search_files(request):
    """Devil/AutoFilter-style real-file search for the website.

    Unlike the grouped title search, this endpoint keeps every matching real
    media record separate. This is what the screenshot-style result panel needs:
    if 20 real files match, the website gets those 20 real files, not 20 title
    guesses and not synthetic combinations.
    """
    query = request.query.get("q", "").strip()
    if not query:
        return web.json_response({"ok": True, "files": [], "total": 0, "page": 0, "page_size": 10, "pages": 1,
                                  "languages": [], "qualities": [], "seasons": [], "episodes": {}})

    try:
        page = max(0, int(request.query.get("page", "0")))
    except ValueError:
        page = 0
    page_size = max(5, min(20, int(get_admin_setting("search", "results_per_page", default=10))))

    prepared = ULTRON_SEARCH.prepare_query(query)
    docs = await search_media(prepared, limit=SEARCH_MAX_DOCS)

    # Keep the same Devil-style fallback semantics: correction is allowed only
    # after the strict real-file search fails, and the corrected title is used
    # only when it resolves back to real AutoFilter records.
    corrected = None
    if not docs and bool(get_admin_setting("search", "spell_check", default=True)):
        fallback, corrected = await ULTRON_SEARCH._local_correction(query, SEARCH_MAX_DOCS)
        docs = fallback
        if not docs:
            docs, corrected = await ULTRON_SEARCH._imdb_correct(query, SEARCH_MAX_DOCS)

    parsed_files = []
    seen = set()
    for doc in docs:
        item = parse_doc(doc)
        if not item.get("file_id") or item.get("_parse_error"):
            continue
        fid = str(item["file_id"])
        if fid in seen:
            continue
        seen.add(fid)
        parsed_files.append(item)

    # Query-level filters operate on the real parsed file records.
    query_context = normalize_query(query)
    language = request.query.get("language", "").strip().casefold() or str(query_context.get("language") or "").casefold()
    quality = request.query.get("quality", "").strip().casefold() or str(query_context.get("quality") or "").casefold()
    season = request.query.get("season", "").strip() or (str(query_context.get("season")) if query_context.get("season") is not None else "")
    episode = request.query.get("episode", "").strip() or (str(query_context.get("episode")) if query_context.get("episode") is not None else "")

    def quality_norm(v):
        return re.sub(r"\s+", "", str(v or "")).casefold().rstrip("p")

    filtered = []
    for item in parsed_files:
        if language and language not in {str(x).casefold() for x in (item.get("languages") or [])} and language not in str(item.get("language") or "").casefold():
            continue
        if quality and quality_norm(item.get("quality")) != quality_norm(quality):
            continue
        if season.isdigit() and item.get("season") != int(season):
            continue
        if episode.isdigit() and item.get("episode") != int(episode):
            continue
        filtered.append(item)

    # Prefer the same practical ordering users see in AutoFilter: newest DB
    # records first, while still making quality deterministic when available.
    def sort_key(x):
        m = re.search(r"\d+", str(x.get("quality") or ""))
        qn = int(m.group(0)) if m else 0
        return (str(x.get("file_name") or "").casefold(), qn)
    filtered.sort(key=sort_key, reverse=True)

    languages = sorted({str(x) for f in parsed_files for x in (f.get("languages") or [])
                        if str(x).strip() and str(x).casefold() != "unknown"}, key=str.casefold)
    qualities = sorted({str(f.get("quality")) for f in parsed_files if f.get("quality") and str(f.get("quality")).casefold() != "auto"},
                       key=lambda x: (int(re.search(r"\d+", x).group()) if re.search(r"\d+", x) else 9999, x.casefold()))
    seasons = sorted({int(f["season"]) for f in parsed_files if f.get("season") is not None})
    episodes = {}
    for f in parsed_files:
        if f.get("season") is not None and f.get("episode") is not None:
            episodes.setdefault(str(int(f["season"])), set()).add(int(f["episode"]))
    episodes = {k: sorted(v) for k, v in episodes.items()}

    # Attach one movie/series identity block to the result panel. The real
    # Telegram files remain the source of truth; TMDB is only used for the
    # poster/description presentation, matching the AutoFilter-style poster
    # shown above the file list.
    poster = next((str(f.get("poster") or "") for f in filtered if f.get("poster")), "")
    first = filtered[0] if filtered else (parsed_files[0] if parsed_files else None)
    panel_title = str((first or {}).get("title") or query).strip()
    panel_type = (first or {}).get("type") or "movie"
    panel_year = (first or {}).get("year")
    description = ""
    rating = None
    requested_season = int(season) if season.isdigit() else None
    if (bool(get_admin_setting("metadata", "tmdb_enabled", default=True)) or bool(TMDB_API_KEY)) and TMDB_API_KEY and first:
        if requested_season is not None and panel_type == "series":
            meta = await tmdb_season_meta(panel_title, requested_season, panel_year)
        else:
            meta = await tmdb_meta(panel_title, panel_type, panel_year)
        poster = poster or str(meta.get("poster") or "")
        description = str(meta.get("description") or "")
        rating = meta.get("rating")
        panel_year = panel_year or meta.get("year")

    total = len(filtered)
    start = page * page_size
    visible = filtered[start:start + page_size]
    for f in visible:
        # Useful to the browser without making title identity dependent on a
        # displayed filename. The real file id remains authoritative.
        f["display_index"] = start + visible.index(f) + 1

    return web.json_response({
        "ok": True,
        "files": visible,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "languages": languages,
        "qualities": qualities,
        "seasons": seasons,
        "episodes": episodes,
        "corrected_query": corrected,
        "title": panel_title,
        "type": panel_type,
        "year": panel_year,
        "poster": poster,
        "description": description,
        "rating": rating,
        "requested_season": requested_season,
    })


async def _cached_or_search_items(query):
    """Reuse a recent grouped search result for title/resolve requests."""
    key = re.sub(r"\s+", " ", str(query)).strip().casefold()
    cached = SEARCH_CACHE.get(key)
    if cached and time.time() - cached[0] < int(get_admin_setting("search", "search_cache_ttl", default=SEARCH_CACHE_TTL)):
        SEARCH_CACHE.move_to_end(key)
        return cached[1]
    task = SEARCH_INFLIGHT.get(key)
    if task is None:
        task = asyncio.create_task(_search_uncached(query))
        SEARCH_INFLIGHT[key] = task
    try:
        result = await task
    finally:
        if SEARCH_INFLIGHT.get(key) is task:
            SEARCH_INFLIGHT.pop(key, None)
    if result:
        SEARCH_CACHE[key] = (time.time(), result)
        SEARCH_CACHE.move_to_end(key)
        while len(SEARCH_CACHE) > int(get_admin_setting("search", "search_cache_max", default=SEARCH_CACHE_MAX)):
            SEARCH_CACHE.popitem(last=False)
    return result


async def _load_grouped_title(title_name, title_id=None, year_hint=None):
    """Load one logical title group and cache it briefly in-process.

    Search discovery is intentionally bounded, but once a title is identified
    this loader reads the title's real Mongo records and builds one grouped
    object. The short cache is especially important on Koyeb Free because a
    user commonly clicks several filters for the same title in succession.
    """
    name = str(title_name or "").strip()
    if not name:
        return None

    cache_key = (normalize_for_search(name), int(year_hint) if year_hint is not None else None, str(title_id or ""))
    now = time.time()
    cached = GROUP_CACHE.get(cache_key)
    if cached and now - cached[0] < GROUP_CACHE_TTL:
        GROUP_CACHE.move_to_end(cache_key)
        value = cached[1]
        if title_id and value.get("id") != title_id:
            return None
        return value
    if cached:
        GROUP_CACHE.pop(cache_key, None)

    # Single-flight the expensive title reconstruction. Filter buttons often
    # fire close together, and they should share one Mongo read/parse pass.
    inflight = GROUP_INFLIGHT.get(cache_key)
    if inflight is None:
        async def build():
            # Keep the Auto Filter-compatible title regex, but stream records
            # instead of materializing a large list. This keeps memory bounded
            # for titles with many episodes/files.
            search_filter = build_search_filter(name)
            if not search_filter:
                return None
            projection = _SEARCH_PROJECTION
            # Build the logical catalog incrementally. We never keep the raw
            # MongoDB documents for the entire title in memory.
            builder = _CatalogBuilder()
            seen = set()
            configured = [("secondary", media2)] if media2 is not None else []
            succeeded = 0
            errors = []
            for db_name, collection in configured:
                try:
                    # Hard bound this reconstruction pass. The previous code
                    # streamed every matching document in the AutoFilter collection,
                    # which could make a single search consume all Koyeb CPU/RAM.
                    cursor = (collection.find(search_filter, projection)
                              .sort("$natural", -1)
                              .limit(TITLE_VARIANT_LIMIT))
                    async for doc in cursor:
                        key = _normalize_id(doc.get("_id")) or _normalize_id(doc.get("file_id"))
                        if key and key in seen:
                            continue
                        if key:
                            seen.add(key)
                        builder.add(doc)
                    succeeded += 1
                except Exception as exc:
                    errors.append(f"{db_name}: {type(exc).__name__}")
                    LOGGER.warning("%s MongoDB complete-title read failed: %s", db_name, type(exc).__name__)
            if configured and succeeded == 0:
                raise RuntimeError(
                    "All configured MongoDB databases are unreachable or the collection cannot be searched ("
                    + ", ".join(errors) + ")"
                )

            items = builder.finish()

            # The result id is authoritative.  Several real releases can share
            # the same display title (e.g. same-name movies/years), and choosing
            # the first title before checking the id caused "Title not found" on
            # the filter panel even though the file existed.
            value = next((item for item in items if title_id and item.get("id") == title_id), None)

            if value is None:
                wanted = normalize_for_search(name)
                exact = [item for item in items if normalize_for_search(item.get("title")) == wanted]
                if year_hint is not None:
                    year_exact = [item for item in exact if item.get("year") in (None, year_hint)]
                    if year_exact:
                        exact = year_exact
                value = exact[0] if exact else next(
                    (item for item in items if search_title_score(item.get("title"), name) >= 0.90),
                    None,
                )
            if value:
                GROUP_CACHE[cache_key] = (time.time(), value)
                GROUP_CACHE.move_to_end(cache_key)
                while len(GROUP_CACHE) > GROUP_CACHE_MAX:
                    GROUP_CACHE.popitem(last=False)
            return value

        inflight = asyncio.create_task(build())
        GROUP_INFLIGHT[cache_key] = inflight
    try:
        value = await inflight
    finally:
        if GROUP_INFLIGHT.get(cache_key) is inflight:
            GROUP_INFLIGHT.pop(cache_key, None)

    if title_id and value and value.get("id") != title_id:
        return None
    return value


async def _resolve_group_robust(title_name="", title_id=None, year_hint=None):
    """Resolve a real AutoFilter title without trusting a fragile display-title lookup.

    First use the logical catalog/group cache. If that fails, fall back to the
    actual AutoFilter records and rebuild the group from the records that really
    exist. This prevents false ``Title not found`` responses caused by punctuation,
    release-word differences, stale catalog identities, or an incomplete candidate
    window.
    """
    name = str(title_name or "").strip()
    tid = str(title_id or "").strip() or None

    target = await _load_grouped_title(name, tid, year_hint) if name else None
    if target:
        return target

    # If a stable catalog id was supplied, recover its stored identity first.
    if tid and not name:
        identity = await get_catalog_identity(tid)
        if identity:
            name = str(identity.get("title") or "").strip()
            year_hint = identity.get("year") if year_hint is None else year_hint
            target = await _load_grouped_title(name, tid, year_hint) if name else None
            if target:
                return target

    if not name:
        return None

    prepared = _autofilter_prepare_query(name)
    parsed = normalize_query(prepared)
    search_title = parsed.get("title") or prepared
    if not search_title:
        return None

    queries = []
    for q in (name, prepared, search_title):
        q = str(q or "").strip()
        if q and q.casefold() not in {x.casefold() for x in queries}:
            queries.append(q)

    docs = []
    async with SEARCH_SEMAPHORE:
        for q in queries:
            try:
                docs = await search_media(q, limit=SEARCH_MAX_DOCS)
            except Exception:
                docs = []
            if docs:
                break
        if not docs and bool(get_admin_setting("search", "fuzzy_fallback", default=True)):
            try:
                docs = await fuzzy_search_media(search_title, limit=min(80, SEARCH_MAX_DOCS))
            except Exception:
                docs = []

    if not docs:
        return None

    builder = _CatalogBuilder()
    for doc in docs:
        try:
            builder.add(doc)
        except Exception:
            LOGGER.debug("Robust title recovery skipped malformed media record", exc_info=True)
    groups = builder.finish()
    if not groups:
        return None

    wanted = normalize_for_search(search_title)
    candidates = [g for g in groups if normalize_for_search(g.get("title")) == wanted]
    if year_hint is not None:
        year_candidates = [g for g in candidates if g.get("year") in (None, year_hint)]
        if year_candidates:
            candidates = year_candidates
    if tid:
        by_id = [g for g in groups if g.get("id") == tid]
        if by_id:
            candidates = by_id
    if not candidates:
        candidates = sorted(groups, key=lambda g: search_title_score(g.get("title"), search_title), reverse=True)
        if not candidates or search_title_score(candidates[0].get("title"), search_title) < 0.60:
            return None

    target = candidates[0]
    GROUP_CACHE[(normalize_for_search(target.get("title")), target.get("year"), str(target.get("id") or ""))] = (time.time(), target)
    return target


async def title(request):
    title_id = request.match_info["id"]
    requested_name = request.query.get("q", "").strip()
    identity = await get_catalog_identity(title_id) if title_id else None
    if identity:
        requested_name = str(identity.get("title") or requested_name).strip()

    # Raw-file ids are accepted too. Recover the real media record directly and
    # then rebuild its logical title. This is the final guard against a false
    # Title-not-found response when the Telegram/AutoFilter file exists.
    if str(title_id).startswith("file:"):
        raw_id = str(title_id)[5:]
        try:
            doc = await find_media(raw_id, _SEARCH_PROJECTION)
        except Exception:
            doc = None
        if doc:
            parsed = parse_doc(doc)
            if parsed.get("title"):
                full = await _resolve_group_robust(parsed["title"], None, parsed.get("year"))
                if full:
                    return web.json_response({"ok": True, **await enrich(full)})

    target = await _resolve_group_robust(
        requested_name,
        title_id if identity else None,
        identity.get("year") if identity else None,
    )
    if target:
        return web.json_response({"ok": True, **await enrich(target)})

    # Deep links without q can still be recovered from recent search caches.
    for _key, (stamp, cached_items) in list(SEARCH_CACHE.items()):
        if time.time() - stamp >= SEARCH_CACHE_TTL:
            continue
        cached_target = next((item for item in cached_items if item.get("id") == title_id), None)
        if cached_target:
            full = await _resolve_group_robust(
                cached_target.get("title"), title_id, cached_target.get("year")
            )
            if full:
                return web.json_response({"ok": True, **await enrich(full)})

    raise web.HTTPNotFound(text="Title not found")

def _same_text(value, wanted):
    return normalize_for_search(value) == normalize_for_search(wanted)


FILTER_LANGUAGES = ["Malayalam", "Tamil", "English", "Hindi", "Telugu", "Kannada", "Gujarati", "Marathi", "Punjabi"]
FILTER_QUALITIES = ["360P", "480P", "720P", "1080P", "1440P", "2160P"]


def _best_file(variants):
    def score(item):
        q = int(re.search(r"\d+", str(item.get("quality") or "")) .group(0)) if re.search(r"\d+", str(item.get("quality") or "")) else 0
        return (q, str(item.get("file_name") or "").casefold())
    return sorted(variants, key=score, reverse=True)[0] if variants else None


async def filter_options(request):
    """Return only filter options backed by real variants for the current state."""
    title_name=request.query.get("title","").strip()
    title_id=request.query.get("id","").strip() or None
    if not title_name and not title_id:
        raise web.HTTPBadRequest(text="A title or title id is required")

    target=await _resolve_group_robust(title_name, title_id)
    if not target:
        raise web.HTTPNotFound(text="Title not found")

    variants=[]
    if target.get("type")=="series":
        for season_item in target.get("seasons") or []:
            for episode_item in season_item.get("episodes") or []:
                variants.extend(episode_item.get("variants") or [])
    else:
        variants=list(target.get("variants") or [])

    def norm_quality(v):
        return re.sub(r"\s+", "", str(v or "")).casefold().rstrip("p")

    language=request.query.get("language","").strip().casefold() or None
    quality=request.query.get("quality","").strip() or None
    try:
        season=int(request.query.get("season")) if request.query.get("season","").strip().isdigit() else None
        episode=int(request.query.get("episode")) if request.query.get("episode","").strip().isdigit() else None
    except ValueError as exc:
        raise web.HTTPBadRequest(text="Invalid season or episode") from exc

    def matches(v):
        if language and language not in {str(x).casefold() for x in (v.get("languages") or v.get("audio_languages") or [])}: return False
        if quality and norm_quality(v.get("quality")) != norm_quality(quality): return False
        if season is not None and v.get("season") != season: return False
        if episode is not None and v.get("episode") != episode: return False
        return True

    scoped=[v for v in variants if matches(v)]
    # For each control, calculate alternatives using all other selected controls.
    def scoped_without(skip):
        out=[]
        for v in variants:
            if skip != "language" and language and language not in {str(x).casefold() for x in (v.get("languages") or v.get("audio_languages") or [])}: continue
            if skip != "quality" and quality and norm_quality(v.get("quality")) != norm_quality(quality): continue
            if skip != "season" and season is not None and v.get("season") != season: continue
            if skip != "episode" and episode is not None and v.get("episode") != episode: continue
            out.append(v)
        return out

    lang_vars=scoped_without("language")
    qual_vars=scoped_without("quality")
    season_vars=scoped_without("season")
    episode_vars=scoped_without("episode")

    languages=sorted({str(value) for variant in lang_vars for value in (variant.get("audio_languages") or variant.get("languages") or []) if value and str(value).strip().casefold()!="unknown"}, key=str.casefold)
    qualities=sorted({str(v.get("quality")).strip() for v in qual_vars if v.get("quality") and str(v.get("quality")).strip().lower()!="auto"}, key=lambda value:(int(re.search(r"\d+",value).group()) if re.search(r"\d+",value) else 9999,value.casefold()))
    seasons=sorted({int(v["season"]) for v in season_vars if v.get("season") is not None})
    episode_values=sorted({int(v["episode"]) for v in episode_vars if v.get("episode") is not None})

    episodes={}
    for v in episode_vars:
        if v.get("season") is None or v.get("episode") is None: continue
        episodes.setdefault(str(int(v["season"])),set()).add(int(v["episode"]))
    episodes={k:sorted(vals) for k,vals in episodes.items()}

    captions=sorted({str(value) for variant in scoped for value in (variant.get("subtitle_languages") or []) if value}, key=str.casefold)
    return web.json_response({"ok":True,"languages":languages,"qualities":qualities,"captions":captions,"seasons":seasons,"episodes":episodes,"episode_values":episode_values,"type":target.get("type")})


async def filter_media(request):
    """Filter the complete logical title group using real parsed assets.

    Auto Filter's Mongo regex is used to locate a title, but once the VYRA
    UI has a logical title id we do NOT issue a second Mongo regex for every
    button click.  We load the already-grouped title and apply season, episode,
    language, quality and subtitle constraints to its actual asset records.
    This is important for names such as ``New Biggboss Day 1`` and prevents
    title-string differences from causing a false backend failure.
    """
    query=request.query.get("q","").strip() or request.query.get("title","").strip()
    title_id=request.query.get("id","").strip() or None
    if not query and not title_id:
        raise web.HTTPBadRequest(text="A search query or title id is required")

    try:
        season=int(request.query["season"]) if request.query.get("season","").strip().isdigit() else None
        episode=int(request.query["episode"]) if request.query.get("episode","").strip().isdigit() else None
    except ValueError as exc:
        raise web.HTTPBadRequest(text="Invalid season or episode") from exc

    language=request.query.get("language","").strip() or None
    quality=request.query.get("quality","").strip() or None
    subtitle=request.query.get("subtitle","").strip() or None

    # The title id is authoritative for an already-open detail page.  Fall
    # back to the query only for older clients that do not send it.
    target=None
    if title_id and query:
        parsed_query=normalize_query(_autofilter_prepare_query(query))
        target=await _load_grouped_title(
            query, title_id, year_hint=parsed_query.get("year")
        )
    elif query:
        prepared=_autofilter_prepare_query(query)
        parsed_query=normalize_query(prepared)
        target=await _load_grouped_title(
            parsed_query.get("title") or prepared,
            None,
            year_hint=parsed_query.get("year"),
        )

    if not target:
        return web.json_response({
            "ok": False, "count": 0, "file": None, "matches": [],
            "error": "TITLE GROUP NOT FOUND",
        }, status=200)

    # Flatten only the real assets that belong to this logical title.  There is
    # no Cartesian-product generation here: every returned option is an actual
    # MongoDB/Telegram file record.
    if target.get("type") == "series":
        all_files=[
            variant
            for season_item in (target.get("seasons") or [])
            for episode_item in (season_item.get("episodes") or [])
            for variant in (episode_item.get("variants") or [])
        ]
    else:
        all_files=list(target.get("variants") or [])

    wanted={
        "year": int(request.query["year"]) if request.query.get("year","").strip().isdigit() else target.get("year"),
        "season": season,
        "episode": episode,
        "quality": quality,
        "source": request.query.get("source","").strip() or None,
        "language": language,
    }

    matches=[]
    seen=set()
    for item in all_files:
        if not item or not item.get("file_id"):
            continue
        file_id=str(item.get("file_id"))
        if file_id in seen:
            continue
        if not _variant_matches_request(item, wanted):
            continue
        if subtitle and subtitle.casefold() not in {
            str(x).casefold() for x in (item.get("subtitle_languages") or [])
        }:
            continue
        seen.add(file_id)
        matches.append(item)

    matches.sort(key=_file_sort_key, reverse=True)

    # search-files is the primary Netflix/AutoFilter result endpoint. It must
    # return poster/description/rating too; otherwise the frontend has no
    # metadata to render even though TMDB integration exists elsewhere.
    meta = {}
    try:
        meta_title = str((target or {}).get("title") or (matches[0].get("title") if matches else query)).strip()
        meta_kind = str((target or {}).get("type") or "movie")
        meta_year = (target or {}).get("year")
        if meta_title and TMDB_API_KEY and (bool(get_admin_setting("metadata", "tmdb_enabled", default=True)) or bool(TMDB_API_KEY)):
            meta = await tmdb_meta(meta_title, meta_kind, meta_year) or {}
            requested_season = season
            if meta_kind == "series" and requested_season is not None:
                season_meta = await tmdb_season_meta(meta_title, requested_season, meta_year)
                if season_meta:
                    meta.update({k:v for k,v in season_meta.items() if v})
    except Exception:
        LOGGER.debug("TMDB enrichment failed for search-files %r", query, exc_info=True)

    return web.json_response({
        "ok": bool(matches),
        "count": len(matches),
        "file": matches[0] if matches else None,
        "matches": matches[:1000],
        "query": target.get("title") or query,
        "title": meta.get("title") or target.get("title") or query,
        "type": meta.get("type") or target.get("type") or "movie",
        "year": meta.get("year") or target.get("year"),
        "poster": meta.get("poster") or target.get("poster") or "",
        "description": meta.get("description") or "",
        "rating": meta.get("rating"),
        "requested_season": season,
        "title_id": target.get("id"),
        "exact": len(matches)==1,
        "error": None if matches else "NO MATCHING FILES FOUND",
    }, status=200)

async def resolve(request):
    """Resolve one real Telegram file using independent metadata matching."""
    title_name = request.query.get("title", "").strip()
    if not title_name:
        raise web.HTTPBadRequest(text="A title is required")
    wanted_type = request.query.get("type", "").strip().lower()
    try:
        season = int(request.query.get("season")) if request.query.get("season") not in (None, "") else None
        episode = int(request.query.get("episode")) if request.query.get("episode") not in (None, "") else None
        year = int(request.query.get("year")) if request.query.get("year") not in (None, "") else None
    except ValueError as exc:
        raise web.HTTPBadRequest(text="Invalid year, season or episode") from exc
    wanted = {
        "year": year,
        "season": season,
        "episode": episode,
        "quality": request.query.get("quality", "").strip() or None,
        "source": request.query.get("source", "").strip() or None,
        "language": request.query.get("audio", "").strip() or None,
    }
    grouped_target = await _resolve_group_robust(title_name)
    parsed = []
    if grouped_target:
        if grouped_target.get("type") == "series":
            for season_item in grouped_target.get("seasons") or []:
                for episode_item in season_item.get("episodes") or []:
                    parsed.extend(episode_item.get("variants") or [])
        else:
            parsed = list(grouped_target.get("variants") or [])
    candidates = []
    subtitle = request.query.get("subtitle", "").strip()
    target_type = str(grouped_target.get("type") or "").casefold() if grouped_target else ""
    if wanted_type and target_type and target_type != wanted_type:
        candidates = []
    else:
        for item in parsed:
            # Variants are asset records and do not necessarily carry catalog-only
            # fields such as type/title. Never index optional metadata directly.
            if not item or not item.get("file_id"):
                continue
            if not _variant_matches_request(item, wanted):
                continue
            if subtitle and subtitle.casefold() not in {str(x).casefold() for x in (item.get("subtitle_languages") or [])}:
                continue
            candidates.append(item)
    if not candidates:
        return web.json_response({"ok": False, "error": "Requested variant is not available"}, status=404)
    def _quality_number(item):
        match = re.search(r"\d+", str(item.get("quality") or ""))
        return int(match.group(0)) if match else 0
    candidates.sort(key=lambda item: (-_quality_number(item), str(item.get("file_name") or "").casefold()))
    return web.json_response({"ok": True, "file": candidates[0]})


async def admin_shortener_test(request):
    require_admin(request)
    try:
        data = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="Invalid JSON body")
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(text="Request body must be an object")
    stage = str(data.get("stage", "1"))
    if stage not in {"1", "2", "3"}:
        raise web.HTTPBadRequest(text="stage must be 1, 2, or 3")
    target = str(data.get("url") or PUBLIC_URL or "").strip()
    if not target:
        raise web.HTTPBadRequest(text="A test URL is required")
    from .verification_provider import shorten
    result = await shorten(target, int(stage))
    return web.json_response({"ok": True, "stage": int(stage), "input": target, "shortlink": result})


async def verify_stream(request):
    return await verification_complete(request)


async def token(request):
    file_id = request.match_info["file_id"]
    doc = await find_media(file_id, projection={"_id": 1})
    if doc is None:
        raise web.HTTPNotFound(text="Media not found in Auto Filter Bot database")
    requirement = None if await _premium_active(request) else await verification_requirement(request, file_id)
    if requirement:
        return web.json_response({"ok": False, **requirement}, status=403)
    ttl = int(get_admin_setting("files", "auto_delete_seconds", default=300)) if bool(get_admin_setting("files", "auto_delete", default=False)) else None
    return web.json_response({"ok": True, "token": make_stream_token(file_id, ttl=ttl)})


async def stream(request):
    file_id = request.match_info["file_id"]
    token_value = request.query.get("token", "")
    if not validate_stream_token(token_value, file_id):
        raise web.HTTPForbidden(text="Invalid or expired stream token")
    streamer = request.app.get("streamer")
    if streamer is None:
        raise web.HTTPServiceUnavailable(text="Telegram streaming is not available")
    return await streamer.stream(request, file_id)


async def tracks(request):
    file_id = request.match_info["file_id"]
    token_value = request.query.get("token", "")
    if not validate_stream_token(token_value, file_id):
        raise web.HTTPForbidden(text="Invalid or expired stream token")
    streamer = request.app.get("streamer")
    if streamer is None:
        raise web.HTTPServiceUnavailable(text="Telegram streaming is not available")
    return web.json_response({"ok": True, **await streamer.probe_tracks(file_id)})


async def subtitle(request):
    file_id = request.match_info["file_id"]
    token_value = request.query.get("token", "")
    if not validate_stream_token(token_value, file_id):
        raise web.HTTPForbidden(text="Invalid or expired stream token")
    streamer = request.app.get("streamer")
    if streamer is None:
        raise web.HTTPServiceUnavailable(text="Telegram streaming is not available")
    return await streamer.subtitle(request, file_id)


async def stream_compatible(request):
    file_id = request.match_info["file_id"]
    token_value = request.query.get("token", "")
    if not validate_stream_token(token_value, file_id):
        raise web.HTTPForbidden(text="Invalid or expired stream token")
    streamer = request.app.get("streamer")
    if streamer is None:
        raise web.HTTPServiceUnavailable(text="Telegram streaming is not available")
    return await streamer.transcode(request, file_id)


async def _premium_active(request):
    try:
        if not bool(get_admin_setting('payments','premium_bypass_verification',default=True)):
            return False
        from .users import current_user
        user = await current_user(request)
        if not user:
            return False
        return bool((await get_premium_status(user["user_id"])).get("premium"))
    except Exception:
        return False

async def download(request):
    """Protected direct download endpoint.

    Download is intentionally gated through the same verification/shortlink
    flow as playback.  A user cannot bypass an enabled ad/verification gate
    merely by constructing /api/download/<file_id> directly.
    """
    file_id = request.match_info["file_id"]
    requirement = None if await _premium_active(request) else await verification_requirement(request, file_id)
    if requirement:
        return web.json_response({"ok": False, **requirement, "download_blocked": True}, status=403)

    token_value = request.query.get("token", "")
    if not validate_stream_token(token_value, file_id):
        raise web.HTTPForbidden(text="Invalid or expired download token")
    streamer = request.app.get("streamer")
    if streamer is None:
        raise web.HTTPServiceUnavailable(text="Telegram streaming is not available")
    return await streamer.stream(request, file_id, attachment=True)


async def download_policy(request):
    settings = get_admin_settings()
    verification = settings.get("verification", {})
    return web.json_response({
        "ok": True,
        "download_requires_verification": bool(verification.get("enabled", False)),
        "shortlink_mode": str(verification.get("shortlink_mode", "enabled")),
        "file_mode": str(verification.get("file_mode", "file")),
        "file_mode_type": str(verification.get("file_mode_type", "single")),
        "premium_bypasses_verification": True,
    }, headers={"Cache-Control": "no-store"})


async def health(request):
    return web.json_response({"ok": True, "tmdb_configured": bool(TMDB_API_KEY), "search_caption_enabled": True})


def _set_status(request, name, configured):
    return {
        "name": name,
        "status": "SET" if configured else "NOT SET",
    }


async def admin_settings_get(request):
    require_admin(request)
    return web.json_response({"ok": True, "settings": get_public_settings(), "plans": premium_plan_list()}, headers={"Cache-Control": "no-store"})


async def admin_settings_update(request):
    global MAINTENANCE
    require_admin(request)
    try:
        data = await request.json()
    except Exception as exc:
        raise web.HTTPBadRequest(text="Invalid JSON body") from exc
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(text="Settings must be an object")
    patch = data.get("settings", data)
    # Secret API fields are write-only. A blank/null API leaves the existing secret unchanged.
    if isinstance(patch, dict):
        verification = patch.get("verification")
        if isinstance(verification, dict) and isinstance(verification.get("shorteners"), dict):
            current = get_admin_settings().get("verification", {}).get("shorteners", {})
            for num in ("1", "2", "3"):
                item = verification["shorteners"].get(num)
                if isinstance(item, dict) and not item.get("api"):
                    old = current.get(num, {}).get("api", "")
                    item.pop("api", None)
                    if old:
                        item["api"] = old
        settings_patch = patch
    else:
        settings_patch = {}
    if isinstance(settings_patch, dict) and isinstance(settings_patch.get("verification"), dict):
        # There is intentionally one public verification master switch. Keep
        # the old shortlink_mode key synchronized for backwards compatibility
        # with older deployments/configuration documents.
        settings_patch["verification"]["shortlink_mode"] = "enabled" if bool(settings_patch["verification"].get("enabled", get_admin_setting("verification", "enabled", default=False))) else "disabled"
    settings = await update_admin_settings(settings_patch)
    MAINTENANCE = bool(settings.get("site", {}).get("maintenance", MAINTENANCE))
    # Never return write-only verification secrets in the admin API response.
    settings = get_public_settings()
    # Settings are process-local controls; invalidate derived search/metadata caches
    # so the next request observes the new admin configuration.
    SEARCH_CACHE.clear()
    GROUP_CACHE.clear()
    META_CACHE.clear()
    ULTRON_SEARCH.cache.clear()
    return web.json_response({"ok": True, "settings": settings}, headers={"Cache-Control": "no-store"})


async def admin_payment_qr_remove(request):
    require_admin(request)
    # QR is optional configuration. Clearing it removes the stored data URL
    # rather than forcing the administrator to replace it with another image.
    settings = await update_admin_settings({"payments": {"manual_qr": ""}})
    return web.json_response(
        {"ok": True, "settings": get_public_settings()},
        headers={"Cache-Control": "no-store"},
    )


async def admin_settings_remove(request):
    require_admin(request)
    data = await request.json()
    path = str(data.get("path", "")).strip()
    allowed_prefixes = ("verification.", "search.", "files.", "metadata.", "site.", "ultron.")
    if not path or not path.startswith(allowed_prefixes):
        raise web.HTTPBadRequest(text="Invalid settings path")
    settings = await remove_admin_setting(path)
    if path.startswith("site.maintenance"):
        MAINTENANCE = bool(settings.get("site", {}).get("maintenance", False))
    SEARCH_CACHE.clear()
    GROUP_CACHE.clear()
    META_CACHE.clear()
    ULTRON_SEARCH.cache.clear()
    return web.json_response({"ok": True, "settings": get_public_settings(), "plans": premium_plan_list()}, headers={"Cache-Control": "no-store"})


async def admin_settings_reset(request):
    require_admin(request)
    await reset_admin_settings()
    settings = get_public_settings()
    MAINTENANCE = bool(settings.get("site", {}).get("maintenance", False))
    SEARCH_CACHE.clear()
    GROUP_CACHE.clear()
    META_CACHE.clear()
    ULTRON_SEARCH.cache.clear()
    return web.json_response({"ok": True, "settings": settings}, headers={"Cache-Control": "no-store"})


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
            "database_uri_role": "website/user data",
            "database_uri2": "SET" if DATABASE_URI2 else "NOT SET",
            "database_uri2_role": "read-only Devil AutoFilter media",
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
        if is_admin(request):
            raise web.HTTPFound("/admin/")
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
    # Koyeb terminates HTTPS at the edge, while the aiohttp process may see
    # an internal HTTP connection. Matching the cookie's Secure flag to the
    # request scheme keeps the same protected session usable on Koyeb and in
    # local HTTP testing without exposing the credential itself.
    response.set_cookie(
        "admin_session",
        make_admin_session(),
        httponly=True,
        secure=(request.secure or request.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().lower()=="https"),
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
    # Keep admin requests bounded on Koyeb Hobby. These are dashboard preview
    # counts, not a reason to materialize the complete MongoDB catalog.
    items = await all_titles(limit=HOME_DOC_LIMIT)
    return web.json_response(
        {
            "authenticated": True,
            "maintenance": bool(get_admin_setting("site", "maintenance", default=MAINTENANCE)),
            "titles": len(items),
            "movies": sum(item["type"] == "movie" for item in items),
            "series": sum(item["type"] == "series" for item in items),
            "catalog_cache_age": None,
            "counts_limited": True,
        },
        headers={"Cache-Control": "no-store"},
    )


async def admin_toggle_maintenance(request):
    global MAINTENANCE
    require_admin(request)

    try:
        data = await request.json()
    except Exception as exc:
        raise web.HTTPBadRequest(text="Invalid JSON body") from exc

    MAINTENANCE = bool(data.get("maintenance"))
    await update_admin_settings({"site": {"maintenance": MAINTENANCE}})
    return web.json_response(
        {"ok": True, "maintenance": MAINTENANCE},
        headers={"Cache-Control": "no-store"},
    )


async def admin_refresh(request):
    require_admin(request)
    META_CACHE.clear()
    items = await all_titles(limit=HOME_DOC_LIMIT)
    return web.json_response({"ok": True, "count": len(items)})


@web.middleware
async def api_error_middleware(request, handler):
    try:
        return await handler(request)
    except web.HTTPException as exc:
        if request.path.startswith("/api/") or request.path.startswith("/admin/api/"):
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
        if request.path.startswith("/api/") or request.path.startswith("/admin/api/"):
            return web.json_response(
                {
                    "ok": False,
                    "error": "Backend failure",
                },
                status=500,
            )
        raise


@web.middleware
async def maintenance_middleware(request, handler):
    if request.path.startswith("/admin") or request.path == "/health":
        return await handler(request)

    if MAINTENANCE:
        if request.path.startswith("/api/") or request.path.startswith("/admin/api/"):
            return web.json_response(
                {
                    "ok": False,
                    "maintenance": True,
                    "message": "Website is temporarily under maintenance.",
                },
                status=503,
            )
        # The public maintenance page intentionally returns HTTP 200. Koyeb's
        # default HTTP health check may probe `/`; returning 503 here would make
        # the instance unhealthy and can cause Koyeb to stop/restart it before an
        # administrator gets a chance to turn maintenance mode back off. The
        # actual public APIs continue to return 503 while maintenance is enabled.
        return web.Response(
            text=MAINTENANCE_HTML,
            content_type="text/html",
            status=200,
            headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
        )

    return await handler(request)


async def startup(app):
    await ensure_web_indexes()
    settings = await init_settings_store()
    global MAINTENANCE
    MAINTENANCE = bool(settings.get("site", {}).get("maintenance", False))
    missing = []
    if not DATABASE_URI:
        missing.append("DATABASE_URI")
    if not DATABASE_URI2:
        missing.append("DATABASE_URI2")
    if not SITE_SECRET:
        missing.append("SITE_SECRET")

    if missing:
        LOGGER.error("Missing required website settings: %s", ", ".join(missing))

    telegram = await create_client()
    app["tg"] = telegram
    app["streamer"] = Streamer(telegram) if telegram else None
    app["telegram_ready"] = telegram is not None

    global TMDB_SESSION, TMDB_SEMAPHORE
    if TMDB_API_KEY:
        TMDB_SESSION, TMDB_SEMAPHORE = ClientSession(timeout=ClientTimeout(total=4)), asyncio.Semaphore(8)
        LOGGER.info("TMDB metadata enrichment enabled with bounded concurrency/cache")

    if telegram:
        LOGGER.info("Telegram streaming client started")
    else:
        LOGGER.error(
            "Telegram streaming client is unavailable. "
            "The catalog can still be served, but playback/download is disabled."
        )


async def cleanup(app):
    global TMDB_SESSION, TMDB_SEMAPHORE
    if TMDB_SESSION is not None and not TMDB_SESSION.closed:
        await TMDB_SESSION.close()
    TMDB_SESSION = None
    TMDB_SEMAPHORE = None

    telegram = app.get("tg")
    if telegram is not None:
        try:
            await telegram.stop()
        except Exception:
            LOGGER.exception("Telegram client shutdown failed")

    # Close both Motor clients without ever writing to the bot collections.
    from . import database
    from . import web_store
    for mongo_client in (database.client, database.client2, getattr(web_store, "_client", None)):
        if mongo_client is not None:
            mongo_client.close()


def create_app():
    app = web.Application(
        client_max_size=8 * 1024 * 1024,
        middlewares=[api_error_middleware, maintenance_middleware],
    )

    app.router.add_get("/health", health)
    app.router.add_get("/api/diagnostics", diagnostics)
    app.router.add_get("/api/home", home)
    app.router.add_get("/api/search", search)
    app.router.add_get("/api/search-files", search_files)
    app.router.add_get("/api/title/{id}", title)
    app.router.add_get("/api/filter-options", filter_options)
    app.router.add_get("/api/filter", filter_media)
    app.router.add_get("/api/resolve", resolve)
    app.router.add_get("/api/stream-token/{file_id}", token)
    app.router.add_get("/api/download-policy", download_policy)
    app.router.add_post("/api/account/create", user_create_account)
    app.router.add_post("/api/account/login", user_login)
    app.router.add_post("/api/account/logout", user_logout)
    app.router.add_get("/api/account/me", user_me)
    app.router.add_put("/api/account/nickname", user_update_nickname)
    app.router.add_get("/api/premium", premium_status)
    app.router.add_get("/api/access-status", access_status)
    app.router.add_post("/api/premium/order", premium_create_order)
    app.router.add_post("/api/premium/verify", premium_verify_payment)
    app.router.add_post("/api/premium/manual", premium_manual_submit)
    app.router.add_get("/api/premium/manual", premium_my_manual)
    app.router.add_post("/api/premium/webhook", premium_webhook)
    app.router.add_get("/api/verify", verify_stream)
    app.router.add_get("/api/stream/{file_id}", stream)
    app.router.add_get("/api/tracks/{file_id}", tracks)
    app.router.add_get("/api/subtitle/{file_id}", subtitle)
    app.router.add_get("/api/stream-compatible/{file_id}", stream_compatible)
    app.router.add_get("/api/download/{file_id}", download)

    app.router.add_get("/admin", admin_login)
    app.router.add_post("/admin/login", admin_login)
    app.router.add_post("/admin/logout", admin_logout)
    app.router.add_get("/admin/api/status", admin_status)
    app.router.add_post("/admin/api/maintenance", admin_toggle_maintenance)
    app.router.add_post("/admin/api/refresh", admin_refresh)
    app.router.add_post("/admin/api/shortener/test", admin_shortener_test)
    app.router.add_get("/admin/api/settings", admin_settings_get)
    app.router.add_put("/admin/api/settings", admin_settings_update)
    app.router.add_post("/admin/api/settings/reset", admin_settings_reset)
    app.router.add_post("/admin/api/payment/qr/remove", admin_payment_qr_remove)
    app.router.add_post("/admin/api/settings/remove", admin_settings_remove)
    app.router.add_post("/admin/api/premium/grant", premium_admin_grant)
    app.router.add_post("/admin/api/premium/revoke", premium_admin_revoke)
    app.router.add_post("/admin/api/premium/extend", premium_admin_extend)
    app.router.add_get("/admin/api/premium/users", premium_admin_users)
    app.router.add_get("/admin/api/users", admin_users_list)
    app.router.add_get("/admin/api/users/{user_id}", admin_user_detail)
    app.router.add_get("/admin/api/payments", admin_payments)
    app.router.add_get("/admin/api/history", admin_history)
    app.router.add_delete("/admin/api/history/{event_id}", admin_history_delete)
    app.router.add_post("/admin/api/history/clear", admin_history_clear)
    app.router.add_get("/admin/api/premium/manual", premium_admin_manual_list)
    app.router.add_get("/admin/api/premium/manual/{request_id}/proof", premium_admin_manual_proof)
    app.router.add_post("/admin/api/premium/manual/decide", premium_admin_manual_decide)

    async def frontend_index(request):
        return web.FileResponse(BASE / "frontend" / "index.html")

    app.router.add_get("/", frontend_index)
    app.router.add_static("/", BASE / "frontend", show_index=False)

    async def admin_index(request):
        return web.FileResponse(BASE / "admin" / "index.html")

    app.router.add_get("/admin/", admin_index)
    app.router.add_static("/admin/", BASE / "admin", show_index=False)

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
    const response=await fetch("/admin/login",{
      method:"POST",
      body:new FormData(form),
      credentials:"same-origin",
      redirect:"manual"
    });
    if(response.ok){
      location.replace("/admin/");
      return;
    }
    const data=await response.json().catch(()=>null);
    const text=data?.error || await response.text().catch(()=> "");
    msg.textContent=text||"Invalid username or password";
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
