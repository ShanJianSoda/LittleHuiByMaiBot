"""
情绪模块（效果优先版框架）

- mood_manager：现有实现，不在此修改。负责 ChatMood、回归任务、失眠机制。
- emotion_engine：门面，委托 mood_manager 读取情绪，并可选提供 VAD/动力学/环境因子。
- vad_lexicon / mood_estimator / mood_dynamics：感知与动力学（VAD 三维）。
- relation_manager / topic_tracker / context_smoother / complex_env_adapter：复杂环境适配。

使用方式（保持与 fork 用法一致）：
  - 写入：仍由 message_handler 等调用 chat_mood.update_mood_by_message(...)。
  - 读取：mood_api.get_mood(chat_id) 或 emotion_engine.get_mood(chat_id)；
          需要 Prompt 时可用 emotion_engine.get_mood_for_prompt(chat_id)。
  - 可选 VAD 路径：emotion_engine.compute_delta_with_framework(...) 后按需 set_mood。
"""

from src.mood.mood_manager import mood_manager, ChatMood, MoodManager

__all__ = [
    "mood_manager",
    "ChatMood",
    "MoodManager",
]
