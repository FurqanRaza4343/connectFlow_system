"""Status card renderer: SVG template -> 1080x1920 PNG with resvg (a Rust SVG renderer, no browser, no web API).

A Python port of legacy/orchestrator/src/render.mjs: the same 8 designs. The text is ALWAYS drawn here, never by an AI image
model (those misspell words); a background picture (AI-made or uploaded) only provides the scenery. Pure functions except
`render_card`, which needs the `resvg_py` package and fonts (DejaVu Sans for Latin, Noto Naskh Arabic for Urdu) in the image."""
import re
from typing import Any

W = 1080
H = 1920
TEMPLATES = ["gradient", "band", "split", "badge", "minimal", "bold", "frame", "diagonal"]
POSITIONS = ["top", "center", "bottom"]

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_RTL = re.compile(r"[؀-ۿ]")


def esc(s: Any) -> str:
    return str("" if s is None else s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")


def is_rtl(s: Any) -> bool:
    return bool(_RTL.search(str(s or "")))


def is_hex(c: Any) -> bool:
    return bool(_HEX.match(str(c or "")))


def _hex(c: Any, d: str) -> str:
    return str(c) if is_hex(c) else d


def _rgb(c: str) -> tuple[int, int, int]:
    n = int(c[1:], 16)
    return (n >> 16) & 255, (n >> 8) & 255, n & 255


def luminance(c: str) -> float:
    """WCAG relative luminance (0 dark .. 1 light)."""
    def ch(v: int) -> float:
        x = v / 255
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in _rgb(c))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio between two colours (1..21)."""
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def readable_ink(bg: str) -> str:
    """White or near-black, whichever reads better on `bg`."""
    return "#ffffff" if contrast("#ffffff", bg) >= contrast("#0b1220", bg) else "#0b1220"


def mix(a: str, b: str, t: float) -> str:
    x, y = _rgb(a), _rgb(b)
    return "#" + "".join(f"{round(x[i] + (y[i] - x[i]) * t):02x}" for i in range(3))


def shade(c: str, amt: float) -> str:
    """Darken (amt < 0) or lighten (amt > 0) a #rrggbb colour."""
    def f(v: int) -> int:
        return max(0, min(255, round(v * (1 + amt) if amt < 0 else v + (255 - v) * amt)))
    return "#" + "".join(f"{f(v):02x}" for v in _rgb(c))


def wrap(text: Any, font_size: int, max_width: int, max_lines: int = 5) -> list[str]:
    """Greedy word wrap by an approximate glyph width (0.55 x font size per character); an ellipsis marks cut text."""
    per = max(6, int(max_width // (font_size * 0.55)))
    lines: list[str] = []
    cur = ""
    for w in str(text or "").split():
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= per:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        keep = lines[:max_lines]
        keep[-1] = re.sub(r"[\s,.;:-]*$", "", keep[-1]) + "…"
        return keep
    return lines


def _block(lines: list[str], x: float, y: float, size: int, *, fill: str = "#fff", weight: int = 700, lh: float = 1.22, rtl: bool = False) -> tuple[str, int]:
    # Urdu / Arabic: ONE font for every line; Noto Naskh has no bold, so a thin stroke in the text colour makes headlines heavier
    d = ' direction="rtl" unicode-bidi="embed"' if rtl else ""
    fam = "Noto Naskh Arabic, sans-serif" if rtl else "DejaVu Sans, Noto Naskh Arabic, sans-serif"
    w = 400 if rtl else weight
    stroke = f' stroke="{fill}" stroke-width="{size * 0.028:.1f}" stroke-linejoin="round"' if rtl and weight >= 700 else ""
    out = "".join(
        f'<text x="{x}" y="{round(y + i * size * lh)}" font-size="{size}" font-weight="{w}" fill="{fill}" text-anchor="middle" font-family="{fam}"{d}{stroke}>'
        f'{esc(re.sub(r"[،؛,.;:!]+$", "", l) if rtl else l)}</text>'   # a trailing comma lands on the wrong side of an RTL line
        for i, l in enumerate(lines))
    return out, round(len(lines) * size * lh)


def _head_size(t: str) -> int:
    n = len(t)
    return 104 if n <= 28 else 88 if n <= 48 else 72 if n <= 80 else 60


def card_svg(o: dict[str, Any]) -> str:
    """SVG markup (1080x1920). Keys: template, headline, sub, badge, cta, hint, footer, accent, dark, ink, logo (data URL),
    bg (data URL), overlay (0-85), pos (top|center|bottom), plain (picture only)."""
    accent, dark = _hex(o.get("accent"), "#004ECB"), _hex(o.get("dark"), "#0b1b3a")
    tpl = o.get("template") if o.get("template") in TEMPLATES else "gradient"
    photo = bool(o.get("bg"))
    if photo and o.get("plain"):
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
                f'<image href="{o["bg"]}" x="0" y="0" width="{W}" height="{H}" preserveAspectRatio="xMidYMid slice"/></svg>')

    # the colour behind the text decides the text colour (an explicit `ink` wins)
    if photo:
        surface = "#000000"
    elif tpl == "minimal":
        surface = "#f4f7fc"
    elif tpl in ("bold", "band"):
        surface = accent
    elif tpl in ("gradient", "badge"):
        surface = mix(accent, shade(dark, -0.2), 0.5)
    else:
        surface = dark
    ink = _hex(o.get("ink"), readable_ink(surface))
    sub_ink = mix(ink, surface, 0.18)
    foot_surface = "#000000" if photo else "#e9edf5" if tpl == "minimal" else mix(accent if tpl == "bold" else dark, "#000000", 0.25)
    foot_ink = _hex(o.get("ink"), readable_ink(foot_surface))
    headline, sub = str(o.get("headline") or ""), str(o.get("sub") or "")
    rtl = is_rtl(headline) or is_rtl(sub)
    hs = _head_size(headline)
    head = wrap(headline, hs, 900, 5)
    sub_lines = wrap(sub, 44, 880, 4) if sub else []
    h_block = len(head) * hs * 1.22
    sub_block = len(sub_lines) * 44 * 1.3
    gap = 56 if sub_lines else 0
    total = h_block + gap + sub_block
    pos = o.get("pos") if o.get("pos") in POSITIONS else "center"
    centre = 1090 if tpl == "split" else 760 if tpl == "band" else 700 if pos == "top" else 1120 if pos == "bottom" else 860
    y = round(centre - total / 2 + hs * 0.9)

    if photo:
        try:
            ov = float(o.get("overlay", 48))
        except (TypeError, ValueError):
            ov = 48.0
        op = min(0.85, max(0.0, ov / 100))
        bg = (f'<image href="{o["bg"]}" x="0" y="0" width="{W}" height="{H}" preserveAspectRatio="xMidYMid slice"/>'
              f'<rect width="{W}" height="{H}" fill="#000" opacity="{op}"/>')
    elif tpl == "minimal":
        bg = (f'<rect width="{W}" height="{H}" fill="#f4f7fc"/><rect x="0" y="0" width="{W}" height="26" fill="{accent}"/>'
              f'<rect x="0" y="{H - 26}" width="{W}" height="26" fill="{accent}"/>')
    elif tpl == "band":
        bg = (f'<rect width="{W}" height="{H}" fill="{dark}"/>'
              f'<rect x="0" y="560" width="{W}" height="{round(h_block + sub_block + gap + 170)}" fill="{accent}"/>')
    elif tpl == "split":
        bg = (f'<rect width="{W}" height="{H}" fill="{dark}"/><path d="M0 0 H{W} V820 Q540 960 0 820 Z" fill="{accent}"/>'
              f'<circle cx="880" cy="250" r="220" fill="#fff" opacity="0.08"/><circle cx="170" cy="640" r="140" fill="#fff" opacity="0.08"/>')
    elif tpl == "bold":
        bg = (f'<rect width="{W}" height="{H}" fill="{accent}"/><circle cx="960" cy="140" r="300" fill="{dark}" opacity="0.16"/>'
              f'<circle cx="90" cy="1760" r="360" fill="{dark}" opacity="0.14"/>')
    elif tpl == "frame":
        bg = (f'<rect width="{W}" height="{H}" fill="{dark}"/>'
              f'<rect x="48" y="48" width="{W - 96}" height="{H - 96}" rx="28" fill="none" stroke="{accent}" stroke-width="10"/>'
              f'<rect x="{W / 2 - 90}" y="{round(y - hs * 0.9 - 70)}" width="180" height="12" rx="6" fill="{accent}"/>')
    elif tpl == "diagonal":
        bg = (f'<rect width="{W}" height="{H}" fill="{dark}"/><polygon points="0,0 {W},0 0,520" fill="{accent}"/>'
              f'<polygon points="{W},{H} {W},{H - 520} 0,{H}" fill="{accent}" opacity="0.9"/>')
    else:
        bg = (f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{accent}"/>'
              f'<stop offset="1" stop-color="{shade(dark, -0.2)}"/></linearGradient></defs><rect width="{W}" height="{H}" fill="url(#g)"/>'
              f'<circle cx="930" cy="190" r="260" fill="#fff" opacity="0.07"/><circle cx="120" cy="1700" r="320" fill="#fff" opacity="0.06"/>')

    parts = [bg]
    if o.get("logo"):
        parts.append(f'<image href="{o["logo"]}" x="{W / 2 - 110}" y="{150 if tpl == "split" else 140}" width="220" height="130" preserveAspectRatio="xMidYMid meet"/>')
    raw = str(o.get("badge") or "")[:14].strip()
    if raw:
        words = raw.split()
        cx, cy = W / 2, 560
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="215" fill="#ffd23f"/>'
                     f'<circle cx="{cx}" cy="{cy}" r="193" fill="none" stroke="#0b1b3a" stroke-width="6" stroke-dasharray="14 10"/>')

        def fit(t: str, mx: int) -> int:
            return max(40, min(mx, int(330 // (max(2, len(t)) * 0.66))))   # keep the text inside the circle
        fam = 'font-family="DejaVu Sans, sans-serif"'
        if len(words) > 1:
            top, rest = words[0], " ".join(words[1:])
            s1, s2 = fit(top, 140), fit(rest, 70)
            parts.append(f'<text x="{cx}" y="{cy + s1 * 0.18}" font-size="{s1}" font-weight="800" fill="#0b1b3a" text-anchor="middle" {fam}>{esc(top)}</text>'
                         f'<text x="{cx}" y="{cy + s1 * 0.18 + s2 * 1.1}" font-size="{s2}" font-weight="800" fill="#0b1b3a" text-anchor="middle" {fam}>{esc(rest)}</text>')
        else:
            s1 = fit(raw, 120)
            parts.append(f'<text x="{cx}" y="{cy + s1 * 0.34}" font-size="{s1}" font-weight="800" fill="#0b1b3a" text-anchor="middle" {fam}>{esc(raw)}</text>')
        y += 170
    head_svg, head_h = _block(head, W / 2, y, hs, fill=ink, rtl=rtl)
    parts.append(head_svg)
    if sub_lines:
        parts.append(_block(sub_lines, W / 2, y + head_h + 30, 44, fill=sub_ink, weight=500, lh=1.3, rtl=rtl)[0])
    cta = str(o.get("cta") or "")
    if cta:
        cy = min(1560, round(y + head_h + sub_block + 150))
        cw = min(900, max(360, len(cta) * 30 + 120))
        btn_fill = accent if tpl == "minimal" and not photo else ink
        btn_ink = readable_ink(btn_fill)
        cr = is_rtl(cta)
        cfam = "Noto Naskh Arabic, DejaVu Sans" if cr else "DejaVu Sans, Noto Naskh Arabic"
        rtl_attr = ' direction="rtl"' if cr else ""
        parts.append(f'<rect x="{(W - cw) / 2}" y="{cy}" width="{cw}" height="110" rx="55" fill="{btn_fill}"/>'
                     f'<text x="{W / 2}" y="{cy + 72}" font-size="44" font-weight="700" fill="{btn_ink}" text-anchor="middle" font-family="{cfam}, sans-serif"{rtl_attr}>{esc(cta[:40])}</text>')
        if o.get("hint"):
            parts.append(f'<text x="{W / 2}" y="{cy + 176}" font-size="34" font-weight="600" fill="{sub_ink}" text-anchor="middle" font-family="DejaVu Sans, sans-serif">{esc(str(o["hint"])[:40])}  ↓</text>')
    footer = str(o.get("footer") or "")
    if footer:
        fop = 0.04 if tpl == "minimal" and not photo else 0.28
        parts.append(f'<rect x="0" y="{H - 190}" width="{W}" height="190" fill="#000" opacity="{fop}"/>')
        parts.append(_block(wrap(re.sub(r"\s*\n\s*", "  ·  ", footer), 38, 940, 2), W / 2, H - 100, 38, fill=foot_ink, weight=600, lh=1.3)[0])
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">{"".join(parts)}</svg>'


def renderer_available() -> bool:
    try:
        import resvg_py  # noqa: F401
        return True
    except ImportError:
        return False


def render_card(o: dict[str, Any]) -> bytes:
    """PNG bytes of a card (CPU-bound: call it in a thread). Raises RuntimeError when the renderer is not installed."""
    try:
        import resvg_py
    except ImportError as e:  # pragma: no cover - the image installs it
        raise RuntimeError("image renderer (resvg_py) is not installed") from e
    return bytes(resvg_py.svg_to_bytes(svg_string=card_svg(o), width=W, font_family="DejaVu Sans"))


def is_png(b: bytes) -> bool:
    return len(b) > 8 and b[:8] == b"\x89PNG\r\n\x1a\n"
