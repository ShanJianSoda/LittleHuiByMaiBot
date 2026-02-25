"""
VAD 词典加载模块（效果优先版 3.1 节）

加载 NRC-VAD 词典，提供词条 valence / arousal / dominance 查询。
词典文件缺失时返回空表，estimate 由 mood_estimator 处理。
"""

import json
import os
from typing import Dict, Tuple

from src.common.logger import get_logger

logger = get_logger("mood")

# 项目根目录
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_DEFAULT_LEXICON_PATH = os.path.join(_PROJECT_ROOT, "data", "emotion", "nrc_vad_internal.json")

_lexicon: Dict[str, Dict[str, float]] | None = None
_custom_path: str | None = None


def load_vad_lexicon(path: str | None = None) -> Dict[str, Dict[str, float]]:
    """
    加载 NRC-VAD 词典。格式: { "word": { "v": float, "a": float, "d": float } }，值域 [-1, 1]。
    若文件不存在则返回空 dict，不抛异常。
    """
    global _lexicon
    if _lexicon is not None:
        return _lexicon

    p = path if path is not None else (_custom_path or _DEFAULT_LEXICON_PATH)
    if not os.path.isfile(p):
        logger.debug(f"VAD 词典文件不存在，使用空表: {p}")
        _lexicon = {}
        return _lexicon

    try:
        with open(p, "r", encoding="utf-8") as f:
            raw = json.load(f)
        _lexicon = {}
        for word, vals in raw.items():
            if isinstance(vals, dict):
                _lexicon[str(word)] = {
                    "v": float(vals.get("v", 0.0)),
                    "a": float(vals.get("a", 0.0)),
                    "d": float(vals.get("d", 0.0)),
                }
            else:
                _lexicon[str(word)] = {"v": 0.0, "a": 0.0, "d": 0.0}
        logger.debug(f"VAD 词典已加载: {p}, 词条数 {len(_lexicon)}")
    except Exception as e:
        logger.warning(f"加载 VAD 词典失败: {e}，使用空表")
        _lexicon = {}

    return _lexicon


def get_lexicon() -> Dict[str, Dict[str, float]]:
    """获取已加载的词典（懒加载）。"""
    return load_vad_lexicon()


def set_lexicon_path(path: str | None) -> None:
    """设置词典路径并清空缓存，下次 get_lexicon 时从新路径加载。path=None 恢复默认路径。"""
    global _lexicon, _custom_path
    _lexicon = None
    _custom_path = path
