"""
knock 联系方式映射服务。

目标：
1. 为系统内部 chat_id 提供更可读的联系方式实体
2. 为 prompt 提供可读名称和别名
3. 为模型输出提供 chat 引用解析能力
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from src.common.database.database_model import ChatStreams, Knock
from src.common.logger import get_logger


logger = get_logger("common.knock")

CHAT_REFERENCE_KEYS = {
    "chat_id",
    "target_chat_id",
    "source_chat_id",
    "share_target_chat_id",
}


def _normalize_text(value: str) -> str:
    return "".join(str(value or "").strip().lower().split())


def _safe_json_loads(raw: Any, default: Any) -> Any:
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    text = str(raw).strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


@dataclass
class KnockContact:
    chat_id: str
    knock_id: str = ""
    platform: str = ""
    knock_type: str = ""
    user_id: str = ""
    group_id: str = ""
    display_name: str = ""
    remark_name: str = ""
    aliases: list[str] = field(default_factory=list)
    source: str = "manual"
    is_enabled: bool = True
    meta_json: dict[str, Any] = field(default_factory=dict)

    @property
    def primary_name(self) -> str:
        return self.remark_name or self.display_name or (self.aliases[0] if self.aliases else "") or self.knock_id or self.chat_id

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "chat_id": self.chat_id,
            "knock_id": self.knock_id,
            "name": self.primary_name,
            "display_name": self.display_name,
            "remark_name": self.remark_name,
            "aliases": self.aliases[:6],
            "platform": self.platform,
            "chat_type": self.knock_type,
            "user_id": self.user_id,
            "group_id": self.group_id,
        }


class KnockManager:
    """联系方式映射管理器。"""

    def get_contact_by_chat_id(self, chat_id: str) -> Optional[KnockContact]:
        chat_id = str(chat_id or "").strip()
        if not chat_id:
            return None

        item = Knock.get_or_none((Knock.chat_id == chat_id) & (Knock.is_enabled == True))  # noqa: E712
        if item:
            aliases = _safe_json_loads(item.aliases, [])
            if not isinstance(aliases, list):
                aliases = []
            meta_json = _safe_json_loads(item.meta_json, {})
            if not isinstance(meta_json, dict):
                meta_json = {}
            return KnockContact(
                chat_id=item.chat_id,
                knock_id=str(item.knock_id or ""),
                platform=str(item.platform or ""),
                knock_type=str(item.knock_type or ""),
                user_id=str(item.user_id or ""),
                group_id=str(item.group_id or ""),
                display_name=str(item.display_name or ""),
                remark_name=str(item.remark_name or ""),
                aliases=[str(alias).strip() for alias in aliases if str(alias).strip()],
                source=str(item.source or "manual"),
                is_enabled=bool(item.is_enabled),
                meta_json=meta_json,
            )

        stream = ChatStreams.get_or_none(ChatStreams.stream_id == chat_id)
        if not stream:
            return None
        knock_type = "group" if stream.group_id else "private"
        display_name = str(stream.group_name or stream.user_nickname or "")
        return KnockContact(
            chat_id=str(stream.stream_id or ""),
            knock_id="",
            platform=str(stream.platform or ""),
            knock_type=knock_type,
            user_id=str(stream.user_id or ""),
            group_id=str(stream.group_id or ""),
            display_name=display_name,
            remark_name="",
            aliases=[],
            source="chat_stream_fallback",
            is_enabled=True,
            meta_json={},
        )

    def list_contacts(self, *, enabled_only: bool = True, limit: int = 100) -> list[KnockContact]:
        query = Knock.select()
        if enabled_only:
            query = query.where(Knock.is_enabled == True)  # noqa: E712
        query = query.order_by(Knock.updated_at.desc()).limit(limit)
        contacts: list[KnockContact] = []
        for item in query:
            contact = self.get_contact_by_chat_id(str(item.chat_id or ""))
            if contact:
                contacts.append(contact)
        return contacts

    def resolve_chat_id(self, ref: str) -> str:
        raw = str(ref or "").strip()
        if not raw:
            return ""

        direct = self.get_contact_by_chat_id(raw)
        if direct:
            return direct.chat_id

        norm = _normalize_text(raw)
        if not norm:
            return ""

        exact_knock = Knock.get_or_none((Knock.knock_id == raw) & (Knock.is_enabled == True))  # noqa: E712
        if exact_knock:
            return str(exact_knock.chat_id or "")

        matched_chat_ids: list[str] = []
        for contact in self.list_contacts(enabled_only=True, limit=500):
            candidates = [
                contact.knock_id,
                contact.display_name,
                contact.remark_name,
                *contact.aliases,
            ]
            normalized_candidates = {_normalize_text(item) for item in candidates if str(item).strip()}
            if norm in normalized_candidates:
                matched_chat_ids.append(contact.chat_id)

        matched_chat_ids = list(dict.fromkeys(matched_chat_ids))
        if len(matched_chat_ids) == 1:
            return matched_chat_ids[0]
        if len(matched_chat_ids) > 1:
            logger.warning(f"knock 引用解析到多个 chat_id，ref={raw} matched={matched_chat_ids[:5]}")
            return ""
        return raw

    def get_display_name(self, chat_id: str) -> str:
        contact = self.get_contact_by_chat_id(chat_id)
        if not contact:
            return str(chat_id or "")
        return contact.primary_name

    def annotate_chat_ref(self, chat_id: str) -> dict[str, Any]:
        contact = self.get_contact_by_chat_id(chat_id)
        if not contact:
            return {
                "chat_id": str(chat_id or ""),
                "knock_id": "",
                "name": str(chat_id or ""),
                "chat_type": "",
            }
        return contact.to_prompt_dict()

    def build_prompt_contact_summary(self, *, chat_ids: list[str] | None = None, limit: int = 30) -> list[dict[str, Any]]:
        if chat_ids:
            contacts = []
            for chat_id in chat_ids[:limit]:
                contacts.append(self.annotate_chat_ref(chat_id))
            return contacts
        return [item.to_prompt_dict() for item in self.list_contacts(enabled_only=True, limit=limit)]

    def humanize_structure(self, value: Any) -> Any:
        if isinstance(value, list):
            return [self.humanize_structure(item) for item in value]
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, item in value.items():
                if key in CHAT_REFERENCE_KEYS and isinstance(item, str) and item.strip():
                    result[key] = self.annotate_chat_ref(item)
                    continue
                result[key] = self.humanize_structure(item)
            return result
        return value

    def resolve_structure_chat_refs(self, value: Any) -> Any:
        if isinstance(value, list):
            return [self.resolve_structure_chat_refs(item) for item in value]
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            target_ref = str(value.get("target_ref") or "").strip()
            for key, item in value.items():
                if key in CHAT_REFERENCE_KEYS and isinstance(item, str):
                    resolved = self.resolve_chat_id(item)
                    result[key] = resolved
                    continue
                result[key] = self.resolve_structure_chat_refs(item)
            if target_ref and not str(result.get("target_chat_id") or "").strip():
                result["target_chat_id"] = self.resolve_chat_id(target_ref)
            return result
        return value


knock_manager = KnockManager()
