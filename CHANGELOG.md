# Changelog

本项目所有重要变更记录于此。格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]
### Changed (P5: 单凭据 + 目录改名收尾)
- 三个技能目录统一改名为 gen-img / gen-tts / gen-video（原 volc-img-gen / volc-tts / volc-video-gen 已删）
- 凭据变量统一为 IMG_* / TTS_* / VIDEO_*（删除 VOLC_* / AWK_GEN_KEY 别名）
- 火山方舟为唯一云凭据来源；删除阿里云百炼（业务空间 + agent plan）+ ASR 自检全套代码
- 删除 _shared/cred.py 的 config.json 兜底逻辑、删除 utils.extract_image_urls / normalize_size / build_payload (bailian) / resolve_provider 等供应商分发代码
- 删除 _DEPRECATED_* 标记的 env var 名（os.environ.get("") 清理为 os.environ.get 不存在）
- index.json TTS auth_env 改为 ["TTS_APP_ID", "TTS_ACCESS_KEY"]（双头备选）；SKILL.md / README.md / pyproject.toml 同步路径与凭据变量名
- tests：删除 TestNormalizeSize / TestBailianVoice / TestBailianStreamSentences / test_bailian_content_list / test_bailian_string_content / test_format_alias，回归到单供应商测试集
- pytest：`114 → 99 passed`


### Removed (P4: 弃用 config.json)
- 删除三个 skill 目录的 `config.json` 与 `config.json.example`（6 个文件）
- 凭据统一从根目录 `.env` 加载（`_shared/dotenv.py` 自动注入）
- `cred.py` 的 `CONFIG_PATH` 读取逻辑**保留**（向后兼容，不再主动推荐）
- 同步更新 README.md 与三个 SKILL.md 的「凭据配置」章节

### Changed (P3: ruff 自动消化)
- `ruff check --fix` 自动应用 12 个修复：unsorted-imports / unused-import / subprocess-run / yoda-conditions
- 手工补：
  - `volc-video-gen/scripts/aigc_common.py`：加 `import argparse`（修 F821 undefined-name）
  - `volc-img-gen/scripts/gen.py` + `volc-tts/scripts/tts.py`：`raise X from None`（修 B904）
  - `volc-tts/scripts/tts.py`：嵌套 `if` 用 `and` 合并（修 SIM102）
  - `volc-tts/scripts/tts.py`：把函数内 `import subprocess` / `import difflib` 提到顶部（修 PLC0415）
  - `tests/`：把函数内 `import sys` / `from types import SimpleNamespace` / `from pathlib import Path as _P` 提到顶部（修 PLC0415 / I001 / F404）
- 最终：`ruff check .` **All checks passed!**；pytest 仍为 **114 passed**

### Fixed (P2: SKILL.md 与代码对齐)
- `volc-img-gen/SKILL.md`：删除"唯一云凭据来源"措辞，改为如实描述「火山方舟（默认）→ 阿里云百炼业务空间 → agent plan」优先级与触发条件
- `volc-tts/SKILL.md`：删除"仅使用火山引擎 openspeech v3 端点"措辞，同步供应商优先级描述；说明火山音色 ID 在百炼端会被自动换成 `longanhuan_v3.6`
- `volc-img-gen/SKILL.md` 与 `volc-tts/SKILL.md` 凭据表后补充百炼 fallback 的环境变量（`WORKSPACE_ID` + `MODELSTUDIO_API_KEY` / `AWK_API_KEY`）
- `README.md` 顶部主描述：「统一走火山方舟作为唯一云端推理来源」→「主供应商偏好火山方舟，火山凭据缺失时回退阿里云百炼」
- `index.json` description 同步更新
### Refactored (P1: 抽取共享模块)
- 三个 skill 各有的 `scripts/cred.py`（1229 字节 × 3）合并为单一 `_shared/cred.py`（58 行）
- `_shared/cred.py` 新增 `reset_cache()` 与可注入的 `CONFIG_PATH`，支持跨 skill 复用
- 修 `volc-img-gen/scripts/gen.py:build_volc_payload`：编辑模式未传 `--image-size` 时原代码会 `size_to_volc(None)` → SystemExit，现改为「不附带 size（跟随参考图）」
- mypy `Argument 1 to size_to_volc ... has incompatible type None` 错误随之消除（13 → 12）
- conftest.py 强化 `_load_module`：发现同名模块已注册时直接返回，避免重复实例化导致 `sys.modules["cred"] is cred_img` 断言失败
- 测试：`test_cred.py` 新增 `test_reset_cache_clears_loaded_config` / `test_config_path_is_injectable` / `test_cred_module_loaded_from_shared` 三个 P1 行为测试；移除旧的「三个 cred 字面相同」测试（已无意义）
- 测试：`test_volc_img_gen.py` 移除 2 个 xfail（已知 bug 已修）
- 测试结果：`112 passed + 2 xfailed` → `114 passed`


### Security (P0: 凭据轮换)
- 三个 skill 目录新增 `config.json.example` 模板（占位符，可入库）
- 清空三个 `config.json` 中的真实 API Key（已泄露：火山方舟 `ark-d06d46fb-...*`、火山语音 `434281f6-...*`）
- `README` 新增「安全 / 凭据轮换」章节，给出禁用旧 Key → 生成新 Key → 写入本地 的操作流程
- 本机磁盘上 `config.json` 现在仅含占位符；真正凭据应通过环境变量（`VOLC_IMG_API_KEY` / `VOLC_TTS_APP_KEY` / `AWK_GEN_KEY`）或重新填入 config.json

### Added（项目级补齐）
- 顶层 `README.md`：三个 Skill 速查与凭据约定
- `LICENSE`：MIT
- `pyproject.toml`：项目元信息 + ruff/mypy/pytest 配置
- `requirements-dev.txt`：仅开发依赖（脚本本身零运行依赖）
- `.gitignore`：保护 `config.json` 与运行产物
- `index.json`：Agent 可一次发现所有 Skill 的清单
- `CONTRIBUTING.md`：贡献指南
- `tests/`：最小烟雾测试（`test_cred` / `test_volc_img_gen` / `test_volc_tts` / `test_volc_video_gen` / `test_aigc_common`）

### Security
- `.gitignore` 明确把 `config.json` 排除，防止真实 API Key 入库

## [0.1.0] - 2026-09-28

### Added
- `volc-img-gen`：火山方舟 Seedream 文生图/图生图/多图融合（OpenAI 兼容 `/images/generations`）
- `volc-tts`：豆包语音合成 2.0（openspeech v3 单向流式，含字级时间戳与 ASR 自检）
- `volc-video-gen`：Seedance 2.0 异步视频生成（t2v / i2v / r2v，首尾帧对齐）

[Unreleased]: https://github.com/<owner>/shot_video_creation/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/<owner>/shot_video_creation/releases/tag/v0.1.0

### Changed (仓库改名)
- 仓库对外名 `media_v1` → `shot_video_creation`（与目录名统一）
- pyproject / LICENSE / README 同步更新

## [Unreleased]

- 子 Skill 改名 kebab-case 以兼容 Claude Code / OpenCode / 豆包桌面端的 `^[a-z0-9]+(-[a-z0-9]+)*$` 命名规范
  - `gen_img` → `gen-img`
  - `gen_tts` → `gen-tts`
  - `gen_video` → `gen-video`
- 顶层 SKILL.md `name` 字段改为 `shot-video-creation`
- 顶层目录保留 `shot_video_creation`（与 GitHub 仓库名一致，sandbox writable root）
- README / pyproject / index.json / tests / .github/workflows 同步更新
- 新增「跨平台安装与发现」一节（Claude Code / OpenCode / 豆包桌面端 / WorkBuddy）

