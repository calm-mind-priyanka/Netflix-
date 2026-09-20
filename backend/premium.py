import hashlib, hmac, json, os, secrets, time
from pathlib import Path
from aiohttp import web
from .config import (
    PREMIUM_PLANS, PAYMENT_PROVIDER, RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET,
    RAZORPAY_WEBHOOK_SECRET, MANUAL_PAYMENT_INSTRUCTIONS, MANUAL_PAYMENT_QR,
    PREMIUM_PROOF_DIR
)

async def _load():
    p=Path(os.getenv('PREMIUM_STORE_FILE','/tmp/streambox_premium.json'))
    try: return json.loads(p.read_text())
    except Exception: return {'users':{},'orders':{},'manual_requests':{}}
async def _save(data):
    p=Path(os.getenv('PREMIUM_STORE_FILE','/tmp/streambox_premium.json')); p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_suffix('.tmp'); tmp.write_text(json.dumps(data,indent=2)); os.replace(tmp,p)

def user_id(request): return request.cookies.get('sb_user') or ''
def ensure_user(response):
    uid=secrets.token_urlsafe(18); response.set_cookie('sb_user',uid,httponly=True,samesite='Lax',secure=True,max_age=31536000,path='/'); return uid

def plan_list(): return [{'id':k,'name':v['name'],'days':v['days'],'price_inr':v['price_inr']} for k,v in PREMIUM_PLANS.items()]
async def get_status(uid):
    if not uid: return {'plan':'free','premium':False,'expires_at':0}
    d=await _load(); u=d['users'].get(uid,{})
    exp=int(u.get('expires_at',0) or 0); active=exp>int(time.time())
    return {'plan':u.get('plan','free') if active else 'free','premium':active,'expires_at':exp,'plan_name':u.get('plan_name','') if active else ''}

async def status(request):
    uid=user_id(request); resp=web.json_response({'ok':True,'user_id':uid,'provider':PAYMENT_PROVIDER,'plans':plan_list(),'manual_instructions':MANUAL_PAYMENT_INSTRUCTIONS,'manual_qr':MANUAL_PAYMENT_QR,**await get_status(uid)})
    if not uid: ensure_user(resp)
    return resp

async def create_order(request):
    body=await request.json(); plan_id=str(body.get('plan_id','30day')); plan=PREMIUM_PLANS.get(plan_id)
    if not plan: return web.json_response({'ok':False,'error':'Invalid premium plan'},status=400)
    if PAYMENT_PROVIDER!='razorpay' or not (RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET): return web.json_response({'ok':False,'error':'Automatic payment is not configured.'},status=503)
    try: import razorpay
    except ImportError: return web.json_response({'ok':False,'error':'Razorpay support is not installed.'},status=503)
    uid=user_id(request)
    if not uid: return web.json_response({'ok':False,'error':'User cookie missing; reload and try again.'},status=400)
    client=razorpay.Client(auth=(RAZORPAY_KEY_ID,RAZORPAY_KEY_SECRET))
    order=client.order.create({'amount':plan['price_inr']*100,'currency':'INR','receipt':f'sb_{uid[:20]}_{plan_id}_{int(time.time())}','notes':{'user_id':uid,'plan_id':plan_id,'days':plan['days']}})
    d=await _load(); d['orders'][order['id']]={'user_id':uid,'plan_id':plan_id,'amount':plan['price_inr']*100,'created_at':int(time.time()),'status':'created'}; await _save(d)
    return web.json_response({'ok':True,'provider':'razorpay','key_id':RAZORPAY_KEY_ID,'order_id':order['id'],'amount':plan['price_inr']*100,'currency':'INR','days':plan['days'],'plan_id':plan_id,'plan_name':plan['name']})

async def _activate(d,uid,plan_id,source,payment_id=''):
    plan=PREMIUM_PLANS[plan_id]; now=int(time.time()); old=int(d['users'].get(uid,{}).get('expires_at',0) or 0); exp=max(now,old)+plan['days']*86400
    d['users'][uid]={'plan':plan_id,'plan_name':plan['name'],'expires_at':exp,'source':source,'last_payment':payment_id}
    return exp

async def verify_payment(request):
    data=await request.json(); uid=user_id(request); oid=str(data.get('razorpay_order_id','')); pay=str(data.get('razorpay_payment_id','')); sig=str(data.get('razorpay_signature',''))
    if not uid or not oid or not pay or not sig: return web.json_response({'ok':False,'error':'Missing payment data'},status=400)
    expected=hmac.new(RAZORPAY_KEY_SECRET.encode(),f'{oid}|{pay}'.encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected,sig): return web.json_response({'ok':False,'error':'Invalid payment signature'},status=400)
    d=await _load(); order=d['orders'].get(oid); plan_id=order.get('plan_id') if order else None
    if not order or order.get('user_id')!=uid or plan_id not in PREMIUM_PLANS: return web.json_response({'ok':False,'error':'Unknown order'},status=400)
    exp=await _activate(d,uid,plan_id,'razorpay',pay); order['status']='paid'; await _save(d)
    return web.json_response({'ok':True,'plan':plan_id,'expires_at':exp})

async def webhook(request):
    raw=await request.read(); sig=request.headers.get('X-Razorpay-Signature','')
    if not RAZORPAY_WEBHOOK_SECRET or not hmac.compare_digest(hmac.new(RAZORPAY_WEBHOOK_SECRET.encode(),raw,hashlib.sha256).hexdigest(),sig): return web.Response(status=401)
    try: data=json.loads(raw); entity=data.get('payload',{}).get('payment',{}).get('entity',{}); oid=entity.get('order_id')
    except Exception: return web.Response(status=400)
    if not oid: return web.Response(status=200)
    d=await _load(); order=d['orders'].get(oid)
    if order and order.get('status')!='paid' and order.get('plan_id') in PREMIUM_PLANS:
        exp=await _activate(d,order['user_id'],order['plan_id'],'razorpay_webhook',entity.get('id')); order['status']='paid'; await _save(d)
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
    req_id=secrets.token_urlsafe(12); p=Path(PREMIUM_PROOF_DIR); p.mkdir(parents=True,exist_ok=True); path=p/f'{req_id}{proof[1]}'; path.write_bytes(proof[0])
    d=await _load(); d.setdefault('manual_requests',{})[req_id]={'user_id':uid,'plan_id':plan_id,'plan_name':plan['name'],'amount':plan['price_inr'],'proof_path':str(path),'proof_name':proof_name,'note':fields.get('note',''),'created_at':int(time.time()),'status':'pending'}; await _save(d)
    return web.json_response({'ok':True,'request_id':req_id,'status':'pending','message':'Payment proof sent for admin approval.'})

async def my_manual(request):
    uid=user_id(request); d=await _load(); rows=[{'id':k,**v} for k,v in d.get('manual_requests',{}).items() if v.get('user_id')==uid]; rows.sort(key=lambda x:x.get('created_at',0),reverse=True); return web.json_response({'ok':True,'requests':rows[:10]})

async def admin_manual_list(request):
    from .auth import validate_admin_session
    if not validate_admin_session(request.cookies.get('admin_session','')): raise web.HTTPUnauthorized(text='Admin login required')
    d=await _load(); rows=[{'id':k,**{kk:vv for kk,vv in v.items() if kk!='proof_path'} } for k,v in d.get('manual_requests',{}).items() if v.get('status')=='pending']; rows.sort(key=lambda x:x.get('created_at',0)); return web.json_response({'ok':True,'requests':rows})

async def admin_manual_proof(request):
    from .auth import validate_admin_session
    if not validate_admin_session(request.cookies.get('admin_session','')): raise web.HTTPUnauthorized(text='Admin login required')
    rid=request.match_info['request_id']; d=await _load(); r=d.get('manual_requests',{}).get(rid)
    if not r: raise web.HTTPNotFound()
    p=Path(r.get('proof_path',''))
    if not p.exists(): raise web.HTTPNotFound()
    return web.FileResponse(p)

async def admin_manual_decide(request):
    from .auth import validate_admin_session
    if not validate_admin_session(request.cookies.get('admin_session','')): raise web.HTTPUnauthorized(text='Admin login required')
    body=await request.json(); rid=str(body.get('request_id','')); action=str(body.get('action','')).lower(); d=await _load(); r=d.get('manual_requests',{}).get(rid)
    if not r: return web.json_response({'ok':False,'error':'Request not found'},status=404)
    if r.get('status')!='pending': return web.json_response({'ok':False,'error':'Request already processed'},status=400)
    if action=='approve':
        exp=await _activate(d,r['user_id'],r['plan_id'],'manual'); r['status']='approved'; r['approved_at']=int(time.time()); r['expires_at']=exp
    elif action=='reject': r['status']='rejected'; r['rejected_at']=int(time.time())
    else: return web.json_response({'ok':False,'error':'Invalid action'},status=400)
    await _save(d); return web.json_response({'ok':True,'status':r['status']})

async def admin_grant(request):
    from .auth import validate_admin_session
    if not validate_admin_session(request.cookies.get('admin_session','')): raise web.HTTPUnauthorized(text='Admin login required')
    body=await request.json(); uid=str(body.get('user_id','')).strip(); plan_id=str(body.get('plan_id','30day'))
    if not uid or plan_id not in PREMIUM_PLANS: return web.json_response({'ok':False,'error':'user_id and valid plan_id required'},status=400)
    d=await _load(); exp=await _activate(d,uid,plan_id,'admin'); await _save(d); return web.json_response({'ok':True,'user_id':uid,'plan':plan_id,'expires_at':exp})
