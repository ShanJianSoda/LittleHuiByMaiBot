"""
话题跟踪（效果优先版 3.6.3）：当前话题标签与切换惩罚。

框架内为轻量实现：仅维护 current_topic / last_topic，返回 topic_factor（话题切换时乘 0.9）。
LLM 分类可由上层在具备模型时再接入，本模块仅提供 get_topic_factor 与 update_topic 接口。
"""

import time
from typing import List, Any

from src.common.logger import get_logger

logger = get_logger("mood")

# todo：优化话题权重，使其更符合中文表达，增加更多话题权重，与分类（概括器）一致。（但是群聊话题跳动复杂）
TOPIC_WEIGHTS = {
    "工作": 1.0,
    "闲聊": 1.0,
    "游戏": 1.2,
    "情感": 1.1,
    "信息": 0.9,
    "其他": 1.0,
}
DEFAULT_TOPIC = "其他"
SWITCH_PENALTY = 0.9


class TopicTracker:
    """话题状态：get_topic_factor() 用于调制情绪增量；话题切换时应用惩罚。"""

    def __init__(self, chat_id: str, cache_ttl: float = 300.0):
        self.chat_id = chat_id
        self.cache_ttl = cache_ttl
        self.current_topic = DEFAULT_TOPIC
        self.last_topic = DEFAULT_TOPIC
        self.last_topic_time = 0.0

    def set_topic(self, topic: str) -> None:
        """设置当前话题（可由上层 LLM 分类后调用）。"""
        topic = (topic or "").strip() or DEFAULT_TOPIC
        if topic not in TOPIC_WEIGHTS:
            topic = DEFAULT_TOPIC
        if topic != self.current_topic:
            self.last_topic = self.current_topic
            self.current_topic = topic
            self.last_topic_time = time.time()

    def get_topic_factor(self) -> float:
        """话题权重；若刚发生切换则乘 switch_penalty。"""
        base = TOPIC_WEIGHTS.get(self.current_topic, 1.0)
        if self.current_topic != self.last_topic:
            return base * SWITCH_PENALTY
        return base

    def update_topic(self, messages: List[Any]) -> None:
        """
        根据最近消息更新话题。框架内不调用 LLM，仅占位；
        上层可在此处调用 LLM 得到话题后 set_topic(...)。
        """
        # 占位：不执行 LLM，保持 current_topic
        pass
