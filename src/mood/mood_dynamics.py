"""
情绪动力学与 VAD→文本映射（效果优先版 3.2、3.3 节）

- MoodDynamics: 指数衰减 + 增量，状态为 [v, a, d] 三维。
- vad_to_bucket: 将 VAD 向量映射为简短文本描述，供 Prompt 或展示用。
"""

import math
import time
from typing import Tuple

from src.common.logger import get_logger

logger = get_logger("mood")


class MoodDynamics:
    """V/A/D 三维情绪动力学，指数衰减 + 增量，状态范围 [-1, 1]。

    tau: 衰减时间常数，影响记忆持久性，越高越持久，默认(120.0, 120.0, 180.0)
    kappa: 增量因子，影响情绪变化速度，越大越剧烈，默认0.5
    state: 当前情绪状态，[v, a, d]
    last_update: 上次更新时间
    """

    def __init__(
        self,
        tau: Tuple[float, float, float] = (120.0, 120.0, 180.0),
        kappa: float = 0.5,
    ):
        self.tau = (float(tau[0]), float(tau[1]), float(tau[2]))
        self.kappa = kappa
        self.state = [0.0, 0.0, 0.0]  # [v, a, d]
        self.last_update = time.time()

    def update(
        self,
        delta: Tuple[float, float, float],
        kappa: float | None = None,
    ) -> Tuple[float, float, float]:
        """应用衰减与增量，裁剪到 [-1,1]，返回新状态 (v, a, d)。"""
        now = time.time()
        dt = max(0.0, now - self.last_update)
        k = kappa if kappa is not None else self.kappa

        decay = [
            math.exp(-dt / self.tau[0]),
            math.exp(-dt / self.tau[1]),
            math.exp(-dt / self.tau[2]),
        ]
        self.state = [
            max(-1.0, min(1.0, self.state[0] * decay[0] + k * float(delta[0]))),
            max(-1.0, min(1.0, self.state[1] * decay[1] + k * float(delta[1]))),
            max(-1.0, min(1.0, self.state[2] * decay[2] + k * float(delta[2]))),
        ]
        self.last_update = now
        return tuple(self.state)

    def tick_only(self) -> Tuple[float, float, float]:
        """仅衰减，无新输入。"""
        return self.update((0.0, 0.0, 0.0))

    def get_state(self) -> Tuple[float, float, float]:
        return (self.state[0], self.state[1], self.state[2])


# VAD 三维 → 文本描述（规则阈值，零推理成本）
VAD_TO_TEXT: list[Tuple[Tuple[float, float, float], str]] = [
    ((0.4, 0.4, 0.3), "感到开心且高涨"),
    ((0.3, 0.0, 0.3), "感到自信、掌控局面"),
    ((-0.3, 0.0, -0.2), "感到受挫、缺乏掌控"),
    ((0.0, -0.4, 0.0), "趋向平静/疲惫"),
    ((0.5, 0.5, 0.0), "感到开心和兴奋"),
    ((-0.5, 0.4, 0.0), "感到生气"),
    ((-0.5, 0.0, 0.0), "感到低落"),
    ((-0.4, -0.3, 0.0), "感到有点沮丧"),
    ((0.0, 0.0, 0.0), "感觉很平静"),
]


def vad_to_bucket(v: float, a: float, d: float) -> str:
    """
    将 VAD 三维映射为最接近的文本描述（用于可视化或 Prompt）。
    主状态仍为 VAD 向量，文本仅作展示。
    """
    best_dist = 1e9
    best_text = "感觉很平静"
    # todo: 优化文本映射，使其更符合中文表达
    for (kv, ka, kd), text in VAD_TO_TEXT:
        dist = abs(v - kv) + abs(a - ka) + abs(d - kd)
        if dist < best_dist:
            best_dist = dist
            best_text = text
    return best_text
