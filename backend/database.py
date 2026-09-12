from motor.motor_asyncio import AsyncIOMotorClient
from .config import DATABASE_URI, DATABASE_URI2, DATABASE_NAME, COLLECTION_NAME, MULTIPLE_DB

client = AsyncIOMotorClient(DATABASE_URI)
db = client[DATABASE_NAME]
media = db[COLLECTION_NAME]
client2 = AsyncIOMotorClient(DATABASE_URI2) if MULTIPLE_DB and DATABASE_URI2 else None
media2 = client2[DATABASE_NAME][COLLECTION_NAME] if client2 else None

async def iter_media(query=None, projection=None):
    query = query or {}
    seen = set()
    collections = (media, media2) if media2 is not None else (media,)
    for collection in collections:
        cursor = collection.find(query, projection)
        async for doc in cursor:
            key = str(doc.get("_id"))
            if key in seen:
                continue
            seen.add(key)
            yield doc

async def get_setting(key, default=None):
    doc = await media.database["website_settings"].find_one({"_id": key})
    return doc.get("value", default) if doc else default

async def set_setting(key, value):
    await media.database["website_settings"].update_one({"_id": key}, {"$set": {"value": value}}, upsert=True)
