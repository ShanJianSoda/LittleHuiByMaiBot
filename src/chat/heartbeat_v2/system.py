"""
心跳系统 V2 主系统。

HeartbeatV2System：fast/normal/slow 三档循环，
normal 主规划循环 collect -> generate -> queue -> execute -> receipt -> history。
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from src.common.logger import get_logger
from src.config.config import global_config

from .executor import HeartbeatExecutor
from .experience_store import experience_store
from .history_store import HistoryStore
from .ingress_queue import ObservationIngressQueue
from .intent_queue import IntentQueue
from .models import ExecutionReceipt, HeartbeatTickMeta, Intent, Observation
from .planner import HeartbeatPlanner
from .policy_gate import PolicyGate
from .reflection import ReflectionEngine
from .router import ActionRouter
from .state_fabric import StateFabric


logger = get_logger("heartbeat_v2.system")


class HeartbeatV2System:
    """心跳 V2 系统：三档循环 + 意图队列 + 执行回流闭环。"""

    def __init__(self) -> None:
        cfg = global_config.heartbeat

        self.state_fabric = StateFabric()
        self.intent_queue = IntentQueue(
            max_ready=cfg.queue_max_ready,
            max_delayed=cfg.queue_max_delayed,
            dedup_window_s=cfg.dedup_window_seconds,
        )
        self.observation_ingress_queue = ObservationIngressQueue(
            max_items=max(cfg.queue_max_ready, 128),
            dedup_window_s=cfg.dedup_window_seconds,
            keep_recent_s=max(180, int(cfg.normal_interval) * 6),
        )
        self.history_store = HistoryStore(max_items=cfg.history_max_items)
        self.planner = HeartbeatPlanner()
        self.policy_gate = PolicyGate(
            min_reply_interval_seconds=cfg.min_reply_interval_seconds,
            active_time_ranges=cfg.active_time_ranges,
        )
        self.router = ActionRouter()
        self.executor = HeartbeatExecutor(policy_gate=self.policy_gate, router=self.router)
        self.reflection_engine = ReflectionEngine()
        self.experience_store = experience_store

        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._normal_tick_count = 0

    def _log_task_done(self, task: asyncio.Task) -> None:
        """任务结束回调：记录取消、异常或正常退出，便于生命周期观测。"""
        task_name = task.get_name()
        if task.cancelled():
            logger.info(f"[v2 task] done name={task_name} (cancelled)")
            return
        try:
            exception = task.exception()
        except Exception as e:  # noqa: BLE001
            logger.error(f"[v2 task] done name={task_name} (inspect failed): {e}", exc_info=True)
            return
        if exception is not None:
            logger.error(
                f"[v2 task] done name={task_name} (crashed): {exception}",
                exc_info=(type(exception), exception, exception.__traceback__),
            )
            return
        logger.info(f"[v2 task] done name={task_name} (exited)")

    def _summarize_intent(self, intent: Intent) -> str:
        payload = intent.payload if isinstance(intent.payload, dict) else {}
        chat_id = str(intent.target_chat_id or payload.get("chat_id") or "").strip() or "-"
        query = str(payload.get("query") or payload.get("text") or "").strip()
        preview = query[:80] if query else ""
        return (
            f"intent_id={intent.intent_id} type={intent.type} chat_id={chat_id}"
            + (f" preview={preview}" if preview else "")
        )

    def ingest_observation(self, observation: Observation) -> bool:
        """接收预处理后的 observation，送入 heartbeat ingress queue。"""

        if not observation.chat_id:
            return False
        accepted = self.observation_ingress_queue.enqueue(observation)
        return accepted > 0

    def ingest_message(self, message: object) -> bool:
        """桥接消息链路：将处理后的 message 转成 observation 投递到 heartbeat，供 normal 循环使用。

        要求 message 具备 chat_id 或 chat_stream.stream_id，否则无法关联会话会被拒绝。
        """

        try:
            observation = self.state_fabric.build_observation(message)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[v2 ingress] build observation failed: {e}", exc_info=True)
            return False
        if not observation.chat_id:
            logger.warning(
                "[v2 ingress] observation has no chat_id, reject (message may lack chat_stream or stream_id)"
            )
            return False
        logger.info(
            f"[v2 ingress] observation built chat_id={observation.chat_id} message_id={observation.message_id} source={observation.source}"
        )
        accepted = self.ingest_observation(observation)
        if accepted:
            logger.debug(
                f"[v2 ingress] accepted chat_id={observation.chat_id} "
                f"message_id={observation.message_id} source={observation.source}"
            )
        return accepted

    def _summarize_memory_followup(self, receipt: ExecutionReceipt) -> str:
        memories = receipt.outputs.get("memories", [])
        if not isinstance(memories, list) or not memories:
            return "我刚试着回忆了一下，但暂时没找到特别明确的线索。"

        top = memories[0] if isinstance(memories[0], dict) else {}
        theme = str(top.get("theme") or "").strip()
        summary = str(top.get("summary") or "").strip()
        if theme and summary:
            return f"我想起一条相关内容：{theme}。大致是：{summary[:120]}"
        if summary:
            return f"我想起一条相关内容：{summary[:140]}"
        return "我想起了一些相关线索，后面可以继续顺着这个方向聊。"

    def _summarize_tool_followup(self, receipt: ExecutionReceipt) -> str:
        used_tools = receipt.outputs.get("used_tools", [])
        if not isinstance(used_tools, list):
            used_tools = []
        tool_results = receipt.outputs.get("tool_results", [])
        if not isinstance(tool_results, list):
            tool_results = []

        preview = ""
        for item in tool_results:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            if content:
                preview = content[:140]
                break

        if used_tools and preview:
            tools_text = "、".join(str(tool) for tool in used_tools[:3])
            return f"我刚用 {tools_text} 查了一下，先给你一个结果：{preview}"
        if used_tools:
            tools_text = "、".join(str(tool) for tool in used_tools[:3])
            return f"我刚试着用了 {tools_text}，如果你愿意我可以继续顺着这个结果往下处理。"
        return "我刚试着处理了一下这件事，但目前还没有拿到特别明确的结果。"

    def _should_share_search_result(self, receipt: ExecutionReceipt) -> bool:
        if receipt.status != "success" or receipt.action_type != "search_web":
            return False
        content = str(receipt.outputs.get("search_result") or "").strip()
        if len(content) < 40:
            return False
        blocked_phrases = (
            "无需搜索",
            "没有找到相关的网络信息",
            "搜索失败",
            "无法确定需要搜索的具体内容",
            "问题为空",
        )
        return not any(phrase in content for phrase in blocked_phrases)

    def _summarize_search_followup(self, receipt: ExecutionReceipt) -> str:
        query = str(receipt.outputs.get("query") or "").strip()
        content = str(receipt.outputs.get("search_result") or "").strip()
        preview = content[:220]
        if query and preview:
            return f"我刚主动查了下“{query}”的最近信息，先分享给你：{preview}"
        if preview:
            return f"我刚主动查到一些信息，先分享给你：{preview}"
        return "我刚主动查了一下这个话题，不过暂时还没有拿到值得分享的结果。"

    def _resolve_share_target_chat(self, receipt: ExecutionReceipt) -> str:
        target_chat_id = str(receipt.outputs.get("share_target_chat_id") or "").strip()
        if target_chat_id:
            return target_chat_id
        return str(receipt.outputs.get("chat_id") or "").strip()

    def _should_build_followup(self, receipt: ExecutionReceipt) -> bool:
        if receipt.status != "success":
            return False
        if receipt.action_type == "find_memory":
            return str(receipt.reason or "") in {"memory_found", "memory_not_found"}
        if receipt.action_type == "tool_call":
            return str(receipt.reason or "") in {"skill_executed", "skill_skipped"}
        if receipt.action_type == "search_web":
            return str(receipt.reason or "") == "search_completed" and self._should_share_search_result(receipt)
        return False

    def _build_followup_text(self, receipt: ExecutionReceipt) -> str:
        if receipt.action_type == "find_memory":
            return self._summarize_memory_followup(receipt)
        if receipt.action_type == "tool_call":
            return self._summarize_tool_followup(receipt)
        if receipt.action_type == "search_web":
            return self._summarize_search_followup(receipt)
        return ""

    def _build_followup_intents(self, receipt: ExecutionReceipt) -> list[Intent]:
        # search_web 超时时向目标会话发送简短失败提示，避免用户无反馈
        if (
            receipt.status == "failed"
            and receipt.action_type == "search_web"
            and "timeout" in str(receipt.reason or "").lower()
        ):
            chat_id = str(receipt.outputs.get("chat_id") or "").strip()
            if chat_id:
                return [
                    Intent.create(
                        source="receipt",
                        intent_type="reply",
                        target_chat_id=chat_id,
                        payload={
                            "chat_id": chat_id,
                            "text": "搜索超时，稍后再试。",
                            "reply_reason": "search_web_timeout",
                            "use_reply_generator": False,
                        },
                        dedup_key=f"timeout-followup-{receipt.receipt_id}",
                        expire_after_s=60,
                        delayed_for_s=0,
                        lane="normal",
                        priority=0.5,
                        urgency=0.4,
                        confidence=0.8,
                        cost_hint=0.05,
                        risk_hint=0.05,
                        interruptiveness=0.1,
                    )
                ]
            return []

        if not self._should_build_followup(receipt):
            return []

        chat_id = self._resolve_share_target_chat(receipt)
        if not chat_id:
            return []
        text = self._build_followup_text(receipt)
        if not text.strip():
            return []

        expire_after_s = 120
        if receipt.action_type == "search_web":
            expire_after_s = int(getattr(global_config.heartbeat, "search_followup_expire_seconds", 600) or 600)

        return [
            Intent.create(
                source="receipt",
                intent_type="reply",
                target_chat_id=chat_id,
                payload={
                    "chat_id": chat_id,
                    "text": text,
                    "followup_of_receipt_id": receipt.receipt_id,
                    "followup_action_type": receipt.action_type,
                    "source_chat_id": str(receipt.outputs.get("source_chat_id") or receipt.outputs.get("chat_id") or ""),
                    "share_target_chat_id": str(receipt.outputs.get("share_target_chat_id") or chat_id),
                    "selector_reason": str(receipt.outputs.get("selector_reason") or ""),
                    "selector_score": float(receipt.outputs.get("selector_score", 0.0) or 0.0),
                    "goal_signature": str(receipt.outputs.get("goal_signature") or ""),
                },
                dedup_key=f"followup-{receipt.receipt_id}",
                expire_after_s=expire_after_s,
                delayed_for_s=2,
                lane="normal",
                priority=0.62,
                urgency=0.35,
                confidence=0.7,
                cost_hint=0.08,
                risk_hint=0.08,
                interruptiveness=0.12,
            )
        ]

    async def start(self) -> None:
        """启动 fast/normal/slow 三个心跳协程。"""

        cfg = global_config.heartbeat
        if self._running:
            logger.warning("HeartbeatV2System already running")
            return
        if not cfg.enable:
            logger.info("HeartbeatV2System disabled (heartbeat.enable=false)")
            return

        self._running = True
        logger.info(
            f"HeartbeatV2System start: fast={cfg.fast_interval}s, normal={cfg.normal_interval}s, slow={cfg.slow_interval}s"
        )
        loop = asyncio.get_running_loop()
        self._tasks = [
            loop.create_task(self._fast_loop(), name="heartbeat_v2_fast"),
            loop.create_task(self._normal_loop(), name="heartbeat_v2_normal"),
            loop.create_task(self._slow_loop(), name="heartbeat_v2_slow"),
        ]
        for task in self._tasks:
            task.add_done_callback(self._log_task_done)

    async def stop(self) -> None:
        """停止所有心跳协程。"""

        if not self._running:
            return
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("HeartbeatV2System stopped")

    async def _fast_loop(self) -> None:
        """快速循环：轻量 requeue_delayed、drop_expired。"""

        logger.info("[v2 fast] loop started")
        interval = max(1, int(global_config.heartbeat.fast_interval))
        while self._running:
            try:
                self.intent_queue.requeue_delayed()
                self.intent_queue.drop_expired()
                self.observation_ingress_queue.drop_expired()
            except Exception as e:  # noqa: BLE001
                logger.error(f"[v2 fast] loop error: {e}")
            await asyncio.sleep(interval)
        logger.info("[v2 fast] loop exiting (stop requested)")

    async def _normal_loop(self) -> None:
        """主规划循环：collect -> generate -> enqueue -> dequeue -> execute -> receipt -> history。"""

        logger.info("[v2 normal] loop started")
        interval = max(1, int(global_config.heartbeat.normal_interval))
        while self._running:
            started_at = time.time()
            consumed_intents: list[Intent] = []
            receipts: list[ExecutionReceipt] = []
            reflections: list[dict] = []
            followup_intents: list[Intent] = []
            try:
                logger.debug("[v2 normal] tick start")
                ingress_recent = self.observation_ingress_queue.recent(limit=30, window_seconds=180)
                state = self.state_fabric.collect_all(
                    self.history_store.recent(limit=4),
                    ingress_observations=ingress_recent,
                )
                self._normal_tick_count += 1
                # 冷启动：首个 normal tick 不根据主动目标立即执行 explore，避免每次启动就搜一次
                if self._normal_tick_count == 1:
                    state["skip_goal_explore_this_tick"] = True

                intents = await self.planner.generate_intents(self.planner.collect_inputs(state))
                self.intent_queue.enqueue(intents)
                take_n = max(1, int(global_config.heartbeat.max_actions_per_tick))
                consumed_intents = self.intent_queue.dequeue(limit=take_n)

                for intent in consumed_intents:
                    try:
                        logger.info(f"[v2 normal] execute start {self._summarize_intent(intent)}")
                        receipt = await self.executor.execute(intent, state)
                    except Exception as e:  # noqa: BLE001
                        logger.error(f"execute intent failed {self._summarize_intent(intent)}: {e}", exc_info=True)
                        receipt = ExecutionReceipt.create(
                            plan_id="plan_unknown",
                            intent_id=intent.intent_id,
                            action_type="unknown",
                            status="failed",
                            reason=str(e),
                            outputs={},
                            latency_ms=0,
                        )
                    logger.info(
                        f"[v2 normal] execute done {self._summarize_intent(intent)} "
                        f"status={receipt.status} action={receipt.action_type} reason={receipt.reason} "
                        f"latency_ms={receipt.latency_ms}"
                    )
                    receipts.append(receipt)
                    try:
                        reflection = self.reflection_engine.reflect(intent=intent, receipt=receipt, state=state)
                        reflection_dict = reflection.to_dict()
                        reflections.append(reflection_dict)
                        self.experience_store.append_reflection(reflection_dict)
                    except Exception as reflection_error:  # noqa: BLE001
                        logger.error(f"reflect receipt failed: {reflection_error}", exc_info=True)
                    try:
                        followup_intents.extend(self._build_followup_intents(receipt))
                    except Exception as followup_error:  # noqa: BLE001
                        logger.error(f"build followup intents failed: {followup_error}", exc_info=True)

                if followup_intents:
                    self.intent_queue.enqueue(followup_intents)

                queue_metrics = self.intent_queue.get_metrics()
                tick_meta = HeartbeatTickMeta.create(
                    loop_type="normal",
                    started_at=started_at,
                    queue_ready_len=queue_metrics["ready_length"],
                    queue_delayed_len=queue_metrics["delayed_length"],
                    note="normal loop",
                )
                self.history_store.append_tick(tick_meta, consumed_intents, receipts, reflections=reflections)

                logger.info(
                    "[v2 normal] tick done "
                    f"intents={len(consumed_intents)} receipts={len(receipts)} reflections={len(reflections)} "
                    f"followups={len(followup_intents)} "
                    f"ready={queue_metrics['ready_length']} delayed={queue_metrics['delayed_length']} "
                    f"ingress_recent={len(ingress_recent)} elapsed_ms={int((time.time() - started_at) * 1000)}"
                )
            except Exception as e:  # noqa: BLE001
                logger.error(f"[v2 normal] loop error: {e}", exc_info=True)
            await asyncio.sleep(interval)
        logger.info("[v2 normal] loop exiting (stop requested)")

    async def _slow_loop(self) -> None:
        """慢速循环：队列与历史观测日志。"""

        logger.info("[v2 slow] loop started")
        interval = max(10, int(global_config.heartbeat.slow_interval))
        while self._running:
            try:
                metrics = self.intent_queue.get_metrics()
                lane_metrics = self.intent_queue.get_lane_metrics()
                distilled_chats = 0
                for chat_id in self.experience_store.get_chat_ids_with_reflections()[:8]:
                    payload = self.experience_store.distill_recent_reflections(chat_id, limit=40)
                    if payload.get("strategies"):
                        distilled_chats += 1
                logger.info(
                    f"[v2 slow] queue={metrics}, lane={lane_metrics}, history_size={self.history_store.size()}, distilled_chats={distilled_chats}"
                )
            except Exception as e:  # noqa: BLE001
                logger.error(f"[v2 slow] loop error: {e}")
            await asyncio.sleep(interval)
        logger.info("[v2 slow] loop exiting (stop requested)")

    def get_recent_history(self, limit: int = 20) -> list[dict]:
        """获取最近 N 次心跳历史（含 intent 与 receipt）。"""

        return self.history_store.recent(limit=limit)

    def get_queue_metrics(self) -> dict[str, int]:
        """获取队列观测指标（长度、入队、出队、丢弃等）。"""

        metrics = self.intent_queue.get_metrics()
        ingress_metrics = self.observation_ingress_queue.get_metrics()
        metrics["observation_recent_length"] = ingress_metrics.get("recent_length", 0)
        return metrics


_global_heartbeat_v2_system: Optional[HeartbeatV2System] = None


def get_heartbeat_v2_system() -> HeartbeatV2System:
    global _global_heartbeat_v2_system
    if _global_heartbeat_v2_system is None:
        _global_heartbeat_v2_system = HeartbeatV2System()
    return _global_heartbeat_v2_system
