"""Media rules as pure functions (no database, no network): upload validation, image re-encoding, the safe-sending limits for media,
tag-based asset selection and round-robin rotation. Everything that decides "may this be sent / stored" lives here so it can be unit-tested."""
import io
import re
from dataclasses import dataclass

# ------------------------------------------------------------------ limits (documented in docs/MEDIA.md)
MB = 1024 * 1024
MAX_BYTES = {"image": 5 * MB, "document": 10 * MB, "video": 16 * MB}
IMAGE_MAX_SIDE = 1600
THUMB_SIDE = 320
MAX_PIXELS = 40_000_000  # decompression-bomb guard, checked from the header BEFORE the image is decoded
MAX_VARIANTS = 5
MAX_TAGS = 10
UPLOADS_PER_HOUR = 30
FIRST_MESSAGE_MAX_ASSETS = 5   # candidates to rotate through (a first message always carries exactly ONE of them)
FOLLOWUP_MAX_ASSETS = 3
REPLY_MAX_ASSETS = 3
CAPTION_MAX = 1024

# safe-sending rules for media
MEDIA_MIN_AGE_DAYS = 3         # a number younger than this never sends media
MEDIA_WARMUP_SHARE_PCT = 30    # while warming up, media may be at most this share of the day's sends from that number
PART_GAP_SECONDS = (3.0, 10.0)  # randomized pause between the parts of a multi-part send
MEDIA_UPLOAD_CONCURRENCY = 3    # simultaneous uploads to the gateway per worker process

KIND_OF_MIME = {"image/jpeg": "image", "image/png": "image", "image/webp": "image", "application/pdf": "document", "video/mp4": "video", "video/quicktime": "video"}


class MediaError(ValueError):
    """A validation failure with the HTTP status it should become."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Detected:
    kind: str   # image | document | video
    mime: str
    ext: str


def detect(data: bytes) -> Detected | None:
    """What the BYTES are, never what the client claims."""
    if data.startswith(b"\xff\xd8\xff"):
        return Detected("image", "image/jpeg", "jpg")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return Detected("image", "image/png", "png")
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return Detected("image", "image/webp", "webp")
    if data.startswith(b"%PDF-"):
        return Detected("document", "application/pdf", "pdf")
    if data[4:8] == b"ftyp":
        return Detected("video", "video/quicktime", "mov") if data[8:12] == b"qt  " else Detected("video", "video/mp4", "mp4")
    return None


_REJECT = [
    (b"MZ", "Windows programs are not allowed"),
    (b"\x7fELF", "Executable files are not allowed"),
    (b"#!", "Scripts are not allowed"),
    (b"PK\x03\x04", "Archives and Office files are not allowed (upload a PDF instead)"),
    (b"\xca\xfe\xba\xbe", "Executable files are not allowed"),
    (b"\xcf\xfa\xed\xfe", "Executable files are not allowed"),
    (b"GIF8", "GIF is not supported (use JPEG, PNG or WebP)"),
]
_PDF_ACTIVE = re.compile(rb"/(JavaScript|JS|Launch|EmbeddedFile)\b")


def validate_upload(data: bytes, declared_mime: str = "") -> Detected:
    """Reject anything that is not a plain image / PDF / short video. Raises MediaError(415|413|422)."""
    if not data:
        raise MediaError(422, "The file is empty")
    head = data[:512].lstrip().lower()
    if head.startswith((b"<svg", b"<?xml", b"<!doctype", b"<html", b"<script")):
        raise MediaError(415, "SVG, HTML and XML files are not allowed (they can carry scripts). Upload a JPEG, PNG or WebP image instead")
    for magic, why in _REJECT:
        if data.startswith(magic):
            raise MediaError(415, why)
    found = detect(data)
    if not found:
        raise MediaError(415, "Unsupported file. Allowed: JPEG, PNG, WebP images, PDF documents and MP4/MOV videos")
    declared = (declared_mime or "").split(";")[0].strip().lower()
    if declared and declared not in ("application/octet-stream", "binary/octet-stream"):
        family = declared.split("/")[0]
        mismatch = (declared == "application/pdf" and found.kind != "document") or (family == "image" and found.kind != "image") or (family == "video" and found.kind != "video") \
            or (family not in ("image", "video") and declared != "application/pdf")
        if mismatch:
            raise MediaError(422, f"The file content ({found.mime}) does not match its declared type ({declared})")
    if len(data) > MAX_BYTES[found.kind]:
        raise MediaError(413, f"{found.kind.capitalize()} files can be at most {MAX_BYTES[found.kind] // MB} MB (this one is {len(data) / MB:.1f} MB)")
    if found.kind == "document" and _PDF_ACTIVE.search(data):
        raise MediaError(415, "This PDF contains scripts or embedded files and was refused. Export a plain PDF and try again")
    return found


def process_image(data: bytes) -> tuple[bytes, bytes, int, int]:
    """Re-encode to JPEG (max 1600 px, no EXIF/GPS/metadata) plus a 320 px thumbnail. Returns (jpeg, thumb, width, height)."""
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.width * im.height > MAX_PIXELS:
                raise MediaError(413, f"That image is too large ({im.width}x{im.height} pixels). Maximum is {MAX_PIXELS // 1_000_000} megapixels")
            im.load()
            img = ImageOps.exif_transpose(im) or im
            if img.mode in ("RGBA", "LA", "P"):
                rgba = img.convert("RGBA")
                flat = Image.new("RGB", rgba.size, (255, 255, 255))
                flat.paste(rgba, mask=rgba.split()[3])
                img = flat
            else:
                img = img.convert("RGB")
            img.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            img.save(out, "JPEG", quality=85, optimize=True)  # no exif= argument: every metadata block is dropped
            thumb = img.copy()
            thumb.thumbnail((THUMB_SIDE, THUMB_SIDE), Image.Resampling.LANCZOS)
            tout = io.BytesIO()
            thumb.save(tout, "JPEG", quality=70, optimize=True)
            return out.getvalue(), tout.getvalue(), img.width, img.height
    except MediaError:
        raise
    except Image.DecompressionBombError:
        raise MediaError(413, "That image has too many pixels")
    except Exception:  # noqa: BLE001 - corrupt or truncated image
        raise MediaError(422, "This image could not be read. Re-export it as JPEG or PNG and try again")


# ------------------------------------------------------------------ labels / tags
_TAG_RE = re.compile(r"[^a-z0-9_-]+")


def clean_tags(raw: str | list[str] | None) -> list[str]:
    """'Services, Portfolio ,pricing' -> ['services', 'portfolio', 'pricing'] (lowercase slugs, unique, max 10 x 30 chars)."""
    items = raw.split(",") if isinstance(raw, str) else list(raw or [])
    out: list[str] = []
    for t in items:
        slug = _TAG_RE.sub("-", str(t).strip().lower()).strip("-")[:30]
        if slug and slug not in out:
            out.append(slug)
    return out[:MAX_TAGS]


# ------------------------------------------------------------------ safe-sending rules
def media_blocked_reason(age_days: int, warmup_days: int, media_today: int, total_today: int) -> str:
    """'' = media may be sent from this number now; otherwise the plain-language reason the message goes out as text only.

    age_days   : age of the number in whole days
    warmup_days: how many days the number is still "warming up" (cap still rising)
    media_today / total_today: media messages / all outbound messages this number sent today (before this one)"""
    if age_days < MEDIA_MIN_AGE_DAYS:
        return f"media is switched on after the number's day {MEDIA_MIN_AGE_DAYS} (this number is {age_days} day{'s' if age_days != 1 else ''} old)"
    if age_days < warmup_days and (media_today + 1) * 100 > MEDIA_WARMUP_SHARE_PCT * (total_today + 1):
        return f"while a number warms up, media is limited to {MEDIA_WARMUP_SHARE_PCT}% of the day's messages"
    return ""


def pick_round_robin(n: int, counter: int) -> int:
    """Index 0..n-1 for the counter-th pick (stable rotation)."""
    return counter % n if n > 0 else 0


def select_for_reply(assets: list[dict], allow_tags: list[str], wanted_ids: list[str], limit: int = REPLY_MAX_ASSETS) -> list[str]:
    """The ids the AI asked to attach, filtered by the workspace's allow-list: an id must exist, carry at least one allowed tag
    (an empty allow-list allows nothing) and appear once. assets = [{id, tags}] of the workspace."""
    allowed = {t.lower() for t in allow_tags}
    by_id = {a["id"]: a for a in assets}
    chosen: list[str] = []
    for i in wanted_ids:
        a = by_id.get(i)
        if a and i not in chosen and allowed.intersection(t.lower() for t in a.get("tags", [])):
            chosen.append(i)
    return chosen[:limit]


TOPIC_TAGS = {"services": ("services", "portfolio"), "pricing": ("pricing",), "trust": ("trust", "portfolio"), "scheduling": ("booking",), "other": (), "smalltalk": ()}


def fake_pick_for_topic(topic: str, assets: list[dict], limit: int = 2) -> list[str]:
    """Deterministic choice used by AI_FAKE mode: assets whose tags match the topic (real mode lets the model choose, then select_for_reply filters)."""
    wanted = set(TOPIC_TAGS.get(topic, ()))
    return [a["id"] for a in assets if wanted.intersection(a.get("tags", []))][:limit]


def append_links(body: str, urls: list[str]) -> str:
    """Add each URL to the text once, on its own line at the end (WhatsApp builds the preview from the first link)."""
    extra = [u for u in urls if u and u not in body]
    return body if not extra else body.rstrip() + "\n" + "\n".join(extra)
