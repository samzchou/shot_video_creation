#!/usr/bin/env python3
"""图像生成/编辑（火山方舟 Seedream）— stdlib only。

凭据：火山方舟（唯一来源）。.env 中 IMG_API_KEY（必填）/ IMG_MODEL（可选）。
模型：缺省 doubao-seedream-5-0-260128；编辑模式（传 --image）支持 1-3 张参考图融合。

参考：docs.volcengine.com/docs/82379（Seedream 图像生成）。
"""

import argparse
import base64
import json
import mimetypes
import re
import sys
import time
from pathlib import Path

# 凭据读取：使用共享 _shared/cred.py，配置来源统一为根目录 .env
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from dotenv import load_env

load_env()  # noqa: E402  # 注入 .env 到 os.environ（在 cred 之前）

from safe_url import SafeUrlError, safe_get_bytes, safe_post_json  # noqa: E402

from cred import get as cred_get  # noqa: E402

# ── 端点与模型 ────────────────────────────────────────────────────────────────

VOLC_BASE = "https://ark.cn-beijing.volces.com/api/v3"
VOLC_IMG_PATH = "/images/generations"
# 默认模型：方舟控制台「开通管理」中需可调用；可在 .env 中 IMG_MODEL 覆盖
DEFAULT_VOLC_MODEL = "doubao-seedream-5-0-260128"

# 触发候选链 fallback 的 HTTP 状态码（模型未开通 / 未找到 / 无权限）
MODEL_UNAVAILABLE_CODES = {403, 404}
# 400 需结合 body 判断（可能是模型不存在，也可能是参数错——参数错不 fallback）
MODEL_ERROR_BODY_HINTS = (
    "modelnotfound",
    "model not found",
    "not exist",
    "unsupported model",
    "access denied",
)

REQUEST_TIMEOUT = 300

IMAGE_MIME_BY_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
}

# ── size 校验（火山方舟 Seedream：总像素 [512², 2048²]，宽高比 [1/8, 8]）──
MIN_TOTAL_PIXELS = 512 * 512  # 262144
MAX_TOTAL_PIXELS = 2048 * 2048  # 4194304
MIN_ASPECT_RATIO = 1 / 8
MAX_ASPECT_RATIO = 8

# ── size 处理 ────────────────────────────────────────────────────────────────


def _print_size_error(size_str: str, reason: str) -> None:
    print(f"[error] --image-size '{size_str}' 无效：{reason}", file=sys.stderr)
    print("[info] 推荐尺寸：WxH 形式（如 2048x2048），'2K' / '4K' / 'auto'。", file=sys.stderr)


def _parse_size(size_str: str) -> tuple[int, int] | None:
    """解析 WxH / W*H / W×H 字符串。失败返回 None。"""
    m = re.match(r"^\s*(\d+)\s*[xX×*]\s*(\d+)\s*$", size_str)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def size_to_volc(size_str: str) -> str | None:
    """把 --image-size 转成火山方舟 size 参数：'WxH' / '2K' / '4K'；'auto' 返回 None（不传）。"""
    s = (size_str or "").strip()
    low = s.lower()
    if low == "auto":
        return None
    if low in ("2k", "4k"):
        return s
    parsed = _parse_size(s)
    if parsed:
        w, h = parsed
        total = w * h
        ratio = w / h if h else 0
        if MIN_TOTAL_PIXELS <= total <= MAX_TOTAL_PIXELS and MIN_ASPECT_RATIO <= ratio <= MAX_ASPECT_RATIO:
            return f"{w}x{h}"
    _print_size_error(
        size_str, "格式必须是 'WxH'/'W*H'（总像素 512²~2048²，宽高比 1/8~8）、'2K'/'4K' 或 'auto'"
    )
    sys.exit(1)


# ── 图像引用解析 ──────────────────────────────────────────────────────────────


def resolve_image_ref(value: str) -> str:
    """把 --image 入参解析为方舟可接受的引用：URL / data URI 原样，本地文件转 data URI。

    本地文件先 stat，超 30MB 直接拒，避免大图 OOM。
    """
    if value.startswith(("http://", "https://", "data:")):
        return value
    path = Path(value)
    if not path.is_file():
        print(f"[error] 参考图不存在: {value}", file=sys.stderr)
        sys.exit(1)
    if path.stat().st_size > 30 * 1024 * 1024:
        print(f"[error] 参考图超过 30MB: {value}", file=sys.stderr)
        sys.exit(1)
    mime = IMAGE_MIME_BY_EXT.get(path.suffix.lower()) or mimetypes.guess_type(str(path))[0] or "image/png"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def build_volc_payload(args: argparse.Namespace, model: str) -> dict:
    """构造火山方舟 images/generations 请求体（OpenAI 兼容，模型由调用方传入）。"""
    payload: dict = {
        "model": model,
        "prompt": args.prompt,
        "watermark": bool(args.watermark),
        "response_format": "url",
        "output_format": "png",
    }
    if args.seed is not None:
        payload["seed"] = args.seed
    # 编辑模式：1-3 张参考图走 image 数组（Seedream i2i/多图融合）
    refs = [r for r in (args.image, args.image2, args.image3) if r]
    if refs:
        payload["image"] = [resolve_image_ref(r) for r in refs]
    # size 决策：
    #   - 用户显式传 --image-size → 用 size_to_volc 校验
    #   - 文生图未传 → 默认 "2K"
    #   - 编辑模式未传 → 不附带（跟随参考图；旧代码在此分支调用 size_to_volc(None) 触发 SystemExit，现修复）
    if args.image_size:
        size = size_to_volc(args.image_size)
    elif refs:
        size = None  # 编辑模式跟随参考图
    else:
        size = "2K"  # 文生图默认
    if size:
        payload["size"] = size
    return payload


# ── API 调用 ────────────────────────────────────────────────────────────────


class ImgGenHTTPError(Exception):
    """HTTP 错误（携带状态码与响应体，供 main 做候选链 fallback 决策）。"""

    def __init__(self, code: int, body: str) -> None:
        super().__init__(f"HTTP {code}: {body}")
        self.code = code
        self.body = body


def is_model_unavailable(exc: ImgGenHTTPError) -> bool:
    """判断 HTTP 错误是否属于"模型层不可用"（可沿候选链 fallback）。

    403/404 直接算；400 需 body 命中模型类错误关键词（参数错的 400 不 fallback，
    换模型也一样错，快速失败暴露真实原因）。
    """
    if exc.code in MODEL_UNAVAILABLE_CODES:
        return True
    if exc.code == 400:
        lowered = exc.body.lower()
        return any(hint in lowered for hint in MODEL_ERROR_BODY_HINTS)
    return False


def api_request(url: str, payload: dict, api_key: str) -> dict:
    """调火山方舟 images/generations；返回解析后的 JSON。失败抛 ImgGenHTTPError。

    scheme 校验 + 大小上限由 _shared/safe_url 提供（50MB 上限，足够任何
    OpenAI 兼容 images/generations 响应）。
    """
    try:
        return safe_post_json(
            url,
            payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=REQUEST_TIMEOUT,
            max_bytes=50 * 1024 * 1024,
        )
    except SafeUrlError as exc:
        raise ImgGenHTTPError(0, str(exc)) from None


def extract_volc_urls(resp: dict) -> list[str]:
    """从火山方舟响应提取图片 URL：data[*].url（OpenAI 兼容）。"""
    urls: list[str] = []
    for item in resp.get("data") or []:
        if isinstance(item, dict) and item.get("url"):
            urls.append(item["url"])
    return urls


# ── 图像下载 ────────────────────────────────────────────────────────────────


def download_image(url: str, dest_path: Path) -> None:
    """下载图片到本地。链接 24h 内有效（火山方舟文档）。

    50MB 上限（远大于方舟实际返回的图片大小）。
    scheme 校验 + Content-Length + 流式大小上限由 _shared/safe_url 提供。
    """
    data = safe_get_bytes(url, timeout=120, max_bytes=50 * 1024 * 1024)
    dest_path.write_bytes(data)


def _print_enable_guide(failed_model: str) -> None:
    """候选链全部不可用时，输出开通指引（供 Agent 转告用户）。"""
    print("", file=sys.stderr)
    print(f"[error] 图像生成模型 {failed_model} 不可用，候选链已全部尝试。", file=sys.stderr)
    print("[guide] 请检查火山方舟账号开通状态：", file=sys.stderr)
    print("  1. 打开 https://console.volcengine.com/ark（方舟控制台）", file=sys.stderr)
    print(
        f"  2. 在「开通管理」确认模型可调用（当前模型：{failed_model}；可改 .env 中 IMG_MODEL）",
        file=sys.stderr,
    )
    print("  3. 确认 .env 中 IMG_API_KEY 有效（方舟 API Key）", file=sys.stderr)
    print(
        "[hint] 免 key 实拍图片可退公共技能 pexels-footage / pixabay-footage（非 AI 生成）。", file=sys.stderr
    )


# ── main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="图像生成/编辑（火山方舟 Seedream）")
    parser.add_argument("--prompt", required=True, help="图像描述（要渲染的文字直接写完整句子）")
    parser.add_argument(
        "--model",
        default=None,
        help="Model ID（缺省按供应商走默认/候选链；显式指定时不 fallback）",
    )
    parser.add_argument(
        "--image-size",
        default=None,
        dest="image_size",
        help="尺寸：'WxH'（如 2048x2048，总像素 512²~2048²）、'2K'/'4K' 或 'auto'；缺省文生图 2K",
    )
    parser.add_argument(
        "--seed", type=int, default=None, help="随机种子 [0, 2147483647]（火山范围 [-1, 2147483647]）"
    )
    parser.add_argument(
        "--watermark",
        choices=["true", "false"],
        default="false",
        help="是否加水印（默认 false，避免影响后续图像处理）",
    )
    parser.add_argument(
        "--prompt-extend",
        action="store_true",
        dest="prompt_extend",
        help="允许方舟自动扩写 prompt（默认关，保证封面文字/布局指令精确）",
    )
    # image-edit inputs（URL / data URI / 本地文件路径均可）
    parser.add_argument("--image", default=None, help="参考图 1：URL 或本地路径（启用编辑模式）")
    parser.add_argument("--image2", default=None, help="参考图 2（编辑模式，多图融合）")
    parser.add_argument("--image3", default=None, help="参考图 3（编辑模式，多图融合）")
    parser.add_argument("--out-dir", default=None, dest="out_dir", help="输出目录")
    args = parser.parse_args()

    # 凭据：火山方舟唯一来源
    api_key = cred_get("api_key", env="IMG_API_KEY")
    if not api_key:
        print("[error] 图像生成凭据未配置：", file=sys.stderr)
        print("  - .env 中 IMG_API_KEY（方舟 API Key）", file=sys.stderr)
        print("  - 火山方舟：https://console.volcengine.com/ark → 「API Key 管理」", file=sys.stderr)
        sys.exit(1)
    model = cred_get("model", env="IMG_MODEL", default=DEFAULT_VOLC_MODEL) or DEFAULT_VOLC_MODEL

    # watermark 字段期望 bool（JSON），从字符串转
    args.watermark = args.watermark == "true"

    ts = int(time.time())
    out_dir = Path(args.out_dir) if args.out_dir else Path(f"./tmp/awk-img-{ts}")
    out_dir.mkdir(parents=True, exist_ok=True)

    # 候选模型：用户显式 --model 时不 fallback；否则仅默认
    candidates = [args.model] if args.model else [model]
    is_edit_mode = bool(args.image)
    gen_mode = "image-edit" if is_edit_mode else "text-to-image"

    url = f"{VOLC_BASE}{VOLC_IMG_PATH}"
    result: dict | None = None
    for idx, cand_model in enumerate(candidates):
        payload = build_volc_payload(args, cand_model)
        size = payload.get("size") or "-"
        print(f"[info] Mode={gen_mode} provider=volc model={cand_model} size={size}", file=sys.stderr)
        try:
            result = api_request(url, payload, api_key)
            break
        except ImgGenHTTPError as e:
            print(f"[error] HTTP {e.code}: {e.body[:500]}", file=sys.stderr)
            is_last = idx == len(candidates) - 1
            if is_model_unavailable(e) and not is_last:
                print(
                    f"[warn] model {cand_model} 不可用 (HTTP {e.code})，切换候选链下一个...", file=sys.stderr
                )
                continue
            if is_model_unavailable(e):
                _print_enable_guide(cand_model)
            sys.exit(1)

    if result is None:
        _print_enable_guide(candidates[-1])
        sys.exit(1)

    # 火山：data[*].url
    image_urls = extract_volc_urls(result)
    if not image_urls:
        print(f"[error] 响应中无图片 URL: {json.dumps(result, ensure_ascii=False)[:800]}", file=sys.stderr)
        sys.exit(1)
    prompts_map: dict = {}
    for i, image_url in enumerate(image_urls):
        dest = out_dir / f"{i:02d}.png"
        print(f"[info] Downloading image {i} → {dest}", file=sys.stderr)
        download_image(image_url, dest)
        prompts_map[str(i)] = {
            "prompt": args.prompt,
            "model": result.get("model", candidates[0] if not args.model else args.model),
            "provider_mode": "volc",
            "url": image_url,
            "file": str(dest),
        }

    (out_dir / "prompts.json").write_text(json.dumps(prompts_map, ensure_ascii=False, indent=2))

    # 简单 HTML gallery
    gallery_html = ["<!DOCTYPE html><html><body>"]
    for i in range(len(image_urls)):
        gallery_html.append(f'<img src="{i:02d}.png" style="max-width:512px;margin:4px">')
    gallery_html.append("</body></html>")
    (out_dir / "index.html").write_text("\n".join(gallery_html))

    usage = result.get("usage") or {}
    print(
        f"[done] {len(image_urls)} image(s) saved to {out_dir}/ (usage: {json.dumps(usage, ensure_ascii=False)})",
        file=sys.stderr,
    )
    for k, v in prompts_map.items():
        print(f"  [{k}] {v['file']}", file=sys.stderr)


if __name__ == "__main__":
    main()
