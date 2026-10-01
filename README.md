# shot_video_creation — 火山方舟媒体生成 Skill 套件

三个开箱即用的 Codex Skill，主供应商偏好火山方舟（豆包系列），火山凭据缺失时回退到阿里云百炼业务空间或 agent plan。全部使用 **Python 标准库**实现，无第三方依赖。

## Skill 清单

| Skill | 目录 | 入口脚本 | 主要能力 |
|---|---|---|---|
| `gen-img` | [gen-img/](gen-img/) | `scripts/gen.py` | 火山方舟 **Seedream** 文生图/图生图（1–3 张参考图融合）/ 多图编辑 |
| `gen-tts` | [gen-tts/](gen-tts/) | `scripts/tts.py` | 火山引擎 **openspeech v3**（豆包 TTS 2.0）文本转语音，含字级字幕与 ASR 自检 |
| `gen-video` | [gen-video/](gen-video/) | `scripts/gen_volc.py` | 火山方舟 **Seedance 2.0** 异步视频生成（t2v / i2v / r2v，首尾帧对齐） |

所有 Skill 共用 `scripts/cred.py`（凭据读取：环境变量优先 → `config.json` → 默认值）。各 Skill 目录的 `SKILL.md` 是面向 Agent 的发现/调用文档。

## 快速上手

```bash
# 1. 配置凭据：编辑根目录 .env
# .env 已 .gitignore；可参考 .env.example 模板
cp .env.example .env
# 编辑 .env 填入三个 Skill 需要的 Key：
#   IMG_API_KEY / TTS_APP_KEY / VIDEO_KEY

# 2. 装开发依赖（脚本本身零依赖；仅测试/lint 需要）
python -m pip install -r requirements-dev.txt

# 3. 调用示例
python gen-img/scripts/gen.py --prompt "国潮奶茶海报，居中" --image-size 2K
python gen-tts/scripts/tts.py --text "你好，这是一段配音" --voice zh_female_shuangkuaisisi_uranus_bigtts --format mp3
python gen-video/scripts/gen_volc.py --prompt "橘猫在窗台打盹，午后阳光" --duration 8 --ratio 9:16 --output tmp/cat.mp4
```

> ⚠️ **SKILL.md 必须 UTF-8 无 BOM**。
> Codex 用 `strip_prefix("---\n")` 识别 frontmatter，文件首字节是 BOM 时 `extract_frontmatter`
> 直接返回 `MissingFrontmatter`，整个 Skill 被静默丢弃（不报错，也不进「Available skills」）。
> PowerShell `[IO.File]::WriteAllText(..., [Text.Encoding]::UTF8)` 默认 **带 BOM**，
> 必须用 `[Text.UTF8Encoding]::new($false)` 或 `Out-File -Encoding utf8NoBOM`。

## 凭据约定

| Skill | 必填 | 备用 |
|---|---|---|
| `gen-img` | `IMG_API_KEY`（`.env`） | `IMG_MODEL` 覆盖默认模型 |
| `gen-tts` | `TTS_APP_KEY`（或 `TTS_APP_ID` + `TTS_ACCESS_KEY` 双头） | `TTS_RESOURCE_ID` 切换 `seed-tts-2.0` 系列 |
| `gen-video` | `VIDEO_KEY`（`.env`） | 火山视频生成**只认这个 Key**，不回落 `ARK_API_KEY` |

> 凭据统一从根目录 `.env` 加载（`_shared/dotenv.py` 自动注入）。`.env` 已 gitignore，`.env.example` 入库。优先级：shell 环境变量 > `.env` > `config.json`（已弃用，仍兼容）。

## 输出路径约束

三个 Skill 的输出文件**必须**落在以下目录之一（相对工作区）：

- `output_videos/`
- `tmp/`
- `fragments/`
- `artifacts/`
- `<平台>/outputs/`

`aigc_common.ensure_safe_output` 在 `gen-video` 里执行此校验，避免写入到工程根。

## 跨平台安装与发现

顶层目录 `shot_video_creation/` 与子目录 `gen-img/`、`gen-tts/`、`gen-video/` 的命名有意区分：

- **GitHub 仓库名 / 本地顶层目录**：`shot_video_creation`（snake_case，与 GitHub URL 一致）
- **每个 SKILL.md 的 `name:` 字段**：`shot-video-creation` / `gen-img` / `gen-tts` / `gen-video`（kebab-case）
  —— 满足 Claude Code / OpenCode / 豆包桌面端的 `^[a-z0-9]+(-[a-z0-9]+)*$` 命名规范

四种安装路径互不影响，安装后直接调用即可。

### 1. Codex（CLI / 桌面）

Codex 递归扫描 `~/.codex/skills/`，发现任意层级的 `SKILL.md`（识别的是文件 frontmatter）。

```bash
# 一键安装（推荐）：从 GitHub 仓库
codex skill install <owner>/shot_video_creation

# 或手动 clone 到 Codex skills 根目录
git clone https://github.com/<owner>/shot_video_creation.git   ~/.codex/skills/shot_video_creation
```

### 2. Claude Code

Claude Code 扫描 `~/.claude/skills/*/SKILL.md`（一层）。

```bash
# 方式 A：整包安装（推荐，保留三个子 Skill 的路由关系）
git clone https://github.com/<owner>/shot_video_creation.git   ~/.claude/skills/shot-video-creation

# 方式 B：扁平安装（只暴露三个子 Skill，顶层元数据 skill 不出现）
for s in gen-img gen-tts gen-video; do
  cp -r ~/.claude/skills/shot-video-creation/$s ~/.claude/skills/$s
done
```

> ⚠️ Claude Code 要求 `name:` 必须等于目录名。本仓库已经把目录改名 kebab-case 以满足此规则；
> **不要**用 `cp -r .../gen_img` 这种旧名，否则会被拒绝加载。

### 3. 豆包桌面端（Windows / macOS）

豆包桌面端的 Skill 文件夹路径：

| 系统 | 路径 |
|------|------|
| **Windows** | `%LOCALAPPDATA%\Doubao\User Data\Default\.doubao\agent_mode\workspace\.user_skills\` |
| **macOS** | `~/Library/Application Support/Doubao/Default/.doubao/agent_mode/workspace/.user_skills/` |
| macOS 用户本地 | `~/Doubao/skills/` |

也兼容 `~/.doubao/skills/` 和 `~/.codex/skills/`（自动 fallback）。

**安装步骤（Windows / macOS）**：

1. 把整个 `shot_video_creation/` 目录复制到上述路径
2. 打开豆包桌面端 → 「技能·连接器·伙伴」 → 「我的技能」 → 「新建—上传技能」
3. 选择「本地文件夹上传」，选 `shot-video-creation/`（含三个子目录）
4. 上传完成后三个子 Skill 会自动出现在列表中

如果你只想用单个子 Skill，可以单独上传 `gen-img/`、`gen-tts/` 或 `gen-video/` 文件夹。

### 4. WorkBuddy（腾讯）

WorkBuddy 支持以下安装方式：

| 方式 | 操作 |
|------|------|
| **技能市场** | 打开 WorkBuddy → 「专家·技能·连接器」→「技能市场」→ 找到技能点击「+」安装 |
| **上传 ZIP** | 点击「添加技能」→「上传技能」，选择本地 ZIP 包 |
| **手动复制** | 将 Skill 文件夹复制到 `~/.workbuddy/skills/` 或 `{项目根目录}/.workbuddy/skills/` |

**安装路径**：

| 类型 | 路径 |
|------|------|
| 用户全局 | `~/.workbuddy/skills/{skill-name}/` |
| 项目级 | `{项目根目录}/.workbuddy/skills/{skill-name}/` |

安装完成后在 WorkBuddy 技能管理界面确认是否出现在列表中。

### 5. OpenCode

OpenCode 按优先级扫描多个根目录，**会自动 fallback 到 `.claude/skills/`**，所以上面的 Claude Code
安装方式完全适用。如果你想用 OpenCode 专属目录：

```bash
git clone https://github.com/<owner>/shot_video_creation.git   ~/.config/opencode/skills/shot-video-creation
```

启动后用 `/skill` 命令查看「`shot-video-creation`」即可触发顶层 Skill（会引导到子 Skill）。

### 命名映射速查

| 看到 `name:` 字段 | 仓库路径 | 调用样例（Claude Code） |
|---|---|---|
| `shot-video-creation` | `shot_video_creation/SKILL.md` | `/shot-video-creation` |
| `gen-img` | `shot_video_creation/gen-img/SKILL.md` | `/gen-img` |
| `gen-tts` | `shot_video_creation/gen-tts/SKILL.md` | `/gen-tts` |
| `gen-video` | `shot_video_creation/gen-video/SKILL.md` | `/gen-video` |

## 开发

```bash
# 静态检查 + 格式化
ruff check .
ruff format --check .

# 类型检查
mypy gen-img/scripts gen-tts/scripts gen-video/scripts

# 跑测试
pytest -v
```

工具配置在 `pyproject.toml`。

## 目录结构

```
shot_video_creation/
├── README.md                ← 本文件
├── LICENSE                  ← MIT
├── CHANGELOG.md             ← 变更日志（Keep a Changelog）
├── CONTRIBUTING.md          ← 贡献指南
├── index.json               ← Skill 清单（供 Agent 发现）
├── pyproject.toml           ← 项目元信息 + ruff/mypy/pytest 配置
├── requirements-dev.txt     ← 仅开发依赖（脚本零运行依赖）
├── .gitignore
├── gen-img/
│   ├── SKILL.md
│   └── scripts/
│       └── gen.py
├── gen-tts/
│   ├── SKILL.md
│   └── scripts/
│       └── tts.py
└── gen-video/
    ├── SKILL.md
    └── scripts/
        ├── aigc_common.py
        └── gen_volc.py
```

## 安全 / 凭据轮换

根目录 `.env` 已加入 `.gitignore`，但本机磁盘上仍是明文。一旦机器被备份/同步工具抓走，密钥即外泄。

如果怀疑密钥泄露（曾经在聊天里贴过、git 误提交、磁盘镜像外流等），请按以下顺序操作：

1. 立刻到控制台轮换（不留窗口期）
   - 火山方舟 API Key：https://console.volcengine.com/ark → 「API Key 管理」 → 禁用旧 Key、生成新 Key
   - 火山语音 App Key：https://console.volcengine.com/ → 「语音技术」 → 「应用管理」 → 重置或新建应用
2. 更新本地配置（任选一种）
   - 直接编辑根目录 `.env`（已 gitignore）
   - 或设环境变量：`IMG_API_KEY` / `TTS_APP_KEY` / `VIDEO_KEY`
3. 验证：脚本会在 `cred.get(...)` 返回空串时 `die(...)`，直接跑一次看报错即可

`config.json.example` 是占位符模板（可入库），`config.json` 是真实凭据（不入库）。

## 许可证

MIT — 详见 [LICENSE](LICENSE)。
