"""
心跳系统 V2 规划器。

collect_inputs、generate_intents、score_intents。
P1 最小规则：notice 触发 reply，否则 no_op；后续可接入 LLM。
"""
from __future__ import annotations

import time
from typing import Any

from src.common.logger import get_logger
from src.common.knock import knock_manager
from src.config.config import global_config, model_config
from src.plugin_system.apis import llm_api

from .active_goal_source import ActiveGoalCandidate, active_goal_source
from .experience_store import experience_store
from .models import Intent
from .planner_prompt import parse_planner_output, render_planner_prompt
from .target_selector import target_chat_selector


logger = get_logger("heartbeat_v2.planner")


class HeartbeatPlanner:
    """规划器：根据状态生成并排序意图。"""

    SUPPORTED_LLM_INTENT_TYPES = {"reply", "retrieve", "no_op"}

    def _build_reply_reason(self, latest_observation: dict[str, Any]) -> str:
        source = str(latest_observation.get("source") or "text")
        intent_guess = str(latest_observation.get("intent_guess") or "conversation")
        metadata = latest_observation.get("metadata", {}) if isinstance(latest_observation.get("metadata", {}), dict) else {}
        notice_type = str(metadata.get("notice_type") or "").strip()
        if source == "notice":
            if notice_type == "poke":
                return "对方刚刚戳了你一下，这是明显的注意力请求，应当简短回应并确认在场。"
            if notice_type == "input_status":
                return "你观察到对方正在输入内容，适合给出轻量陪伴式回应，表达你在关注。"
            return "你收到了一个系统提醒，需要给出简短确认，表示你已经注意到。"
        if intent_guess == "attention_request":
            return "对方正在主动引起你的注意，应该自然地回应并维持对话在场感。"
        if intent_guess == "question":
            return "对方提出了一个问题，应该围绕问题本身生成自然回复。"
        return "根据最近的对话状态，你决定给出一条自然、简短但有在场感的回应。"

    def _has_recent_action(
        self,
        state: dict[str, Any],
        *,
        chat_id: str,
        action_type: str,
    ) -> bool:
        history_state = state.get("history", {})
        recent_actions = history_state.get("recent_receipt_actions", [])
        if not isinstance(recent_actions, list):
            return False
        for item in reversed(recent_actions):
            if not isinstance(item, dict):
                continue
            if str(item.get("chat_id") or "") != chat_id:
                continue
            if str(item.get("action_type") or "") != action_type:
                continue
            if str(item.get("status") or "") != "success":
                continue
            return True
        return False

    def _get_strategy_hints(
        self,
        state: dict[str, Any],
        chat_id: str,
        latest_observation: dict[str, Any],
        desired_action_type: str | None = None,
    ) -> list[dict[str, Any]]:
        hints = experience_store.match_strategy_hints(
            chat_id,
            latest_observation,
            desired_action_type=desired_action_type,
            limit=3,
        )
        if hints:
            experience_store.record_strategy_hits(
                chat_id,
                [str(item.get("strategy_key") or "") for item in hints],
            )
            return hints

        memory_state = state.get("memory", {})
        strategies = memory_state.get("distilled_strategies", {}).get(chat_id, [])
        if not isinstance(strategies, list):
            return []
        return strategies[:3]

    def _is_search_cooldown(self, state: dict[str, Any], chat_id: str) -> bool:
        return self._has_recent_action(state, chat_id=chat_id, action_type="search_web")

    def _strategy_confidence_boost(self, strategy_hints: list[dict[str, Any]]) -> float:
        if not strategy_hints:
            return 0.0
        top = strategy_hints[0]
        planner_weight = float(top.get("planner_weight", 0.0) or 0.0)
        return min(0.18, planner_weight * 0.18)

    def _strategy_interruptiveness_adjustment(self, strategy_hints: list[dict[str, Any]]) -> float:
        if not strategy_hints:
            return 0.0
        top = strategy_hints[0]
        adjustment = str(top.get("recommended_adjustment", ""))
        planner_weight = float(top.get("planner_weight", 0.0) or 0.0)
        if "降低打扰性" in adjustment:
            return min(0.25, 0.08 + planner_weight * 0.2)
        return -min(0.08, planner_weight * 0.08) if "保留当前策略" in adjustment else 0.0

    def _build_reply_text(self, latest_observation: dict[str, Any], strategy_hints: list[dict[str, Any]]) -> str:
        source = latest_observation.get("source")
        intent_guess = latest_observation.get("intent_guess")
        if source == "notice":
            notice_type = latest_observation.get("metadata", {}).get("notice_type")
            if notice_type == "poke":
                return "我在，刚刚感受到你戳我啦。"
            if notice_type == "input_status":
                return "我在看着，有想说的就继续发吧。"
            return "我留意到这个提醒了。"

        if strategy_hints:
            top_hint = strategy_hints[0]
            if "简洁" in str(top_hint.get("recommended_adjustment", "")):
                return "收到，我在。"

        if intent_guess == "memory_query":
            return "我先想想之前有没有相关线索。"
        return "我在，继续说。"

    def _build_memory_query(self, latest_observation: dict[str, Any]) -> str:
        text = str(latest_observation.get("text") or "").strip()
        if not text:
            return "最近这段对话里提到过什么"
        return text[:120]

    def _build_explore_query(self, chat: dict[str, Any]) -> str:
        last_message_text = str(chat.get("last_message_text") or "").strip()
        if not last_message_text:
            return ""
        explore_tokens = ("最近", "新闻", "更新", "变化", "发布", "消息", "情况", "新版本", "动态")
        if any(token in last_message_text for token in explore_tokens):
            return last_message_text[:120]
        return f"{last_message_text[:80]} 最新进展"

    def _pick_message_explore_chat(self, state: dict[str, Any]) -> dict[str, Any] | None:
        chat_state = state.get("chat", {})
        chats = chat_state.get("chats", [])
        if not isinstance(chats, list):
            return None

        observation_state = state.get("observation", {})
        by_chat = observation_state.get("by_chat", {})
        if not isinstance(by_chat, dict):
            by_chat = {}

        for item in chats:
            if not isinstance(item, dict):
                continue
            chat_id = str(item.get("chat_id") or "").strip()
            if not chat_id:
                continue
            silent_for_s = int(item.get("silent_for_s") or 0)
            last_message_text = str(item.get("last_message_text") or "").strip()
            recent_observations = by_chat.get(chat_id, [])
            if recent_observations:
                continue
            if silent_for_s < 300 or silent_for_s > 7200:
                continue
            if len(last_message_text) < 6:
                continue
            if self._is_search_cooldown(state, chat_id):
                continue
            return item
        return None

    def collect_inputs(self, state: dict[str, Any]) -> dict[str, Any]:
        """透传 state，后续可在此做预处理。"""

        return state

    def score_intents(self, intents: list[Intent]) -> list[Intent]:
        """按 score、created_at 排序，高分在前。"""

        intents.sort(key=lambda x: (x.score, x.created_at), reverse=True)
        return intents

    def _collect_prompt_chat_ids(self, state: dict[str, Any]) -> list[str]:
        chat_ids: list[str] = []

        def append_chat_id(value: Any) -> None:
            chat_id = str(value or "").strip()
            if chat_id and chat_id not in chat_ids:
                chat_ids.append(chat_id)

        observation_state = state.get("observation", {})
        recent_observations = observation_state.get("recent", [])
        if isinstance(recent_observations, list):
            for item in recent_observations[:12]:
                if isinstance(item, dict):
                    append_chat_id(item.get("chat_id"))

        chat_state = state.get("chat", {})
        chats = chat_state.get("chats", [])
        if isinstance(chats, list):
            for item in chats[:12]:
                if isinstance(item, dict):
                    append_chat_id(item.get("chat_id"))

        goal_state = state.get("goal", {})
        goal_candidates = goal_state.get("candidates", []) if isinstance(goal_state, dict) else []
        if isinstance(goal_candidates, list):
            for item in goal_candidates[:6]:
                if isinstance(item, dict):
                    append_chat_id(item.get("chat_id"))

        return chat_ids[:20]

    def _build_input_source_summary(self, state: dict[str, Any]) -> dict[str, Any]:
        observation_state = state.get("observation", {})
        recent = observation_state.get("recent", [])
        top_recent: list[dict[str, Any]] = []
        if isinstance(recent, list):
            for item in recent[:5]:
                if not isinstance(item, dict):
                    continue
                top_recent.append(
                    {
                        "chat_id": item.get("chat_id"),
                        "source": item.get("source"),
                        "intent_guess": item.get("intent_guess"),
                        "salience": item.get("salience"),
                        "created_at": item.get("created_at"),
                    }
                )
        return {
            "recent_count": len(recent) if isinstance(recent, list) else 0,
            "source_counts": observation_state.get("source_counts", {}),
            "top_recent": top_recent,
        }

    def _build_input_content_summary(self, state: dict[str, Any]) -> dict[str, Any]:
        observation_state = state.get("observation", {})
        recent = observation_state.get("recent", [])
        top_content: list[dict[str, Any]] = []
        if isinstance(recent, list):
            for item in recent[:5]:
                if not isinstance(item, dict):
                    continue
                top_content.append(
                    {
                        "chat_id": item.get("chat_id"),
                        "text": str(item.get("text") or "")[:160],
                        "metadata": item.get("metadata", {}) if isinstance(item.get("metadata", {}), dict) else {},
                    }
                )
        return {
            "latest_texts": top_content,
        }

    def _intent_from_llm_item(self, item: dict[str, Any]) -> Intent | None:
        intent_type = str(item.get("intent_type") or "").strip()
        if intent_type not in self.SUPPORTED_LLM_INTENT_TYPES:
            return None

        payload = item.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}

        target_chat_id = str(item.get("target_chat_id") or payload.get("chat_id") or "").strip()
        source = str(item.get("source") or "observation").strip() or "observation"
        lane = str(item.get("lane") or "normal").strip() or "normal"
        dedup_key = str(item.get("dedup_key") or "").strip() or None

        if intent_type == "reply":
            if not target_chat_id:
                return None
            normalized_payload = {
                "chat_id": target_chat_id,
                "text": str(payload.get("text") or "").strip(),
                "reply_reason": str(payload.get("reply_reason") or item.get("reason") or "").strip(),
                "observation": payload.get("observation", {}) if isinstance(payload.get("observation", {}), dict) else {},
                "use_reply_generator": bool(payload.get("use_reply_generator", False)),
                "think_level": int(payload.get("think_level", 1) or 1),
            }
        elif intent_type == "retrieve":
            if not target_chat_id:
                return None
            query = str(payload.get("query") or "").strip()
            if not query:
                return None
            normalized_payload = {
                "chat_id": target_chat_id,
                "query": query,
                "observation": payload.get("observation", {}) if isinstance(payload.get("observation", {}), dict) else {},
            }
        else:
            normalized_payload = {
                "reason": str(payload.get("reason") or item.get("reason") or "llm_noop").strip(),
            }
            target_chat_id = None

        return Intent.create(
            source=source,
            intent_type=intent_type,  # type: ignore[arg-type]
            target_chat_id=target_chat_id,
            target_user_id=str(item.get("target_user_id") or "").strip() or None,
            payload=normalized_payload,
            dedup_key=dedup_key,
            expire_after_s=int(item.get("expire_after_s") or 0) or None,
            delayed_for_s=int(item.get("delayed_for_s") or 0) or None,
            lane=lane,
            priority=float(item.get("priority") or 0.0),
            urgency=float(item.get("urgency") or 0.0),
            confidence=float(item.get("confidence") or 0.0),
            cost_hint=float(item.get("cost_hint") or 0.0),
            risk_hint=float(item.get("risk_hint") or 0.0),
            interruptiveness=float(item.get("interruptiveness") or 0.0),
        )

    async def llm_assisted_generate_intents(self, state: dict[str, Any]) -> list[Intent] | None:
        observation_state = state.get("observation", {})
        latest_observations = observation_state.get("recent", [])
        if not isinstance(latest_observations, list) or not latest_observations:
            return None

        prompt_chat_ids = self._collect_prompt_chat_ids(state)
        prompt = render_planner_prompt(
            current_meta={
                "planner_stage": "heartbeat_v2_llm_assisted_v1",
                "supported_runtime_intent_types": ["reply", "retrieve", "no_op"],
                "fallback_mode": "rule_planner_for_other_or_invalid_outputs",
                "generated_at": time.time(),
            },
            heartbeat_history=state.get("history", {}).get("last_tick") and [state.get("history", {}).get("last_tick")] or [],
            input_source_summary=self._build_input_source_summary(state),
            input_content_summary=self._build_input_content_summary(state),
            latest_observations=latest_observations[:8],
            memory_summary=state.get("memory", {}),
            chat_summary=state.get("chat", {}),
            capability_summary=state.get("capability", {}),
            goal_summary=state.get("goal", {}),
            knock_summary=knock_manager.build_prompt_contact_summary(chat_ids=prompt_chat_ids, limit=20),
            allowed_intent_types=["reply", "retrieve", "no_op"],
        )
        if global_config.debug.show_planner_prompt:
            logger.info(f"[heartbeat_v2 planner prompt]\n{prompt}")

        success, response, reasoning, model_name = await llm_api.generate_with_model(
            prompt,
            model_config.model_task_config.planner,
            request_type="heartbeat_v2_planner",
            temperature=0.2,
            max_tokens=1200,
        )
        if not success:
            logger.warning(f"[v2 planner] llm_assisted failed: {response}")
            return None

        parsed = parse_planner_output(response)
        raw_intents = parsed.get("intents", [])
        if not isinstance(raw_intents, list):
            return None

        intents: list[Intent] = []
        for item in raw_intents:
            if not isinstance(item, dict):
                continue
            intent = self._intent_from_llm_item(item)
            if intent is not None:
                intents.append(intent)

        if intents:
            logger.info(
                f"[v2 planner] llm_assisted accepted intents={len(intents)} model={model_name} reasoning_len={len(reasoning or '')}"
            )
            return self.score_intents(intents)

        logger.warning(
            f"[v2 planner] llm_assisted returned no usable intents, planning_mode={parsed.get('planning_mode', '')}, model={model_name}"
        )
        return None

    def _rule_generate_intents(self, state: dict[str, Any]) -> list[Intent]:
        """
        根据 observation + memory + strategy 生成意图：
        - notice/高显著度 attention_request -> reply
        - memory_query -> retrieve
        - 无触发时 -> no_op
        """
        intents: list[Intent] = []
        observation_state = state.get("observation", {})
        by_chat: dict[str, list[dict[str, Any]]] = observation_state.get("by_chat", {})

        for chat_id, observations in by_chat.items():
            if not observations:
                continue
            latest = observations[0]
            intent_guess = str(latest.get("intent_guess") or "conversation")
            source = str(latest.get("source") or "text")
            salience = float(latest.get("salience") or 0.0)
            base_interruptiveness = 0.15

            if source == "notice" or intent_guess == "attention_request":
                strategy_hints = self._get_strategy_hints(state, chat_id, latest, desired_action_type="reply")
                interruptiveness = max(
                    0.0,
                    min(1.0, base_interruptiveness + self._strategy_interruptiveness_adjustment(strategy_hints)),
                )
                confidence = min(0.98, 0.8 + self._strategy_confidence_boost(strategy_hints))
                intents.append(
                    Intent.create(
                        source="observation",
                        intent_type="reply",
                        target_chat_id=chat_id,
                        payload={
                            "chat_id": chat_id,
                            "text": self._build_reply_text(latest, strategy_hints),
                            "reply_reason": self._build_reply_reason(latest),
                            "observation": latest,
                            "use_reply_generator": True,
                            "think_level": 0 if source == "notice" else 1,
                            "enable_tool": False,
                            "matched_strategy_keys": [
                                str(item.get("strategy_key") or "") for item in strategy_hints
                            ],
                        },
                        dedup_key=f"reply-{chat_id}-{latest.get('intent_guess', 'notice')}",
                        expire_after_s=90,
                        lane="urgent",
                        priority=0.88,
                        urgency=min(1.0, 0.6 + salience * 0.4),
                        confidence=confidence,
                        cost_hint=0.2,
                        risk_hint=0.1,
                        interruptiveness=interruptiveness,
                    )
                )

            if intent_guess == "memory_query" and salience >= 0.45:
                strategy_hints = self._get_strategy_hints(state, chat_id, latest, desired_action_type="find_memory")
                intents.append(
                    Intent.create(
                        source="observation",
                        intent_type="retrieve",
                        target_chat_id=chat_id,
                        payload={
                            "chat_id": chat_id,
                            "query": self._build_memory_query(latest),
                            "observation": latest,
                            "matched_strategy_keys": [
                                str(item.get("strategy_key") or "") for item in strategy_hints
                            ],
                        },
                        dedup_key=f"retrieve-{chat_id}-{self._build_memory_query(latest)}",
                        expire_after_s=120,
                        lane="normal",
                        priority=0.68,
                        urgency=0.45,
                        confidence=min(
                            0.98,
                            max(0.45, 1.0 - float(latest.get("uncertainty") or 0.0))
                            + self._strategy_confidence_boost(strategy_hints),
                        ),
                        cost_hint=0.25,
                        risk_hint=0.05,
                        interruptiveness=0.05,
                    )
                )

            if intent_guess == "tool_request" and salience >= 0.4:
                strategy_hints = self._get_strategy_hints(state, chat_id, latest, desired_action_type="tool_call")
                intents.append(
                    Intent.create(
                        source="observation",
                        intent_type="tool_call",
                        target_chat_id=chat_id,
                        payload={
                            "chat_id": chat_id,
                            "query": str(latest.get("text") or "")[:200],
                            "observation": latest,
                            "matched_strategy_keys": [
                                str(item.get("strategy_key") or "") for item in strategy_hints
                            ],
                        },
                        dedup_key=f"tool-{chat_id}-{str(latest.get('text') or '')[:80]}",
                        expire_after_s=120,
                        lane="normal",
                        priority=0.72,
                        urgency=0.52,
                        confidence=min(
                            0.98,
                            max(0.4, 1.0 - float(latest.get("uncertainty") or 0.0))
                            + self._strategy_confidence_boost(strategy_hints),
                        ),
                        cost_hint=0.35,
                        risk_hint=0.12,
                        interruptiveness=0.08,
                    )
                )

        active_goal: ActiveGoalCandidate | None = None
        goal_state = state.get("goal", {})
        goal_candidates = goal_state.get("candidates", []) if isinstance(goal_state, dict) else []
        if isinstance(goal_candidates, list) and goal_candidates:
            first_goal = goal_candidates[0]
            if isinstance(first_goal, dict):
                try:
                    active_goal = ActiveGoalCandidate(
                        goal_id=str(first_goal.get("goal_id") or ""),
                        goal_type=str(first_goal.get("goal_type") or ""),
                        chat_id=str(first_goal.get("chat_id") or ""),
                        query=str(first_goal.get("query") or ""),
                        reason=str(first_goal.get("reason") or ""),
                        score=float(first_goal.get("score") or 0.0),
                        source=str(first_goal.get("source") or ""),
                        metadata=first_goal.get("metadata", {}) if isinstance(first_goal.get("metadata", {}), dict) else {},
                    )
                except Exception:  # noqa: BLE001
                    active_goal = None
        if active_goal is None:
            active_goal = active_goal_source.pick_goal(state)
        source_chat_id = ""
        query = ""
        explore_reason = ""
        goal_payload: dict[str, Any] = {}
        pseudo_observation: dict[str, Any] | None = None

        if active_goal and not self._is_search_cooldown(state, active_goal.chat_id):
            source_chat_id = active_goal.chat_id
            query = active_goal.query
            explore_reason = active_goal.reason
            goal_payload = active_goal.to_dict()
            pseudo_observation = {
                "chat_id": source_chat_id,
                "source": "system",
                "text": query,
                "intent_guess": "active_goal_explore",
                "salience": max(0.35, min(0.82, float(active_goal.score))),
                "metadata": {
                    "trigger": active_goal.source,
                    "goal_type": active_goal.goal_type,
                    "goal_reason": active_goal.reason,
                },
            }
        else:
            explore_chat = self._pick_message_explore_chat(state)
            if explore_chat:
                source_chat_id = str(explore_chat.get("chat_id") or "").strip()
                query = self._build_explore_query(explore_chat)
                if source_chat_id and query:
                    explore_reason = "heartbeat_active_thinking"
                    pseudo_observation = {
                        "chat_id": source_chat_id,
                        "source": "system",
                        "text": query,
                        "intent_guess": "active_explore",
                        "salience": 0.42,
                        "metadata": {
                            "trigger": "heartbeat_active_thinking",
                            "silent_for_s": int(explore_chat.get("silent_for_s") or 0),
                        },
                    }

        if source_chat_id and query and pseudo_observation:
            selection = target_chat_selector.select_share_target(
                source_chat_id=source_chat_id,
                query=query,
                state=state,
            )
            strategy_hints = self._get_strategy_hints(
                state,
                source_chat_id,
                pseudo_observation,
                desired_action_type="search_web",
            )
            intents.append(
                Intent.create(
                    source="scheduler",
                    intent_type="explore",
                    target_chat_id=source_chat_id,
                    payload={
                        "chat_id": source_chat_id,
                        "source_chat_id": source_chat_id,
                        "query": query,
                        "observation": pseudo_observation,
                        "explore_reason": explore_reason,
                        "goal_candidate": goal_payload,
                        "share_target_chat_id": selection.share_target_chat_id,
                        "selector_reason": selection.selector_reason,
                        "selector_score": selection.selector_score,
                        "selector_mode": selection.selection_mode,
                        "selector_candidates_preview": selection.candidates_preview,
                        "matched_strategy_keys": [
                            str(item.get("strategy_key") or "") for item in strategy_hints
                        ],
                    },
                    dedup_key=f"explore-{source_chat_id}-{query[:48]}",
                    expire_after_s=300,
                    lane="maintenance",
                    priority=0.46 if not goal_payload else 0.58,
                    urgency=0.22 if not goal_payload else 0.34,
                    confidence=min(0.92, 0.52 + self._strategy_confidence_boost(strategy_hints) + (0.08 if goal_payload else 0.0)),
                    cost_hint=0.38,
                    risk_hint=0.1,
                    interruptiveness=0.03,
                )
            )

        if not intents:
            intents.append(
                Intent.create(
                    source="scheduler",
                    intent_type="no_op",
                    payload={"reason": "no_trigger"},
                    dedup_key="no-op",
                    expire_after_s=60,
                    lane="maintenance",
                    priority=0.1,
                    urgency=0.1,
                    confidence=1.0,
                )
            )

        return self.score_intents(intents)

    async def generate_intents(self, state: dict[str, Any]) -> list[Intent]:
        llm_intents = await self.llm_assisted_generate_intents(state)
        if llm_intents is not None:
            return llm_intents
        return self._rule_generate_intents(state)
