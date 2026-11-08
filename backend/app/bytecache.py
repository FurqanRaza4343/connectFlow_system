"""In-process LRU cache for media file bytes.

Why: every media message used to re-read its file from storage (a Cloudinary download or a disk read) and post it to the gateway. A campaign
sends the SAME few files hundreds of times, so we keep recently used files in memory: one storage read per file per hour of sends.

* Bounded by total bytes AND number of entries (least recently used goes first), so memory stays predictable (default 64 MB / 16 files).
* Entries expire after `ttl_s` (default 1 h): another process (the API) deleting an asset cannot leave a stale copy here for long.
* Thread-safe (a plain lock around short dictionary operations); safe to call from async code.
* Keyed by (storage backend, storage key). Storage keys are random and never reused, so a key always means the same bytes; deleting the file
  (`Storage.delete`) invalidates its key in the process that deletes it.
"""
import threading
import time
from collections import OrderedDict

MAX_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 16
TTL_S = 3600


class ByteCache:
    def __init__(self, max_bytes: int = MAX_BYTES, max_entries: int = MAX_ENTRIES, ttl_s: float = TTL_S) -> None:
        self.max_bytes, self.max_entries, self.ttl_s = max_bytes, max_entries, ttl_s
        self._d: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> bytes | None:
        now = time.monotonic()
        with self._lock:
            item = self._d.get(key)
            if item is None:
                self.misses += 1
                return None
            at, data = item
            if now - at > self.ttl_s:
                self._drop(key)
                self.misses += 1
                return None
            self._d.move_to_end(key)
            self.hits += 1
            return data

    def put(self, key: str, data: bytes) -> bool:
        """Store a copy of the reference. Returns False when the file alone is bigger than the whole cache (never cached)."""
        size = len(data)
        if size > self.max_bytes:
            return False
        with self._lock:
            if key in self._d:
                self._drop(key)
            while self._d and (self._bytes + size > self.max_bytes or len(self._d) >= self.max_entries):
                self._drop(next(iter(self._d)))  # least recently used
            self._d[key] = (time.monotonic(), data)
            self._bytes += size
            return True

    def _drop(self, key: str) -> None:
        item = self._d.pop(key, None)
        if item is not None:
            self._bytes -= len(item[1])

    def invalidate(self, key: str) -> bool:
        with self._lock:
            had = key in self._d
            self._drop(key)
            return had

    def clear(self) -> None:
        with self._lock:
            self._d.clear()
            self._bytes = 0

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"entries": len(self._d), "bytes": self._bytes, "hits": self.hits, "misses": self.misses}


cache = ByteCache()


def make_key(backend: str, storage_key: str) -> str:
    return f"{backend}:{storage_key}"
