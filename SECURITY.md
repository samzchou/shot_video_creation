# Security Policy

## Supported Versions

下表列出当前获得安全更新支持的版本。建议生产使用 Latest。

| Version | Supported          |
|---------|--------------------|
| Latest  | :white_check_mark: |
| < 0.1.0 | :x:                |

## Reporting a Vulnerability

**请勿在 GitHub Issues / Discussions 公开漏洞细节。**

### 私密报告渠道（首选）

GitHub Security Advisories（私有漏洞披露）：

> https://github.com/<owner>/shot_video_creation/security/advisories/new

在该页面点击「Open a draft security advisory」，按模板填写。维护者会在 72 小时内确认。

### 邮件（备选）

如果不便使用上述渠道，发邮件到仓库所有者邮箱（见 GitHub profile）。

### 报告时请附上

- 受影响版本（commit hash / tag）
- 复现步骤
- 漏洞类型与潜在影响
- 是否已自行披露 / 利用

我们会在确认后 14 天内给出修复时间表，修复发布后公开致谢（除非你要求匿名）。

---

## API Key 与凭据安全

本项目**不内置任何凭据**。所有火山方舟 / 豆包 API Key 都由用户通过本地 `.env` 提供，**不会**通过代码或 CI 传递。

### 你必须遵守的几条

1. **永远不要把 `.env` 提交到 Git**——`.gitignore` 已默认排除，但请确认你的 fork / 分支也没有意外跟踪
2. **不要把 Key 贴在 Issue / PR / Discussions / commit message**——任何公开历史都视为已泄漏，必须轮换
3. **周期性轮换**——方舟控制台 → API Key 管理 → 禁用旧 Key / 生成新 Key，旧 Key 立即从 `.env` 移除
4. **最小权限**——给每个 Skill 单独的 Key（`IMG_API_KEY` / `TTS_APP_KEY` / `VIDEO_KEY`），出问题时可以单独禁用

### 已知历史泄漏（已处理）

> 2025 年某次内部会话中，`ark-d06d46fb-*` 这把 Key 曾出现在 `.codex/HANDOFF.md` 的对话历史里。
> 该 Key 已轮换；`HANDOFF.md` 已加入 `.gitignore`，不会随代码发布。
> 如果你在 fork / 历史 commit 里仍然看到这把 Key，**请立即到方舟控制台禁用**。

---

## SKILL.md / frontmatter 安全

本仓库所有 `SKILL.md` 必须以 `\n---\n` 开头，且**不得带 UTF-8 BOM**（详见 [README 常见踩坑](README.md#常见踩坑skillmd-不被-codex-发现)）。

如果 Codex 不识别某个 Skill，不要通过添加恶意 frontmatter 字段绕过——直接按文档检查 BOM / frontmatter 即可。

---

## 供应链安全

- 运行时依赖：**零第三方依赖**（仅 Python 标准库），不会引入供应链风险
- 开发依赖（仅 lint / test 用，CI 可见）：`requirements-dev.txt` 列出了版本范围
- CI：`.github/workflows/test.yml` 跑 ruff + pytest，可信执行环境是 GitHub-hosted ubuntu-latest

---

## 致谢

感谢所有负责任地披露漏洞的研究者。
