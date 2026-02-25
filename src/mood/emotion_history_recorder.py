"""
情绪变更历史写入：在 mood 更新/回归时可选写入 emotion_history 表。

由 config.mood.enable_emotion_history 控制；不修改 mood_manager 核心逻辑，
仅在状态变更后追加一次记录（可后续扩展为异步或采样）。
"""

from typing import Optional

from src.common.database.database_model import EmotionHistory
from src.common.logger import get_logger
from src.config.config import global_config

logger = get_logger("mood")


def record_emotion_change(
    chat_id: str,
    mood_state: str,
    source: str,
    ts: float,
    v: Optional[float] = None,
    a: Optional[float] = None,
    d: Optional[float] = None,
) -> None:
    """
    在情绪状态变更后写入一条 emotion_history 记录（若配置开启）。

    Args:
        chat_id: 聊天流 ID
        mood_state: 情绪文本描述
        source: 来源，建议 "message" | "regress"
        ts: 变更时间戳
        v, a, d: 可选 VAD 数值，[-1, 1]；未提供则不写入
    """
    if not getattr(global_config.mood, "enable_emotion_history", False):
        return
    try:
        EmotionHistory.create(
            chat_id=chat_id,
            ts=ts,
            mood_state=mood_state or "",
            source=source,
            v=v,
            a=a,
            d=d,
        )
        logger.debug(f"emotion_history 已记录: chat_id={chat_id} source={source} ts={ts}")
    except Exception as e:
        logger.warning(f"emotion_history 写入失败（不影响情绪更新）: {e}")
