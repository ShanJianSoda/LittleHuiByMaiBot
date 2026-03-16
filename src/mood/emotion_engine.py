"""
情绪引擎门面（效果优先版）：对接现有 mood_manager，并暴露 VAD/动力学/环境因子能力。

设计原则：AI-bot 一致性。VAD 为全 bot 单一实例，所有聊天流共享同一情绪状态（与 mood_state 默认「感觉很平静」一致）。

- 读取情绪：统一通过 get_mood / get_mood_for_prompt，底层仍用 mood_manager。
- 可选 VAD 路径（use_vad_path）：词典+动力学更新/回归，写入 mood_manager.mood.mood_state（全 bot 单一 ChatMood）。

VAD 初始化与动态过程（config.mood.use_vad_path = True 时）：
1. 初始化：无集中启动。全 bot 唯一 MoodDynamics 在首次 compute_delta_with_framework 或 get_global_dynamics 时懒加载，state = [0, 0, 0]（中性）。
2. 动态更新：任意 chat 收到消息且概率命中时，用当前 chat_id 的环境因子调制 delta，更新**全局** VAD，再写回 mood_manager.mood.mood_state。
3. 动态回归：MoodRegressionTask 每周期对**全局** VAD 仅 tick 一次（tick_only），再写回 mood_manager.mood.mood_state。
"""

from typing import Tuple, Optional

from src.common.logger import get_logger
from src.mood.mood_estimator import estimate_from_lexicon
from src.mood.mood_dynamics import MoodDynamics, vad_to_bucket
from src.mood.complex_env_adapter import get_complex_env_adapter

logger = get_logger("mood")


def get_mood_for_prompt(chat_id: str, include_vad: bool = False) -> str:
    """
    获取用于 Prompt 的情绪描述。
    - include_vad=False：仅返回 mood_state（与现有行为一致）。
    - include_vad=True：若有全局 VAD 则追加简短 VAD 描述（所有 chat 一致）。
    """
    from src.mood.mood_manager import mood_manager
    
    chat_mood = mood_manager.get_mood_by_chat_id(chat_id)
    base = (chat_mood.mood_state or "").strip()
    if not include_vad:
        return base or "（未提供）"
    dyn = _global_dynamics
    if dyn is None:
        return base or "（未提供）"
    v, a, d = dyn.get_state()
    bucket = vad_to_bucket(v, a, d)
    # 若 mood_state 已是 VAD 转描述（如 use_vad_path 下），避免重复拼接
    if (bucket or "").strip() == base:
        return base
    return f"{base}（当前VAD: {bucket}）" if base else bucket


def get_vad_state(chat_id: str) -> Optional[Tuple[float, float, float]]:
    """返回全 bot 统一的 VAD 状态 (v, a, d)；未初始化时 None。chat_id 仅保留接口兼容。"""
    if _global_dynamics is None:
        return None
    return _global_dynamics.get_state()


def compute_delta_with_framework(
    chat_id: str,
    message_text: str = "",
    user_id: str = "",
    message_count: int = 1,
    use_env_factors: bool = True,
    message_vad: Optional[Tuple[float, float, float]] = None,
) -> Tuple[float, float, float]:
    """
    使用消息 VAD（或词典估计）+ 当前 chat 的环境因子（可选）+ **全局**动力学，计算本次增量并更新全局 VAD。
    不写入 mood_manager，由调用方写回 mood_manager.mood.mood_state。
    返回更新后的 (v, a, d)。chat_id 用于环境因子（关系/话题/上下文），VAD 本身为全局单一。

    - message_vad: 若传入 (v, a, d)，则跳过 estimate_from_lexicon，用该值作为 delta 基底（适合已入库算好的 Messages.emotion_v/a/d）。
    """
    if message_vad is not None and len(message_vad) >= 3:
        v, a, d = float(message_vad[0]), float(message_vad[1]), float(message_vad[2])
    else:
        v, a, d, _ = estimate_from_lexicon(message_text or "")
    delta = (v, a, d)
    if use_env_factors:
        adapter = get_complex_env_adapter(chat_id)
        w_rel, w_topic, w_context = adapter.get_factors(user_id, message_count)
        scale = w_rel * w_topic * w_context
        delta = (v * scale, a * scale, d * scale)
    dyn = get_global_dynamics()
    new_state = dyn.update(delta)
    return new_state


# 全 bot 单一 VAD（设计原则：AI-bot 一致性），懒加载
_global_dynamics: Optional[MoodDynamics] = None


def get_global_dynamics() -> MoodDynamics:
    """获取全 bot 唯一的动力学实例；首次调用时创建，初始 state=[0,0,0]。"""
    global _global_dynamics
    if _global_dynamics is None:
        _global_dynamics = MoodDynamics()
    return _global_dynamics