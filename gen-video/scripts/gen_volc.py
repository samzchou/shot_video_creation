#!/usr/bin/env python3
"""火山引擎方舟（Volcengine Ark）Seedance 视频生成。

直连火山异步任务端点：
  - POST /api/v3/contents/generations/tasks   → 创建任务，返回 task_id
  - GET  /api/v3/contents/generations/tasks/{task_id} → 轮询 status
  - 成功时 content.video_url 即成片下载地址。

模型：doubao-seedance-2-0 系列（fast / normal / mini，均多模态 t2v/i2v/r2v）。
鉴权：HTTP header `Authorization: Bearer ${VIDEO_KEY}`。

⚠️ 火山视频生成只认 VIDEO_KEY，不回退 ARK_API_KEY：ARK_API_KEY 是火山主模型
（doubao 对话）的 key，用户可能只想用火山主模型而不用火山生成视频；若回退会
误触发火山视频生成。想用火山生成视频必须单独配 VIDEO_KEY。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# aigc_common.py 与本脚本同目录（scripts/）；cred.py 用共享 _shared/
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from dotenv import load_env

load_env()  # noqa: E402  # 注入 .env 到 os.environ（在 cred 之前）
import cred as _cred  # noqa: E402
from aigc_common import (  # noqa: E402
    TaskFailed,
    die,
    download,
    ensure_safe_output,
    generate,
    get_json,
    log,
    post_json,
    resolve_image,
    resolve_media_url,
    resolve_prev_segment,
)

_cred.CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"
from cred import get as cred_get  # noqa: E402

# ---- 火山端点与常量 -----------------------------------------------------------

VOLC_BASE = "https://ark.cn-beijing.volces.com/api/v3"
VOLC_CREATE = f"{VOLC_BASE}/contents/generations/tasks"
VOLC_QUERY = f"{VOLC_BASE}/contents/generations/tasks/{{task_id}}"

# Seedance 2.0 series. Fast preferred → normal → mini. All three are multimodal
# (t2v / i2v / r2v share the same model id).
VOLC_MODELS = {
    "fast": "doubao-seedance-2-0-fast-260128",
    "normal": "doubao-seedance-2-0-260128",
    "mini": "doubao-seedance-2-0-mini-260615",
}

VOLC_POLL_INTERVAL = 15
VOLC_TIMEOUT = 900


# ---- 火山视频生成 -------------------------------------------------------------


def volc_build_content(args: argparse.Namespace) -> list[dict]:
    items: list[dict] = [{"type": "text", "text": args.prompt}]
    if args.image:
        items.append(
            {"type": "image_url", "image_url": {"url": resolve_image(args.image)}, "role": "first_frame"}
        )
    if args.last_frame:
        items.append(
            {"type": "image_url", "image_url": {"url": resolve_image(args.last_frame)}, "role": "last_frame"}
        )
    if args.ref_image:
        items.append(
            {
                "type": "image_url",
                "image_url": {"url": resolve_image(args.ref_image)},
                "role": "reference_image",
            }
        )
    if args.ref_video:
        items.append(
            {"type": "video_url", "video_url": {"url": resolve_media_url(args.ref_video, "ref-video")}}
        )
    return items


def volc_submit(model: str, args: argparse.Namespace, api_key: str) -> str:
    payload: dict = {
        "model": model,
        "content": volc_build_content(args),
        "ratio": args.ratio,
        "duration": args.duration,
        "resolution": args.resolution.lower(),
        "generate_audio": args.audio,
        "watermark": False,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    resp = post_json(VOLC_CREATE, payload, headers, timeout=60)
    task_id = resp.get("id") or resp.get("task_id")
    if not task_id:
        die(f"volcengine submit: no task id in response: {json.dumps(resp, ensure_ascii=False)}")
    return task_id


def volc_poll(task_id: str, api_key: str) -> str:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    url = VOLC_QUERY.format(task_id=task_id)
    deadline = time.time() + VOLC_TIMEOUT
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        resp = get_json(url, headers, timeout=30)
        status = resp.get("status", "")
        log(f"volc poll #{attempt}: status={status}")
        if status == "succeeded":
            video_url = (resp.get("content") or {}).get("video_url")
            if not video_url:
                die(f"volcengine succeeded but no video_url: {json.dumps(resp, ensure_ascii=False)}")
            return video_url
        if status in {"failed", "cancelled", "expired"}:
            err = resp.get("error") or {}
            raise TaskFailed(f"volcengine task {status}: {err.get('code', '')} {err.get('message', '')}")
        time.sleep(VOLC_POLL_INTERVAL)
    die(f"volcengine timed out after {VOLC_TIMEOUT}s (task {task_id})")


def volc_candidates(args: argparse.Namespace) -> list[str]:
    chain = [VOLC_MODELS["fast"], VOLC_MODELS["normal"], VOLC_MODELS["mini"]]
    # fast only supports 720p; skip it for 1080p
    if args.resolution.lower() == "1080p":
        chain = [m for m in chain if m != VOLC_MODELS["fast"]]
    return chain


# ---- 调度 --------------------------------------------------------------------


def run_one(platform: str, model: str, args: argparse.Namespace, api_key: str) -> str:
    """Submit + poll for a single Volcengine model. Returns video URL or raises."""
    task_id = volc_submit(model, args, api_key)
    log(f"volcengine task submitted: {task_id} (model={model})")
    return volc_poll(task_id, api_key)


def cmd_video(args: argparse.Namespace) -> None:
    if args.duration < 2 or args.duration > 15:
        die("--duration 必须在 2–15 秒之间")

    # --prev-segment: 抽取上一段末帧作为本段首帧（人物故事首尾帧对齐）
    resolve_prev_segment(args)

    api_key = cred_get("api_key", env="VIDEO_KEY")
    if not api_key:
        die(
            "火山视频生成凭据未配置：请在技能目录 config.json 填写 api_key（方舟 API Key），或设置环境变量 VIDEO_KEY（火山视频生成专用 key，不可与 ARK_API_KEY 混用）"
        )

    candidates = [args.model] if args.model else volc_candidates(args)

    output_path = ensure_safe_output(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    has_ref = bool(args.ref_image or args.ref_video)
    has_i2v = bool(args.image or args.last_frame)
    mode = "r2v" if has_ref else ("i2v" if has_i2v else "t2v")
    log(
        f"platform=volcengine mode={mode} candidates={candidates} "
        f"duration={args.duration}s ratio={args.ratio} resolution={args.resolution} audio={args.audio}"
    )
    video_url = generate("volcengine", candidates, args, api_key, run_one)
    download(video_url, output_path)

    meta = output_path.with_suffix(".json")
    meta.write_text(
        json.dumps(
            {
                "platform": "volcengine",
                "mode": mode,
                "model_candidates": candidates,
                "duration": args.duration,
                "ratio": args.ratio,
                "resolution": args.resolution,
                "audio": args.audio,
                "video_url": video_url,
                "file": str(output_path),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[done] video saved: {output_path}")
    print(f"[done] metadata:    {meta}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Volcengine Seedance video generation.")
    sub = parser.add_subparsers(dest="command")

    # ── 默认子命令：video ──
    p_video = sub.add_parser("video", help="视频生成(默认,可省略 video 子命令)")
    p_video.add_argument("--prompt", required=True, help="画面+音频描述（声画同出）")
    p_video.add_argument("--image", default=None, help="首帧图片：URL 或本地路径（→ i2v）")
    p_video.add_argument(
        "--prev-segment",
        default=None,
        dest="prev_segment",
        help="上一段视频本地路径：脚本自动抽取其末帧作为本段首帧（人物故事首尾帧对齐）。与 --image 互斥",
    )
    p_video.add_argument(
        "--last-frame", default=None, dest="last_frame", help="尾帧图片：URL 或本地路径（i2v 首尾帧）"
    )
    p_video.add_argument(
        "--ref-image", default=None, dest="ref_image", help="参考图片：URL 或本地路径（→ r2v）"
    )
    p_video.add_argument(
        "--ref-video", default=None, dest="ref_video", help="参考视频 URL（→ r2v，需公网 URL）"
    )
    p_video.add_argument("--duration", type=int, default=8, help="时长（秒），默认 8，火山范围 2–15")
    p_video.add_argument("--ratio", default="9:16", help="宽高比，默认 9:16")
    p_video.add_argument(
        "--resolution", default="720P", choices=["720P", "1080P"], help="分辨率，默认 720P（Fast 仅 720P）"
    )
    p_video.add_argument("--no-audio", action="store_false", dest="audio", help="关闭声画同出（默认开启）")
    p_video.add_argument("--model", default=None, help="指定模型 id（关闭候选链 fallback）")
    p_video.add_argument(
        "--output",
        required=True,
        help="输出 MP4 路径（相对工作区，须在 output_videos/tmp/fragments/artifacts 或 <platform>/outputs/ 下）",
    )

    return parser


def main() -> None:
    parser = build_parser()

    # 向后兼容：无子命令时，把全部 argv 当成 video 子命令的参数
    argv = sys.argv[1:]
    if not argv or argv[0].startswith("-"):
        argv = ["video"] + argv

    # video 子命令可省略：如果第一个 token 不是已知子命令，默认走 video
    known_subcommands = {"video"}
    if argv and argv[0] not in known_subcommands:
        argv = ["video"] + argv

    args = parser.parse_args(argv)
    cmd_video(args)


if __name__ == "__main__":
    main()
