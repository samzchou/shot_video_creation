"""HTTP/URL safety helpers (stdlib only).

All API calls and downloads in the 3 skills go through this module so we have
a single chokepoint for two defenses:

  1. URL scheme validation — only http/https; reject file://, ftp://, data:,
     and other schemes that urllib.request.urlopen would otherwise accept.
     Without this, a compromised / spoofed API endpoint could redirect to
     ``file:///c:/...`` and trick the script into exfiltrating local data
     through the downloaded artifact path.

  2. Response size cap — Content-Length pre-check + streaming byte cap. The
     cap prevents a malicious or buggy server from filling the disk or RAM.

The script-side functions (``post_json`` / ``get_json`` / ``download`` in
``aigc_common.py``, ``api_request`` / ``download_image`` in ``gen-img``,
``create_speech`` in ``gen-tts``) all delegate here.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import urlparse

DEFAULT_USER_AGENT = "shot-video-creation/1.0"


class SafeUrlError(Exception):
    """URL scheme rejected or response exceeded the size cap."""


def _check_scheme(url: str) -> None:
    """Raise SafeUrlError unless url's scheme is http or https."""
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise SafeUrlError(
            f"refusing non-http(s) URL ({scheme or '<empty>'}): {url}"
        )


def safe_urlopen(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict | None = None,
    timeout: int = 60,
    user_agent: str = DEFAULT_USER_AGENT,
) -> urllib.request.addinfourl:
    """scheme-checked ``urllib.request.urlopen``.

    HTTP errors (4xx/5xx) still propagate as ``urllib.error.HTTPError`` so
    callers can branch on ``.code`` / ``.body`` as before. Only network and
    scheme failures raise :class:`SafeUrlError`.
    """
    _check_scheme(url)
    h = {"User-Agent": user_agent}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError:
        raise  # let 4xx/5xx propagate; callers branch on .code / .body
    except urllib.error.URLError as exc:
        raise SafeUrlError(f"urlopen failed for {url}: {exc.reason}") from exc


def safe_get_bytes(
    url: str,
    *,
    timeout: int = 60,
    max_bytes: int,
    user_agent: str = DEFAULT_USER_AGENT,
) -> bytes:
    """GET ``url`` and return response body bytes.

    Enforces scheme + ``Content-Length`` pre-check + streaming byte cap of
    ``max_bytes``. Raises :class:`SafeUrlError` on scheme rejection,
    oversized ``Content-Length``, or streamed body exceeding ``max_bytes``
    (catches servers that lie about ``Content-Length``).
    """
    resp = safe_urlopen(url, timeout=timeout, user_agent=user_agent)
    try:
        cl = resp.headers.get("Content-Length")
        if cl is not None:
            try:
                size = int(cl)
            except ValueError:
                raise SafeUrlError(f"invalid Content-Length header: {cl!r}") from None
            if size > max_bytes:
                raise SafeUrlError(
                    f"Content-Length {size} exceeds limit {max_bytes}"
                )
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = resp.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise SafeUrlError(
                    f"streamed body exceeds limit {max_bytes} bytes"
                )
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        resp.close()


def safe_post_bytes(
    url: str,
    payload: dict,
    *,
    headers: dict | None = None,
    timeout: int = 60,
    max_bytes: int = 50 * 1024 * 1024,
    user_agent: str = DEFAULT_USER_AGENT,
) -> bytes:
    """POST JSON ``payload`` and return raw response bytes."""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    resp = safe_urlopen(
        url,
        method="POST",
        data=data,
        headers=h,
        timeout=timeout,
        user_agent=user_agent,
    )
    try:
        body = resp.read()
        if len(body) > max_bytes:
            raise SafeUrlError(
                f"response body {len(body)} exceeds limit {max_bytes}"
            )
        return body
    finally:
        resp.close()


def safe_post_json(
    url: str,
    payload: dict,
    *,
    headers: dict | None = None,
    timeout: int = 60,
    max_bytes: int = 50 * 1024 * 1024,
    user_agent: str = DEFAULT_USER_AGENT,
) -> dict:
    """POST JSON and return parsed JSON response."""
    body = safe_post_bytes(
        url,
        payload,
        headers=headers,
        timeout=timeout,
        max_bytes=max_bytes,
        user_agent=user_agent,
    )
    return json.loads(body.decode("utf-8"))


def safe_get_json(
    url: str,
    *,
    headers: dict | None = None,
    timeout: int = 30,
    max_bytes: int = 10 * 1024 * 1024,
    user_agent: str = DEFAULT_USER_AGENT,
) -> dict:
    """GET JSON and return parsed JSON response."""
    resp = safe_urlopen(url, timeout=timeout, headers=headers, user_agent=user_agent)
    try:
        body = resp.read()
        if len(body) > max_bytes:
            raise SafeUrlError(
                f"response body {len(body)} exceeds limit {max_bytes}"
            )
        return json.loads(body.decode("utf-8"))
    finally:
        resp.close()
