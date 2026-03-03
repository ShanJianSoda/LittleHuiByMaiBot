"""
心跳系统 V2 规划器。

collect_inputs、generate_intents、score_intents。
P1 最小规则：notice 触发 reply，否则 no_op；后续可接入 LLM。
"""
from __future__ import annotations

from typing import Any

from src.common.logger import get_logger

from .models import Intent


logger = get_logger("heartbeat_v2.planner")


class HeartbeatPlanner:
    """规划器：根据状态生成并排序意图。"""

    def collect_inputs(self, state: dict[str, Any]) -> dict[str, Any]:
        """透传 state，后续可在此做预处理。"""

        return state

    def score_intents(self, intents: list[Intent]) -> list[Intent]:
        """按 score、created_at 排序，高分在前。"""

        intents.sort(key=lambda x: (x.score, x.created_at), reverse=True)
        return intents

    def generate_intents(self, state: dict[str, Any]) -> list[Intent]:
        """
        P1 最小规则：
        - 若近期有 notice（poke/input_status），生成 reply intent。
        - 其余场景生成 no_op intent，保证闭环稳定可观测。
        """
        intents: list[Intent] = []
        notice_state = state.get("notice", {})
        chat_notices: dict[str, list[str]] = notice_state.get("chat_notices", {})

        for chat_id, notices in chat_notices.items():
            if "poke" in notices or "input_status" in notices:
                intents.append(
                    Intent.create(
                        source="notice",
                        intent_type="reply",
                        target_chat_id=chat_id,
                        payload={"chat_id": chat_id, "text": "我在，刚看到你啦。"},
                        dedup_key=f"notice-reply-{chat_id}",
                        expire_after_s=90,
                        lane="urgent",
                        priority=0.9,
                        urgency=0.9,
                        confidence=0.8,
                        cost_hint=0.2,
                        risk_hint=0.1,
                    )
                )

        if not intents:
            intents.append(
                Intent.create(
                    source="scheduler",
                    intent_type="no_op",
                    payload={"reason": "no_trigger"},
                    dedup_key="no-op",
                    expire_after_s=60,
                    lane="maintenance",
                    priority=0.1,
                    urgency=0.1,
                    confidence=1.0,
                )
            )

        return self.score_intents(intents)
