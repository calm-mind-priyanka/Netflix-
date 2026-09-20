"""Persistent website-side MongoDB storage.

Media collections remain read-only. These collections are separate website
collections in the same configured MongoDB database and store only website
state (premium orders/users, manual proofs and verification tokens).
"""
from __future__ import annotations

import os
from motor.motor_asyncio import AsyncIOMotorClient
from .config import DATABASE_URI, DATABASE_NAME, WEBSITE_SETTINGS_COLLECTION, WEBSITE_CATALOG_COLLECTION

_client = None
_db = None
premium_users = None
premium_orders = None
premium_manual = None
verification_tokens = None
settings = None
catalog_index = None

if DATABASE_URI:
    _client = AsyncIOMotorClient(
        DATABASE_URI,
        serverSelectionTimeoutMS=8000,
        connectTimeoutMS=8000,
    )
    _db = _client[DATABASE_NAME]
    premium_users = _db[os.getenv("WEBSITE_PREMIUM_USERS_COLLECTION", "vyra_premium_users")]
    premium_orders = _db[os.getenv("WEBSITE_PREMIUM_ORDERS_COLLECTION", "vyra_premium_orders")]
    premium_manual = _db[os.getenv("WEBSITE_PREMIUM_MANUAL_COLLECTION", "vyra_premium_manual")]
    verification_tokens = _db[os.getenv("WEBSITE_VERIFY_COLLECTION", "vyra_verification_tokens")]
    settings = _db[WEBSITE_SETTINGS_COLLECTION]
    catalog_index = _db[WEBSITE_CATALOG_COLLECTION]

_indexes_ready = False

async def ensure_indexes():
    global _indexes_ready
    if _indexes_ready or _db is None:
        return
    try:
        await premium_orders.create_index("order_id", unique=True)
        await premium_users.create_index("user_id", unique=True)
        await premium_manual.create_index([("status", 1), ("created_at", 1)])
        await verification_tokens.create_index("code", unique=True)
        await verification_tokens.create_index("expires", expireAfterSeconds=0)
        await settings.create_index("_id", unique=True)
        await catalog_index.create_index("_id", unique=True)
        await catalog_index.create_index([("title_norm", 1), ("type", 1), ("year", 1)])
    except Exception:
        # Index creation must never prevent the web server from starting.
        pass
    _indexes_ready = True

async def ping():
    if _client is None:
        raise RuntimeError("DATABASE_URI is not configured")
    await _client.admin.command("ping")


async def load_settings_document():
    if settings is None:
        return None
    return await settings.find_one({"_id": "global"})

async def save_settings_document(value):
    if settings is None:
        raise RuntimeError("Website settings Mongo collection is unavailable")
    await settings.replace_one({"_id": "global"}, {"_id": "global", "settings": value, "updated_at": __import__("time").time()}, upsert=True)

async def save_catalog_identity(item):
    if catalog_index is None or not item or not item.get("id"):
        return
    doc = {
        "_id": str(item["id"]),
        "title": item.get("title"),
        "title_norm": str(item.get("title") or "").casefold(),
        "type": item.get("type", "movie"),
        "year": item.get("year"),
        "poster": item.get("poster"),
        "tmdb_id": item.get("tmdb_id"),
        "updated_at": __import__("time").time(),
    }
    await catalog_index.replace_one({"_id": doc["_id"]}, {"$set": doc}, upsert=True)

async def get_catalog_identity(title_id):
    if catalog_index is None:
        return None
    return await catalog_index.find_one({"_id": str(title_id)})
