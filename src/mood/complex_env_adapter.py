"""
复杂环境适配层（效果优先版 3.6 节）：为情绪评估提供 w_rel、w_topic、w_context。

在消息处理层/规划层前调用，输出因子供 E_input = w_rel · w_topic · w_context · g_msg(msg) 使用。
不依赖 mood_manager。
"""

from typing import Tuple, List, Any

from src.common.logger import get_logger
from src.mood.relation_manager import RelationManager
from src.mood.topic_tracker import TopicTracker
from src.mood.context_smoother import ContextSmoother

logger = get_logger("mood")

_adapters: dict[str, "ComplexEnvAdapter"] = {}


class ComplexEnvAdapter:
    """单 chat 的环境适配：Relation + Topic + Context，输出 (w_rel, w_topic, w_context)。"""

    def __init__(self, chat_id: str):
        self.chat_id = chat_id
        self.relation = RelationManager(chat_id)
        self.topic = TopicTracker(chat_id)
        self.context = ContextSmoother(chat_id)

    def get_factors(
        self,
        user_id: str,
        message_count: int = 1,
    ) -> Tuple[float, float, float]:
        """
        获取当前消息对应的环境因子 (w_rel, w_topic, w_context)。
        可在情绪更新前调用；内部会更新 context 的统计。
        """
        self.context.update(message_count)
        w_rel = self.relation.get_weight(user_id)
        w_topic = self.topic.get_topic_factor()
        w_context = self.context.get_context_factor()
        return (w_rel, w_topic, w_context)

    def update_relation(self, user_id: str, interaction_freq: float = 0.0, reply_speed: float = 60.0) -> None:
        """关系权重轻量学习（可选）。"""
        self.relation.update_weight(user_id, interaction_freq, reply_speed)

    def update_topic(self, messages: List[Any]) -> None:
        """话题更新（占位；上层可在此前调用 set_topic）。"""
        self.topic.update_topic(messages)


def get_complex_env_adapter(chat_id: str) -> ComplexEnvAdapter:
    """按 chat_id 获取或创建环境适配器。"""
    if chat_id not in _adapters:
        _adapters[chat_id] = ComplexEnvAdapter(chat_id)
    return _adapters[chat_id]
