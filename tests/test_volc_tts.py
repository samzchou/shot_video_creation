"""volc-tts/scripts/tts.py：文本转语音核心逻辑。

覆盖范围：
  - 音色 ID → Resource ID 路由（resolve_resource_id）
  - ASR 文本清洗与相似度（_clean_for_similarity / similarity_ratio）
  - 百炼音色识别（bailian_voice）
  - 字级时间戳 schema 转换（_bailian_stream_sentences）
  - tts_requirement.md 解析（extract_tts_requirement_text / read_tts_requirement）
"""

from __future__ import annotations

import pytest

# ── 资源 ID 路由 ─────────────────────────────────────────────────────────────


class TestResolveResourceId:
    @pytest.mark.parametrize(
        "voice,expected",
        [
            # S_xxx 克隆音色 → icl 2.0
            ("S_user_abc123", "seed-icl-2.0"),
            ("S_lower_clone", "seed-icl-2.0"),
            # _uranus_bigtts 后缀（官方 2.0 音色）→ seed-tts-2.0
            ("zh_female_shuangkuaisisi_uranus_bigtts", "seed-tts-2.0"),
            ("zh_male_taocheng_uranus_bigtts", "seed-tts-2.0"),
            # saturn_ 前缀也是 2.0
            ("saturn_xyz", "seed-tts-2.0"),
            # _mars_bigtts / _moon_bigtts → 旧 1.0
            ("zh_female_old_mars_bigtts", "seed-tts-1.0"),
            ("zh_male_old_moon_bigtts", "seed-tts-1.0"),
            # ICL_ 前缀 → 旧 1.0
            ("ICL_legacy_voice", "seed-tts-1.0"),
        ],
    )
    def test_routes_correctly(self, tts, monkeypatch, voice, expected):
        # 默认值是 DEFAULT_RESOURCE_ID（seed-tts-2.0），但路由函数优先看 speaker 特征
        # 当所有特征都不命中时，回落到 cred_get("resource_id", ...) 的默认值
        monkeypatch.setattr(tts, "cred_get", lambda *a, **kw: "")
        assert tts.resolve_resource_id(voice) == expected

    def test_falls_back_to_default_when_unknown(self, tts, monkeypatch):
        # 既非 S_/_uranus/_mars/_moon/ICL_/saturn_ → 用 cred_get 的 default
        monkeypatch.setattr(tts, "cred_get", lambda *a, **kw: "seed-tts-2.0")
        assert tts.resolve_resource_id("unknown_voice") == "seed-tts-2.0"


# ── ASR 文本清洗与相似度 ─────────────────────────────────────────────────────


class TestCleanForSimilarity:
    def test_strips_punctuation(self, tts):
        assert tts._clean_for_similarity("你好，世界！") == "你好世界"

    def test_strips_whitespace(self, tts):
        assert tts._clean_for_similarity("a b\tc\n") == "abc"

    def test_keeps_chinese_and_ascii(self, tts):
        assert tts._clean_for_similarity("Hello, 世界! 123.") == "Hello世界123"


class TestSimilarityRatio:
    def test_identical_text(self, tts):
        assert tts.similarity_ratio("你好世界", "你好世界") == 1.0

    def test_both_empty(self, tts):
        # 两个空串：定义为 1.0（完全相同）
        assert tts.similarity_ratio("", "") == 1.0

    def test_one_empty(self, tts):
        assert tts.similarity_ratio("hello", "") == 0.0

    def test_partial_overlap(self, tts):
        # 清洗后比对，对顺序敏感
        sim = tts.similarity_ratio("你好世界", "你好世")
        assert 0.5 < sim < 1.0

    def test_punctuation_does_not_affect(self, tts):
        # 标点差异不应让相似度归零（清洗后再比对）
        assert tts.similarity_ratio("你好，世界！", "你好世界") == 1.0


# ── 常量与合法性 ─────────────────────────────────────────────────────────────


class TestConstants:
    def test_valid_voices_contains_known(self, tts):
        assert "zh_female_shuangkuaisisi_uranus_bigtts" in tts.VALID_VOICES
        assert "en_male_tim_uranus_bigtts" in tts.VALID_VOICES

    def test_valid_formats(self, tts):
        assert {"mp3", "pcm", "ogg_opus", "wav"} == tts.VALID_FORMATS

    def test_max_text_chars(self, tts):
        # 文档约束 ≤ 5000 字符/次
        assert tts.MAX_TEXT_CHARS == 5000
