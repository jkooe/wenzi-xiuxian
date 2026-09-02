"""境界体系配置。

层级结构：大境界（炼气/筑基/金丹……）-> 小境界（炼气九层 / 筑基初期~圆满）。

两种突破：
    小境界突破（层内进阶）：几乎必成，只耗修为。
    大境界突破（跨境界）  ：有失败率、可嗑丹、金丹以上还会引动天劫。

凡界九境（炼气 -> 渡劫）之后接仙界九境（人仙 -> 混元）：
    渡劫圆满突破即「飞升」，进入人仙。飞升不是终点——法无止境，仙是修不完的。
    仙界跨境界突破继续渡劫，名目分层（仙劫 / 天衰之劫 / 道心劫 / 斩三尸 / 合道大劫）。

境界神通（POWERS）：每境一个数据驱动被动，跨境界突破时注入 Modifier 生效。
    数值型（add/mul）走属性管线；比率型（炼丹/修炼/渡劫/突破加成）由各系统按境界查表。

寿元（lifespan）：纯展示。挂机养老定位，无寿元耗尽死亡（game.py 不判定）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RealmDef:
    key: str                       # 内部标识
    name: str                      # 显示名
    stages: tuple[str, ...]        # 小境界名称
    hp_per_stage: float            # 每层气血成长
    mp_per_stage: float
    atk_per_stage: float
    def_per_stage: float
    spirit_per_stage: float
    exp_base: float                # 该大境界第一层所需修为
    exp_growth: float              # 每深一层，需求 ×= exp_growth
    major_success: float           # 突破到本大境界（跨境界）的基础成功率
    lifespan: int                  # 该境界的寿元（纯展示，无死亡判定）
    title: str = ""                # 尊号，如「真人」「元君」
    tribulation: str = "天劫"       # 跨境界突破时的劫名（金丹以上用，仙界分层名目）

    @property
    def stage_count(self) -> int:
        return len(self.stages)


@dataclass(frozen=True)
class PowerDef:
    """境界神通：跨境界突破时授予的永久被动。

    add/mul 走 Modifier 属性管线；四个比率加成由对应系统按玩家当前境界查表：
        alchemy_bonus     炼丹成功率加成
        cultivation_bonus 修炼产出加成（挂机友好，纯增益）
        tribulation_bonus 渡劫成功率加成
        breakthrough_bonus 突破成功率加成
    """

    name: str
    desc: str
    add: dict[str, float] = field(default_factory=dict)
    mul: dict[str, float] = field(default_factory=dict)
    alchemy_bonus: float = 0.0
    cultivation_bonus: float = 0.0
    tribulation_bonus: float = 0.0
    breakthrough_bonus: float = 0.0


QI_STAGES = ("一层", "二层", "三层", "四层", "五层", "六层", "七层", "八层", "九层")
TIER_STAGES = ("初期", "中期", "后期", "圆满")

REALMS: tuple[RealmDef, ...] = (
    # ---------------- 凡界九境 ----------------
    RealmDef("qi_refining", "炼气", QI_STAGES,
             12, 20, 2, 1, 1, exp_base=100,  exp_growth=1.35, major_success=0.90,
             lifespan=100, title="修士"),
    RealmDef("foundation", "筑基", TIER_STAGES,
             70, 90, 8, 5, 4, exp_base=1500, exp_growth=1.60, major_success=0.55,
             lifespan=200, title="修士"),
    RealmDef("core", "金丹", TIER_STAGES,
             320, 400, 30, 18, 15, exp_base=12000, exp_growth=1.75, major_success=0.45,
             lifespan=500, title="真人", tribulation="天劫"),
    RealmDef("nascent", "元婴", TIER_STAGES,
             1400, 1800, 110, 70, 60, exp_base=110_000, exp_growth=1.80, major_success=0.40,
             lifespan=1000, title="元君", tribulation="天劫"),
    RealmDef("deity", "化神", TIER_STAGES,
             6000, 8000, 420, 260, 220, exp_base=1_000_000, exp_growth=1.85, major_success=0.34,
             lifespan=2000, title="神君", tribulation="天劫"),
    RealmDef("void", "炼虚", TIER_STAGES,
             26_000, 34_000, 1600, 1000, 800, exp_base=9_000_000, exp_growth=1.90,
             major_success=0.30, lifespan=5000, title="虚尊", tribulation="天劫"),
    RealmDef("integration", "合体", TIER_STAGES,
             110_000, 150_000, 6000, 3800, 3000, exp_base=80_000_000, exp_growth=1.95,
             major_success=0.26, lifespan=10_000, title="道尊", tribulation="天劫"),
    RealmDef("mahayana", "大乘", TIER_STAGES,
             460_000, 640_000, 22_000, 14_000, 11_000, exp_base=700_000_000,
             exp_growth=2.00, major_success=0.22, lifespan=30_000, title="道主",
             tribulation="天劫"),
    RealmDef("ascension", "渡劫", ("初期", "中期", "后期"),
             2_000_000, 2_800_000, 80_000, 50_000, 40_000, exp_base=6_000_000_000,
             exp_growth=2.10, major_success=0.18, lifespan=100_000, title="仙尊",
             tribulation="天劫"),
    # ---------------- 仙界九境（渡劫圆满 -> 飞升人仙） ----------------
    # 属性每境约 ×4，需求延续指数延伸，突破率继续递减；寿元为纯展示（挂机养老无死亡）
    RealmDef("human_immortal", "人仙", TIER_STAGES,
             8_000_000, 11_000_000, 300_000, 200_000, 150_000,
             exp_base=60_000_000_000, exp_growth=2.20, major_success=0.15,
             lifespan=1_000_000, title="仙士", tribulation="仙劫"),
    RealmDef("earth_immortal", "地仙", TIER_STAGES,
             32_000_000, 45_000_000, 1_200_000, 800_000, 600_000,
             exp_base=400_000_000_000, exp_growth=2.30, major_success=0.13,
             lifespan=5_000_000, title="仙师", tribulation="仙劫"),
    RealmDef("heaven_immortal", "天仙", TIER_STAGES,
             130_000_000, 180_000_000, 5_000_000, 3_200_000, 2_400_000,
             exp_base=3_000_000_000_000, exp_growth=2.40, major_success=0.11,
             lifespan=20_000_000, title="真君", tribulation="天衰之劫"),
    RealmDef("mystic_immortal", "玄仙", TIER_STAGES,
             520_000_000, 720_000_000, 20_000_000, 13_000_000, 9_500_000,
             exp_base=25_000_000_000_000, exp_growth=2.50, major_success=0.09,
             lifespan=80_000_000, title="仙君", tribulation="天衰之劫"),
    RealmDef("gold_immortal", "金仙", TIER_STAGES,
             2_100_000_000, 2_900_000_000, 80_000_000, 52_000_000, 38_000_000,
             exp_base=200_000_000_000_000, exp_growth=2.60, major_success=0.08,
             lifespan=300_000_000, title="仙王", tribulation="道心劫"),
    RealmDef("taiyi", "太乙", TIER_STAGES,
             8_400_000_000, 12_000_000_000, 320_000_000, 210_000_000, 150_000_000,
             exp_base=1_500_000_000_000_000, exp_growth=2.70, major_success=0.07,
             lifespan=1_000_000_000, title="仙帝", tribulation="道心劫"),
    RealmDef("luo_po", "大罗", TIER_STAGES,
             34_000_000_000, 48_000_000_000, 1_300_000_000, 840_000_000, 600_000_000,
             exp_base=10_000_000_000_000_000, exp_growth=2.80, major_success=0.06,
             lifespan=3_000_000_000, title="仙尊", tribulation="斩三尸"),
    RealmDef("quasi_saint", "准圣", TIER_STAGES,
             140_000_000_000, 190_000_000_000, 5_200_000_000, 3_400_000_000, 2_400_000_000,
             exp_base=60_000_000_000_000_000, exp_growth=2.90, major_success=0.05,
             lifespan=10_000_000_000, title="帝尊", tribulation="合道大劫"),
    RealmDef("hunyuan", "混元", TIER_STAGES,
             560_000_000_000, 760_000_000_000, 21_000_000_000, 14_000_000_000, 9_500_000_000,
             exp_base=300_000_000_000_000_000, exp_growth=3.00, major_success=0.04,
             lifespan=10_000_000_000, title="道祖", tribulation="无劫"),
)

BY_KEY: dict[str, RealmDef] = {r.key: r for r in REALMS}
ORDER: tuple[str, ...] = tuple(r.key for r in REALMS)

# 跨大境界突破时的「境界铭文」——纯文案，写数据层不写逻辑：
# 每踏入一个大境界，日志里多一句意境描述。想换文风只改这张表。
REVELATIONS: dict[str, str] = {
    "qi_refining": "气海初开，天地灵气丝丝入体，修行之路自此而始。",
    "foundation": "道基初成，浊气尽去，灵台清明，脱胎换骨。",
    "core": "丹田紫气氤氲，凝成一枚金丹，内视如观日月。",
    "nascent": "金丹碎裂，一道元婴自识海踏出，从此夺舍避死、神通自成。",
    "deity": "元婴与天地相合，神识漫过群山，一念千里。",
    "void": "炼虚合道，虚实之间自有真意，遁入虚空如履平地。",
    "integration": "万法归一，诸相皆空，举手投足暗合天理。",
    "mahayana": "一步一莲，大道在望，尘世因果渐渐淡去。",
    "ascension": "九霄雷动，凡身蜕尽，只待天劫临门，一朝飞升。",
    "human_immortal": "凡躯蜕尽，仙灵灌体，你自九霄之上俯瞰人间——仙途自此而始。",
    "earth_immortal": "大地母气凝于身，仙体由虚转实，隐有山岳之重。",
    "heaven_immortal": "天人合一，仙元周流不息，举手投足皆合天道。",
    "mystic_immortal": "玄之又玄，众妙之门，你窥见法则的一线真容。",
    "gold_immortal": "法则之丝交织如网，一方天地为你所辖，仙域初成。",
    "taiyi": "太乙道果凝成，万法不侵，一念可开一方小界。",
    "luo_po": "跳出时光长河，斩断因果牵连，自此不朽不灭。",
    "quasi_saint": "道身初成，半步圣人，大道本源向你敞开。",
    "hunyuan": "与道合真，不死不灭，你就是道的化身。",
}
TIER4_AND_ABOVE = ORDER[2:]  # 金丹及以上：突破引动天劫（仙界天然包含在内）


# 境界神通表：key -> PowerDef。炼气/筑基为入门阶段，无神通；金丹起每境一个。
# 跨境界突破进入新境界时，由 cultivation 注入 Modifier（source="realm:<key>"）。
POWERS: dict[str, PowerDef] = {
    "core": PowerDef("丹心", "金丹凝成，丹火纯青，炼丹成功率 +6%。", alchemy_bonus=0.06),
    "nascent": PowerDef("元灵", "元婴自成，灵元浩瀚，灵力上限 +12%。", mul={"max_mp": 1.12}),
    "deity": PowerDef("神游", "神识漫过群山，一念千里，神识 +30。", add={"spirit": 30}),
    "void": PowerDef("虚遁", "虚实之间自有真意，身法 +15%。", mul={"speed": 1.15}),
    "integration": PowerDef("道体", "法体合一，吐纳天地，修炼产出 +10%。",
                            cultivation_bonus=0.10),
    "mahayana": PowerDef("法相", "法相天地，全属性 +8%。",
                         mul={"max_hp": 1.08, "max_mp": 1.08, "atk": 1.08, "def": 1.08,
                              "speed": 1.08, "spirit": 1.08}),
    "ascension": PowerDef("劫体", "九霄雷劫淬体，渡劫成功率 +8%。", tribulation_bonus=0.08),
    "human_immortal": PowerDef("仙体", "仙灵灌体，气血上限 +10%。", mul={"max_hp": 1.10}),
    "earth_immortal": PowerDef("仙灵", "仙元浩瀚，灵力上限 +12%。", mul={"max_mp": 1.12}),
    "heaven_immortal": PowerDef("法则之丝", "窥见法则，攻击 +12%。", mul={"atk": 1.12}),
    "mystic_immortal": PowerDef("道印", "道印护身，防御 +12%。", mul={"def": 1.12}),
    "gold_immortal": PowerDef("仙域", "领域初成，修炼产出 +15%。", cultivation_bonus=0.15),
    "taiyi": PowerDef("道果", "太乙道果，神识 +80。", add={"spirit": 80}),
    "luo_po": PowerDef("不朽之躯", "跳出时光长河，气血上限 +20%。", mul={"max_hp": 1.20}),
    "quasi_saint": PowerDef("言出法随", "大道亲近，突破成功率 +5%。", breakthrough_bonus=0.05),
    "hunyuan": PowerDef("与道合真", "不死不灭，全属性 +15%。",
                        mul={"max_hp": 1.15, "max_mp": 1.15, "atk": 1.15, "def": 1.15,
                             "speed": 1.15, "spirit": 1.15}),
}


def power_of(realm_key: str) -> PowerDef | None:
    """当前境界的神通（无则为 None）。"""
    return POWERS.get(realm_key)


class RealmRegistry:
    """境界查询与比较工具。"""

    @staticmethod
    def get(key: str) -> RealmDef:
        if key not in BY_KEY:
            raise KeyError(f"未知境界: {key}")
        return BY_KEY[key]

    @staticmethod
    def index_of(key: str) -> int:
        return ORDER.index(key)

    @staticmethod
    def next_realm(key: str) -> RealmDef | None:
        i = ORDER.index(key)
        return REALMS[i + 1] if i + 1 < len(REALMS) else None

    @staticmethod
    def is_last(key: str) -> bool:
        return key == ORDER[-1]

    @staticmethod
    def in_immortal_realm(key: str) -> bool:
        """是否已飞升仙界（人仙及以上）。"""
        return RealmRegistry.index_of(key) >= RealmRegistry.index_of("human_immortal")

    @staticmethod
    def within(key: str, min_realm: str | None = None, max_realm: str | None = None) -> bool:
        i = ORDER.index(key)
        if min_realm and i < ORDER.index(min_realm):
            return False
        if max_realm and i > ORDER.index(max_realm):
            return False
        return True

    @staticmethod
    def global_stage_index(realm_key: str, stage: int) -> int:
        """把 (大境界, 层) 折算成全局序号，用于强弱比较。"""
        total = 0
        for r in REALMS:
            if r.key == realm_key:
                return total + stage
            total += r.stage_count
        raise KeyError(realm_key)

    @staticmethod
    def stage_exp_required(realm: RealmDef, stage: int) -> float:
        """该大境界第 stage 层（0-based）升下一层所需修为。"""
        return round(realm.exp_base * (realm.exp_growth ** stage), 1)

    @staticmethod
    def full_name(realm_key: str, stage: int) -> str:
        r = BY_KEY[realm_key]
        return f"{r.name}{r.stages[stage]}"


# 灵气浓度：不同地点的修炼倍率，扩展地图时只改这里
LOCATIONS: dict[str, dict[str, float]] = {
    # v2：凶险机制已取消，地点只影响灵气倍率与机缘率（无危险惩罚）
    "青石镇": {"density": 1.0, "event_rate": 0.45},
    "落云山脉": {"density": 1.6, "event_rate": 0.65},
    "万兽深渊": {"density": 2.3, "event_rate": 0.80},
    "宗门灵脉": {"density": 3.0, "event_rate": 0.35},
    # 仙界地点：飞升后才可涉足，灵气密度更高（值经仙界需求比例推导，不破坏凡界节奏）。
    # min_realm 键由 game.travel 做境界门槛；不加则默认无门槛。
    "瑶池": {"density": 4.5, "event_rate": 0.40, "min_realm": "human_immortal"},
    "万劫云海": {"density": 6.0, "event_rate": 0.30, "min_realm": "gold_immortal"},
}
