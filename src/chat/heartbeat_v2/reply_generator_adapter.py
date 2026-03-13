"""
心跳系统 V2 reply 生成适配器。

把 heartbeat 的 reply intent 桥接到旧回复模块，
让 heartbeat 负责决策与调度，旧 replyer 负责高质量生成。
"""
from __future__ import annotations

import time
from typing import Any, Optional

from src.chat.message_receive.chat_stream import get_chat_manager
from src.common.data_models.database_data_model import DatabaseMessages
from src.common.logger import get_logger
from src.common.message_repository import find_messages
from src.plugin_system.apis import generator_api, send_api


logger = get_logger("heartbeat_v2.reply_generator_adapter")


class ReplyGeneratorAdapter:
    """将 reply action 适配到旧回复器生成链。"""

    def _resolve_anchor_message(
        self,
        chat_id: str,
        observation: dict[str, Any] | None = None,
    ) -> Optional[DatabaseMessages]:
        if not chat_id:
            return None

        message_id = ""
        if isinstance(observation, dict):
            message_id = str(observation.get("message_id") or "").strip()
        if message_id:
            matched = find_messages(
                message_filter={"chat_id": chat_id, "message_id": message_id},
                limit=1,
                limit_mode="latest",
            )
            if matched:
                return matched[-1]

        latest = find_messages(
            message_filter={"chat_id": chat_id},
            limit=1,
            limit_mode="latest",
            filter_bot=True,
            filter_command=True,
        )
        if latest:
            return latest[-1]
        return None

    def _flatten_reply_set_preview(self, llm_response: Any) -> list[str]:
        reply_set = getattr(llm_response, "reply_set", None)
        reply_data = getattr(reply_set, "reply_data", None)
        if not isinstance(reply_data, list):
            return []
        previews: list[str] = []
        for item in reply_data:
            content = getattr(item, "content", None)
            if isinstance(content, str) and content.strip():
                previews.append(content.strip()[:200])
        return previews

    async def generate_and_send(self, action_args: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        chat_id = str(action_args.get("chat_id", "")).strip()
        if not chat_id:
            return False, "missing_chat_id", {}

        chat_stream = get_chat_manager().get_stream(chat_id)
        if not chat_stream:
            return False, "missing_chat_stream", {"chat_id": chat_id}

        observation = action_args.get("observation")
        if not isinstance(observation, dict):
            observation = {}
        reply_reason = str(action_args.get("reply_reason", "")).strip()
        fallback_text = str(action_args.get("text", "")).strip()
        extra_info = str(action_args.get("extra_info", "")).strip()
        think_level = int(action_args.get("think_level", 1) or 1)
        enable_tool = bool(action_args.get("enable_tool", False))
        unknown_words = action_args.get("unknown_words")
        if not isinstance(unknown_words, list):
            unknown_words = None

        anchor_message = self._resolve_anchor_message(chat_id, observation)
        success, llm_response = await generator_api.generate_reply(
            chat_stream=chat_stream,
            chat_id=chat_id,
            reply_message=anchor_message,
            think_level=max(0, min(2, think_level)),
            extra_info=extra_info,
            reply_reason=reply_reason,
            unknown_words=unknown_words,
            enable_tool=enable_tool,
            enable_splitter=True,
            enable_chinese_typo=True,
            request_type="heartbeat_v2_reply",
            from_plugin=False,
            reply_time_point=time.time(),
        )
        if success and llm_response and getattr(llm_response, "reply_set", None):
            send_ok = await send_api.custom_reply_set_to_stream(
                reply_set=llm_response.reply_set,
                stream_id=chat_id,
                typing=True,
                reply_message=anchor_message,
                set_reply=anchor_message is not None,
            )
            outputs = {
                "chat_id": chat_id,
                "reply_mode": "generator_adapter",
                "reply_reason": reply_reason,
                "anchor_message_id": getattr(anchor_message, "message_id", None),
                "generated_text": str(getattr(llm_response, "content", "") or ""),
                "processed_output": getattr(llm_response, "processed_output", None),
                "model": getattr(llm_response, "model", None),
                "reply_preview": self._flatten_reply_set_preview(llm_response),
            }
            return send_ok, "generated_and_sent" if send_ok else "generated_but_send_failed", outputs

        if fallback_text:
            send_ok = await send_api.text_to_stream(
                text=fallback_text,
                stream_id=chat_id,
                typing=False,
                set_reply=anchor_message is not None,
                reply_message=anchor_message,
            )
            outputs = {
                "chat_id": chat_id,
                "reply_mode": "fallback_text",
                "reply_reason": reply_reason,
                "anchor_message_id": getattr(anchor_message, "message_id", None),
                "text": fallback_text,
            }
            return send_ok, "fallback_sent" if send_ok else "fallback_send_failed", outputs

        return False, "reply_generation_failed", {"chat_id": chat_id, "reply_reason": reply_reason}


reply_generator_adapter = ReplyGeneratorAdapter()
