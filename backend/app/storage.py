"""Where the bytes of media-library files live. Postgres only ever holds metadata.

Two interchangeable backends behind one small interface, chosen per asset (so switching STORAGE_BACKEND later never strands old files):
  local      files on a Docker volume (ASSETS_DIR, default /data/assets), one folder per workspace, names are random ids (never user input)
  cloudinary Cloudinary REST API with signed uploads (CLOUDINARY_URL=cloudinary://key:secret@cloud), resource type 'private' so nothing is
             publicly fetchable: we download through a signed API URL and stream it to the already-authenticated customer.
The Cloudinary secret is parsed here and never logged or returned."""
import asyncio
import errno
import hashlib
import re
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from . import bytecache
from .config import settings

CHUNK = 64 * 1024
LOCAL_KEY = re.compile(r"^[0-9a-f-]{36}/[0-9a-f]{32}\.(jpg|png|webp|pdf|mp4|mov)$")


class StorageError(Exception):
    """Storage failed; `status` is the HTTP status the API should answer with (502 provider trouble, 507 out of space/quota)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class Storage:
    name = ""

    async def put(self, tenant_id: str, data: bytes, ext: str, kind: str) -> str:
        raise NotImplementedError

    async def get(self, key: str) -> bytes:
        raise NotImplementedError

    async def stream(self, key: str) -> AsyncIterator[bytes]:
        data = await self.get(key)
        for i in range(0, len(data), CHUNK):
            yield data[i:i + CHUNK]

    async def delete(self, key: str) -> None:
        raise NotImplementedError

    async def sweep_orphans(self, known_keys: set[str], min_age_seconds: int = 3600) -> int:
        return 0


# ------------------------------------------------------------------ local volume
class LocalStorage(Storage):
    name = "local"

    def __init__(self, root: str) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        if not LOCAL_KEY.match(key):
            raise StorageError(502, "Invalid storage key")
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents:
            raise StorageError(502, "Invalid storage key")
        return p

    async def put(self, tenant_id: str, data: bytes, ext: str, kind: str) -> str:
        key = f"{tenant_id}/{uuid.uuid4().hex}.{ext}"
        path = self._path(key)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".part")
            tmp.write_bytes(data)
            tmp.replace(path)

        try:
            await asyncio.to_thread(write)
        except OSError as e:
            raise StorageError(507 if e.errno in (errno.ENOSPC, errno.EDQUOT) else 502, "The server's storage is full" if e.errno in (errno.ENOSPC, errno.EDQUOT) else "Could not store the file")
        return key

    async def get(self, key: str) -> bytes:
        try:
            return await asyncio.to_thread(self._path(key).read_bytes)
        except FileNotFoundError:
            raise StorageError(404, "The stored file is missing")
        except OSError:
            raise StorageError(502, "Could not read the stored file")

    async def stream(self, key: str) -> AsyncIterator[bytes]:
        path = self._path(key)
        try:
            f = await asyncio.to_thread(path.open, "rb")
        except FileNotFoundError:
            raise StorageError(404, "The stored file is missing")
        try:
            while True:
                chunk = await asyncio.to_thread(f.read, CHUNK)
                if not chunk:
                    break
                yield chunk
        finally:
            f.close()

    async def delete(self, key: str) -> None:
        bytecache.cache.invalidate(bytecache.make_key(self.name, key))  # never serve a deleted file from memory
        try:
            await asyncio.to_thread(self._path(key).unlink, True)
        except OSError:
            pass  # nothing to remove, or the volume is read-only: the orphan sweep retries

    async def sweep_orphans(self, known_keys: set[str], min_age_seconds: int = 3600) -> int:
        """Delete files nothing in the database refers to (failed uploads, deleted workspaces). Files younger than min_age are left alone."""
        def run() -> int:
            n = 0
            if not self.root.exists():
                return 0
            cutoff = time.time() - min_age_seconds
            for f in self.root.glob("*/*"):
                key = f"{f.parent.name}/{f.name}"
                try:
                    if key in known_keys or f.stat().st_mtime > cutoff:
                        continue
                    f.unlink()
                    n += 1
                except OSError:
                    continue
            return n

        return await asyncio.to_thread(run)


# ------------------------------------------------------------------ Cloudinary
def parse_cloudinary_url(url: str) -> tuple[str, str, str]:
    """cloudinary://<api_key>:<api_secret>@<cloud_name>  ->  (cloud_name, api_key, api_secret)"""
    u = urlsplit(url)
    if u.scheme != "cloudinary" or not (u.hostname and u.username and u.password):
        raise StorageError(502, "CLOUDINARY_URL is not valid (expected cloudinary://key:secret@cloud_name)")
    return u.hostname, u.username, u.password


def cloudinary_signature(params: dict[str, Any], api_secret: str) -> str:
    """Cloudinary's request signature: sha1 of the sorted 'k=v' pairs (without file, cloud_name, resource_type, api_key) joined by '&', plus the secret."""
    skip = {"file", "cloud_name", "resource_type", "api_key"}
    to_sign = "&".join(f"{k}={params[k]}" for k in sorted(params) if k not in skip and params[k] not in (None, ""))
    return hashlib.sha1((to_sign + api_secret).encode()).hexdigest()


class CloudinaryStorage(Storage):
    name = "cloudinary"

    def __init__(self, url: str, api_base: str) -> None:
        self.cloud, self.key, self.secret = parse_cloudinary_url(url)
        self.base = f"{api_base.rstrip('/')}/{self.cloud}"

    @staticmethod
    def _resource_type(kind: str) -> str:
        return {"image": "image", "video": "video"}.get(kind, "raw")

    @staticmethod
    def _split(key: str) -> tuple[str, str, str]:
        rtype, _, rest = key.partition("|")
        public_id, _, fmt = rest.rpartition("|")
        return rtype, public_id, fmt

    def _fail(self, r: httpx.Response) -> StorageError:
        try:
            msg = str((r.json().get("error") or {}).get("message") or "")
        except ValueError:
            msg = ""
        low = msg.lower()
        if r.status_code in (420, 429) or "quota" in low or "limit" in low and "exceed" in low:
            return StorageError(507, "The media storage provider is out of quota right now. Try again later or contact support")
        if r.status_code in (401, 403):
            return StorageError(502, "The media storage provider rejected our credentials")
        return StorageError(502, f"The media storage provider returned an error ({r.status_code})")

    async def put(self, tenant_id: str, data: bytes, ext: str, kind: str) -> str:
        rtype = self._resource_type(kind)
        stem = f"connectflow/{tenant_id}/{uuid.uuid4().hex}"
        public_id = f"{stem}.{ext}" if rtype == "raw" else stem   # raw assets carry their extension in the public id
        params = {"public_id": public_id, "timestamp": str(int(time.time())), "type": "private"}
        form = {**params, "api_key": self.key, "signature": cloudinary_signature(params, self.secret)}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10)) as c:
                r = await c.post(f"{self.base}/{rtype}/upload", data=form, files={"file": ("upload", data)})
        except httpx.HTTPError:
            raise StorageError(502, "Could not reach the media storage provider")
        if r.status_code >= 400:
            raise self._fail(r)
        try:
            j = r.json()
            return f"{rtype}|{j['public_id']}|{'' if rtype == 'raw' else (j.get('format') or ext)}"
        except (ValueError, KeyError):
            raise StorageError(502, "The media storage provider sent an unexpected answer")

    def _download_url(self, key: str) -> str:
        rtype, public_id, fmt = self._split(key)
        params: dict[str, Any] = {"public_id": public_id, "timestamp": str(int(time.time())), "type": "private"}
        if fmt:
            params["format"] = fmt
        q = {**params, "api_key": self.key, "signature": cloudinary_signature(params, self.secret)}
        from urllib.parse import urlencode
        return f"{self.base}/{rtype}/download?{urlencode(q)}"

    async def get(self, key: str) -> bytes:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10)) as c:
                r = await c.get(self._download_url(key))
        except httpx.HTTPError:
            raise StorageError(502, "Could not reach the media storage provider")
        if r.status_code == 404:
            raise StorageError(404, "The stored file is missing")
        if r.status_code >= 400:
            raise self._fail(r)
        return r.content

    async def delete(self, key: str) -> None:
        bytecache.cache.invalidate(bytecache.make_key(self.name, key))
        rtype, public_id, _ = self._split(key)
        params = {"public_id": public_id, "timestamp": str(int(time.time())), "type": "private", "invalidate": "true"}
        form = {**params, "api_key": self.key, "signature": cloudinary_signature(params, self.secret)}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10)) as c:
                r = await c.post(f"{self.base}/{rtype}/destroy", data=form)
        except httpx.HTTPError:
            raise StorageError(502, "Could not reach the media storage provider")
        if r.status_code >= 400:
            raise self._fail(r)


# ------------------------------------------------------------------ factory
_cache: dict[str, Storage] = {}


def get_storage(backend: str | None = None) -> Storage:
    """The backend an asset was stored with (or the configured one for new uploads)."""
    name = backend or settings.storage_backend
    if name not in _cache:
        if name == "cloudinary":
            _cache[name] = CloudinaryStorage(settings.cloudinary_url, settings.cloudinary_api_base)
        elif name == "local":
            _cache[name] = LocalStorage(settings.assets_dir)
        else:
            raise StorageError(502, f"Unknown storage backend '{name}'")
    return _cache[name]


def reset_cache() -> None:
    _cache.clear()
