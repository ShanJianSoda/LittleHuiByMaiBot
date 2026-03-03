"""
心跳系统 V2 意图队列。

实现 ready_queue / delayed_queue，支持 enqueue、dequeue、peek、
requeue_delayed、drop_expired，以及去重与过期丢弃。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Optional
import time

from src.common.logger import get_logger

from .models import Intent


logger = get_logger("heartbeat_v2.intent_queue")


@dataclass
class IntentQueueMetrics:
    """队列观测指标：入队、出队、丢弃、去重丢弃、过期丢弃。"""

    enqueued: int = 0
    dequeued: int = 0
    dropped: int = 0
    dedup_dropped: int = 0
    expired_dropped: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "enqueued": self.enqueued,
            "dequeued": self.dequeued,
            "dropped": self.dropped,
            "dedup_dropped": self.dedup_dropped,
            "expired_dropped": self.expired_dropped,
        }


class IntentQueue:
    """意图队列：ready 立即可执行，delayed 受冷却/窗口限制。"""

    def __init__(self, *, max_ready: int = 256, max_delayed: int = 256, dedup_window_s: int = 30):
        self._ready_queue: list[Intent] = []
        self._delayed_queue: list[Intent] = []
        self._dedup_index: dict[str, float] = {}
        self._lane_counts: defaultdict[str, int] = defaultdict(int)

        self.max_ready = max(1, max_ready)
        self.max_delayed = max(1, max_delayed)
        self.dedup_window_s = max(0, dedup_window_s)
        self.metrics = IntentQueueMetrics()

    def enqueue(self, intents: Intent | Iterable[Intent]) -> int:
        """入队，支持单个或批量。返回实际接受数量。"""

        items = [intents] if isinstance(intents, Intent) else list(intents)
        accepted = 0
        now = time.time()
        self._cleanup_dedup_index(now)
        for intent in items:
            if not self._accept_intent(intent, now):
                continue
            if intent.delayed_until and intent.delayed_until > now:
                if len(self._delayed_queue) >= self.max_delayed:
                    self._drop_intent(intent, "delayed queue overflow")
                    continue
                self._delayed_queue.append(intent)
            else:
                if len(self._ready_queue) >= self.max_ready:
                    self._drop_intent(intent, "ready queue overflow")
                    continue
                self._ready_queue.append(intent)
            self.metrics.enqueued += 1
            self._lane_counts[intent.lane] += 1
            accepted += 1
        self._sort_ready_queue()
        return accepted

    def peek(self, limit: int = 5) -> list[Intent]:
        """查看队首若干条，不取出。"""

        if limit <= 0:
            return []
        self._sort_ready_queue()
        return self._ready_queue[:limit]

    def dequeue(self, limit: int = 1) -> list[Intent]:
        """出队前先 requeue_delayed、drop_expired，再按 score 取出。"""

        self.requeue_delayed()
        self.drop_expired()
        if limit <= 0:
            return []
        self._sort_ready_queue()
        output: list[Intent] = []
        while self._ready_queue and len(output) < limit:
            intent = self._ready_queue.pop(0)
            self.metrics.dequeued += 1
            self._lane_counts[intent.lane] = max(0, self._lane_counts[intent.lane] - 1)
            output.append(intent)
        return output

    def requeue_delayed(self, now: Optional[float] = None) -> int:
        """将已到期的 delayed 意图移回 ready。"""

        current = now or time.time()
        remain: list[Intent] = []
        moved: list[Intent] = []
        for intent in self._delayed_queue:
            if intent.delayed_until and intent.delayed_until > current:
                remain.append(intent)
            else:
                moved.append(intent)
        self._delayed_queue = remain
        if moved:
            self.enqueue(moved)
        return len(moved)

    def drop_expired(self, now: Optional[float] = None) -> int:
        """丢弃已过期的意图。"""

        current = now or time.time()
        dropped = 0
        for queue_name in ("_ready_queue", "_delayed_queue"):
            queue: list[Intent] = getattr(self, queue_name)
            kept: list[Intent] = []
            for intent in queue:
                if intent.expire_at and intent.expire_at <= current:
                    self._drop_intent(intent, "expired")
                    self.metrics.expired_dropped += 1
                    dropped += 1
                else:
                    kept.append(intent)
            setattr(self, queue_name, kept)
        return dropped

    def get_metrics(self) -> dict[str, int]:
        return {
            "ready_length": len(self._ready_queue),
            "delayed_length": len(self._delayed_queue),
            **self.metrics.to_dict(),
        }

    def get_lane_metrics(self) -> dict[str, int]:
        return dict(self._lane_counts)

    def _accept_intent(self, intent: Intent, now: float) -> bool:
        if intent.expire_at and intent.expire_at <= now:
            self._drop_intent(intent, "expired before enqueue")
            self.metrics.expired_dropped += 1
            return False
        if intent.dedup_key:
            last_seen = self._dedup_index.get(intent.dedup_key)
            if last_seen and (now - last_seen) < self.dedup_window_s:
                self._drop_intent(intent, f"dedup in {self.dedup_window_s}s")
                self.metrics.dedup_dropped += 1
                return False
            self._dedup_index[intent.dedup_key] = now
        return True

    def _drop_intent(self, intent: Intent, reason: str) -> None:
        self.metrics.dropped += 1
        logger.debug(f"[queue] drop intent={intent.intent_id}, reason={reason}")

    def _cleanup_dedup_index(self, now: float) -> None:
        if self.dedup_window_s <= 0:
            self._dedup_index.clear()
            return
        stale = [k for k, ts in self._dedup_index.items() if (now - ts) >= self.dedup_window_s]
        for key in stale:
            self._dedup_index.pop(key, None)

    def _sort_ready_queue(self) -> None:
        self._ready_queue.sort(key=lambda x: (x.score, x.created_at), reverse=True)
