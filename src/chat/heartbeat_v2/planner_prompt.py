"""
heartbeat_v2 planner prompt 定义。

本文件先只做三件事：
1. 定义统一 prompt 输入模板
2. 定义结构化 JSON 输出 schema
3. 提供最小的渲染与解析辅助函数

后续 planner 真正接 LLM 时，优先复用这里的输入/输出协议。

提示词长度策略：
- 规划器（本文件）：仅注入「精简」心跳历史，控制 token，保留角色特质与决策所需最小信息。
- 意图消费者（executor/reply_generator_adapter 等）：通过 state["history"] 使用完整历史，
  在具体执行时再组装相对完整的提示词内容。
"""
from __future__ import annotations

import json
from typing import Any

from src.chat.utils.prompt_builder import Prompt
from src.common.knock import knock_manager

from .structured_output import parse_structured_json

# 规划器侧心跳历史在 prompt 中的最大字符数，避免提示词过长
PLANNER_HEARTBEAT_HISTORY_MAX_CHARS = 1500


PLANNER_ALLOWED_INTENT_TYPES = [
    "reply",
    "retrieve",
    "tool_call",
    "explore",
    "no_op",
]

PLANNER_ALLOWED_LANES = [
    "urgent",
    "normal",
    "maintenance",
]

PLANNER_ALLOWED_SCORE_LEVELS = [
    "low",
    "medium",
    "high",
]

PLANNER_ALLOWED_EXPIRE_TIERS = [
    "short",
    "normal",
    "long",
]

PLANNER_ALLOWED_DELAY_TIERS = [
    "none",
    "short",
    "medium",
]

PLANNER_INTENT_PAYLOAD_TEMPLATES: dict[str, dict[str, Any]] = {
    "reply": {
        "text": "string, optional, 回复文本草案",
        "reply_reason": "string, optional, 为什么要回复",
        "observation": "object, optional, 触发该回复的 observation 摘要",
        "use_reply_generator": "bool, optional, 是否交给旧回复链生成高质量回复",
        "think_level": "0|1|2, optional",
    },
    "retrieve": {
        "query": "string, required, 要检索的记忆查询",
        "observation": "object, optional, 触发检索的 observation 摘要",
    },
    "tool_call": {
        "query": "string, required, 工具调用要处理的问题",
        "observation": "object, optional, 触发该动作的 observation 摘要",
    },
    "explore": {
        "query": "string, required, 主动搜索查询",
        "observation": "object, optional, 触发该动作的 observation 摘要",
        "source_chat_id": "string, optional, 可填写真实 chat_id 或可解析名称",
        "share_target_chat_id": "string, optional, 可填写真实 chat_id 或可解析名称",
        "goal_candidate": "object, optional",
        "selector_reason": "string, optional",
    },
    "no_op": {
        "reason": "string, optional, 不行动的原因",
    },
}

PLANNER_OUTPUT_SCHEMA: dict[str, Any] = {
    "summary": "string, 对本次输入与状态的简短判断",
    "planning_mode": "string, rule_assisted / llm_assisted / noop",
    "intents": [
        {
            "source": "string, 例如 observation / scheduler / history / receipt",
            "intent_type": "string, one of reply/retrieve/tool_call/explore/no_op",
            "target_ref": "string, 推荐填写 knock_id / 备注名 / 显示名 / chat_id",
            "target_chat_id": "string, optional, 如果已知真实 chat_id 也可以填写",
            "target_user_id": "string, optional",
            "reason": "string, 说明为什么生成这个 intent",
            "payload": "object, 必须严格匹配该 intent_type 对应的 payload 子模板；不要混入其他字段",
            "priority_level": "string, one of low/medium/high",
            "urgency_level": "string, one of low/medium/high",
            "confidence_level": "string, one of low/medium/high",
            "cost_level": "string, one of low/medium/high",
            "risk_level": "string, one of low/medium/high",
            "interruptiveness_level": "string, one of low/medium/high",
            "lane": "string, one of urgent/normal/maintenance",
            "expire_tier": "string, one of short/normal/long",
            "delay_tier": "string, one of none/short/medium",
            "dedup_hint": "string, optional, 简短去重提示词，不要求精确",
        }
    ],
}

PLANNER_OUTPUT_EXAMPLE: dict[str, Any] = {
    "summary": "最近输入主要来自一个 notice，显著度高，适合给出轻量回复。",
    "planning_mode": "llm_assisted",
    "intents": [
        {
            "source": "observation",
            "intent_type": "reply",
            "target_ref": "小明",
            "target_chat_id": "chat_xxx",
            "target_user_id": "",
            "reason": "对方刚刚戳了你一下，属于明确的注意力请求。",
            "payload": {
                "chat_id": "chat_xxx",
                "text": "我在，刚刚感受到你戳我啦。",
                "reply_reason": "对方主动引起注意，适合即时轻量回应。",
                "observation": {
                    "chat_id": "chat_xxx",
                    "source": "notice",
                    "text": "poke",
                },
                "use_reply_generator": True,
                "think_level": 0,
                "enable_tool": False,
            },
            "priority_level": "high",
            "urgency_level": "high",
            "confidence_level": "high",
            "cost_level": "low",
            "risk_level": "low",
            "interruptiveness_level": "low",
            "lane": "urgent",
            "expire_tier": "short",
            "delay_tier": "none",
            "dedup_hint": "reply poke",
        }
    ],
}


PLANNER_PROMPT_TEMPLATE = Prompt(
    """
你是 heartbeat_v2 的规划器。你的职责不是直接回复用户，而是根据当前状态生成结构化 intent。

你的目标：
1. 判断当前是否需要行动
2. 如果需要，输出 0 到 3 个结构化 intents
3. 如果不需要行动，输出一个 `no_op` intent 或空 intents

规划要求：
- 只能使用这些 intent_type：{allowed_intent_types}
- 只能使用这些 lane：{allowed_lanes}
- 评分等级只能使用这些 level：{allowed_score_levels}
- 过期档位只能使用这些值：{allowed_expire_tiers}
- 延迟档位只能使用这些值：{allowed_delay_tiers}
- 输出必须是严格 JSON，不要输出 Markdown 代码块，不要输出解释文字
- 优先依据最新输入、心跳历史、记忆状态、能力列表、主动目标候选来决策
- 除非确信需要多个动作，否则优先少量高质量 intents
- 不要凭空捏造不存在的 chat_id、observation、goal_candidate
- 如果你知道可聊天对象的 `knock_id`、备注名或显示名，优先填写 `target_ref`
- 只有在输入里已经明确给出真实 `chat_id` 时，才直接填写 `target_chat_id`
- `payload` 必须严格遵守对应 `intent_type` 的固定子模板，不要混字段
- 不要输出精细浮点分数，请优先输出离散等级：
  - `priority_level / urgency_level / confidence_level / cost_level / risk_level / interruptiveness_level`
- `dedup_hint` 只需要给简短提示，不需要自己拼出稳定唯一 key
- 如果输入不足以支撑行动，请返回 `no_op`

当前元信息：
{current_meta}

当前情绪（供规划时参考）：
{mood_summary}

最近心跳历史：
{heartbeat_history}

输入源摘要：
{input_source_summary}

输入内容摘要：
{input_content_summary}

最新 observation：
{latest_observations}

记忆摘要：
{memory_summary}

聊天流摘要：
{chat_summary}

能力列表：
{capability_summary}

主动目标候选：
{goal_summary}

可聊天对象（knock 联系方式）：
{knock_summary}

按 intent_type 固定的 payload 子模板：
{payload_templates}

结构化输出 schema：
{output_schema}

结构化输出示例：
{output_example}
""".strip(),
    name="heartbeat_v2_planner_prompt",
)


def _json_block(value: Any, *, max_chars: int = 4000) -> str:
    try:
        rendered = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except Exception:
        rendered = str(value)
    if len(rendered) <= max_chars:
        return rendered
    return rendered[: max_chars - 20] + "\n...<truncated>..."


def compress_heartbeat_history_for_planner(
    heartbeat_history: list[dict[str, Any]] | None,
    *,
    max_ticks: int = 2,
) -> list[dict[str, Any]]:
    """
    将心跳历史压缩为规划器提示用摘要，保留角色特质与决策所需最小信息，控制 token。

    仅保留：每 tick 的 meta 摘要、intent 类型与 chat_id、receipt 类型与 status。
    完整历史仍通过 state["history"] 提供给意图消费者（executor 等）使用。
    """
    if not heartbeat_history or not isinstance(heartbeat_history, list):
        return []
    out: list[dict[str, Any]] = []
    for tick in heartbeat_history[-max_ticks:]:
        if not isinstance(tick, dict):
            continue
        meta = tick.get("meta") or {}
        if isinstance(meta, dict):
            slim_meta = {
                "loop_type": meta.get("loop_type"),
                "started_at": meta.get("started_at"),
                "queue_ready": meta.get("queue_ready_len"),
                "queue_delayed": meta.get("queue_delayed_len"),
            }
        else:
            slim_meta = {}
        intents = tick.get("intents") or []
        slim_intents = []
        for i in intents[:5]:
            if not isinstance(i, dict):
                continue
            slim_intents.append({
                "type": i.get("type"),
                "chat_id": (i.get("target_chat_id") or (i.get("payload") or {}).get("chat_id")) if isinstance(i.get("payload"), dict) else i.get("target_chat_id"),
            })
        receipts = tick.get("receipts") or []
        slim_receipts = []
        for r in receipts[:5]:
            if not isinstance(r, dict):
                continue
            slim_receipts.append({
                "action_type": r.get("action_type"),
                "status": r.get("status"),
                "chat_id": (r.get("outputs") or {}).get("chat_id") if isinstance(r.get("outputs"), dict) else None,
            })
        out.append({
            "meta": slim_meta,
            "intent_count": len(intents),
            "intents": slim_intents,
            "receipt_count": len(receipts),
            "receipts": slim_receipts,
        })
    return out


def build_planner_prompt_input(
    *,
    current_meta: dict[str, Any] | None = None,
    mood_summary: str | None = None,
    heartbeat_history: list[dict[str, Any]] | None = None,
    input_source_summary: dict[str, Any] | None = None,
    input_content_summary: dict[str, Any] | None = None,
    latest_observations: list[dict[str, Any]] | None = None,
    memory_summary: dict[str, Any] | None = None,
    chat_summary: dict[str, Any] | None = None,
    capability_summary: dict[str, Any] | None = None,
    goal_summary: dict[str, Any] | None = None,
    knock_summary: list[dict[str, Any]] | None = None,
    allowed_intent_types: list[str] | None = None,
) -> dict[str, str]:
    compressed_history = compress_heartbeat_history_for_planner(heartbeat_history)
    humanized_history = knock_manager.humanize_structure(compressed_history)
    humanized_input_source = knock_manager.humanize_structure(input_source_summary or {})
    humanized_input_content = knock_manager.humanize_structure(input_content_summary or {})
    humanized_observations = knock_manager.humanize_structure(latest_observations or [])
    humanized_memory = knock_manager.humanize_structure(memory_summary or {})
    humanized_chat = knock_manager.humanize_structure(chat_summary or {})
    humanized_goal = knock_manager.humanize_structure(goal_summary or {})
    effective_intent_types = allowed_intent_types or PLANNER_ALLOWED_INTENT_TYPES
    return {
        "allowed_intent_types": ", ".join(effective_intent_types),
        "allowed_lanes": ", ".join(PLANNER_ALLOWED_LANES),
        "allowed_score_levels": ", ".join(PLANNER_ALLOWED_SCORE_LEVELS),
        "allowed_expire_tiers": ", ".join(PLANNER_ALLOWED_EXPIRE_TIERS),
        "allowed_delay_tiers": ", ".join(PLANNER_ALLOWED_DELAY_TIERS),
        "current_meta": _json_block(knock_manager.humanize_structure(current_meta or {})),
        "mood_summary": (mood_summary or "").strip() or "（未提供）",
        "heartbeat_history": _json_block(
            humanized_history, max_chars=PLANNER_HEARTBEAT_HISTORY_MAX_CHARS
        ),
        "input_source_summary": _json_block(humanized_input_source),
        "input_content_summary": _json_block(humanized_input_content),
        "latest_observations": _json_block(humanized_observations),
        "memory_summary": _json_block(humanized_memory),
        "chat_summary": _json_block(humanized_chat),
        "capability_summary": _json_block(capability_summary or {}),
        "goal_summary": _json_block(humanized_goal),
        "knock_summary": _json_block(knock_summary or knock_manager.build_prompt_contact_summary(limit=20)),
        "payload_templates": _json_block(PLANNER_INTENT_PAYLOAD_TEMPLATES, max_chars=4000),
        "output_schema": _json_block(PLANNER_OUTPUT_SCHEMA, max_chars=6000),
        "output_example": _json_block(PLANNER_OUTPUT_EXAMPLE, max_chars=4000),
    }


def render_planner_prompt(
    *,
    current_meta: dict[str, Any] | None = None,
    mood_summary: str | None = None,
    heartbeat_history: list[dict[str, Any]] | None = None,
    input_source_summary: dict[str, Any] | None = None,
    input_content_summary: dict[str, Any] | None = None,
    latest_observations: list[dict[str, Any]] | None = None,
    memory_summary: dict[str, Any] | None = None,
    chat_summary: dict[str, Any] | None = None,
    capability_summary: dict[str, Any] | None = None,
    goal_summary: dict[str, Any] | None = None,
    knock_summary: list[dict[str, Any]] | None = None,
    allowed_intent_types: list[str] | None = None,
) -> str:
    kwargs = build_planner_prompt_input(
        current_meta=current_meta,
        mood_summary=mood_summary,
        heartbeat_history=heartbeat_history,
        input_source_summary=input_source_summary,
        input_content_summary=input_content_summary,
        latest_observations=latest_observations,
        memory_summary=memory_summary,
        chat_summary=chat_summary,
        capability_summary=capability_summary,
        goal_summary=goal_summary,
        knock_summary=knock_summary,
        allowed_intent_types=allowed_intent_types,
    )
    return PLANNER_PROMPT_TEMPLATE.format(**kwargs)


def parse_planner_output(raw: Any) -> dict[str, Any]:
    parsed = parse_structured_json(raw, default={})
    if not isinstance(parsed, dict):
        return {"summary": "", "planning_mode": "invalid", "intents": []}

    parsed = knock_manager.resolve_structure_chat_refs(parsed)
    intents = parsed.get("intents", [])
    if not isinstance(intents, list):
        intents = []

    normalized_intents: list[dict[str, Any]] = []
    for item in intents[:3]:
        if not isinstance(item, dict):
            continue
        target_chat_id = str(item.get("target_chat_id") or item.get("target_ref") or "")
        payload = _normalize_payload_by_intent(
            str(item.get("intent_type") or ""),
            item.get("payload", {}),
            target_chat_id=target_chat_id,
            fallback_reason=str(item.get("reason") or ""),
        )
        normalized_intents.append(
            {
                "source": str(item.get("source") or ""),
                "intent_type": str(item.get("intent_type") or ""),
                "target_chat_id": target_chat_id,
                "target_user_id": str(item.get("target_user_id") or ""),
                "reason": str(item.get("reason") or ""),
                "payload": knock_manager.resolve_structure_chat_refs(payload),
                "priority": _resolve_score_value(item, "priority", "priority_level"),
                "urgency": _resolve_score_value(item, "urgency", "urgency_level"),
                "confidence": _resolve_score_value(item, "confidence", "confidence_level"),
                "cost_hint": _resolve_score_value(item, "cost_hint", "cost_level"),
                "risk_hint": _resolve_score_value(item, "risk_hint", "risk_level"),
                "interruptiveness": _resolve_score_value(item, "interruptiveness", "interruptiveness_level"),
                "lane": str(item.get("lane") or "normal"),
                "dedup_key": _build_dedup_key_hint(item),
                "expire_after_s": _resolve_expire_seconds(item),
                "delayed_for_s": _resolve_delay_seconds(item),
            }
        )

    return {
        "summary": str(parsed.get("summary") or ""),
        "planning_mode": str(parsed.get("planning_mode") or ""),
        "intents": normalized_intents,
    }


def _normalize_payload_by_intent(
    intent_type: str,
    payload: Any,
    *,
    target_chat_id: str,
    fallback_reason: str,
) -> dict[str, Any]:
    payload_dict = payload if isinstance(payload, dict) else {}
    observation = payload_dict.get("observation", {})
    normalized_observation = observation if isinstance(observation, dict) else {}
    normalized_chat_id = str(payload_dict.get("chat_id") or target_chat_id or "").strip()
    normalized_intent = str(intent_type or "").strip()

    if normalized_intent == "reply":
        return {
            "chat_id": normalized_chat_id,
            "text": str(payload_dict.get("text") or "").strip(),
            "reply_reason": str(payload_dict.get("reply_reason") or fallback_reason or "").strip(),
            "observation": normalized_observation,
            "use_reply_generator": bool(payload_dict.get("use_reply_generator", False)),
            "think_level": _resolve_think_level(payload_dict.get("think_level")),
        }
    if normalized_intent == "retrieve":
        return {
            "chat_id": normalized_chat_id,
            "query": str(payload_dict.get("query") or "").strip(),
            "observation": normalized_observation,
        }
    if normalized_intent == "tool_call":
        return {
            "chat_id": normalized_chat_id,
            "query": str(payload_dict.get("query") or "").strip(),
            "observation": normalized_observation,
        }
    if normalized_intent == "explore":
        return {
            "chat_id": normalized_chat_id,
            "query": str(payload_dict.get("query") or "").strip(),
            "observation": normalized_observation,
            "source_chat_id": str(payload_dict.get("source_chat_id") or normalized_chat_id or "").strip(),
            "share_target_chat_id": str(payload_dict.get("share_target_chat_id") or "").strip(),
            "goal_candidate": payload_dict.get("goal_candidate", {}) if isinstance(payload_dict.get("goal_candidate", {}), dict) else {},
            "selector_reason": str(payload_dict.get("selector_reason") or "").strip(),
        }
    return {
        "reason": str(payload_dict.get("reason") or fallback_reason or "").strip(),
    }


def _resolve_think_level(value: Any) -> int:
    try:
        level = int(value)
    except Exception:
        level = 1
    return max(0, min(2, level))


def _clamp_score(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except Exception:
        return 0


def _resolve_score_value(item: dict[str, Any], numeric_key: str, level_key: str) -> float:
    if numeric_key in item:
        return _clamp_score(item.get(numeric_key))
    level = str(item.get(level_key) or "").strip().lower()
    mapping = {
        "low": 0.25,
        "medium": 0.55,
        "high": 0.85,
    }
    return mapping.get(level, 0.0)


def _resolve_expire_seconds(item: dict[str, Any]) -> int:
    if "expire_after_s" in item:
        return _safe_int(item.get("expire_after_s"))
    tier = str(item.get("expire_tier") or "").strip().lower()
    mapping = {
        "short": 90,
        "normal": 180,
        "long": 300,
    }
    return mapping.get(tier, 0)


def _resolve_delay_seconds(item: dict[str, Any]) -> int:
    if "delayed_for_s" in item:
        return _safe_int(item.get("delayed_for_s"))
    tier = str(item.get("delay_tier") or "").strip().lower()
    mapping = {
        "none": 0,
        "short": 2,
        "medium": 10,
    }
    return mapping.get(tier, 0)


def _build_dedup_key_hint(item: dict[str, Any]) -> str:
    explicit = str(item.get("dedup_key") or "").strip()
    if explicit:
        return explicit
    dedup_hint = str(item.get("dedup_hint") or "").strip()
    intent_type = str(item.get("intent_type") or "").strip()
    target_chat_id = str(item.get("target_chat_id") or "").strip()
    if dedup_hint and intent_type and target_chat_id:
        return f"{intent_type}-{target_chat_id}-{dedup_hint}"
    return dedup_hint
