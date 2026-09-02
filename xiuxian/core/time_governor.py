"""时间管理者：约束游戏时间推进速度不超过现实时间的固定倍率。

设计要点
--------
1. **记账模型**：不做「实时钟替换」，而是给 `advance_time()` 加一道**预算闸门**。
   现实时间流逝 → 预算增长 → `advance_time` 消耗预算 → 预算不足则截断。
   这样现有架构（day/hour + advance_time）零改动，只在入口处加一道闸。

2. **预算计算**：
       可用预算 = (now - anchor) / 3600 × TIME_RATIO - consumed
   其中 `anchor` 是上次预算同步的时刻（UTC epoch），`consumed` 是自那以后
   已消耗的游戏时辰。预算随现实时间线性增长，耗尽后需等现实时间流逝。

3. **幂等**：`sync()` 把 anchor 推进到 now 并清零 consumed —— 调用后预算从零起算。
   读档时 sync 一次，之后 `advance_time` 自然消耗预算。

4. **离线**：离线结算（offline.py）独立处理，不受此闸门影响 —— 离线收益
   已有 365 天上限与待领池设计，与本模块正交。

5. **旁路**：`TIME_RATIO = 0` 表示不限制（demo / 测试用）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

TIME_RATIO = 10.0              # 1 现实小时 = 10 游戏时辰（可调）
GRACE_SECONDS = 5.0            # 宽限期：小于 5 秒的间隙不补充预算（防连点抖动）


@dataclass
class TimeGovernor:
    """时间预算管理者（存档字段，见 to_dict / from_dict）。"""

    anchor: float = 0.0            # 上次预算同步的 UTC epoch
    consumed: float = 0.0          # 自 anchor 以来已消耗的游戏时辰
    disabled: bool = False         # False = 启用闸门（生产默认）；测试/演示设 True 跳过

    def to_dict(self) -> dict[str, Any]:
        return {"anchor": self.anchor, "consumed": round(self.consumed, 2)}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "TimeGovernor":
        data = data or {}
        return cls(
            anchor=float(data.get("anchor", 0.0) or 0.0),
            consumed=float(data.get("consumed", 0.0) or 0.0),
        )

    @staticmethod
    def now() -> float:
        """统一时间源（UTC epoch）。测试可 monkeypatch。"""
        return time.time()

    def sync(self, now: float | None = None) -> None:
        """同步预算基准：anchor = now，consumed = 0。读档/首次登录时调用。"""
        self.anchor = self.now() if now is None else now
        self.consumed = 0.0

    def available(self, now: float | None = None) -> float:
        """当前可用的游戏时辰预算。"""
        if self.disabled:
            return float("inf")
        now = self.now() if now is None else now
        if self.anchor <= 0:
            return float("inf")            # 未初始化 = 不限制
        elapsed = max(0.0, now - self.anchor)
        budget = elapsed / 3600.0 * TIME_RATIO
        return max(0.0, budget - self.consumed)

    def consume(self, hours: float, now: float | None = None) -> float:
        """尝试消耗 `hours` 游戏时辰。返回实际允许的时辰（可能截断）。"""
        if self.disabled:
            return hours
        now = self.now() if now is None else now
        if self.anchor <= 0:
            # 未初始化：初始化并全额放行（首次调用）
            self.anchor = now
            return hours
        avail = self.available(now)
        actual = min(hours, avail)
        self.consumed += actual
        return actual

    def status_text(self) -> str:
        """给玩家看的预算状态。"""
        avail = self.available()
        if avail >= 9999:
            return ""
        hours = int(avail)
        minutes = int((avail - hours) * 60)
        return f"时间预算 {hours}时辰{minutes}分"
