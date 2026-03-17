"""
图片/表情处理限流：在指定时间窗口内，每个聊天流最多处理 N 张图片（含表情），
超出则不再调用 VLM/落盘，直接返回占位文案，避免内存爆掉导致进程退出。
"""

import time
from collections import deque
from typing import Dict

from src.common.logger import get_logger
from src.config.config import global_config

logger = get_logger("image_rate_limiter")

# 每个 stream_id 一个 deque，只保留窗口内的时间戳
_stream_timestamps: Dict[str, deque] = {}


def _get_config():
    c = getattr(global_config, "message_receive", None)
    if c is None:
        return True, 10, 60
    enabled = getattr(c, "image_rate_limit_enabled", True)
    max_count = getattr(c, "image_rate_limit_max", 10)
    window_sec = getattr(c, "image_rate_limit_window_seconds", 60)
    return enabled, max_count, window_sec


def allow_image_processing(stream_id: str) -> bool:
    """
    判断当前是否允许对该聊天流再处理一张图片/表情（VLM+存储）。
    若允许，调用方应在实际处理完成后调用 record_image_processed(stream_id)。
    """
    enabled, max_count, window_sec = _get_config()
    if not enabled or max_count <= 0 or window_sec <= 0:
        return True
    now = time.time()
    if stream_id not in _stream_timestamps:
        _stream_timestamps[stream_id] = deque(maxlen=max_count * 2)
    q = _stream_timestamps[stream_id]
    # 丢弃窗口外的时间戳
    while q and q[0] < now - window_sec:
        q.popleft()
    return len(q) < max_count


def record_image_processed(stream_id: str) -> None:
    """记录该聊天流刚处理了一张图片/表情，用于限流计数。"""
    enabled, _, window_sec = _get_config()
    if not enabled:
        return
    now = time.time()
    if stream_id not in _stream_timestamps:
        _stream_timestamps[stream_id] = deque(maxlen=100)
    _stream_timestamps[stream_id].append(now)


def get_placeholder_text(segment_type: str = "image") -> str:
    """被限流时返回的占位文案（不触发 VLM/存储）。"""
    if segment_type == "emoji":
        return "[表情]"
    return "[图片]"
