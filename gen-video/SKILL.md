---
name: gen-video
description: 火山方舟 Seedance 2.0 视频生成（t2v 文生视频 / i2v 图生视频 / r2v 参考视频）。直连火山异步任务端点，支持首帧/尾帧/参考图/参考视频、2–15 秒、9:16 等宽高比、720P/1080P、声画同出与上一段末帧对齐。用于短视频、人物故事、宣传片片段、商品广告等 AI 视频生成任务。凭据通过 .env 中 VIDEO_KEY（必填）提供，纯 Python 标准库无第三方依赖。
---

# 火山视频生成（Seedance 2.0）

基于火山方舟异步任务端点（POST /api/v3/contents/generations/tasks → 轮询 → 拿 video_url 下载）生成 AI 视频。默认候选链 fast → normal → mini，遇到 403/404 自动降级下一个。纯 Python 标准库，无第三方依赖；--prev-segment 首尾帧对齐需要系统安装 ffmpeg/ffprobe。

## 凭据配置

在 <技能目录>/../.env 中填写：

| 环境变量 | 必填 | 说明 |
|---|---|---|
| VIDEO_KEY | 是 | 火山方舟 API Key，形态 ark-xxx-xxx。**视频生成只认这个 Key**，不会回落 ARK_API_KEY（那个是主模型对话的 Key） |
| VIDEO_BASE | 否 | 端点 base，默认 https://ark.cn-beijing.volces.com/api/v3 |

> .env 已在 .gitignore 内。复制 .env.example 起步即可。

## 使用

在任意工作目录运行：

python <技能目录>/scripts/gen_volc.py video --prompt "一只橘猫在窗台打盹，午后阳光，舒缓钢琴曲" --duration 8 --ratio 9:16 --output tmp/cat.mp4

video 子命令可省略（参数直接跟脚本后即可）。

模式与参数：

| 模式 | 触发参数 | 说明 |
|---|---|---|
| t2v 文生视频 | 仅 --prompt | 默认 |
| i2v 图生视频 | --image（首帧）可选 --last-frame（尾帧） | 图片可为公网 URL 或本地路径 |
| i2v 首尾帧对齐 | --prev-segment 上一段.mp4 | 自动抽取上段末帧作本段首帧（与 --image 互斥），适合连续人物故事 |
| r2v 参考视频 | --ref-image / --ref-video | 参考图可为 URL/本地；参考视频必须是公网 URL |

- --duration：2–15 秒，默认 8
- --ratio：宽高比，默认 9:16（也支持 16:9、1:1、4:3 等火山支持的取值）
- --resolution：720P（默认）/ 1080P（Fast 模型仅 720P，1080P 自动跳过 fast）
- --no-audio：关闭声画同出（默认开，prompt 里可直接描述背景乐/音效）
- --model：显式指定模型 id（关闭候选链 fallback）。默认候选链：doubao-seedance-2-0-fast-260128 → doubao-seedance-2-0-260128 → doubao-seedance-2-0-mini-260615

输出约束：--output 必须是相对路径，落在 output_videos / tmp / fragments / artifacts 或 <平台>/outputs/ 下；成片下载到该路径，并生成同名 .json（含模式、候选模型、源 video_url）。

## 注意

- 任务轮询间隔 15s，最长等 900s；长时间未完成会超时退出
- HTTP 408/429/5xx 自动重试（每模型最多 3 次），模型任务失败（failed/cancelled）自动沿候选链降级
- 参考视频/音频类素材必须是公网 URL，本地视频不支持 base64
- 本地图片单张 ≤ 30 MB；总请求体建议 ≤ 4 MB（大图先压缩）
- 火山方舟是唯一云端来源，凭据缺失会立即报错
