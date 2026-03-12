"""
心跳系统 V2 技能编排层。

把单次 tool_call 从“直接调工具”提升为：
context -> 选择 skill -> 顺序执行 -> 聚合结果。
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any

from src.chat.message_receive.chat_stream import get_chat_manager
from src.common.logger import get_logger
from src.memory_system.memory_retrieval import build_memory_retrieval_prompt
from src.plugin_system.apis import message_api
from src.plugin_system.apis.tool_api import get_tool_instance
from src.plugin_system.core.tool_use import ToolExecutor

from .experience_store import experience_store


logger = get_logger("heartbeat_v2.skill_orchestrator")


@dataclass
class SkillContext:
    chat_id: str
    query: str
    sender: str = "用户"
    observation: dict[str, Any] = field(default_factory=dict)
    chat_history: str = ""
    chat_stream: Any = None
    memory_result: str = ""
    skill_outputs: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseSkill:
    name = "base_skill"

    def should_run(self, context: SkillContext) -> bool:
        return True

    async def run(self, context: SkillContext) -> dict[str, Any]:
        raise NotImplementedError


class MemoryRecallSkill(BaseSkill):
    name = "memory_recall_skill"

    def should_run(self, context: SkillContext) -> bool:
        text = context.query
        intent_guess = str(context.observation.get("intent_guess") or "")
        memory_tokens = ("上次", "之前", "以前", "记得", "回忆", "聊过", "提到过", "历史")
        return intent_guess == "memory_query" or any(token in text for token in memory_tokens)

    async def run(self, context: SkillContext) -> dict[str, Any]:
        if context.chat_stream is None:
            return {"skill_name": self.name, "status": "skipped", "reason": "missing_chat_stream", "content": ""}

        try:
            result = await build_memory_retrieval_prompt(
                message=context.chat_history or context.query,
                sender=context.sender,
                target=context.query,
                chat_stream=context.chat_stream,
                think_level=1,
                question=context.query,
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"MemoryRecallSkill failed for {context.chat_id}: {e}")
            return {"skill_name": self.name, "status": "failed", "reason": str(e), "content": ""}

        context.memory_result = result or ""
        return {
            "skill_name": self.name,
            "status": "success" if result else "skipped",
            "reason": "memory_found" if result else "memory_empty",
            "content": result or "",
        }


class ToolUseSkill(BaseSkill):
    name = "tool_use_skill"

    def should_run(self, context: SkillContext) -> bool:
        return bool(context.query.strip())

    async def run(self, context: SkillContext) -> dict[str, Any]:
        executor = ToolExecutor(chat_id=context.chat_id)
        enriched_query = context.query
        if context.memory_result.strip():
            enriched_query = f"{context.query}\n\n可参考的相关记忆：\n{context.memory_result}"

        try:
            tool_results, used_tools, prompt = await executor.execute_from_chat_message(
                target_message=enriched_query,
                chat_history=context.chat_history,
                sender=context.sender,
                return_details=True,
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"ToolUseSkill failed for {context.chat_id}: {e}")
            return {
                "skill_name": self.name,
                "status": "failed",
                "reason": str(e),
                "used_tools": [],
                "tool_results": [],
                "prompt": "",
            }

        return {
            "skill_name": self.name,
            "status": "success" if used_tools else "skipped",
            "reason": "tool_executed" if used_tools else "tool_not_needed",
            "used_tools": used_tools,
            "tool_results": tool_results,
            "prompt": prompt,
        }


class WebSearchSkill(BaseSkill):
    name = "web_search_skill"

    def should_run(self, context: SkillContext) -> bool:
        return bool(context.query.strip())

    async def run(self, context: SkillContext) -> dict[str, Any]:
        tool_instance = get_tool_instance("web_search", context.chat_stream)
        if tool_instance is None:
            return {
                "skill_name": self.name,
                "status": "failed",
                "reason": "web_search_tool_unavailable",
                "content": "",
                "used_tools": [],
            }

        try:
            result = await tool_instance.execute({"question": context.query})
        except Exception as e:  # noqa: BLE001
            logger.error(f"WebSearchSkill failed for {context.chat_id}: {e}")
            return {
                "skill_name": self.name,
                "status": "failed",
                "reason": str(e),
                "content": "",
                "used_tools": ["web_search"],
            }

        content = ""
        if isinstance(result, dict):
            content = str(result.get("content") or "").strip()
        return {
            "skill_name": self.name,
            "status": "success" if content else "skipped",
            "reason": "search_completed" if content else "search_empty",
            "content": content,
            "tool_result": result if isinstance(result, dict) else {},
            "used_tools": ["web_search"],
        }


class SkillOrchestrator:
    """为 tool_call 提供共享上下文、技能选择和结果聚合。"""

    def __init__(self) -> None:
        self.skills: list[BaseSkill] = [
            MemoryRecallSkill(),
            ToolUseSkill(),
        ]
        self.skill_map = {skill.name: skill for skill in self.skills}
        self.web_search_skill = WebSearchSkill()

    def _load_chat_context(self, chat_id: str, query: str, observation: dict[str, Any] | None) -> SkillContext:
        manager = get_chat_manager()
        chat_stream = manager.get_stream(chat_id)

        end_ts = time.time()
        start_ts = end_ts - 1800
        history_messages = message_api.get_messages_by_time_in_chat(
            chat_id=chat_id,
            start_time=start_ts,
            end_time=end_ts,
            limit=20,
            limit_mode="latest",
            filter_mai=False,
            filter_command=False,
        )
        chat_history = ""
        if history_messages:
            try:
                chat_history = message_api.build_readable_messages(
                    history_messages,
                    replace_bot_name=True,
                    timestamp_mode="normal_no_YMD",
                    read_mark=0.0,
                    truncate=False,
                    show_actions=False,
                )
            except Exception as e:  # noqa: BLE001
                logger.error(f"build chat history failed for {chat_id}: {e}")

        return SkillContext(
            chat_id=chat_id,
            query=query,
            sender="用户",
            observation=observation or {},
            chat_history=chat_history,
            chat_stream=chat_stream,
        )

    def _default_skill_plan(self, context: SkillContext) -> list[BaseSkill]:
        return [skill for skill in self.skills if skill.should_run(context)]

    def _build_plan_from_chain(self, context: SkillContext, skill_names: list[str]) -> list[BaseSkill]:
        resolved: list[BaseSkill] = []
        seen: set[str] = set()
        for skill_name in skill_names:
            name = str(skill_name or "").strip()
            skill = self.skill_map.get(name)
            if not skill or name in seen or not skill.should_run(context):
                continue
            resolved.append(skill)
            seen.add(name)

        for skill in self.skills:
            if skill.name in seen:
                continue
            if skill.should_run(context):
                resolved.append(skill)
        return resolved

    def _pick_alternative_hint(
        self,
        matched_hints: list[dict[str, Any]],
        top_hint: dict[str, Any],
    ) -> dict[str, Any] | None:
        top_key = str(top_hint.get("strategy_key") or "")
        for item in matched_hints[1:]:
            if str(item.get("strategy_key") or "") != top_key:
                chain = item.get("recommended_skill_chain", [])
                if isinstance(chain, list) and chain:
                    return item
        return None

    def _apply_selection_mode_bias(
        self,
        base_exploration_rate: float,
        selection_mode_hints: list[dict[str, Any]],
    ) -> tuple[float, dict[str, Any] | None]:
        if not selection_mode_hints:
            return base_exploration_rate, None

        top_hint = selection_mode_hints[0]
        recommended_mode = str(top_hint.get("recommended_selection_mode") or "").strip()
        planner_weight = float(top_hint.get("planner_weight", 0.0) or 0.0)
        success_rate = float(top_hint.get("success_rate", 0.0) or 0.0)
        confidence = max(0.0, min(1.0, planner_weight * 0.6 + success_rate * 0.4))

        adjusted = base_exploration_rate
        if recommended_mode == "exploit_top_combo":
            adjusted -= 0.18 * confidence
        elif recommended_mode in {"explore_alternative_combo", "explore_default_plan"}:
            adjusted += 0.2 * confidence
        elif recommended_mode in {"default_no_history", "default_low_confidence"}:
            adjusted += 0.1 * confidence

        adjusted = max(0.02, min(0.6, adjusted))
        return round(adjusted, 4), top_hint

    def _resolve_skill_plan(
        self,
        context: SkillContext,
    ) -> tuple[list[BaseSkill], list[dict[str, Any]], list[str], dict[str, Any]]:
        matched_hints = experience_store.match_skill_combo_hints(
            context.chat_id,
            context.observation,
            limit=3,
        )
        if not matched_hints:
            return self._default_skill_plan(context), [], [], {"selection_mode": "default_no_history"}

        top_hint = matched_hints[0]
        planner_weight = float(top_hint.get("planner_weight", 0.0) or 0.0)
        recommended_chain = top_hint.get("recommended_skill_chain", [])
        selection_mode_hints = experience_store.match_selection_mode_hints(
            context.chat_id,
            context.observation,
            limit=2,
        )
        selected_meta_strategy_keys: list[str] = []
        adjusted_exploration_rate = 0.0
        selected_selection_mode_hint: dict[str, Any] | None = None
        if not isinstance(recommended_chain, list) or planner_weight < 0.35:
            return self._default_skill_plan(context), matched_hints, [], {
                "selection_mode": "default_low_confidence",
                "top_strategy_key": str(top_hint.get("strategy_key") or ""),
                "selection_mode_strategy_keys": [],
            }

        exploration_rate = float(top_hint.get("exploration_rate", 0.0) or 0.0)
        adjusted_exploration_rate, selected_selection_mode_hint = self._apply_selection_mode_bias(
            exploration_rate,
            selection_mode_hints,
        )
        decision_roll = random.random()
        if decision_roll < adjusted_exploration_rate:
            alternative_hint = self._pick_alternative_hint(matched_hints, top_hint)
            if alternative_hint:
                alt_chain = alternative_hint.get("recommended_skill_chain", [])
                if isinstance(alt_chain, list) and alt_chain:
                    if (
                        selected_selection_mode_hint
                        and str(selected_selection_mode_hint.get("recommended_selection_mode") or "")
                        == "explore_alternative_combo"
                    ):
                        selected_meta_strategy_keys.append(str(selected_selection_mode_hint.get("strategy_key") or ""))
                    return (
                        self._build_plan_from_chain(context, alt_chain),
                        matched_hints,
                        [str(alternative_hint.get("strategy_key") or "")] + selected_meta_strategy_keys,
                        {
                            "selection_mode": "explore_alternative_combo",
                            "top_strategy_key": str(top_hint.get("strategy_key") or ""),
                            "selected_strategy_key": str(alternative_hint.get("strategy_key") or ""),
                            "exploration_rate": exploration_rate,
                            "adjusted_exploration_rate": adjusted_exploration_rate,
                            "decision_roll": round(decision_roll, 4),
                            "selection_mode_strategy_keys": selected_meta_strategy_keys,
                            "selection_mode_hints": selection_mode_hints,
                        },
                    )
            if (
                selected_selection_mode_hint
                and str(selected_selection_mode_hint.get("recommended_selection_mode") or "") == "explore_default_plan"
            ):
                selected_meta_strategy_keys.append(str(selected_selection_mode_hint.get("strategy_key") or ""))
            return self._default_skill_plan(context), matched_hints, [], {
                "selection_mode": "explore_default_plan",
                "top_strategy_key": str(top_hint.get("strategy_key") or ""),
                "exploration_rate": exploration_rate,
                "adjusted_exploration_rate": adjusted_exploration_rate,
                "decision_roll": round(decision_roll, 4),
                "selection_mode_strategy_keys": selected_meta_strategy_keys,
                "selection_mode_hints": selection_mode_hints,
            }

        if (
            selected_selection_mode_hint
            and str(selected_selection_mode_hint.get("recommended_selection_mode") or "") == "exploit_top_combo"
        ):
            selected_meta_strategy_keys.append(str(selected_selection_mode_hint.get("strategy_key") or ""))
        return (
            self._build_plan_from_chain(context, recommended_chain),
            matched_hints,
            [str(top_hint.get("strategy_key") or "")] + selected_meta_strategy_keys,
            {
                "selection_mode": "exploit_top_combo",
                "selected_strategy_key": str(top_hint.get("strategy_key") or ""),
                "exploration_rate": exploration_rate,
                "adjusted_exploration_rate": adjusted_exploration_rate,
                "decision_roll": round(decision_roll, 4),
                "selection_mode_strategy_keys": selected_meta_strategy_keys,
                "selection_mode_hints": selection_mode_hints,
            },
        )

    async def run(self, *, chat_id: str, query: str, observation: dict[str, Any] | None = None) -> dict[str, Any]:
        context = self._load_chat_context(chat_id, query, observation)
        selected_skills, matched_hints, selected_strategy_keys, plan_meta = self._resolve_skill_plan(context)
        if selected_strategy_keys:
            experience_store.record_strategy_hits(context.chat_id, selected_strategy_keys)
        executed_skills: list[str] = []
        outputs: list[dict[str, Any]] = []
        aggregated_tools: list[str] = []
        aggregated_tool_results: list[dict[str, Any]] = []

        for skill in selected_skills:
            result = await skill.run(context)
            outputs.append(result)
            context.skill_outputs.append(result)
            executed_skills.append(skill.name)

            if result.get("used_tools"):
                aggregated_tools.extend([str(item) for item in result.get("used_tools", [])])
            if result.get("tool_results"):
                aggregated_tool_results.extend(result.get("tool_results", []))

        return {
            "chat_id": chat_id,
            "query": query,
            "selected_skill_plan": [skill.name for skill in selected_skills],
            "executed_skills": executed_skills,
            "matched_skill_strategy_keys": selected_strategy_keys,
            "candidate_skill_strategy_keys": [str(item.get("strategy_key") or "") for item in matched_hints],
            "matched_skill_hints": matched_hints,
            "skill_plan_meta": plan_meta,
            "memory_result": context.memory_result,
            "used_tools": aggregated_tools,
            "tool_results": aggregated_tool_results,
            "skill_results": outputs,
            "chat_history_preview": context.chat_history[:500],
        }

    async def run_web_search(
        self,
        *,
        chat_id: str,
        query: str,
        observation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = self._load_chat_context(chat_id, query, observation)
        result = await self.web_search_skill.run(context)
        return {
            "chat_id": chat_id,
            "query": query,
            "executed_skills": [self.web_search_skill.name],
            "used_tools": result.get("used_tools", []),
            "search_result": result.get("content", ""),
            "skill_results": [result],
            "observation": observation if isinstance(observation, dict) else {},
            "chat_history_preview": context.chat_history[:500],
        }


skill_orchestrator = SkillOrchestrator()

