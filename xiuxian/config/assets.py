"""产业（「财」）配置。

「财」为修仙五要之一：无财不足以养道。产业提供每日被动产出（灵石与材料），
是挂机养老的长线收入 —— 但需要前期投入与持续维护，且产出受境界（经营规模）限制。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AssetDef:
    key: str
    name: str
    desc: str = ""
    price: int = 0                                  # 购置灵石
    min_realm: str = "qi_refining"
    upkeep: int = 0                                 # 每日维护灵石
    stones: int = 0                                 # 每日灵石产出（1 级）
    items: tuple[tuple[str, int], ...] = ()         # 每日材料产出（1 级）
    max_level: int = 5
    upgrade_cost: int = 0                           # 每级升级灵石（随等级线性递增）
    growth: float = 0.35                            # 每级产出成长（线性，非复利）


ASSETS: dict[str, AssetDef] = {
    "spirit_field": AssetDef(
        "spirit_field", "灵田",
        "引灵泉灌溉，岁岁生灵草，丹道之基。",
        price=800, upkeep=5, stones=10,
        items=(("spirit_herb", 1),), upgrade_cost=600,
    ),
    "spirit_mine": AssetDef(
        "spirit_mine", "灵石矿脉",
        "地脉中掘灵石，坐地生财，然矿有尽时。",
        price=3000, min_realm="foundation", upkeep=18, stones=60,
        items=(("beast_core", 1),), upgrade_cost=2200,
    ),
    "pill_shop": AssetDef(
        "pill_shop", "坊间丹铺",
        "雇人看顾，坐收丹药之利，最是省心。",
        price=30000, min_realm="core", upkeep=120, stones=280,
        items=(("healing_pill", 1), ("qi_gathering_pill", 1)),
        upgrade_cost=18000,
    ),
    "trade_caravan": AssetDef(
        "trade_caravan", "跨域商队",
        "往来仙城之间，薄利多销，风险自担。",
        price=400000, min_realm="nascent", upkeep=700, stones=1800,
        items=(("beast_blood", 2), ("spirit_herb", 2)),
        upgrade_cost=200000,
    ),
}


def get_asset(key: str) -> AssetDef:
    if key not in ASSETS:
        raise KeyError(f"未知产业: {key}")
    return ASSETS[key]


def level_scale(level: int, asset: AssetDef | None = None) -> float:
    """等级对产出的线性成长（1 级为 1.0）。刻意用线性而非复利，防止后期收入爆炸。"""
    growth = asset.growth if asset else 0.35
    return 1.0 + growth * max(0, int(level) - 1)


def upgrade_price(asset: AssetDef, level: int) -> int:
    """升到下一级所需灵石（随等级线性递增）。"""
    return int(asset.upgrade_cost * max(1, int(level)))
