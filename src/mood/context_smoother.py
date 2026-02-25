"""
上下文平滑（效果优先版 3.6.4）：聊天频率与消息密度的 EMA，输出 w_context。

用于刷屏时降低单条消息影响、冷场后首条保持正常敏感度。纯统计，无训练。
"""

import time
from typing import Deque
from collections import deque

from src.common.logger import get_logger

logger = get_logger("mood")


class ContextSmoother:
    """EMA 频率与密度，get_context_factor() 范围建议 [0.5, 2.0]。"""

    def __init__(
        self,
        chat_id: str,
        alpha_freq: float = 0.1,
        alpha_density: float = 0.05,
        time_window: float = 60.0,
    ):
        self.chat_id = chat_id
        self.alpha_freq = alpha_freq
        self.alpha_density = alpha_density
        self.time_window = time_window
        self.ema_freq = 1.0
        self.ema_density = 1.0
        self.last_update = time.time()
        self.message_timestamps: Deque[float] = deque(maxlen=500)

    def update(self, message_count: int = 1) -> None:
        """收到 message_count 条消息时调用，更新 EMA。"""
        now = time.time()
        dt = now - self.last_update
        if dt <= 0:
            return
        for _ in range(message_count):
            self.message_timestamps.append(now)

        freq = message_count / (dt / 60.0) if dt > 0 else 0.0
        norm_freq = min(2.0, freq / 5.0)
        cutoff = now - self.time_window
        while self.message_timestamps and self.message_timestamps[0] < cutoff:
            self.message_timestamps.popleft()
        density = len(self.message_timestamps) / self.time_window
        norm_density = min(2.0, density / 0.5)

        self.ema_freq = self.alpha_freq * norm_freq + (1.0 - self.alpha_freq) * self.ema_freq
        self.ema_density = self.alpha_density * norm_density + (1.0 - self.alpha_density) * self.ema_density
        self.last_update = now

    def get_context_factor(self) -> float:
        """组合频率与密度，返回 [0.5, 2.0] 的因子。"""
        freq_factor = 1.0 / max(0.5, self.ema_freq)
        density_factor = max(0.5, self.ema_density)
        raw = freq_factor * density_factor
        return max(0.5, min(2.0, raw))
