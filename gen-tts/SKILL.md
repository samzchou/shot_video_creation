---
name: gen-tts
description: 火山引擎豆包语音合成（方舟 openspeech v3，seed-tts-2.0）。把文本合成为 mp3 / wav / ogg_opus / pcm 音频，支持 10+ 中英文音色、语速/响度/情感上下文调节、字级字幕时间戳。用于配音、口播、短视频旁白、有声内容、视频配乐等文本转语音任务。凭据通过 .env 中 TTS_APP_KEY（推荐单头）或 TTS_APP_ID + TTS_ACCESS_KEY（旧版双头）提供，纯 Python 标准库无第三方依赖。
---

# 火山 TTS（豆包语音合成 2.0）

把配音文案/旁白文本合成为音频文件。基于火山引擎 openspeech v3，单向流式 NDJSON 响应，每行含 base64 音频分片，脚本拼装成完整音频。默认 Resource 为 \`seed-tts-2.0\`，可用 TTS_RESOURCE_ID 切换到 \`seed-icl-2.0\`（克隆音色）/ 旧版模型等。纯 Python 标准库，无第三方依赖。

## 凭据配置

在 <技能目录>/../.env 中填写（推荐新版单头）：

| 环境变量 | 必填 | 说明 |
|---|---|---|
| TTS_APP_KEY | 是（推荐） | 火山语音控制台新版单头，在 https://console.volcengine.com/ 语音技术 → 应用管理生成 |
| TTS_RESOURCE_ID | 否 | 模型版本，默认 seed-tts-2.0；克隆音色（S_xxx）切到 seed-icl-2.0 |
| TTS_BASE | 否 | 端点 base，默认 https://openspeech.bytedance.com/api/v3 |

旧版双头凭据（二选一即可，与上面 TTS_APP_KEY 互斥）：

| 环境变量 | 说明 |
|---|---|
| TTS_APP_ID + TTS_ACCESS_KEY | 旧版「App ID + Access Token」双头鉴权 |

> .env 已在 .gitignore 内。复制 .env.example 起步即可。

## 使用

在任意工作目录运行：

python <技能目录>/scripts/tts.py --text "你好，这是一段配音。" --voice zh_female_shuangkuaisisi_uranus_bigtts --format mp3 --output tmp/voice.mp3

常用参数：

- 输入：--text "文本" / --text-file 路径（.txt/.md/.srt/.vtt）/ 位置参数 fragment_dir（读取其中的 tts_requirement.md 配音文案段）
- --voice：音色 ID，默认 zh_female_shuangkuaisisi_uranus_bigtts（爽快思思）。全部内置音色见 scripts/tts.py 的 VALID_VOICES，含知性灿灿、甜美小源、Vivi、小何、暖阳女声（女），舟、小天（男），Dacey/Tim（英）
- --format：mp3（默认）/ wav / ogg_opus / pcm
- --sample-rate：8000~48000；--speech-rate：-50~100（100=2x，-50=0.5x）；--loudness：-50~100
- --context-text：情感控制上下文，如「用撒娇甜蜜的语气」
- --enable-subtitle：额外产出 .subtitle.json（字级时间戳，供逐句对齐）
- --overwrite：覆盖已存在的输出

输出路径约束：--output/--out-dir 必须是相对路径，落在 assets/audio / tmp / output_videos / fragments 或 <平台>/outputs/ 下。单次文本上限 5000 字符。

## 音色 → Resource 自动路由

- S_xxx 克隆音色 → seed-icl-2.0
- _uranus_bigtts 后缀 / saturn_ 前缀（官方 2.0 音色） → seed-tts-2.0
- _mars_bigtts / _moon_bigtts / ICL_ 前缀（旧版 1.0 音色） → seed-tts-1.0
- 其余走 TTS_RESOURCE_ID（默认 seed-tts-2.0）

## 注意

- 文本超长需自行分段多次调用（5000 字符/次）
- HTTP 4xx 检查鉴权与 resource_id 是否开通；429/5xx 限流或服务端异常，稍后重试
- 音频时长统计依赖系统 ffprobe（缺失返回 0.0，不影响生成）
- 火山 openspeech v3 是唯一云端来源，凭据缺失会立即报错
