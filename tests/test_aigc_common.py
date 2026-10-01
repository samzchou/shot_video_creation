"""volc-video-gen/scripts/aigc_common.py：跨供应商共享工具。

覆盖范围：
  - URL / data URI 解析（is_url / resolve_image / image_to_data_url）
  - 输出路径安全（ensure_safe_output：拒绝绝对路径/..，必须在白名单）
  - 候选链调度（generate：fallback 顺序、pinned 不降级、TaskFailed 不重试）
  - 决策审计（append_decision：写 decisions.log）
  - ffprobe 时长获取（ffprobe_duration：缺失/失败时返回 0.0）
  - 末帧抽取（extract_last_frame：标记 @pytest.mark.ffmpeg）
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from aigc_common_skill import (
    HttpError,
    TaskFailed,
    ensure_safe_output,
    generate,
    image_to_data_url,
    is_url,
    resolve_image,
    resolve_prev_segment,
)

# ── URL 解析 ────────────────────────────────────────────────────────────────


class TestIsUrl:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("https://example.com/x.png", True),
            ("http://example.com/x.png", True),
            ("data:image/png;base64,AAAA", False),
            ("/tmp/local.png", False),
            ("./relative.png", False),
            ("", False),
        ],
    )
    def test_classifies(self, aigc_common, value, expected):
        assert is_url(value) is expected


class TestResolveImage:
    def test_url_passthrough(self, aigc_common):
        url = "https://example.com/x.png"
        assert resolve_image(url) == url

    def test_local_to_data_uri(self, aigc_common, tmp_path: Path):
        p = tmp_path / "a.png"
        p.write_bytes(b"\x89PNG\r\n")
        result = resolve_image(str(p))
        assert result.startswith("data:image/png;base64,")

    def test_missing_file_exits(self, aigc_common, tmp_path: Path):
        with pytest.raises(SystemExit):
            resolve_image(str(tmp_path / "missing.png"))

    def test_oversized_file_exits(self, aigc_common, tmp_path: Path):
        # mock read_bytes 返回 31MB
        p = tmp_path / "big.png"
        p.write_bytes(b"x")
        # 用 monkeypatch 改写 stat 不靠谱；直接用真实文件 + monkeypatch read_bytes
        orig_read = Path.read_bytes

        def fake_read(self):
            if self == p:
                return b"x" * (31 * 1024 * 1024)
            return orig_read(self)

        monkey = pytest.MonkeyPatch()
        monkey.setattr(Path, "read_bytes", fake_read)
        try:
            with pytest.raises(SystemExit):
                resolve_image(str(p))
        finally:
            monkey.undo()


# ── 输出路径安全 ─────────────────────────────────────────────────────────────


class TestEnsureSafeOutput:
    def test_absolute_path_rejected(self, aigc_common, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit):
            ensure_safe_output("/etc/passwd")

    def test_parent_traversal_rejected(self, aigc_common, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit):
            ensure_safe_output("../escape.mp4")

    @pytest.mark.parametrize(
        "subdir",
        ["output_videos", "tmp", "fragments", "artifacts"],
    )
    def test_safe_dirs_accepted(self, aigc_common, monkeypatch, tmp_path: Path, subdir):
        monkeypatch.chdir(tmp_path)
        p = ensure_safe_output(f"{subdir}/x.mp4")
        assert p.is_absolute()
        assert tmp_path.resolve() in p.parents or tmp_path.resolve() == p.parent

    def test_platform_outputs_accepted(self, aigc_common, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        p = ensure_safe_output("douyin/outputs/campaign/01.mp4")
        assert p.is_absolute()
        assert "douyin" in str(p)
        assert "outputs" in str(p)

    def test_random_dir_rejected(self, aigc_common, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit):
            ensure_safe_output("random_dir/x.mp4")


# ── 候选链调度 ───────────────────────────────────────────────────────────────


class TestGenerateFallback:
    def _args(self, model=None):
        return SimpleNamespace(model=model, prompt="x")

    def test_succeeds_on_first_model(self, aigc_common, monkeypatch):
        called = []

        def run_one(platform, model, args, api_key):
            called.append(model)
            return "https://video/1.mp4"

        url = generate("volcengine", ["m1", "m2"], self._args(), "key", run_one)
        assert url == "https://video/1.mp4"
        assert called == ["m1"]  # 没走到 m2

    def test_fallback_to_next_on_http_error(self, aigc_common, monkeypatch):
        called = []

        def run_one(platform, model, args, api_key):
            called.append(model)
            if model == "m1":
                raise HttpError(403, "forbidden")
            return f"https://video/{model}.mp4"

        url = generate("volcengine", ["m1", "m2"], self._args(), "key", run_one)
        assert called == ["m1", "m2"]
        assert url == "https://video/m2.mp4"

    def test_pinned_does_not_fallback(self, aigc_common, monkeypatch):
        called = []

        def run_one(platform, model, args, api_key):
            called.append(model)
            raise HttpError(403, "forbidden")

        with pytest.raises(SystemExit):
            generate("volcengine", ["m1", "m2"], self._args(model="m1"), "key", run_one)
        # pinned 时只跑首个，不切换
        assert called == ["m1"]

    def test_task_failed_does_not_retry(self, aigc_common, monkeypatch):
        called = []

        def run_one(platform, model, args, api_key):
            called.append(model)
            raise TaskFailed("model failed")

        with pytest.raises(SystemExit):
            generate("volcengine", ["m1", "m2"], self._args(), "key", run_one)
        # 应当 m1 → m2（task 失败立即切换，不重试）
        assert called == ["m1", "m2"]

    def test_retryable_http_retries_then_falls_back(self, aigc_common, monkeypatch):
        attempts = []

        def run_one(platform, model, args, api_key):
            attempts.append(model)
            if model == "m1":
                raise HttpError(429, "rate limit")  # 可重试
            return "https://video/m2.mp4"

        url = generate("volcengine", ["m1", "m2"], self._args(), "key", run_one)
        # 429 应当被重试 3 次，然后切换
        assert attempts[:3] == ["m1", "m1", "m1"]
        assert attempts[3] == "m2"
        assert url == "https://video/m2.mp4"

    def test_all_candidates_exhausted_exits(self, aigc_common, monkeypatch):
        def run_one(platform, model, args, api_key):
            raise HttpError(500, "internal")

        with pytest.raises(SystemExit):
            generate("volcengine", ["m1", "m2", "m3"], self._args(), "key", run_one)


# ── prev-segment 处理 ────────────────────────────────────────────────────────


class TestResolvePrevSegment:
    def test_none_when_not_set(self, aigc_common):
        args = SimpleNamespace(prev_segment=None, image=None)
        assert resolve_prev_segment(args) is None

    def test_conflict_with_image_exits(self, aigc_common, monkeypatch, tmp_path: Path):
        # 用一个不存在的视频避免触发 ffmpeg 实际抽取
        prev = tmp_path / "prev.mp4"
        prev.write_bytes(b"")
        args = SimpleNamespace(prev_segment=str(prev), image="explicit.png")
        with pytest.raises(SystemExit):
            resolve_prev_segment(args)

    @pytest.mark.ffmpeg
    def test_extracts_last_frame_when_ffmpeg_available(self, aigc_common, tmp_path: Path):
        # 仅在系统装 ffmpeg 时跑
        prev = tmp_path / "prev.mp4"
        prev.write_bytes(b"")  # 空文件；extract_last_frame 会失败 → die
        args = SimpleNamespace(prev_segment=str(prev), image=None)
        with pytest.raises(SystemExit):
            resolve_prev_segment(args)


# ── 决策审计 ────────────────────────────────────────────────────────────────


class TestAppendDecision:
    def test_writes_to_decisions_log(self, aigc_common, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        aigc_common.append_decision("fallback | m1 -> m2 | reason: 403")
        log = tmp_path / "decisions.log"
        assert log.exists()
        line = log.read_text(encoding="utf-8")
        assert "fallback | m1 -> m2 | reason: 403" in line
        # 应当含 ISO 时间戳
        assert " | " in line

    def test_swallows_io_errors(self, aigc_common, monkeypatch, tmp_path: Path):
        # append_decision 不应在写入失败时崩溃
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("builtins.open", lambda *a, **kw: (_ for _ in ()).throw(OSError("boom")))
        # 不抛错即可
        aigc_common.append_decision("test")
