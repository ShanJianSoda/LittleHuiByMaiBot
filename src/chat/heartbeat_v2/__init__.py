"""
心跳系统 V2 模块。

提供 HeartbeatV2System 与 get_heartbeat_v2_system，
实现「全局心跳中枢 + 意图队列 + 执行回流」架构。
"""
from .system import HeartbeatV2System, get_heartbeat_v2_system

__all__ = ["HeartbeatV2System", "get_heartbeat_v2_system"]
