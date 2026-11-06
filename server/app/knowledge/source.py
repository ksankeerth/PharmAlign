"""Getting a file from a URL. Only public http and https addresses; a redirect is checked again; the size is capped."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx

from ..problems import problem

MAX_REDIRECTS = 4


def _check_public(url: str) -> None:
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        problem(422, "invalid_request", "That address can't be fetched", "Only http and https addresses are fetched.")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except OSError:
        problem(502, "fetch_failed", "The address could not be reached", f"{parts.hostname} did not resolve.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            problem(422, "invalid_request", "That address can't be fetched", "Private and local addresses are not fetched.")


async def fetch(url: str, max_bytes: int, timeout: float = 30.0) -> tuple[bytes, str]:
    """(bytes, content type). Raises a problem the caller shows as it is."""
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for _ in range(MAX_REDIRECTS + 1):
            _check_public(url)
            try:
                async with client.stream("GET", url) as r:
                    if r.is_redirect:
                        url = str(r.next_request.url) if r.next_request else url
                        continue
                    if r.status_code != 200:
                        problem(502, "fetch_failed", "The address did not return the file", f"It answered {r.status_code}.")
                    data = bytearray()
                    async for part in r.aiter_bytes():
                        data += part
                        if len(data) > max_bytes:
                            problem(422, "invalid_request", "The file is too large", f"Files over {max_bytes // 1_000_000} MB are not stored.")
                    return bytes(data), r.headers.get("content-type", "application/octet-stream").split(";")[0].strip().lower()
            except httpx.HTTPError as e:
                problem(502, "fetch_failed", "The address could not be reached", type(e).__name__)
    problem(502, "fetch_failed", "Too many redirects", None)
