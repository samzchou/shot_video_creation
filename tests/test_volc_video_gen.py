"""volc-video-gen/scripts/gen_volc.py：火山视频生成核心逻辑。

覆盖范围：
  - 候选链路由（volc_candidates：1080p 剔除 fast）
  - content 组合（volc_build_content：role 标注）
  - argparse：video 子命令解析 + 隐式默认
  - 关键常量（端点 / 模型 ID / 超时）
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

# ── 候选链路由 ───────────────────────────────────────────────────────────────


class TestVolcCandidates:
    def test_default_chain_includes_fast(self, gen_volc):
        args = SimpleNamespace(resolution="720P")
        chain = gen_volc.volc_candidates(args)
        assert chain[0] == "doubao-seedance-2-0-fast-260128"
        assert "doubao-seedance-2-0-260128" in chain
        assert "doubao-seedance-2-0-mini-260615" in chain

    def test_1080p_skips_fast(self, gen_volc):
        args = SimpleNamespace(resolution="1080P")
        chain = gen_volc.volc_candidates(args)
        assert "doubao-seedance-2-0-fast-260128" not in chain
        assert "doubao-seedance-2-0-260128" in chain
        assert "doubao-seedance-2-0-mini-260615" in chain

    def test_1080p_lowercase(self, gen_volc):
        # 防御：分辨率输入大小写不应影响剔除
        args = SimpleNamespace(resolution="1080p")
        chain = gen_volc.volc_candidates(args)
        assert "doubao-seedance-2-0-fast-260128" not in chain


# ── content 组合 ─────────────────────────────────────────────────────────────


class TestVolcBuildContent:
    def _args(self, **kw):
        base = dict(prompt="test", image=None, last_frame=None, ref_image=None, ref_video=None)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_text_only(self, gen_volc):
        items = gen_volc.volc_build_content(self._args())
        assert len(items) == 1
        assert items[0]["type"] == "text"
        assert items[0]["text"] == "test"

    def test_first_frame(self, gen_volc):
        items = gen_volc.volc_build_content(self._args(image="https://x/a.png"))
        assert len(items) == 2
        assert items[1]["role"] == "first_frame"
        assert items[1]["type"] == "image_url"
        assert items[1]["image_url"]["url"] == "https://x/a.png"

    def test_last_frame(self, gen_volc):
        items = gen_volc.volc_build_content(self._args(image="https://x/a.png", last_frame="https://x/b.png"))
        roles = [item.get("role") for item in items]
        assert "first_frame" in roles
        assert "last_frame" in roles

    def test_reference_image(self, gen_volc):
        items = gen_volc.volc_build_content(self._args(ref_image="https://x/ref.png"))
        ref_items = [i for i in items if i.get("role") == "reference_image"]
        assert len(ref_items) == 1
        assert ref_items[0]["type"] == "image_url"

    def test_reference_video_url_only(self, gen_volc, monkeypatch):
        # 引用视频走 resolve_media_url；mock 一下避免创建真实 data URI
        def fake_resolve(value, kind):
            return f"https://resolved/{value}"

        monkeypatch.setattr(gen_volc, "resolve_media_url", fake_resolve)
        items = gen_volc.volc_build_content(self._args(ref_video="https://x/v.mp4"))
        ref_items = [i for i in items if i["type"] == "video_url"]
        assert len(ref_items) == 1
        assert ref_items[0]["video_url"]["url"] == "https://resolved/https://x/v.mp4"

    def test_local_image_resolved_via_resolve_image(self, gen_volc, tmp_path):
        img = tmp_path / "frame.png"
        img.write_bytes(b"\x89PNG")
        items = gen_volc.volc_build_content(self._args(image=str(img)))
        # 本地路径会被 resolve_image 转 data URI
        assert items[1]["image_url"]["url"].startswith("data:image/png;base64,")


# ── argparse ─────────────────────────────────────────────────────────────────


class TestBuildParser:
    def test_video_subcommand_required_fields(self, gen_volc):
        parser = gen_volc.build_parser()
        # 缺 --prompt 应 SystemExit
        with pytest.raises(SystemExit):
            parser.parse_args(["video", "--output", "tmp/x.mp4"])

    def test_video_subcommand_defaults(self, gen_volc):
        parser = gen_volc.build_parser()
        args = parser.parse_args(["video", "--prompt", "p", "--output", "tmp/x.mp4"])
        assert args.duration == 8
        assert args.ratio == "9:16"
        assert args.resolution == "720P"
        assert args.audio is True  # store_false 默认 True
        assert args.image is None
        assert args.last_frame is None

    def test_no_audio_flag(self, gen_volc):
        parser = gen_volc.build_parser()
        args = parser.parse_args(["video", "--prompt", "p", "--output", "tmp/x.mp4", "--no-audio"])
        assert args.audio is False

    def test_1080p_resolution_accepted(self, gen_volc):
        parser = gen_volc.build_parser()
        args = parser.parse_args(["video", "--prompt", "p", "--output", "tmp/x.mp4", "--resolution", "1080P"])
        assert args.resolution == "1080P"


# ── 常量 ─────────────────────────────────────────────────────────────────────


class TestConstants:
    def test_endpoint_path(self, gen_volc):
        assert gen_volc.VOLC_CREATE == "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks"
        assert "{task_id}" in gen_volc.VOLC_QUERY

    def test_model_ids(self, gen_volc):
        assert gen_volc.VOLC_MODELS["fast"].startswith("doubao-seedance-2-0-fast")
        assert gen_volc.VOLC_MODELS["normal"].startswith("doubao-seedance-2-0-")
        assert gen_volc.VOLC_MODELS["mini"].startswith("doubao-seedance-2-0-mini")

    def test_timeouts(self, gen_volc):
        # 文档承诺：轮询 15s，最长 900s
        assert gen_volc.VOLC_POLL_INTERVAL == 15
        assert gen_volc.VOLC_TIMEOUT == 900
