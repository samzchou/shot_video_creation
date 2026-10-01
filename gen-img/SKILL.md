---
name: gen-img
description: 火山方舟 Seedream 图像生成与编辑（文生图/图生图/多图融合）。OpenAI 兼容 /images/generations 端点，POST 后直接拿到 URL。支持尺寸 WxH / 2K / 4K / auto、随机种子、无水印输出、1–3 张参考图融合编辑。用于封面、海报、配图、电商图、创意视觉等图像生成任务。凭据通过 .env 中 IMG_API_KEY（必填）、IMG_MODEL（可选）提供，纯 Python 标准库无第三方依赖。
---

# 火山图像生成（Seedream）

生成或编辑图片：文生图、图生图（1–3 张参考图融合/编辑）。基于火山方舟 Seedream 系列模型（默认 doubao-seedream-5-0-260128），走方舟 images/generations 端点，响应返回图片 URL（24h 有效），脚本自动下载到本地。纯 Python 标准库，无第三方依赖。

## 凭据配置

在 <技能目录>/../.env（即当前 Skill 包根目录的 .env）中填写：

| 环境变量 | 必填 | 说明 |
|---|---|---|
| IMG_API_KEY | 是 | 火山方舟 API Key，形态 ark-xxx-xxx，在 https://console.volcengine.com/ark 的「API Key 管理」生成 |
| IMG_MODEL | 否 | 模型 ID，默认 doubao-seedream-5-0-260128；可在方舟控制台「开通管理」确认可调用后改为其他 Seedream 模型 |
| IMG_BASE | 否 | 端点 base，默认 https://ark.cn-beijing.volces.com/api/v3 |

> .env 已在 .gitignore 内，不会入仓。复制 .env.example 起步即可。

## 使用

在任意工作目录（脚本会按 cwd 校验输出路径）运行：

python <技能目录>/scripts/gen.py --prompt "国潮风格奶茶海报" --image-size 2K --out-dir tmp/img

常用参数：

- --prompt：图像描述（要渲染的文字直接写完整句子）
- 编辑模式：--image（必带）可选 --image2/--image3，参考图可为公网 URL 或本地路径（自动转 data URI）
- --image-size：WxH（如 2048x2048）/ 2K / 4K / auto；编辑模式默认跟随参考图
- --seed：随机种子（火山范围 [-1, 2147483647]，-1 为随机）
- --watermark：true/false，默认 false
- --model：显式指定模型（关闭默认选择）
- --out-dir：输出目录（默认 tmp/awk-img-<时间戳>/）

输出：目录内 00.png（每张图一个文件）+ prompts.json（prompt/模型/URL 元数据）+ index.html（预览图集）。

输出路径约束：--out-dir 必须是相对路径，落在 output_videos / tmp / fragments / artifacts 或 <平台>/outputs/ 下。

## 注意

- 火山方舟 Seedream 是唯一云端来源，凭据缺失会立即报错（不再有第三方 fallback）
- 模型未在账号开通时（HTTP 403/404）会输出开通指引，附方舟控制台链接
- 参考图单张 ≤ 30 MB；本地大图建议先压缩再传
- 图片 URL 通常 24h 有效，脚本下载后本地文件长期保留
