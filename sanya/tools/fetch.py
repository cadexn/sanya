"""The guarded fetcher: the only way a web address chosen by a member or the model is opened.

Public addresses only. The DNS answer is checked and pinned into the
connection, every redirect is validated as a new request, and there are no
cookies, proxies, credentials or odd ports.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver

_REDIRECTS = {301, 302, 303, 307, 308}


class FetchError(Exception):
    """Why a fetch was refused or failed. ``kind`` is safe to show the model."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind  # bad_link, private_address, unavailable, too_large, too_many_redirects, timeout


class _PrivateDestination(OSError):
    pass


@dataclass(frozen=True)
class Fetched:
    data: bytes
    content_type: str
    url: str  # after redirects


def public_address(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    embedded = getattr(ip, "ipv4_mapped", None) or getattr(ip, "sixtofour", None)
    return bool(ip.is_global and not ip.is_multicast and not getattr(ip, "teredo", None)
                and (embedded is None or (embedded.is_global and not embedded.is_multicast)))


def validate_url(url: Any) -> str:
    """Return ``url`` if it may be fetched; raise FetchError otherwise."""
    if not isinstance(url, str) or len(url) > 4096 or any(ord(c) < 33 for c in url) or "\\" in url:
        raise FetchError("bad_link")
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError:
        raise FetchError("bad_link") from None
    if parts.scheme not in {"http", "https"} or not host or parts.username is not None or parts.password is not None:
        raise FetchError("bad_link")
    if port not in {None, 80, 443} or "%" in host:
        raise FetchError("bad_link")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if "." not in host or host.endswith((".local", ".localhost", ".internal", ".home", ".lan")):
            raise FetchError("private_address") from None
    else:
        if not public_address(host):
            raise FetchError("private_address")
    return url


class PublicResolver(AbstractResolver):
    """Resolves a name and refuses it if any of its addresses is not public."""

    async def resolve(self, host, port=0, family=socket.AF_UNSPEC):
        records = await asyncio.get_running_loop().getaddrinfo(host, port, family=family, type=socket.SOCK_STREAM)
        if not records or any(not public_address(record[4][0]) for record in records):
            raise _PrivateDestination("private destination")
        return [{"hostname": host, "host": record[4][0], "port": port, "family": record[0],
                 "proto": record[2], "flags": socket.AI_NUMERICHOST} for record in records]

    async def close(self):
        pass


def _is_private(exc: BaseException) -> bool:
    return any(isinstance(cause, _PrivateDestination)
               for cause in (exc, getattr(exc, "os_error", None), exc.__cause__))


async def fetch(url: str, *, max_bytes: int, timeout: float, user_agent: str, max_redirects: int,
                session: Optional[Any] = None) -> Fetched:
    """Download ``url``, following at most ``max_redirects`` redirects, each checked like a new address.

    ``session`` is for tests; normally a guarded one is made per call.
    """
    url = validate_url(url)
    own = session is None
    if own:
        connector = aiohttp.TCPConnector(resolver=PublicResolver(), use_dns_cache=False)
        session = aiohttp.ClientSession(
            connector=connector, trust_env=False, cookie_jar=aiohttp.DummyCookieJar(),
            timeout=aiohttp.ClientTimeout(total=timeout),
            headers={"User-Agent": user_agent, "Accept-Encoding": "identity"})
    try:
        for _ in range(max_redirects):
            async with session.get(url, allow_redirects=False) as response:
                if response.status in _REDIRECTS:
                    # A redirect is a new request and is checked like one.
                    url = validate_url(urljoin(url, response.headers.get("Location", "")))
                    continue
                if response.status != 200:
                    raise FetchError("unavailable")
                if response.content_length is not None and response.content_length > max_bytes:
                    raise FetchError("too_large")
                chunks, size = [], 0
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > max_bytes:
                        raise FetchError("too_large")
                    chunks.append(chunk)
                return Fetched(b"".join(chunks), response.content_type or "", url)
        raise FetchError("too_many_redirects")
    except asyncio.TimeoutError:
        raise FetchError("timeout") from None
    except (aiohttp.ClientError, OSError) as exc:
        raise FetchError("private_address" if _is_private(exc) else "unavailable") from None
    finally:
        if own:
            await session.close()
