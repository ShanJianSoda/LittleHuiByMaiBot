"""
VAD（Valence–Arousal–Dominance）情绪维度数据模型与常用方法。

供情绪模块、DatabaseMessages、chat_history 等复用；
提供加权平均、序列化等，值域统一为 [-1, 1]。
"""

from dataclasses import dataclass
from typing import Any


def _clamp(x: float, low: float = -1.0, high: float = 1.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    if v != v:  # nan
        return 0.0
    return max(low, min(high, v))


@dataclass(frozen=True)
class VADValue:
    """VAD 三维情绪向量，值域 [-1, 1]。"""

    v: float  # valence 效价
    a: float  # arousal 唤醒
    d: float  # dominance 支配感

    def __post_init__(self) -> None:
        object.__setattr__(self, "v", _clamp(self.v))
        object.__setattr__(self, "a", _clamp(self.a))
        object.__setattr__(self, "d", _clamp(self.d))

    def to_dict(self) -> dict[str, float]:
        """转为字典，键为 "v", "a", "d"。"""
        return {"v": self.v, "a": self.a, "d": self.d}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | Any) -> "VADValue":
        """从字典或含 v/a/d 属性的对象构造；缺失键视为 0。"""
        if hasattr(d, "v") and hasattr(d, "a") and hasattr(d, "d"):
            return cls(
                v=_clamp(getattr(d, "v", 0.0)),
                a=_clamp(getattr(d, "a", 0.0)),
                d=_clamp(getattr(d, "d", 0.0)),
            )
        if isinstance(d, dict):
            return cls(
                v=_clamp(d.get("v", 0.0)),
                a=_clamp(d.get("a", 0.0)),
                d=_clamp(d.get("d", 0.0)),
            )
        raise TypeError("VADValue.from_dict 需要 dict 或含 v/a/d 的对象")

    def tuple(self) -> tuple[float, float, float]:
        """返回 (v, a, d) 元组。"""
        return (self.v, self.a, self.d)


def weighted_average(
    items: list[tuple[VADValue, float]],
) -> VADValue:
    """
    对多个 VAD 值按权重加权平均；权重为 0 的项不参与。
    若总权重为 0，返回中性 (0, 0, 0)。
    """
    if not items:
        return VADValue(0.0, 0.0, 0.0)
    total_w = sum(w for _, w in items)
    if total_w <= 0:
        return VADValue(0.0, 0.0, 0.0)
    v_sum = sum(vad.v * w for vad, w in items)
    a_sum = sum(vad.a * w for vad, w in items)
    d_sum = sum(vad.d * w for vad, w in items)
    return VADValue(
        v=v_sum / total_w,
        a=a_sum / total_w,
        d=d_sum / total_w,
    )


def average_vad(items: list[VADValue]) -> VADValue:
    """对多个 VAD 值做等权平均；空列表返回中性。"""
    if not items:
        return VADValue(0.0, 0.0, 0.0)
    return weighted_average([(x, 1.0) for x in items])
