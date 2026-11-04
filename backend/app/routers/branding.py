"""Workspace branding: accent colour + logo (white-label). Default = ConnectFlow green."""
import base64
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Ctx, need_role
from ..models import AuditLog

router = APIRouter(prefix="/api", tags=["branding"])
MAX_LOGO = 256 * 1024


class ColorIn(BaseModel):
    color: str | None = None


class LogoIn(BaseModel):
    data_url: str = Field(alias="dataUrl")


class NameIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)


def _valid_logo(data_url: str) -> str:
    m = re.fullmatch(r"data:(image/(?:png|jpeg|webp|svg\+xml));base64,([A-Za-z0-9+/=]+)", data_url or "")
    if not m:
        raise HTTPException(422, "Logo must be a PNG, JPEG, WebP or SVG image")
    raw = base64.b64decode(m.group(2), validate=True)
    if len(raw) > MAX_LOGO:
        raise HTTPException(413, "Logo is too large (max 256 KB)")
    kind = m.group(1)
    ok = (
        (kind == "image/png" and raw[:8] == b"\x89PNG\r\n\x1a\n")
        or (kind == "image/jpeg" and raw[:3] == b"\xff\xd8\xff")
        or (kind == "image/webp" and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP")
        or (kind == "image/svg+xml" and b"<svg" in raw[:2000].lower())
    )
    if not ok:
        raise HTTPException(422, "The file content does not match its image type")
    if kind == "image/svg+xml" and re.search(rb"<script|onload\s*=|onerror\s*=|javascript:", raw, re.I):
        raise HTTPException(422, "SVG logos must not contain scripts")
    return data_url


@router.put("/branding/color")
def set_color(body: ColorIn, ctx: Ctx = Depends(need_role("admin")), db: Session = Depends(get_db)):
    if body.color is not None and not re.fullmatch(r"#[0-9a-fA-F]{6}", body.color):
        raise HTTPException(422, "Colour must look like #1a73e8")
    ctx.tenant.brand_color = body.color.lower() if body.color else None
    db.add(AuditLog(tenant_id=ctx.tenant.id, user_id=ctx.user.id, action="branding.color", detail=body.color or "default"))
    db.commit()
    return {"color": ctx.tenant.brand_color}


@router.put("/branding/logo")
def set_logo(body: LogoIn, ctx: Ctx = Depends(need_role("admin")), db: Session = Depends(get_db)):
    ctx.tenant.logo_data = _valid_logo(body.data_url)
    db.add(AuditLog(tenant_id=ctx.tenant.id, user_id=ctx.user.id, action="branding.logo", detail="set"))
    db.commit()
    return {"ok": True}


@router.delete("/branding/logo", status_code=204)
def clear_logo(ctx: Ctx = Depends(need_role("admin")), db: Session = Depends(get_db)):
    ctx.tenant.logo_data = None
    db.commit()


@router.patch("/workspace")
def rename(body: NameIn, ctx: Ctx = Depends(need_role("admin")), db: Session = Depends(get_db)):
    ctx.tenant.name = body.name.strip()
    db.commit()
    return {"name": ctx.tenant.name}


class ThemeIn(BaseModel):
    accent: str | None = None
    sidebar: str | None = None
    page: str | None = None


@router.put("/branding/theme")
def set_theme(body: ThemeIn, ctx: Ctx = Depends(need_role("admin")), db: Session = Depends(get_db)):
    for v in (body.accent, body.sidebar, body.page):
        if v is not None and not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
            raise HTTPException(422, "Colours must look like #1a73e8")
    t = ctx.tenant
    t.brand_color = body.accent.lower() if body.accent else None
    t.sidebar_color = body.sidebar.lower() if body.sidebar else None
    t.page_color = body.page.lower() if body.page else None
    db.add(AuditLog(tenant_id=t.id, user_id=ctx.user.id, action="branding.theme", detail=f"{body.accent or '-'} / {body.sidebar or '-'} / {body.page or '-'}"))
    db.commit()
    return {"accent": t.brand_color, "sidebar": t.sidebar_color, "page": t.page_color}


class WebsiteIn(BaseModel):
    website: str = Field(default="", max_length=300)


@router.put("/workspace/website")
def set_website(body: WebsiteIn, ctx: Ctx = Depends(need_role("admin")), db: Session = Depends(get_db)):
    w = body.website.strip()
    if w:
        if not re.match(r"^https?://", w, re.I):
            w = "https://" + w
        if not re.fullmatch(r"https?://[^\s/$.?#][^\s]*\.[^\s]{2,}", w, re.I):
            raise HTTPException(422, "That does not look like a website address")
    ctx.tenant.website_url = w
    db.commit()
    return {"website": w}
