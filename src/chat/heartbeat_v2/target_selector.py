"""
心跳系统 V2 目标聊天流候选聚合与选择。

把 ChatStreams / Messages / PersonInfo / runtime ChatStream
统一为可打分的 TargetChatCandidate，供 planner 选择主动分享目标。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
import time
from typing import Any, Callable

from src.common.database.database_model import ChatStreams, Messages, PersonInfo
from src.common.logger import get_logger
from .structured_output import parse_structured_json


logger = get_logger("heartbeat_v2.target_selector")

RelationWeightProvider = Callable[[dict[str, Any], "TargetChatCandidate", str], tuple[float, str] | float | None]
_relation_weight_provider: RelationWeightProvider | None = None


def set_relation_weight_provider(provider: RelationWeightProvider | None) -> None:
    """为未来关系系统注册可插拔权重提供器。"""

    global _relation_weight_provider
    _relation_weight_provider = provider


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def _extract_tokens(text: str) -> list[str]:
    if not text:
        return []
    tokens = re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z][A-Za-z0-9_]{1,24}", text)
    seen: set[str] = set()
    ordered: list[str] = []
    for token in tokens:
        key = _normalize_text(token)
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(token.strip())
        if len(ordered) >= 10:
            break
    return ordered


@dataclass
class TargetChatCandidate:
    stream_id: str
    platform: str
    chat_type: str
    group_id: str = ""
    group_name: str = ""
    user_id: str = ""
    user_nickname: str = ""
    person_id: str = ""
    person_name: str = ""
    aliases: list[str] = field(default_factory=list)
    last_active_time: float = 0.0
    last_message_time: float = 0.0
    last_message_text: str = ""
    recent_message_count: int = 0
    relation_memory_count: int = 0
    relation_last_updated: float = 0.0
    source: str = "chat_streams"

    def display_name(self) -> str:
        if self.chat_type == "group":
            return self.group_name or self.group_id or self.stream_id
        return self.person_name or self.user_nickname or self.user_id or self.stream_id

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["chat_id"] = self.stream_id
        payload["is_group"] = self.chat_type == "group"
        payload["silent_for_s"] = max(0, int(time.time() - float(self.last_message_time or self.last_active_time or time.time())))
        payload["display_name"] = self.display_name()
        payload["aliases"] = self.aliases[:10]
        return payload


@dataclass
class TargetChatSelection:
    source_chat_id: str
    share_target_chat_id: str
    selector_reason: str
    selector_score: float
    selection_mode: str
    candidates_preview: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_chat_id": self.source_chat_id,
            "share_target_chat_id": self.share_target_chat_id,
            "selector_reason": self.selector_reason,
            "selector_score": round(self.selector_score, 4),
            "selection_mode": self.selection_mode,
            "selector_candidates_preview": self.candidates_preview,
        }


class TargetChatCandidateBuilder:
    """构建统一的聊天流候选列表。"""

    def __init__(self, *, max_candidates: int = 40, message_scan_limit: int = 240) -> None:
        self.max_candidates = max_candidates
        self.message_scan_limit = message_scan_limit

    def _safe_json_loads(self, raw: Any, default: Any) -> Any:
        return parse_structured_json(raw, default)

    def _append_alias(self, aliases: list[str], value: str) -> None:
        cleaned = str(value or "").strip()
        if not cleaned:
            return
        if cleaned not in aliases:
            aliases.append(cleaned)

    def _build_base_candidate(self, item: Any) -> TargetChatCandidate:
        group_id = str(getattr(item, "group_id", "") or "").strip()
        return TargetChatCandidate(
            stream_id=str(getattr(item, "stream_id", "") or "").strip(),
            platform=str(getattr(item, "platform", "") or "").strip(),
            chat_type="group" if group_id else "private",
            group_id=group_id,
            group_name=str(getattr(item, "group_name", "") or "").strip(),
            user_id=str(getattr(item, "user_id", "") or "").strip(),
            user_nickname=str(getattr(item, "user_nickname", "") or "").strip(),
            last_active_time=float(getattr(item, "last_active_time", 0.0) or 0.0),
            source="chat_streams",
        )

    def _overlay_runtime_streams(self, candidates: dict[str, TargetChatCandidate], runtime_streams: dict[str, Any]) -> None:
        for stream_id, stream in runtime_streams.items():
            if not stream_id:
                continue
            group_info = getattr(stream, "group_info", None)
            user_info = getattr(stream, "user_info", None)
            candidate = candidates.get(stream_id)
            if candidate is None:
                candidate = TargetChatCandidate(
                    stream_id=stream_id,
                    platform=str(getattr(stream, "platform", "") or "").strip(),
                    chat_type="group" if group_info else "private",
                    source="runtime",
                )
                candidates[stream_id] = candidate

            candidate.platform = str(getattr(stream, "platform", candidate.platform) or candidate.platform).strip()
            candidate.last_active_time = max(
                float(getattr(stream, "last_active_time", 0.0) or 0.0),
                candidate.last_active_time,
            )
            if group_info:
                candidate.chat_type = "group"
                candidate.group_id = str(getattr(group_info, "group_id", candidate.group_id) or candidate.group_id).strip()
                candidate.group_name = str(getattr(group_info, "group_name", candidate.group_name) or candidate.group_name).strip()
            if user_info:
                candidate.user_id = str(getattr(user_info, "user_id", candidate.user_id) or candidate.user_id).strip()
                candidate.user_nickname = str(
                    getattr(user_info, "user_nickname", candidate.user_nickname) or candidate.user_nickname
                ).strip()
            candidate.source = "merged"

    def _overlay_message_history(self, candidates: dict[str, TargetChatCandidate]) -> None:
        if not candidates:
            return
        chat_ids = list(candidates.keys())
        try:
            rows = (
                Messages.select(
                    Messages.chat_id,
                    Messages.time,
                    Messages.processed_plain_text,
                    Messages.display_message,
                    Messages.chat_info_group_name,
                    Messages.chat_info_group_id,
                    Messages.chat_info_user_nickname,
                    Messages.user_nickname,
                    Messages.chat_info_user_id,
                )
                .where(Messages.chat_id.in_(chat_ids))
                .order_by(Messages.time.desc())
                .limit(self.message_scan_limit)
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"load selector messages failed: {e}")
            return

        latest_seen: set[str] = set()
        for row in rows:
            chat_id = str(getattr(row, "chat_id", "") or "").strip()
            candidate = candidates.get(chat_id)
            if candidate is None:
                continue
            text = (
                str(getattr(row, "processed_plain_text", "") or "").strip()
                or str(getattr(row, "display_message", "") or "").strip()
            )
            if chat_id not in latest_seen:
                candidate.last_message_time = float(getattr(row, "time", 0.0) or 0.0)
                candidate.last_message_text = text[:200]
                latest_seen.add(chat_id)
            candidate.recent_message_count += 1
            self._append_alias(candidate.aliases, str(getattr(row, "chat_info_group_name", "") or ""))
            self._append_alias(candidate.aliases, str(getattr(row, "chat_info_user_nickname", "") or ""))
            self._append_alias(candidate.aliases, str(getattr(row, "user_nickname", "") or ""))
            group_id = str(getattr(row, "chat_info_group_id", "") or "").strip()
            if group_id and not candidate.group_id:
                candidate.group_id = group_id
                candidate.chat_type = "group"
            user_id = str(getattr(row, "chat_info_user_id", "") or "").strip()
            if user_id and not candidate.user_id:
                candidate.user_id = user_id

    def _overlay_person_info(self, candidates: dict[str, TargetChatCandidate]) -> None:
        user_ids = {item.user_id for item in candidates.values() if item.user_id}
        platforms = {item.platform for item in candidates.values() if item.platform}
        if not user_ids or not platforms:
            return

        try:
            rows = (
                PersonInfo.select(
                    PersonInfo.platform,
                    PersonInfo.user_id,
                    PersonInfo.person_id,
                    PersonInfo.person_name,
                    PersonInfo.nickname,
                    PersonInfo.group_nick_name,
                )
                .where((PersonInfo.user_id.in_(list(user_ids))) & (PersonInfo.platform.in_(list(platforms))))
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"load selector person info failed: {e}")
            return

        person_index: dict[tuple[str, str], Any] = {}
        for row in rows:
            person_index[(str(row.platform), str(row.user_id))] = row

        for candidate in candidates.values():
            person = person_index.get((candidate.platform, candidate.user_id))
            if person is None:
                continue
            candidate.person_id = str(getattr(person, "person_id", "") or "").strip()
            candidate.person_name = str(getattr(person, "person_name", "") or "").strip()
            candidate.relation_last_updated = float(getattr(person, "last_know", 0.0) or 0.0)
            self._append_alias(candidate.aliases, candidate.person_name)
            self._append_alias(candidate.aliases, str(getattr(person, "nickname", "") or ""))
            memory_points = self._safe_json_loads(getattr(person, "memory_points", None), [])
            if isinstance(memory_points, list):
                candidate.relation_memory_count = len(memory_points)
            else:
                raw_points = str(getattr(person, "memory_points", "") or "").strip()
                candidate.relation_memory_count = 1 if raw_points else 0
            group_nicknames = self._safe_json_loads(getattr(person, "group_nick_name", None), [])
            if isinstance(group_nicknames, list):
                for item in group_nicknames[:8]:
                    if not isinstance(item, dict):
                        continue
                    self._append_alias(candidate.aliases, str(item.get("group_nick_name") or ""))

    def _finalize_candidates(self, candidates: dict[str, TargetChatCandidate]) -> list[TargetChatCandidate]:
        for candidate in candidates.values():
            self._append_alias(candidate.aliases, candidate.group_name)
            self._append_alias(candidate.aliases, candidate.user_nickname)
            self._append_alias(candidate.aliases, candidate.person_name)
            if candidate.last_message_time <= 0:
                candidate.last_message_time = candidate.last_active_time
        items = list(candidates.values())
        items.sort(key=lambda x: (x.last_message_time, x.last_active_time), reverse=True)
        return items[: self.max_candidates]

    def build(self, runtime_streams: dict[str, Any] | None = None) -> list[TargetChatCandidate]:
        candidates: dict[str, TargetChatCandidate] = {}

        try:
            rows = (
                ChatStreams.select(
                    ChatStreams.stream_id,
                    ChatStreams.platform,
                    ChatStreams.group_id,
                    ChatStreams.group_name,
                    ChatStreams.user_id,
                    ChatStreams.user_nickname,
                    ChatStreams.last_active_time,
                )
                .order_by(ChatStreams.last_active_time.desc())
                .limit(self.max_candidates)
            )
            for row in rows:
                candidate = self._build_base_candidate(row)
                if candidate.stream_id:
                    candidates[candidate.stream_id] = candidate
        except Exception as e:  # noqa: BLE001
            logger.error(f"load selector chat streams failed: {e}")

        self._overlay_runtime_streams(candidates, runtime_streams or {})
        self._overlay_message_history(candidates)
        self._overlay_person_info(candidates)
        return self._finalize_candidates(candidates)


class TargetChatSelector:
    """选择主动搜索结果应该发回哪个聊天流。"""

    def _recent_search_penalty(self, state: dict[str, Any], chat_id: str) -> tuple[float, list[str]]:
        history_state = state.get("history", {})
        recent_actions = history_state.get("recent_receipt_actions", [])
        if not isinstance(recent_actions, list):
            return 0.0, []
        penalty = 0.0
        reasons: list[str] = []
        for index, item in enumerate(reversed(recent_actions)):
            if not isinstance(item, dict):
                continue
            action_chat_id = str(item.get("chat_id") or "").strip()
            share_chat_id = str(item.get("share_target_chat_id") or "").strip()
            if chat_id not in {action_chat_id, share_chat_id}:
                continue
            if str(item.get("action_type") or "") != "search_web":
                continue
            decay = max(0.25, 1.0 - index * 0.18)
            penalty += 0.12 * decay
            if index == 0:
                reasons.append("immediate_search_cooldown")
            else:
                reasons.append("recent_search_penalty")
            if penalty >= 0.3:
                break
        return min(0.36, penalty), reasons

    def _score_target_name_match(
        self,
        candidate: TargetChatCandidate,
        normalized_query: str,
        query_tokens: list[str],
    ) -> tuple[float, list[str]]:
        score = 0.0
        reasons: list[str] = []
        names = [
            candidate.group_name,
            candidate.person_name,
            candidate.user_nickname,
            candidate.group_id,
            candidate.user_id,
        ]
        for raw_name in names:
            normalized_name = _normalize_text(raw_name)
            if not normalized_name:
                continue
            if normalized_name == normalized_query:
                score += 0.32
                reasons.append("exact_target_name")
                continue
            if normalized_name in normalized_query:
                score += 0.22
                reasons.append("target_name_in_query")
                continue
            if any(_normalize_text(token) == normalized_name for token in query_tokens):
                score += 0.18
                reasons.append("exact_token_match")
        return min(0.42, score), reasons

    def _score_alias_match(
        self,
        candidate: TargetChatCandidate,
        normalized_query: str,
        query_tokens: list[str],
    ) -> tuple[float, list[str]]:
        score = 0.0
        exact_hits = 0
        fuzzy_hits = 0
        for alias in candidate.aliases:
            normalized_alias = _normalize_text(alias)
            if not normalized_alias:
                continue
            if normalized_alias == normalized_query or any(_normalize_text(token) == normalized_alias for token in query_tokens):
                exact_hits += 1
                continue
            if normalized_alias in normalized_query or normalized_query in normalized_alias:
                fuzzy_hits += 1
                continue
            if any(_normalize_text(token) in normalized_alias for token in query_tokens):
                fuzzy_hits += 1
        reasons: list[str] = []
        if exact_hits:
            score += min(0.3, exact_hits * 0.12)
            reasons.append(f"exact_alias_hit:{exact_hits}")
        if fuzzy_hits:
            score += min(0.18, fuzzy_hits * 0.05)
            reasons.append(f"fuzzy_alias_hit:{fuzzy_hits}")
        return score, reasons

    def _score_relation_weight(
        self,
        candidate: TargetChatCandidate,
        *,
        state: dict[str, Any],
        source_chat_id: str,
    ) -> tuple[float, list[str]]:
        score = 0.0
        reasons: list[str] = []

        if candidate.relation_memory_count > 0:
            score += min(0.18, candidate.relation_memory_count * 0.04)
            reasons.append("relation_memory")

        if candidate.relation_last_updated > 0:
            age = max(0.0, time.time() - candidate.relation_last_updated)
            if age <= 7 * 86400:
                score += 0.08
                reasons.append("recent_relation")
            elif age <= 30 * 86400:
                score += 0.04
                reasons.append("known_relation")

        if _relation_weight_provider is not None:
            try:
                provided = _relation_weight_provider(state, candidate, source_chat_id)
                if isinstance(provided, tuple):
                    provider_weight, provider_reason = provided
                else:
                    provider_weight, provider_reason = provided, ""
                provider_weight = float(provider_weight or 0.0)
                if provider_weight > 0:
                    score += min(0.24, provider_weight)
                    if provider_reason:
                        reasons.append(provider_reason[:40])
                    else:
                        reasons.append("relation_hook")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"relation weight provider failed: {e}")

        return score, reasons

    def _score_chat_frequency(self, candidate: TargetChatCandidate) -> tuple[float, list[str]]:
        score = 0.0
        reasons: list[str] = []
        message_count = max(0, int(candidate.recent_message_count))
        if message_count >= 8:
            score += 0.12
            reasons.append("high_chat_frequency")
        elif message_count >= 3:
            score += 0.06
            reasons.append("medium_chat_frequency")
        return score, reasons

    def _score_intent_context(self, candidate: TargetChatCandidate, query: str) -> tuple[float, list[str]]:
        score = 0.0
        reasons: list[str] = []
        if candidate.chat_type == "group" and any(token in query for token in ("群", "大家", "群里", "这群")):
            score += 0.08
            reasons.append("group_context")
        if candidate.chat_type == "private" and any(token in query for token in ("他", "她", "这个人", "那个人", "私聊")):
            score += 0.05
            reasons.append("person_context")
        return score, reasons

    def _score_candidate(
        self,
        candidate: TargetChatCandidate,
        *,
        source_chat_id: str,
        query: str,
        state: dict[str, Any],
    ) -> tuple[float, list[str]]:
        score = 0.0
        reasons: list[str] = []
        query_tokens = _extract_tokens(query)
        normalized_query = _normalize_text(query)

        if candidate.stream_id == source_chat_id:
            score += 0.36
            reasons.append("same_source_chat")

        target_name_score, target_name_reasons = self._score_target_name_match(candidate, normalized_query, query_tokens)
        score += target_name_score
        reasons.extend(target_name_reasons)

        alias_score, alias_reasons = self._score_alias_match(candidate, normalized_query, query_tokens)
        score += alias_score
        reasons.extend(alias_reasons)

        if candidate.last_message_text and query_tokens:
            normalized_last = _normalize_text(candidate.last_message_text)
            overlap = sum(1 for token in query_tokens if _normalize_text(token) in normalized_last)
            if overlap:
                score += min(0.16, overlap * 0.05)
                reasons.append(f"message_overlap:{overlap}")

        now = time.time()
        latest_ts = float(candidate.last_message_time or candidate.last_active_time or 0.0)
        if latest_ts:
            age = max(0.0, now - latest_ts)
            if age <= 3600:
                score += 0.12
                reasons.append("recently_active")
            elif age <= 86400:
                score += 0.06
                reasons.append("active_today")

        frequency_score, frequency_reasons = self._score_chat_frequency(candidate)
        score += frequency_score
        reasons.extend(frequency_reasons)

        relation_score, relation_reasons = self._score_relation_weight(
            candidate,
            state=state,
            source_chat_id=source_chat_id,
        )
        score += relation_score
        reasons.extend(relation_reasons)

        intent_score, intent_reasons = self._score_intent_context(candidate, query)
        score += intent_score
        reasons.extend(intent_reasons)

        penalty, penalty_reasons = self._recent_search_penalty(state, candidate.stream_id)
        if penalty > 0:
            score -= penalty
            reasons.extend(penalty_reasons)

        return score, reasons

    def _build_preview(
        self,
        ranked: list[tuple[TargetChatCandidate, float, list[str]]],
    ) -> list[dict[str, Any]]:
        preview: list[dict[str, Any]] = []
        for candidate, score, reasons in ranked[:3]:
            preview.append(
                {
                    "chat_id": candidate.stream_id,
                    "display_name": candidate.display_name(),
                    "chat_type": candidate.chat_type,
                    "score": round(score, 4),
                    "reasons": reasons[:4],
                }
            )
        return preview

    def select_share_target(
        self,
        *,
        source_chat_id: str,
        query: str,
        state: dict[str, Any],
    ) -> TargetChatSelection:
        chat_state = state.get("chat", {})
        raw_candidates = chat_state.get("candidates", [])
        candidates: list[TargetChatCandidate] = []
        for item in raw_candidates:
            if not isinstance(item, dict):
                continue
            try:
                candidate = TargetChatCandidate(
                    stream_id=str(item.get("stream_id") or item.get("chat_id") or "").strip(),
                    platform=str(item.get("platform") or "").strip(),
                    chat_type="group" if bool(item.get("is_group")) else str(item.get("chat_type") or "private"),
                    group_id=str(item.get("group_id") or "").strip(),
                    group_name=str(item.get("group_name") or "").strip(),
                    user_id=str(item.get("user_id") or "").strip(),
                    user_nickname=str(item.get("user_nickname") or "").strip(),
                    person_id=str(item.get("person_id") or "").strip(),
                    person_name=str(item.get("person_name") or "").strip(),
                    aliases=[str(alias).strip() for alias in item.get("aliases", []) if str(alias).strip()],
                    last_active_time=float(item.get("last_active_time") or 0.0),
                    last_message_time=float(item.get("last_message_time") or 0.0),
                    last_message_text=str(item.get("last_message_text") or "").strip(),
                    recent_message_count=int(item.get("recent_message_count") or 0),
                    relation_memory_count=int(item.get("relation_memory_count") or 0),
                    relation_last_updated=float(item.get("relation_last_updated") or 0.0),
                    source=str(item.get("source") or "state"),
                )
            except Exception:  # noqa: BLE001
                continue
            if candidate.stream_id:
                candidates.append(candidate)

        if not candidates:
            return TargetChatSelection(
                source_chat_id=source_chat_id,
                share_target_chat_id=source_chat_id,
                selector_reason="fallback_source_chat_no_candidates",
                selector_score=0.0,
                selection_mode="fallback",
                candidates_preview=[],
            )

        ranked: list[tuple[TargetChatCandidate, float, list[str]]] = []
        for candidate in candidates:
            score, reasons = self._score_candidate(candidate, source_chat_id=source_chat_id, query=query, state=state)
            ranked.append((candidate, score, reasons))
        ranked.sort(key=lambda item: (item[1], item[0].last_message_time, item[0].last_active_time), reverse=True)

        top_candidate, top_score, top_reasons = ranked[0]
        if top_score < 0.18 and source_chat_id:
            return TargetChatSelection(
                source_chat_id=source_chat_id,
                share_target_chat_id=source_chat_id,
                selector_reason="fallback_source_chat_low_confidence",
                selector_score=top_score,
                selection_mode="fallback",
                candidates_preview=self._build_preview(ranked),
            )

        return TargetChatSelection(
            source_chat_id=source_chat_id,
            share_target_chat_id=top_candidate.stream_id,
            selector_reason="|".join(top_reasons[:4]) or "selector_top_match",
            selector_score=top_score,
            selection_mode="selector_top_match",
            candidates_preview=self._build_preview(ranked),
        )


target_chat_candidate_builder = TargetChatCandidateBuilder()
target_chat_selector = TargetChatSelector()
