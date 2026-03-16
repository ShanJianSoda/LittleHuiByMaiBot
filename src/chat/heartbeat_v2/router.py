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
from .reply_generator_adapter import reply_generator_adapter
from .skill_orchestrator import skill_orchestrator


logger = get_logger("heartbeat_v2.router")


class ActionRouter:
    """动作路由：route(action_type, action_args) -> (ok, reason, outputs)。"""

    """动作路由：route(action_type, action_args) -> (ok, reason, outputs)。"""

    def _summarize_action(self, action_type: str, action_args: dict[str, Any]) -> str:
        chat_id = str(action_args.get("chat_id") or "").strip() or "-"
        query = str(action_args.get("query") or action_args.get("text") or "").strip()
        preview = query[:80] if query else ""
        return f"action={action_type} chat_id={chat_id}" + (f" preview={preview}" if preview else "")

    async def route(self, action_type: str, action_args: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        """执行动作，返回 (成功与否, 原因, 输出)。"""

        """根据 action_type 执行对应动作，返回 (成功, 原因, 输出)。"""

        started_at = time.time()
        try:
            logger.debug(f"[router] start {self._summarize_action(action_type, action_args)}")
            if action_type == "reply":
                chat_id = str(action_args.get("chat_id", ""))
                text = str(action_args.get("text", "")).strip()
                use_reply_generator = bool(action_args.get("use_reply_generator", False))
                if not chat_id:
                    return False, "missing_chat_id", {}
                # 呼唤类短句（小绘小绘/在吗等）+ 短 draft 时直接发 draft，避免 replyer 用整段上下文生成成段内容导致「没回复呼唤」
                observation = action_args.get("observation") or {}
                obs_text = (observation.get("text") or "").strip() if isinstance(observation, dict) else ""
                if use_reply_generator and text and len(text) <= 30 and obs_text and len(obs_text) <= 25:
                    if any(k in obs_text for k in ("小绘", "在吗", "在不在", "在么", "喂", "嘿")):
                        use_reply_generator = False
                        logger.debug(
                            "[router] reply: use draft for call-like observation (avoid generator overwrite)"
                        )
                if use_reply_generator:
                    ok, reason, outputs = await reply_generator_adapter.generate_and_send(action_args)
                    logger.debug(
                        f"[router] done {self._summarize_action(action_type, action_args)} "
                        f"ok={ok} reason={reason} elapsed_ms={int((time.time() - started_at) * 1000)}"
                    )
                    return ok, reason, outputs
                if not text:
                    return False, "empty_reply_text", {}
                ok = await send_api.text_to_stream(text=text, stream_id=chat_id, typing=False)
                logger.debug(
                    f"[router] done {self._summarize_action(action_type, action_args)} "
                    f"ok={ok} reason={'sent' if ok else 'send_failed'} elapsed_ms={int((time.time() - started_at) * 1000)}"
                )
                return ok, "sent" if ok else "send_failed", {"chat_id": chat_id, "text": text}

            if action_type == "no_op":
                logger.debug(
                    f"[router] done {self._summarize_action(action_type, action_args)} ok=True reason=noop "
                    f"elapsed_ms={int((time.time() - started_at) * 1000)}"
                )
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
                logger.debug(
                    f"[router] done {self._summarize_action(action_type, action_args)} ok=True "
                    f"reason={'memory_found' if memories else 'memory_not_found'} "
                    f"elapsed_ms={int((time.time() - started_at) * 1000)}"
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
                logger.debug(
                    f"[router] done {self._summarize_action(action_type, action_args)} ok=True "
                    f"reason={'skill_executed' if has_skill_output else 'skill_skipped'} "
                    f"used_tools={used_tools} elapsed_ms={int((time.time() - started_at) * 1000)}"
                )
                return True, "skill_executed" if has_skill_output else "skill_skipped", orchestration_result

            if action_type == "search_web":
                chat_id = str(action_args.get("chat_id", ""))
                source_chat_id = str(action_args.get("source_chat_id", "")).strip() or chat_id
                query = str(action_args.get("query", "")).strip()
                observation = action_args.get("observation") or {}
                share_target_chat_id = str(action_args.get("share_target_chat_id", "")).strip() or chat_id
                if not chat_id:
                    logger.warning("[router] search_web missing_chat_id")
                    return False, "missing_chat_id", {}
                if not query:
                    logger.warning("[router] search_web empty_search_query chat_id=%s", chat_id)
                    return False, "empty_search_query", {}
                logger.info(
                    "[router] search_web start chat_id=%s query_preview=%s",
                    chat_id,
                    query[:80] if query else "",
                )
                search_result = await skill_orchestrator.run_web_search(
                    chat_id=chat_id,
                    query=query,
                    observation=observation if isinstance(observation, dict) else {},
                )
                elapsed_ms = int((time.time() - started_at) * 1000)
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
                skill_reason = str(first_result.get("reason") or "").strip() if isinstance(first_result, dict) else ""
                if skill_status == "failed":
                    logger.info(
                        "[router] search_web done ok=False reason=search_failed chat_id=%s elapsed_ms=%d skill_reason=%s",
                        chat_id,
                        elapsed_ms,
                        skill_reason or "-",
                    )
                    return False, "search_failed", search_result
                logger.info(
                    "[router] search_web done ok=True reason=search_completed chat_id=%s elapsed_ms=%d",
                    chat_id,
                    elapsed_ms,
                )
                return True, "search_completed", search_result

            return False, f"unsupported_action:{action_type}", {}
        except Exception as e:  # noqa: BLE001
            logger.error(f"route failed for {self._summarize_action(action_type, action_args)}: {e}", exc_info=True)
            return False, "exception", {"error": str(e), "elapsed_ms": int((time.time() - started_at) * 1000)}
