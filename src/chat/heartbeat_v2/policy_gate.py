"""
心跳系统 V2 策略门控。

在执行前检查：活跃时段、reply 冷却、chat_id 等，
返回 (allow: bool, reason: str)。
"""
"""
心跳系统 V2 策略门控。

在出队执行前检查：活跃时段、reply 冷却、chat_id 等，
allow(plan, state) -> (bool, reason)。
"""
from __future__ import annotations

import time
from typing import Optional

from src.common.logger import get_logger

from .models import Plan


logger = get_logger("heartbeat_v2.policy_gate")


class PolicyGate:
    """策略门控：预算、冷却、时段、安全等检查。"""

    """策略门控：预算、冷却、时段、安全等检查。"""

    def __init__(self, *, min_reply_interval_seconds: int = 30, active_time_ranges: Optional[list[str]] = None):
        self.min_reply_interval_seconds = max(0, min_reply_interval_seconds)
        self.active_time_ranges = active_time_ranges or ["00:00-23:59"]
        self._last_reply_at: dict[str, float] = {}

    def allow(self, plan: Plan, state: dict) -> tuple[bool, str]:
        """判断 plan 是否允许执行，返回 (是否允许, 原因)。"""

        """判断 plan 是否允许执行，返回 (是否允许, 原因)。"""

        if not self._is_active_now():
            return False, "inactive_time_range"

        if plan.action_type == "reply":
            chat_id = str(plan.action_args.get("chat_id", ""))
            if not chat_id:
                return False, "missing_chat_id"
            last_at = self._last_reply_at.get(chat_id, 0)
            now = time.time()
            if now - last_at < self.min_reply_interval_seconds:
                return False, "reply_cooldown"
            self._last_reply_at[chat_id] = now
        return True, "allowed"

    def _is_active_now(self) -> bool:
        now = time.localtime()
        now_min = now.tm_hour * 60 + now.tm_min
        for item in self.active_time_ranges:
            parsed = self._parse_time_range(item)
            if not parsed:
                logger.warning(f"invalid active_time_ranges item: {item}")
                continue
            start_min, end_min = parsed
            if self._in_range(now_min, start_min, end_min):
                return True
        return False

    @staticmethod
    def _parse_time_range(value: str) -> Optional[tuple[int, int]]:
        try:
            start, end = [x.strip() for x in value.split("-")]
            sh, sm = [int(x) for x in start.split(":")]
            eh, em = [int(x) for x in end.split(":")]
            if sh < 0 or sh > 23 or eh < 0 or eh > 23 or sm < 0 or sm > 59 or em < 0 or em > 59:
                return None
            return sh * 60 + sm, eh * 60 + em
        except Exception:
            return None

    @staticmethod
    def _in_range(now_min: int, start_min: int, end_min: int) -> bool:
        if start_min <= end_min:
            return start_min <= now_min <= end_min
        return now_min >= start_min or now_min <= end_min
