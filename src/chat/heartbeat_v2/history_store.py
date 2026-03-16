"""
心跳系统 V2 历史存储。

保存最近 N 次心跳的 meta、intents、receipts，
支持 append_tick 与 recent 查询。
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

from .models import ExecutionReceipt, HeartbeatTickMeta, Intent


@dataclass
class HeartbeatHistoryItem:
    """单次心跳的完整记录。"""

    meta: HeartbeatTickMeta
    intents: list[Intent]
    receipts: list[ExecutionReceipt]
    reflections: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "meta": asdict(self.meta),
            "intents": [intent.to_dict() for intent in self.intents],
            "receipts": [receipt.to_dict() for receipt in self.receipts],
            "reflections": self.reflections,
        }


class HistoryStore:
    """历史存储：append_tick 追加，recent 查询最近 N 条。"""

    def __init__(self, max_items: int = 100):
        self.max_items = max(1, max_items)
        self._items: deque[HeartbeatHistoryItem] = deque(maxlen=self.max_items)

    def append_tick(
        self,
        meta: HeartbeatTickMeta,
        intents: list[Intent],
        receipts: list[ExecutionReceipt],
        reflections: list[dict[str, Any]] | None = None,
    ) -> None:
        """追加一次心跳 tick 记录。"""

        self._items.append(
            HeartbeatHistoryItem(
                meta=meta,
                intents=intents,
                receipts=receipts,
                reflections=reflections or [],
            )
        )

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """获取最近 limit 条心跳记录（字典形式）。"""

        if limit <= 0:
            return []
        # TODO：策略获取
        items = list[HeartbeatHistoryItem](self._items)[-limit:]
        return [item.to_dict() for item in items]

    def size(self) -> int:
        return len(self._items)
