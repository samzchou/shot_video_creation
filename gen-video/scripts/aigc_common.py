#!/usr/bin/env python3
"""AIGC 视频生成共享模块（gen_volc.py 共用）。

  - HTTP 工具（post_json / get_json / download / HttpError）：scheme 校验 + 大小上限，统一走 _shared/safe_url
  - 任务异常（TaskFailed）
  - 资源解析（is_url / resolve_image / resolve_media_url / image_to_data_url）
  - 末帧抽取（extract_last_frame / ffprobe_duration）
  - 输出路径校验（ensure_safe_output）
  - 日志/决策审计（log / die / append_decision）
  - 候选链调度（generate / run_one 接口）

只依赖 stdlib + _shared/safe_url（无第三方包）。
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import subprocess
import sys
import time
import urllib.error
from datetime import datetime
from pathlib import Path

# 统一 URL 出入口：scheme 校验 + 大小上限（防 SSRF / DoS）
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from safe_url import (  # noqa: E402
    SafeUrlError,
    safe_get_bytes,
    safe_get_json,
    safe_post_json,
)

# ---- 输出路径安全约束 ---------------------------------------------------------

SAFE_OUTPUT_DIRS = (
    Path("output_videos"),
    Path("tmp"),
    Path("fragments"),
    Path("artifacts"),
)
# 平台运营文件夹约定：<platform>/outputs/... 同样允许（如 douyin/outputs/<work>/generations/01.mp4）

# 可重试的 HTTP 状态码（同一模型内重试，再不行才沿候选链降级）
RETRYABLE_HTTP = {408, 429, 500, 502, 503, 504}


# ---- 日志与异常 --------------------------------------------------------------


def die(message: str, code: int = 1) -> None:
    print(f"[error] {message}", file=sys.stderr)
    sys.exit(code)


def log(message: str) -> None:
    print(f"[info] {message}")


def append_decision(entry: str) -> None:
    """候选链 fallback 或全失败时往 decisions.log 追一行（审计辅助）。

    落点：workdir 下 decisions.log。append-only，不动旧内容。格式：
      ISO 时间 | 事件 | 详情
    落盘失败不阻塞主流程。
    """
    try:
        ts = datetime.now().astimezone().isoformat(timespec="seconds")
        line = f"{ts} | {entry}\n"
        with Path("decisions.log").resolve().open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


class HttpError(Exception):
    def __init__(self, code: int, body: str):
        super().__init__(f"HTTP {code}: {body}")
        self.code = code
        self.body = body


class TaskFailed(Exception):
    """任务级失败（status=failed/cancelled），不重试，直接降级到下一个候选模型。"""

    pass


# ---- HTTP --------------------------------------------------------------------


# API JSON 响应默认上限 50 MB（API 自身就 bounded，留够 buffer）
_API_MAX_BYTES = 50 * 1024 * 1024
# 视频成片默认上限 500 MB（火山 1080P/15s 约几十 MB，500 MB 留够 buffer）
_VIDEO_MAX_BYTES = 500 * 1024 * 1024


def post_json(
    url: str,
    payload: dict,
    headers: dict,
    timeout: int = 60,
    max_bytes: int = _API_MAX_BYTES,
) -> dict:
    """POST JSON；scheme 校验 + 大小上限由 _shared/safe_url 提供。

    HTTP 4xx/5xx 仍抛 HttpError（保留旧 callers 的分支能力）。
    """
    try:
        return safe_post_json(
            url, payload, headers=headers, timeout=timeout, max_bytes=max_bytes
        )
    except urllib.error.HTTPError as exc:
        # 4xx/5xx：保留 callers 的 HttpError 契约，body 透传
        body = exc.read().decode(errors="replace")
        raise HttpError(exc.code, body) from None
    except SafeUrlError as exc:
        # scheme 不合法 / Content-Length 超限 / 流式超限
        raise HttpError(0, str(exc)) from None


def get_json(
    url: str,
    headers: dict,
    timeout: int = 30,
    max_bytes: int = _API_MAX_BYTES,
) -> dict:
    """GET JSON；scheme 校验 + 大小上限由 _shared/safe_url 提供。"""
    try:
        return safe_get_json(url, headers=headers, timeout=timeout, max_bytes=max_bytes)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise HttpError(exc.code, body) from None
    except SafeUrlError as exc:
        raise HttpError(0, str(exc)) from None


def download(
    url: str,
    dest: Path,
    timeout: int = 300,
    max_bytes: int = _VIDEO_MAX_BYTES,
) -> None:
    """下载 URL 到 dest。scheme 校验 + Content-Length + 流式大小上限。

    失败抛 SafeUrlError（捕获后调用方一般 die 出来）。
    """
    log(f"downloading → {dest}")
    data = safe_get_bytes(url, timeout=timeout, max_bytes=max_bytes)
    dest.write_bytes(data)


# ---- 资源解析 ----------------------------------------------------------------


def is_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def image_to_data_url(path: Path) -> str:
    """Base64-encode a local image into a data: URL acceptable by both platforms.

    先 stat 再读，避免几十 GB 文件把进程撑爆（OOM）。
    """
    if not path.is_file():
        die(f"image file not found: {path}")
    mime, _ = mimetypes.guess_type(str(path))
    if not mime or not mime.startswith("image/"):
        die(f"unsupported image type: {path}")
    # 先 stat fast-fail（防大文件 OOM）；读后再 defense-in-depth 校验一次
    if path.stat().st_size > 30 * 1024 * 1024:
        die(f"image exceeds 30MB: {path}")
    raw = path.read_bytes()
    if len(raw) > 30 * 1024 * 1024:
        die(f"image exceeds 30MB: {path}")
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


def resolve_image(value: str) -> str:
    """Images may be a public URL or a local file (base64 data URL)."""
    if is_url(value):
        return value
    return image_to_data_url(Path(value))


def resolve_media_url(value: str, kind: str) -> str:
    """Video/audio references must be public URLs — neither platform accepts
    base64 for video/audio in a way we can reliably use, so require a URL."""
    if is_url(value):
        return value
    die(
        f"--{kind} must be a public http(s) URL; local {kind} files are not "
        f"supported (upload to OSS/TOS/a public host first). Got: {value}"
    )


# ---- ffmpeg 末帧抽取（--prev-segment 用）--------------------------------------


def ffprobe_duration(path: Path) -> float:
    """Get media duration in seconds via ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_entries",
                "format=duration",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        info = json.loads(result.stdout)
        return float(info.get("format", {}).get("duration", 0) or 0)
    except (subprocess.SubprocessError, json.JSONDecodeError, ValueError) as exc:
        die(f"ffprobe failed on {path}: {exc}")


def extract_last_frame(video_path: Path) -> Path:
    """Extract the last frame of a video to a sibling hidden .jpg.

    Used by --prev-segment: the last frame of the previous segment becomes the
    first frame of the next segment, giving首尾帧对齐 between人物故事片段.

    Strategy: try multiple ffmpeg seek strategies in order. Some AI-generated
    videos produce MP4s where the container duration is slightly larger than
    the actual stream end. We try three strategies in order and use the first
    one that produces a non-empty jpg.
    """
    if not video_path.is_file():
        die(f"--prev-segment video not found: {video_path}")
    duration = ffprobe_duration(video_path)
    if duration <= 0:
        die(f"could not determine duration for --prev-segment video: {video_path}")
    dest = video_path.with_name(f".{video_path.stem}_lastframe.jpg")
    if dest.is_file():
        dest.unlink()

    strategies: list[tuple[str, list[str]]] = [
        (
            "-sseof -1 (seek-from-end)",
            [
                "ffmpeg",
                "-y",
                "-sseof",
                "-1",
                "-i",
                str(video_path),
                "-update",
                "1",
                "-frames:v",
                "1",
                "-q:v",
                "2",
                "-an",
                str(dest),
            ],
        ),
        (
            f"-ss {max(0.0, duration - 0.5):.3f} (duration - 0.5s)",
            [
                "ffmpeg",
                "-y",
                "-i",
                str(video_path),
                "-ss",
                f"{max(0.0, duration - 0.5):.3f}",
                "-frames:v",
                "1",
                "-q:v",
                "2",
                "-an",
                str(dest),
            ],
        ),
        (
            f"-ss {max(0.0, duration - 1.0):.3f} (duration - 1.0s)",
            [
                "ffmpeg",
                "-y",
                "-i",
                str(video_path),
                "-ss",
                f"{max(0.0, duration - 1.0):.3f}",
                "-frames:v",
                "1",
                "-q:v",
                "2",
                "-an",
                str(dest),
            ],
        ),
    ]

    attempts: list[str] = []
    for label, cmd in strategies:
        try:
            result = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            attempts.append(f"[{label}] ffmpeg timed out after 60s")
            continue
        if result.returncode == 0 and dest.is_file() and dest.stat().st_size > 0:
            log(f"extracted last frame of {video_path.name} → {dest.name} (strategy: {label})")
            return dest
        tail = (result.stderr or "")[-300:]
        attempts.append(f"[{label}] rc={result.returncode} dest_exists={dest.is_file()} tail={tail!r}")
        if dest.is_file():
            dest.unlink()

    die(
        f"ffmpeg last-frame extraction failed on {video_path} "
        f"(tried {len(strategies)} strategies):\n" + "\n".join(attempts)
    )


# ---- 输出路径校验 ------------------------------------------------------------


def ensure_safe_output(raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        die(f"--output must be relative to the workspace: {raw_path}")
    if ".." in path.parts:
        die(f"--output must not contain '..': {raw_path}")
    root = Path.cwd().resolve()
    resolved = (root / path).resolve()
    under_safe_dir = any(resolved.is_relative_to((root / base).resolve()) for base in SAFE_OUTPUT_DIRS)
    under_platform_outputs = "outputs" in path.parts[:-1]
    if not (under_safe_dir or under_platform_outputs):
        die(
            f"--output must be under one of: {', '.join(str(d) for d in SAFE_OUTPUT_DIRS)}, "
            f"or a platform ops folder <platform>/outputs/"
        )
    return resolved


# ---- --prev-segment 预处理 ---------------------------------------------------


def resolve_prev_segment(args: argparse.Namespace) -> Path | None:
    """处理 --prev-segment：抽取上一段视频末帧，设为 args.image。

    人物故事模式 A.1 首尾帧对齐：每段从上一段末帧开始。
    与 --image 互斥（首帧由上一段末帧决定）。
    返回抽取的末帧临时文件路径（用于审计），未传 --prev-segment 时返回 None。
    """
    if not getattr(args, "prev_segment", None):
        return None
    if getattr(args, "image", None):
        die("--prev-segment 与 --image 互斥：首帧由上一段末帧决定")
    prev_segment_frame = extract_last_frame(Path(args.prev_segment))
    args.image = str(prev_segment_frame)
    return prev_segment_frame


# ---- 候选链调度 --------------------------------------------------------------


def generate(
    platform: str,
    candidates: list[str],
    args: argparse.Namespace,
    api_key: str,
    run_one_fn,
) -> str:
    """Try candidate models in order. HttpError/TaskFailed trigger fallback
    unless the user explicitly pinned --model (then only transient retries).

    run_one_fn: callable(platform, model, args, api_key) -> video_url
    """
    pinned = args.model is not None
    models = candidates
    last_err = ""
    for idx, model in enumerate(models):
        for attempt in range(1, 4):  # up to 3 transient retries per model
            try:
                return run_one_fn(platform, model, args, api_key)
            except TaskFailed as exc:
                last_err = str(exc)
                log(f"model {model} task failed: {last_err}")
                break  # task-level failure → fall back to next model, no retry
            except HttpError as exc:
                last_err = str(exc)
                if exc.code in RETRYABLE_HTTP and attempt < 3:
                    log(f"model {model} HTTP {exc.code}, retrying ({attempt}/2)")
                    time.sleep(3 * attempt)
                    continue
                log(f"model {model} submit error: {last_err}")
                break  # fall back to next model
        if pinned:
            break  # respect explicit user choice — no chain walk
        if idx < len(models) - 1:
            next_model = models[idx + 1]
            log(f"falling back to next model: {next_model}")
            append_decision(f"fallback | {model} → {next_model} | reason: {last_err}")
    append_decision(f"all candidates exhausted | models: {','.join(models)} | last error: {last_err}")
    die(f"all model attempts failed; last error: {last_err}")
