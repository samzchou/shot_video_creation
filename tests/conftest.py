"""pytest 共用夹具：把三个 skill 的 scripts/ 加入 sys.path 并按需加载。

Sandbox 兼容：在沙箱环境下，pytest 默认使用系统 TEMP 目录创建 tmp_path，
会被文件系统拒绝。这里在 conftest 加载阶段把 tempfile.tempdir 重定向到工作区
下的 .pytest-tmp/，让所有临时文件（cache、tmp_path fixture）都落在沙箱可写处。
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from types import ModuleType

# 测试中禁用 dotenv 自动加载：preload 会触发脚本顶部的 load_env()，污染环境变量
import dotenv
import pytest

dotenv.load_env = lambda *a, **kw: 0  # noqa: E731

# 必须发生在任何 fixture 之前
_WORKSPACE_TMP = Path(__file__).resolve().parent.parent / ".pytest-tmp"
_WORKSPACE_TMP.mkdir(parents=True, exist_ok=True)
os.environ["TMP"] = str(_WORKSPACE_TMP)
os.environ["TEMP"] = str(_WORKSPACE_TMP)
os.environ["TMPDIR"] = str(_WORKSPACE_TMP)
tempfile.tempdir = None  # 失效缓存，让后续 gettempdir() 重读

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIRS = {
    "img": ROOT / "gen-img",
    "tts": ROOT / "gen-tts",
    "video": ROOT / "gen-video",
}


def _load_module(name: str, path: Path) -> ModuleType:
    """从 PATH 加载模块为 name；如 sys.modules 已有同名条目则直接返回（避免重复实例化）。

    重复加载会让 CONFIG_PATH / _cache 等模块级状态分裂，且 `module is sys.modules[name]`
    断言会失败。
    """
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# 预加载到 sys.modules，使测试文件顶层 `from xxx import ...` 直接可用
for _name, _path in [
    ("aigc_common_skill", SKILL_DIRS["video"] / "scripts" / "aigc_common.py"),
    ("gen_volc_skill", SKILL_DIRS["video"] / "scripts" / "gen_volc.py"),
    ("tts_skill", SKILL_DIRS["tts"] / "scripts" / "tts.py"),
    ("gen_img", SKILL_DIRS["img"] / "scripts" / "gen.py"),
    ("cred", ROOT / "_shared" / "cred.py"),  # P1: 三个 skill 共用 _shared/cred.py
]:
    _load_module(_name, _path)


# P1 之后三个 skill 共用 _shared/cred.py；保留旧 fixture 名以兼容 test_cred.py 调用。
@pytest.fixture(scope="session")
def cred_img() -> ModuleType:
    return _load_module("cred", ROOT / "_shared" / "cred.py")


@pytest.fixture(scope="session")
def cred_tts() -> ModuleType:
    return _load_module("cred", ROOT / "_shared" / "cred.py")


@pytest.fixture(scope="session")
def cred_video() -> ModuleType:
    return _load_module("cred", ROOT / "_shared" / "cred.py")


@pytest.fixture(scope="session")
def gen_img() -> ModuleType:
    return _load_module("gen_img", SKILL_DIRS["img"] / "scripts" / "gen.py")


@pytest.fixture(scope="session")
def tts() -> ModuleType:
    return _load_module("tts_skill", SKILL_DIRS["tts"] / "scripts" / "tts.py")


@pytest.fixture(scope="session")
def aigc_common() -> ModuleType:
    return _load_module("aigc_common_skill", SKILL_DIRS["video"] / "scripts" / "aigc_common.py")


@pytest.fixture(scope="session")
def gen_volc() -> ModuleType:
    return _load_module("gen_volc_skill", SKILL_DIRS["video"] / "scripts" / "gen_volc.py")


@pytest.fixture
def isolated_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """清空所有凭据相关环境变量，让被测代码走 config.json / default 分支。

    用途：避免读到本机真实凭据，让测试可重复。
    """
    for key in list(os.environ):
        if (
            any(
                key.startswith(prefix)
                for prefix in ("VOLC_", "AWK_", "MODELSTUDIO_", "DASHSCOPE_", "BAILIAN_")
            )
            or key == "WORKSPACE_ID"
        ):
            monkeypatch.delenv(key, raising=False)
    return tmp_path
