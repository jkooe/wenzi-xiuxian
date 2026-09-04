"""物品与丹药配置表。

kind 说明：
    pill      丹药（可服用，会累积丹毒）
    material  材料（任务/炼丹用）
    treasure  天材地宝（可直接使用）
    equip     装备（穿戴后经 Modifier 注入属性，卸下即失效）

effects 使用效果 DSL，语法见 core/effects.py。
breakthrough_bonus：在突破结算时提供的成功率加成（键为目标境界 key，"*" 为通用）。
equip_add / equip_mul：装备穿戴后的固定加成与百分比加成。
alchemy_bonus：装备为丹炉时提供的炼丹成功率加成。

品阶（下品/中品/上品）：只有 pill 有品阶，由炼丹结果决定，不手写三份表——
    中品即基准物品（id 不变，老存档兼容），下品/上品用 dataclasses.replace 程序化派生，
    id 形如 "qi_gathering_pill#low"，药效与丹毒按品阶倍率缩放。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class ItemDef:
    id: str
    name: str
    kind: str
    desc: str = ""
    stack: int = 99
    price: int = 0                       # 灵石价
    usable: bool = False
    effects: tuple[dict[str, Any], ...] = ()
    breakthrough_bonus: dict[str, float] = field(default_factory=dict)
    poison: float = 0.0                  # 服用累积的丹毒
    equip_slot: str = ""                 # kind=equip 时的部位
    equip_add: dict[str, float] = field(default_factory=dict)   # 穿戴后的固定加成
    equip_mul: dict[str, float] = field(default_factory=dict)   # 穿戴后的百分比加成（1.2 = +20%）
    min_realm: str = ""                  # 穿戴的境界门槛（境界 key，空表示无门槛）
    alchemy_bonus: float = 0.0           # 丹炉类装备的炼丹成功率加成

    # ---------- 派生属性 ----------
    @property
    def grade_key(self) -> str:
        """本物品的品阶键（无后缀即中品）。"""
        return parse_graded(self.id)[1]

    @property
    def grade_label(self) -> str:
        return GRADES[self.grade_key].label


# ---------- 丹药品阶 ----------
@dataclass(frozen=True)
class PillGrade:
    key: str
    label: str
    potency: float          # 药效倍率（hp/exp/attr/突破加成全部按此缩放）
    poison_scale: float     # 丹毒倍率：药力越猛，毒性越重
    price_scale: float


GRADES: dict[str, PillGrade] = {
    "low":  PillGrade("low",  "下品", 0.70, 0.60, 0.45),
    "mid":  PillGrade("mid",  "中品", 1.00, 1.00, 1.00),
    "high": PillGrade("high", "上品", 1.40, 1.40, 2.00),
}
GRADE_ORDER: tuple[str, ...] = ("high", "mid", "low")   # 展示与择优排序：从好到差
BASE_GRADE = "mid"          # 基准品阶，id 不带后缀


def parse_graded(item_id: str) -> tuple[str, str]:
    """拆 "xxx#low" -> ("xxx", "low")；无后缀 -> ("xxx", "mid")。"""
    if "#" in item_id:
        base, _, grade = item_id.partition("#")
        if grade in GRADES:
            return base, grade
    return item_id, BASE_GRADE


def graded_id(base_id: str, grade: str) -> str:
    """拼品阶 id；中品回落到基 id，保证老存档与掉落表不受影响。"""
    if grade == BASE_GRADE:
        return base_id
    return f"{base_id}#{grade}"


ITEMS: dict[str, ItemDef] = {
    # ---------- 丹药 ----------
    "qi_gathering_pill": ItemDef(
        "qi_gathering_pill", "聚气丹", "pill",
        "初阶修士常用，可补充灵力。",
        price=20, usable=True, poison=1.0,
        effects=(
            {"type": "mp_ratio", "value": 0.5},
            # 修为按「本层需求比例」给：写固定值在炼气够用、到渡劫等于零
            {"type": "exp_ratio", "value": 0.02},
        ),
    ),
    "healing_pill": ItemDef(
        "healing_pill", "疗伤丹", "pill",
        "止血生肌，恢复三成气血。",
        price=30, usable=True, poison=1.0,
        effects=({"type": "hp_ratio", "value": 0.35},),
    ),
    "foundation_pill": ItemDef(
        "foundation_pill", "筑基丹", "pill",
        "筑基期修士突破必备，可大幅提升结基之机。",
        price=800, usable=True, poison=3.0,
        breakthrough_bonus={"foundation": 0.25},
        effects=({"type": "log", "text": "药力沉于丹田，静待突破之时。"},),
    ),
    "golden_core_pill": ItemDef(
        "golden_core_pill", "结金丹", "pill",
        "凝结金丹之机，可增三成胜算。",
        price=6000, usable=True, poison=6.0,
        breakthrough_bonus={"core": 0.30},
        effects=({"type": "log", "text": "金丹之气隐隐搏动。"},),
    ),
    "enlighten_pill": ItemDef(
        "enlighten_pill", "悟道丹", "pill",
        "短期内灵台清明，修炼速度大增。",
        price=500, usable=True, poison=4.0,
        effects=(
            {"type": "buff", "id": "enlighten", "hours": 72,
             "name": "悟道", "mul": {"comprehension": 1.3}},
        ),
    ),
    "stonestone": ItemDef(
        "stonestone", "锻骨丹", "pill",
        "淬炼肉身，永久提升根骨。",
        price=1500, usable=True, poison=5.0,
        effects=({"type": "attr", "key": "physique", "value": 2},),
    ),
    "cleansing_pill": ItemDef(
        "cleansing_pill", "清毒丹", "pill",
        "不进药力，专化体内沉积的丹毒。",
        price=400, usable=True, poison=0.0,
        effects=({"type": "poison", "value": -15},),
    ),

    # ---------- 仙界丹药（第 28 批：仙界灵石消耗点） ----------
    # 设计定位：仙俸以灵石发放、仙界却无专属高价消耗，灵石在仙界易堆成死数。
    #   这两枚仙丹即仙界灵石出口，且都被天然闸门锁死、不破坏 1170 天节奏：
    #     · 法则感悟丹 走 insight_hours —— 自动受 INSIGHT_DAILY_LIMIT=2 每日硬闸（与事件共享），
    #       且单价 30000 灵石让「全买派」被灵石收入天然限速（验证见 calibrate）。
    #     · 仙体丹 永久 +3 悟性 —— 仙界 power sink，不碰法则门槛，只让悟道略快。
    "law_insight_pill": ItemDef(
        "law_insight_pill", "法则感悟丹", "pill",
        "仙界秘炼，服之如静悟八时辰，法则感悟自生（受每日感悟上限约束）。",
        price=30000, usable=True, min_realm="earth_immortal", poison=0.0,
        effects=({"type": "insight_hours", "value": 8},),
    ),
    "immortal_body_pill": ItemDef(
        "immortal_body_pill", "仙体丹", "pill",
        "温养仙骨，永久提升悟性三点，仙途更顺。",
        price=20000, usable=True, min_realm="earth_immortal", poison=0.0,
        effects=({"type": "attr", "key": "comprehension", "value": 3},),
    ),

    # ---------- 天材地宝 ----------
    "spirit_water": ItemDef(
        "spirit_water", "灵泉水", "treasure",
        "蕴含精纯灵气，饮之修为暴涨。",
        price=200, usable=True,
        effects=({"type": "exp_ratio", "value": 0.05}, {"type": "mp_ratio", "value": 0.3}),
    ),
    "beast_core": ItemDef(
        "beast_core", "妖丹", "material",
        "妖兽内丹，炼丹与宗门任务的硬通货。", price=300, stack=99,
    ),
    "beast_blood": ItemDef(
        "beast_blood", "妖兽精血", "material",
        "可炼制淬体药剂。", price=120, stack=99,
    ),
    "jade_bottle": ItemDef(
        "jade_bottle", "玉瓶", "material",
        "盛装灵液之物。", price=50, stack=99,
    ),
    "spirit_herb": ItemDef(
        "spirit_herb", "灵草", "material",
        "常见炼丹材料。", price=60, stack=99,
    ),

    # ---------- 装备（穿戴后经 Modifier 注入属性） ----------
    "qingfeng_sword": ItemDef(
        "qingfeng_sword", "青锋剑", "equip",
        "凡铁铸就的飞剑，轻快趁手。", price=400, equip_slot="weapon",
        equip_add={"atk": 6},
    ),
    "xuantheavy_sword": ItemDef(
        "xuantheavy_sword", "玄铁重剑", "equip",
        "重逾百斤，一剑之威惊人，然施展迟缓。", price=1200,
        equip_slot="weapon", min_realm="foundation",
        equip_add={"atk": 16, "speed": -3},
    ),
    "chainmail": ItemDef(
        "chainmail", "锁子甲", "equip",
        "铁环相扣，护住要害。", price=350, equip_slot="armor",
        equip_add={"def": 4, "max_hp": 25},
    ),
    "guard_mirror": ItemDef(
        "guard_mirror", "玄龟护心镜", "equip",
        "玄龟甲所制，厚重非常，代价是身法迟滞。", price=1500,
        equip_slot="armor", min_realm="foundation",
        equip_add={"def": 9, "max_hp": 60, "speed": -2},
    ),
    "windwalker_boots": ItemDef(
        "windwalker_boots", "御风履", "equip",
        "踏之生风，进退自如。", price=300, equip_slot="boots",
        equip_add={"speed": 5},
    ),
    "jade_pendant": ItemDef(
        "jade_pendant", "护身玉佩", "equip",
        "温润护主，可御片刻之敌。", price=500, equip_slot="accessory",
        equip_add={"max_hp": 20, "def": 2},
    ),
    "spirit_belt": ItemDef(
        "spirit_belt", "聚灵腰带", "equip",
        "腰缠灵蚕丝，灵力自生，悟道事半功倍。", price=800, equip_slot="belt",
        equip_add={"max_mp": 25, "comprehension": 2},
    ),
    "gourd_treasure": ItemDef(
        "gourd_treasure", "青囊葫芦", "equip",
        "内藏乾坤，可蓄灵气，亦壮神识。", price=2000,
        equip_slot="treasure", min_realm="foundation",
        equip_add={"max_mp": 40, "spirit": 3},
    ),

    # ---------- 丹炉（占法宝位，与葫芦二选一）----------
    "clay_cauldron": ItemDef(
        "clay_cauldron", "紫砂药鼎", "equip",
        "寻常药鼎，蓄火尚稳，聊胜于无。", price=600,
        equip_slot="treasure", alchemy_bonus=0.06,
        equip_add={},
    ),
    "cinnabar_furnace": ItemDef(
        "cinnabar_furnace", "朱雀丹炉", "equip",
        "炉腹刻有朱雀纹，火候自调，成丹之机大增。", price=1800,
        equip_slot="treasure", min_realm="foundation",
        alchemy_bonus=0.12,
        equip_add={"spirit": 2},
    ),

    # ---------- 仙界材料（仙兽/仙草，秘境与仙阶丹方用） ----------
    "immortal_core": ItemDef(
        "immortal_core", "仙兽内丹", "material",
        "太古异种体内凝结的纯净仙元，仙阶炼丹与仙器淬炼的硬通货。",
        price=2000, stack=99,
    ),
    "immortal_herb": ItemDef(
        "immortal_herb", "仙草", "material",
        "生长于仙灵之地的灵植，蕴含精纯道韵，仙阶丹药的主药。",
        price=800, stack=99,
    ),

    # ---------- 仙界丹药：渡劫圆满后继续冲关的主线支持 ----------
    "immortal_pill": ItemDef(
        "immortal_pill", "蕴仙丹", "pill",
        "以仙元温养仙体，修为自生，是飞升后最常见的补充。",
        price=8000, usable=True, poison=4.0,
        effects=(
            {"type": "exp_ratio", "value": 0.08},
            {"type": "mp_ratio", "value": 0.5},
        ),
    ),
    "heaven_pill": ItemDef(
        "heaven_pill", "破境仙丹", "pill",
        "引动一缕天道本源，飞升后冲击新境界时几可点石成金。",
        price=60000, usable=True, poison=12.0,
        breakthrough_bonus={"human_immortal": 0.15, "earth_immortal": 0.15,
                            "heaven_immortal": 0.12, "mystic_immortal": 0.12,
                            "gold_immortal": 0.10, "taiyi": 0.10,
                            "luo_po": 0.08, "quasi_saint": 0.08, "hunyuan": 0.06},
        effects=({"type": "log", "text": "一缕仙韵沉于丹田，待突破之时引动。"},),
    ),

    # ---------- 仙界装备（飞升后秘境/战斗掉落；属性量级对齐仙界境界，经境界缩放可比） ----------
    "immortal_sword": ItemDef(
        "immortal_sword", "斩仙飞剑", "equip",
        "仙金所铸，剑出则一缕寒芒直指仙庭。", price=200000,
        equip_slot="weapon", min_realm="human_immortal",
        equip_add={"atk": 300000},
    ),
    "immortal_armor": ItemDef(
        "immortal_armor", "仙灵战甲", "equip",
        "经纬皆由仙丝织就，轻若无物却坚逾山峦。", price=180000,
        equip_slot="armor", min_realm="human_immortal",
        equip_add={"def": 200000, "max_hp": 1500000},
    ),
    "immortal_boots": ItemDef(
        "immortal_boots", "踏虚履", "equip",
        "踏之足下生云，一步跨出已在万里之外。", price=150000,
        equip_slot="boots", min_realm="human_immortal",
        equip_add={"speed": 150000},
    ),
    "immortal_pendant": ItemDef(
        "immortal_pendant", "天心玉佩", "equip",
        "佩之灵台澄澈，道韵自生，挡一次致命之击。", price=200000,
        equip_slot="accessory", min_realm="human_immortal",
        equip_add={"max_hp": 1000000, "def": 80000},
    ),
    "immortal_belt": ItemDef(
        "immortal_belt", "仙元腰带", "equip",
        "腰系仙元，吐纳周流，恍若随身带了一条灵脉。", price=170000,
        equip_slot="belt", min_realm="human_immortal",
        equip_add={"max_mp": 1500000, "comprehension": 200},
    ),
    "immortal_treasure": ItemDef(
        "immortal_treasure", "紫霄仙塔", "equip",
        "内藏一方小界，可蓄灵亦可镇敌，仙器之属。", price=300000,
        equip_slot="treasure", min_realm="human_immortal",
        equip_add={"max_mp": 2500000, "spirit": 500},
    ),
}

# 装备部位：内部键 -> 显示名
EQUIP_SLOTS: dict[str, str] = {
    "weapon": "兵器",
    "armor": "护甲",
    "boots": "靴履",
    "accessory": "佩饰",
    "belt": "腰带",
    "treasure": "法宝",
}


# ---------- 品阶派生 ----------
# 需要品阶的丹药：只写基表，下品/上品由程序派生，加新丹药只需往这里添 id
GRADED_PILLS: tuple[str, ...] = (
    "qi_gathering_pill",
    "healing_pill",
    "foundation_pill",
    "golden_core_pill",
    "enlighten_pill",
    "stonestone",
    "cleansing_pill",
    "immortal_pill",
    "heaven_pill",
)

# poison 也按药效缩放：上品药力猛，毒性也重；清毒丹是负值，缩放后化毒更多
# 品阶缩放需覆盖所有数值型效果；exp_ratio（按需求比例）同样要按药效缩放，
# 否则下品/上品丹药的修为比例会完全相同，品阶意义丢失。
_SCALED_VALUE_TYPES = {"exp", "exp_ratio", "hp", "mp", "stamina", "hp_ratio",
                       "mp_ratio", "attr", "poison"}


def _scale_effect(eff: dict[str, Any], potency: float) -> dict[str, Any]:
    """按药效倍率缩放一条效果。buff 的百分比按「偏离 1 的部分」缩放，与功法熟练度同法。"""
    out = dict(eff)
    etype = out.get("type")
    if etype in _SCALED_VALUE_TYPES:
        out["value"] = round(float(out.get("value", 0)) * potency, 4)
    elif etype == "buff":
        if out.get("add"):
            out["add"] = {k: round(v * potency, 4) for k, v in out["add"].items()}
        if out.get("mul"):
            out["mul"] = {k: round(1 + (v - 1) * potency, 4) for k, v in out["mul"].items()}
        if out.get("hours"):
            out["hours"] = round(float(out["hours"]) * potency, 2)
    return out


def make_grade_variant(base: ItemDef, grade: str) -> ItemDef:
    """由中品基表派生出下品/上品，避免手抄三份表导致数值失衡。"""
    g = GRADES[grade]
    return replace(
        base,
        id=graded_id(base.id, grade),
        name=f"{g.label}·{base.name}",
        desc=f"{base.desc}（{g.label}，药力 {g.potency:.0%}）",
        price=max(1, int(round(base.price * g.price_scale))),
        effects=tuple(_scale_effect(e, g.potency) for e in base.effects),
        breakthrough_bonus={k: round(v * g.potency, 4)
                            for k, v in base.breakthrough_bonus.items()},
        poison=round(base.poison * g.poison_scale, 2),
    )


def _inject_graded_pills() -> None:
    for base_id in GRADED_PILLS:
        base = ITEMS[base_id]
        for grade in GRADES:
            if grade == BASE_GRADE:
                continue
            ITEMS[graded_id(base_id, grade)] = make_grade_variant(base, grade)


_inject_graded_pills()


def get_item(item_id: str) -> ItemDef:
    if item_id not in ITEMS:
        raise KeyError(f"未知物品: {item_id}")
    return ITEMS[item_id]


def has_item(item_id: str) -> bool:
    return item_id in ITEMS


def base_of(item_id: str) -> ItemDef:
    """取品阶物品的基表（下品·聚气丹 -> 聚气丹）。"""
    return ITEMS[parse_graded(item_id)[0]]
