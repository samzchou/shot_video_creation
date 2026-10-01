"""共享凭据读取：环境变量优先，其次 skill 目录的 config.json。

三个 skill（volc-img-gen / volc-tts / volc-video-gen）共用本模块。
各 skill 脚本 import 前需先把 _shared 加进 sys.path，再设置本模块的 CONFIG_PATH
指向自己的 config.json（典型：Path(__file__).resolve().parent.parent / "config.json"）。

调用方典型用法：
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
    import cred
    cred.CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"
    from cred import get as cred_get

config.json 已置入 .gitignore，仅本机可见。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# 由调用方注入；缺省值放在 _shared 同级的 config.json（通常不存在）。
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.json"

_cache: dict | None = None


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            if CONFIG_PATH.is_file():
                _cache = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            else:
                _cache = {}
        except Exception:
            _cache = {}
    return _cache


def get(name: str, env: str | None = None, default: str = "") -> str:
    """读取配置项：环境变量 env > config.json 同名 key > default。"""
    if env:
        value = os.environ.get(env)
        if value is not None and str(value).strip():
            return str(value).strip()
    value = _load().get(name)
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip()
    return str(value)


def reset_cache() -> None:
    """清空模块缓存（测试或热更新场景用）。"""
    global _cache
    _cache = None
