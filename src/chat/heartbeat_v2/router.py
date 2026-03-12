"""
心跳系统 V2 动作路由。

根据 action_type 分发到对应适配器执行，
P1 支持 reply、no_op。
"""
from __future__ import annotations

import json
import time
from typing import Any

from src.common.database.database_model import ChatHistory
from src.common.logger import get_logger
from src.plugin_system.apis import send_api
from .skill_orchestrator import skill_orchestrator


logger = get_logger("heartbeat_v2.router")


class ActionRouter:
    """动作路由：route(action_type, action_args) -> (ok, reason, outputs)。"""

    """动作路由：route(action_type, action_args) -> (ok, reason, outputs)。"""

    async def route(self, action_type: str, action_args: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        """执行动作，返回 (成功与否, 原因, 输出)。"""

        """根据 action_type 执行对应动作，返回 (成功, 原因, 输出)。"""

        started_at = time.time()
        try:
            if action_type == "reply":
                chat_id = str(action_args.get("chat_id", ""))
                text = str(action_args.get("text", "")).strip()
                if not chat_id:
                    return False, "missing_chat_id", {}
                if not text:
                    return False, "empty_reply_text", {}
                ok = await send_api.text_to_stream(text=text, stream_id=chat_id, typing=False)
                return ok, "sent" if ok else "send_failed", {"chat_id": chat_id, "text": text}

            if action_type == "no_op":
                return True, "noop", {"detail": action_args or {}}

            if action_type == "find_memory":
                chat_id = str(action_args.get("chat_id", ""))
                query = str(action_args.get("query", "")).strip()
                if not chat_id:
                    return False, "missing_chat_id", {}
                if not query:
                    return False, "empty_query", {}

                matches = (
                    ChatHistory.select(
                        ChatHistory.id,
                        ChatHistory.theme,
                        ChatHistory.summary,
                        ChatHistory.keywords,
                        ChatHistory.end_time,
                    )
                    .where(
                        (ChatHistory.chat_id == chat_id)
                        & (
                            ChatHistory.theme.contains(query)
                            | ChatHistory.summary.contains(query)
                            | ChatHistory.keywords.contains(query)
                        )
                    )
                    .order_by(ChatHistory.end_time.desc())
                    .limit(3)
                )
                memories: list[dict[str, Any]] = []
                for item in matches:
                    memories.append(
                        {
                            "memory_id": item.id,
                            "theme": item.theme,
                            "summary": (item.summary or "")[:200],
                            "keywords": json.loads(item.keywords) if item.keywords else [],
                            "end_time": item.end_time,
                        }
                    )
                return True, "memory_found" if memories else "memory_not_found", {
                    "chat_id": chat_id,
                    "query": query,
                    "memories": memories,
                }

            if action_type == "tool_call":
                chat_id = str(action_args.get("chat_id", ""))
                query = str(action_args.get("query", "")).strip()
                observation = action_args.get("observation") or {}
                if not chat_id:
                    return False, "missing_chat_id", {}
                if not query:
                    return False, "empty_tool_query", {}
                orchestration_result = await skill_orchestrator.run(
                    chat_id=chat_id,
                    query=query,
                    observation=observation if isinstance(observation, dict) else {},
                )
                used_tools = orchestration_result.get("used_tools", [])
                has_skill_output = bool(orchestration_result.get("skill_results"))
                return True, "skill_executed" if has_skill_output else "skill_skipped", orchestration_result

            if action_type == "search_web":
                chat_id = str(action_args.get("chat_id", ""))
                source_chat_id = str(action_args.get("source_chat_id", "")).strip() or chat_id
                query = str(action_args.get("query", "")).strip()
                observation = action_args.get("observation") or {}
                share_target_chat_id = str(action_args.get("share_target_chat_id", "")).strip() or chat_id
                if not chat_id:
                    return False, "missing_chat_id", {}
                if not query:
                    return False, "empty_search_query", {}
                search_result = await skill_orchestrator.run_web_search(
                    chat_id=chat_id,
                    query=query,
                    observation=observation if isinstance(observation, dict) else {},
                )
                search_result["source_chat_id"] = source_chat_id
                search_result["share_target_chat_id"] = share_target_chat_id
                search_result["explore_reason"] = str(action_args.get("explore_reason", "")).strip()
                search_result["goal_candidate"] = action_args.get("goal_candidate", {})
                search_result["selector_reason"] = str(action_args.get("selector_reason", "")).strip()
                search_result["selector_score"] = float(action_args.get("selector_score", 0.0) or 0.0)
                search_result["selector_mode"] = str(action_args.get("selector_mode", "")).strip()
                search_result["selector_candidates_preview"] = action_args.get("selector_candidates_preview", [])
                skill_results = search_result.get("skill_results", [])
                first_result = skill_results[0] if isinstance(skill_results, list) and skill_results else {}
                skill_status = str(first_result.get("status") or "").strip() if isinstance(first_result, dict) else ""
                if skill_status == "failed":
                    return False, "search_failed", search_result
                return True, "search_completed", search_result

            return False, f"unsupported_action:{action_type}", {}
        except Exception as e:  # noqa: BLE001
            logger.error(f"route failed for action={action_type}: {e}")
            return False, "exception", {"error": str(e), "elapsed_ms": int((time.time() - started_at) * 1000)}
