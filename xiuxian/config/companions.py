"""道侣（「侣」）配置。

「侣」为修仙五要之一：同道相伴，可双修共参、可并肩对敌。
道侣的加成随「情分（bond）」增长 —— 情分越高，加成越接近配置值，
符合「相识（五成）→ 知交 → 道侣（十成）」的渐进关系，也避免结识即满收益。
"""

from __future__ import annotations

from dataclasses import dataclass

from .arts import ArtEffect


@dataclass(frozen=True)
class CompanionDef:
    key: str
    name: str
    title: str                    # 称谓，如「剑修」「丹师」
    desc: str = ""
    min_realm: str = "qi_refining"
    meet_cost: int = 0            # 结识所需灵石（或礼金）
    bond_max: int = 1000          # 情分上限
    effects: tuple[ArtEffect, ...] = ()


COMPANIONS: dict[str, CompanionDef] = {
    "qingyao": CompanionDef(
        "qingyao", "青瑶", "剑修",
        "性子清冷，剑术通神，与之论剑常有奇悟。",
        meet_cost=500, bond_max=1000,
        effects=(
            ArtEffect("attr_mul", 0.08, key="atk"),
            ArtEffect("insight_rate", 0.02),
        ),
    ),
    "yaogu": CompanionDef(
        "yaogu", "药奴", "丹师",
        "出身药王谷，识百草、通丹性，最擅调养。",
        meet_cost=1200, min_realm="foundation", bond_max=1200,
        effects=(
            ArtEffect("cultivate_speed", 0.12),
            ArtEffect("attr_mul", 0.06, key="physique"),
        ),
    ),
    "xuanyin": CompanionDef(
        "xuanyin", "玄音", "阵修",
        "善布阵法，与之双修可借阵聚灵，事半功倍。",
        meet_cost=8000, min_realm="core", bond_max=1500,
        effects=(
            ArtEffect("cultivate_speed", 0.20),
            ArtEffect("attr_mul", 0.06, key="max_mp"),
            ArtEffect("insight_rate", 0.02),
        ),
    ),
    "baiyi": CompanionDef(
        "baiyi", "白衣", "剑仙",
        "来历成谜的剑仙，只与元婴之上的道友论道。",
        meet_cost=60000, min_realm="nascent", bond_max=2000,
        effects=(
            ArtEffect("cultivate_speed", 0.25),
            ArtEffect("breakthrough_rate", 0.02),
            ArtEffect("attr_mul", 0.10, key="atk"),
            ArtEffect("insight_rate", 0.03),
        ),
    ),
}


def get_companion(key: str) -> CompanionDef:
    if key not in COMPANIONS:
        raise KeyError(f"未知道侣: {key}")
    return COMPANIONS[key]


def bond_scale(bond: int, max_bond: int) -> float:
    """情分对加成的缩放：初识 0.5，情分圆满 1.0。"""
    ratio = max(0.0, min(1.0, bond / max(1, max_bond)))
    return 0.5 + 0.5 * ratio
