"""volc-img-gen/scripts/gen.py：图像生成核心逻辑。

覆盖范围：
  - size 解析/校验（_parse_size / normalize_size / size_to_volc）
  - 参考图引用（resolve_image_ref）
  - payload 构造（build_volc_payload / build_payload）
  - 响应解析（extract_volc_urls / extract_image_urls）
  - 候选链 fallback 触发条件（is_model_unavailable）
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

# ── size 解析 ───────────────────────────────────────────────────────────────


class TestParseSize:
    def test_wxh_basic(self, gen_img):
        assert gen_img._parse_size("1024x768") == (1024, 768)

    def test_w_star_h(self, gen_img):
        assert gen_img._parse_size("2048*2048") == (2048, 2048)

    def test_w_cross_h(self, gen_img):
        # W×H（火山文档里的全角符号）
        assert gen_img._parse_size("1536×2688") == (1536, 2688)

    def test_with_whitespace(self, gen_img):
        assert gen_img._parse_size("  1024 x  1024  ") == (1024, 1024)

    def test_invalid_returns_none(self, gen_img):
        assert gen_img._parse_size("not a size") is None
        assert gen_img._parse_size("") is None
        assert gen_img._parse_size("1024") is None


class TestSizeToVolc:
    def test_explicit_2k(self, gen_img):
        assert gen_img.size_to_volc("2K") == "2K"
        assert gen_img.size_to_volc("4k") == "4k"

    def test_auto_returns_none(self, gen_img):
        assert gen_img.size_to_volc("auto") is None

    def test_in_range_passes(self, gen_img):
        assert gen_img.size_to_volc("2048x2048") == "2048x2048"
        assert gen_img.size_to_volc("1024x1024") == "1024x1024"

    def test_out_of_range_exits(self, gen_img):
        with pytest.raises(SystemExit):
            gen_img.size_to_volc("3000x3000")

    def test_invalid_exits(self, gen_img):
        with pytest.raises(SystemExit):
            gen_img.size_to_volc("garbage")


# ── 参考图引用 ───────────────────────────────────────────────────────────────


class TestResolveImageRef:
    def test_https_url_passes_through(self, gen_img):
        url = "https://example.com/img.png"
        assert gen_img.resolve_image_ref(url) == url

    def test_data_uri_passes_through(self, gen_img):
        du = "data:image/png;base64,AAAA"
        assert gen_img.resolve_image_ref(du) == du

    def test_local_file_to_base64(self, gen_img, tmp_path: Path):
        png = tmp_path / "tiny.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n")
        result = gen_img.resolve_image_ref(str(png))
        assert result.startswith("data:image/png;base64,")
        assert "AAAA" not in result  # 实际 base64 ≠ "AAAA"

    def test_missing_local_file_exits(self, gen_img, tmp_path: Path):
        with pytest.raises(SystemExit):
            gen_img.resolve_image_ref(str(tmp_path / "missing.png"))


# ── Payload 构造（火山） ─────────────────────────────────────────────────────


class TestBuildVolcPayload:
    def _args(self, **kw):
        base = dict(
            prompt="hello", seed=None, watermark=False, image=None, image2=None, image3=None, image_size=None
        )
        base.update(kw)
        return SimpleNamespace(**base)

    def test_text_to_image_default(self, gen_img):
        args = self._args()
        p = gen_img.build_volc_payload(args, model="doubao-seedream-5-0-260128")
        assert p["model"] == "doubao-seedream-5-0-260128"
        assert p["prompt"] == "hello"
        assert p["watermark"] is False
        assert p["response_format"] == "url"
        assert p["output_format"] == "png"
        # 文生图默认 2K
        assert p["size"] == "2K"
        # 没有 image 字段
        assert "image" not in p

    def test_edit_mode_drops_size(self, gen_img):
        # 编辑模式：传了 --image，应不附带 size（跟随参考图）
        args = self._args(image="https://example.com/ref.png", image_size="2048x2048")
        p = gen_img.build_volc_payload(args, model="m")
        # 编辑模式 + 显式 image_size 仍可附带 size
        assert p["size"] == "2048x2048"
        assert "image" in p
        assert p["image"] == ["https://example.com/ref.png"]

    def test_edit_mode_no_image_size(self, gen_img):
        args = self._args(image="https://example.com/ref.png")
        p = gen_img.build_volc_payload(args, model="m")
        # 编辑模式未显式 size → 不附带
        assert "size" not in p

    def test_multi_reference_images(self, gen_img):
        args = self._args(
            image="https://a/1.png",
            image2="https://a/2.png",
            image3="https://a/3.png",
        )
        p = gen_img.build_volc_payload(args, model="m")
        assert p["image"] == [
            "https://a/1.png",
            "https://a/2.png",
            "https://a/3.png",
        ]

    def test_seed_included(self, gen_img):
        args = self._args(seed=42)
        p = gen_img.build_volc_payload(args, model="m")
        assert p["seed"] == 42


# ── 响应解析 ────────────────────────────────────────────────────────────────


class TestExtractUrls:
    def test_volc_url_format(self, gen_img):
        resp = {"data": [{"url": "https://x/1.png"}, {"url": "https://x/2.png"}]}
        assert gen_img.extract_volc_urls(resp) == ["https://x/1.png", "https://x/2.png"]

    def test_volc_empty_data(self, gen_img):
        assert gen_img.extract_volc_urls({"data": []}) == []
        assert gen_img.extract_volc_urls({}) == []

    def test_volc_skips_b64_json_silently(self, gen_img):
        # 当前实现只识别 url；b64_json 暂未实现 —— 记录为已知限制
        resp = {"data": [{"b64_json": "AAAA"}]}
        assert gen_img.extract_volc_urls(resp) == []  # 限制：返回空，调用方需自行处理


# ── 候选链 fallback 触发条件 ─────────────────────────────────────────────────


class TestIsModelUnavailable:
    def test_403_and_404_trigger_fallback(self, gen_img):
        for code in (403, 404):
            exc = gen_img.ImgGenHTTPError(code, "")
            assert gen_img.is_model_unavailable(exc) is True

    def test_400_with_model_keyword_triggers(self, gen_img):
        exc = gen_img.ImgGenHTTPError(400, "ModelNotFound: model not exist")
        assert gen_img.is_model_unavailable(exc) is True

    def test_400_without_keyword_does_not_trigger(self, gen_img):
        # 参数错的 400：不应当 fallback（换模型也一样错）
        exc = gen_img.ImgGenHTTPError(400, "InvalidArgument: prompt too long")
        assert gen_img.is_model_unavailable(exc) is False

    def test_500_does_not_trigger(self, gen_img):
        # 5xx 是瞬时错误，不应触发 fallback
        exc = gen_img.ImgGenHTTPError(500, "InternalError")
        assert gen_img.is_model_unavailable(exc) is False
