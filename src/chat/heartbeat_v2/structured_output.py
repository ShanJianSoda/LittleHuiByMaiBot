"""
heartbeat_v2 结构化输出辅助工具。

统一处理：
- 模型返回的 fenced JSON / 普通 JSON
- 轻量 repair_json 容错
- 存量字符串字段的安全解析
"""
from __future__ import annotations

import json
import re
from typing import Any

from json_repair import repair_json


def strip_json_code_fence(raw: str) -> str:
    text = str(raw or "").strip()
    if not text.startswith("```"):
        return text
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else text


def parse_structured_json(raw: Any, default: Any) -> Any:
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw

    text = strip_json_code_fence(str(raw).strip())
    if not text:
        return default

    try:
        return json.loads(text)
    except Exception:
        pass

    try:
        repaired = repair_json(text)
        if isinstance(repaired, str):
            return json.loads(repaired)
        return repaired
    except Exception:
        return default
