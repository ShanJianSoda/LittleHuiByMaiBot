"""
心跳系统 V2 执行器。

intent_to_plan、execute_plan、record_receipt。
执行前经 PolicyGate 检查，执行后产出 ExecutionReceipt。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from src.common.logger import get_logger
from src.config.config import global_config

from .models import ExecutionReceipt, Intent, Plan, PlanExecutionState, PlanGraph, PlanNode, PlanStepState
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
                    "reply_reason": intent.payload.get("reply_reason", ""),
                    "observation": intent.payload.get("observation", {}),
                    "use_reply_generator": intent.payload.get("use_reply_generator", False),
                    "think_level": intent.payload.get("think_level", 1),
                    "enable_tool": intent.payload.get("enable_tool", False),
                    "extra_info": intent.payload.get("extra_info", ""),
                    "unknown_words": intent.payload.get("unknown_words", []),
                },
                policy_tags=["normal"],
                timeout_s=30,
            )
        if intent.type == "retrieve":
            return Plan.create(
                intent_id=intent.intent_id,
                action_type="find_memory",
                action_args={
                    "chat_id": intent.target_chat_id or intent.payload.get("chat_id", ""),
                    "query": intent.payload.get("query", ""),
                },
                policy_tags=["memory_probe"],
                timeout_s=15,
            )
        if intent.type == "tool_call":
            return Plan.create(
                intent_id=intent.intent_id,
                action_type="tool_call",
                action_args={
                    "chat_id": intent.target_chat_id or intent.payload.get("chat_id", ""),
                    "query": intent.payload.get("query", ""),
                    "observation": intent.payload.get("observation", {}),
                },
                policy_tags=["tool_use"],
                timeout_s=30,
            )
        if intent.type == "explore":
            search_timeout = max(60, int(getattr(global_config.heartbeat, "search_web_timeout_seconds", 120)))
            return Plan.create(
                intent_id=intent.intent_id,
                action_type="search_web",
                action_args={
                    "chat_id": intent.target_chat_id or intent.payload.get("chat_id", ""),
                    "source_chat_id": intent.payload.get("source_chat_id", ""),
                    "query": intent.payload.get("query", ""),
                    "observation": intent.payload.get("observation", {}),
                    "explore_reason": intent.payload.get("explore_reason", ""),
                    "goal_candidate": intent.payload.get("goal_candidate", {}),
                    "share_target_chat_id": intent.payload.get("share_target_chat_id", ""),
                    "selector_reason": intent.payload.get("selector_reason", ""),
                    "selector_score": intent.payload.get("selector_score", 0.0),
                    "selector_mode": intent.payload.get("selector_mode", ""),
                    "selector_candidates_preview": intent.payload.get("selector_candidates_preview", []),
                },
                policy_tags=["tool_use", "active_explore"],
                timeout_s=search_timeout,
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

    def build_linear_plan_graph(
        self,
        *,
        intent_id: str,
        steps: list[dict[str, Any]],
    ) -> PlanGraph:
        nodes = []
        previous_node_id = ""
        for step in steps:
            action_type = str(step.get("action_type") or "").strip()
            if not action_type:
                continue
            depends_on = [previous_node_id] if previous_node_id else []
            plan_node = PlanNode.create(
                action_type=action_type,
                action_args=step.get("action_args", {}),
                depends_on=depends_on,
            )
            previous_node_id = plan_node.node_id
            nodes.append(plan_node)
        return PlanGraph.create(intent_id=intent_id, nodes=nodes)

    def _render_node_args(self, action_args: dict[str, Any], shared_context: dict[str, Any]) -> dict[str, Any]:
        rendered: dict[str, Any] = {}
        for key, value in action_args.items():
            if isinstance(value, str) and value.startswith("$context."):
                rendered[key] = shared_context.get(value[len("$context.") :], "")
            else:
                rendered[key] = value
        return rendered

    def _update_plan_state(
        self,
        execution_state: PlanExecutionState,
        step_state: PlanStepState,
        *,
        status: str,
        reason: str = "",
        outputs: dict[str, Any] | None = None,
        receipt_id: str | None = None,
    ) -> None:
        now = time.time()
        step_state.status = status  # type: ignore[assignment]
        step_state.finished_at = now
        step_state.reason = reason
        step_state.outputs = outputs or {}
        step_state.receipt_id = receipt_id
        execution_state.updated_at = now
        execution_state.current_node_id = step_state.node_id

    async def execute_plan_graph(
        self,
        graph: PlanGraph,
        state: dict | None = None,
    ) -> tuple[PlanExecutionState, list[ExecutionReceipt]]:
        """顺序执行多步计划图，先提供轻量状态层与共享上下文。"""

        execution_state = PlanExecutionState.create(
            plan_id=graph.plan_id,
            intent_id=graph.intent_id,
            step_states=[
                PlanStepState(node_id=node.node_id, action_type=node.action_type)
                for node in graph.nodes
            ],
            shared_context={},
        )
        receipts: list[ExecutionReceipt] = []
        execution_state.status = "running"

        step_map = {step.node_id: step for step in execution_state.step_states}
        for node in graph.nodes:
            step_state = step_map[node.node_id]
            unmet_dependencies = [
                dependency_id
                for dependency_id in node.depends_on
                if step_map.get(dependency_id) is None or step_map[dependency_id].status != "success"
            ]
            if unmet_dependencies:
                self._update_plan_state(
                    execution_state,
                    step_state,
                    status="blocked",
                    reason=f"unmet_dependencies:{','.join(unmet_dependencies)}",
                )
                execution_state.status = "blocked"
                break

            step_state.status = "running"
            step_state.started_at = time.time()
            execution_state.current_node_id = node.node_id
            rendered_args = self._render_node_args(node.action_args, execution_state.shared_context)
            plan = Plan.create(
                intent_id=graph.intent_id,
                action_type=node.action_type,  # type: ignore[arg-type]
                action_args=rendered_args,
                policy_tags=["multi_step_plan"],
                timeout_s=45,
            )
            receipt = await self.execute_plan(plan, state or {})
            receipts.append(receipt)
            self._update_plan_state(
                execution_state,
                step_state,
                status="success" if receipt.status == "success" else "failed",
                reason=str(receipt.reason or ""),
                outputs=receipt.outputs,
                receipt_id=receipt.receipt_id,
            )
            execution_state.shared_context[f"{node.node_id}.outputs"] = receipt.outputs
            for output_key, output_value in receipt.outputs.items():
                execution_state.shared_context[f"{node.node_id}.{output_key}"] = output_value
            if receipt.status != "success":
                execution_state.status = "failed"
                break
        else:
            execution_state.status = "success"

        return execution_state, receipts

    def _build_receipt_outputs(self, plan: Plan, outputs: dict[str, Any] | None = None) -> dict[str, Any]:
        result = outputs.copy() if isinstance(outputs, dict) else {}
        for key in (
            "chat_id",
            "source_chat_id",
            "share_target_chat_id",
            "query",
            "text",
            "reply_reason",
            "explore_reason",
            "goal_signature",
            "selector_reason",
            "selector_score",
            "selector_mode",
            "goal_candidate",
        ):
            if key not in result and key in plan.action_args:
                result[key] = plan.action_args.get(key)
        return result

    def _describe_plan(self, plan: Plan) -> str:
        chat_id = str(plan.action_args.get("chat_id") or "").strip()
        query = str(plan.action_args.get("query") or plan.action_args.get("text") or "").strip()
        preview = query[:80] if query else ""
        return (
            f"plan_id={plan.plan_id} intent_id={plan.intent_id} action={plan.action_type} "
            f"chat_id={chat_id or '-'} timeout_s={plan.timeout_s}"
            + (f" preview={preview}" if preview else "")
        )

    async def execute_plan(self, plan: Plan, state: dict) -> ExecutionReceipt:
        """执行 Plan：先 PolicyGate 检查，再 Router 执行，最后 record_receipt。"""
        start = time.time()
        allowed, reason = self.policy_gate.allow(plan, state)
        if not allowed:
            return self.record_receipt(
                plan=plan,
                status="skipped",
                reason=reason,
                outputs=self._build_receipt_outputs(plan),
                latency_ms=int((time.time() - start) * 1000),
            )

        desc = self._describe_plan(plan)
        logger.debug(f"[executor] start {desc}")
        if plan.action_type == "search_web":
            logger.info(f"[executor] search_web start {desc}")
        try:
            ok, route_reason, outputs = await asyncio.wait_for(
                self.router.route(plan.action_type, plan.action_args),
                timeout=max(1, int(plan.timeout_s)),
            )
            latency_ms = int((time.time() - start) * 1000)
            if plan.action_type == "search_web":
                logger.info(
                    f"[executor] search_web done plan_id={plan.plan_id} chat_id={plan.action_args.get('chat_id') or '-'} "
                    f"ok={ok} reason={route_reason} elapsed_ms={latency_ms}"
                )
            return self.record_receipt(
                plan=plan,
                status="success" if ok else "failed",
                reason=route_reason,
                outputs=self._build_receipt_outputs(plan, outputs),
                latency_ms=latency_ms,
            )
        except TimeoutError:
            latency_ms = int((time.time() - start) * 1000)
            if plan.action_type == "search_web":
                logger.error(
                    f"[executor] search_web timeout {desc} elapsed_ms={latency_ms} (limit={plan.timeout_s}s)"
                )
            else:
                logger.error(f"[executor] timeout {desc} elapsed_ms={latency_ms}")
            return self.record_receipt(
                plan=plan,
                status="failed",
                reason="timeout",
                outputs=self._build_receipt_outputs(
                    plan,
                    {
                        "error": f"{plan.action_type} timed out after {plan.timeout_s}s",
                        "timeout_s": plan.timeout_s,
                        "elapsed_ms": latency_ms,
                    },
                ),
                latency_ms=latency_ms,
            )
        except Exception as e:  # noqa: BLE001
            latency_ms = int((time.time() - start) * 1000)
            logger.error(f"[executor] exception {self._describe_plan(plan)}: {e}", exc_info=True)
            return self.record_receipt(
                plan=plan,
                status="failed",
                reason="exception",
                outputs=self._build_receipt_outputs(
                    plan,
                    {
                        "error": str(e),
                        "elapsed_ms": latency_ms,
                    },
                ),
                latency_ms=latency_ms,
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
