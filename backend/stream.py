import base64
import math
import mimetypes
import re
import struct
import asyncio
import logging
import os
import shutil
import json
from collections import OrderedDict
from types import SimpleNamespace

from aiohttp import web
from pyrogram import Client, raw, utils
from pyrogram.errors import AuthBytesInvalid
from pyrogram.file_id import FileId, FileType, ThumbnailSource
from pyrogram.session import Session, Auth

from .config import API_ID, API_HASH, BOT_TOKEN, SESSION_NAME, TRANSCODE_CONCURRENCY
from .database import find_media

LOGGER = logging.getLogger("streambox.stream")

CHUNK_SIZE = 1024 * 1024

def _decode_urlsafe(value):
    value = str(value or "")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

def _decode_canonical_file_id(value):
    """Decode the exact encode_file_id() format used by the Auto Filter Bot."""
    encoded = _decode_urlsafe(value)
    unpacked = bytearray()
    index = 0

    while index < len(encoded):
        byte = encoded[index]
        if byte == 0:
            if index + 1 >= len(encoded):
                raise ValueError("truncated zero-run")
            count = encoded[index + 1]
            if count == 0:
                raise ValueError("invalid zero-run")
            unpacked.extend(b"\x00" * count)
            index += 2
        else:
            unpacked.append(byte)
            index += 1

    if len(unpacked) < 26 or bytes(unpacked[-2:]) != bytes((22, 4)):
        raise ValueError("not an Auto Filter Bot canonical file id")

    packed = bytes(unpacked[:-2])
    if len(packed) != struct.calcsize("<iiqq"):
        raise ValueError("invalid canonical file id length")

    file_type, dc_id, media_id, access_hash = struct.unpack("<iiqq", packed)
    return file_type, dc_id, media_id, access_hash

def _build_properties(doc):
    canonical = str(doc.get("_id") or doc.get("file_id") or "")
    file_ref_text = str(doc.get("file_ref") or "")

    try:
        file_type, dc_id, media_id, access_hash = _decode_canonical_file_id(canonical)
        file_reference = _decode_urlsafe(file_ref_text) if file_ref_text else b""
        return SimpleNamespace(
            file_type=file_type,
            dc_id=dc_id,
            media_id=media_id,
            access_hash=access_hash,
            file_reference=file_reference,
            thumbnail_size="",
            thumbnail_source=None,
            chat_id=0,
            chat_access_hash=0,
            volume_id=0,
            local_id=0,
            file_size=int(doc.get("file_size") or 0),
            mime_type=str(doc.get("mime_type") or ""),
            file_name=str(doc.get("file_name") or ""),
        )
    except Exception:
        # Compatibility fallback for a record containing a raw Telegram
        # file_id. The original Auto Filter Bot stores canonical ids, so this
        # path is not used for normal records.
        decoded = FileId.decode(canonical)
        if file_ref_text:
            decoded.file_reference = _decode_urlsafe(file_ref_text)
        decoded.file_size = int(doc.get("file_size") or getattr(decoded, "file_size", 0) or 0)
        decoded.mime_type = str(doc.get("mime_type") or getattr(decoded, "mime_type", "") or "")
        decoded.file_name = str(doc.get("file_name") or getattr(decoded, "file_name", "") or "")
        return decoded

class Streamer:
    def __init__(self, client):
        self.client = client
        self.cache = OrderedDict()
        self.cache_max = 256
        self.transcode_slots = asyncio.Semaphore(TRANSCODE_CONCURRENCY)
        self.lock = asyncio.Lock()
        self.track_cache = OrderedDict()
        self.track_cache_max = 96
        self.track_slots = asyncio.Semaphore(1)

    async def properties(self, file_id):
        key = str(file_id)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]

        doc = await find_media(
            key,
            projection={
                "_id": 1,
                "file_ref": 1,
                "file_name": 1,
                "file_size": 1,
                "mime_type": 1,
                "file_type": 1,
            },
        )
        if doc is None:
            raise web.HTTPNotFound(text="Media not found in Auto Filter Bot database")

        try:
            properties = _build_properties(doc)
        except Exception as exc:
            LOGGER.exception("Could not decode Telegram media reference for file %s", key)
            raise web.HTTPBadRequest(
                text="The stored Telegram media reference could not be decoded"
            ) from exc

        self.cache[key] = properties
        self.cache.move_to_end(key)
        while len(self.cache) > self.cache_max:
            self.cache.popitem(last=False)
        return properties

    async def media_session(self, file_id):
        sessions = self.client.media_sessions
        if file_id.dc_id in sessions:
            return sessions[file_id.dc_id]

        if file_id.dc_id != await self.client.storage.dc_id():
            session = Session(
                self.client,
                file_id.dc_id,
                await Auth(
                    self.client,
                    file_id.dc_id,
                    await self.client.storage.test_mode(),
                ).create(),
                await self.client.storage.test_mode(),
                is_media=True,
            )
            await session.start()

            for _ in range(6):
                exported = await self.client.invoke(
                    raw.functions.auth.ExportAuthorization(dc_id=file_id.dc_id)
                )
                try:
                    await session.send(
                        raw.functions.auth.ImportAuthorization(
                            id=exported.id,
                            bytes=exported.bytes,
                        )
                    )
                    break
                except AuthBytesInvalid:
                    continue
            else:
                await session.stop()
                raise AuthBytesInvalid("Unable to import Telegram DC authorization")
        else:
            session = Session(
                self.client,
                file_id.dc_id,
                await self.client.storage.auth_key(),
                await self.client.storage.test_mode(),
                is_media=True,
            )
            await session.start()

        sessions[file_id.dc_id] = session
        return session

    @staticmethod
    def location(file_id):
        file_type = int(file_id.file_type)

        if file_type == int(FileType.CHAT_PHOTO):
            if file_id.chat_id > 0:
                peer = raw.types.InputPeerUser(
                    user_id=file_id.chat_id,
                    access_hash=file_id.chat_access_hash,
                )
            elif file_id.chat_access_hash == 0:
                peer = raw.types.InputPeerChat(chat_id=-file_id.chat_id)
            else:
                peer = raw.types.InputPeerChannel(
                    channel_id=utils.get_channel_id(file_id.chat_id),
                    access_hash=file_id.chat_access_hash,
                )
            return raw.types.InputPeerPhotoFileLocation(
                peer=peer,
                volume_id=file_id.volume_id,
                local_id=file_id.local_id,
                big=False,
            )

        if file_type == int(FileType.PHOTO):
            return raw.types.InputPhotoFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size,
            )

        return raw.types.InputDocumentFileLocation(
            id=file_id.media_id,
            access_hash=file_id.access_hash,
            file_reference=file_id.file_reference,
            thumb_size=file_id.thumbnail_size,
        )

    async def _telegram_chunks(self, properties, start=0):
        """Yield the real Telegram media bytes without downloading the whole file."""
        offset = max(0, int(start))
        if offset:
            offset = (offset // CHUNK_SIZE) * CHUNK_SIZE
        session = await self.media_session(properties)
        location = self.location(properties)

        while True:
            result = await session.send(
                raw.functions.upload.GetFile(
                    location=location,
                    offset=offset,
                    limit=CHUNK_SIZE,
                )
            )
            if not isinstance(result, raw.types.upload.File):
                raise RuntimeError(
                    f"Telegram returned unsupported media response: {type(result).__name__}"
                )
            data = result.bytes or b""
            if not data:
                break
            yield data
            offset += len(data)
            if len(data) < CHUNK_SIZE:
                break

    @staticmethod
    def _pretty_language(code, title=None):
        mapping = {
            "eng":"English", "hin":"Hindi", "tam":"Tamil", "tel":"Telugu",
            "mal":"Malayalam", "kan":"Kannada", "ben":"Bengali", "mar":"Marathi",
            "pun":"Punjabi", "guj":"Gujarati", "bho":"Bhojpuri", "urd":"Urdu",
            "kor":"Korean", "jpn":"Japanese", "spa":"Spanish", "fra":"French",
            "deu":"German", "ger":"German", "ita":"Italian", "por":"Portuguese",
            "rus":"Russian", "ara":"Arabic", "zho":"Chinese", "chi":"Chinese",
        }
        value = str(title or "").strip()
        if value:
            return value
        code = str(code or "").strip().lower()
        return mapping.get(code, code.upper() if code else "Unknown")

    async def probe_tracks(self, file_id):
        """Read actual embedded audio/subtitle track metadata on demand.

        Only a bounded prefix is sent to ffprobe. This keeps the Hobby instance
        from downloading an entire movie merely to discover its track list.
        Results are cached in-process and MongoDB is never modified.
        """
        key = str(file_id)
        cached = self.track_cache.get(key)
        if cached is not None:
            self.track_cache.move_to_end(key)
            return cached
        if shutil.which("ffprobe") is None:
            return {"audio_tracks": [], "subtitle_tracks": [], "available": False}

        properties = await self.properties(key)
        try:
            await asyncio.wait_for(self.track_slots.acquire(), timeout=5)
        except asyncio.TimeoutError:
            return {"audio_tracks": [], "subtitle_tracks": [], "available": False, "busy": True}

        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                "ffprobe", "-hide_banner", "-loglevel", "error",
                "-probesize", "16000000", "-analyzeduration", "5000000",
                "-show_entries", "stream=index,codec_type:stream_tags=language,title,handler_name",
                "-of", "json", "-i", "pipe:0",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            sent = 0
            max_probe = 16 * 1024 * 1024
            async for chunk in self._telegram_chunks(properties):
                if sent >= max_probe:
                    break
                chunk = chunk[:max_probe-sent]
                try:
                    process.stdin.write(chunk)
                    await process.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    return {"audio_tracks": [], "subtitle_tracks": [], "available": False}
                sent += len(chunk)
                # Containers normally expose stream headers very early. Give
                # ffprobe enough bytes for headers, but never consume a movie.
                if sent >= 8 * 1024 * 1024:
                    try:
                        if process.returncode is not None:
                            break
                    except Exception:
                        pass
            try:
                process.stdin.close()
            except Exception:
                pass
            try:
                raw = await asyncio.wait_for(process.stdout.read(), timeout=8)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return {"audio_tracks": [], "subtitle_tracks": [], "available": False}
            await process.wait()
            data = json.loads(raw.decode("utf-8", "replace") or "{}")
            audio_tracks, subtitle_tracks = [], []
            audio_no, subtitle_no = 0, 0
            for stream in data.get("streams") or []:
                typ = stream.get("codec_type")
                tags = stream.get("tags") or {}
                language = self._pretty_language(tags.get("language"), tags.get("title") or tags.get("handler_name"))
                item = {
                    "index": int(stream.get("index", -1)),
                    "language": language,
                    "title": str(tags.get("title") or tags.get("handler_name") or "").strip(),
                }
                if typ == "audio":
                    item["track"] = audio_no
                    audio_tracks.append(item)
                    audio_no += 1
                elif typ == "subtitle":
                    item["track"] = subtitle_no
                    subtitle_tracks.append(item)
                    subtitle_no += 1
            result = {"audio_tracks": audio_tracks, "subtitle_tracks": subtitle_tracks, "available": True}
            self.track_cache[key] = result
            self.track_cache.move_to_end(key)
            while len(self.track_cache) > self.track_cache_max:
                self.track_cache.popitem(last=False)
            return result
        except Exception:
            LOGGER.exception("Embedded track probe failed for file %s", key)
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            return {"audio_tracks": [], "subtitle_tracks": [], "available": False}
        finally:
            self.track_slots.release()

    async def subtitle(self, request, file_id):
        """Extract one embedded subtitle track as WebVTT on demand."""
        if self.client is None or shutil.which("ffmpeg") is None:
            raise web.HTTPServiceUnavailable(text="Subtitle extraction is not available")
        track = request.query.get("subtitle_track")
        if not str(track or "").isdigit():
            raise web.HTTPBadRequest(text="A valid subtitle track is required")
        token = request.query.get("token", "")
        input_url = f"http://127.0.0.1:{os.getenv('PORT', '8080')}/api/stream/{file_id}?token={token}"
        try:
            await asyncio.wait_for(self.track_slots.acquire(), timeout=5)
        except asyncio.TimeoutError as exc:
            raise web.HTTPServiceUnavailable(text="Subtitle extraction is busy. Please try again shortly.") from exc
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-i", input_url,
                "-map", f"0:s:{int(track)}?",
                "-c:s", "webvtt", "-f", "webvtt", "pipe:1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            data = await asyncio.wait_for(process.stdout.read(), timeout=45)
            code = await process.wait()
            if code != 0 or not data:
                raise web.HTTPBadRequest(text="This subtitle track could not be converted for the browser")
            return web.Response(
                body=data,
                content_type="text/vtt",
                headers={"Cache-Control": "private, max-age=300"},
            )
        except asyncio.TimeoutError as exc:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            raise web.HTTPGatewayTimeout(text="Subtitle extraction timed out") from exc
        finally:
            self.track_slots.release()

    async def transcode(self, request, file_id):
        """Transcode incompatible Telegram media to browser-friendly fragmented MP4.

        FFmpeg receives bytes directly from Telegram through a pipe. Only one
        compatibility conversion is allowed by default on small instances,
        preventing concurrent CPU-heavy encodes from taking down the web process.
        """
        if self.client is None:
            raise web.HTTPServiceUnavailable(text="Telegram streaming is not configured")
        if shutil.which("ffmpeg") is None:
            raise web.HTTPServiceUnavailable(text="FFmpeg is not installed on the streaming server")

        properties = await self.properties(file_id)
        try:
            await asyncio.wait_for(self.transcode_slots.acquire(), timeout=5)
        except asyncio.TimeoutError as exc:
            raise web.HTTPServiceUnavailable(
                text="A compatibility stream is already running. Please try again shortly."
            ) from exc

        process = None
        feeder = None
        try:
            audio_track = request.query.get("audio_track")
            start = max(0.0, float(request.query.get("start", "0") or 0))
            token = request.query.get("token", "")
            input_url = f"http://127.0.0.1:{os.getenv('PORT', '8080')}/api/stream/{file_id}?token={token}"
            map_audio = "0:a:0?"
            if audio_track is not None and str(audio_track).isdigit():
                map_audio = f"0:a:{int(audio_track)}?"
            args = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
            if start > 0:
                args += ["-ss", f"{start:.3f}"]
            args += ["-i", input_url,
                "-map", "0:v:0?", "-map", map_audio,
                "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1",
                "-pix_fmt", "yuv420p", "-profile:v", "main",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
                "-f", "mp4", "pipe:1"]
            process = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )

            response = web.StreamResponse(
                status=200,
                headers={
                    "Content-Type": "video/mp4",
                    "Accept-Ranges": "none",
                    "Cache-Control": "private, no-store",
                    "Content-Disposition": "inline; filename=\"stream.mp4\"",
                },
            )
            await response.prepare(request)

            while True:
                data = await process.stdout.read(CHUNK_SIZE)
                if not data:
                    break
                await response.write(data)
            code = await process.wait()
            if code != 0:
                raise RuntimeError(f"FFmpeg exited with status {code}")

            try:
                await response.write_eof()
            except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
                pass
            return response
        except (ConnectionResetError, BrokenPipeError):
            if feeder is not None:
                feeder.cancel()
            if process is not None and process.returncode is None:
                process.kill()
            if process is not None:
                await process.wait()
            return response if "response" in locals() else None
        except Exception:
            if feeder is not None and not feeder.done():
                feeder.cancel()
            if process is not None and process.returncode is None:
                process.kill()
            if process is not None:
                await process.wait()
            LOGGER.exception("Browser compatibility stream failed for file %s", file_id)
            raise
        finally:
            self.transcode_slots.release()

    @staticmethod
    def _range(request, size):
        header = request.headers.get("Range")
        if not header:
            return 0, size - 1, False

        match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
        if not match:
            raise web.HTTPRequestRangeNotSatisfiable(
                headers={"Content-Range": f"bytes */{size}"}
            )

        start_text, end_text = match.groups()
        if not start_text:
            length = int(end_text or 0)
            if length <= 0:
                raise web.HTTPRequestRangeNotSatisfiable(
                    headers={"Content-Range": f"bytes */{size}"}
                )
            start = max(0, size - length)
            end = size - 1
        else:
            start = int(start_text)
            end = int(end_text) if end_text else size - 1

        if start >= size or start < 0 or end < start:
            raise web.HTTPRequestRangeNotSatisfiable(
                headers={"Content-Range": f"bytes */{size}"}
            )

        return start, min(end, size - 1), True

    async def stream(self, request, file_id, attachment=False):
        if self.client is None:
            raise web.HTTPServiceUnavailable(text="Telegram streaming is not configured")

        properties = await self.properties(file_id)
        size = int(properties.file_size)
        if size <= 0:
            raise web.HTTPInternalServerError(
                text="Stored media has no valid file size"
            )

        start, end, partial = self._range(request, size)
        total = end - start + 1

        # Telegram file offsets must be aligned to the chunk boundary used by
        # the downloader. The Auto Filter Bot uses the same 1 MiB strategy.
        offset = (start // CHUNK_SIZE) * CHUNK_SIZE
        first_cut = start - offset
        part_count = math.ceil((end + 1) / CHUNK_SIZE) - (offset // CHUNK_SIZE)

        session = await self.media_session(properties)
        location = self.location(properties)

        async def write_body(response):
            current_part = 1
            current_offset = offset
            remaining = total

            while current_part <= part_count and remaining > 0:
                result = await session.send(
                    raw.functions.upload.GetFile(
                        location=location,
                        offset=current_offset,
                        limit=CHUNK_SIZE,
                    )
                )

                if not isinstance(result, raw.types.upload.File):
                    raise RuntimeError(
                        f"Telegram returned unsupported media response: {type(result).__name__}"
                    )

                data = result.bytes or b""
                if not data:
                    break

                if current_part == 1:
                    data = data[first_cut:]

                data = data[:remaining]
                if not data:
                    break

                await response.write(data)
                remaining -= len(data)
                current_offset += CHUNK_SIZE
                current_part += 1

        mime = (
            properties.mime_type
            or mimetypes.guess_type(properties.file_name or "")[0]
            or "application/octet-stream"
        )

        safe_name = re.sub(r'[\r\n"]+', "_", properties.file_name or "stream")
        response = web.StreamResponse(
            status=206 if partial else 200,
            headers={
                "Content-Type": mime,
                "Content-Length": str(total),
                "Accept-Ranges": "bytes",
                "Cache-Control": "private, max-age=30",
                "Content-Disposition": (
                    f'{"attachment" if attachment else "inline"}; '
                    f'filename="{safe_name}"'
                ),
            },
        )
        if partial:
            response.headers["Content-Range"] = f"bytes {start}-{end}/{size}"

        await response.prepare(request)
        try:
            await write_body(response)
        except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
            return response
        except Exception:
            LOGGER.exception("Telegram streaming failed for file %s", file_id)
            if not response.prepared:
                raise
            # The response has already started, so the useful information is in
            # the server log rather than an invalid JSON response mid-stream.
        finally:
            try:
                await response.write_eof()
            except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
                pass

        return response

async def create_client():
    if not (BOT_TOKEN and API_ID and API_HASH):
        return None

    client = Client(
        SESSION_NAME,
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        no_updates=True,
        in_memory=True,
    )
    try:
        await client.start()
    except Exception:
        LOGGER.exception("Telegram client startup failed")
        try:
            await client.stop()
        except Exception:
            pass
        return None
    return client
