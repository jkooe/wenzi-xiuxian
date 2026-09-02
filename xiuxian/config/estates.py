"""洞府（「地」）配置。

修仙五要「法财侣地师」，地即洞天福地 —— 灵气浓度决定进境快慢。
洞府提供修炼/炼丹/悟道等加成（与功法共用 ArtEffect 与同一套叠加规则），
但有每日维护灵石：维护不起则加成失效（荒废），形成「财」与「地」的相互牵制。
"""

from __future__ import annotations

from dataclasses import dataclass

from .arts import ArtEffect


@dataclass(frozen=True)
class EstateDef:
    key: str
    name: str
    rank: str                     # 品阶：凡居 / 洞府 / 福地 / 洞天
    desc: str = ""
    price: int = 0                # 购置灵石
    min_realm: str = "qi_refining"
    upkeep: int = 0               # 每日维护灵石
    effects: tuple[ArtEffect, ...] = ()
    upgrade_to: str = ""          # 可升级到的下一档洞府


ESTATES: dict[str, EstateDef] = {
    "cottage": EstateDef(
        "cottage", "山间茅庐", "凡居",
        "竹篱茅舍，聊避风雨，聊胜于无。",
        price=300, upkeep=2,
        effects=(ArtEffect("cultivate_speed", 0.05),),
        upgrade_to="cave",
    ),
    "cave": EstateDef(
        "cave", "清虚洞府", "洞府",
        "依山凿洞，灵气自聚，静修之所。",
        price=5000, min_realm="foundation", upkeep=15,
        effects=(
            ArtEffect("cultivate_speed", 0.15),
            ArtEffect("insight_rate", 0.01),
        ),
        upgrade_to="blessed",
    ),
    "blessed": EstateDef(
        "blessed", "云澜福地", "福地",
        "地脉交汇，灵气如雾，修行一日千里。",
        price=60000, min_realm="core", upkeep=90,
        effects=(
            ArtEffect("cultivate_speed", 0.28),
            ArtEffect("insight_rate", 0.02),
            ArtEffect("attr_mul", 0.05, key="max_mp"),
        ),
        upgrade_to="heaven",
    ),
    "heaven": EstateDef(
        "heaven", "太虚洞天", "洞天",
        "自成一界，内有洞天日月，仙家气象。",
        price=800000, min_realm="nascent", upkeep=600,
        effects=(
            ArtEffect("cultivate_speed", 0.45),
            ArtEffect("insight_rate", 0.04),
            ArtEffect("attr_mul", 0.10, key="max_mp"),
            ArtEffect("attr_mul", 0.05, key="max_hp"),
        ),
        upgrade_to="",
    ),
}


def get_estate(key: str) -> EstateDef:
    if key not in ESTATES:
        raise KeyError(f"未知洞府: {key}")
    return ESTATES[key]
