from motor.motor_asyncio import AsyncIOMotorClient
from .config import DATABASE_URI, DATABASE_URI2, DATABASE_NAME, COLLECTION_NAME, MULTIPLE_DB

client = AsyncIOMotorClient(DATABASE_URI, serverSelectionTimeoutMS=8000)
db = client[DATABASE_NAME]
media = db[COLLECTION_NAME]
client2 = AsyncIOMotorClient(DATABASE_URI2, serverSelectionTimeoutMS=8000) if MULTIPLE_DB and DATABASE_URI2 else None
media2 = client2[DATABASE_NAME][COLLECTION_NAME] if client2 else None

async def ping():
    await client.admin.command("ping")
    if client2 is not None:
        await client2.admin.command("ping")

async def collection_counts():
    counts = {"primary": 0, "secondary": 0}
    counts["primary"] = await media.count_documents({})
    if media2 is not None:
        counts["secondary"] = await media2.count_documents({})
    return counts

async def iter_media(query=None, projection=None):
    query = query or {}
    seen = set()
    collections = (media, media2) if media2 is not None else (media,)
    for collection in collections:
        if collection is None:
            continue
        cursor = collection.find(query, projection).sort("$natural", -1)
        async for doc in cursor:
            key = str(doc.get("_id") or doc.get("file_id") or "")
            if key in seen:
                continue
            seen.add(key)
            yield doc

async def get_setting(key, default=None):
    doc = await media.database["website_settings"].find_one({"_id": key})
    return doc.get("value", default) if doc else default

async def set_setting(key, value):
    await media.database["website_settings"].update_one(
        {"_id": key}, {"$set": {"value": value}}, upsert=True
    )
