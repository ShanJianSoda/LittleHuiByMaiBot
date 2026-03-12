"""
心跳系统 V2 经验存储。

使用本地 JSON/JSONL 持久化反思记录与蒸馏后的策略记忆，
避免在第一阶段引入新的数据库迁移成本。
"""

from __future__ import annotations

import json
import math
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from src.common.logger import get_logger

from .models import ReflectionRecord


logger = get_logger("heartbeat_v2.experience_store")

DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "heartbeat_v2"
REFLECTION_DIR = DATA_DIR / "reflections"
STRATEGY_DIR = DATA_DIR / "strategies"
STRATEGY_HALF_LIFE_SECONDS = 3 * 24 * 3600
SKILL_COMBO_FAILURE_COOLDOWN_SECONDS = 6 * 3600
SKILL_COMBO_MAX_ITEMS = 8
TOTAL_MAX_ITEMS = 16
SKILL_COMBO_MIN_EXPLORATION_RATE = 0.08
SKILL_COMBO_MAX_EXPLORATION_RATE = 0.4


def _sanitize_chat_id(chat_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", chat_id or "global")


class ExperienceStore:
    """负责反思记录与策略记忆的轻量持久化。"""

    def __init__(self) -> None:
        REFLECTION_DIR.mkdir(parents=True, exist_ok=True)
        STRATEGY_DIR.mkdir(parents=True, exist_ok=True)

    def _reflection_path(self, chat_id: str) -> Path:
        return REFLECTION_DIR / f"{_sanitize_chat_id(chat_id)}.jsonl"

    def _strategy_path(self, chat_id: str) -> Path:
        return STRATEGY_DIR / f"{_sanitize_chat_id(chat_id)}.json"

    def _compute_time_decay(self, last_active_at: float, now: float | None = None) -> float:
        current = now or time.time()
        ts = max(0.0, float(last_active_at or 0.0))
        if ts <= 0:
            return 0.25
        elapsed = max(0.0, current - ts)
        return round(math.exp(-elapsed / STRATEGY_HALF_LIFE_SECONDS), 4)

    def _compute_planner_weight(self, strategy: dict[str, Any], now: float | None = None) -> float:
        current = now or time.time()
        hit_count = max(0, int(strategy.get("hit_count", 0) or 0))
        success_rate = float(strategy.get("success_rate", 0.0) or 0.0)
        confidence = float(strategy.get("confidence", 0.0) or 0.0)
        avg_score = max(0.0, float(strategy.get("avg_score", 0.0) or 0.0))
        failure_penalty = max(0.0, float(strategy.get("failure_penalty", 0.0) or 0.0))
        last_active_at = float(
            strategy.get("last_hit_at")
            or strategy.get("updated_at")
            or 0.0
        )
        time_decay = self._compute_time_decay(last_active_at, now=current)
        hit_bonus = min(1.0, math.log1p(hit_count) / math.log(10))
        planner_weight = (
            confidence * 0.3
            + success_rate * 0.25
            + avg_score * 0.2
            + time_decay * 0.15
            + hit_bonus * 0.1
            - failure_penalty * 0.35
        )
        return round(max(0.0, min(1.0, planner_weight)), 4)

    def _compute_failure_penalty(self, strategy: dict[str, Any]) -> float:
        failure_count = max(0, int(strategy.get("failure_count", 0) or 0))
        success_rate = float(strategy.get("success_rate", 0.0) or 0.0)
        avg_score = float(strategy.get("avg_score", 0.0) or 0.0)
        strategy_scope = str(strategy.get("strategy_scope") or "action")

        penalty = 0.0
        if strategy_scope == "skill_combo":
            penalty += min(0.4, failure_count * 0.07)
            penalty += max(0.0, 0.45 - success_rate) * 0.6
            penalty += max(0.0, -avg_score) * 0.15
        else:
            penalty += min(0.2, failure_count * 0.03)
            penalty += max(0.0, 0.35 - success_rate) * 0.25
        return round(max(0.0, min(1.0, penalty)), 4)

    def _compute_exploration_rate(self, strategy: dict[str, Any]) -> float:
        strategy_scope = str(strategy.get("strategy_scope") or "action")
        if strategy_scope != "skill_combo":
            return 0.0

        hit_count = max(0, int(strategy.get("hit_count", 0) or 0))
        evidence_count = max(1, int(strategy.get("evidence_count", 0) or 0))
        success_rate = float(strategy.get("success_rate", 0.0) or 0.0)
        planner_weight = float(strategy.get("planner_weight", 0.0) or 0.0)
        failure_penalty = float(strategy.get("failure_penalty", 0.0) or 0.0)

        exploration = 0.28
        exploration += 0.1 if evidence_count <= 2 else 0.0
        exploration += 0.06 if hit_count == 0 else 0.0
        exploration -= min(0.16, hit_count * 0.025)
        exploration -= success_rate * 0.1
        exploration -= planner_weight * 0.12
        exploration += failure_penalty * 0.08

        return round(
            max(SKILL_COMBO_MIN_EXPLORATION_RATE, min(SKILL_COMBO_MAX_EXPLORATION_RATE, exploration)),
            4,
        )

    def _apply_skill_combo_guardrails(self, strategy: dict[str, Any], now: float | None = None) -> dict[str, Any]:
        current = now or time.time()
        normalized = dict(strategy)
        if str(normalized.get("strategy_scope") or "action") != "skill_combo":
            normalized["is_disabled"] = bool(normalized.get("is_disabled", False))
            return normalized

        failure_count = max(0, int(normalized.get("failure_count", 0) or 0))
        success_count = max(0, int(normalized.get("success_count", 0) or 0))
        hit_count = max(0, int(normalized.get("hit_count", 0) or 0))
        success_rate = float(normalized.get("success_rate", 0.0) or 0.0)
        planner_weight = float(normalized.get("planner_weight", 0.0) or 0.0)
        time_decay = float(normalized.get("time_decay", 0.0) or 0.0)
        disabled_until = float(normalized.get("disabled_until", 0.0) or 0.0)

        if failure_count >= 3 and success_rate < 0.35 and planner_weight < 0.45:
            disabled_until = max(disabled_until, current + SKILL_COMBO_FAILURE_COOLDOWN_SECONDS)

        if success_count >= failure_count and success_rate >= 0.55:
            disabled_until = 0.0

        normalized["disabled_until"] = disabled_until
        normalized["is_disabled"] = disabled_until > current
        normalized["should_prune"] = bool(
            (failure_count >= 5 and success_rate < 0.3 and planner_weight < 0.32)
            or (hit_count == 0 and time_decay < 0.08 and planner_weight < 0.18)
        )
        return normalized

    def _prune_strategies(self, strategies: list[dict[str, Any]], now: float | None = None) -> list[dict[str, Any]]:
        current = now or time.time()
        action_items: list[dict[str, Any]] = []
        skill_combo_items: list[dict[str, Any]] = []

        for item in strategies:
            normalized = self._normalize_strategy(item, now=current)
            normalized = self._apply_skill_combo_guardrails(normalized, now=current)
            if normalized.get("should_prune"):
                continue
            if str(normalized.get("strategy_scope") or "action") == "skill_combo":
                skill_combo_items.append(normalized)
            else:
                action_items.append(normalized)

        skill_combo_items.sort(
            key=lambda item: (
                0 if item.get("is_disabled") else 1,
                float(item.get("planner_weight", 0.0) or 0.0),
                float(item.get("success_rate", 0.0) or 0.0),
                int(item.get("hit_count", 0) or 0),
            ),
            reverse=True,
        )
        action_items.sort(
            key=lambda item: (
                float(item.get("planner_weight", 0.0) or 0.0),
                float(item.get("success_rate", 0.0) or 0.0),
                int(item.get("hit_count", 0) or 0),
            ),
            reverse=True,
        )

        skill_combo_items = skill_combo_items[:SKILL_COMBO_MAX_ITEMS]
        combined = action_items + skill_combo_items
        combined.sort(
            key=lambda item: (
                float(item.get("planner_weight", 0.0) or 0.0),
                float(item.get("success_rate", 0.0) or 0.0),
                int(item.get("hit_count", 0) or 0),
            ),
            reverse=True,
        )
        return combined[:TOTAL_MAX_ITEMS]

    def _normalize_strategy(self, strategy: dict[str, Any], now: float | None = None) -> dict[str, Any]:
        current = now or time.time()
        normalized = dict(strategy)
        normalized["strategy_scope"] = str(normalized.get("strategy_scope") or "action")
        normalized["hit_count"] = max(0, int(normalized.get("hit_count", 0) or 0))
        normalized["success_count"] = max(0, int(normalized.get("success_count", 0) or 0))
        normalized["failure_count"] = max(0, int(normalized.get("failure_count", 0) or 0))
        evidence_count = max(1, int(normalized.get("evidence_count", 0) or 0))
        normalized["evidence_count"] = evidence_count
        normalized["success_rate"] = round(
            normalized["success_count"] / max(1, normalized["success_count"] + normalized["failure_count"]),
            4,
        )
        last_active_at = float(
            normalized.get("last_hit_at")
            or normalized.get("updated_at")
            or current
        )
        normalized["failure_penalty"] = self._compute_failure_penalty(normalized)
        normalized["time_decay"] = self._compute_time_decay(last_active_at, now=current)
        normalized["planner_weight"] = self._compute_planner_weight(normalized, now=current)
        normalized["exploration_rate"] = self._compute_exploration_rate(normalized)
        if "recommended_skill_chain" in normalized and not isinstance(normalized.get("recommended_skill_chain"), list):
            normalized["recommended_skill_chain"] = []
        if "recommended_selection_mode" in normalized and not isinstance(
            normalized.get("recommended_selection_mode"),
            str,
        ):
            normalized["recommended_selection_mode"] = ""
        return normalized

    def build_situation_key(
        self,
        *,
        observation: dict[str, Any],
        intent_source: str = "observation",
        intent_type: str = "tool_call",
    ) -> str:
        latest_source = str(observation.get("source") or "none")
        intent_guess = str(observation.get("intent_guess") or "unknown")
        notice_type = str(observation.get("metadata", {}).get("notice_type") or "none")
        return f"{intent_source}:{intent_type}:{latest_source}:{intent_guess}:{notice_type}"

    def _match_bonus(
        self,
        strategy: dict[str, Any],
        observation: dict[str, Any],
        desired_action_type: str | None = None,
    ) -> float:
        situation_key = str(strategy.get("situation_key") or "")
        latest_source = str(observation.get("source") or "")
        intent_guess = str(observation.get("intent_guess") or "")
        notice_type = str(observation.get("metadata", {}).get("notice_type") or "")
        bonus = 0.0
        if desired_action_type and str(strategy.get("action_type") or "") == desired_action_type:
            bonus += 0.35
        if latest_source and latest_source in situation_key:
            bonus += 0.2
        if intent_guess and intent_guess in situation_key:
            bonus += 0.25
        if notice_type and notice_type in situation_key:
            bonus += 0.1
        return bonus

    def append_reflection(self, record: ReflectionRecord | dict[str, Any]) -> None:
        payload = record.to_dict() if isinstance(record, ReflectionRecord) else dict(record)
        chat_id = str(payload.get("chat_id") or "global")
        path = self._reflection_path(chat_id)
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as e:  # noqa: BLE001
            logger.error(f"append_reflection failed for {chat_id}: {e}")

    def get_recent_reflections(self, chat_id: str, limit: int = 20) -> list[dict[str, Any]]:
        path = self._reflection_path(chat_id)
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]
        except Exception as e:  # noqa: BLE001
            logger.error(f"read reflections failed for {chat_id}: {e}")
            return []

        results: list[dict[str, Any]] = []
        for line in lines[-max(1, limit) :]:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                results.append(payload)
        return results

    def get_chat_ids_with_reflections(self) -> list[str]:
        chat_ids: list[str] = []
        for path in REFLECTION_DIR.glob("*.jsonl"):
            try:
                chat_ids.append(path.stem)
            except Exception:
                continue
        return sorted(set(chat_ids))

    def get_strategy_memory(self, chat_id: str) -> dict[str, Any]:
        path = self._strategy_path(chat_id)
        if not path.exists():
            return {"chat_id": chat_id, "strategies": [], "updated_at": 0.0}
        try:
            with path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
            if isinstance(payload, dict):
                strategies = payload.get("strategies", [])
                if isinstance(strategies, list):
                    payload["strategies"] = [self._normalize_strategy(item) for item in strategies if isinstance(item, dict)]
                return payload
        except Exception as e:  # noqa: BLE001
            logger.error(f"load strategy memory failed for {chat_id}: {e}")
        return {"chat_id": chat_id, "strategies": [], "updated_at": 0.0}

    def save_strategy_memory(self, chat_id: str, strategies: Iterable[dict[str, Any]]) -> dict[str, Any]:
        now = time.time()
        normalized = self._prune_strategies(list(strategies), now=now)
        payload = {
            "chat_id": chat_id,
            "updated_at": now,
            "strategies": normalized,
        }
        path = self._strategy_path(chat_id)
        try:
            with path.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:  # noqa: BLE001
            logger.error(f"save strategy memory failed for {chat_id}: {e}")
        return payload

    def distill_recent_reflections(self, chat_id: str, limit: int = 50) -> dict[str, Any]:
        reflections = self.get_recent_reflections(chat_id, limit=limit)
        if not reflections:
            return self.get_strategy_memory(chat_id)

        existing_payload = self.get_strategy_memory(chat_id)
        existing_map = {
            str(item.get("strategy_key")): item
            for item in existing_payload.get("strategies", [])
            if isinstance(item, dict) and item.get("strategy_key")
        }

        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        skill_grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        selection_mode_grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for item in reflections:
            key = (
                str(item.get("situation_key") or "unknown"),
                str(item.get("action_type") or "unknown"),
            )
            grouped[key].append(item)
            evidence = item.get("evidence", {})
            receipt_outputs = evidence.get("receipt_outputs", {}) if isinstance(evidence, dict) else {}
            executed_skills = receipt_outputs.get("executed_skills", []) if isinstance(receipt_outputs, dict) else []
            if isinstance(executed_skills, list):
                skill_chain = [str(skill).strip() for skill in executed_skills if str(skill).strip()]
                if skill_chain:
                    skill_combo_key = ">".join(skill_chain)
                    skill_key = (
                        str(item.get("situation_key") or "unknown"),
                        str(item.get("action_type") or "unknown"),
                        skill_combo_key,
                    )
                    skill_grouped[skill_key].append(item)
            skill_plan_meta = receipt_outputs.get("skill_plan_meta", {}) if isinstance(receipt_outputs, dict) else {}
            selection_mode = (
                str(skill_plan_meta.get("selection_mode") or "").strip() if isinstance(skill_plan_meta, dict) else ""
            )
            if selection_mode:
                selection_mode_key = (
                    str(item.get("situation_key") or "unknown"),
                    str(item.get("action_type") or "unknown"),
                    selection_mode,
                )
                selection_mode_grouped[selection_mode_key].append(item)

        strategies: list[dict[str, Any]] = []
        for (situation_key, action_type), items in grouped.items():
            evidence_count = len(items)
            avg_score = sum(float(item.get("score") or 0.0) for item in items) / max(1, evidence_count)
            success_count = sum(1 for item in items if item.get("status") == "success")
            failure_count = sum(1 for item in items if item.get("status") in {"failed", "skipped"})

            if evidence_count < 2 and abs(avg_score) < 0.7:
                continue

            sample = items[-1]
            suggestion = str(sample.get("suggested_adjustment") or "").strip()
            if not suggestion:
                if success_count >= failure_count:
                    suggestion = "延续当前策略，保持简洁稳定。"
                else:
                    suggestion = "降低打扰性并延迟类似动作。"

            strategy_key = f"{situation_key}:{action_type}"
            previous = existing_map.get(strategy_key, {})
            strategies.append(
                {
                    "strategy_key": strategy_key,
                    "strategy_scope": "action",
                    "situation_key": situation_key,
                    "action_type": action_type,
                    "recommended_adjustment": suggestion,
                    "confidence": round(min(0.95, 0.35 + evidence_count * 0.1 + max(0.0, avg_score) * 0.2), 3),
                    "avg_score": round(avg_score, 3),
                    "success_count": success_count,
                    "failure_count": failure_count,
                    "evidence_count": evidence_count,
                    "last_reason": str(sample.get("reason") or ""),
                    "hit_count": int(previous.get("hit_count", 0) or 0),
                    "last_hit_at": float(previous.get("last_hit_at", 0.0) or 0.0),
                    "updated_at": time.time(),
                }
            )

        for (situation_key, action_type, skill_combo_key), items in skill_grouped.items():
            evidence_count = len(items)
            avg_score = sum(float(item.get("score") or 0.0) for item in items) / max(1, evidence_count)
            success_count = sum(1 for item in items if item.get("status") == "success")
            failure_count = sum(1 for item in items if item.get("status") in {"failed", "skipped"})
            if evidence_count < 2 and avg_score < 0.6:
                continue

            sample = items[-1]
            previous = existing_map.get(f"skill_combo:{situation_key}:{skill_combo_key}", {})
            recommended_chain = [part for part in skill_combo_key.split(">") if part]
            strategies.append(
                {
                    "strategy_key": f"skill_combo:{situation_key}:{skill_combo_key}",
                    "strategy_scope": "skill_combo",
                    "situation_key": situation_key,
                    "action_type": action_type,
                    "skill_combo_key": skill_combo_key,
                    "recommended_skill_chain": recommended_chain,
                    "recommended_adjustment": "优先复用这组技能顺序执行。",
                    "confidence": round(min(0.95, 0.3 + evidence_count * 0.12 + max(0.0, avg_score) * 0.2), 3),
                    "avg_score": round(avg_score, 3),
                    "success_count": success_count,
                    "failure_count": failure_count,
                    "evidence_count": evidence_count,
                    "last_reason": str(sample.get("reason") or ""),
                    "hit_count": int(previous.get("hit_count", 0) or 0),
                    "last_hit_at": float(previous.get("last_hit_at", 0.0) or 0.0),
                    "updated_at": time.time(),
                }
            )

        for (situation_key, action_type, selection_mode), items in selection_mode_grouped.items():
            evidence_count = len(items)
            avg_score = sum(float(item.get("score") or 0.0) for item in items) / max(1, evidence_count)
            success_count = sum(1 for item in items if item.get("status") == "success")
            failure_count = sum(1 for item in items if item.get("status") in {"failed", "skipped"})
            if evidence_count < 2 and avg_score < 0.4:
                continue

            previous = existing_map.get(f"selection_mode:{situation_key}:{selection_mode}", {})
            strategies.append(
                {
                    "strategy_key": f"selection_mode:{situation_key}:{selection_mode}",
                    "strategy_scope": "selection_mode",
                    "situation_key": situation_key,
                    "action_type": action_type,
                    "recommended_selection_mode": selection_mode,
                    "recommended_adjustment": f"在类似情境下优先采用 {selection_mode}。",
                    "confidence": round(min(0.95, 0.28 + evidence_count * 0.1 + max(0.0, avg_score) * 0.22), 3),
                    "avg_score": round(avg_score, 3),
                    "success_count": success_count,
                    "failure_count": failure_count,
                    "evidence_count": evidence_count,
                    "last_reason": str(items[-1].get("reason") or ""),
                    "hit_count": int(previous.get("hit_count", 0) or 0),
                    "last_hit_at": float(previous.get("last_hit_at", 0.0) or 0.0),
                    "updated_at": time.time(),
                }
            )

        strategies.sort(
            key=lambda item: (
                float(item.get("confidence") or 0.0),
                float(item.get("avg_score") or 0.0),
                int(item.get("evidence_count") or 0),
            ),
            reverse=True,
        )
        strategies = strategies[:12]
        return self.save_strategy_memory(chat_id, strategies)

    def match_strategy_hints(
        self,
        chat_id: str,
        observation: dict[str, Any],
        desired_action_type: str | None = None,
        strategy_scope: str = "action",
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        payload = self.get_strategy_memory(chat_id)
        strategies = payload.get("strategies", [])
        if not isinstance(strategies, list):
            return []

        ranked: list[dict[str, Any]] = []
        for item in strategies:
            if not isinstance(item, dict):
                continue
            normalized = self._normalize_strategy(item)
            normalized = self._apply_skill_combo_guardrails(normalized)
            if str(normalized.get("strategy_scope") or "action") != strategy_scope:
                continue
            if normalized.get("is_disabled"):
                continue
            match_bonus = self._match_bonus(normalized, observation, desired_action_type=desired_action_type)
            if match_bonus <= 0.0:
                continue
            normalized["match_bonus"] = round(match_bonus, 4)
            normalized["rank_score"] = round(
                float(normalized.get("planner_weight", 0.0) or 0.0) + match_bonus,
                4,
            )
            ranked.append(normalized)

        ranked.sort(
            key=lambda item: (
                float(item.get("rank_score", 0.0) or 0.0),
                float(item.get("planner_weight", 0.0) or 0.0),
                int(item.get("hit_count", 0) or 0),
            ),
            reverse=True,
        )
        return ranked[: max(1, limit)]

    def match_skill_combo_hints(
        self,
        chat_id: str,
        observation: dict[str, Any],
        limit: int = 2,
    ) -> list[dict[str, Any]]:
        return self.match_strategy_hints(
            chat_id,
            observation,
            desired_action_type="tool_call",
            strategy_scope="skill_combo",
            limit=limit,
        )

    def match_selection_mode_hints(
        self,
        chat_id: str,
        observation: dict[str, Any],
        limit: int = 2,
    ) -> list[dict[str, Any]]:
        return self.match_strategy_hints(
            chat_id,
            observation,
            desired_action_type="tool_call",
            strategy_scope="selection_mode",
            limit=limit,
        )

    def record_strategy_hits(self, chat_id: str, strategy_keys: list[str]) -> None:
        if not strategy_keys:
            return
        payload = self.get_strategy_memory(chat_id)
        strategies = payload.get("strategies", [])
        if not isinstance(strategies, list):
            return

        now = time.time()
        key_set = {key for key in strategy_keys if key}
        if not key_set:
            return
        changed = False
        updated: list[dict[str, Any]] = []
        for item in strategies:
            if not isinstance(item, dict):
                continue
            strategy = dict(item)
            if str(strategy.get("strategy_key") or "") in key_set:
                strategy["hit_count"] = int(strategy.get("hit_count", 0) or 0) + 1
                strategy["last_hit_at"] = now
                changed = True
            updated.append(strategy)

        if changed:
            self.save_strategy_memory(chat_id, updated)


experience_store = ExperienceStore()

