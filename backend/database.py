import logging
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
    """Read the existing Auto Filter Bot collection(s), never writing to them."""
    query = query or {}
    seen = set()
    remaining = None if limit is None else max(0, int(limit))

    for collection in _collections():
        if remaining == 0:
            break
        cursor = collection.find(query, projection).sort("$natural", -1)
        if remaining is not None:
            cursor = cursor.limit(remaining)

        emitted_from_collection = 0
        async for doc in cursor:
            key = _normalize_id(doc.get("_id"))
            if not key:
                key = _normalize_id(doc.get("file_id"))
            if key in seen:
                continue
            seen.add(key)
            yield doc
            emitted_from_collection += 1

        if remaining is not None:
            remaining = max(0, remaining - emitted_from_collection)

async def find_media(file_id, projection=None):
    """Find one canonical Auto Filter Bot media document by its Mongo _id."""
    if media is None:
        raise RuntimeError("DATABASE_URI is not configured")

    value = str(file_id)
    for collection in _collections():
        doc = await collection.find_one({"_id": value}, projection)
        if doc is not None:
            return doc

        # Compatibility for a database that may contain a separately stored
        # file_id field. The original Auto Filter Bot maps file_id to _id, so
        # this is only a safe fallback.
        doc = await collection.find_one({"file_id": value}, projection)
        if doc is not None:
            return doc
    return None

def build_search_filter(query):
    """Build a case-insensitive token search against file_name/caption."""
    import re

    terms = [t for t in re.split(r"\s+", str(query or "").strip()) if t]
    if not terms:
        return None

    clauses = []
    for term in terms:
        pattern = re.escape(term)
        clauses.append(
            {
                "$or": [
                    {"file_name": {"$regex": pattern, "$options": "i"}},
                    {"caption": {"$regex": pattern, "$options": "i"}},
                ]
            }
        )
    return {"$and": clauses}

async def search_media(query, limit=100):
    """Search both existing collections without changing their data."""
    search_filter = build_search_filter(query)
    if not search_filter:
        return []

    docs = []
    seen = set()
    per_collection = max(1, int(limit))
    projection = {
        "_id": 1,
        "file_name": 1,
        "file_size": 1,
        "file_type": 1,
        "mime_type": 1,
        "caption": 1,
        "file_ref": 1,
    }

    for collection in _collections():
        cursor = (
            collection.find(search_filter, projection)
            .sort("$natural", -1)
            .limit(per_collection)
        )
        async for doc in cursor:
            key = _normalize_id(doc.get("_id"))
            if key in seen:
                continue
            seen.add(key)
            docs.append(doc)
            if len(docs) >= limit:
                return docs
    return docs
