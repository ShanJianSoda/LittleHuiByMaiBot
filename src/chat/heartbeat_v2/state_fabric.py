"""
心跳系统 V2 状态汇聚层。

从 mood、memory、notice、chat、history 等模块收集状态，
供 Planner 生成意图使用。
"""
from __future__ import annotations

import time
from typing import Any

from src.chat.message_receive.chat_stream import get_chat_manager
from src.common.database.database_model import Messages
from src.common.logger import get_logger
from src.mood.mood_manager import mood_manager


logger = get_logger("heartbeat_v2.state_fabric")


class StateFabric:
    """状态汇聚：collect_* 系列方法聚合各维度状态。"""

    def collect_mood_state(self) -> dict[str, Any]:
        """收集情绪状态（mood_state、emotion_v/a/d）。"""

        try:
            mood = mood_manager.get_mood_by_chat_id("global")
            return {
                "mood_state": getattr(mood, "mood_state", ""),
                "emotion_v": getattr(mood, "emotion_v", None),
                "emotion_a": getattr(mood, "emotion_a", None),
                "emotion_d": getattr(mood, "emotion_d", None),
            }
        except Exception as e:  # noqa: BLE001
            logger.error(f"collect_mood_state failed: {e}")
            return {"mood_state": "unknown"}

    def collect_memory_state(self) -> dict[str, Any]:
        """收集记忆相关状态（P1 最小实现，后续接入 memory 指标）。"""

        # P1 最小实现：先保留可扩展结构，后续接入 memory 指标。
        return {"memory_ready": True}

    def collect_notice_state(self, window_seconds: int = 120) -> dict[str, Any]:
        """收集近期通知（戳一戳、输入状态、撤回等）按 chat 聚合。"""

        now = time.time()
        try:
            notices = (
                Messages.select(Messages.chat_id, Messages.notice_type, Messages.time)
                .where(
                    (Messages.is_notice == True)  # noqa: E712
                    & (Messages.time >= now - window_seconds)
                )
                .order_by(Messages.time.desc())
            )
            by_chat: dict[str, list[str]] = {}
            total = 0
            for item in notices:
                total += 1
                by_chat.setdefault(item.chat_id, []).append(item.notice_type or "unknown")
            return {"total_notices": total, "chat_notices": by_chat}
        except Exception as e:  # noqa: BLE001
            logger.error(f"collect_notice_state failed: {e}")
            return {"total_notices": 0, "chat_notices": {}}

    def collect_history_state(self, recent_history: list[dict[str, Any]]) -> dict[str, Any]:
        """收集最近 N 次心跳历史摘要。"""

        return {
            "recent_tick_count": len(recent_history),
            "last_tick": recent_history[-1] if recent_history else None,
        }

    def collect_chat_state(self) -> dict[str, Any]:
        """收集各聊天流最后消息时间、静默时长等。"""

        manager = get_chat_manager()
        now = time.time()
        chats: list[dict[str, Any]] = []
        for stream_id, stream in manager.streams.items():
            try:
                last_msg = (
                    Messages.select(Messages.time, Messages.processed_plain_text)
                    .where(Messages.chat_id == stream_id)
                    .order_by(Messages.time.desc())
                    .limit(1)
                    .first()
                )
                if not last_msg:
                    continue
                chats.append(
                    {
                        "chat_id": stream_id,
                        "platform": getattr(stream, "platform", ""),
                        "is_group": bool(getattr(stream, "group_info", None)),
                        "last_message_time": float(last_msg.time),
                        "last_message_text": (last_msg.processed_plain_text or "")[:200],
                        "silent_for_s": max(0, int(now - float(last_msg.time))),
                    }
                )
            except Exception as e:  # noqa: BLE001
                logger.error(f"collect_chat_state failed for {stream_id}: {e}")
        chats.sort(key=lambda x: x["last_message_time"], reverse=True)
        return {"chats": chats, "chat_count": len(chats), "now": now}

    def collect_all(self, recent_history: list[dict[str, Any]]) -> dict[str, Any]:
        """汇总所有维度状态，供 Planner 使用。"""

        return {
            "mood": self.collect_mood_state(),
            "memory": self.collect_memory_state(),
            "notice": self.collect_notice_state(),
            "history": self.collect_history_state(recent_history),
            "chat": self.collect_chat_state(),
        }
