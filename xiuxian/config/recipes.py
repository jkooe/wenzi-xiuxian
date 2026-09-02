"""丹方配置表。

纯数据，不含判定逻辑（判定在 systems/alchemy.py）。加新丹药三步：
    1. config/items.py 的 ITEMS 里加 pill，并把 id 写进 GRADED_PILLS（自动派生下品/上品）
    2. 本文件 RECIPES 加一条
    3. 完事。炼丹系统不需要改一行代码

字段说明：
    output    产出丹药的基 id，实际产出品阶由炼制时的技术余量决定
    base_rate 基础成功率，未计造诣/悟性/气运/丹炉
    price     丹方售价（灵石）；0 表示只能靠机缘获得，买不到
    hours     每炉耗时（小时）
    stamina   每炉耗精力
    mp        每炉耗灵力
    exp       成丹时获得的修为（失败只给三成，权作火候上的领悟）
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RecipeDef:
    id: str
    name: str
    output: str                                   # 基物品 id（品阶由炼制判定决定）
    inputs: dict[str, int] = field(default_factory=dict)
    base_rate: float = 0.5
    min_realm: str = ""                           # 开炉的境界门槛（空表示无门槛）
    hours: float = 4.0
    stamina: float = 12.0
    mp: float = 8.0
    price: int = 0
    exp: float = 20.0
    desc: str = ""


RECIPES: dict[str, RecipeDef] = {
    "qi_pill": RecipeDef(
        "qi_pill", "聚气丹方", "qi_gathering_pill",
        inputs={"spirit_herb": 2},
        base_rate=0.72, hours=3.0, stamina=6, mp=8, exp=15,
        desc="最粗浅的丹方，初入丹道者皆由此起步。",
    ),
    "heal_pill": RecipeDef(
        "heal_pill", "疗伤丹方", "healing_pill",
        inputs={"spirit_herb": 1, "beast_blood": 1},
        base_rate=0.66, hours=4.0, stamina=6, mp=10, exp=25,
        desc="以妖兽精血为引，止血生肌。",
    ),
    "cleansing_pill": RecipeDef(
        "cleansing_pill", "清毒丹方", "cleansing_pill",
        inputs={"spirit_herb": 3, "beast_blood": 1},
        base_rate=0.55, hours=6.0, stamina=6, mp=18, price=700, exp=60,
        desc="不进药力，专化体内丹毒。嗑药太多时，全靠它收拾残局。",
    ),
    "body_pill": RecipeDef(
        "body_pill", "锻骨丹方", "stonestone",
        inputs={"beast_blood": 3, "beast_core": 1},
        base_rate=0.42, hours=10.0, stamina=6, mp=30, price=1200, exp=150,
        desc="以妖丹之力淬炼筋骨，非一朝一夕可成。",
    ),
    "enlighten_pill": RecipeDef(
        "enlighten_pill", "悟道丹方", "enlighten_pill",
        inputs={"spirit_herb": 4, "beast_core": 1},
        base_rate=0.45, min_realm="foundation",
        hours=8.0, stamina=6, mp=40, price=900, exp=120,
        desc="灵台清明之丹，需筑基以上的神识方能掌控火候。",
    ),
    "foundation_pill": RecipeDef(
        "foundation_pill", "筑基丹方", "foundation_pill",
        inputs={"spirit_herb": 6, "beast_core": 1, "beast_blood": 2},
        base_rate=0.38, hours=14.0, stamina=6, mp=60, price=1600, exp=300,
        desc="结基之丹，材料繁复，火候稍有差池便前功尽弃。",
    ),
    "core_pill": RecipeDef(
        "core_pill", "结金丹方", "golden_core_pill",
        inputs={"spirit_herb": 10, "beast_core": 4, "jade_bottle": 1},
        base_rate=0.28, min_realm="foundation",
        hours=24.0, stamina=6, mp=120, price=5000, exp=800,
        desc="凝金丹之机，须以玉瓶承接，否则药气散逸。",
    ),
    # —— 仙阶丹方：飞升后炼丹主线，材料用仙界材料 ——
    "immortal_pill_rec": RecipeDef(
        "immortal_pill_rec", "蕴仙丹方", "immortal_pill",
        inputs={"immortal_herb": 4, "immortal_core": 2},
        base_rate=0.40, min_realm="human_immortal",
        hours=36.0, stamina=12, mp=200, price=20000, exp=5000,
        desc="以仙元温养仙体，丹成一枚便抵凡俗数月之功。",
    ),
    "heaven_pill_rec": RecipeDef(
        "heaven_pill_rec", "破境仙丹方", "heaven_pill",
        inputs={"immortal_herb": 8, "immortal_core": 5, "jade_bottle": 2},
        base_rate=0.24, min_realm="human_immortal",
        hours=72.0, stamina=18, mp=500, price=90000, exp=15000,
        desc="引天道本源入丹，非仙风道骨不能控其火候，突破之凭。",
    ),
}

# 开局自带：最基础的两张丹方，其余靠灵石购买或机缘（事件 effect "recipe"）
STARTING_RECIPES: tuple[str, ...] = ("qi_pill", "heal_pill")


def get_recipe(recipe_id: str) -> RecipeDef:
    if recipe_id not in RECIPES:
        raise KeyError(f"未知丹方: {recipe_id}")
    return RECIPES[recipe_id]
