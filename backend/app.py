import asyncio
import logging
import os
import re
import time
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
from .database import (
    collection_counts,
    find_media,
    iter_media,
    search_media,
    search_media_by_title,
    fuzzy_search_media,
    search_media_with_filters,
)
from .parser import (
    normalize,
    normalize_async,
    normalize_for_search,
    normalize_query,
    parse_doc,
    search_title_score,
    stable_id,
)
from .stream import Streamer, create_client

LOGGER = logging.getLogger("streambox")
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
SEARCH_SEMAPHORE = asyncio.Semaphore(3)
SEARCH_CANDIDATE_LIMIT = min(max(80, int(os.getenv("SEARCH_CANDIDATE_LIMIT", "120"))), 300)
HOME_CACHE = None
HOME_CACHE_TIME = 0.0

# Keep homepage catalog work bounded. The website only needs enough recent
# media records to build the visible homepage; it must never materialize the
# entire bot collection in RAM on a small Koyeb instance.
HOME_DOC_LIMIT = 300
HOME_TITLE_LIMIT = 100
HOME_ENRICH_LIMIT = 100
SEARCH_ENRICH_LIMIT = 50
# Never let an environment value such as 10000 turn one HTTP request into a
# huge in-memory MongoDB result set. The exact title can still have many real
# variants; 300 is the default/safety ceiling for a single web request on Koyeb Free.
TITLE_VARIANT_LIMIT = min(max(500, int(SEARCH_MAX_DOCS)), 1500)

# Maintenance is deliberately kept in memory. The website must not create or
# modify a collection inside the Auto Filter Bot's MongoDB database.
MAINTENANCE = False


async def all_titles(limit=None):
    """Build the bounded homepage catalog, cached briefly in this process."""
    global HOME_CACHE, HOME_CACHE_TIME
    if not DATABASE_URI:
        raise RuntimeError("DATABASE_URI is not configured")

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
        TMDB_SESSION = ClientSession(timeout=ClientTimeout(total=8))
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
    if not TMDB_API_KEY:
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
                    _tmdb_cache_put(key, {})
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
            _tmdb_cache_put(key, {})
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
        _tmdb_cache_put(key, {})
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
    items = (await all_titles(limit=HOME_DOC_LIMIT))[:HOME_TITLE_LIMIT]
    # Preserve real caption posters and enrich every visible item that is
    # missing one. TMDB results are cached for a day, so repeat home loads do
    # not repeat the external requests.
    enriched = list(items)
    targets = [i for i, item in enumerate(enriched) if not item.get("poster")][:HOME_ENRICH_LIMIT]
    if targets and TMDB_API_KEY:
        values = await asyncio.gather(*(enrich(enriched[i]) for i in targets), return_exceptions=True)
        for i, value in zip(targets, values):
            if not isinstance(value, Exception):
                enriched[i] = value
    return web.json_response({"ok": True, "items": enriched, "count": len(enriched)})


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
    bounded = SEARCH_CANDIDATE_LIMIT if limit is None else min(max(1, int(limit)), 1500)
    async with SEARCH_SEMAPHORE:
        docs = await search_media(prepared, limit=bounded)
        if not docs:
            parsed_query = normalize_query(prepared)
            title = parsed_query.get("title") or prepared
            docs = await search_media(title, limit=bounded)
            if not docs and parsed_query.get("year") is None:
                docs = await fuzzy_search_media(title, limit=min(40, bounded))
        return await asyncio.to_thread(_parsed_files, docs)


async def _search_uncached(query):
    """Find the logical Netflix title while keeping Ultron raw files authoritative.

    The search entry point uses the title portion of the query (for example,
    ``Reacher`` from ``Reacher S04E07``) to locate real Mongo records. Season
    and episode are parsed separately and are passed to the existing Netflix
    detail/Auto Filter UI. This is important because the raw release token is
    commonly stored as ``S04E07`` without a separator; searching the literal
    tokens ``S04`` + ``E07`` as separate Mongo terms can therefore miss valid
    files. No raw files are discarded from the database search used by filters.
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
        docs = await search_media(base_query, limit=SEARCH_CANDIDATE_LIMIT)
        if not docs and search_title.casefold() != base_query.casefold():
            docs = await search_media(search_title, limit=SEARCH_CANDIDATE_LIMIT)
        if not docs and parsed.get("year") is None:
            fuzzy_docs = await fuzzy_search_media(search_title, limit=min(40, SEARCH_CANDIDATE_LIMIT))
            if fuzzy_docs:
                fuzzy_parsed = parse_doc(fuzzy_docs[0])
                docs = await search_media(fuzzy_parsed.get("title") or search_title, limit=SEARCH_CANDIDATE_LIMIT)

    raw_items = await asyncio.to_thread(_parsed_files, docs)
    if not raw_items:
        return []

    # When S/E was supplied, prefer a real raw file from that season/episode
    # when choosing the title card. We do NOT throw away the other files; the
    # detail page reloads the full title pool and /api/filter searches the raw
    # Mongo collection again with cumulative constraints.
    if parsed.get("season") is not None or parsed.get("episode") is not None:
        matching = [
            item for item in raw_items
            if (parsed.get("season") is None or item.get("season") == parsed.get("season"))
            and (parsed.get("episode") is None or item.get("episode") == parsed.get("episode"))
        ]
        if matching:
            raw_items = matching + [item for item in raw_items if item not in matching]

    # Group only the SEARCH PRESENTATION by logical title. The underlying
    # Mongo records remain individual raw files and are never collapsed for
    # Auto Filter/playback.
    grouped = {}
    for item in raw_items:
        title = str(item.get("title") or "Untitled").strip()
        kind = str(item.get("type") or "movie")
        year = item.get("year")
        key = (normalize_for_search(title), kind, year)
        if key not in grouped:
            copy = dict(item)
            copy["id"] = f"file:{copy.get('file_id') or len(grouped)}"
            copy["raw_match_count"] = 0
            grouped[key] = copy
        grouped[key]["raw_match_count"] += 1

    selected = list(grouped.values())
    selected.sort(
        key=lambda item: (
            0 if (parsed.get("season") is None or item.get("season") == parsed.get("season"))
            and (parsed.get("episode") is None or item.get("episode") == parsed.get("episode")) else 1,
            -int(item.get("raw_match_count") or 0),
            str(item.get("title") or "").casefold(),
        )
    )

    if TMDB_API_KEY and selected:
        values = await asyncio.gather(
            *(enrich(item) for item in selected[:SEARCH_ENRICH_LIMIT]),
            return_exceptions=True,
        )
        for i, value in enumerate(values):
            if not isinstance(value, Exception):
                selected[i] = value
    return selected


async def search(request):
    query = request.query.get("q", "").strip()
    if not query:
        return web.json_response({"ok": True, "items": [], "count": 0})

    key = re.sub(r"\s+", " ", query).strip().casefold()
    now = time.time()
    cached = SEARCH_CACHE.get(key)
    if cached and now - cached[0] < SEARCH_CACHE_TTL:
        SEARCH_CACHE.move_to_end(key)
        return web.json_response({"ok": True, "items": cached[1], "count": len(cached[1]), "cached": True})

    # Single-flight: 100 users asking for the same title at once share one
    # database search instead of creating 100 identical MongoDB scans.
    task = SEARCH_INFLIGHT.get(key)
    if task is None:
        task = asyncio.create_task(_search_uncached(query))
        SEARCH_INFLIGHT[key] = task
    try:
        selected = await task
    finally:
        if SEARCH_INFLIGHT.get(key) is task:
            SEARCH_INFLIGHT.pop(key, None)

    SEARCH_CACHE[key] = (time.time(), selected)
    SEARCH_CACHE.move_to_end(key)
    while len(SEARCH_CACHE) > SEARCH_CACHE_MAX:
        SEARCH_CACHE.popitem(last=False)
    return web.json_response({"ok": True, "items": selected, "count": len(selected)})


async def _cached_or_search_items(query):
    """Reuse a recent grouped search result for title/resolve requests."""
    key = re.sub(r"\s+", " ", str(query)).strip().casefold()
    cached = SEARCH_CACHE.get(key)
    if cached and time.time() - cached[0] < SEARCH_CACHE_TTL:
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
    SEARCH_CACHE[key] = (time.time(), result)
    SEARCH_CACHE.move_to_end(key)
    while len(SEARCH_CACHE) > SEARCH_CACHE_MAX:
        SEARCH_CACHE.popitem(last=False)
    return result


async def _load_grouped_title(title_name, title_id=None):
    """Load the complete real variant pool for one logical title.

    Search uses a small candidate window for speed; opening a title or resolving
    a file uses the larger bounded window so all real seasons/episodes and
    variants for that title are retained.
    """
    name = str(title_name or "").strip()
    if not name:
        return None
    docs = await search_media(name, limit=TITLE_VARIANT_LIMIT)
    items = await asyncio.to_thread(normalize, docs)
    if title_id:
        return next((item for item in items if item.get("id") == title_id), None)
    wanted = normalize_for_search(name)
    exact = [item for item in items if normalize_for_search(item.get("title")) == wanted]
    if exact:
        return exact[0]
    return next((item for item in items if search_title_score(item.get("title"), name) >= 0.90), None)


async def title(request):
    title_id = request.match_info["id"]
    requested_name = request.query.get("q", "").strip()

    # Search results are raw Auto Filter file records. When the search UI
    # opens the first result automatically, resolve that raw id back to its
    # logical Netflix title instead of treating the file id as a catalog id.
    if str(title_id).startswith("file:"):
        for _key, (stamp, cached_items) in list(SEARCH_CACHE.items()):
            if time.time() - stamp >= SEARCH_CACHE_TTL:
                continue
            target = next((item for item in cached_items if item.get("id") == title_id), None)
            if target and target.get("title"):
                full = await _load_grouped_title(target.get("title"), None)
                if full:
                    return web.json_response({"ok": True, **await enrich(full)})
        if requested_name:
            full = await _load_grouped_title(requested_name, None)
            if full:
                return web.json_response({"ok": True, **await enrich(full)})
        raise web.HTTPNotFound(text="Title not found")

    if requested_name:
        target = await _load_grouped_title(requested_name, title_id)
        if target:
            return web.json_response({"ok": True, **await enrich(target)})

    # Deep links without q can still be resolved from a recent search cache.
    for _key, (stamp, cached_items) in list(SEARCH_CACHE.items()):
        if time.time() - stamp >= SEARCH_CACHE_TTL:
            continue
        target = next((item for item in cached_items if item.get("id") == title_id), None)
        if target:
            full = await _load_grouped_title(target.get("title"), title_id)
            if full:
                return web.json_response({"ok": True, **await enrich(full)})

    raise web.HTTPNotFound(text="Title not found")

def _same_text(value, wanted):
    return normalize_for_search(value) == normalize_for_search(wanted)


FILTER_LANGUAGES = ["Malayalam", "Tamil", "English", "Hindi", "Telugu", "Kannada", "Gujarati", "Marathi", "Punjabi"]
FILTER_QUALITIES = ["360P", "480P", "720P", "1080P", "1440P", "2160P"]
FILTER_SEASONS = [f"Season {i}" for i in range(1, 11)]


def _best_file(variants):
    def score(item):
        q = int(re.search(r"\d+", str(item.get("quality") or "")) .group(0)) if re.search(r"\d+", str(item.get("quality") or "")) else 0
        return (q, str(item.get("file_name") or "").casefold())
    return sorted(variants, key=score, reverse=True)[0] if variants else None


async def filter_options(request):
    """Return the same fixed Auto Filter controls used by the original UI.

    Availability is checked by /api/filter against the raw Mongo result set;
    this endpoint only supplies the controls and title type.
    """
    title_name=request.query.get("title","").strip()
    if not title_name:
        raise web.HTTPBadRequest(text="A title is required")
    target=await _load_grouped_title(title_name, None)
    if not target:
        raise web.HTTPNotFound(text="Title not found")
    return web.json_response({
        "ok":True,
        "languages":FILTER_LANGUAGES,
        "qualities":FILTER_QUALITIES,
        "seasons":[f"Season {i}" for i in range(1,16)],
        "episodes":{str(i):list(range(1,11)) for i in range(1,16)},
        "type":target.get("type"),
    })


async def filter_media(request):
    """Apply cumulative filters to the ORIGINAL raw Auto Filter result set.

    The query is reduced to its title/year portion first. Season, episode,
    language and quality are then independent Mongo constraints ANDed onto the
    same raw file search. This avoids the S04 E07 token-spacing problem and,
    importantly, never collapses multiple valid files into one.
    """
    query=request.query.get("q","").strip() or request.query.get("title","").strip()
    if not query:
        raise web.HTTPBadRequest(text="A search query is required")
    try:
        season=int(request.query["season"]) if request.query.get("season","").isdigit() else None
        episode=int(request.query["episode"]) if request.query.get("episode","").isdigit() else None
    except ValueError as exc:
        raise web.HTTPBadRequest(text="Invalid season or episode") from exc
    language=request.query.get("language","").strip() or None
    quality=request.query.get("quality","").strip() or None

    prepared=_autofilter_prepare_query(query)
    parsed=normalize_query(prepared)
    base_title=parsed.get("title") or prepared
    base_parts=[base_title]
    if parsed.get("year") is not None:
        base_parts.append(str(parsed["year"]))
    base_query=" ".join(base_parts)

    files=await search_media_with_filters(
        base_query,
        season=season,
        episode=episode,
        language=language,
        quality=quality,
        limit=SEARCH_MAX_DOCS,
    )
    files=await asyncio.to_thread(_parsed_files, files)
    files.sort(key=_file_sort_key,reverse=True)

    # Keep every matching raw file. Player.open receives the whole matching
    # pool so one file is not required before Play becomes available.
    return web.json_response({
        "ok":bool(files),
        "count":len(files),
        "file":files[0] if files else None,
        "matches":files[:100],
        "query":base_query,
        "exact":len(files)==1,
        "error":None if files else "NO FILES WERE FOUND",
    },status=200 if files else 404)

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
    grouped_target = await _load_grouped_title(title_name)
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


async def token(request):
    file_id = request.match_info["file_id"]
    doc = await find_media(file_id, projection={"_id": 1})
    if doc is None:
        raise web.HTTPNotFound(text="Media not found in Auto Filter Bot database")
    return web.json_response({"ok": True, "token": make_stream_token(file_id)})


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
    return web.json_response({"ok": True, "tmdb_configured": bool(TMDB_API_KEY), "search_caption_enabled": True})


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
            "maintenance": MAINTENANCE,
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
            headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
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

    global TMDB_SESSION, TMDB_SEMAPHORE
    if TMDB_API_KEY:
        TMDB_SESSION, TMDB_SEMAPHORE = ClientSession(timeout=ClientTimeout(total=8)), asyncio.Semaphore(8)
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
    app.router.add_get("/api/filter-options", filter_options)
    app.router.add_get("/api/filter", filter_media)
    app.router.add_get("/api/resolve", resolve)
    app.router.add_get("/api/stream-token/{file_id}", token)
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
