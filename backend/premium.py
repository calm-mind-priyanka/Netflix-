"""Premium, automatic payment and manual UPI payment service.

All business state is MongoDB-backed. Website identity comes from the account
session in backend.users; the public eight-digit User ID is never treated as
a secret or as authentication.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

from aiohttp import web

from .config import (
    PREMIUM_PLANS,
    PAYMENT_PROVIDER,
    RAZORPAY_KEY_ID,
    RAZORPAY_KEY_SECRET,
    RAZORPAY_WEBHOOK_SECRET,
    MANUAL_PAYMENT_INSTRUCTIONS,
    MANUAL_PAYMENT_QR,
)
from .web_store import premium_users, premium_orders, premium_manual, payments, users, history
from .admin_settings import get_value
from .users import require_user, current_user

async def _ready():
    from .web_store import ensure_indexes
    await ensure_indexes()


def plan_list():
    return [
        {"id": k, "name": v["name"], "days": v["days"], "price_inr": v["price_inr"]}
        for k, v in PREMIUM_PLANS.items()
    ]


def _payment_config():
    return {
        "upi_id": str(get_value("payments", "upi_id", default="") or "").strip(),
        "manual_enabled": bool(get_value("payments", "manual_enabled", default=PAYMENT_PROVIDER in ("manual", "both"))),
        "instructions": str(get_value("payments", "manual_instructions", default=MANUAL_PAYMENT_INSTRUCTIONS) or "").strip(),
        "qr": str(get_value("payments", "manual_qr", default=MANUAL_PAYMENT_QR) or "").strip(),
    }


async def get_status(uid):
    await _ready()
    if not uid or premium_users is None:
        return {"plan": "free", "premium": False, "expires_at": 0, "plan_name": ""}
    u = await premium_users.find_one({"user_id": uid}) or {}
    exp = int(u.get("expires_at", 0) or 0)
    active = exp > int(time.time()) and u.get("status", "active") not in {"revoked", "inactive"}
    return {
        "plan": u.get("plan", "free") if active else "free",
        "premium": active,
        "expires_at": exp if active else exp,
        "plan_name": u.get("plan_name", "") if active else "",
        "source": u.get("source", "") if active else "",
    }


async def status(request):
    user = await current_user(request)
    if not user:
        return web.json_response({
            "ok": True,
            "authenticated": False,
            "provider": PAYMENT_PROVIDER,
            "activation_mode": str(get_value("payments", "activation_mode", default="environment")).lower(),
            "plans": plan_list(),
            **_payment_config(),
        })
    activation_mode = str(get_value("payments", "activation_mode", default="environment")).lower()
    cfg = _payment_config()
    return web.json_response({
        "ok": True,
        "authenticated": True,
        "user_id": user["user_id"],
        "nickname": user.get("nickname", ""),
        "provider": PAYMENT_PROVIDER,
        "activation_mode": activation_mode,
        "plans": plan_list(),
        "manual_instructions": cfg["instructions"],
        "manual_qr": cfg["qr"],
        "upi_id": cfg["upi_id"],
        "manual_enabled": cfg["manual_enabled"],
        **await get_status(user["user_id"]),
    })


async def create_order(request):
    user = await require_user(request)
    activation_mode = str(get_value("payments", "activation_mode", default="environment")).lower()
    if activation_mode == "manual":
        return web.json_response({"ok": False, "error": "Automatic payment is disabled by the admin. Please use manual payment proof."}, status=403)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid payment request"}, status=400)
    plan_id = str(body.get("plan_id", "30day"))
    plan = PREMIUM_PLANS.get(plan_id)
    if not plan:
        return web.json_response({"ok": False, "error": "Invalid premium plan"}, status=400)
    if PAYMENT_PROVIDER not in ("razorpay", "both") or not (RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET):
        return web.json_response({"ok": False, "error": "Automatic Razorpay payment is not configured."}, status=503)
    try:
        import razorpay
    except ImportError:
        return web.json_response({"ok": False, "error": "Razorpay support is not installed."}, status=503)
    client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
    receipt = f"vyra_{user['user_id']}_{plan_id}_{int(time.time())}_{secrets.token_hex(3)}"
    order = client.order.create({
        "amount": plan["price_inr"] * 100,
        "currency": "INR",
        "receipt": receipt,
        "notes": {"user_id": user["user_id"], "plan_id": plan_id, "days": plan["days"]},
    })
    await _ready()
    now = int(time.time())
    await premium_orders.update_one(
        {"order_id": order["id"]},
        {"$set": {
            "order_id": order["id"],
            "user_id": user["user_id"],
            "nickname": user.get("nickname", ""),
            "plan_id": plan_id,
            "plan_name": plan["name"],
            "amount": plan["price_inr"] * 100,
            "created_at": now,
            "status": "created",
        }},
        upsert=True,
    )
    return web.json_response({
        "ok": True,
        "provider": "razorpay",
        "key_id": RAZORPAY_KEY_ID,
        "order_id": order["id"],
        "amount": plan["price_inr"] * 100,
        "currency": "INR",
        "days": plan["days"],
        "plan_id": plan_id,
        "plan_name": plan["name"],
        "user_id": user["user_id"],
    })


async def _record_history(uid, event_type, metadata=None, actor="system"):
    if history is None:
        return
    await history.insert_one({
        "event_id": secrets.token_urlsafe(12),
        "user_id": uid,
        "event_type": event_type,
        "metadata": metadata or {},
        "actor": actor,
        "created_at": int(time.time()),
    })


async def _record_payment(doc):
    if payments is None:
        return
    payment_id = str(doc.get("payment_id") or doc.get("request_id") or doc.get("order_id") or secrets.token_urlsafe(12))
    await payments.update_one(
        {"payment_id": payment_id},
        {"$set": {**doc, "payment_id": payment_id}},
        upsert=True,
    )
    return payment_id


async def _activate(uid, plan_id, source, payment_id="", actor="system", payment_method=""):
    await _ready()
    if users is not None:
        user = await users.find_one({"user_id": uid}, {"nickname": 1})
        if not user:
            raise ValueError("Website user does not exist")
    plan = PREMIUM_PLANS[plan_id]
    now = int(time.time())
    old_doc = await premium_users.find_one({"user_id": uid}) or {}
    old = int(old_doc.get("expires_at", 0) or 0)
    exp = max(now, old) + plan["days"] * 86400
    was_active = old > now and old_doc.get("status", "active") not in {"revoked", "inactive"}
    start = int(old_doc.get("started_at", now) or now) if was_active else now
    await premium_users.update_one(
        {"user_id": uid},
        {"$set": {
            "user_id": uid,
            "status": "active",
            "plan": plan_id,
            "plan_name": plan["name"],
            "amount": plan["price_inr"],
            "started_at": int(old_doc.get("started_at", start) or start),
            "expires_at": exp,
            "source": source,
            "last_payment": payment_id,
            "updated_at": now,
        }},
        upsert=True,
    )
    event = "PREMIUM_EXTENDED" if was_active else "PREMIUM_ACTIVATED"
    await _record_history(uid, event, {
        "plan_id": plan_id,
        "plan_name": plan["name"],
        "amount": plan["price_inr"],
        "source": source,
        "payment_id": payment_id,
        "previous_expires_at": old,
        "expires_at": exp,
        "payment_method": payment_method,
    }, actor=actor)
    return exp


async def _release_order(order_id):
    if premium_orders is not None:
        await premium_orders.update_one({"order_id": order_id, "status": "processing"}, {"$set": {"status": "created"}, "$unset": {"processing_at": ""}})


async def _claim_order(order_id):
    await _ready()
    now = int(time.time())
    order = await premium_orders.find_one({"order_id": order_id})
    if not order:
        return None, False
    if order.get("status") == "paid":
        return order, False
    processing_at = int(order.get("processing_at", 0) or 0)
    if order.get("status") == "processing" and processing_at and now - processing_at < 600:
        return order, False
    result = await premium_orders.update_one(
        {"order_id": order_id, "$or": [
            {"status": {"$ne": "processing"}},
            {"processing_at": {"$lt": now - 600}},
        ]},
        {"$set": {"status": "processing", "processing_at": now}},
    )
    if result.modified_count:
        order["status"] = "processing"
        return order, True
    return await premium_orders.find_one({"order_id": order_id}), False


async def verify_payment(request):
    user = await require_user(request)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid payment data"}, status=400)
    oid = str(data.get("razorpay_order_id", ""))
    pay = str(data.get("razorpay_payment_id", ""))
    sig = str(data.get("razorpay_signature", ""))
    if not oid or not pay or not sig:
        return web.json_response({"ok": False, "error": "Missing payment data"}, status=400)
    if not RAZORPAY_KEY_SECRET:
        return web.json_response({"ok": False, "error": "Razorpay secret is not configured."}, status=503)
    expected = hmac.new(RAZORPAY_KEY_SECRET.encode(), f"{oid}|{pay}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return web.json_response({"ok": False, "error": "Invalid payment signature"}, status=400)

    await _ready()
    order = await premium_orders.find_one({"order_id": oid})
    plan_id = order.get("plan_id") if order else None
    if not order or order.get("user_id") != user["user_id"] or plan_id not in PREMIUM_PLANS:
        return web.json_response({"ok": False, "error": "Unknown order"}, status=400)

    if order.get("status") == "paid":
        return web.json_response({"ok": True, "plan": plan_id, "expires_at": int((await get_status(user["user_id"])).get("expires_at", 0))})

    order, claimed = await _claim_order(oid)
    if not claimed:
        if order and order.get("status") == "paid":
            return web.json_response({"ok": True, "plan": plan_id, "expires_at": int((await get_status(user["user_id"])).get("expires_at", 0))})
        return web.json_response({"ok": False, "error": "Payment verification is already being processed. Please check Premium status shortly."}, status=409)

    try:
        import razorpay
        client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
        payment_entity = client.payment.fetch(pay)
        if str(payment_entity.get("order_id", "")) != oid:
            await _release_order(oid)
            return web.json_response({"ok": False, "error": "Payment/order mismatch"}, status=400)
        if int(payment_entity.get("amount", 0) or 0) != int(order.get("amount", 0) or 0):
            await _release_order(oid)
            return web.json_response({"ok": False, "error": "Payment amount mismatch"}, status=400)
        if str(payment_entity.get("status", "")).lower() not in {"captured", "authorized"}:
            await _release_order(oid)
            return web.json_response({"ok": False, "error": "Payment is not captured yet"}, status=400)
    except Exception as exc:
        await _release_order(oid)
        return web.json_response({"ok": False, "error": f"Payment verification could not be confirmed: {exc}"}, status=400)

    exp = await _activate(user["user_id"], plan_id, "razorpay", pay, actor="system", payment_method="razorpay")
    now = int(time.time())
    await premium_orders.update_one({"order_id": oid}, {"$set": {"status": "paid", "payment_id": pay, "paid_at": now, "expires_at": exp}})
    await _record_payment({
        "payment_id": pay,
        "user_id": user["user_id"],
        "nickname": user.get("nickname", ""),
        "method": "automatic",
        "provider": "razorpay",
        "provider_order_id": oid,
        "provider_payment_id": pay,
        "plan_id": plan_id,
        "plan_name": PREMIUM_PLANS[plan_id]["name"],
        "amount": PREMIUM_PLANS[plan_id]["price_inr"],
        "currency": "INR",
        "status": "successful",
        "created_at": now,
        "verified_at": now,
        "expires_at": exp,
    })
    await _record_history(user["user_id"], "PAYMENT_SUCCESS", {"payment_id": pay, "order_id": oid, "plan_id": plan_id, "amount": PREMIUM_PLANS[plan_id]["price_inr"]})
    return web.json_response({"ok": True, "plan": plan_id, "expires_at": exp})


async def webhook(request):
    raw = await request.read()
    sig = request.headers.get("X-Razorpay-Signature", "")
    if not RAZORPAY_WEBHOOK_SECRET or not hmac.compare_digest(hmac.new(RAZORPAY_WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest(), sig):
        return web.Response(status=401)
    try:
        import json
        data = json.loads(raw)
        entity = data.get("payload", {}).get("payment", {}).get("entity", {})
        oid = entity.get("order_id")
    except Exception:
        return web.Response(status=400)
    if not oid:
        return web.Response(status=200)
    await _ready()
    order = await premium_orders.find_one({"order_id": oid})
    if order and order.get("status") != "paid" and order.get("plan_id") in PREMIUM_PLANS:
        order, claimed = await _claim_order(oid)
        if not claimed:
            return web.Response(status=200)
        plan_id = order["plan_id"]
        expected_amount = PREMIUM_PLANS[plan_id]["price_inr"] * 100
        if int(entity.get("amount", expected_amount) or 0) != expected_amount:
            return web.Response(status=400)
        if str(entity.get("status", "captured")).lower() not in {"captured", "authorized"}:
            return web.Response(status=200)
        exp = await _activate(order["user_id"], plan_id, "razorpay_webhook", entity.get("id", ""), actor="webhook", payment_method="razorpay")
        now = int(time.time())
        await premium_orders.update_one({"order_id": oid}, {"$set": {"status": "paid", "payment_id": entity.get("id", ""), "paid_at": now, "expires_at": exp}})
        await _record_payment({
            "payment_id": entity.get("id", "") or oid,
            "user_id": order["user_id"],
            "nickname": order.get("nickname", ""),
            "method": "automatic",
            "provider": "razorpay",
            "provider_order_id": oid,
            "provider_payment_id": entity.get("id", ""),
            "plan_id": plan_id,
            "plan_name": PREMIUM_PLANS[plan_id]["name"],
            "amount": PREMIUM_PLANS[plan_id]["price_inr"],
            "currency": "INR",
            "status": "successful",
            "created_at": int(order.get("created_at", now)),
            "verified_at": now,
            "expires_at": exp,
        })
        await _record_history(order["user_id"], "PAYMENT_SUCCESS", {"payment_id": entity.get("id", ""), "order_id": oid, "source": "webhook"})
    return web.Response(status=200)


async def manual_submit(request):
    user = await require_user(request)
    activation_mode = str(get_value("payments", "activation_mode", default="environment")).lower()
    cfg = _payment_config()
    if activation_mode == "auto" or not cfg["manual_enabled"] or PAYMENT_PROVIDER not in ("manual", "both"):
        return web.json_response({"ok": False, "error": "Manual payment is disabled."}, status=403)

    reader = await request.multipart()
    fields = {}
    proof = None
    proof_name = ""
    async for part in reader:
        if part.name == "proof":
            proof_name = os.path.basename(part.filename or "proof.jpg")
            ext = Path(proof_name).suffix.lower()
            if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
                return web.json_response({"ok": False, "error": "Proof must be JPG, PNG or WEBP."}, status=400)
            raw = await part.read(decode=False)
            if len(raw) > 5 * 1024 * 1024:
                return web.json_response({"ok": False, "error": "Proof image must be 5MB or smaller."}, status=400)
            proof = (raw, ext)
        else:
            fields[part.name] = (await part.text()).strip()

    plan_id = fields.get("plan_id", "")
    plan = PREMIUM_PLANS.get(plan_id)
    utr = fields.get("utr", "").strip()
    if not plan or not proof:
        return web.json_response({"ok": False, "error": "Select a plan and upload payment screenshot."}, status=400)
    if len(utr) < 6 or len(utr) > 80:
        return web.json_response({"ok": False, "error": "Enter a valid UTR/reference number."}, status=400)

    await _ready()
    existing = await premium_manual.find_one({"utr": utr}, {"_id": 1, "status": 1})
    if existing:
        return web.json_response({"ok": False, "error": "This UTR/reference has already been submitted."}, status=409)

    req_id = secrets.token_urlsafe(12)
    now = int(time.time())
    doc = {
        "request_id": req_id,
        "user_id": user["user_id"],
        "nickname": user.get("nickname", ""),
        "plan_id": plan_id,
        "plan_name": plan["name"],
        "amount": plan["price_inr"],
        "utr": utr,
        "proof": proof[0],
        "proof_ext": proof[1],
        "proof_name": proof_name,
        "note": fields.get("note", ""),
        "created_at": now,
        "status": "pending",
    }
    await premium_manual.insert_one(doc)
    await _record_payment({
        "payment_id": req_id,
        "request_id": req_id,
        "user_id": user["user_id"],
        "nickname": user.get("nickname", ""),
        "method": "upi_manual",
        "provider": "manual",
        "plan_id": plan_id,
        "plan_name": plan["name"],
        "amount": plan["price_inr"],
        "currency": "INR",
        "status": "pending",
        "utr": utr,
        "proof_reference": f"manual:{req_id}",
        "note": fields.get("note", ""),
        "created_at": now,
    })
    await _record_history(user["user_id"], "MANUAL_PAYMENT_SUBMITTED", {"request_id": req_id, "plan_id": plan_id, "amount": plan["price_inr"], "utr": utr})
    return web.json_response({"ok": True, "request_id": req_id, "status": "pending", "message": "Payment proof sent for admin approval."})


async def my_manual(request):
    user = await require_user(request)
    await _ready()
    rows = []
    if premium_manual is not None:
        async for v in premium_manual.find({"user_id": user["user_id"]}, {"proof": 0}).sort("created_at", -1).limit(20):
            v.pop("_id", None)
            v["id"] = v.pop("request_id", "")
            rows.append(v)
    return web.json_response({"ok": True, "requests": rows})


def _admin_ok(request):
    from .auth import validate_admin_session
    return validate_admin_session(request.cookies.get("admin_session", ""))


async def admin_manual_list(request):
    if not _admin_ok(request):
        raise web.HTTPUnauthorized(text="Admin login required")
    await _ready()
    rows = []
    async for v in premium_manual.find({}, {"proof": 0}).sort("created_at", -1).limit(500):
        v.pop("_id", None)
        v["id"] = v.pop("request_id", "")
        rows.append(v)
    return web.json_response({"ok": True, "requests": rows})


async def admin_manual_proof(request):
    if not _admin_ok(request):
        raise web.HTTPUnauthorized(text="Admin login required")
    rid = request.match_info["request_id"]
    await _ready()
    r = await premium_manual.find_one({"request_id": rid}, {"proof": 1, "proof_ext": 1})
    if not r or not r.get("proof"):
        raise web.HTTPNotFound()
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}.get(r.get("proof_ext"), "application/octet-stream")
    return web.Response(body=bytes(r["proof"]), content_type=mime, headers={"Cache-Control": "private, no-store"})


async def admin_manual_decide(request):
    if not _admin_ok(request):
        raise web.HTTPUnauthorized(text="Admin login required")
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "Request body must be an object"}, status=400)
    rid = str(body.get("request_id", "")).strip()
    action = str(body.get("action", "")).strip().lower()
    if not rid:
        return web.json_response({"ok": False, "error": "Missing payment request ID"}, status=400)
    if action not in {"approve", "reject"}:
        return web.json_response({"ok": False, "error": "Invalid action"}, status=400)
    await _ready()
    if premium_manual is None or payments is None:
        return web.json_response({"ok": False, "error": "Payment storage is unavailable"}, status=503)
    r = await premium_manual.find_one({"request_id": rid})
    if not r:
        return web.json_response({"ok": False, "error": "Request not found"}, status=404)
    if r.get("status") != "pending":
        return web.json_response({"ok": False, "error": "Request already processed"}, status=400)
    now = int(time.time())
    if action == "approve":
        try:
            exp = await _activate(r["user_id"], r["plan_id"], "manual", rid, actor="admin", payment_method="upi_manual")
        except ValueError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)
        await premium_manual.update_one({"request_id": rid}, {"$set": {"status": "approved", "approved_at": now, "reviewed_at": now, "expires_at": exp}})
        await payments.update_one({"payment_id": rid}, {"$set": {"status": "approved", "reviewed_at": now, "approved_at": now, "expires_at": exp}})
        await _record_history(r["user_id"], "MANUAL_PAYMENT_APPROVED", {"request_id": rid, "utr": r.get("utr", ""), "expires_at": exp}, actor="admin")
    else:
        await premium_manual.update_one({"request_id": rid}, {"$set": {"status": "rejected", "rejected_at": now, "reviewed_at": now}})
        await payments.update_one({"payment_id": rid}, {"$set": {"status": "rejected", "reviewed_at": now, "rejected_at": now}})
        await _record_history(r["user_id"], "MANUAL_PAYMENT_REJECTED", {"request_id": rid, "utr": r.get("utr", "")}, actor="admin")
    return web.json_response({"ok": True, "status": action})


async def admin_grant(request):
    if not _admin_ok(request):
        raise web.HTTPUnauthorized(text="Admin login required")
    body = await request.json()
    uid = str(body.get("user_id", "")).strip()
    plan_id = str(body.get("plan_id", "30day"))
    if not uid or plan_id not in PREMIUM_PLANS:
        return web.json_response({"ok": False, "error": "user_id and valid plan_id required"}, status=400)
    user = await users.find_one({"user_id": uid}) if users is not None else None
    if not user:
        return web.json_response({"ok": False, "error": "Website user not found"}, status=404)
    exp = await _activate(uid, plan_id, "admin", "", actor="admin", payment_method="admin_grant")
    await _record_payment({
        "payment_id": secrets.token_urlsafe(12),
        "user_id": uid,
        "nickname": user.get("nickname", ""),
        "method": "admin_grant",
        "provider": "admin",
        "plan_id": plan_id,
        "plan_name": PREMIUM_PLANS[plan_id]["name"],
        "amount": PREMIUM_PLANS[plan_id]["price_inr"],
        "currency": "INR",
        "status": "admin_granted",
        "created_at": int(time.time()),
        "expires_at": exp,
    })
    return web.json_response({"ok": True, "user_id": uid, "plan": plan_id, "expires_at": exp})


async def admin_revoke(request):
    if not _admin_ok(request):
        raise web.HTTPUnauthorized(text="Admin login required")
    uid = str((await request.json()).get("user_id", "")).strip()
    if not uid:
        return web.json_response({"ok": False, "error": "user_id required"}, status=400)
    await _ready()
    if users is None or not await users.find_one({"user_id": uid}, {"_id": 1}):
        return web.json_response({"ok": False, "error": "Website user not found"}, status=404)
    now = int(time.time())
    await premium_users.update_one({"user_id": uid}, {"$set": {"status": "revoked", "updated_at": now, "revoked_at": now, "revoked_by": "admin"}})
    await _record_history(uid, "PREMIUM_REMOVED", {"at": now}, actor="admin")
    return web.json_response({"ok": True, "user_id": uid, "status": "revoked"})


async def admin_extend(request):
    if not _admin_ok(request):
        raise web.HTTPUnauthorized(text="Admin login required")
    body = await request.json()
    uid = str(body.get("user_id", "")).strip()
    plan_id = str(body.get("plan_id", "30day"))
    if not uid or plan_id not in PREMIUM_PLANS:
        return web.json_response({"ok": False, "error": "user_id and valid plan_id required"}, status=400)
    user = await users.find_one({"user_id": uid}) if users is not None else None
    if not user:
        return web.json_response({"ok": False, "error": "Website user not found"}, status=404)
    current = await premium_users.find_one({"user_id": uid}) or {}
    old = int(current.get("expires_at", 0) or 0)
    if old <= int(time.time()):
        return web.json_response({"ok": False, "error": "Premium is not currently active; use Add Premium instead."}, status=400)
    exp = await _activate(uid, plan_id, "admin_extend", "", actor="admin", payment_method="admin_extend")
    return web.json_response({"ok": True, "user_id": uid, "previous_expires_at": old, "expires_at": exp})

async def admin_premium_users(request):
    if not _admin_ok(request):
        raise web.HTTPUnauthorized(text="Admin login required")
    await _ready()
    now = int(time.time())
    rows = []
    async for p in premium_users.find({"status": {"$nin": ["revoked", "inactive"]}, "expires_at": {"$gt": now}}).sort("expires_at", 1).limit(1000):
        uid = p.get("user_id", "")
        u = await users.find_one({"user_id": uid}, {"nickname": 1}) if users is not None else None
        rows.append({
            "user_id": uid,
            "nickname": (u or {}).get("nickname", "Unknown"),
            "status": "active",
            "plan_id": p.get("plan", ""),
            "plan_name": p.get("plan_name", ""),
            "amount": p.get("amount", 0),
            "started_at": p.get("started_at", 0),
            "expires_at": p.get("expires_at", 0),
            "source": p.get("source", ""),
            "last_payment": p.get("last_payment", ""),
        })
    return web.json_response({"ok": True, "users": rows})
