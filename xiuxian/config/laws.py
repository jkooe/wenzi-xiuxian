"""仙界法则配置（第二十七批：仙界深度重构）。

设计定位
--------
凡界修命，仙界修法。

    凡界：打坐攒修为 ──► 突破          （单轴，修为驱动）
    仙界：打坐攒修为(快) + 悟道攒法则(慢) ──► 突破   （双轴）

这是仙与凡的本质分野，也是本批**唯一的结构性改动**：
仙界突破从「只看修为」变成「修为 + 法则进度」（软门槛，见 LAW_GATE_PENALTY）。

为什么节奏能自然分层（而不是硬拉 exp_growth）
---------------------------------------------
产出挂「本层需求比例」（项目铁律），每层所需时辰 = 1/(0.002×密度) ≈ 常数，
与 exp_growth 无关 —— 这正是第 26 批诊断出的「仙界＝凡界节奏」根因。

本批不改这个铁律，而是**加第二条轴**：仙界灵气密度高（4.5~6.0），修为很快圆满；
剩下的时间玩家必须拿去「悟道」攒法则进度，才够突破门槛。
于是「仙途漫漫」由玩法自然产生，不是靠数值硬拉 —— 且卡境时有事可做。

节奏账（目标：仙界九境共约 1170 天）
------------------------------------
    每境停留 = 修为时间 + 悟道时间
    修为时间 ≈ 15 天/境（密度 4.5~6.0，四层打坐）
    悟道时间 = 目标停留 - 修为时间  ──► 占全程约 88%，是仙界时间的主要去处

为什么是乘区而不是加区
----------------------
境界成长走 `grow_base`（加区，每境属性约 ×3.4）。若法则也走加区，
两条轴会互相稀释，玩家感受不到「我点了法则」的差异。
法则走 `attr_mul` 乘区，与境界加区相乘 —— 同境界、法则深的明确更强。

八条法则各管一个维度，仙界全程只能点亮约 60% 节点（见 LAW_GATE 末档 24/40），
强制 Build 取舍：想变强就得放弃别的路线。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LawDef:
    """一条法则的定义。

    effect_type / effect_key 直接对应 config/arts.py 的 EFFECT_TYPES，
    由 LawSystem.collect_bonuses 交给全局聚合器 —— 核心计算一行都不用改。
    """

    key: str                 # 内部标识
    name: str                # 显示名
    effect_type: str         # attr_mul / cultivate_speed / insight_rate
    effect_key: str          # attr_mul 时为属性名，其余为空串
    desc: str                # 一句话说明（法则面板展示）


# ---------- 八条法则：各管一个维度 ----------
LAWS: tuple[LawDef, ...] = (
    LawDef("metal", "金之法则", "attr_mul", "atk", "锋芒内蕴，一念断金。攻击提升。"),
    LawDef("wood", "木之法则", "attr_mul", "max_hp", "生生不息，枯木回春。气血上限提升。"),
    LawDef("water", "水之法则", "attr_mul", "max_mp", "上善若水，绵延不绝。灵力上限提升。"),
    LawDef("fire", "火之法则", "attr_mul", "speed", "心火明动，身随意走。身法提升。"),
    LawDef("earth", "土之法则", "attr_mul", "def", "厚德载物，岿然不动。防御提升。"),
    LawDef("space", "空间法则", "attr_mul", "spirit", "须弥芥子，咫尺天涯。神识提升。"),
    LawDef("time", "时间法则", "cultivate_speed", "", "时光流转，白驹过隙。修炼速度提升。"),
    LawDef("causality", "因果法则", "insight_rate", "", "因果轮回，机缘自生。顿悟概率提升。"),
)

BY_KEY = {law.key: law for law in LAWS}
ORDER = tuple(law.key for law in LAWS)

# ---------- 阶位 ----------
# 五阶：感悟 → 小成 → 大成 → 圆满 → 主宰
LAW_STAGES: tuple[str, ...] = ("感悟", "小成", "大成", "圆满", "主宰")
LAW_MAX_STAGE = len(LAW_STAGES)          # 5

# 每阶所需感悟值（**增量**，非累计）。
#
# 推导（按目标「仙界 1170 天」反推，非拍脑袋）：
#   1) 仙界九境目标 1170 天，其中修为时间约 15 天/境 → 悟道时间约 1035 天；
#   2) 悟道产出实测约 20 感悟值/天 → 全程可获约 2.07 万感悟值；
#   3) 末档门槛 28 阶（均衡路线：先铺满所有低阶）应恰好消耗掉这笔感悟值：
#        24 阶(八条各三阶) = 11200，再加 4 个四阶 = 4×2400 → 合计 20800 ✓
# 首版成本（100/250/600/1400/3200）过便宜，实测中期即点满、后期无瓶颈，故上调后期档位。
# 第 27 批再把末阶 6000 → 4200：为给混元设「终局门槛 34 阶」腾出成本空间。
#   若维持 6000，34 阶（两条满阶 + 六条四阶）= 2×9800 + 6×3800 = 42400，
#   较 32 阶的 30400 贵 39%，混元一境就要多出约 350 天，喧宾夺主；
#   削到 4200 后 34 阶 = 2×8000 + 6×3800 = 38800，仅贵 28%，混元约 +250 天 ——
#   作为收尾之境最长是合理的，且总量仍落在 1170 目标附近。
# 这是**可调基线**：校准仙界总时长时优先动这张表。
LAW_STAGE_COST: tuple[float, ...] = (100.0, 300.0, 1000.0, 2400.0, 4200.0)
# 单条法则点满五阶的总成本（= 5550）
LAW_FULL_COST: float = sum(LAW_STAGE_COST)

# 每阶效果值（+8% 偏移）。attr_mul 按偏移相加（见 arts.EFFECT_TYPES），
# 故单条满阶 = +40% 偏移 → 该属性 ×1.40；不连乘，防多路线叠出指数爆炸。
LAW_STAGE_VALUE = 0.08

# ---------- 法则词条化（第 28 批） ----------
# 每条法则按阶解锁的「副词条」。索引 = 阶(1..5)，元素为该阶解锁的副词条列表：
#     (effect_type, attr_key, value)
# 主属性仍由 LawDef.effect_type/effect_key + LAW_STAGE_VALUE×stage 提供（collect_bonuses 主路径），
# 此处只放「额外」词条 —— 高阶(圆满4/主宰5)解锁，作深投奖励，避免法则只是扁平单值。
#
# 幅度基线（可调）：
#   SEC_AFFIX = 0.05   → 副词条 +5%/阶（attr_mul）或固定加值（attr_add）
#   TIME_CAUSALITY_PRIMARY = 0.08 → time/causality 主键属性 +8%/阶（每阶都给，满阶 +40%，
#        与金属等六条对称；它们本无属性，靠此补上维度，同时保留 cultivate_speed/insight_rate 功能）
#
# 为什么只用现有属性、不引入暴击/穿透/减伤：
#   战斗属性模型仅支持 atk/def/max_hp/max_mp/speed/spirit/comprehension/physique/luck
#   （见 core/attributes.py 与 config/arts.py 的可用属性表），引入二级战斗属性需扩展 combat
#   管线、回归面过广，故本期不碰；副词条全部落在上述 9 个现有属性内。
SEC_AFFIX = 0.05
TIME_CAUSALITY_PRIMARY = 0.08

# 每条法则五阶的副词条（不足五阶的阶位用空元组占位）。
#   metal/wood/water/fire/earth/space：主属性已在 LawDef，这里只放 圆满(4)/主宰(5) 的副词条。
#   time/causality：主属性(physique/luck)在此每阶给，功能效果(cultivate_speed/insight_rate)走 LawDef。
LAW_AFFIXES: dict[str, tuple[tuple, ...]] = {
    "metal": ((), (), (),
              (("attr_mul", "speed", SEC_AFFIX),),
              (("attr_mul", "def", SEC_AFFIX),)),
    "wood": ((), (), (),
             (("attr_mul", "max_mp", SEC_AFFIX),),
             (("attr_add", "physique", 30.0),)),
    "water": ((), (), (),
              (("attr_mul", "speed", SEC_AFFIX),),
              (("attr_mul", "spirit", SEC_AFFIX),)),
    "fire": ((), (), (),
             (("attr_mul", "atk", SEC_AFFIX),),
             (("attr_add", "physique", 30.0),)),
    "earth": ((), (), (),
              (("attr_mul", "max_hp", SEC_AFFIX),),
              (("attr_mul", "spirit", SEC_AFFIX),)),
    "space": ((), (), (),
              (("attr_mul", "speed", SEC_AFFIX),),
              (("attr_mul", "comprehension", SEC_AFFIX),)),
    # time：每阶补 physique（时光淬体），保留 cultivate_speed；圆满/主宰再加 speed/comprehension
    "time": ((("attr_mul", "physique", TIME_CAUSALITY_PRIMARY),),
             (("attr_mul", "physique", TIME_CAUSALITY_PRIMARY),),
             (("attr_mul", "physique", TIME_CAUSALITY_PRIMARY),),
             (("attr_mul", "physique", TIME_CAUSALITY_PRIMARY),
              ("attr_mul", "speed", SEC_AFFIX)),
             (("attr_mul", "physique", TIME_CAUSALITY_PRIMARY),
              ("attr_mul", "comprehension", SEC_AFFIX)),),
    # causality：每阶补 luck（因果牵运），保留 insight_rate；圆满/主宰再加 spirit/comprehension
    "causality": ((("attr_mul", "luck", TIME_CAUSALITY_PRIMARY),),
                 (("attr_mul", "luck", TIME_CAUSALITY_PRIMARY),),
                 (("attr_mul", "luck", TIME_CAUSALITY_PRIMARY),),
                 (("attr_mul", "luck", TIME_CAUSALITY_PRIMARY),
                  ("attr_mul", "spirit", SEC_AFFIX),),
                 (("attr_mul", "luck", TIME_CAUSALITY_PRIMARY),
                  ("attr_mul", "comprehension", SEC_AFFIX)),),
}

# 词条幅度校验：每条法则必须恰好五阶占位，防止 collect_bonuses 越界。
assert all(len(v) == LAW_MAX_STAGE for v in LAW_AFFIXES.values()), "LAW_AFFIXES 阶数不对"

# ---------- 悟道 ----------
# 基础产出（感悟值 / 时辰）。
#
# 这是**可调基线**：校准仙界总时长时优先动它（其次动 LAW_STAGE_COST，最后才动 LAW_GATE）。
#
# 校准史（每次都以模拟实测为准，不拍脑袋）：
#   初版 2.0  推导：悟道约 1031 天 × 12 时辰/天，要点亮 2.4 万感悟 → 2.4e4/1031/12 ≈ 1.94
#            实测：仙界 1132 日（目标 1170，偏差 -3.3%），法则 32/40 阶 ✓
#   第 27 批 加入「法则顿悟」（因果法则的第二处消费点）后实测 1043 日，偏差扩大到 -10.9%。
#            拆解时间构成：总 1043 = 修为 120（8 次跨境 × 15 天，固定）+ 悟道 923（∝ 1/base）
#            反解：悟道需 1050 日 → base = 2.0 × 923 / 1050 = 1.758 ──► 取 1.75
#            实测 1.75 → 1212 日（+3.6%），但大罗境 235 日超出目标区间上限 200，再调一档：
#            取 1.82 → 实测见下（第 27 批最终值）。
#            注意：只动这一个数即可，不要把 LAW_GATE 也一起调 —— 两个旋钮同时动会失去可追溯性。
WUDAO_BASE = 1.82
# 悟道精力消耗（/时辰）。与打坐（1.5）持平：两条轴抢同一份精力预算，
# 玩家要在「今天冲境界」还是「今天攒法则」之间取舍。
WUDAO_STAMINA_PER_HOUR = 1.5
# 悟性倍率基准，与 cultivation.COMPREHENSION_REF 保持一致（悟性高则悟道快）
WUDAO_COMPREHENSION_REF = 1.5
# 单次悟道上限（时辰）
WUDAO_MAX_HOURS = 24.0

# ---------- 法则顿悟 ----------
# 静悟时气运触发，一次额外给「LAW_INSIGHT_BONUS_HOURS 时辰」的感悟产出。
#
# 存在理由：因果法则（insight_rate）原本只作用于打坐顿悟，而打坐顿悟硬顶 15%、
# 基础 5% + 气运约 0.8%，因果点两阶即触顶，后三阶白点（第 27 批实测）。
# 此处开出第二处消费点，让因果五阶都有边际收益。
#
# 量级推导（基准：仙界 1132 天 / 32 阶 / 总感悟 30400 → 日均 26.9）：
#   每 4 时辰判定一次，日均有效悟道约 12 时辰 → 3 次判定/日；
#   顿悟率区间 3%（因果 0 阶）→ 20%（因果 4 阶），取中 ~12% → 0.36 次/日；
#   单次 4 时辰 × 2.0 产出 = 8 感悟 → 期望 2.9 感悟/日 ≈ 基准的 11%。
#   这是刻意压小的：随机惊喜不参与长期节奏定价，超标就调 LAW_GATE 补偿。
LAW_INSIGHT_BASE_RATE = 0.03      # 基础顿悟率（因果 0 阶）
# 上限取 0.36：因果满四阶给 +32%，加上基础 3% = 35% < 36%，
# 即五阶全程不触顶 —— 这正是本机制存在的意义（若上限取 20%，因果两阶就满了，白点照旧）。
LAW_INSIGHT_MAX_RATE = 0.36
LAW_INSIGHT_BONUS_HOURS = 4.0     # 单次顿悟 = 几时辰的产出
LAW_INSIGHT_ROLL_HOURS = 4.0      # 每几时辰判定一次（与打坐顿悟同款，防操作粒度套利）

# ---------- 软门槛 ----------
# 未达门槛时的突破成功率惩罚（软门槛：可硬冲，代价大；不会卡死玩家）。
# 30pp 的量级参考：仙界基础突破率 4%~15%，扣 30pp 会直接压到 5% 安全下限，
# 即「硬冲几乎必败但不禁」——符合项目 v2「失败温和化、不阻断」的一贯哲学。
LAW_GATE_PENALTY = 0.30

# 终局门槛（混元圆满）的惩罚：比跨境界更狠（35pp），因为它是**收尾关**而非过渡关，
# 且此时基础成功率本身已低到 4%，扣 35pp 后仍压在 5% 安全下限 —— 只降不禁。
LAW_FINAL_GATE_PENALTY = 0.35

# 突破到「目标境界」所需的**累计阶数**（跨八条法则合计）。
#
# 推导（实测校准，非拍脑袋）：
#   1) 悟道产出实测约 20 感悟值/天（WUDAO_BASE=2.0，含悟性倍率与浮动）；
#   2) 每境悟道天数 = 目标停留 - 修为时间(约 15 天)，逐级累加得累计可获感悟值；
#   3) 按均衡路线（先铺满所有一阶，再二阶……）换算成可达阶数；
#   4) 门槛取该值 × 0.9 —— 留一成余量，让每境都「差一点点」，持续有瓶颈。
# 首版门槛过松（2/4/7/10…24），实测中期就点满 25 阶，后期完全无瓶颈、
# 节奏退回纯修为（太乙仅停 54 天）；本表为按上述推导收紧后的版本。
LAW_GATE: dict[str, int] = {
    "earth_immortal": 5,     # 首境放宽：飞升后立刻卡 151 天太挫败（实测）
    "heaven_immortal": 12,
    "mystic_immortal": 17,
    "gold_immortal": 21,
    "taiyi": 25,
    "luo_po": 28,
    "quasi_saint": 30,
    "hunyuan": 32,           # 末档 32/40 = 80%，强制取舍且后期持续有瓶颈
}

# 终局门槛：混元**圆满**所需累计阶数（不是「突破到混元」的门槛）。
#
# 存在理由（第 27 批实测发现的结构性空洞）：
#   should_keep_wudao() 原本只在「还有下一境」时才让挂机转悟道，
#   而混元是末境 → 无下一境 → 挂机在混元完全不悟道 → 只剩快速修为 →
#   实测混元仅停留 8 天（其余各境 70~230 天），终局潦草收尾。
#   补上终局门槛后，混元成为真正的收尾之境：需从 32 阶推到 34 阶。
# 34/40 = 85%，与「末档强制取舍、点不满」的设计一致（余下 6 阶是留给长线玩家的白金追求）。
LAW_FINAL_GATE = 34


def stage_name(stage: int) -> str:
    """阶数 → 阶名（0 表示尚未入门）。"""
    if stage <= 0:
        return "未入门"
    return LAW_STAGES[min(stage, LAW_MAX_STAGE) - 1]


def stage_of(insight: float) -> int:
    """由累计感悟值反推当前阶数（0~5）。

    用累计阈值逐阶判定：insight 达到前 n 阶成本之和即为第 n 阶。
    """
    total = 0.0
    for i, cost in enumerate(LAW_STAGE_COST):
        total += cost
        if insight < total:
            return i
    return LAW_MAX_STAGE


def cost_to_next(insight: float) -> float:
    """距下一阶还差多少感悟值（已圆满返回 0）。"""
    stage = stage_of(insight)
    if stage >= LAW_MAX_STAGE:
        return 0.0
    spent = sum(LAW_STAGE_COST[:stage])
    return LAW_STAGE_COST[stage] - (insight - spent)


def total_stages(progress: dict[str, float]) -> int:
    """累计阶数（八条法则合计）——软门槛判定用。"""
    return sum(stage_of(v) for v in progress.values() if v > 0)


def gate_of(realm_key: str) -> int:
    """突破到该境界所需的累计阶数（无门槛返回 0）。"""
    return LAW_GATE.get(realm_key, 0)
