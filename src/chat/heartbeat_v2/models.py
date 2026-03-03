"""
心跳系统 V2 核心数据结构。

定义 Intent、Plan、PlanGraph、ExecutionReceipt、HeartbeatTickMeta 等模型，
用于「全局心跳中枢 + 意图队列 + 执行回流」架构。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Optional
import time
import uuid


# 意图类型：engage/explore/retrieve/reflect/handoff/no_op/reply
IntentType = Literal["engage", "explore", "retrieve", "reflect", "handoff", "no_op", "reply"]
# 动作类型：reply/search_web/find_memory/tool_call/handoff_dialogue/no_op
ActionType = Literal["reply", "search_web", "find_memory", "tool_call", "handoff_dialogue", "no_op"]
# 执行回执状态
ReceiptStatus = Literal["success", "failed", "skipped", "deferred"]


def _new_id(prefix: str) -> str:
    """生成带前缀的唯一 ID。"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class Intent:
    """意图：队列中存储的决策单元，执行前展开为 Plan。"""

    intent_id: str
    source: str  # mood / memory / notice / scheduler / receipt / external
    type: IntentType
    target_chat_id: Optional[str]
    target_user_id: Optional[str]
    payload: dict[str, Any]

    # 评分因子（用于队列排序）
    priority: float = 0.0
    urgency: float = 0.0
    confidence: float = 0.0
    cost_hint: float = 0.0
    risk_hint: float = 0.0
    interruptiveness: float = 0.0

    dedup_key: Optional[str] = None  # 去重键，同 key 在时间窗口内只保留一条
    created_at: float = field(default_factory=time.time)
    expire_at: Optional[float] = None
    delayed_until: Optional[float] = None
    lane: str = "normal"  # urgent / normal / maintenance

    @property
    def score(self) -> float:
        return (
            self.priority
            + self.urgency
            + self.confidence
            - self.cost_hint
            - self.risk_hint
            - self.interruptiveness
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def create(
        cls,
        *,
        source: str,
        intent_type: IntentType,
        payload: Optional[dict[str, Any]] = None,
        target_chat_id: Optional[str] = None,
        target_user_id: Optional[str] = None,
        dedup_key: Optional[str] = None,
        expire_after_s: Optional[int] = None,
        delayed_for_s: Optional[int] = None,
        lane: str = "normal",
        priority: float = 0.0,
        urgency: float = 0.0,
        confidence: float = 0.0,
        cost_hint: float = 0.0,
        risk_hint: float = 0.0,
        interruptiveness: float = 0.0,
    ) -> "Intent":
        now = time.time()
        expire_at = now + expire_after_s if expire_after_s else None
        delayed_until = now + delayed_for_s if delayed_for_s else None
        return cls(
            intent_id=_new_id("intent"),
            source=source,
            type=intent_type,
            target_chat_id=target_chat_id,
            target_user_id=target_user_id,
            payload=payload or {},
            dedup_key=dedup_key,
            expire_at=expire_at,
            delayed_until=delayed_until,
            lane=lane,
            priority=priority,
            urgency=urgency,
            confidence=confidence,
            cost_hint=cost_hint,
            risk_hint=risk_hint,
            interruptiveness=interruptiveness,
        )


@dataclass
class Plan:
    """执行计划：由 Intent 展开得到，包含具体 action_type 与 action_args。"""

    plan_id: str
    intent_id: str
    action_type: ActionType
    action_args: dict[str, Any]
    policy_tags: list[str] = field(default_factory=list)
    timeout_s: int = 30

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def create(
        cls,
        *,
        intent_id: str,
        action_type: ActionType,
        action_args: Optional[dict[str, Any]] = None,
        policy_tags: Optional[list[str]] = None,
        timeout_s: int = 30,
    ) -> "Plan":
        return cls(
            plan_id=_new_id("plan"),
            intent_id=intent_id,
            action_type=action_type,
            action_args=action_args or {},
            policy_tags=policy_tags or [],
            timeout_s=timeout_s,
        )


@dataclass
class PlanNode:
    """计划图节点（P2 引入，当前未使用）。"""

    node_id: str
    action_type: str
    action_args: dict[str, Any]
    depends_on: list[str] = field(default_factory=list)


@dataclass
class PlanGraph:
    """计划图（P2 引入，用于多阶段链）。"""

    plan_id: str
    intent_id: str
    nodes: list[PlanNode] = field(default_factory=list)


@dataclass
class ExecutionReceipt:
    """执行回执：记录每次动作执行结果，用于反馈与历史。"""

    receipt_id: str
    plan_id: str
    intent_id: str
    action_type: str
    status: ReceiptStatus
    reason: Optional[str]
    outputs: dict[str, Any]
    cost_actual: Optional[float]
    latency_ms: int
    timestamp: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def create(
        cls,
        *,
        plan_id: str,
        intent_id: str,
        action_type: str,
        status: ReceiptStatus,
        reason: Optional[str] = None,
        outputs: Optional[dict[str, Any]] = None,
        cost_actual: Optional[float] = None,
        latency_ms: int = 0,
    ) -> "ExecutionReceipt":
        return cls(
            receipt_id=_new_id("receipt"),
            plan_id=plan_id,
            intent_id=intent_id,
            action_type=action_type,
            status=status,
            reason=reason,
            outputs=outputs or {},
            cost_actual=cost_actual,
            latency_ms=latency_ms,
            timestamp=time.time(),
        )


@dataclass
class HeartbeatTickMeta:
    """单次心跳 tick 的元信息，用于历史记录与观测。"""

    tick_id: str
    loop_type: str
    started_at: float
    finished_at: float
    duration_ms: int
    queue_ready_len: int
    queue_delayed_len: int
    note: str = ""

    @classmethod
    def create(
        cls,
        *,
        loop_type: str,
        started_at: float,
        queue_ready_len: int,
        queue_delayed_len: int,
        note: str = "",
    ) -> "HeartbeatTickMeta":
        finished_at = time.time()
        return cls(
            tick_id=_new_id("tick"),
            loop_type=loop_type,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=max(0, int((finished_at - started_at) * 1000)),
            queue_ready_len=queue_ready_len,
            queue_delayed_len=queue_delayed_len,
            note=note,
        )
