"""
心跳系统 V2 当前规划任务（current planners）。

类似 task 列表：CRUD、按 dedup_key 去重/合并，供 state 暴露给规划器。
retrieve 等只记录 receipt 到历史，不自动生成下一步；规划器根据 state（含 history + current_planners）再规划。
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from src.common.logger import get_logger

from .models import _new_id


logger = get_logger("heartbeat_v2.current_planners")


@dataclass
class PlannerTask:
    """单条规划任务，用于「当前待办」视图与去重。"""

    task_id: str
    type: str  # reply / retrieve / explore / no_op 等
    payload: dict[str, Any]
    created_at: float
    updated_at: float
    dedup_key: Optional[str] = None
    status: str = "pending"  # pending / running / done / cancelled

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "type": self.type,
            "payload": self.payload,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "dedup_key": self.dedup_key,
            "status": self.status,
        }


class CurrentPlannersStore:
    """当前规划任务存储：CRUD + 按 dedup_key 去重。"""

    def __init__(self, max_items: int = 64, dedup_window_s: float = 60.0):
        self.max_items = max(1, max_items)
        self.dedup_window_s = max(0.0, dedup_window_s)
        self._by_id: dict[str, PlannerTask] = {}
        self._order: list[str] = []  # task_id 顺序，便于 list 按时间

    def create(
        self,
        type: str,
        payload: dict[str, Any],
        *,
        dedup_key: Optional[str] = None,
        status: str = "pending",
    ) -> PlannerTask:
        """新增一条任务。若 dedup_key 在窗口内已存在则替换并返回该条（去重）。"""
        now = time.time()
        if dedup_key and self.dedup_window_s > 0:
            existing = self._get_by_dedup_key(dedup_key, now)
            if existing:
                updated = PlannerTask(
                    task_id=existing.task_id,
                    type=type,
                    payload=payload,
                    created_at=existing.created_at,
                    updated_at=now,
                    dedup_key=dedup_key,
                    status=status,
                )
                self._by_id[existing.task_id] = updated
                logger.debug("[current_planners] dedup replace task_id=%s dedup_key=%s", existing.task_id, dedup_key)
                return updated
        task_id = _new_id("planner_task")
        task = PlannerTask(
            task_id=task_id,
            type=type,
            payload=payload,
            created_at=now,
            updated_at=now,
            dedup_key=dedup_key,
            status=status,
        )
        self._by_id[task_id] = task
        self._order.append(task_id)
        while len(self._order) > self.max_items:
            old_id = self._order.pop(0)
            self._by_id.pop(old_id, None)
        return task

    def _get_by_dedup_key(self, key: str, now: float) -> Optional[PlannerTask]:
        for tid in reversed(self._order):
            t = self._by_id.get(tid)
            if not t or t.dedup_key != key:
                continue
            if self.dedup_window_s > 0 and (now - t.updated_at) > self.dedup_window_s:
                continue
            return t
        return None

    def get(self, task_id: str) -> Optional[PlannerTask]:
        return self._by_id.get(task_id)

    def list_tasks(
        self,
        limit: int = 20,
        status: Optional[str] = None,
        type_filter: Optional[str] = None,
    ) -> list[PlannerTask]:
        """按时间倒序返回任务列表，可按 status/type 过滤。"""
        out: list[PlannerTask] = []
        for tid in reversed(self._order):
            if len(out) >= limit:
                break
            t = self._by_id.get(tid)
            if not t:
                continue
            if status is not None and t.status != status:
                continue
            if type_filter is not None and t.type != type_filter:
                continue
            out.append(t)
        return out

    def update(self, task_id: str, **kwargs: Any) -> Optional[PlannerTask]:
        """更新指定任务字段（payload/status/updated_at 等）。"""
        t = self._by_id.get(task_id)
        if not t:
            return None
        allowed = {"type", "payload", "status", "dedup_key"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return t
        new_updated = time.time()
        new_t = PlannerTask(
            task_id=t.task_id,
            type=updates.get("type", t.type),
            payload=updates.get("payload", t.payload),
            created_at=t.created_at,
            updated_at=new_updated,
            dedup_key=updates.get("dedup_key", t.dedup_key),
            status=updates.get("status", t.status),
        )
        self._by_id[task_id] = new_t
        return new_t

    def delete(self, task_id: str) -> bool:
        """删除指定任务。"""
        if task_id not in self._by_id:
            return False
        del self._by_id[task_id]
        try:
            self._order.remove(task_id)
        except ValueError:
            pass
        return True

    def dedup_merge(
        self,
        dedup_key: str,
        type: str,
        payload: dict[str, Any],
        *,
        status: str = "pending",
    ) -> PlannerTask:
        """按 dedup_key 去重合并：窗口内已存在则更新并返回，否则 create。"""
        return self.create(type=type, payload=payload, dedup_key=dedup_key, status=status)

    def list_for_state(self, limit: int = 20) -> list[dict[str, Any]]:
        """供 state_fabric 拉取，用于规划器输入。默认只返回 pending。"""
        tasks = self.list_tasks(limit=limit, status="pending")
        return [t.to_dict() for t in tasks]

    def size(self) -> int:
        return len(self._by_id)


# 单例，供 state_fabric 与 system 使用
current_planners_store = CurrentPlannersStore(max_items=64, dedup_window_s=60.0)
