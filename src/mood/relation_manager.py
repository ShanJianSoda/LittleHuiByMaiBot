"""
关系权重管理（效果优先版 3.6.2）：混合模式，relation 放大 msg 影响。

- 支持按 (chat_id, user_id) 或 user_id 配置初始权重。
- 可选在线学习：基于交互频率、回复延迟的 EMA 更新，并 clamp 到 bounds。
不依赖 mood_manager。
"""

import time
from typing import Dict, Tuple

from src.common.logger import get_logger

logger = get_logger("mood")

# (chat_id, user_id) -> weight; 或 user_id -> weight（单聊）
_default_bounds: Tuple[float, float] = (0.5, 2.0)
_default_update_rate: float = 0.3


class RelationManager:
    """关系权重：输出 w_rel ∈ [bounds[0], bounds[1]]，与 g_msg 相乘放大/减弱消息情绪影响。"""

    def __init__(
        self,
        chat_id: str,
        bounds: Tuple[float, float] = _default_bounds,
        update_rate: float = _default_update_rate,
    ):
        self.chat_id = chat_id
        self.bounds = bounds
        self.update_rate = update_rate
        # key: (chat_id, user_id) 或 user_id（str）
        self._weights: Dict[str, float] = {}
        self._last_update: Dict[str, float] = {}
        self._min_interval = 60.0  # 每用户每分钟最多更新 1 次

    def get_weight(self, user_id: str) -> float:
        """获取当前消息发送者对应的关系权重，未配置则 1.0。"""
        key = f"{self.chat_id}:{user_id}"
        return self._weights.get(key, 1.0)

    def set_weight(self, user_id: str, weight: float) -> None:
        """设置权重（管理员或初始化）。"""
        key = f"{self.chat_id}:{user_id}"
        w = max(self.bounds[0], min(self.bounds[1], weight))
        self._weights[key] = w

    def update_weight(
        self,
        user_id: str,
        interaction_freq: float = 0.0,
        reply_speed: float = 60.0,
    ) -> None:
        """
        轻量学习：根据交互频率、回复延迟更新权重。
        interaction_freq: 近期交互次数/分钟；reply_speed: 平均回复延迟（秒）。
        """
        key = f"{self.chat_id}:{user_id}"
        now = time.time()
        if now - self._last_update.get(key, 0) < self._min_interval:
            return
        self._last_update[key] = now

        current = self._weights.get(key, 1.0)
        norm_freq = min(1.0, interaction_freq / 10.0)
        norm_speed = max(0.0, (60.0 - reply_speed) / 60.0)
        factor = 0.7 * norm_freq + 0.3 * norm_speed
        new_weight = current * (1.0 - self.update_rate) + (1.0 + 0.3 * factor) * self.update_rate
        new_weight = max(self.bounds[0], min(self.bounds[1], new_weight))
        self._weights[key] = new_weight
