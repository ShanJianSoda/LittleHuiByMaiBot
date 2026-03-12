"""
心跳系统 V2 反思器。

将执行结果转成结构化反思，供经验存储和离线蒸馏使用。
"""

from __future__ import annotations

from typing import Any

from .models import ExecutionReceipt, Intent, ReflectionRecord


class ReflectionEngine:
    """根据意图、观察和执行结果生成结构化反思。"""

    def _score_receipt(self, receipt: ExecutionReceipt) -> float:
        if receipt.status == "success":
            return 1.0
        if receipt.status == "skipped":
            return -0.25
        if receipt.status == "deferred":
            return -0.1
        return -1.0

    def _build_situation_key(self, intent: Intent, state: dict[str, Any], chat_id: str) -> str:
        observation_state = state.get("observation", {})
        recent_obs = observation_state.get("by_chat", {}).get(chat_id, [])
        latest = recent_obs[0] if recent_obs else {}
        latest_source = str(latest.get("source") or "none")
        intent_guess = str(latest.get("intent_guess") or "unknown")
        notice_type = str(latest.get("metadata", {}).get("notice_type") or "none")
        return f"{intent.source}:{intent.type}:{latest_source}:{intent_guess}:{notice_type}"

    def reflect(self, *, intent: Intent, receipt: ExecutionReceipt, state: dict[str, Any]) -> ReflectionRecord:
        chat_id = intent.target_chat_id or intent.payload.get("chat_id") or "global"
        score = self._score_receipt(receipt)
        accepted = receipt.status == "success"

        if receipt.status == "success":
            adjustment = "保留当前策略，优先简洁、低打扰地重复使用。"
        elif receipt.reason == "reply_cooldown":
            adjustment = "同类场景降低打扰性，并在更长静默后再尝试。"
        elif receipt.reason == "inactive_time_range":
            adjustment = "仅在活跃时间内处理此类触发。"
        elif receipt.reason in {"send_failed", "exception"}:
            adjustment = "下次先检查通道状态或降级为不发送的内部处理。"
        else:
            adjustment = "下次遇到类似情境时先降低风险和中断性。"

        observation_state = state.get("observation", {})
        recent_obs = observation_state.get("by_chat", {}).get(chat_id, [])
        memory_state = state.get("memory", {})
        evidence = {
            "intent_payload": intent.payload,
            "matched_strategy_keys": intent.payload.get("matched_strategy_keys", []),
            "latest_observation": recent_obs[0] if recent_obs else None,
            "recent_observation_count": len(recent_obs),
            "memory_event_count": memory_state.get("event_count", 0),
            "memory_fact_count": memory_state.get("fact_count", 0),
            "strategy_count": len(memory_state.get("distilled_strategies", {}).get(chat_id, [])),
            "receipt_outputs": receipt.outputs,
            "executed_skills": receipt.outputs.get("executed_skills", []) if isinstance(receipt.outputs, dict) else [],
            "matched_skill_strategy_keys": (
                receipt.outputs.get("matched_skill_strategy_keys", []) if isinstance(receipt.outputs, dict) else []
            ),
            "selection_mode": (
                receipt.outputs.get("skill_plan_meta", {}).get("selection_mode", "")
                if isinstance(receipt.outputs, dict)
                else ""
            ),
            "selection_mode_strategy_keys": (
                receipt.outputs.get("skill_plan_meta", {}).get("selection_mode_strategy_keys", [])
                if isinstance(receipt.outputs, dict)
                else []
            ),
        }

        return ReflectionRecord.create(
            chat_id=chat_id,
            intent_id=intent.intent_id,
            receipt_id=receipt.receipt_id,
            situation_key=self._build_situation_key(intent, state, chat_id),
            action_type=receipt.action_type,
            status=receipt.status,
            score=score,
            accepted=accepted,
            reason=receipt.reason or "",
            suggested_adjustment=adjustment,
            evidence=evidence,
        )

