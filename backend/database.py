import asyncio
import logging
import re
from motor.motor_asyncio import AsyncIOMotorClient
from .config import (
    DATABASE_URI,
    DATABASE_URI2,
    DATABASE_NAME,
    COLLECTION_NAME,
    MULTIPLE_DB,
)

LOGGER = logging.getLogger("streambox.database")

client = None
db = None
media = None
client2 = None
db2 = None
media2 = None

if DATABASE_URI:
    client = AsyncIOMotorClient(
        DATABASE_URI,
        serverSelectionTimeoutMS=8000,
        connectTimeoutMS=8000,
    )
    db = client[DATABASE_NAME]
    media = db[COLLECTION_NAME]

if MULTIPLE_DB and DATABASE_URI2:
    client2 = AsyncIOMotorClient(
        DATABASE_URI2,
        serverSelectionTimeoutMS=8000,
        connectTimeoutMS=8000,
    )
    db2 = client2[DATABASE_NAME]
    media2 = db2[COLLECTION_NAME]

def _collections():
    return tuple(c for c in (media, media2) if c is not None)

def _normalize_id(value):
    return str(value) if value is not None else ""

async def ping():
    if media is None:
        raise RuntimeError("DATABASE_URI is not configured")
    await client.admin.command("ping")
    if media2 is not None:
        await client2.admin.command("ping")

async def collection_counts():
    counts = {"primary": 0, "secondary": 0}
    if media is not None:
        counts["primary"] = await media.count_documents({})
    if media2 is not None:
        counts["secondary"] = await media2.count_documents({})
    return counts

async def iter_media(query=None, projection=None, limit=None):
    """Read the existing Auto Filter Bot collection(s), read-only.

    A database that is unreachable is skipped only when another configured
    database successfully answers. If every configured database fails, raise a
    clear error instead of silently returning an empty catalog.
    """
    query = query or {}
    seen = set()
    remaining = None if limit is None else max(0, int(limit))
    configured = 0
    succeeded = 0
    errors = []

    for name, collection in (("primary", media), ("secondary", media2)):
        if collection is None or remaining == 0:
            continue
        configured += 1
        try:
            cursor = collection.find(query, projection).sort("$natural", -1)
            if remaining is not None:
                cursor = cursor.limit(remaining)

            emitted_from_collection = 0
            async for doc in cursor:
                key = _normalize_id(doc.get("_id")) or _normalize_id(doc.get("file_id"))
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                yield doc
                emitted_from_collection += 1

            succeeded += 1
            if remaining is not None:
                remaining = max(0, remaining - emitted_from_collection)
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}")
            LOGGER.exception("%s MongoDB catalog read failed", name)
            continue

    if configured == 0:
        raise RuntimeError("No MongoDB database is configured")
    if succeeded == 0:
        raise RuntimeError(
            "All configured MongoDB databases are unreachable or the collection "
            "cannot be read (" + ", ".join(errors) + ")"
        )


async def find_media(file_id, projection=None):
    """Find one existing Auto Filter Bot media document by its canonical ID.

    The bot stores the Telegram file ID as MongoDB ``_id``. For compatibility
    with older records, ``file_id`` is also checked. Reads are attempted against
    both configured databases without modifying either collection.
    """
    if not _collections():
        raise RuntimeError("No MongoDB database is configured")

    value = str(file_id)
    errors = []

    for name, collection in (("primary", media), ("secondary", media2)):
        if collection is None:
            continue
        try:
            doc = await collection.find_one({"_id": value}, projection)
            if doc is not None:
                return doc

            doc = await collection.find_one({"file_id": value}, projection)
            if doc is not None:
                return doc
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}")
            LOGGER.exception("%s MongoDB media lookup failed", name)
            continue

    # A missing document is a normal lookup result. Only report an error when
    # every configured database actually failed during the lookup.
    if errors and len(errors) == len(_collections()):
        raise RuntimeError(
            "All configured MongoDB databases are unreachable or the collection "
            "cannot be searched (" + ", ".join(errors) + ")"
        )
    return None


from functools import lru_cache

@lru_cache(maxsize=512)
def _autofilter_regex(query):
    """Build the same kind of Mongo regex used by Auto Filter.

    Auto Filter does one bounded Mongo query using a release-name-aware regex,
    rather than downloading a large candidate set and then searching it in
    Python.  Keep this function intentionally small and deterministic.
    """
    value = re.sub(r"\s+", " ", str(query or "").strip())
    if not value:
        return None
    parts = value.split(" ")
    escaped = [r"(\b|[\.\+\-_])" + re.escape(part) + r"(\b|[\.\+\-_])" for part in parts]
    return r".*[\s\.\+\-_()\[\]]".join(escaped) if len(escaped) > 1 else escaped[0]


def build_search_filter(query):
    """Build an Auto Filter-compatible filename/caption regex filter."""
    pattern = _autofilter_regex(query)
    if not pattern:
        return None
    return {
        "$or": [
            {"file_name": {"$regex": pattern, "$options": "i"}},
            {"caption": {"$regex": pattern, "$options": "i"}},
        ]
    }


_SEARCH_PROJECTION = {
    "_id": 1,
    "file_id": 1,
    "file_name": 1,
    "file_size": 1,
    "file_type": 1,
    "mime_type": 1,
    "caption": 1,
    "file_ref": 1,
    "tmdb_id": 1,
    "tmdbId": 1,
    "tmdb": 1,
}


async def search_media(query, limit=None):
    """Fast, read-only Auto Filter style search across every configured DB."""
    search_filter = build_search_filter(query)
    if not search_filter:
        return []
    bounded = 120 if limit is None else max(1, min(int(limit), 1500))
    projection = _SEARCH_PROJECTION

    async def fetch(name, collection):
        if collection is None:
            return name, [], None
        try:
            rows = await (collection.find(search_filter, projection)
                          .sort("$natural", -1).limit(bounded)
                          .to_list(length=bounded))
            return name, rows, None
        except Exception as exc:
            return name, [], exc

    configured = [(n, c) for n, c in (("primary", media), ("secondary", media2)) if c is not None]
    if not configured:
        raise RuntimeError("No MongoDB database is configured")
    results = await asyncio.gather(*(fetch(n, c) for n, c in configured))
    docs, succeeded, errors, seen = [], 0, [], set()
    for name, rows, error in results:
        if error is not None:
            errors.append(f"{name}: {type(error).__name__}")
            LOGGER.warning("%s MongoDB search failed: %s", name, type(error).__name__)
            continue
        succeeded += 1
        for doc in rows:
            key = _normalize_id(doc.get("_id")) or _normalize_id(doc.get("file_id"))
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            docs.append(doc)
            if len(docs) >= bounded:
                break
        if len(docs) >= bounded:
            break
    if succeeded == 0:
        raise RuntimeError("All configured MongoDB databases are unreachable or the collection cannot be searched (" + ", ".join(errors) + ")")
    return docs

async def fuzzy_search_media(query, limit=80):
    """Cheap local fuzzy fallback modeled on Ultron's fuzzy search.

    MongoDB first narrows candidates using distinctive three-character prefixes;
    Python then scores the real stored filenames/captions. No external service
    is allowed to manufacture a result.
    """
    import re
    from difflib import SequenceMatcher

    def norm(value):
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold())).strip()

    q = norm(query)
    if len(q) < 2:
        return []
    tokens = [t for t in q.split() if len(t) >= 3]
    prefixes = list(dict.fromkeys(t[:3] for t in tokens))[:4] or [q[:3]]
    pattern = re.compile("|".join(re.escape(x) for x in prefixes), re.I)
    mongo_filter = {"$or": [{"file_name": {"$regex": pattern}}, {"caption": {"$regex": pattern}}]}
    projection = {"_id": 1, "file_id": 1, "file_name": 1, "file_size": 1,
                  "file_type": 1, "mime_type": 1, "caption": 1, "file_ref": 1,
                  "tmdb_id": 1, "tmdbId": 1, "tmdb": 1}

    candidates = []
    seen = set()
    for collection in (media, media2):
        if collection is None:
            continue
        try:
            rows = await collection.find(mongo_filter, projection).limit(max(100, limit * 6)).to_list(length=max(100, limit * 6))
        except Exception:
            continue
        for row in rows:
            key = _normalize_id(row.get("_id")) or _normalize_id(row.get("file_id"))
            if key in seen:
                continue
            seen.add(key)
            name = norm(row.get("file_name"))
            caption = norm(row.get("caption"))
            score = max(SequenceMatcher(None, q, name).ratio(), SequenceMatcher(None, q, caption).ratio())
            if name:
                score = max(score, sum(max(SequenceMatcher(None, t, n).ratio() for n in name.split()) for t in q.split()) / max(1, len(q.split())))
            if score >= (0.70 if len(q) > 5 else 0.82):
                candidates.append((score, row))
    candidates.sort(key=lambda x: x[0], reverse=True)
    return [row for _, row in candidates[:limit]]


async def search_media_by_title(title, limit=500):
    """Search real Auto Filter records for a title without changing MongoDB."""
    return await search_media(str(title or "").strip(), limit=limit)


def _raw_field_constraint(pattern):
    return {"$or":[
        {"file_name":{"$regex":pattern,"$options":"i"}},
        {"caption":{"$regex":pattern,"$options":"i"}},
    ]}

def _season_constraint(season):
    n=int(season)
    return _raw_field_constraint(rf"(?:\b|[\.\+\-_])(?:s0*{n}|season\s*0*{n})(?:e(?:p(?:isode)?)?\s*0*\d+)?(?:\b|[\.\+\-_])")

def _episode_constraint(episode):
    n=int(episode)
    return _raw_field_constraint(
        rf"(?:\b|[\.\+\-_])(?:(?:s0*\d+|season\s*0*\d+)\s*e(?:p(?:isode)?)?\s*0*{n}|"
        rf"e(?:p(?:isode)?)?\s*0*{n}|episode\s*0*{n}|\d+\s*x\s*0*{n})(?:\b|[\.\+\-_])"
    )

async def search_media_with_filters(query, *, season=None, episode=None,
                                    language=None, quality=None, subtitle=None, limit=1500):
    """Apply cumulative constraints to the same real Auto Filter records."""
    base = build_search_filter(str(query or "").strip())
    if not base:
        return []
    constraints = [base]
    if season is not None:
        constraints.append(_season_constraint(season))
    if episode is not None:
        constraints.append(_episode_constraint(episode))
    if language:
        token = r"(?:\b|[\.\+\-_])" + re.escape(str(language).strip()) + r"(?:\b|[\.\+\-_])"
        constraints.append(_raw_field_constraint(token))
    if quality:
        q = str(quality).strip()
        q_token = q[:-1] if q.lower().endswith("p") else q
        token = r"(?:\b|[\.\+\-_])" + re.escape(q_token) + r"p?(?:\b|[\.\+\-_])"
        constraints.append(_raw_field_constraint(token))
    if subtitle:
        token = r"(?<!\w)" + re.escape(str(subtitle).strip()) + r"(?!\w)"
        constraints.append({"caption": {"$regex": token, "$options": "i"}})

    mongo_filter = constraints[0] if len(constraints) == 1 else {"$and": constraints}
    bounded = max(1, min(int(limit), 1500))
    projection = _SEARCH_PROJECTION

    async def fetch(name, collection):
        if collection is None:
            return name, [], None
        try:
            rows = await (collection.find(mongo_filter, projection)
                          .sort("$natural", -1).limit(bounded)
                          .to_list(length=bounded))
            return name, rows, None
        except Exception as exc:
            return name, [], exc

    configured = [(n, c) for n, c in (("primary", media), ("secondary", media2)) if c is not None]
    if not configured:
        raise RuntimeError("No MongoDB database is configured")
    results = await asyncio.gather(*(fetch(n, c) for n, c in configured))
    docs, succeeded, errors, seen = [], 0, [], set()
    for name, rows, error in results:
        if error is not None:
            errors.append(f"{name}: {type(error).__name__}")
            LOGGER.warning("%s MongoDB filtered search failed: %s", name, type(error).__name__)
            continue
        succeeded += 1
        for doc in rows:
            key = _normalize_id(doc.get("_id")) or _normalize_id(doc.get("file_id"))
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            docs.append(doc)
            if len(docs) >= bounded:
                break
        if len(docs) >= bounded:
            break
    if succeeded == 0:
        raise RuntimeError("All configured MongoDB databases are unreachable or the collection cannot be searched (" + ", ".join(errors) + ")")
    return docs

