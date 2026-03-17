"""
进程内存占用监控与按组件统计，用于排查内存偏高或泄漏。
在内存超过阈值或定时输出时，打印各聊天流、缓存列表等的数据量，便于定位大对象。
"""

import os
import sys
from typing import Any, Dict

from src.common.logger import get_logger

logger = get_logger("memory_monitor")

# 默认超过此值(MB)时以 WARNING 级别输出并标注「内存占用偏高」
DEFAULT_HIGH_RSS_MB = 800


def get_process_rss_mb() -> float:
    """当前进程常驻内存 RSS（MB）。Linux 读 /proc/self/status，其它平台用 resource 等。"""
    try:
        if sys.platform == "linux":
            with open("/proc/self/status", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        # 格式: "VmRSS:    12345 kB"
                        parts = line.split()
                        if len(parts) >= 3 and parts[2].lower() == "kb":
                            return int(parts[1]) / 1024.0
                        if len(parts) >= 2:
                            return int(parts[1]) / (1024.0 * 1024.0)
                        return 0.0
        if hasattr(os, "getpagesize"):
            try:
                import resource
                # ru_maxrss: Linux 为 KB，macOS 为 bytes
                rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                if rss > 1024 * 1024:
                    return rss / (1024.0 * 1024.0)
                return rss / 1024.0
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"获取 RSS 失败: {e}")
    return 0.0


def _safe_len(obj: Any, default: int = -1) -> int:
    try:
        return len(obj)
    except Exception:
        return default


def get_memory_breakdown() -> Dict[str, Any]:
    """
    收集各组件与缓存的数量/长度，便于分析内存占用。
    使用懒导入避免循环依赖，单次采集失败不影响其它项。
    """
    out: Dict[str, Any] = {}

    # 1) 心流聊天实例
    try:
        from src.chat.heart_flow.heartflow import heartflow
        hf = heartflow
        out["heartflow_chat_count"] = _safe_len(getattr(hf, "heartflow_chat_list", None))
        chat_list = getattr(hf, "heartflow_chat_list", None) or {}
        history_lens = []
        for _cid, chat in list(chat_list.items())[:20]:  # 最多 20 个
            try:
                history_lens.append(_safe_len(getattr(chat, "history_loop", None)))
            except Exception:
                pass
        if history_lens:
            out["heartflow_history_loop_lens"] = history_lens
            out["heartflow_history_loop_max"] = max(history_lens)
    except Exception as e:
        out["heartflow_error"] = str(e)[:80]

    # 2) ChatManager streams / last_messages
    try:
        from src.chat.message_receive.chat_stream import get_chat_manager
        cm = get_chat_manager()
        out["chat_manager_streams_count"] = _safe_len(getattr(cm, "streams", None))
        out["chat_manager_last_messages_count"] = _safe_len(getattr(cm, "last_messages", None))
    except Exception as e:
        out["chat_manager_error"] = str(e)[:80]

    # 3) 表达学习器缓存
    try:
        from src.bw_learner.expression_learner import expression_learner_manager
        out["expression_learners_count"] = _safe_len(getattr(expression_learner_manager, "expression_learners", None))
    except Exception as e:
        out["expression_learners_error"] = str(e)[:80]

    # 4) 频率控制缓存
    try:
        from src.chat.heart_flow.frequency_control import frequency_control_manager
        out["frequency_control_count"] = _safe_len(getattr(frequency_control_manager, "frequency_control_dict", None))
    except Exception as e:
        out["frequency_control_error"] = str(e)[:80]

    # 5) 心流内各聊天的概括器 topic_cache（仅统计已存在的心流实例）
    try:
        from src.chat.heart_flow.heartflow import heartflow
        topic_counts = []
        for _cid, chat in list(getattr(heartflow, "heartflow_chat_list", None) or {}).items():
            try:
                summarizer = getattr(chat, "chat_history_summarizer", None)
                if summarizer is not None:
                    cache = getattr(summarizer, "topic_cache", None)
                    topic_counts.append((str(_cid)[:16], _safe_len(cache)))
            except Exception:
                pass
        if topic_counts:
            out["summarizer_topic_cache_by_chat"] = topic_counts
            out["summarizer_topic_cache_total"] = sum(n for _, n in topic_counts)
    except Exception as e:
        out["summarizer_error"] = str(e)[:80]

    return out


def log_memory_breakdown(
    threshold_mb: float = DEFAULT_HIGH_RSS_MB,
    force_high: bool = False,
) -> None:
    """
    获取当前 RSS 与各组件统计并写日志。
    若 RSS >= threshold_mb 或 force_high 为 True，以 WARNING 级别输出并标注「内存占用偏高」。
    """
    rss_mb = get_process_rss_mb()
    breakdown = get_memory_breakdown()

    is_high = force_high or (rss_mb > 0 and rss_mb >= threshold_mb)
    level = "WARNING" if is_high else "INFO"
    prefix = "[内存占用偏高] " if is_high else ""

    parts = [f"{prefix}RSS≈{rss_mb:.1f}MB"]
    for k, v in breakdown.items():
        if k.endswith("_error"):
            continue
        if isinstance(v, list) and len(v) > 5:
            parts.append(f"{k}={v[:5]}...")
        else:
            parts.append(f"{k}={v}")

    msg = " | ".join(parts)
    if level == "WARNING":
        logger.warning(msg)
    else:
        logger.info(msg)

    # 若有错误项，单独 debug 输出
    errors = {k: v for k, v in breakdown.items() if k.endswith("_error")}
    if errors:
        logger.debug(f"memory_breakdown 部分采集失败: {errors}")


def _create_memory_monitor_task(
    run_interval: int = 1800,
    threshold_mb: float = DEFAULT_HIGH_RSS_MB,
    wait_before_start: int = 120,
):
    """构造一个继承 AsyncTask 的内存监控任务，供 async_task_manager 调度。"""
    from src.manager.async_task_manager import AsyncTask

    class _MemoryMonitorTask(AsyncTask):
        async def run(self) -> None:
            log_memory_breakdown(threshold_mb=self.threshold_mb)

    inst = _MemoryMonitorTask(
        task_name="Memory Monitor",
        wait_before_start=wait_before_start,
        run_interval=run_interval,
    )
    inst.threshold_mb = threshold_mb
    return inst


# 供 main 等直接 add_task 使用
MemoryMonitorTask = _create_memory_monitor_task
