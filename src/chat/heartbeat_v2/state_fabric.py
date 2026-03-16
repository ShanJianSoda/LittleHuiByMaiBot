"""
心跳系统 V2 状态汇聚层。

从 mood、memory、notice、chat、history 等模块收集状态，
供 Planner 生成意图使用。
"""
from __future__ import annotations

import re
import time
from typing import Any

from src.chat.message_receive.chat_stream import get_chat_manager
from src.common.knock import knock_manager
from src.common.database.database_model import ChatHistory, Expression, Messages, PersonInfo, ThinkingBack
from src.common.logger import get_logger
from src.mood.mood_manager import mood_manager
from src.mood.emotion_engine import get_mood_for_prompt

from .active_goal_source import active_goal_source, build_goal_signature
from .capability_registry import capability_registry
from .experience_store import experience_store
from .models import Observation
from .structured_output import parse_structured_json
from .target_selector import target_chat_candidate_builder


logger = get_logger("heartbeat_v2.state_fabric")


class StateFabric:
    """状态汇聚：collect_* 系列方法聚合各维度状态。"""

    def _safe_json_loads(self, raw: Any, default: Any) -> Any:
        return parse_structured_json(raw, default)

    def _read_attr(self, item: Any, name: str, default: Any = None) -> Any:
        if isinstance(item, dict):
            return item.get(name, default)
        return getattr(item, name, default)

    def _read_message_info_attr(self, item: Any, name: str, default: Any = None) -> Any:
        message_info = getattr(item, "message_info", None)
        if message_info is None:
            return default
        return getattr(message_info, name, default)

    def _classify_message_source(self, text: str, is_notice: bool) -> str:
        if is_notice:
            return "notice"
        lowered = text.lower()
        if "[图片" in text or "[image" in lowered or "image:" in lowered:
            return "image"
        if "[语音" in text or "[voice" in lowered or "audio:" in lowered:
            return "audio"
        return "text"

    def _extract_entities(self, text: str) -> list[str]:
        candidates = re.findall(r"[\u4e00-\u9fff]{2,6}|[A-Za-z][A-Za-z0-9_]{1,20}", text)
        seen: set[str] = set()
        entities: list[str] = []
        for item in candidates:
            cleaned = item.strip()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            entities.append(cleaned)
            if len(entities) >= 6:
                break
        return entities

    def _guess_intent(self, text: str, source: str, notice_type: str) -> str:
        if source == "notice":
            if notice_type in {"poke", "input_status"}:
                return "attention_request"
            return "system_notice"
        if source in {"image", "audio"}:
            return "multimodal_context"
        lowered = text.lower()
        if any(token in text for token in ("上次", "之前", "记得", "回忆", "以前")):
            return "memory_query"
        if "?" in text or "？" in text or any(token in text for token in ("什么", "怎么", "为什么", "吗", "呢")):
            return "question"
        if any(token in lowered for token in ("帮我", "查", "搜索", "tool", "调用")):
            return "tool_request"
        return "conversation"

    def _build_emotion_hint(self, item: Any) -> dict[str, Any]:
        v = getattr(item, "emotion_v", None)
        a = getattr(item, "emotion_a", None)
        d = getattr(item, "emotion_d", None)
        if v is None and a is None and d is None:
            return {}
        tone = "neutral"
        try:
            if v is not None and float(v) > 0.35:
                tone = "positive"
            elif v is not None and float(v) < -0.35:
                tone = "negative"
            elif a is not None and float(a) > 0.55:
                tone = "activated"
        except (TypeError, ValueError):
            tone = "neutral"
        return {"v": v, "a": a, "d": d, "tone": tone}

    def build_observation(self, item: Any) -> Observation:
        text = (
            self._read_attr(item, "processed_plain_text")
            or self._read_attr(item, "display_message")
            or self._read_attr(item, "notice_type")
            or ""
        ).strip()
        is_notice = bool(self._read_attr(item, "is_notice", False) or self._read_attr(item, "is_notify", False))
        source = self._classify_message_source(text, is_notice)
        notice_type = str(self._read_attr(item, "notice_type", "") or "")
        salience = 0.35
        if source == "notice":
            salience += 0.35
        if self._read_attr(item, "is_at", False) or self._read_attr(item, "is_mentioned", False):
            salience += 0.2
        if "?" in text or "？" in text:
            salience += 0.1
        if self._read_attr(item, "reply_to", None) or getattr(self._read_attr(item, "reply", None), "message_info", None):
            salience += 0.05
        chat_id = str(
            self._read_attr(item, "chat_id", "")
            or getattr(self._read_attr(item, "chat_stream", None), "stream_id", "")
            or ""
        )
        message_id = str(self._read_attr(item, "message_id", "") or self._read_message_info_attr(item, "message_id", "") or "")
        created_at = float(self._read_attr(item, "time", time.time()) or self._read_message_info_attr(item, "time", time.time()) or time.time())
        platform = str(self._read_message_info_attr(item, "platform", "") or self._read_attr(item, "platform", "") or "")
        user_info = self._read_message_info_attr(item, "user_info", None)
        group_info = self._read_message_info_attr(item, "group_info", None)
        observation = Observation.create(
            chat_id=chat_id,
            source=source,  # type: ignore[arg-type]
            created_at=created_at,
            message_id=message_id,
            text=text[:500],
            salience=salience,
            emotion_hint=self._build_emotion_hint(item),
            entities=self._extract_entities(text),
            intent_guess=self._guess_intent(text, source, notice_type),
            uncertainty=0.15 if source == "notice" else (0.25 if source in {"image", "audio"} else 0.4),
            metadata={
                "notice_type": notice_type,
                "is_notice": is_notice,
                "is_at": bool(self._read_attr(item, "is_at", False)),
                "is_mentioned": bool(self._read_attr(item, "is_mentioned", False)),
                "platform": platform,
                "user_id": str(getattr(user_info, "user_id", "") or ""),
                "group_id": str(getattr(group_info, "group_id", "") or ""),
                "has_image": bool(self._read_attr(item, "is_picid", False)),
                "has_audio": bool(self._read_attr(item, "is_voice", False)),
                "knock": knock_manager.annotate_chat_ref(chat_id) if chat_id else {},
            },
        )
        return observation

    def _build_observation(self, item: Any) -> dict[str, Any]:
        return self.build_observation(item).to_dict()

    def collect_mood_state(self) -> dict[str, Any]:
        """
        收集情绪状态（mood_state、mood_for_prompt、可选 emotion_v/a/d）。

        mood_for_prompt：供提示词使用的描述，优先含 VAD 转描述（vad_to_bucket），
        与 get_mood_for_prompt(chat_id, include_vad=True) 一致，供 consumer 拼完整提示词用。
        """
        try:
            mood = mood_manager.get_mood_by_chat_id("global")
            mood_state = getattr(mood, "mood_state", "") or ""
            mood_for_prompt = get_mood_for_prompt("global", include_vad=True)
            return {
                "mood_state": mood_state,
                "mood_for_prompt": mood_for_prompt,
                "emotion_v": getattr(mood, "emotion_v", None),
                "emotion_a": getattr(mood, "emotion_a", None),
                "emotion_d": getattr(mood, "emotion_d", None),
            }
        except Exception as e:  # noqa: BLE001
            logger.error(f"collect_mood_state failed: {e}")
            return {"mood_state": "unknown", "mood_for_prompt": "unknown"}

    def collect_memory_state(self, chat_ids: list[str] | None = None) -> dict[str, Any]:
        """收集事件、事实、策略三层记忆快照。"""

        target_chat_ids = list(chat_ids or [])
        recent_events: list[dict[str, Any]] = []
        recent_facts: list[dict[str, Any]] = []
        unresolved_questions: list[dict[str, Any]] = []
        relation_memories: list[dict[str, Any]] = []
        style_patterns: list[dict[str, Any]] = []
        distilled_strategies: dict[str, list[dict[str, Any]]] = {}

        try:
            event_query = (
                ChatHistory.select(
                    ChatHistory.chat_id,
                    ChatHistory.theme,
                    ChatHistory.summary,
                    ChatHistory.keywords,
                    ChatHistory.end_time,
                )
                .order_by(ChatHistory.end_time.desc())
                .limit(12)
            )
            for item in event_query:
                recent_events.append(
                    {
                        "chat_id": item.chat_id,
                        "theme": item.theme,
                        "summary": (item.summary or "")[:200],
                        "keywords": self._safe_json_loads(item.keywords, [])[:6],
                        "time": item.end_time,
                    }
                )
        except Exception as e:  # noqa: BLE001
            logger.error(f"collect_memory_state events failed: {e}")

        try:
            fact_query = (
                ThinkingBack.select(
                    ThinkingBack.chat_id,
                    ThinkingBack.question,
                    ThinkingBack.answer,
                    ThinkingBack.update_time,
                )
                .where(ThinkingBack.found_answer == True)  # noqa: E712
                .order_by(ThinkingBack.update_time.desc())
                .limit(10)
            )
            for item in fact_query:
                recent_facts.append(
                    {
                        "chat_id": item.chat_id,
                        "question": item.question,
                        "answer": (item.answer or "")[:200],
                        "time": item.update_time,
                    }
                )
        except Exception as e:  # noqa: BLE001
            logger.error(f"collect_memory_state facts failed: {e}")

        try:
            unresolved_query = (
                ThinkingBack.select(
                    ThinkingBack.chat_id,
                    ThinkingBack.question,
                    ThinkingBack.context,
                    ThinkingBack.update_time,
                )
                .where(ThinkingBack.found_answer == False)  # noqa: E712
                .order_by(ThinkingBack.update_time.desc())
                .limit(8)
            )
            for item in unresolved_query:
                unresolved_questions.append(
                    {
                        "chat_id": item.chat_id,
                        "question": item.question,
                        "context": (item.context or "")[:200],
                        "time": item.update_time,
                    }
                )
        except Exception as e:  # noqa: BLE001
            logger.error(f"collect_memory_state unresolved facts failed: {e}")

        try:
            style_query = (
                Expression.select(
                    Expression.chat_id,
                    Expression.situation,
                    Expression.style,
                    Expression.count,
                    Expression.last_active_time,
                )
                .where(Expression.rejected == False)  # noqa: E712
                .order_by(Expression.last_active_time.desc())
                .limit(10)
            )
            for item in style_query:
                style_patterns.append(
                    {
                        "chat_id": item.chat_id,
                        "situation": item.situation,
                        "style": item.style,
                        "count": item.count,
                        "time": item.last_active_time,
                    }
                )
        except Exception as e:  # noqa: BLE001
            logger.error(f"collect_memory_state styles failed: {e}")

        try:
            relation_query = (
                PersonInfo.select(
                    PersonInfo.platform,
                    PersonInfo.user_id,
                    PersonInfo.person_id,
                    PersonInfo.person_name,
                    PersonInfo.nickname,
                    PersonInfo.memory_points,
                    PersonInfo.last_know,
                )
                .where(PersonInfo.is_known == True)  # noqa: E712
                .order_by(PersonInfo.last_know.desc())
                .limit(10)
            )
            for item in relation_query:
                memory_points = self._safe_json_loads(getattr(item, "memory_points", None), [])
                if not isinstance(memory_points, list):
                    raw_points = str(getattr(item, "memory_points", "") or "").strip()
                    memory_points = [raw_points] if raw_points else []
                relation_memories.append(
                    {
                        "platform": item.platform,
                        "user_id": item.user_id,
                        "person_id": item.person_id,
                        "person_name": item.person_name,
                        "nickname": item.nickname,
                        "memory_points": memory_points[:4] if isinstance(memory_points, list) else [],
                        "time": item.last_know,
                    }
                )
        except Exception as e:  # noqa: BLE001
            logger.error(f"collect_memory_state relation memories failed: {e}")

        try:
            for chat_id in target_chat_ids[:8]:
                payload = experience_store.get_strategy_memory(chat_id)
                distilled_strategies[chat_id] = payload.get("strategies", [])[:5]
        except Exception as e:  # noqa: BLE001
            logger.error(f"collect_memory_state strategies failed: {e}")

        return {
            "memory_ready": True,
            "event_count": len(recent_events),
            "fact_count": len(recent_facts),
            "strategy_count": len(style_patterns),
            "recent_events": recent_events,
            "recent_facts": recent_facts,
            "unresolved_questions": unresolved_questions,
            "relation_memories": relation_memories,
            "style_patterns": style_patterns,
            "distilled_strategies": distilled_strategies,
        }

    def collect_notice_state(self, window_seconds: int = 120) -> dict[str, Any]:
        """收集近期通知（戳一戳、输入状态、撤回等）按 chat 聚合。"""

        now = time.time()
        try:
            notices = (
                Messages.select(Messages.chat_id, Messages.notice_type, Messages.time)
                .where(
                    (Messages.is_notice == True)  # noqa: E712
                    & (Messages.time >= now - window_seconds)
                )
                .order_by(Messages.time.desc())
            )
            by_chat: dict[str, list[str]] = {}
            total = 0
            for item in notices:
                total += 1
                by_chat.setdefault(item.chat_id, []).append(item.notice_type or "unknown")
            return {"total_notices": total, "chat_notices": by_chat}
        except Exception as e:  # noqa: BLE001
            logger.error(f"collect_notice_state failed: {e}")
            return {"total_notices": 0, "chat_notices": {}}

    def collect_history_state(self, recent_history: list[dict[str, Any]]) -> dict[str, Any]:
        """收集最近 N 次心跳历史摘要。"""

        recent_receipt_actions: list[dict[str, Any]] = []
        resolved_goal_signatures: list[str] = []
        for tick in recent_history[-30:]:
            receipts = tick.get("receipts", []) if isinstance(tick, dict) else []
            if not isinstance(receipts, list):
                continue
            for receipt in receipts[-5:]:
                if not isinstance(receipt, dict):
                    continue
                outputs = receipt.get("outputs", {}) if isinstance(receipt.get("outputs", {}), dict) else {}
                chat_id = outputs.get("chat_id")
                query = outputs.get("query")
                goal_signature = str(outputs.get("goal_signature") or "").strip()
                if not goal_signature:
                    goal_signature = build_goal_signature(str(chat_id or ""), str(query or ""))
                if (
                    receipt.get("action_type") == "search_web"
                    and receipt.get("status") == "success"
                    and str(receipt.get("reason") or "") == "search_completed"
                    and str(outputs.get("search_result") or "").strip()
                    and goal_signature
                    and goal_signature not in resolved_goal_signatures
                ):
                    resolved_goal_signatures.append(goal_signature)
                recent_receipt_actions.append(
                    {
                        "action_type": receipt.get("action_type"),
                        "status": receipt.get("status"),
                        "chat_id": chat_id,
                        "query": query,
                        "share_target_chat_id": outputs.get("share_target_chat_id"),
                        "reason": receipt.get("reason"),
                        "timestamp": receipt.get("timestamp"),
                        "goal_signature": goal_signature,
                    }
                )
        return {
            "recent_tick_count": len(recent_history),
            "last_tick": recent_history[-1] if recent_history else None,
            "recent_receipt_actions": recent_receipt_actions[-20:],
            "resolved_goal_signatures": resolved_goal_signatures[-20:],
        }

    def collect_chat_state(self) -> dict[str, Any]:
        """收集各聊天流最后消息时间、静默时长等。"""

        manager = get_chat_manager()
        now = time.time()
        candidates = target_chat_candidate_builder.build(manager.streams)
        chats = [item.to_dict() for item in candidates]
        return {
            "chats": chats,
            "candidates": chats,
            "chat_count": len(chats),
            "now": now,
        }

    def collect_observation_state(
        self,
        window_seconds: int = 180,
        limit: int = 30,
        ingress_observations: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """标准化近期文本/图片/语音/通知为 observation。"""

        now = time.time()
        try:
            recent: list[dict[str, Any]] = []
            by_chat: dict[str, list[dict[str, Any]]] = {}
            source_counts: dict[str, int] = {}
            seen_keys: set[str] = set()

            def append_observation(observation: dict[str, Any]) -> None:
                if not isinstance(observation, dict):
                    return
                message_id = str(observation.get("message_id") or "").strip()
                observation_id = str(observation.get("observation_id") or "").strip()
                dedup_key = message_id or observation_id
                if dedup_key and dedup_key in seen_keys:
                    return
                if dedup_key:
                    seen_keys.add(dedup_key)
                recent.append(observation)
                chat_id = str(observation.get("chat_id") or "").strip()
                by_chat.setdefault(chat_id, []).append(observation)
                source = str(observation.get("source") or "")
                source_counts[source] = source_counts.get(source, 0) + 1

            for item in ingress_observations or []:
                created_at = float(item.get("created_at") or 0.0) if isinstance(item, dict) else 0.0
                if created_at and (now - created_at) > window_seconds:
                    continue
                append_observation(item)

            rows = (
                Messages.select(
                    Messages.message_id,
                    Messages.chat_id,
                    Messages.time,
                    Messages.processed_plain_text,
                    Messages.display_message,
                    Messages.reply_to,
                    Messages.is_notice,
                    Messages.notice_type,
                    Messages.is_at,
                    Messages.is_mentioned,
                    Messages.emotion_v,
                    Messages.emotion_a,
                    Messages.emotion_d,
                )
                .where(Messages.time >= now - window_seconds)
                .order_by(Messages.time.desc())
                .limit(limit)
            )
            for item in rows:
                append_observation(self._build_observation(item))
            recent.sort(key=lambda x: float(x.get("created_at") or 0.0), reverse=True)
            for chat_id, items in by_chat.items():
                items.sort(key=lambda x: float(x.get("created_at") or 0.0), reverse=True)
                by_chat[chat_id] = items[:limit]
            return {
                "recent": recent[:limit],
                "by_chat": by_chat,
                "source_counts": source_counts,
                "window_seconds": window_seconds,
                "ingress_recent_count": len(list(ingress_observations or [])),
            }
        except Exception as e:  # noqa: BLE001
            logger.error(f"collect_observation_state failed: {e}")
            return {
                "recent": [],
                "by_chat": {},
                "source_counts": {},
                "window_seconds": window_seconds,
                "ingress_recent_count": 0,
            }

    def collect_capability_state(self) -> dict[str, Any]:
        """收集工具、技能、MCP 三类能力快照。"""

        try:
            snapshot = capability_registry.snapshot()
            return {
                "tools": snapshot.get("tools", []),
                "skills": snapshot.get("skills", []),
                "mcps": snapshot.get("mcps", []),
                "tool_count": len(snapshot.get("tools", [])),
                "skill_count": len(snapshot.get("skills", [])),
                "mcp_count": len(snapshot.get("mcps", [])),
            }
        except Exception as e:  # noqa: BLE001
            logger.error(f"collect_capability_state failed: {e}")
            return {"tools": [], "skills": [], "mcps": [], "tool_count": 0, "skill_count": 0, "mcp_count": 0}

    def collect_all(
        self,
        recent_history: list[dict[str, Any]],
        ingress_observations: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """汇总所有维度状态，供 Planner 使用。"""

        chat_state = self.collect_chat_state()
        chat_ids = [item.get("chat_id", "") for item in chat_state.get("chats", []) if item.get("chat_id")]
        state = {
            "mood": self.collect_mood_state(),
            "memory": self.collect_memory_state(chat_ids),
            "notice": self.collect_notice_state(),
            "history": self.collect_history_state(recent_history),
            "chat": chat_state,
            "observation": self.collect_observation_state(ingress_observations=ingress_observations),
            "capability": self.collect_capability_state(),
        }
        try:
            goal_candidates = active_goal_source.collect_candidates(state)
            state["goal"] = {
                "candidates": [item.to_dict() for item in goal_candidates],
                "count": len(goal_candidates),
            }
        except Exception as e:  # noqa: BLE001
            logger.error(f"collect_goal_state failed: {e}")
            state["goal"] = {"candidates": [], "count": 0}
        try:
            from .current_planners import current_planners_store
            state["current_planners"] = current_planners_store.list_for_state(limit=20)
        except Exception as e:  # noqa: BLE001
            logger.error(f"collect current_planners failed: {e}")
            state["current_planners"] = []
        return state
