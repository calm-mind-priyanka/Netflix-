import hashlib, hmac, os, secrets, time
from pathlib import Path
from aiohttp import web
from .config import PREMIUM_PLANS, PAYMENT_PROVIDER, RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, RAZORPAY_WEBHOOK_SECRET, MANUAL_PAYMENT_INSTRUCTIONS, MANUAL_PAYMENT_QR
from .web_store import premium_users, premium_orders, premium_manual

async def _ready():
    from .web_store import ensure_indexes
    await ensure_indexes()

def user_id(request):
    return request.cookies.get('vyra_user') or ''

def ensure_user(response):
    uid=secrets.token_urlsafe(18)
    response.set_cookie('vyra_user',uid,httponly=True,samesite='Lax',secure=True,max_age=31536000,path='/')
    return uid

def plan_list():
    return [{'id':k,'name':v['name'],'days':v['days'],'price_inr':v['price_inr']} for k,v in PREMIUM_PLANS.items()]

async def get_status(uid):
    await _ready()
    if not uid or premium_users is None:
        return {'plan':'free','premium':False,'expires_at':0}
    u=await premium_users.find_one({'user_id':uid}) or {}
    exp=int(u.get('expires_at',0) or 0)
    active=exp>int(time.time())
    return {'plan':u.get('plan','free') if active else 'free','premium':active,'expires_at':exp,'plan_name':u.get('plan_name','') if active else ''}

async def status(request):
    uid=user_id(request)
    resp=web.json_response({'ok':True,'user_id':uid,'provider':PAYMENT_PROVIDER,'plans':plan_list(),'manual_instructions':MANUAL_PAYMENT_INSTRUCTIONS,'manual_qr':MANUAL_PAYMENT_QR,**await get_status(uid)})
    if not uid: ensure_user(resp)
    return resp

async def create_order(request):
    body=await request.json(); plan_id=str(body.get('plan_id','30day')); plan=PREMIUM_PLANS.get(plan_id)
    if not plan: return web.json_response({'ok':False,'error':'Invalid premium plan'},status=400)
    if PAYMENT_PROVIDER not in ('razorpay','both') or not (RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET):
        return web.json_response({'ok':False,'error':'Automatic Razorpay payment is not configured.'},status=503)
    try: import razorpay
    except ImportError: return web.json_response({'ok':False,'error':'Razorpay support is not installed.'},status=503)
    uid=user_id(request)
    if not uid: return web.json_response({'ok':False,'error':'User cookie missing; reload and try again.'},status=400)
    client=razorpay.Client(auth=(RAZORPAY_KEY_ID,RAZORPAY_KEY_SECRET))
    order=client.order.create({'amount':plan['price_inr']*100,'currency':'INR','receipt':f'sb_{uid[:20]}_{plan_id}_{int(time.time())}','notes':{'user_id':uid,'plan_id':plan_id,'days':plan['days']}})
    await _ready()
    await premium_orders.update_one({'order_id':order['id']},{'$set':{'order_id':order['id'],'user_id':uid,'plan_id':plan_id,'amount':plan['price_inr']*100,'created_at':int(time.time()),'status':'created'}},upsert=True)
    return web.json_response({'ok':True,'provider':'razorpay','key_id':RAZORPAY_KEY_ID,'order_id':order['id'],'amount':plan['price_inr']*100,'currency':'INR','days':plan['days'],'plan_id':plan_id,'plan_name':plan['name']})

async def _activate(uid,plan_id,source,payment_id=''):
    await _ready()
    plan=PREMIUM_PLANS[plan_id]; now=int(time.time())
    old_doc=await premium_users.find_one({'user_id':uid}) or {}
    old=int(old_doc.get('expires_at',0) or 0)
    exp=max(now,old)+plan['days']*86400
    await premium_users.update_one({'user_id':uid},{'$set':{'user_id':uid,'plan':plan_id,'plan_name':plan['name'],'expires_at':exp,'source':source,'last_payment':payment_id,'updated_at':now}},upsert=True)
    return exp

async def verify_payment(request):
    data=await request.json(); uid=user_id(request); oid=str(data.get('razorpay_order_id','')); pay=str(data.get('razorpay_payment_id','')); sig=str(data.get('razorpay_signature',''))
    if not uid or not oid or not pay or not sig: return web.json_response({'ok':False,'error':'Missing payment data'},status=400)
    if not RAZORPAY_KEY_SECRET: return web.json_response({'ok':False,'error':'Razorpay secret is not configured.'},status=503)
    expected=hmac.new(RAZORPAY_KEY_SECRET.encode(),f'{oid}|{pay}'.encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected,sig): return web.json_response({'ok':False,'error':'Invalid payment signature'},status=400)
    await _ready(); order=await premium_orders.find_one({'order_id':oid}); plan_id=order.get('plan_id') if order else None
    if not order or order.get('user_id')!=uid or plan_id not in PREMIUM_PLANS: return web.json_response({'ok':False,'error':'Unknown order'},status=400)
    if order.get('status')=='paid': return web.json_response({'ok':True,'plan':plan_id,'expires_at':int((await get_status(uid)).get('expires_at',0))})
    exp=await _activate(uid,plan_id,'razorpay',pay)
    await premium_orders.update_one({'order_id':oid},{'$set':{'status':'paid','payment_id':pay,'paid_at':int(time.time())}})
    return web.json_response({'ok':True,'plan':plan_id,'expires_at':exp})

async def webhook(request):
    raw=await request.read(); sig=request.headers.get('X-Razorpay-Signature','')
    if not RAZORPAY_WEBHOOK_SECRET or not hmac.compare_digest(hmac.new(RAZORPAY_WEBHOOK_SECRET.encode(),raw,hashlib.sha256).hexdigest(),sig): return web.Response(status=401)
    try:
        import json
        data=json.loads(raw); entity=data.get('payload',{}).get('payment',{}).get('entity',{}); oid=entity.get('order_id')
    except Exception: return web.Response(status=400)
    if not oid: return web.Response(status=200)
    await _ready(); order=await premium_orders.find_one({'order_id':oid})
    if order and order.get('status')!='paid' and order.get('plan_id') in PREMIUM_PLANS:
        exp=await _activate(order['user_id'],order['plan_id'],'razorpay_webhook',entity.get('id'))
        await premium_orders.update_one({'order_id':oid},{'$set':{'status':'paid','payment_id':entity.get('id',''),'paid_at':int(time.time()),'expires_at':exp}})
    return web.Response(status=200)

async def manual_submit(request):
    if PAYMENT_PROVIDER not in ('manual','both'): return web.json_response({'ok':False,'error':'Manual payment is disabled.'},status=403)
    uid=user_id(request)
    if not uid: return web.json_response({'ok':False,'error':'User cookie missing; reload and try again.'},status=400)
    reader=await request.multipart(); fields={}; proof=None; proof_name=''
    async for part in reader:
        if part.name=='proof':
            proof_name=os.path.basename(part.filename or 'proof.jpg'); ext=Path(proof_name).suffix.lower()
            if ext not in {'.jpg','.jpeg','.png','.webp'}: return web.json_response({'ok':False,'error':'Proof must be JPG, PNG or WEBP.'},status=400)
            raw=await part.read(decode=False)
            if len(raw)>5*1024*1024: return web.json_response({'ok':False,'error':'Proof image must be 5MB or smaller.'},status=400)
            proof=(raw,ext)
        else: fields[part.name]=(await part.text()).strip()
    plan_id=fields.get('plan_id',''); plan=PREMIUM_PLANS.get(plan_id)
    if not plan or not proof: return web.json_response({'ok':False,'error':'Select a plan and upload payment screenshot.'},status=400)
    await _ready(); req_id=secrets.token_urlsafe(12)
    await premium_manual.insert_one({'request_id':req_id,'user_id':uid,'plan_id':plan_id,'plan_name':plan['name'],'amount':plan['price_inr'],'proof':proof[0],'proof_ext':proof[1],'proof_name':proof_name,'note':fields.get('note',''),'created_at':int(time.time()),'status':'pending'})
    return web.json_response({'ok':True,'request_id':req_id,'status':'pending','message':'Payment proof sent for admin approval.'})

async def my_manual(request):
    uid=user_id(request); await _ready(); rows=[]
    if premium_manual is not None:
        async for v in premium_manual.find({'user_id':uid},{'proof':0}).sort('created_at',-1).limit(10):
            v.pop('_id',None); v['id']=v.pop('request_id',''); rows.append(v)
    return web.json_response({'ok':True,'requests':rows})

def _admin_ok(request):
    from .auth import validate_admin_session
    return validate_admin_session(request.cookies.get('admin_session',''))

async def admin_manual_list(request):
    if not _admin_ok(request): raise web.HTTPUnauthorized(text='Admin login required')
    await _ready(); rows=[]
    async for v in premium_manual.find({'status':'pending'},{'proof':0}).sort('created_at',1):
        v.pop('_id',None); v['id']=v.pop('request_id',''); rows.append(v)
    return web.json_response({'ok':True,'requests':rows})

async def admin_manual_proof(request):
    if not _admin_ok(request): raise web.HTTPUnauthorized(text='Admin login required')
    rid=request.match_info['request_id']; await _ready(); r=await premium_manual.find_one({'request_id':rid},{'proof':1,'proof_ext':1})
    if not r or not r.get('proof'): raise web.HTTPNotFound()
    mime={'.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png','.webp':'image/webp'}.get(r.get('proof_ext'),'application/octet-stream')
    return web.Response(body=bytes(r['proof']),content_type=mime)

async def admin_manual_decide(request):
    if not _admin_ok(request): raise web.HTTPUnauthorized(text='Admin login required')
    body=await request.json(); rid=str(body.get('request_id','')); action=str(body.get('action','')).lower(); await _ready(); r=await premium_manual.find_one({'request_id':rid})
    if not r: return web.json_response({'ok':False,'error':'Request not found'},status=404)
    if r.get('status')!='pending': return web.json_response({'ok':False,'error':'Request already processed'},status=400)
    if action=='approve':
        exp=await _activate(r['user_id'],r['plan_id'],'manual',rid)
        await premium_manual.update_one({'request_id':rid},{'$set':{'status':'approved','approved_at':int(time.time()),'expires_at':exp},'$unset':{'proof':''}})
    elif action=='reject':
        await premium_manual.update_one({'request_id':rid},{'$set':{'status':'rejected','rejected_at':int(time.time())}})
    else: return web.json_response({'ok':False,'error':'Invalid action'},status=400)
    return web.json_response({'ok':True,'status':action})

async def admin_grant(request):
    if not _admin_ok(request): raise web.HTTPUnauthorized(text='Admin login required')
    body=await request.json(); uid=str(body.get('user_id','')).strip(); plan_id=str(body.get('plan_id','30day'))
    if not uid or plan_id not in PREMIUM_PLANS: return web.json_response({'ok':False,'error':'user_id and valid plan_id required'},status=400)
    exp=await _activate(uid,plan_id,'admin'); return web.json_response({'ok':True,'user_id':uid,'plan':plan_id,'expires_at':exp})
