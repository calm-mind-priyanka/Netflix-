"""Persistent website-side MongoDB storage.

Media collections remain read-only. These collections are separate website
collections in the same configured MongoDB database and store only website
state (premium orders/users, manual proofs and verification tokens).
"""
from __future__ import annotations

import os
from motor.motor_asyncio import AsyncIOMotorClient
from .config import DATABASE_URI, DATABASE_NAME

_client = None
_db = None
premium_users = None
premium_orders = None
premium_manual = None
verification_tokens = None

if DATABASE_URI:
    _client = AsyncIOMotorClient(
        DATABASE_URI,
        serverSelectionTimeoutMS=8000,
        connectTimeoutMS=8000,
    )
    _db = _client[DATABASE_NAME]
    premium_users = _db[os.getenv("WEBSITE_PREMIUM_USERS_COLLECTION", "streambox_premium_users")]
    premium_orders = _db[os.getenv("WEBSITE_PREMIUM_ORDERS_COLLECTION", "streambox_premium_orders")]
    premium_manual = _db[os.getenv("WEBSITE_PREMIUM_MANUAL_COLLECTION", "streambox_premium_manual")]
    verification_tokens = _db[os.getenv("WEBSITE_VERIFY_COLLECTION", "streambox_verification_tokens")]

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
    except Exception:
        # Index creation must never prevent the web server from starting.
        pass
    _indexes_ready = True

async def ping():
    if _client is None:
        raise RuntimeError("DATABASE_URI is not configured")
    await _client.admin.command("ping")
