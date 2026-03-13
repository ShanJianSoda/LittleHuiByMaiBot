"""
心跳系统 V2 observation ingress queue。

用于承接消息预处理后的 observation 事件，作为 planner 前的输入缓冲层。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable, Optional

from src.common.logger import get_logger

from .models import Observation


logger = get_logger("heartbeat_v2.ingress_queue")


@dataclass
class ObservationIngressMetrics:
    enqueued: int = 0
    dropped: int = 0
    dedup_dropped: int = 0
    expired_dropped: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "enqueued": self.enqueued,
            "dropped": self.dropped,
            "dedup_dropped": self.dedup_dropped,
            "expired_dropped": self.expired_dropped,
        }


class ObservationIngressQueue:
    """轻量 observation 输入队列，保留近期事件供 planner 优先消费。"""

    def __init__(self, *, max_items: int = 256, dedup_window_s: int = 30, keep_recent_s: int = 180):
        self._items: list[Observation] = []
        self._dedup_index: dict[str, float] = {}
        self.max_items = max(1, max_items)
        self.dedup_window_s = max(0, dedup_window_s)
        self.keep_recent_s = max(30, keep_recent_s)
        self.metrics = ObservationIngressMetrics()

    def enqueue(self, observations: Observation | Iterable[Observation]) -> int:
        items = [observations] if isinstance(observations, Observation) else list(observations)
        accepted = 0
        now = time.time()
        self._cleanup(now)
        for observation in items:
            dedup_key = self._build_dedup_key(observation)
            last_seen = self._dedup_index.get(dedup_key)
            if last_seen and (now - last_seen) < self.dedup_window_s:
                self.metrics.dedup_dropped += 1
                self.metrics.dropped += 1
                logger.debug(f"[ingress] drop duplicated observation={observation.observation_id}")
                continue
            self._dedup_index[dedup_key] = now
            self._items.append(observation)
            self.metrics.enqueued += 1
            accepted += 1
        self._trim()
        return accepted

    def recent(self, *, limit: int = 30, window_seconds: Optional[int] = None) -> list[dict]:
        self._cleanup()
        if limit <= 0:
            return []
        now = time.time()
        effective_window = max(1, int(window_seconds or self.keep_recent_s))
        selected = [
            item
            for item in self._items
            if (now - float(item.created_at or now)) <= effective_window
        ]
        selected.sort(key=lambda x: x.created_at, reverse=True)
        return [item.to_dict() for item in selected[:limit]]

    def drop_expired(self, now: Optional[float] = None) -> int:
        current = now or time.time()
        before = len(self._items)
        self._items = [
            item for item in self._items if (current - float(item.created_at or current)) <= self.keep_recent_s
        ]
        dropped = max(0, before - len(self._items))
        if dropped:
            self.metrics.expired_dropped += dropped
            self.metrics.dropped += dropped
        self._cleanup_dedup_index(current)
        return dropped

    def get_metrics(self) -> dict[str, int]:
        self._cleanup()
        return {
            "recent_length": len(self._items),
            **self.metrics.to_dict(),
        }

    def _cleanup(self, now: Optional[float] = None) -> None:
        current = now or time.time()
        self.drop_expired(current)
        self._trim()

    def _trim(self) -> None:
        overflow = len(self._items) - self.max_items
        if overflow <= 0:
            return
        self._items.sort(key=lambda x: x.created_at, reverse=True)
        self._items = self._items[: self.max_items]
        self.metrics.dropped += overflow

    def _cleanup_dedup_index(self, now: float) -> None:
        if self.dedup_window_s <= 0:
            self._dedup_index.clear()
            return
        stale = [key for key, ts in self._dedup_index.items() if (now - ts) >= self.dedup_window_s]
        for key in stale:
            self._dedup_index.pop(key, None)

    def _build_dedup_key(self, observation: Observation) -> str:
        message_id = str(observation.message_id or "").strip()
        if message_id:
            return f"message:{message_id}"
        return f"observation:{observation.observation_id}"
