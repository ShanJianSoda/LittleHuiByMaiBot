"""
心跳系统 V2 动作路由。

根据 action_type 将 Plan 分发到对应适配器执行，
当前支持 reply、no_op。
"""
"""
心跳系统 V2 动作路由。

根据 action_type 分发到对应适配器执行，
P1 支持 reply、no_op。
"""
from __future__ import annotations

import time
from typing import Any

from src.common.logger import get_logger
from src.plugin_system.apis import send_api


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

            return False, f"unsupported_action:{action_type}", {}
        except Exception as e:  # noqa: BLE001
            logger.error(f"route failed for action={action_type}: {e}")
            return False, "exception", {"error": str(e), "elapsed_ms": int((time.time() - started_at) * 1000)}
