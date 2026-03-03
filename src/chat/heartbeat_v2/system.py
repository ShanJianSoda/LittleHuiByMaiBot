"""
心跳系统 V2 主入口。

实现 HeartbeatV2System.start/stop，fast/normal/slow 三档循环，
normal 中完成 collect -> generate_intents -> enqueue -> dequeue -> execute -> receipt -> history。
"""
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
from .history_store import HistoryStore
from .intent_queue import IntentQueue
from .models import ExecutionReceipt, HeartbeatTickMeta, Intent
from .planner import HeartbeatPlanner
from .policy_gate import PolicyGate
from .router import ActionRouter
from .state_fabric import StateFabric


logger = get_logger("heartbeat_v2.system")


class HeartbeatV2System:
    """心跳 V2 系统：三档循环 + 意图队列 + 执行回流闭环。"""

    def __init__(self) -> None:
        cfg = global_config.heartbeat_v2

        self.state_fabric = StateFabric()
        self.intent_queue = IntentQueue(
            max_ready=cfg.queue_max_ready,
            max_delayed=cfg.queue_max_delayed,
            dedup_window_s=cfg.dedup_window_seconds,
        )
        self.history_store = HistoryStore(max_items=cfg.history_max_items)
        self.planner = HeartbeatPlanner()
        self.policy_gate = PolicyGate(
            min_reply_interval_seconds=cfg.min_reply_interval_seconds,
            active_time_ranges=cfg.active_time_ranges,
        )
        self.router = ActionRouter()
        self.executor = HeartbeatExecutor(policy_gate=self.policy_gate, router=self.router)

        self._running = False
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        """启动 fast/normal/slow 三个心跳协程。"""

        cfg = global_config.heartbeat_v2
        if self._running:
            logger.warning("HeartbeatV2System already running")
            return
        if not cfg.enable:
            logger.info("HeartbeatV2System disabled (heartbeat_v2.enable=false)")
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

        interval = max(1, int(global_config.heartbeat_v2.fast_interval))
        while self._running:
            try:
                self.intent_queue.requeue_delayed()
                self.intent_queue.drop_expired()
            except Exception as e:  # noqa: BLE001
                logger.error(f"[v2 fast] loop error: {e}")
            await asyncio.sleep(interval)

    async def _normal_loop(self) -> None:
        """主规划循环：collect -> generate -> enqueue -> dequeue -> execute -> receipt -> history。"""

        interval = max(1, int(global_config.heartbeat_v2.normal_interval))
        while self._running:
            started_at = time.time()
            consumed_intents: list[Intent] = []
            receipts: list[ExecutionReceipt] = []
            try:
                state = self.state_fabric.collect_all(self.history_store.recent(limit=10))

                intents = self.planner.generate_intents(self.planner.collect_inputs(state))
                self.intent_queue.enqueue(intents)
                take_n = max(1, int(global_config.heartbeat_v2.max_actions_per_tick))
                consumed_intents = self.intent_queue.dequeue(limit=take_n)

                for intent in consumed_intents:
                    try:
                        receipt = await self.executor.execute(intent, state)
                    except Exception as e:  # noqa: BLE001
                        logger.error(f"execute intent failed: {e}")
                        receipt = ExecutionReceipt.create(
                            plan_id="plan_unknown",
                            intent_id=intent.intent_id,
                            action_type="unknown",
                            status="failed",
                            reason=str(e),
                            outputs={},
                            latency_ms=0,
                        )
                    receipts.append(receipt)

                queue_metrics = self.intent_queue.get_metrics()
                tick_meta = HeartbeatTickMeta.create(
                    loop_type="normal",
                    started_at=started_at,
                    queue_ready_len=queue_metrics["ready_length"],
                    queue_delayed_len=queue_metrics["delayed_length"],
                    note="normal loop",
                )
                self.history_store.append_tick(tick_meta, consumed_intents, receipts)

                logger.info(
                    "[v2 normal] tick done "
                    f"intents={len(consumed_intents)} receipts={len(receipts)} "
                    f"ready={queue_metrics['ready_length']} delayed={queue_metrics['delayed_length']}"
                )
            except Exception as e:  # noqa: BLE001
                logger.error(f"[v2 normal] loop error: {e}")
            await asyncio.sleep(interval)

    async def _slow_loop(self) -> None:
        """慢速循环：队列与历史观测日志。"""

        interval = max(10, int(global_config.heartbeat_v2.slow_interval))
        while self._running:
            try:
                metrics = self.intent_queue.get_metrics()
                lane_metrics = self.intent_queue.get_lane_metrics()
                logger.info(
                    f"[v2 slow] queue={metrics}, lane={lane_metrics}, history_size={self.history_store.size()}"
                )
            except Exception as e:  # noqa: BLE001
                logger.error(f"[v2 slow] loop error: {e}")
            await asyncio.sleep(interval)

    def get_recent_history(self, limit: int = 20) -> list[dict]:
        """获取最近 N 次心跳历史（含 intent 与 receipt）。"""

        return self.history_store.recent(limit=limit)

    def get_queue_metrics(self) -> dict[str, int]:
        """获取队列观测指标（长度、入队、出队、丢弃等）。"""

        return self.intent_queue.get_metrics()


_global_heartbeat_v2_system: Optional[HeartbeatV2System] = None


def get_heartbeat_v2_system() -> HeartbeatV2System:
    global _global_heartbeat_v2_system
    if _global_heartbeat_v2_system is None:
        _global_heartbeat_v2_system = HeartbeatV2System()
    return _global_heartbeat_v2_system
