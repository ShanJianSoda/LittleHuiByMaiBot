"""
心跳系统 V2 执行器。

intent_to_plan、execute_plan、record_receipt。
执行前经 PolicyGate 检查，执行后产出 ExecutionReceipt。
"""
from __future__ import annotations

import time

from src.common.logger import get_logger

from .models import ExecutionReceipt, Intent, Plan
from .policy_gate import PolicyGate
from .router import ActionRouter


logger = get_logger("heartbeat_v2.executor")


class HeartbeatExecutor:
    """执行器：Intent -> Plan -> 执行 -> Receipt。"""

    def __init__(self, *, policy_gate: PolicyGate, router: ActionRouter):
        self.policy_gate = policy_gate
        self.router = router

    def intent_to_plan(self, intent: Intent) -> Plan:
        """将 Intent 展开为可执行的 Plan。"""
        if intent.type == "reply":
            return Plan.create(
                intent_id=intent.intent_id,
                action_type="reply",
                action_args={
                    "chat_id": intent.target_chat_id or intent.payload.get("chat_id", ""),
                    "text": intent.payload.get("text", ""),
                },
                policy_tags=["normal"],
                timeout_s=30,
            )
        return Plan.create(
            intent_id=intent.intent_id,
            action_type="no_op",
            action_args={"reason": intent.payload.get("reason", "planner_noop")},
            policy_tags=["safe_noop"],
            timeout_s=5,
        )

    async def execute(self, intent: Intent, state: dict | None = None) -> ExecutionReceipt:
        """执行单个 Intent，返回 ExecutionReceipt。"""

        plan = self.intent_to_plan(intent)
        return await self.execute_plan(plan, state or {})

    async def execute_plan(self, plan: Plan, state: dict) -> ExecutionReceipt:
        """执行 Plan：先 PolicyGate 检查，再 Router 执行，最后 record_receipt。"""
        start = time.time()
        allowed, reason = self.policy_gate.allow(plan, state)
        if not allowed:
            return self.record_receipt(
                plan=plan,
                status="skipped",
                reason=reason,
                outputs={},
                latency_ms=int((time.time() - start) * 1000),
            )

        ok, route_reason, outputs = await self.router.route(plan.action_type, plan.action_args)
        return self.record_receipt(
            plan=plan,
            status="success" if ok else "failed",
            reason=route_reason,
            outputs=outputs,
            latency_ms=int((time.time() - start) * 1000),
        )

    def record_receipt(
        self,
        *,
        plan: Plan,
        status: str,
        reason: str,
        outputs: dict,
        latency_ms: int,
    ) -> ExecutionReceipt:
        """创建并返回 ExecutionReceipt，用于历史与反馈。"""
        receipt = ExecutionReceipt.create(
            plan_id=plan.plan_id,
            intent_id=plan.intent_id,
            action_type=plan.action_type,
            status=status,  # type: ignore[arg-type]
            reason=reason,
            outputs=outputs,
            latency_ms=latency_ms,
        )
        logger.debug(
            f"[executor] receipt status={receipt.status}, action={receipt.action_type}, "
            f"reason={receipt.reason}, latency_ms={receipt.latency_ms}"
        )
        return receipt
