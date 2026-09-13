import math, mimetypes, secrets, asyncio, base64, struct
from types import SimpleNamespace
from aiohttp import web
from pyrogram import Client, raw, utils
from pyrogram.errors import AuthBytesInvalid
from pyrogram.file_id import FileId, FileType, ThumbnailSource
from pyrogram.session import Session, Auth
from .config import API_ID, API_HASH, BOT_TOKEN, SESSION_NAME
from .database import iter_media

class Streamer:
    def __init__(self, client):
        self.client = client
        self.cache = {}
        self.lock = asyncio.Lock()

    async def properties(self, file_id):
        if file_id in self.cache:
            return self.cache[file_id]
        # The Auto Filter Bot does NOT store a normal Telegram file_id in _id.
        # It stores a canonical packed id in _id plus file_ref. Decode both forms.
        doc = None
        async for d in iter_media({"_id": file_id}, projection={
            "_id": 1, "file_id": 1, "file_ref": 1, "file_name": 1,
            "file_size": 1, "mime_type": 1
        }):
            doc = d
            break
        if doc is None:
            async for d in iter_media({"file_id": file_id}, projection={
                "_id": 1, "file_id": 1, "file_ref": 1, "file_name": 1,
                "file_size": 1, "mime_type": 1
            }):
                doc = d
                break
        if doc is None:
            raise web.HTTPNotFound(text="Media not found in Auto Filter Bot database")

        raw_id = str(doc.get("file_id") or doc.get("_id") or "")
        try:
            fid = FileId.decode(raw_id)
        except Exception:
            try:
                encoded = base64.urlsafe_b64decode(raw_id + "=" * (-len(raw_id) % 4))
                # Reverse the Auto Filter Bot's encode_file_id(): runs of zero
                # bytes are stored as 0,count and two sentinel bytes (22,4)
                # are appended before base64 encoding.
                unpacked = bytearray()
                i = 0
                while i < len(encoded):
                    b = encoded[i]
                    if b == 0:
                        if i + 1 >= len(encoded):
                            raise ValueError("truncated zero-run in stored file id")
                        count = encoded[i + 1]
                        if count == 0:
                            raise ValueError("invalid zero-run in stored file id")
                        unpacked.extend(b"\x00" * count)
                        i += 2
                    else:
                        unpacked.append(b)
                        i += 1
                if len(unpacked) < 26 or unpacked[-2:] != bytes([22, 4]):
                    raise ValueError("invalid Auto Filter Bot file id sentinel")
                packed = bytes(unpacked[:-2])
                file_type, dc_id, media_id, access_hash = struct.unpack("<iiqq", packed)
                file_ref = base64.urlsafe_b64decode(
                    str(doc.get("file_ref") or "") + "=" * (-len(str(doc.get("file_ref") or "")) % 4)
                )
                fid = SimpleNamespace(
                    file_type=FileType(file_type), dc_id=dc_id, media_id=media_id,
                    access_hash=access_hash, file_reference=file_ref,
                    thumbnail_size="", thumbnail_source=ThumbnailSource.THUMBNAIL,
                    chat_id=0, chat_access_hash=0, volume_id=0, local_id=0,
                    file_size=int(doc.get("file_size") or 0),
                    mime_type=doc.get("mime_type") or "",
                    file_name=doc.get("file_name") or "",
                )
            except Exception as e:
                raise web.HTTPBadRequest(text="Invalid stored Telegram media reference") from e

        # DB values are authoritative for the file metadata.
        fid.file_size = int(doc.get("file_size") or getattr(fid, "file_size", 0) or 0)
        fid.mime_type = doc.get("mime_type") or getattr(fid, "mime_type", "") or ""
        fid.file_name = doc.get("file_name") or getattr(fid, "file_name", "") or ""
        self.cache[file_id] = fid
        return fid

    async def media_session(self, fid):
        sessions = self.client.media_sessions
        if fid.dc_id in sessions:
            return sessions[fid.dc_id]
        if fid.dc_id != await self.client.storage.dc_id():
            session = Session(
                self.client, fid.dc_id,
                await Auth(self.client, fid.dc_id, await self.client.storage.test_mode()).create(),
                await self.client.storage.test_mode(), is_media=True
            )
            await session.start()
            for _ in range(6):
                exported = await self.client.invoke(raw.functions.auth.ExportAuthorization(dc_id=fid.dc_id))
                try:
                    await session.send(raw.functions.auth.ImportAuthorization(id=exported.id, bytes=exported.bytes))
                    break
                except AuthBytesInvalid:
                    continue
            else:
                await session.stop()
                raise AuthBytesInvalid
        else:
            session = Session(
                self.client, fid.dc_id, await self.client.storage.auth_key(),
                await self.client.storage.test_mode(), is_media=True
            )
            await session.start()
        sessions[fid.dc_id] = session
        return session

    @staticmethod
    def location(fid):
        if fid.file_type == FileType.CHAT_PHOTO:
            if fid.chat_id > 0:
                peer = raw.types.InputPeerUser(user_id=fid.chat_id, access_hash=fid.chat_access_hash)
            elif fid.chat_access_hash == 0:
                peer = raw.types.InputPeerChat(chat_id=-fid.chat_id)
            else:
                peer = raw.types.InputPeerChannel(
                    channel_id=utils.get_channel_id(fid.chat_id), access_hash=fid.chat_access_hash
                )
            return raw.types.InputPeerPhotoFileLocation(
                peer=peer, volume_id=fid.volume_id, local_id=fid.local_id,
                big=fid.thumbnail_source == ThumbnailSource.CHAT_PHOTO_BIG
            )
        if fid.file_type == FileType.PHOTO:
            return raw.types.InputPhotoFileLocation(
                id=fid.media_id, access_hash=fid.access_hash,
                file_reference=fid.file_reference, thumb_size=fid.thumbnail_size
            )
        return raw.types.InputDocumentFileLocation(
            id=fid.media_id, access_hash=fid.access_hash,
            file_reference=fid.file_reference, thumb_size=fid.thumbnail_size
        )

    async def stream(self, request, file_id, attachment=False):
        fid = await self.properties(file_id)
        size = int(fid.file_size)
        if size <= 0:
            raise web.HTTPInternalServerError(text="Stored media has no valid file size")
        rng = request.http_range
        start = int(rng.start) if rng and rng.start is not None else 0
        stop = int(rng.stop) if rng and rng.stop is not None else size
        if start < 0 or start >= size or stop <= start:
            raise web.HTTPRequestRangeNotSatisfiable(headers={"Content-Range": f"bytes */{size}"})
        stop = min(stop, size)
        chunk = 1024 * 1024
        offset = start - (start % chunk)
        first = start - offset
        last = (stop - 1) % chunk + 1
        count = math.ceil(stop / chunk) - math.floor(offset / chunk)
        session = await self.media_session(fid)
        location = self.location(fid)

        async def body():
            current = 1
            while current <= count:
                r = await session.send(raw.functions.upload.GetFile(location=location, offset=offset, limit=chunk))
                if not isinstance(r, raw.types.upload.File) or not r.bytes:
                    break
                data = r.bytes
                if count == 1:
                    yield data[first:last]
                elif current == 1:
                    yield data[first:]
                elif current == count:
                    yield data[:last]
                else:
                    yield data
                current += 1
                offset += chunk

        mime = fid.mime_type or mimetypes.guess_type(fid.file_name or "")[0] or "video/mp4"
        headers = {
            "Content-Type": mime,
            "Content-Range": f"bytes {start}-{stop-1}/{size}",
            "Content-Length": str(stop-start),
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, max-age=30",
            "Content-Disposition": f'{"attachment" if attachment else "inline"}; filename="{fid.file_name or secrets.token_hex(4)}"',
        }
        return web.Response(status=206 if request.headers.get("Range") else 200, body=body(), headers=headers)

async def create_client():
    c = Client(
        SESSION_NAME, api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN,
        no_updates=True, in_memory=True
    )
    await c.start()
    return c
