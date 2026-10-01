# 贡献指南

欢迎贡献代码、文档与测试。请遵循以下流程。

## 开发环境

- Python ≥ 3.10（脚本用到 PEP 604 `Path | None`）
- 安装开发依赖：`python -m pip install -r requirements-dev.txt`

## 风格

- `ruff format` 统一格式；`ruff check` + `mypy` 提交前必跑
- 行长度 110（见 `pyproject.toml`）
- 提交前：`ruff check . && ruff format --check . && mypy . && pytest -m "not network and not ffmpeg"`

## 凭据

- 严禁把真实 API Key 写入任何文件；本地放 `config.json`（已 gitignore）
- PR 中包含的任何示例命令都使用占位符（如 `ark-xxxx-xxxx`）

## 测试

- 新增功能至少补一个烟雾测试；已有测试不要无理由删除
- 不在 CI 中跑需要真实凭据或网络的测试（用 `pytest -m "not network"` 过滤）

## 目录约定

- 新 Skill 顶层放 `SKILL.md` + `config.json` + `scripts/`
- 凭据读取统一通过 `scripts/cred.py` 的 `get(name, env, default)`
- 共享工具放到 `scripts/<module>.py`，并由依赖方 `sys.path.insert` 后 `import`
