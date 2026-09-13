from pathlib import Path
import time
from aiohttp import web, ClientSession
from .database import iter_media, get_setting, set_setting
from .parser import normalize
from .auth import make_stream_token, validate_stream_token, make_admin_session, validate_admin_session, verify_admin_password
from .stream import create_client, Streamer
from .config import HOST,PORT,TMDB_API_KEY,ADMIN_USERNAME,ADMIN_PASSWORD_HASH,CATALOG_TTL

BASE=Path(__file__).resolve().parent.parent
META_CACHE={}
CATALOG_CACHE={"at":0,"items":[]}

async def all_titles(force=False):
    now=time.time()
    if not force and CATALOG_CACHE["items"] and now-CATALOG_CACHE["at"] < CATALOG_TTL:
        return CATALOG_CACHE["items"]
    docs=[]
    projection={"_id":1,"file_id":1,"file_name":1,"file_size":1,"mime_type":1,"caption":1}
    async for d in iter_media(projection=projection): docs.append(d)
    items=normalize(docs)
    CATALOG_CACHE.update(at=now,items=items)
    return items

async def tmdb_meta(title,kind):
    if not TMDB_API_KEY:return {}
    key=(kind,title.casefold())
    cached=META_CACHE.get(key)
    if cached and time.time()-cached[0] < 86400:return cached[1]
    endpoint="tv" if kind=="series" else "movie"
    url=f"https://api.themoviedb.org/3/search/{endpoint}"
    try:
        async with ClientSession() as s:
            async with s.get(url,params={"api_key":TMDB_API_KEY,"query":title,"include_adult":"false"},timeout=8) as r:
                data=await r.json()
        result=(data.get("results") or [None])[0]
        if not result:return {}
        date=result.get("first_air_date") or result.get("release_date") or ""
        out={"poster":f"https://image.tmdb.org/t/p/w500{result['poster_path']}" if result.get('poster_path') else None,"backdrop":f"https://image.tmdb.org/t/p/w1280{result['backdrop_path']}" if result.get('backdrop_path') else None,"description":result.get('overview'),"year":int(date[:4]) if date[:4].isdigit() else None,"rating":result.get('vote_average')}
        META_CACHE[key]=(time.time(),out); return out
    except Exception:return {}

async def enrich(t):
    m=await tmdb_meta(t["title"],t["type"])
    for k,v in m.items():
        if v is not None:t[k]=v
    return t

async def home(request):
    items=await all_titles(); items=[await enrich(x) for x in items]
    return web.json_response({"items":items[:100],"count":len(items)})

async def search(request):
    q=request.query.get("q","").strip().casefold()
    if not q:return web.json_response({"items":[]})
    terms=q.split(); items=[]
    for t in await all_titles():
        if all(x in t["title"].casefold() for x in terms):items.append(await enrich(t))
    return web.json_response({"items":items[:100],"count":len(items)})

async def title(request):
    tid=request.match_info["id"]
    for t in await all_titles():
        if t["id"]==tid:return web.json_response(await enrich(t))
    raise web.HTTPNotFound(text="Title not found")

async def token(request):
    fid=request.match_info["file_id"]
    async for d in iter_media({"_id":fid},projection={"_id":1}):return web.json_response({"token":make_stream_token(fid)})
    async for d in iter_media({"file_id":fid},projection={"_id":1}):return web.json_response({"token":make_stream_token(fid)})
    raise web.HTTPNotFound(text="Media not found")

async def stream(request):
    fid=request.match_info["file_id"]; tok=request.query.get("token","")
    if not validate_stream_token(tok,fid):raise web.HTTPForbidden(text="Invalid or expired stream token")
    return await request.app["streamer"].stream(request,fid)

async def download(request):
    fid=request.match_info["file_id"]; tok=request.query.get("token","")
    if not validate_stream_token(tok,fid):raise web.HTTPForbidden(text="Invalid or expired download token")
    return await request.app["streamer"].stream(request,fid,attachment=True)

async def health(request):return web.json_response({"ok":True})

def is_admin(request):return validate_admin_session(request.cookies.get("admin_session",""))

def require_admin(request):
    if not is_admin(request):raise web.HTTPUnauthorized(text="Admin login required")

async def admin_login(request):
    if request.method=="GET":
        return web.Response(text=ADMIN_HTML,content_type="text/html")
    data=await request.post()
    if data.get("username")!=ADMIN_USERNAME or not ADMIN_PASSWORD_HASH or not verify_admin_password(data.get("password", ""),ADMIN_PASSWORD_HASH):
        raise web.HTTPUnauthorized(text="Invalid admin credentials")
    resp=web.json_response({"ok":True})
    resp.set_cookie("admin_session",make_admin_session(),httponly=True,secure=True,samesite="Strict",max_age=43200,path="/")
    return resp

async def admin_logout(request):
    require_admin(request); resp=web.json_response({"ok":True});resp.del_cookie("admin_session",path="/");return resp

async def admin_status(request):
    require_admin(request)
    items=await all_titles()
    return web.json_response({"authenticated":True,"maintenance":bool(await get_setting("maintenance",False)),"titles":len(items),"movies":sum(x["type"]=="movie" for x in items),"series":sum(x["type"]=="series" for x in items),"catalog_cache_age":round(time.time()-CATALOG_CACHE["at"],1) if CATALOG_CACHE["at"] else None})

async def admin_toggle_maintenance(request):
    require_admin(request)
    data=await request.json(); value=bool(data.get("maintenance"));await set_setting("maintenance",value)
    return web.json_response({"ok":True,"maintenance":value})

async def admin_refresh(request):
    require_admin(request); META_CACHE.clear(); items=await all_titles(force=True)
    return web.json_response({"ok":True,"count":len(items)})

async def maintenance_middleware(app,handler):
    async def middleware(request):
        if request.path.startswith("/admin") or request.path=="/health":return await handler(request)
        if await get_setting("maintenance",False):
            if request.path.startswith("/api/"):return web.json_response({"maintenance":True,"message":"Website is temporarily under maintenance."},status=503)
            return web.Response(text=MAINTENANCE_HTML,content_type="text/html",status=503)
        return await handler(request)
    return middleware

async def startup(app):app["tg"]=await create_client();app["streamer"]=Streamer(app["tg"])
async def cleanup(app):await app["tg"].stop()

def create_app():
    app=web.Application(client_max_size=1024*1024,middlewares=[maintenance_middleware])
    app.router.add_get("/health",health)
    app.router.add_get("/api/home",home);app.router.add_get("/api/search",search);app.router.add_get("/api/title/{id}",title);app.router.add_get("/api/stream-token/{file_id}",token);app.router.add_get("/api/stream/{file_id}",stream);app.router.add_get("/api/download/{file_id}",download)
    app.router.add_get("/admin",admin_login);app.router.add_post("/admin/login",admin_login);app.router.add_post("/admin/logout",admin_logout);app.router.add_get("/admin/api/status",admin_status);app.router.add_post("/admin/api/maintenance",admin_toggle_maintenance);app.router.add_post("/admin/api/refresh",admin_refresh)
    async def frontend_index(request):
        return web.FileResponse(BASE/"frontend"/"index.html")
    app.router.add_get("/",frontend_index)
    app.router.add_static("/",BASE/"frontend",show_index=False)
    app.router.add_static("/admin/",BASE/"admin",show_index=True)
    app.on_startup.append(startup);app.on_cleanup.append(cleanup);return app

ADMIN_HTML='''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Admin Login</title><style>body{margin:0;background:#080808;color:#fff;font:16px system-ui;display:grid;place-items:center;min-height:100vh}.box{width:min(380px,90vw);padding:28px;background:#151515;border-radius:18px}input,button{width:100%;box-sizing:border-box;padding:13px;margin-top:10px;border-radius:10px;border:1px solid #333;background:#0d0d0d;color:#fff}button{background:#fff;color:#000;font-weight:700;cursor:pointer}#msg{margin-top:12px;color:#f88}</style></head><body><div class="box"><h1>Website Admin</h1><form id="f"><input name="username" placeholder="Username" required><input name="password" type="password" placeholder="Password" required><button>Sign in</button></form><div id="msg"></div></div><script>f.onsubmit=async e=>{e.preventDefault();let r=await fetch('/admin/login',{method:'POST',body:new FormData(f)});if(r.ok)location.href='/admin/';else msg.textContent='Invalid credentials';}</script></body></html>'''
MAINTENANCE_HTML='''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Maintenance</title><style>body{margin:0;background:#080808;color:#fff;display:grid;place-items:center;min-height:100vh;font:18px system-ui;text-align:center}main{padding:30px}h1{font-size:42px}</style></head><body><main><div style="font-size:60px">🛠️</div><h1>We'll be back soon</h1><p>This website is temporarily under maintenance.</p></main></body></html>'''

if __name__=="__main__":web.run_app(create_app(),host=HOST,port=PORT)
