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


# 意图类型：engage/explore/retrieve/reflect/handoff/no_op/reply/tool_call
IntentType = Literal["engage", "explore", "retrieve", "reflect", "handoff", "no_op", "reply", "tool_call"]
# 动作类型：reply/search_web/find_memory/tool_call/handoff_dialogue/no_op
ActionType = Literal["reply", "search_web", "find_memory", "tool_call", "handoff_dialogue", "no_op"]
# 执行回执状态
ReceiptStatus = Literal["success", "failed", "skipped", "deferred"]
PlanNodeStatus = Literal["pending", "running", "success", "failed", "skipped", "blocked"]
ObservationSource = Literal["text", "image", "audio", "notice", "system"]


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
class Observation:
    """统一观察对象：把文本、图片、语音、通知统一映射到同一输入平面。"""

    observation_id: str
    chat_id: str
    source: ObservationSource
    created_at: float
    message_id: Optional[str]
    text: str
    salience: float
    emotion_hint: dict[str, Any]
    entities: list[str]
    intent_guess: str
    uncertainty: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def create(
        cls,
        *,
        chat_id: str,
        source: ObservationSource,
        text: str,
        created_at: Optional[float] = None,
        message_id: Optional[str] = None,
        salience: float = 0.5,
        emotion_hint: Optional[dict[str, Any]] = None,
        entities: Optional[list[str]] = None,
        intent_guess: str = "conversation",
        uncertainty: float = 0.5,
        metadata: Optional[dict[str, Any]] = None,
    ) -> "Observation":
        return cls(
            observation_id=_new_id("obs"),
            chat_id=chat_id,
            source=source,
            created_at=created_at or time.time(),
            message_id=message_id,
            text=text,
            salience=max(0.0, min(1.0, salience)),
            emotion_hint=emotion_hint or {},
            entities=entities or [],
            intent_guess=intent_guess,
            uncertainty=max(0.0, min(1.0, uncertainty)),
            metadata=metadata or {},
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

    @classmethod
    def create(
        cls,
        *,
        action_type: str,
        action_args: Optional[dict[str, Any]] = None,
        depends_on: Optional[list[str]] = None,
    ) -> "PlanNode":
        return cls(
            node_id=_new_id("plan_node"),
            action_type=action_type,
            action_args=action_args or {},
            depends_on=depends_on or [],
        )


@dataclass
class PlanGraph:
    """计划图（P2 引入，用于多阶段链）。"""

    plan_id: str
    intent_id: str
    nodes: list[PlanNode] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        *,
        intent_id: str,
        nodes: Optional[list[PlanNode]] = None,
    ) -> "PlanGraph":
        return cls(
            plan_id=_new_id("plan_graph"),
            intent_id=intent_id,
            nodes=nodes or [],
        )


@dataclass
class PlanStepState:
    """多步计划中单个节点的运行状态。"""

    node_id: str
    action_type: str
    status: PlanNodeStatus = "pending"
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    receipt_id: Optional[str] = None
    reason: str = ""
    outputs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlanExecutionState:
    """多步计划的共享运行态，避免仅靠 receipt 再喂回 planner。"""

    execution_id: str
    plan_id: str
    intent_id: str
    status: str
    current_node_id: Optional[str]
    step_states: list[PlanStepState] = field(default_factory=list)
    shared_context: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def create(
        cls,
        *,
        plan_id: str,
        intent_id: str,
        step_states: Optional[list[PlanStepState]] = None,
        shared_context: Optional[dict[str, Any]] = None,
    ) -> "PlanExecutionState":
        now = time.time()
        return cls(
            execution_id=_new_id("plan_exec"),
            plan_id=plan_id,
            intent_id=intent_id,
            status="pending",
            current_node_id=None,
            step_states=step_states or [],
            shared_context=shared_context or {},
            created_at=now,
            updated_at=now,
        )


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
class ReflectionRecord:
    """执行后的结构化反思，用于经验沉淀和离线蒸馏。"""

    reflection_id: str
    chat_id: str
    intent_id: str
    receipt_id: str
    situation_key: str
    action_type: str
    status: ReceiptStatus
    score: float
    accepted: bool
    reason: str
    suggested_adjustment: str
    evidence: dict[str, Any]
    created_at: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def create(
        cls,
        *,
        chat_id: str,
        intent_id: str,
        receipt_id: str,
        situation_key: str,
        action_type: str,
        status: ReceiptStatus,
        score: float,
        accepted: bool,
        reason: str,
        suggested_adjustment: str,
        evidence: Optional[dict[str, Any]] = None,
    ) -> "ReflectionRecord":
        return cls(
            reflection_id=_new_id("reflection"),
            chat_id=chat_id,
            intent_id=intent_id,
            receipt_id=receipt_id,
            situation_key=situation_key,
            action_type=action_type,
            status=status,
            score=max(-1.0, min(1.0, score)),
            accepted=accepted,
            reason=reason,
            suggested_adjustment=suggested_adjustment,
            evidence=evidence or {},
            created_at=time.time(),
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
