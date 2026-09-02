"""可存档的随机数发生器。

用 random.Random 并持久化内部状态，保证读档后随机序列可延续，
便于复现 bug 和跑确定性演示。
"""

from __future__ import annotations

import random
from typing import Any, Iterable, Sequence, TypeVar

T = TypeVar("T")


class RNG:
    """带状态的随机源。所有随机行为都必须走这里，禁止直接用 random 模块。"""

    def __init__(self, seed: int | None = None) -> None:
        self.seed: int = seed if seed is not None else random.randrange(1, 2**31)
        self._r = random.Random(self.seed)

    # ---------- 基础取值 ----------
    def rand(self) -> float:
        return self._r.random()

    def between(self, low: float, high: float) -> float:
        return low + (high - low) * self._r.random()

    def randint(self, low: int, high: int) -> int:
        return self._r.randint(low, high)

    def chance(self, p: float) -> bool:
        """以概率 p 返回 True。"""
        return self._r.random() < p

    def choice(self, seq: Sequence[T]) -> T:
        return self._r.choice(list(seq))

    def shuffle(self, seq: list[Any]) -> None:
        self._r.shuffle(seq)

    def weighted_choice(
        self, items: Iterable[T], weight_of: Any = None
    ) -> T | None:
        """按权重抽取。weight_of 接受元素返回权重，默认取元素 .weight 属性。"""
        pool = list(items)
        if not pool:
            return None
        if weight_of is None:
            weight_of = lambda x: getattr(x, "weight", 1)
        weights = [max(0.0, float(weight_of(x))) for x in pool]
        total = sum(weights)
        if total <= 0:
            return self.choice(pool)
        return self._r.choices(pool, weights=weights, k=1)[0]

    # ---------- 序列化 ----------
    def to_dict(self) -> dict[str, Any]:
        # getstate -> (version, tuple[625 ints], gauss_next)
        return {"seed": self.seed, "state": list(self._r.getstate()[1])}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RNG":
        obj = cls(int(data["seed"]))
        state = data.get("state")
        if state:
            obj._r.setstate((3, tuple(int(x) for x in state), None))
        return obj
