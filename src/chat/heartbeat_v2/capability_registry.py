"""
心跳系统 V2 能力注册表。

区分 tool / skill / mcp 三类能力，供 runtime 观测和 planner 使用。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from src.plugin_system.apis.tool_api import get_llm_available_tool_definitions


CapabilityKind = Literal["tool", "skill", "mcp"]


@dataclass
class Capability:
    name: str
    kind: CapabilityKind
    description: str
    source: str

    def to_dict(self) -> dict:
        return asdict(self)


class CapabilityRegistry:
    """统一描述系统能力，而不是把协议和业务能力混在一起。"""

    def _collect_tools(self) -> list[Capability]:
        capabilities: list[Capability] = []
        for name, definition in get_llm_available_tool_definitions():
            capabilities.append(
                Capability(
                    name=name,
                    kind="tool",
                    description=str(definition.get("description", "") or ""),
                    source="plugin_system",
                )
            )
        return capabilities

    def _collect_skills(self) -> list[Capability]:
        return [
            Capability(
                name="memory_recall_skill",
                kind="skill",
                description="围绕聊天上下文生成问题并检索长期记忆。",
                source="memory_system",
            ),
            Capability(
                name="dream_maintenance_skill",
                kind="skill",
                description="离线整理、合并、更新和删除长期记忆。",
                source="dream_agent",
            ),
            Capability(
                name="expression_learning_skill",
                kind="skill",
                description="从用户对话中学习表达风格与黑话。",
                source="bw_learner",
            ),
            Capability(
                name="multimodal_observation_skill",
                kind="skill",
                description="将文本、图片、语音、通知转为统一 observation。",
                source="heartbeat_v2",
            ),
            Capability(
                name="tool_orchestration_skill",
                kind="skill",
                description="基于共享上下文编排记忆检索与工具执行，而不是直接单步调 tool。",
                source="heartbeat_v2",
            ),
            Capability(
                name="tool_use_skill",
                kind="skill",
                description="将用户请求映射到具体插件工具并执行。",
                source="heartbeat_v2",
            ),
            Capability(
                name="web_search_skill",
                kind="skill",
                description="封装 web_search 工具，用于主动探索和联网检索。",
                source="heartbeat_v2",
            ),
            Capability(
                name="share_decider",
                kind="skill",
                description="根据目标聊天流选择结果与搜索质量决定是否回流为后续 reply。",
                source="heartbeat_v2",
            ),
            Capability(
                name="target_chat_selector",
                kind="skill",
                description="聚合聊天流元信息并为主动探索选择分享目标聊天流。",
                source="heartbeat_v2",
            ),
            Capability(
                name="active_goal_source",
                kind="skill",
                description="将近期事件、未解问题、关系记忆和失败经验转为主动探索目标。",
                source="heartbeat_v2",
            ),
            Capability(
                name="multi_step_plan_executor",
                kind="skill",
                description="为未来的多阶段 plan 提供显式步骤状态和顺序执行骨架。",
                source="heartbeat_v2",
            ),
        ]

    def _collect_mcps(self) -> list[Capability]:
        # 当前项目尚未接入显式 MCP 客户端，这里保留协议层入口，避免与业务能力混淆。
        return []

    def snapshot(self) -> dict[str, list[dict]]:
        tools = self._collect_tools()
        skills = self._collect_skills()
        mcps = self._collect_mcps()
        return {
            "tools": [item.to_dict() for item in tools],
            "skills": [item.to_dict() for item in skills],
            "mcps": [item.to_dict() for item in mcps],
        }


capability_registry = CapabilityRegistry()

