"""cred.py：环境变量优先 → config.json → 默认值。

所有三个 skill 的 cred.py 模块相同（SHA256 一致），行为一致。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


def test_env_var_takes_precedence(monkeypatch, tmp_path: Path, cred_img):
    """环境变量优先于 config.json。"""
    # 写一份 config.json，里面有老值
    cfg = tmp_path / "config.json"
    cfg.write_text('{"api_key": "from_config"}', encoding="utf-8")

    monkeypatch.setenv("VOLC_IMG_API_KEY", "from_env")
    # 把 CONFIG_PATH 临时改到 tmp_path
    monkeypatch.setattr(cred_img, "CONFIG_PATH", cfg)
    monkeypatch.setattr(cred_img, "_cache", None)

    assert cred_img.get("api_key", env="VOLC_IMG_API_KEY") == "from_env"


def test_config_json_used_when_no_env(monkeypatch, tmp_path: Path, cred_img):
    """无环境变量时回退到 config.json。"""
    cfg = tmp_path / "config.json"
    cfg.write_text('{"api_key": "from_config", "model": "qwen-image-3.0"}', encoding="utf-8")
    monkeypatch.delenv("VOLC_IMG_API_KEY", raising=False)

    monkeypatch.setattr(cred_img, "CONFIG_PATH", cfg)
    monkeypatch.setattr(cred_img, "_cache", None)

    assert cred_img.get("api_key", env="VOLC_IMG_API_KEY") == "from_config"
    assert cred_img.get("model", env="VOLC_IMG_MODEL") == "qwen-image-3.0"


def test_default_when_missing(monkeypatch, tmp_path: Path, cred_img):
    """都缺失时返回 default。"""
    cfg = tmp_path / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.delenv("VOLC_IMG_API_KEY", raising=False)

    monkeypatch.setattr(cred_img, "CONFIG_PATH", cfg)
    monkeypatch.setattr(cred_img, "_cache", None)

    assert cred_img.get("api_key", env="VOLC_IMG_API_KEY", default="fallback") == "fallback"


def test_missing_config_file_returns_default(monkeypatch, tmp_path: Path, cred_img):
    """config.json 不存在时不应抛错，返回 default。"""
    nonexistent = tmp_path / "missing.json"
    monkeypatch.delenv("VOLC_IMG_API_KEY", raising=False)
    monkeypatch.setattr(cred_img, "CONFIG_PATH", nonexistent)
    monkeypatch.setattr(cred_img, "_cache", None)

    assert cred_img.get("api_key", env="VOLC_IMG_API_KEY") == ""


def test_strips_whitespace(monkeypatch, tmp_path: Path, cred_img):
    """字符串值自动 strip。"""
    cfg = tmp_path / "config.json"
    cfg.write_text('{"api_key": "  spaced  "}', encoding="utf-8")
    monkeypatch.delenv("VOLC_IMG_API_KEY", raising=False)
    monkeypatch.setattr(cred_img, "CONFIG_PATH", cfg)
    monkeypatch.setattr(cred_img, "_cache", None)

    assert cred_img.get("api_key", env="VOLC_IMG_API_KEY") == "spaced"


def test_cred_module_loaded_from_shared(cred_img):
    """P1：三个 skill 共用 _shared/cred.py（同一 sys.modules 条目）。"""
    assert cred_img.__file__.replace("\\", "/").endswith("_shared/cred.py")
    assert sys.modules["cred"] is cred_img  # 单一来源


def test_reset_cache_clears_loaded_config(cred_img, tmp_path):
    """P1 新增：reset_cache() 让后续 get() 重新读 CONFIG_PATH。"""
    cfg = tmp_path / "config.json"
    cfg.write_text('{"api_key": "v1"}', encoding="utf-8")
    cred_img.CONFIG_PATH = cfg
    assert cred_img.get("api_key") == "v1"
    # 修改文件但不动 cache，仍然返回旧值
    cfg.write_text('{"api_key": "v2"}', encoding="utf-8")
    assert cred_img.get("api_key") == "v1"
    # reset_cache 后重读
    cred_img.reset_cache()
    assert cred_img.get("api_key") == "v2"


def test_config_path_is_injectable(cred_img, tmp_path):
    """P1：CONFIG_PATH 由调用方注入，支持跨 skill 复用同一模块。"""
    original = cred_img.CONFIG_PATH
    try:
        cfg = tmp_path / "x.json"
        cfg.write_text('{"api_key": "injected"}', encoding="utf-8")
        cred_img.CONFIG_PATH = cfg
        cred_img.reset_cache()
        assert cred_img.get("api_key") == "injected"
    finally:
        cred_img.CONFIG_PATH = original
        cred_img.reset_cache()
