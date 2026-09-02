"""师承（「师」）配置。

「师」为修仙五要之一：无师不成道。师承提供稳定的修炼/突破加成，
但需要以「贡献」换取师父指点的次数 —— 形成「做师门任务 → 攒贡献 → 换指点」的循环。
"""

from __future__ import annotations

from dataclasses import dataclass

from .arts import ArtEffect


@dataclass(frozen=True)
class MasterDef:
    key: str
    name: str
    sect: str                      # 所属门派 key
    title: str                     # 称谓
    desc: str = ""
    min_rank: str = "外门"          # 拜师所需门派职位
    effects: tuple[ArtEffect, ...] = ()
    mentor_cost: int = 30          # 每次指点消耗的贡献
    mentor_breakthrough: float = 0.05   # 指点后本次突破的额外成功率


MASTERS: dict[str, MasterDef] = {
    "qingyun_sword": MasterDef(
        "qingyun_sword", "玄清真人", "qingyun", "剑修",
        "青云宗剑修耆宿，最重心性，指点时往往一言中的。",
        min_rank="外门",
        effects=(
            ArtEffect("cultivate_speed", 0.08),
            ArtEffect("attr_mul", 0.05, key="comprehension"),
        ),
    ),
    "xuesha_blood": MasterDef(
        "xuesha_blood", "血手人屠", "xuesha", "魔修",
        "行事狠辣，只认实力，指点粗暴却见效。",
        min_rank="内门",
        effects=(
            ArtEffect("cultivate_speed", 0.10),
            ArtEffect("attr_mul", 0.06, key="atk"),
        ),
        mentor_breakthrough=0.06,
    ),
    "yaowang_dan": MasterDef(
        "yaowang_dan", "药王", "yaowang", "丹修",
        "丹道宗师，随手指点便省去数年苦功。",
        min_rank="内门",
        effects=(
            ArtEffect("cultivate_speed", 0.12),
            ArtEffect("insight_rate", 0.02),
            ArtEffect("attr_mul", 0.05, key="physique"),
        ),
        mentor_breakthrough=0.05,
    ),
    "tianji_star": MasterDef(
        "tianji_star", "观星老人", "tianji", "阵修",
        "天机阁阁主，夜观星象，能算人命数，亦能算突破之机。",
        min_rank="真传",
        effects=(
            ArtEffect("cultivate_speed", 0.14),
            ArtEffect("breakthrough_rate", 0.02),
            ArtEffect("attr_mul", 0.08, key="comprehension"),
        ),
        mentor_breakthrough=0.07,
    ),
    "jianzhong_master": MasterDef(
        "jianzhong_master", "断水剑仙", "jianzhong", "剑仙",
        "剑冢守冢人，一剑断水，指点唯有一字——快。",
        min_rank="真传",
        effects=(
            ArtEffect("cultivate_speed", 0.12),
            ArtEffect("attr_mul", 0.10, key="atk"),
        ),
        mentor_breakthrough=0.05,
    ),
    "wanbao_master": MasterDef(
        "wanbao_master", "金算盘", "wanbao", "商修",
        "万宝楼主事，凡事皆可交易，连突破也能花钱铺路。",
        min_rank="内门",
        effects=(
            ArtEffect("stone_gain", 0.10),
            ArtEffect("cultivate_speed", 0.08),
            ArtEffect("attr_mul", 0.05, key="luck"),
        ),
        mentor_breakthrough=0.04,
    ),
}


def get_master(key: str) -> MasterDef:
    if key not in MASTERS:
        raise KeyError(f"未知师承: {key}")
    return MASTERS[key]


def masters_of(sect_key: str) -> list[MasterDef]:
    return [m for m in MASTERS.values() if m.sect == sect_key]
