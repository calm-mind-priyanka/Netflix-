import math,mimetypes,secrets,asyncio
from aiohttp import web
from pyrogram import Client, raw, utils
from pyrogram.errors import AuthBytesInvalid
from pyrogram.file_id import FileId, FileType, ThumbnailSource
from pyrogram.session import Session, Auth
from .config import API_ID,API_HASH,BOT_TOKEN,SESSION_NAME

class Streamer:
    def __init__(self,client): self.client=client; self.cache={}; self.lock=asyncio.Lock()
    async def properties(self,file_id):
        if file_id in self.cache:return self.cache[file_id]
        try: fid=FileId.decode(file_id)
        except Exception as e: raise web.HTTPBadRequest(text="Invalid Telegram file reference") from e
        self.cache[file_id]=fid; return fid
    async def media_session(self,fid):
        sessions=self.client.media_sessions
        if fid.dc_id in sessions:return sessions[fid.dc_id]
        if fid.dc_id != await self.client.storage.dc_id():
            session=Session(self.client,fid.dc_id,await Auth(self.client,fid.dc_id,await self.client.storage.test_mode()).create(),await self.client.storage.test_mode(),is_media=True); await session.start()
            for _ in range(6):
                exported=await self.client.invoke(raw.functions.auth.ExportAuthorization(dc_id=fid.dc_id))
                try: await session.send(raw.functions.auth.ImportAuthorization(id=exported.id,bytes=exported.bytes)); break
                except AuthBytesInvalid: continue
            else: await session.stop(); raise AuthBytesInvalid
        else:
            session=Session(self.client,fid.dc_id,await self.client.storage.auth_key(),await self.client.storage.test_mode(),is_media=True); await session.start()
        sessions[fid.dc_id]=session; return session
    @staticmethod
    def location(fid):
        if fid.file_type==FileType.CHAT_PHOTO:
            if fid.chat_id>0: peer=raw.types.InputPeerUser(user_id=fid.chat_id,access_hash=fid.chat_access_hash)
            elif fid.chat_access_hash==0: peer=raw.types.InputPeerChat(chat_id=-fid.chat_id)
            else: peer=raw.types.InputPeerChannel(channel_id=utils.get_channel_id(fid.chat_id),access_hash=fid.chat_access_hash)
            return raw.types.InputPeerPhotoFileLocation(peer=peer,volume_id=fid.volume_id,local_id=fid.local_id,big=fid.thumbnail_source==ThumbnailSource.CHAT_PHOTO_BIG)
        if fid.file_type==FileType.PHOTO:return raw.types.InputPhotoFileLocation(id=fid.media_id,access_hash=fid.access_hash,file_reference=fid.file_reference,thumb_size=fid.thumbnail_size)
        return raw.types.InputDocumentFileLocation(id=fid.media_id,access_hash=fid.access_hash,file_reference=fid.file_reference,thumb_size=fid.thumbnail_size)
    async def stream(self,request,file_id,attachment=False):
        fid=await self.properties(file_id); size=int(fid.file_size); rng=request.http_range
        start=int(rng.start) if rng and rng.start is not None else 0; stop=int(rng.stop) if rng and rng.stop is not None else size
        if start<0 or start>=size or stop<=start: raise web.HTTPRequestRangeNotSatisfiable(headers={"Content-Range":f"bytes */{size}"})
        stop=min(stop,size); chunk=1024*1024; offset=start-(start%chunk); first=start-offset; last=(stop-1)%chunk+1; count=math.ceil(stop/chunk)-math.floor(offset/chunk)
        session=await self.media_session(fid); location=self.location(fid)
        async def body():
            current=1
            while current<=count:
                r=await session.send(raw.functions.upload.GetFile(location=location,offset=offset,limit=chunk))
                if not isinstance(r,raw.types.upload.File) or not r.bytes: break
                data=r.bytes
                if count==1: yield data[first:last]
                elif current==1: yield data[first:]
                elif current==count: yield data[:last]
                else: yield data
                current+=1; offset+=chunk
        mime=fid.mime_type or mimetypes.guess_type(fid.file_name or "")[0] or "video/mp4"
        headers={"Content-Type":mime,"Content-Range":f"bytes {start}-{stop-1}/{size}","Content-Length":str(stop-start),"Accept-Ranges":"bytes","Cache-Control":"private, max-age=30","Content-Disposition":f'{"attachment" if attachment else "inline"}; filename="{fid.file_name or secrets.token_hex(4)}"'}
        return web.Response(status=206 if request.headers.get("Range") else 200,body=body(),headers=headers)

async def create_client():
    c=Client(SESSION_NAME,api_id=API_ID,api_hash=API_HASH,bot_token=BOT_TOKEN,no_updates=True,in_memory=True); await c.start(); return c
