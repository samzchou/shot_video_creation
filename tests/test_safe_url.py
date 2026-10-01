"""_shared/safe_url.py 行为测试（SSRF 防线 + 大小上限）。"""
from __future__ import annotations

import io
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "_shared"))

import safe_url  # noqa: E402
from safe_url import (  # noqa: E402
    DEFAULT_USER_AGENT,
    SafeUrlError,
    safe_get_bytes,
    safe_get_json,
    safe_post_bytes,
    safe_post_json,
    safe_urlopen,
)


class TestSchemeRejection:
    """非 http(s) scheme 必须被拒绝——这是 SSRF 防御的核心。"""

    @pytest.mark.parametrize(
        "url",
        [
            "file:///c:/windows/system32/drivers/etc/hosts",
            "file:///etc/passwd",
            "ftp://internal.corp/secret",
            "data:text/html,<script>alert(1)</script>",
            "javascript:alert(1)",
            "gopher://internal:11211/",
            "",  # 空 URL
            "no-scheme.example.com/x",  # 没有 scheme
        ],
    )
    @pytest.mark.parametrize(
        "fn_name",
        ["safe_urlopen", "safe_get_bytes", "safe_post_bytes", "safe_post_json", "safe_get_json"],
    )
    def test_non_http_scheme_rejected(self, url, fn_name):
        fn = getattr(safe_url, fn_name)
        with pytest.raises(SafeUrlError) as exc:
            if fn_name == "safe_urlopen":
                fn(url)
            elif fn_name == "safe_get_bytes":
                fn(url, max_bytes=1024)
            elif fn_name in {"safe_post_bytes", "safe_post_json"}:
                fn(url, payload={"x": 1})
            elif fn_name == "safe_get_json":
                fn(url)
        assert "non-http(s)" in str(exc.value) or "URL" in str(exc.value)


class TestContentLengthCap:
    """Content-Length 超 max_bytes 必须立即拒（不让任何字节落盘）。"""

    def test_content_length_pre_check(self, monkeypatch):
        # 模拟一个返回 Content-Length=200 但 max_bytes=100 的"撒谎"端点
        # —— 必须立刻拒，resp.read() 不能被调用
        fake_resp = mock.MagicMock()
        fake_resp.headers = {"Content-Length": "200"}
        fake_resp.read = mock.MagicMock(side_effect=AssertionError("read() should not be called"))
        fake_resp.close = mock.MagicMock()

        monkeypatch.setattr(safe_url.urllib.request, "urlopen", lambda *a, **kw: fake_resp)
        with pytest.raises(SafeUrlError, match="Content-Length"):
            safe_get_bytes("https://example.com/big", max_bytes=100)


class TestStreamingSizeCap:
    """服务端不返 Content-Length 时，流式字节计数也必须超限拒。"""

    def test_streaming_body_capped(self, monkeypatch):
        # 模拟一个分 4 次返回 200 字节、但 Content-Length 不存在的"撒谎"端点
        chunks = [b"x" * 80, b"x" * 80, b"x" * 40, b""]  # 200 字节总计
        fake_resp = mock.MagicMock()
        fake_resp.headers = {}  # 无 Content-Length
        fake_resp.read = mock.MagicMock(side_effect=chunks)
        fake_resp.close = mock.MagicMock()

        monkeypatch.setattr(safe_url.urllib.request, "urlopen", lambda *a, **kw: fake_resp)
        with pytest.raises(SafeUrlError, match="exceeds limit"):
            safe_get_bytes("https://example.com/big", max_bytes=100)

    def test_streaming_within_limit_ok(self, monkeypatch):
        # 120 字节，max_bytes=200，应当成功拼装返回
        chunks = [b"x" * 80, b"x" * 40, b""]
        fake_resp = mock.MagicMock()
        fake_resp.headers = {}
        fake_resp.read = mock.MagicMock(side_effect=chunks)
        fake_resp.close = mock.MagicMock()

        monkeypatch.setattr(safe_url.urllib.request, "urlopen", lambda *a, **kw: fake_resp)
        result = safe_get_bytes("https://example.com/ok", max_bytes=200)
        assert len(result) == 120


class TestHttpErrorPassthrough:
    """HTTP 4xx/5xx 必须作为 urllib.error.HTTPError 透传（不吞为 SafeUrlError）。"""
    pass  # urllib.error is already imported at top

    def test_http_error_propagates(self, monkeypatch):
        monkeypatch.setattr(
            safe_url.urllib.request,
            "urlopen",
            mock.MagicMock(side_effect=safe_url.urllib.error.HTTPError(
                "https://example.com/404", 404, "Not Found", {}, io.BytesIO(b"oops")
            )),
        )
        with pytest.raises(safe_url.urllib.error.HTTPError) as exc:
            safe_urlopen("https://example.com/404")
        assert exc.value.code == 404


class TestUserAgent:
    def test_default_user_agent(self, monkeypatch):
        captured: dict = {}

        def fake_urlopen(req, timeout=None):
            captured["ua"] = req.headers.get("User-agent") or req.headers.get("User-Agent")
            return mock.MagicMock(read=lambda: b"{}", headers={}, close=mock.MagicMock())

        monkeypatch.setattr(safe_url.urllib.request, "urlopen", fake_urlopen)
        safe_get_json("https://example.com/x")
        # urllib 会归一化为 "User-agent"
        assert captured["ua"] == DEFAULT_USER_AGENT
        assert "shot-video-creation" in captured["ua"]
