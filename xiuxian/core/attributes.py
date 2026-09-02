"""属性面板：基础属性 + 修正器（Modifier）体系。

设计要点：
    属性值 = (base + Σ add) × Π mul
    装备、功法、丹药 buff、阵法加成全部走 Modifier，互不干扰、可随时装卸，
    后续加装备系统不需要改一行属性计算代码。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

# 参与战斗/修炼计算的属性键
MAX_HP = "max_hp"
MAX_MP = "max_mp"
ATK = "atk"
DEF = "def"
SPEED = "speed"
SPIRIT = "spirit"          # 神识：影响渡劫、探查
COMPREHENSION = "comprehension"  # 悟性：修炼速度与突破率
PHYSIQUE = "physique"      # 根骨：气血上限与突破率
LUCK = "luck"              # 气运：事件品质与突破率

ATTR_LABELS: dict[str, str] = {
    MAX_HP: "气血上限",
    MAX_MP: "灵力上限",
    ATK: "攻击",
    DEF: "防御",
    SPEED: "身法",
    SPIRIT: "神识",
    COMPREHENSION: "悟性",
    PHYSIQUE: "根骨",
    LUCK: "气运",
}

DEFAULT_BASE: dict[str, float] = {
    MAX_HP: 60.0,
    MAX_MP: 40.0,
    ATK: 6.0,
    DEF: 3.0,
    SPEED: 5.0,
    SPIRIT: 5.0,
    COMPREHENSION: 10.0,
    PHYSIQUE: 10.0,
    LUCK: 10.0,
}


@dataclass
class Modifier:
    """一条属性修正。hours_left 为 None 表示永久（装备/功法）。"""

    source: str                                  # 来源标识，如 "equip:青锋剑"、"buff:顿悟"
    add: dict[str, float] = field(default_factory=dict)
    mul: dict[str, float] = field(default_factory=dict)   # 1.2 表示 +20%
    hours_left: float | None = None

    def tick(self, hours: float) -> bool:
        """返回 True 表示仍然有效。"""
        if self.hours_left is None:
            return True
        self.hours_left -= hours
        return self.hours_left > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "add": dict(self.add),
            "mul": dict(self.mul),
            "hours_left": self.hours_left,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Modifier":
        return cls(
            source=data["source"],
            add=dict(data.get("add", {})),
            mul=dict(data.get("mul", {})),
            hours_left=data.get("hours_left"),
        )


class AttributeSet:
    """一组属性及其修正器。"""

    def __init__(self, base: dict[str, float] | None = None) -> None:
        self.base: dict[str, float] = dict(base or DEFAULT_BASE)
        self.modifiers: list[Modifier] = []

    # ---------- 读取 ----------
    def value(self, key: str) -> float:
        total = self.base.get(key, 0.0)
        ratio = 1.0
        for m in self.modifiers:
            total += m.add.get(key, 0.0)
            ratio *= m.mul.get(key, 1.0)
        return total * ratio

    def int_value(self, key: str) -> int:
        return int(round(self.value(key)))

    def panel(self) -> dict[str, int]:
        return {k: self.int_value(k) for k in ATTR_LABELS}

    # ---------- 修改 ----------
    def grow_base(self, key: str, amount: float) -> None:
        self.base[key] = self.base.get(key, 0.0) + amount

    def add_modifier(self, modifier: Modifier) -> None:
        """同名 source 的修正器会被替换（避免重复嗑药叠加无限 buff）。"""
        self.remove_modifier(modifier.source)
        self.modifiers.append(modifier)

    def remove_modifier(self, source: str) -> None:
        self.modifiers = [m for m in self.modifiers if m.source != source]

    def remove_modifiers_with_prefix(self, prefix: str) -> list[str]:
        """按 source 前缀批量移除（装备、功法重建属性时用），返回被移除的 source 列表。"""
        removed = [m.source for m in self.modifiers if m.source.startswith(prefix)]
        self.modifiers = [m for m in self.modifiers if not m.source.startswith(prefix)]
        return removed

    def has_modifier(self, source: str) -> bool:
        return any(m.source == source for m in self.modifiers)

    def active_buffs(self) -> list[Modifier]:
        return [m for m in self.modifiers if m.hours_left is not None]

    def tick(self, hours: float) -> list[str]:
        """推进时间，返回过期提示。"""
        expired = [m for m in self.modifiers if not m.tick(hours)]
        self.modifiers = [m for m in self.modifiers if m not in expired]
        return [f"【{m.source}】效果已散去" for m in expired]

    # ---------- 序列化 ----------
    def to_dict(self) -> dict[str, Any]:
        return {
            "base": dict(self.base),
            "modifiers": [m.to_dict() for m in self.modifiers],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AttributeSet":
        obj = cls(data.get("base"))
        obj.modifiers = [Modifier.from_dict(m) for m in data.get("modifiers", [])]
        return obj


def make_modifier(
    source: str,
    hours: float | None = None,
    add: dict[str, float] | None = None,
    mul: dict[str, float] | None = None,
) -> Modifier:
    return Modifier(source=source, add=dict(add or {}), mul=dict(mul or {}), hours_left=hours)


def merge_modifiers(modifiers: Iterable[Modifier]) -> Modifier:
    """把多条修正合并成一条（用于结算展示）。"""
    add: dict[str, float] = {}
    mul: dict[str, float] = {}
    for m in modifiers:
        for k, v in m.add.items():
            add[k] = add.get(k, 0.0) + v
        for k, v in m.mul.items():
            mul[k] = mul.get(k, 1.0) * v
    return Modifier(source="merged", add=add, mul=mul)
