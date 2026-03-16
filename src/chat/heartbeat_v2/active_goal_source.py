"""
心跳系统 V2 主动目标源。

把事件记忆、未解问题、关系记忆和失败经验统一成可调度的主动目标候选。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any


def normalize_goal_query(text: str) -> str:
    raw = str(text or "").strip().lower()
    if not raw:
        return ""
    normalized = re.sub(r"\s+", " ", raw)
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized[:120]


def build_goal_signature(chat_id: str, query: str) -> str:
    normalized_query = normalize_goal_query(query)
    normalized_chat_id = str(chat_id or "").strip()
    if not normalized_chat_id or not normalized_query:
        return ""
    return f"{normalized_chat_id}::{normalized_query}"


@dataclass
class ActiveGoalCandidate:
    goal_id: str
    goal_type: str
    chat_id: str
    query: str
    reason: str
    score: float
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ActiveGoalSource:
    """基于当前 state 汇总主动探索候选目标。"""

    def _append_candidate(self, items: list[ActiveGoalCandidate], candidate: ActiveGoalCandidate) -> None:
        if not candidate.chat_id or not candidate.query.strip():
            return
        candidate.metadata.setdefault("goal_signature", build_goal_signature(candidate.chat_id, candidate.query))
        items.append(candidate)

    def _from_unresolved_questions(self, state: dict[str, Any]) -> list[ActiveGoalCandidate]:
        memory_state = state.get("memory", {})
        unresolved = memory_state.get("unresolved_questions", [])
        if not isinstance(unresolved, list):
            return []

        items: list[ActiveGoalCandidate] = []
        for index, item in enumerate(unresolved[:4]):
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or "").strip()
            chat_id = str(item.get("chat_id") or "").strip()
            if not question or not chat_id:
                continue
            self._append_candidate(
                items,
                ActiveGoalCandidate(
                    goal_id=f"unresolved-{chat_id}-{index}",
                    goal_type="unresolved_question",
                    chat_id=chat_id,
                    query=question[:120],
                    reason="回访未解决的问题，主动补充线索或搜索答案。",
                    score=0.74 - index * 0.04,
                    source="thinking_back",
                    metadata={"question": question[:200], "context": str(item.get("context") or "")[:160]},
                ),
            )
        return items

    def _from_recent_events(self, state: dict[str, Any]) -> list[ActiveGoalCandidate]:
        memory_state = state.get("memory", {})
        recent_events = memory_state.get("recent_events", [])
        if not isinstance(recent_events, list):
            return []

        items: list[ActiveGoalCandidate] = []
        for index, item in enumerate(recent_events[:4]):
            if not isinstance(item, dict):
                continue
            theme = str(item.get("theme") or "").strip()
            chat_id = str(item.get("chat_id") or "").strip()
            keywords = item.get("keywords", [])
            if not theme or not chat_id:
                continue
            keyword_text = ""
            if isinstance(keywords, list) and keywords:
                keyword_text = "、".join(str(keyword).strip() for keyword in keywords[:3] if str(keyword).strip())
            query = f"{theme} 最新进展"
            if keyword_text:
                query = f"{theme} {keyword_text} 最新进展"
            self._append_candidate(
                items,
                ActiveGoalCandidate(
                    goal_id=f"event-{chat_id}-{index}",
                    goal_type="recent_topic_followup",
                    chat_id=chat_id,
                    query=query[:120],
                    reason="回访近期高价值话题，补充最新信息。",
                    score=0.66 - index * 0.03,
                    source="chat_history",
                    metadata={"theme": theme, "summary": str(item.get("summary") or "")[:160]},
                ),
            )
        return items

    def _from_relation_memories(self, state: dict[str, Any]) -> list[ActiveGoalCandidate]:
        memory_state = state.get("memory", {})
        relation_memories = memory_state.get("relation_memories", [])
        chat_state = state.get("chat", {})
        chats = chat_state.get("candidates", [])
        if not isinstance(relation_memories, list) or not isinstance(chats, list):
            return []

        user_chat_index: dict[tuple[str, str], str] = {}
        for item in chats:
            if not isinstance(item, dict):
                continue
            platform = str(item.get("platform") or "").strip()
            user_id = str(item.get("user_id") or "").strip()
            chat_id = str(item.get("chat_id") or item.get("stream_id") or "").strip()
            if platform and user_id and chat_id:
                user_chat_index[(platform, user_id)] = chat_id

        items: list[ActiveGoalCandidate] = []
        for index, item in enumerate(relation_memories[:3]):
            if not isinstance(item, dict):
                continue
            platform = str(item.get("platform") or "").strip()
            user_id = str(item.get("user_id") or "").strip()
            chat_id = user_chat_index.get((platform, user_id), "")
            person_name = str(item.get("person_name") or item.get("nickname") or "").strip()
            memory_points = item.get("memory_points", [])
            if not chat_id or not person_name:
                continue
            query = f"{person_name} 最近情况"
            if isinstance(memory_points, list) and memory_points:
                first_point = str(memory_points[0] or "").strip()
                if first_point:
                    query = f"{person_name} {first_point[:24]} 最近情况"
            self._append_candidate(
                items,
                ActiveGoalCandidate(
                    goal_id=f"relation-{chat_id}-{index}",
                    goal_type="relationship_followup",
                    chat_id=chat_id,
                    query=query[:120],
                    reason="根据长期关系记忆主动延续人物线索。",
                    score=0.58 - index * 0.03,
                    source="person_info",
                    metadata={"person_name": person_name},
                ),
            )
        return items

    def _from_failed_actions(self, state: dict[str, Any]) -> list[ActiveGoalCandidate]:
        history_state = state.get("history", {})
        recent_actions = history_state.get("recent_receipt_actions", [])
        if not isinstance(recent_actions, list):
            return []

        items: list[ActiveGoalCandidate] = []
        for index, item in enumerate(reversed(recent_actions[-8:])):
            if not isinstance(item, dict):
                continue
            if str(item.get("status") or "") != "failed":
                continue
            chat_id = str(item.get("chat_id") or "").strip()
            query = str(item.get("query") or "").strip()
            action_type = str(item.get("action_type") or "").strip()
            if not chat_id or not query:
                continue
            self._append_candidate(
                items,
                ActiveGoalCandidate(
                    goal_id=f"failed-{chat_id}-{index}",
                    goal_type="retry_failed_action",
                    chat_id=chat_id,
                    query=query[:120],
                    reason=f"之前的 {action_type or '动作'} 失败，后续可低频重试。",
                    score=0.52 - index * 0.02,
                    source="receipt_history",
                    metadata={"action_type": action_type, "reason": str(item.get("reason") or "")[:80]},
                ),
            )
            if len(items) >= 3:
                break
        return items

    def collect_candidates(self, state: dict[str, Any]) -> list[ActiveGoalCandidate]:
        items: list[ActiveGoalCandidate] = []
        items.extend(self._from_unresolved_questions(state))
        items.extend(self._from_recent_events(state))
        items.extend(self._from_relation_memories(state))
        items.extend(self._from_failed_actions(state))
        history_state = state.get("history", {})
        resolved_goal_signatures = history_state.get("resolved_goal_signatures", [])
        resolved = {
            str(item).strip()
            for item in (resolved_goal_signatures if isinstance(resolved_goal_signatures, list) else [])
            if str(item).strip()
        }
        deduped: list[ActiveGoalCandidate] = []
        seen_signatures: set[str] = set()
        for item in items:
            goal_signature = str(item.metadata.get("goal_signature") or build_goal_signature(item.chat_id, item.query)).strip()
            if goal_signature and goal_signature in resolved:
                continue
            if goal_signature and goal_signature in seen_signatures:
                continue
            if goal_signature:
                seen_signatures.add(goal_signature)
            deduped.append(item)
        deduped.sort(key=lambda item: item.score, reverse=True)
        return deduped[:8]

    def pick_goal(self, state: dict[str, Any]) -> ActiveGoalCandidate | None:
        candidates = self.collect_candidates(state)
        return candidates[0] if candidates else None


active_goal_source = ActiveGoalSource()
