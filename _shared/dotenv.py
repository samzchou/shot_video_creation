"""极简 .env 加载器（stdlib only）。

解析 KEY=VALUE 行，注入 os.environ。不支持 python-dotenv 的高级语法
（嵌套引号、export、转义），但对单层 KEY=VALUE 已够用。

默认查找：调用方 __file__ 反推 shot_video_creation 仓库根目录（即 _shared/../.env），
再回退到 cwd/.env。让 Skill 脚本不论从哪个目录调用都能找到 .env。
"""

from __future__ import annotations

import os
from pathlib import Path


def load_env(path: str | Path | None = None, *, override: bool = False) -> int:
    """从 .env 文件加载 KEY=VALUE 到 os.environ，返回成功注入的键数。

    参数：
      path：显式指定 .env 路径；缺省时自动查找 _shared/../.env 或 cwd/.env
      override：是否覆盖已有环境变量（默认 False：现有 shell env 优先）

    示例：
      from dotenv import load_env
      load_env()  # 加载根目录 .env，shell 已有变量优先
    """
    target = Path(path) if path else _find_env()
    if target is None or not target.is_file():
        return 0
    loaded = 0
    for raw in target.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


def _find_env() -> Path | None:
    """优先 _shared/../.env（仓库根目录 shot_video_creation/），再回退到 cwd/.env。"""
    here = Path(__file__).resolve().parent.parent / ".env"
    if here.is_file():
        return here
    cwd_env = Path.cwd() / ".env"
    if cwd_env.is_file():
        return cwd_env
    return None
