"""功法配置表（数据驱动的可扩展效果系统）。

设计要点
--------
1. **效果即数据**：每门功法的效果是一组 `ArtEffect`，每条只声明 `type + value (+key +params)`。
   新增效果类型 = 在 `EFFECT_TYPES` 声明一条（叠加规则 + 说明）+ 在 `systems/arts.py` 的
   `EFFECT_APPLIERS` 注册一个「如何应用」的函数 —— 核心的聚合/装备/重算逻辑一行都不用改。
2. **品阶预算（平衡的可执行化）**：不同效果按「效果点」折价（`EFFECT_COST`），
   每门功法的总点数不得超过其品阶预算（`RANK_BUDGET`），并由 `validate_balance()` 自检，
   测试会守着它 —— 避免拍脑袋配数值，平衡变成可验证的约束。
3. **等级与熟练度**：效果实际值 = 配置值 × 等级系数 × 熟练度系数。
   等级由熟练度派生（每 LEVEL_PROFICIENCY 点 1 级），熟练度决定「发挥几成」（0.6~1.0）。
   所以拿到手只有六成效力，练满才十成 —— 换高阶功法不等于立刻变强。
4. **同时装备多件**：装备数量上限随境界提升（`slots_for_realm`），多件功法的同类效果
   按类型声明的 `stack` 规则叠加（add 相加 / mul 相乘 / max 取最高）。

获得途径：
    art learn <id>（花灵石 + 境界门槛） / 事件效果 {"type": "art", "id": "qingxin"}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------- 效果类型 ----------
# stack 规则：
#   add —— 数值直接相加（固定值加成、速率加成）
#   mul —— 数值连乘（用于刻意做成指数收益的类型，慎用）
#   max —— 取最高值（用于「不该叠加破上限」的类型，如突破成功率）
STACK_ADD = "add"
STACK_MUL = "mul"
STACK_MAX = "max"


@dataclass(frozen=True)
class EffectTypeDef:
    """效果类型的元信息（数据驱动的核心：核心逻辑只认 stack，不认具体类型）。"""

    stack: str                 # add / mul / max
    desc: str = ""
    unit: str = ""             # 展示单位："" / "%"
    cost: float = 1.0          # 每个单位（1 数值 或 1%）折算的「效果点」，用于品阶预算


@dataclass(frozen=True)
class ArtEffect:
    """一条功法效果。

    type   效果类型（见 EFFECT_TYPES）
    value  数值：attr_add 为固定值；attr_mul 为百分比偏移（0.15 = +15%）；速率类为百分比
    key    属性键（attr_add / attr_mul 用，见 core/attributes.py）
    params 新类型自定义参数（无需改结构即可扩展）
    stack  可选：覆盖该类型的默认叠加规则（add / mul / max）
    """

    type: str
    value: float = 0.0
    key: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    stack: str = ""


EFFECT_TYPES: dict[str, EffectTypeDef] = {
    # 属性：固定值相加
    "attr_add": EffectTypeDef(STACK_ADD, "属性固定值加成", cost=1.0),
    # 属性：百分比。value 为「偏移」（0.15 = +15%），叠加按偏移相加而非连乘，
    # 避免多件功法把百分比叠成指数爆炸（总乘数 = 1 + Σ偏移）
    "attr_mul": EffectTypeDef(STACK_ADD, "属性百分比加成（按偏移相加）", unit="%", cost=1.0),
    # 修炼：打坐/闭关/离线的修为获取速率
    "cultivate_speed": EffectTypeDef(STACK_ADD, "打坐修为速率加成", unit="%", cost=2.0),
    # 突破：成功率加成，取最高（叠加会破坏「突破是决策点」的设计）
    "breakthrough_rate": EffectTypeDef(STACK_MAX, "突破成功率加成", unit="%", cost=3.0),
    # 灵石：战斗/任务灵石获取
    "stone_gain": EffectTypeDef(STACK_ADD, "灵石获取加成", unit="%", cost=2.0),
    # 悟道：顿悟触发概率
    "insight_rate": EffectTypeDef(STACK_ADD, "悟道（顿悟概率）加成", unit="%", cost=2.5),
}

# ---------- 品阶与平衡预算 ----------
RANKS_ORDER = ("黄阶", "玄阶", "地阶", "天阶", "仙阶")
# 每门功法的效果点总预算（按 RANK_BUDGET 校验，防止某一件功法碾压同阶其他功法）
RANK_BUDGET: dict[str, float] = {
    "黄阶": 32.0,
    "玄阶": 90.0,
    "地阶": 240.0,
    "天阶": 260.0,
    "仙阶": 480.0,
}

# 装备数量上限：随境界提升（炼气/筑基/金丹 2 件 → 仙界 5 件）
SLOT_BASE = 2
SLOT_CAP_MAX = 5

# 等级：每 LEVEL_PROFICIENCY 熟练度提升 1 级；每级效果按 level_growth 线性成长
LEVEL_PROFICIENCY = 200

# 固定值加成按「累计属性量级」缩放（见 realm_attr_scale），不写死系数


@dataclass(frozen=True)
class ArtDef:
    id: str
    name: str
    rank: str                        # 品阶
    desc: str = ""
    price: int = 0                   # 修习灵石（sect 功法通常为 0，改用贡献）
    min_realm: str = "qi_refining"   # 修习门槛
    sect: str = ""                   # 所属门派（空 = 通用功法，坊市可购）
    cost_contribution: int = 0       # 门派贡献价（>0 时以贡献支付，需已入门派）
    effects: tuple[ArtEffect, ...] = ()
    max_proficiency: int = 1000
    practice_gain: float = 1.0       # 熟练度增长系数（高阶功法练得更慢）
    level_growth: float = 0.15       # 每级效果成长（等级由熟练度派生）
    skills: tuple[str, ...] = ()     # 签名技能（见 config/skills.py）

    @property
    def max_level(self) -> int:
        return max(1, self.max_proficiency // LEVEL_PROFICIENCY)

    def budget_cost(self) -> float:
        """本功法的效果点总消耗（用于品阶预算自检）。

        按「练满」计价：等级成长（level_scale(max_level)）会让最终效果高于配置值，
        若只按配置值计价，预算会被等级成长架空 —— 因此这里折算到满级满熟练的最终值。
        """
        total = 0.0
        for eff in self.effects:
            meta = EFFECT_TYPES.get(eff.type)
            if meta is None:
                continue
            value = abs(eff.value)
            if meta.unit == "%":
                value *= 100.0            # 百分比：0.15 → 15 点基准
            if eff.type == "attr_add":
                value *= realm_attr_scale(self.min_realm)   # 固定值按境界量级折算
            total += value * meta.cost
        return total * level_scale(self.max_level, self)


# ---------- 功法表（不同定位） ----------
ARTS: dict[str, ArtDef] = {
    # —— 黄阶·辅助：新手起手，偏悟性与灵力 ——
    "qingxin": ArtDef(
        "qingxin", "清心诀", "黄阶",
        "入门心法，重在凝神静气，打坐时事半功倍。",
        price=200,
        effects=(
            ArtEffect("attr_mul", 0.10, key="comprehension"),
            ArtEffect("attr_add", 5.0, key="max_mp"),
        ),
        practice_gain=1.4, skills=("qingxin_heal",),
    ),
    # —— 黄阶·攻伐：固定值 + 百分比双加成 —
    "gengjin": ArtDef(
        "gengjin", "庚金剑诀", "黄阶",
        "以金气淬剑，出手锋锐。",
        price=400,
        effects=(
            ArtEffect("attr_add", 2.0, key="atk"),
            ArtEffect("attr_mul", 0.12, key="atk"),
        ),
        practice_gain=1.2, skills=("gengjin_strike",),
    ),
    # —— 玄阶·防御：有得有失（身法下降） ——
    "xuangui": ArtDef(
        "xuangui", "玄龟镇海功", "玄阶",
        "守御如山，然身法迟滞，宜稳不宜快。",
        price=1500, min_realm="foundation",
        effects=(
            ArtEffect("attr_mul", 0.20, key="def"),
            ArtEffect("attr_add", 4.0, key="def"),
            ArtEffect("attr_mul", 0.12, key="max_hp"),
            ArtEffect("attr_mul", -0.05, key="speed"),
        ),
        practice_gain=1.0, skills=("xuangui_guard",),
    ),
    # —— 玄阶·爆发：高风险高攻 ——
    "chiyan": ArtDef(
        "chiyan", "赤焰焚天诀", "玄阶",
        "攻伐凌厉，代价是气血亏虚，一击不中反受其害。",
        price=1800, min_realm="foundation",
        effects=(
            ArtEffect("attr_mul", 0.26, key="atk"),
            ArtEffect("attr_add", 3.0, key="atk"),
            ArtEffect("attr_mul", -0.10, key="max_hp"),
        ),
        practice_gain=1.0, skills=("chiyan_burst",),
    ),
    # —— 地阶·续航：灵力与修炼速度（挂机向） ——
    "taixu": ArtDef(
        "taixu", "太虚养神功", "地阶",
        "神识如海，灵力绵绵不绝，最宜久战与长修。",
        price=6000, min_realm="core",
        effects=(
            ArtEffect("attr_mul", 0.25, key="max_mp"),
            ArtEffect("attr_mul", 0.10, key="comprehension"),
            ArtEffect("attr_add", 5.0, key="spirit"),
            ArtEffect("cultivate_speed", 0.12),
        ),
        practice_gain=0.7, skills=("taixu_recover",),
    ),
    # —— 地阶·专修炼：把预算几乎全压在修炼速度上 ——
    "qingmu": ArtDef(
        "qingmu", "青木长生功", "地阶",
        "木行生生不息，吐纳之间灵气自聚，进境虽不快却极稳。",
        price=8000, min_realm="core",
        effects=(
            ArtEffect("cultivate_speed", 0.25),
            ArtEffect("attr_mul", 0.15, key="max_hp"),
            ArtEffect("insight_rate", 0.02),
        ),
        practice_gain=0.8, skills=(),
    ),
    # —— 天阶·全能：面面俱到但都不极端 ——
    "dayan": ArtDef(
        "dayan", "大衍周天诀", "天阶",
        "周天运转，诸脉共鸣，无所不包而无所极。",
        price=30000, min_realm="nascent",
        effects=(
            ArtEffect("attr_mul", 0.12, key="comprehension"),
            ArtEffect("attr_mul", 0.12, key="max_hp"),
            ArtEffect("attr_mul", 0.12, key="max_mp"),
            ArtEffect("attr_mul", 0.12, key="atk"),
            ArtEffect("attr_mul", 0.12, key="def"),
            ArtEffect("cultivate_speed", 0.20),
        ),
        practice_gain=0.5, skills=("dayan_star",),
    ),
    # —— 天阶·冲关：突破率（max 规则）+ 悟道 ——
    "dongxuan": ArtDef(
        "dongxuan", "洞玄破境诀", "天阶",
        "专破瓶颈，然于战力无甚增益，宜在冲关前后修习。",
        price=36000, min_realm="nascent",
        effects=(
            # max 规则：多件功法只取最高，不会因为叠加而破坏突破的难度设计
            ArtEffect("breakthrough_rate", 0.03),
            ArtEffect("insight_rate", 0.04),
            ArtEffect("attr_mul", 0.10, key="spirit"),
        ),
        practice_gain=0.6, skills=(),
    ),
    # —— 仙阶·聚财：灵石向（演示又一种效果类型） ——
    "juyuan": ArtDef(
        "juyuan", "聚源化金诀", "仙阶",
        "点石成金之术，灵石自来，然于道行无益。",
        price=120000, min_realm="human_immortal",
        effects=(
            ArtEffect("stone_gain", 0.20),
            ArtEffect("attr_mul", 0.15, key="luck"),
        ),
        practice_gain=0.6, skills=(),
    ),
    # —— 仙阶·攻伐：仙剑之巅，锋芒毕露 ——
    "xianjian": ArtDef(
        "xianjian", "太虚剑典", "仙阶",
        "仙庭至高剑道，一剑出而万法寂灭，然消耗极大。",
        price=480000, min_realm="human_immortal",
        effects=(
            ArtEffect("attr_mul", 0.40, key="atk"),
            ArtEffect("attr_mul", 0.18, key="speed"),
            ArtEffect("attr_mul", -0.12, key="max_hp"),
        ),
        practice_gain=0.3, skills=("gengjin_strike",),
    ),
    # —— 仙阶·修炼：挂机向，进境如虹 ——
    "xianzhou": ArtDef(
        "xianzhou", "周天妙法", "仙阶",
        "行周天之妙，吐纳之间道韵自聚，进境远胜凡俗。",
        price=520000, min_realm="human_immortal",
        effects=(
            ArtEffect("cultivate_speed", 0.60),
            ArtEffect("attr_mul", 0.15, key="max_hp"),
            ArtEffect("attr_mul", 0.15, key="max_mp"),
            ArtEffect("insight_rate", 0.05),
        ),
        practice_gain=0.4, skills=("taixu_recover",),
    ),
    # —— 仙阶·突破：冲关核心，仙途瓶颈的破壁之钥 ——
    "xianpo": ArtDef(
        "xianpo", "破虚诀", "仙阶",
        "专攻突破人之瓶颈，窥得大道一缕真容，于战力无益。",
        price=560000, min_realm="human_immortal",
        effects=(
            ArtEffect("breakthrough_rate", 0.06),
            ArtEffect("insight_rate", 0.08),
            ArtEffect("attr_mul", 0.12, key="comprehension"),
        ),
        practice_gain=0.35, skills=(),
    ),
    # —— 仙阶·全能：面面俱到而皆臻大成 ——
    "hunyuanzhou": ArtDef(
        "hunyuanzhou", "混元一气诀", "仙阶",
        "混元一气，万法归一，无所不包而无所不极。",
        price=800000, min_realm="taiyi",
        effects=(
            ArtEffect("attr_mul", 0.15, key="comprehension"),
            ArtEffect("attr_mul", 0.15, key="max_hp"),
            ArtEffect("attr_mul", 0.15, key="max_mp"),
            ArtEffect("attr_mul", 0.15, key="atk"),
            ArtEffect("attr_mul", 0.15, key="def"),
            ArtEffect("attr_mul", 0.15, key="speed"),
            ArtEffect("cultivate_speed", 0.40),
            ArtEffect("insight_rate", 0.06),
        ),
        practice_gain=0.25, skills=("dayan_star",),
    ),
}


# ---------- 门派专属功法（「师」与「法」的联动：以贡献兑换，唯本门弟子可修） ----------
SECT_ARTS: dict[str, ArtDef] = {
    # 青云宗·剑修正宗：中庸攻伐，外门即可修
    "qingyun_sword_art": ArtDef(
        "qingyun_sword_art", "青云剑诀", "玄阶",
        "青云宗嫡传剑诀，中正平和，剑势连绵不绝。",
        min_realm="foundation", sect="qingyun", cost_contribution=150,
        effects=(
            ArtEffect("attr_mul", 0.18, key="atk"),
            ArtEffect("attr_add", 3.0, key="atk"),
            ArtEffect("attr_mul", 0.08, key="speed"),
        ),
        practice_gain=1.0, skills=("gengjin_strike",),
    ),
    # 血煞门·魔功：极端攻伐，气血为代价（内门）
    "xuesha_art": ArtDef(
        "xuesha_art", "血煞魔功", "地阶",
        "以血养煞，攻伐凌厉至极，然气血亏虚如附骨之疽。",
        min_realm="core", sect="xuesha", cost_contribution=300,
        effects=(
            ArtEffect("attr_mul", 0.35, key="atk"),
            ArtEffect("attr_mul", -0.15, key="max_hp"),
            ArtEffect("cultivate_speed", 0.10),
        ),
        practice_gain=0.8,
    ),
    # 药王谷·丹道：悟道与体质（内门）
    "yaowang_art": ArtDef(
        "yaowang_art", "药王真解", "地阶",
        "药王谷不传之秘，百草入药，药理入道。",
        min_realm="core", sect="yaowang", cost_contribution=300,
        effects=(
            ArtEffect("insight_rate", 0.05),
            ArtEffect("attr_mul", 0.10, key="physique"),
            ArtEffect("attr_add", 3.0, key="physique"),
            ArtEffect("cultivate_speed", 0.10),
        ),
        practice_gain=0.8,
    ),
    # 天机阁·演算：突破与悟道（真传）
    "tianji_art": ArtDef(
        "tianji_art", "天机演算诀", "天阶",
        "推演天机，趋吉避凶，于瓶颈处窥得一线生机。",
        min_realm="nascent", sect="tianji", cost_contribution=500,
        effects=(
            ArtEffect("breakthrough_rate", 0.03),
            ArtEffect("insight_rate", 0.05),
            ArtEffect("attr_mul", 0.10, key="comprehension"),
            ArtEffect("cultivate_speed", 0.10),
        ),
        practice_gain=0.6,
    ),
    # 剑冢·万剑归宗：天阶攻伐巅峰（真传）
    "jianzhong_art": ArtDef(
        "jianzhong_art", "万剑归宗", "天阶",
        "剑冢至高传承，万剑齐鸣，一剑破万法。",
        min_realm="nascent", sect="jianzhong", cost_contribution=600,
        effects=(
            ArtEffect("attr_mul", 0.30, key="atk"),
            ArtEffect("attr_add", 3.0, key="atk"),
            ArtEffect("attr_mul", 0.08, key="speed"),
            ArtEffect("attr_mul", -0.08, key="max_hp"),
        ),
        practice_gain=0.5, skills=("gengjin_strike",),
    ),
    # 万宝楼·商修：灵石与丹药获取（内门）——「财」与「法」的联动
    "wanbao_art": ArtDef(
        "wanbao_art", "点石成金术", "玄阶",
        "万宝楼秘传敛财之术，灵石自来，于战力无补。",
        min_realm="foundation", sect="wanbao", cost_contribution=150,
        effects=(
            ArtEffect("stone_gain", 0.15),
            ArtEffect("attr_mul", 0.08, key="luck"),
        ),
        practice_gain=1.2,
    ),
}
ARTS.update(SECT_ARTS)

def get_art(art_id: str) -> ArtDef:
    if art_id not in ARTS:
        raise KeyError(f"未知功法: {art_id}")
    return ARTS[art_id]


def slots_for_realm(realm_key: str) -> int:
    """同时可装备的功法数量上限（随境界提升）。"""
    from .realms import RealmRegistry

    idx = RealmRegistry.index_of(realm_key)
    return min(SLOT_CAP_MAX, SLOT_BASE + idx // 3)


def proficiency_scale(proficiency: int, max_proficiency: int) -> float:
    """熟练度对功法效果的缩放系数，区间 [0.6, 1.0]：拿到手六成，练满十成。"""
    ratio = max(0.0, min(1.0, proficiency / max(1, max_proficiency)))
    return 0.6 + 0.4 * ratio


def level_of(proficiency: int, art: ArtDef) -> int:
    """等级由熟练度派生：每 LEVEL_PROFICIENCY 点提升 1 级。"""
    return max(1, min(art.max_level, int(proficiency) // LEVEL_PROFICIENCY + 1))


def level_scale(level: int, art: ArtDef) -> float:
    """等级对效果的成长系数（线性，避免复利失控）。"""
    return 1.0 + art.level_growth * max(0, int(level) - 1)


def realm_attr_scale(realm_key: str) -> float:
    """固定值加成的境界缩放系数 = 该境界累计攻击量级 / 炼气累计量级。

    直接从境界配置累加（数据驱动，不写死系数）：改任何境界的 atk_per_stage，
    固定值加成的缩放会自动跟随，始终保持在基础属性的同一相对水位（实测约 +13%/件）。
    """
    from .realms import REALMS

    def cumulative(target: str) -> float:
        total = 6.0                       # 初始 atk（见 attributes.DEFAULT_BASE）
        for r in REALMS:
            total += r.atk_per_stage * r.stage_count
            if r.key == target:
                break
        return total

    base = cumulative("qi_refining")
    return cumulative(realm_key) / max(1.0, base)


def effective_value(art: ArtDef, eff: ArtEffect, proficiency: int,
                    realm_scale: float = 1.0) -> float:
    """效果实际生效值 = 配置值 × 等级系数 × 熟练度系数（固定值再乘境界缩放）。"""
    level = level_of(proficiency, art)
    scale = realm_scale if eff.type == "attr_add" else 1.0
    return (eff.value * scale * level_scale(level, art)
            * proficiency_scale(proficiency, art.max_proficiency))


def validate_balance() -> list[str]:
    """品阶预算自检：返回超出预算的功法说明（空列表 = 全部合格）。由测试守着。"""
    over: list[str] = []
    for art in ARTS.values():
        budget = RANK_BUDGET.get(art.rank)
        if budget is None:
            over.append(f"{art.name}：未知品阶 {art.rank}")
            continue
        cost = art.budget_cost()
        if cost > budget + 1e-6:
            over.append(f"{art.name}（{art.rank}）效果点 {cost:.0f} > 预算 {budget:.0f}")
    return over
