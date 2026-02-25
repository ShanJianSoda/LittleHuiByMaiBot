"""
情绪估计（感知层）：词典快速计算 V/A/D（效果优先版 3.1 节）

基于 NRC-VAD 词典对文本做词语级加权平均，返回 (v, a, d, confidence)。
不依赖 mood_manager，可被 emotion_engine 或其它模块调用。
"""

import re
from typing import Tuple

from src.mood.vad_lexicon import get_lexicon

try:
    import jieba
    _HAS_JIEBA = True
except ImportError:
    _HAS_JIEBA = False


def _tokenize(text: str) -> list[str]:
    """简单分词：有 jieba 用 jieba，否则按空格/标点切分并过滤短词。"""
    if not text or not text.strip():
        return []
    text = text.strip()
    if _HAS_JIEBA:
        return [w for w in jieba.cut(text) if w.strip()]
    # fallback: 按非字母数字、非汉字切分，保留长度>=2 的片段
    parts = re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z0-9]+", text)
    return [p for p in parts if len(p) >= 2]


def estimate_from_lexicon(text: str) -> Tuple[float, float, float, float]:
    """
    使用 NRC-VAD 词典估计文本情绪。
    返回: (valence, arousal, dominance, confidence)，值域 [-1, 1]，confidence [0, 1]。
    """
    # todo：text过滤用户昵称等内容
    words = _tokenize(text)
    lexicon = get_lexicon()
    if not lexicon:
        return (0.0, 0.0, 0.0, 0.0)

    total_v = total_a = total_d = 0.0
    matched = 0
    for w in words:
        w = w.strip()
        if len(w) < 2:
            continue
        if w in lexicon:
            total_v += lexicon[w]["v"]
            total_a += lexicon[w]["a"]
            total_d += lexicon[w]["d"]
            matched += 1

    if matched == 0:
        return (0.0, 0.0, 0.0, 0.0)

    v = total_v / matched
    a = total_a / matched
    d = total_d / matched
    confidence = min(1.0, matched / 10.0)
    return (
        round(v, 4),
        round(a, 4),
        round(d, 4),
        round(confidence, 4),
    )
