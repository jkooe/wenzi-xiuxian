"""全局加成聚合器：把「法财侣地师」各来源的效果统一聚合。

为什么需要它
------------
功法、洞府、道侣、师门（乃至将来的法宝、符箓、丹毒）都会给角色加成。
若每个系统各写一套聚合与注入逻辑，就会出现：
    - 同一类型的叠加规则不一致（有的连乘、有的相加）
    - 消费端要挨个问每个系统，新增来源就要改多处
    - 装卸/升级后的重算容易漏掉某个来源

因此核心只做三件事，其余全部数据驱动：
    1. 各来源把 `ArtEffect`（带 source 与已缩放的 value）交给聚合器
    2. 聚合器按 `EFFECT_TYPES` 声明的 stack 规则（add / mul / max）合并
    3. 属性类注入 Modifier，非属性类按 key 供消费端查询

新增一个加成来源 = 实现 `collect_bonuses(agg)` 并调用 `game.rebuild_bonuses()`，
聚合、重算、注入、查询全部自动支持。
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

from ..config.arts import (
    EFFECT_TYPES,
    STACK_ADD,
    STACK_MAX,
    STACK_MUL,
    ArtEffect,
)
from .attributes import Modifier

# 每种叠加规则的幺元
_IDENTITY = {STACK_ADD: 0.0, STACK_MUL: 1.0, STACK_MAX: float("-inf")}

SOURCE_PREFIX = "bonus:"


def _combine(rule: str, current: float, incoming: float) -> float:
    if rule == STACK_MUL:
        return current * incoming
    if rule == STACK_MAX:
        return max(current, incoming)
    return current + incoming


class BonusAggregator:
    """收集各来源效果 -> 聚合 -> 注入属性 / 提供查询。"""

    def __init__(self) -> None:
        # (source, effect, value)：value 由来源自行缩放（等级、熟练度、好感、境界……）
        self._entries: list[tuple[str, ArtEffect, float]] = []
        self._bonus: dict[str, float] = {}
        self._attr_add: dict[str, float] = {}
        self._attr_mul: dict[str, float] = {}

    # ---------- 收集 ----------
    def clear(self) -> None:
        self._entries = []

    def add(self, source: str, eff: ArtEffect, value: float) -> None:
        self._entries.append((source, eff, value))

    def add_many(self, source: str, effects: Iterable[ArtEffect],
                 scaler: Callable[[ArtEffect], float]) -> None:
        """批量加入：scaler 负责把配置值换算成实际生效值（等级/熟练度/好感等）。"""
        for eff in effects:
            self.add(source, eff, scaler(eff))

    # ---------- 聚合与注入 ----------
    def rebuild(self, player) -> None:
        """清空旧结果 -> 聚合所有来源 -> 注入属性修正（**不做 clamp**）。

        夹气血/灵力只放在 `Game.rebuild_bonuses()` 的末尾（唯一出口）。
        因为各系统是逐个恢复/触发重算的，中间态的加成不全会让上限暂时偏低，
        若在此处 clamp 就会把当前值按「无加成的上限」误削且不可恢复。
        """
        self._bonus = {}
        self._attr_add = {}
        self._attr_mul = {}
        player.attributes.remove_modifiers_with_prefix(SOURCE_PREFIX)

        agg: dict[tuple[str, str], float] = {}
        rules: dict[tuple[str, str], str] = {}
        for source, eff, value in self._entries:
            meta = EFFECT_TYPES.get(eff.type)
            if meta is None:
                continue
            rule = eff.stack or meta.stack
            bucket = (eff.type, eff.key or "")
            if bucket not in agg:
                agg[bucket] = _IDENTITY.get(rule, 0.0)
                rules[bucket] = rule
            if rule == STACK_MUL:
                value = 1.0 + value
            agg[bucket] = _combine(rule, agg[bucket], value)

        for (etype, key), value in agg.items():
            if etype == "attr_add":
                self._attr_add[key] = self._attr_add.get(key, 0.0) + value
            elif etype == "attr_mul":
                # 百分比按「偏移」存储，最终乘数 = 1 + 偏移和（非连乘，防爆炸）
                self._attr_mul[key] = self._attr_mul.get(key, 0.0) + value
            else:
                self._bonus[etype] = value

        if self._attr_add or self._attr_mul:
            player.attributes.add_modifier(
                Modifier(
                    source=f"{SOURCE_PREFIX}total",
                    add=dict(self._attr_add),
                    mul={k: 1.0 + v for k, v in self._attr_mul.items()},
                )
            )
        # 注意：不在此处夹气血/灵力，见上方说明（由 Game.rebuild_bonuses 统一处理）

    # ---------- 查询 ----------
    def value(self, key: str) -> float:
        """非属性类加成（修炼速度 / 突破率 / 灵石 / 悟道……）。"""
        return float(self._bonus.get(key, 0.0))

    def value_of(self, key: str, source_prefix: str) -> float:
        """只取某个来源（如 "art:" / "estate:"）的加成 —— 用于分来源展示。"""
        total = 0.0
        for source, eff, value in self._entries:
            if eff.type == key and source.startswith(source_prefix):
                total += value
        return total

    def summary(self) -> list[str]:
        """当前全部加成的一行汇总（可解释性）。"""
        parts = []
        for etype, value in sorted(self._bonus.items()):
            meta = EFFECT_TYPES.get(etype)
            if meta and abs(value) >= 1e-6:
                shown = value * 100 if meta.unit == "%" else value
                parts.append(f"{meta.desc} {shown:+.0f}{meta.unit}")
        return parts

    # ---------- 持久化（不落盘，读档后由各系统重新收集） ----------
    def to_dict(self) -> dict[str, Any]:
        return {}

    def load_state(self, data: dict[str, Any]) -> None:
        self.clear()
