"""SSRF guard for customer-supplied URLs (webhooks): only public internet hosts are allowed."""
import asyncio
import ipaddress
import socket
import time
from urllib.parse import urlparse

from .config import settings


class UnsafeUrl(ValueError):
    pass


def _check_sync(url: str) -> None:
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise UnsafeUrl("URL must start with http:// or https://")
    if u.username or u.password:
        raise UnsafeUrl("URLs with credentials are not allowed")
    if settings.webhook_allow_private:
        return
    if u.scheme != "https":
        raise UnsafeUrl("Webhook URLs must use https://")
    host = u.hostname
    if "." not in host and not host.replace(":", "").isdigit():
        raise UnsafeUrl("Internal host names are not allowed")
    try:
        infos = socket.getaddrinfo(host, u.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise UnsafeUrl("That host name does not resolve")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise UnsafeUrl("That address is not a public internet host")


async def assert_public_url(url: str) -> None:
    await asyncio.to_thread(_check_sync, url)


def _check_crawl_sync(url: str) -> None:
    """Crawling lead websites: http(s) only, never private/internal addresses (independent of WEBHOOK_ALLOW_PRIVATE)."""
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
        raise UnsafeUrl("not a crawlable URL")
    host = u.hostname
    if "." not in host and not host.replace(":", "").isdigit():
        raise UnsafeUrl("internal host name")
    infos = None
    for attempt in range(3):  # Docker's DNS forwarder drops lookups now and then: retry before calling a site dead
        try:
            infos = socket.getaddrinfo(host, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
            break
        except socket.gaierror:
            if attempt < 2:
                time.sleep(0.6)
    if not infos:
        raise UnsafeUrl("does not resolve")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise UnsafeUrl("not a public host")


async def assert_crawlable(url: str) -> None:
    await asyncio.to_thread(_check_crawl_sync, url)
