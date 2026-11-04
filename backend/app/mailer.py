"""Outgoing email. Failures never break the request that triggered them: they are logged and (in development) visible in Mailpit."""
import asyncio
import logging
import smtplib

import httpx
from email.message import EmailMessage
from html import escape

from .config import settings

log = logging.getLogger("mailer")


def _send(to: str, subject: str, text: str, html: str) -> None:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = settings.smtp_from, to, subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as s:
        if settings.smtp_user:
            s.starttls()
            s.login(settings.smtp_user, settings.smtp_password)
        s.send_message(msg)


def layout(title: str, body: str, button: tuple[str, str] | None = None) -> str:
    btn = f'<p style="margin:28px 0"><a href="{escape(button[1])}" style="background:#2ed47a;color:#04120a;padding:13px 22px;border-radius:10px;text-decoration:none;font-weight:600">{escape(button[0])}</a></p>' if button else ""
    return (
        '<div style="font-family:system-ui,Segoe UI,sans-serif;max-width:520px;margin:0 auto;padding:28px;color:#14201a">'
        f'<h2 style="margin:0 0 14px">{escape(title)}</h2><div style="line-height:1.6;color:#33463b">{body}</div>{btn}'
        '<p style="color:#7a8c82;font-size:12.5px">ConnectFlow · If you did not expect this email you can ignore it.</p></div>'
    )


async def _send_resend(to: str, subject: str, text: str, html: str) -> None:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post("https://api.resend.com/emails", headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                         json={"from": settings.smtp_from, "to": [to], "subject": subject, "text": text, "html": html})
        r.raise_for_status()


async def send_email(to: str, subject: str, text: str, html: str) -> bool:
    try:
        if settings.resend_api_key:
            await _send_resend(to, subject, text, html)
        else:
            await asyncio.to_thread(_send, to, subject, text, html)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("email to %s failed: %s", to, e)
        return False
