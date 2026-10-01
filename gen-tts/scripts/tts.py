#!/usr/bin/env python3
"""火山豆包 TTS（openspeech v3 / seed-tts-2.0）— stdlib only。

合成流程：POST https://openspeech.bytedance.com/api/v3/tts/unidirectional，
鉴权 X-Api-App-Id + X-Api-Access-Key（旧控制台双头）或 X-Api-Key（新控制台单头），
X-Api-Resource-Id 路由模型版本（seed-tts-2.0 / seed-icl-2.0 等）。
响应 NDJSON，每行含 base64 音频分片，拼装成完整音频。

凭据：TTS_APP_KEY（新单头，推荐）或 TTS_APP_ID + TTS_ACCESS_KEY（旧双头）。
Resource ID 默认 seed-tts-2.0，可由 TTS_RESOURCE_ID 覆盖。
"""

import argparse
import base64
import difflib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

# 凭据读取（环境变量优先，其次 skill 根目录 config.json）——使用共享 _shared/cred.py
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from dotenv import load_env

load_env()  # noqa: E402  # 注入 .env 到 os.environ（在 cred 之前）
import cred as _cred  # noqa: E402

_cred.CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"
from cred import get as cred_get  # noqa: E402

# 统一 URL 出入口：scheme 校验 + 大小上限
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from safe_url import SafeUrlError, safe_post_bytes  # noqa: E402

DEFAULT_API_BASE = "https://openspeech.bytedance.com/api/v3"
DEFAULT_TTS_PATH = "/tts/unidirectional"
DEFAULT_RESOURCE_ID = "seed-tts-2.0"
# 默认 speaker：爽快思思 2.0（通用，官方 2.0 音色）
DEFAULT_SPEAKER = "zh_female_shuangkuaisisi_uranus_bigtts"

# 火山官方 2.0 音色列表（_uranus_bigtts 后缀）
VALID_VOICES = {
    # 通用女声（对应原 claire 清澈女声）
    "zh_female_shuangkuaisisi_uranus_bigtts",  # 爽快思思 2.0，通用，推荐默认
    "zh_female_cancan_uranus_bigtts",  # 知性灿灿 2.0，角色扮演
    "zh_female_tianmeixiaoyuan_uranus_bigtts",  # 甜美小源 2.0，通用
    "zh_female_vv_uranus_bigtts",  # Vivi 2.0，多语种通用
    "zh_female_xiaohe_uranus_bigtts",  # 小何 2.0，通用
    "zh_female_kefunvsheng_uranus_bigtts",  # 暖阳女声 2.0，客服
    # 通用男声（对应原 benjamin 幽默男声 / david 清脆男声 / charles 激昂男声）
    "zh_male_m191_uranus_bigtts",  # 舟 2.0，通用
    "zh_male_taocheng_uranus_bigtts",  # 小天 2.0，通用
    # 多语种（对应原 diana 可爱女声的娃娃音色备选）
    "en_female_dacey_uranus_bigtts",  # Dacey，多语种（英）
    "en_male_tim_uranus_bigtts",  # Tim，多语种（英）
}

VALID_FORMATS = {"mp3", "pcm", "ogg_opus", "wav"}
SAMPLE_RATES = {8000, 16000, 22050, 24000, 32000, 44100, 48000}
# 语速 [-50, 100]：0 默认，100 = 2x，-50 = 0.5x；映射到 [0.5, 2.0]
SPEECH_RATE_MIN = -50
SPEECH_RATE_MAX = 100
LOUDNESS_RATE_MIN = -50
LOUDNESS_RATE_MAX = 100

DEFAULT_ASR_RESOURCE_ID = "volc.bigasr.auc_turbo"
DEFAULT_ASR_MODEL = "bigasr"


SAFE_INPUT_DIRS = (Path("scripts"), Path("assets"), Path("tmp"), Path("output_videos"), Path("fragments"))
SAFE_OUTPUT_DIRS = (Path("assets/audio"), Path("tmp"), Path("output_videos"), Path("fragments"))
TEXT_EXTENSIONS = {".txt", ".md", ".srt", ".vtt"}
MAX_TEXT_FILE_BYTES = 512 * 1024
# seed-tts-2.0 单次合成文本上限（字符版，保守值）
MAX_TEXT_CHARS = 5000


def die(message: str) -> None:
    print(f"[error] {message}", file=sys.stderr)
    sys.exit(1)


def workspace_root(root: Path | None = None) -> Path:
    return (root or Path.cwd()).resolve()


def ensure_safe_path(
    raw_path: str, allowed_dirs: tuple[Path, ...], purpose: str, root: Path | None = None
) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path.resolve()
    if ".." in path.parts:
        die(f"{purpose} path must not contain '..'")

    resolved_root = workspace_root(root)
    resolved = (resolved_root / path).resolve()
    under_allowed = any(
        resolved == (resolved_root / base).resolve()
        or resolved.is_relative_to((resolved_root / base).resolve())
        for base in allowed_dirs
    )
    # 平台运营文件夹约定：允许 <platform>/outputs/...（平台内容项目目录）
    under_platform_outputs = "outputs" in path.parts[:-1]
    if not (under_allowed or under_platform_outputs):
        allowed = ", ".join(str(base) for base in allowed_dirs)
        die(f"{purpose} path must be under one of: {allowed}, or a platform ops folder <platform>/outputs/")
    return resolved


def _strip_markdown(text: str) -> str:
    """Remove markdown formatting that shouldn't be read aloud."""
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^#{1,6}\s", stripped):
            continue
        if stripped.startswith("<!--") or stripped.endswith("-->"):
            continue
        cleaned = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", stripped)
        cleaned = re.sub(r"_([^_]+)_", r"\1", cleaned)
        cleaned = re.sub(r"^[-*]\s+", "", cleaned)
        lines.append(cleaned)
    return "\n".join(lines).strip()


def extract_tts_requirement_text(content: str) -> str:
    """Extract only the voiceover copy from a tts_requirement.md file."""
    heading_markers = (
        "配音文案",
        "voiceover text",
        "voiceover copy",
        "narration text",
        "script text",
    )
    lines = content.splitlines()
    collecting = False
    extracted: list[str] = []

    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if re.match(r"^#{1,6}\s", stripped):
            if collecting:
                break
            if stripped.startswith("## "):
                collecting = any(marker in lower for marker in heading_markers)
            continue
        if not collecting:
            continue
        if not stripped or stripped.startswith("<!--"):
            continue
        extracted.append(stripped)

    return _strip_markdown("\n".join(extracted).strip())


def extract_tts_requirement_settings(content: str) -> dict:
    settings: dict = {}
    for line in content.splitlines():
        stripped = line.strip().strip("-").strip()
        voice_match = re.search(
            r"(?:音色|语音|voice|speaker)\s*[:：]\s*`?([^\s`，,]+)", stripped, re.IGNORECASE
        )
        if voice_match:
            settings["voice"] = voice_match.group(1)
        speed_match = re.search(
            r"(?:语速|speed|speech_rate)\s*[:：]\s*(-?\d+(?:\.\d+)?)", stripped, re.IGNORECASE
        )
        if speed_match:
            settings["speech_rate"] = float(speed_match.group(1))
    return settings


def read_tts_requirement(path: Path) -> tuple[str, dict]:
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        die("tts_requirement.md must be UTF-8 encoded")
    except OSError as exc:
        die(f"failed to read tts_requirement.md: {exc}")

    return extract_tts_requirement_text(content) or content, extract_tts_requirement_settings(content)


def resolve_fragment_dir(raw_path: str, root: Path | None = None) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        fragment_dir = path.resolve()
    else:
        if ".." in path.parts:
            die("fragment directory path must not contain '..'")
        resolved_root = workspace_root(root)
        fragment_dir = (resolved_root / path).resolve()
    if fragment_dir.name == "artifacts":
        fragment_dir = fragment_dir.parent
    if not fragment_dir.is_dir():
        die(f"fragment directory does not exist: {raw_path}")
    if (
        not (fragment_dir / "tts_requirement.md").is_file()
        and not (fragment_dir / "requirement.md").is_file()
    ):
        die(f"fragment directory must contain tts_requirement.md or requirement.md: {raw_path}")
    return fragment_dir


def get_fragment_dir(args: argparse.Namespace) -> str | None:
    return getattr(args, "fragment_dir", None)


def read_text_file(raw_path: str, root: Path | None = None) -> str:
    path = ensure_safe_path(raw_path, SAFE_INPUT_DIRS, "--text-file", root=root)
    if path.suffix.lower() not in TEXT_EXTENSIONS:
        die(f"--text-file must use one of these extensions: {', '.join(sorted(TEXT_EXTENSIONS))}")
    if not path.is_file():
        die(f"--text-file does not exist or is not a file: {raw_path}")
    if path.stat().st_size > MAX_TEXT_FILE_BYTES:
        die(f"--text-file exceeds {MAX_TEXT_FILE_BYTES} bytes")
    try:
        content = path.read_text(encoding="utf-8")
        if path.name == "tts_requirement.md":
            extracted = extract_tts_requirement_text(content)
            return extracted or content
        return content
    except UnicodeDecodeError:
        die("--text-file must be UTF-8 encoded")
    except OSError as exc:
        die(f"failed to read --text-file: {exc}")
    raise AssertionError("unreachable")


def read_text_source(args: argparse.Namespace, root: Path | None = None) -> tuple[str, dict]:
    fragment_dir_arg = get_fragment_dir(args)
    source_count = sum(1 for value in (args.text, args.text_file, fragment_dir_arg) if value)
    if source_count > 1:
        die("Use only one of --text, --text-file, or fragment_dir")
    if args.text_file:
        path = ensure_safe_path(args.text_file, SAFE_INPUT_DIRS, "--text-file", root=root)
        if path.name == "tts_requirement.md":
            text, settings = read_tts_requirement(path)
        else:
            text = read_text_file(args.text_file, root=root)
            settings = {}
    elif args.text:
        text = args.text
        settings = {}
    elif fragment_dir_arg:
        fragment_dir = resolve_fragment_dir(fragment_dir_arg, root=root)
        tts_requirement = fragment_dir / "tts_requirement.md"
        if not tts_requirement.is_file():
            die(f"tts_requirement.md not found under fragment directory: {fragment_dir_arg}")
        text, settings = read_tts_requirement(tts_requirement)
    else:
        die("Either --text, --text-file, or fragment_dir is required")
    text = text.strip()
    if not text:
        die("Input text is empty")
    return text, settings


def read_text(args: argparse.Namespace, root: Path | None = None) -> str:
    text, _settings = read_text_source(args, root=root)
    return text


def apply_tts_settings(args: argparse.Namespace, settings: dict) -> None:
    default_voice = DEFAULT_SPEAKER
    if args.voice is None:
        args.voice = settings.get("voice") or default_voice
    if args.speech_rate is None and settings.get("speech_rate") is not None:
        args.speech_rate = settings["speech_rate"]


def resolve_resource_id(speaker: str) -> str:
    """按 speaker ID 特征路由 X-Api-Resource-Id。

    - S_xxx 克隆音色 → seed-icl-2.0
    - _uranus_bigtts / saturn_ 官方 2.0 → seed-tts-2.0
    - _mars_bigtts / _moon_bigtts / ICL_ 官方 1.0 → seed-tts-1.0
    """
    if speaker.startswith("S_"):
        return "seed-icl-2.0"
    if "_uranus_bigtts" in speaker or speaker.startswith("saturn_"):
        return "seed-tts-2.0"
    if "_mars_bigtts" in speaker or "_moon_bigtts" in speaker or speaker.startswith("ICL_"):
        return "seed-tts-1.0"
    return cred_get("resource_id", env="TTS_RESOURCE_ID", default=DEFAULT_RESOURCE_ID) or DEFAULT_RESOURCE_ID


def build_payload(args: argparse.Namespace, text: str) -> dict:
    audio_params: dict = {"format": args.format}
    if args.sample_rate is not None:
        audio_params["sample_rate"] = args.sample_rate
    if args.speech_rate is not None:
        audio_params["speech_rate"] = int(args.speech_rate)
    if args.loudness_rate is not None:
        audio_params["loudness_rate"] = int(args.loudness_rate)
    # enable_subtitle=true 让火山单向流式 HTTP 原生返回字级时间戳
    # （sentence.words 带 startTime/endTime，秒）。默认 false 保持向后兼容。
    if getattr(args, "enable_subtitle", False):
        audio_params["enable_subtitle"] = True

    payload: dict = {
        "user": {"uid": f"wiseflow-awk-tts-{int(time.time())}"},
        "req_params": {
            "text": text,
            "speaker": args.voice,
            "audio_params": audio_params,
        },
    }

    additions: dict = {}
    if args.context_text:
        additions["context_texts"] = [args.context_text]
    # 克隆音色需 model_type=4
    if args.voice.startswith("S_"):
        additions["model_type"] = 4
    if additions:
        payload["req_params"]["additions"] = json.dumps(additions, ensure_ascii=False)

    return payload


def validate_args(args: argparse.Namespace, text: str) -> None:
    if len(text) > MAX_TEXT_CHARS:
        die(f"Input text exceeds {MAX_TEXT_CHARS} characters (single-call limit)")
    # 不硬拦克隆音色 S_xxx 与官方未列入的 speaker
    if (
        args.voice
        and args.voice not in VALID_VOICES
        and not (
            args.voice.startswith("S_")
            or "_uranus_bigtts" in args.voice
            or "_mars_bigtts" in args.voice
            or "_moon_bigtts" in args.voice
            or args.voice.startswith("saturn_")
            or args.voice.startswith("ICL_")
        )
    ):
        die(f"Unsupported voice: {args.voice}. Valid voices: {', '.join(sorted(VALID_VOICES))}")
    if args.sample_rate is not None and args.sample_rate not in SAMPLE_RATES:
        die(f"--sample-rate must be one of: {', '.join(str(r) for r in sorted(SAMPLE_RATES))}")
    if args.speech_rate is not None and not SPEECH_RATE_MIN <= args.speech_rate <= SPEECH_RATE_MAX:
        die(
            f"--speech-rate must be between {SPEECH_RATE_MIN} and {SPEECH_RATE_MAX} (0=default, 100=2x, -50=0.5x)"
        )
    if args.loudness_rate is not None and not LOUDNESS_RATE_MIN <= args.loudness_rate <= LOUDNESS_RATE_MAX:
        die(f"--loudness must be between {LOUDNESS_RATE_MIN} and {LOUDNESS_RATE_MAX}")


def build_headers(resource_id: str) -> dict[str, str]:
    """构造火山鉴权 header：优先旧控制台双头，否则新控制台单头。"""
    headers = {
        "Content-Type": "application/json",
        "X-Api-Resource-Id": resource_id,
        "Connection": "keep-alive",
    }
    app_id = cred_get("app_id", env="TTS_APP_ID")
    access_key = cred_get("access_key", env="TTS_ACCESS_KEY")
    app_key = cred_get("app_key", env="TTS_APP_KEY")

    if app_id and access_key:
        headers["X-Api-App-Id"] = app_id
        headers["X-Api-Access-Key"] = access_key
    elif app_key:
        headers["X-Api-Key"] = app_key
    else:
        die(
            "火山 TTS 凭据未配置：请在技能目录 config.json 填写 app_id+access_key（旧控制台双头）或 app_key（新控制台单头），或设置环境变量 TTS_APP_ID/TTS_ACCESS_KEY/TTS_APP_KEY"
        )
    return headers


def create_speech(
    api_base: str, payload: dict, headers: dict, *, timeout: int = 120
) -> tuple[bytes, list[dict]]:
    """调火山 openspeech v3 单向流式 TTS。

    返回 (audio_bytes, sentences)：
    - audio_bytes: 拼装后的 base64 音频分片
    - sentences: 各 sentence 段（含 text/words/phonemes），enable_subtitle=true 时 words 带字级时间戳

    scheme 校验 + 大小上限（默认 50MB，足够 5 分钟高质量音频）由 _shared/safe_url 提供。
    """
    url = f"{api_base.rstrip('/')}{DEFAULT_TTS_PATH}"
    try:
        raw = safe_post_bytes(url, payload, headers=headers, timeout=timeout, max_bytes=50 * 1024 * 1024)
    except SafeUrlError as exc:
        die(f"request failed: {exc}")

    # NDJSON：逐行解析 JSON，收集 data 字段（base64 音频分片）+ sentence 段
    chunks: list[bytes] = []
    sentences: list[dict] = []
    for line in raw.decode(errors="replace").splitlines():
        trimmed = line.strip()
        if not trimmed:
            continue
        try:
            parsed = json.loads(trimmed)
        except json.JSONDecodeError:
            continue
        code = parsed.get("code")
        if code == 0 and parsed.get("data"):
            chunks.append(base64.b64decode(parsed["data"]))
        if code == 0 and parsed.get("sentence"):
            sentences.append(parsed["sentence"])
        elif code == 20000000:
            # 正常结束事件
            break
        elif code is not None and code != 0:
            die(f"火山 TTS 流错误: code={code} message={parsed.get('message', '')}")

    if not chunks:
        die("火山 TTS 返回空音频（无 data 分片）")
    return b"".join(chunks), sentences


# ── TTS 输出路径 ────────────────────────────────────────────────────────────


def resolve_output_path(args: argparse.Namespace, root: Path | None = None) -> Path:
    """解析 --output / --out-dir / fragment_dir；强制相对工作区路径（与 gen_video 一致）。"""
    fragment_dir_arg = get_fragment_dir(args)
    if args.output:
        # 对齐 gen_video/ensure_safe_output：拒绝绝对路径
        if Path(args.output).is_absolute():
            die("--output must be relative to the workspace")
        output_path = ensure_safe_path(args.output, SAFE_OUTPUT_DIRS, "output", root=root)
    elif args.out_dir:
        if Path(args.out_dir).is_absolute():
            die("--out-dir must be relative to the workspace")
        output_path = ensure_safe_path(
            str(Path(args.out_dir) / f"speech.{args.format}"), SAFE_OUTPUT_DIRS, "output", root=root
        )
    elif fragment_dir_arg:
        if Path(fragment_dir_arg).is_absolute():
            die("fragment_dir must be relative to the workspace")
        frag_path = Path(fragment_dir_arg)
        if frag_path.name == "artifacts":
            output_path = ensure_safe_path(
                str(frag_path / f"speech.{args.format}"), SAFE_OUTPUT_DIRS, "output", root=root
            )
        else:
            output_path = ensure_safe_path(
                str(frag_path / "artifacts" / f"speech.{args.format}"),
                SAFE_OUTPUT_DIRS,
                "output",
                root=root,
            )
    else:
        output_path = ensure_safe_path(
            str(Path(f"tmp/awk-tts-{int(time.time())}") / f"speech.{args.format}"),
            SAFE_OUTPUT_DIRS,
            "output",
            root=root,
        )
    if output_path.exists() and not args.overwrite:
        die(f"output file already exists: {output_path}. Use --overwrite to replace it")
    metadata_path = output_path.with_suffix(".json")
    if metadata_path.exists() and not args.overwrite:
        die(f"metadata file already exists: {metadata_path}. Use --overwrite to replace it")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="火山方舟豆包语音合成 2.0 (seed-tts-2.0)")
    parser.add_argument(
        "fragment_dir", nargs="?", default=None, help="Fragment directory containing tts_requirement.md"
    )
    parser.add_argument("--text", default=None, help="Text to synthesize")
    parser.add_argument("--text-file", default=None, dest="text_file", help="UTF-8 text file")
    parser.add_argument("--voice", default=None, help="音色 ID（如 zh_female_shuangkuaisisi_uranus_bigtts）")
    parser.add_argument(
        "--format",
        default="mp3",
        choices=sorted(VALID_FORMATS),
        help="Audio response format (mp3/pcm/ogg_opus/wav)",
    )
    parser.add_argument(
        "--sample-rate", type=int, default=None, dest="sample_rate", help="Output sample rate"
    )
    parser.add_argument(
        "--speech-rate", type=float, default=None, help="Speech rate [-50, 100], 0=default, 100=2x, -50=0.5x"
    )
    parser.add_argument(
        "--loudness", type=float, default=None, dest="loudness_rate", help="Loudness [-50, 100], 0=default"
    )
    parser.add_argument(
        "--context-text",
        default=None,
        dest="context_text",
        help="情感控制上下文文本（如 '用撒娇甜蜜的语气'）",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Exact output file path under assets/audio, tmp, output_videos, fragments, or <platform>/outputs/",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        dest="out_dir",
        help="Output directory under assets/audio, tmp, output_videos, fragments, or <platform>/outputs/",
    )
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing output files")
    parser.add_argument(
        "--enable-subtitle",
        action="store_true",
        dest="enable_subtitle",
        help="产出字级时间戳 .subtitle.json（火山：单向流式原生返回）",
    )
    args = parser.parse_args()

    # 火山 TTS（唯一供应商）
    text, tts_settings = read_text_source(args)
    apply_tts_settings(args, tts_settings)
    validate_args(args, text)

    output_path = resolve_output_path(args)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    estimated_duration = len(text) / 4
    timeout = max(120, int(estimated_duration * 1.5))

    resource_id = resolve_resource_id(args.voice)
    payload = build_payload(args, text)
    api_base = cred_get("api_base", env="TTS_BASE", default=DEFAULT_API_BASE) or DEFAULT_API_BASE
    headers = build_headers(resource_id)
    print(
        f"[info] generating speech: provider=volc speaker={args.voice} resource={resource_id} "
        f"format={args.format} chars={len(text)} timeout={timeout}s"
    )
    audio, sentences = create_speech(api_base, payload, headers, timeout=timeout)
    meta_extra = {"provider": "volcengine-openspeech-v3", "resource_id": resource_id}
    if not audio:
        die("empty audio response")

    output_path.write_bytes(audio)

    audio_duration = get_audio_duration(output_path)

    metadata_path = output_path.with_suffix(".json")
    metadata = {
        **meta_extra,
        "speaker": args.voice,
        "format": args.format,
        "sample_rate": args.sample_rate,
        "speech_rate": args.speech_rate,
        "loudness_rate": args.loudness_rate,
        "text_chars": len(text),
        "audio_bytes": len(audio),
        "duration": round(audio_duration, 3),
        "file": str(output_path),
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    # enable_subtitle=true 时 sentences 带 words[startTime/endTime]，落盘供下游对齐
    subtitle_path: Path | None = None
    if sentences:
        subtitle_path = output_path.with_suffix(".subtitle.json")
        subtitle_path.write_text(
            json.dumps({"sentences": sentences}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(f"[done] Audio saved to: {output_path}")
    print(f"[done] Metadata: {metadata_path}")
    print(f"[done] Duration: {audio_duration:.3f}s")
    if subtitle_path:
        total_words = sum(len(s.get("words", [])) for s in sentences)
        print(
            f"[done] Subtitle (字级时间戳): {subtitle_path} ({len(sentences)} sentences, {total_words} words)"
        )


def get_audio_duration(filepath: Path) -> float:
    """Get audio duration via ffprobe. Returns 0.0 if unavailable."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(filepath)],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return float(data.get("format", {}).get("duration", 0))
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError):
        pass
    return 0.0


def _clean_for_similarity(text: str) -> str:
    """去掉标点/空白/符号，只留中日韩文字与字母数字。

    中文无空格，按空白分词会把整句压成单 token——一个标点差异相似度即归零；
    清洗后做序列比对，中英文统一可用。
    """
    return re.sub(r"[^0-9A-Za-z一-鿿]", "", text)


def similarity_ratio(text_a: str, text_b: str) -> float:
    """转写文本与脚本文本的相似度（清洗后 SequenceMatcher ratio，序敏感）。"""
    a = _clean_for_similarity(text_a)
    b = _clean_for_similarity(text_b)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


if __name__ == "__main__":
    main()
