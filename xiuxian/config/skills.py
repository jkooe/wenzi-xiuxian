"""技能配置表。

与功法的关系：
    每门功法自带一个「签名技能」，修习功法即得技能（见 ArtDef.skills）。
    技能威力按「所属功法的熟练度」缩放，复用 config/arts.py 的 proficiency_scale：
        刚学会只有六成效力，练满才十成。
    所以「换更强的功法」不等于立刻变强，与属性加成的取舍逻辑完全一致。

**所有消耗与产出都是比例，不是绝对值**（这是本表最重要的约束）：
    灵力上限从开局 40 一路涨到渡劫的 1173 万，跨五个数量级。
    若 mp_cost 写死 20 点：炼气期（蓝条 220）是 9%，渡劫期等于 0.0002%，
    技能和白送没区别——与「灵石挂在指数增长量上结余 288 亿」是同一类错误。
    因此：
        mp_cost       占最大灵力的比例
        power(heal)   占最大气血的比例
        power(recover)占最大灵力的比例
        hp_cost_ratio 占最大气血的比例（自伤类技能的代价）
        power(attack) 普攻倍率（本来就是相对量，天然可跨境界）
    只有 power(attack) 是倍率而非占比，因为攻击力本身已随境界指数增长。

施放文案（cast 字段）：
    战斗日志的叙事部分写在配置里、不写死在逻辑中——这是「文字是玩法」
    这一原则的落点：换文案只改这张表，不动 combat.py 一行。
        heal/guard/recover 的 cast 是动作描写，直接拼在「你施展【名】」之后；
        attack 的 cast 可用 {enemy} 占位敌人名（str.format 渲染，非 f-string）。
    空串的 cast 由战斗系统退回通用句式，第三方功法无需配置也能战斗。

技能类型（kind）：
    attack   替代普攻的一击，伤害 = atk × power
    heal     耗灵力回气血，回复 max_hp × power
    guard    本回合减伤，减伤比例 = power（护到你下次出手前）
    recover  回灵，回复 max_mp × power。不耗灵力——它是「蓝空了」这个
             死局的出口，收 0 费才能在真正需要时放得出来，代价是占用一回合。
"""

from __future__ import annotations

from dataclasses import dataclass

# 技能类型的中文名，供面板展示
SKILL_KINDS: dict[str, str] = {
    "attack": "攻伐",
    "heal": "疗伤",
    "guard": "护体",
    "recover": "回灵",
}


@dataclass(frozen=True)
class SkillDef:
    id: str
    name: str
    kind: str                       # attack / heal / guard / recover
    desc: str = ""
    mp_cost: float = 0.10           # 占最大灵力的比例
    cooldown: int = 0               # 冷却回合数（0 = 每回合都能放）
    power: float = 1.0              # 见文件头：按 kind 决定含义
    hp_cost_ratio: float = 0.0      # 自伤：占最大气血的比例（攻伐凌厉的代价）
    # 施放时的叙事文案（战斗日志用）。attack 类可用 {enemy} 占位敌人名。
    # 空串表示退回战斗系统的通用句式——留给第三方/事件发放的功法用。
    cast: str = ""


SKILLS: dict[str, SkillDef] = {
    # 清心诀 —— 凝神静气，耗灵力疗伤
    "qingxin_heal": SkillDef(
        "qingxin_heal", "清心咒", "heal",
        "澄心静虑，以灵力滋养伤处。",
        mp_cost=0.18, power=0.22, cooldown=3,
        cast="指尖凝起一点清光，没入心口，经脉间的暗伤缓缓弥合",
    ),
    # 庚金剑诀 —— 以金气淬剑，出手锋锐
    "gengjin_strike": SkillDef(
        "gengjin_strike", "庚金一击", "attack",
        "剑气凝于一线，锋锐无匹。",
        mp_cost=0.10, power=1.9, cooldown=2,
        cast="一道庚金剑气凝于剑尖，锐啸破空，直贯 {enemy} 要害",
    ),
    # 玄龟镇海功 —— 守御如山
    "xuangui_guard": SkillDef(
        "xuangui_guard", "玄龟护体", "guard",
        "玄龟负山，水泼不进。护到你下次出手为止。",
        mp_cost=0.12, power=0.45, cooldown=2,
        cast="一道玄龟虚影自背后升起，背负山岳之势，将你牢牢罩住",
    ),
    # 赤焰焚天诀 —— 攻伐凌厉，代价是气血亏虚
    "chiyan_burst": SkillDef(
        "chiyan_burst", "焚天斩", "attack",
        "倾力一击，焚尽八荒，然反噬己身。",
        mp_cost=0.16, power=2.6, hp_cost_ratio=0.06, cooldown=4,
        cast="赤焰自剑身席卷而出，如焚天之势，将 {enemy} 整个吞没",
    ),
    # 太虚养神功 —— 灵力绵绵不绝，最宜久战
    "taixu_recover": SkillDef(
        "taixu_recover", "太虚回灵", "recover",
        "引天地灵气入体，不耗自身分毫，但需静立一瞬。",
        mp_cost=0.0, power=0.28, cooldown=5,
        cast="识海洞开，天地灵气自虚空倒灌而入，枯竭的丹田重新充盈",
    ),
    # 大衍周天诀 —— 诸脉共鸣，无所不包
    "dayan_star": SkillDef(
        "dayan_star", "周天星斗", "attack",
        "周天星力加身，一击蕴含着诸脉共鸣之力。",
        mp_cost=0.20, power=2.9, cooldown=3,
        cast="周天星力垂落，凝成一束璀璨星光，轰然砸向 {enemy}",
    ),
}


def get_skill(skill_id: str) -> SkillDef:
    if skill_id not in SKILLS:
        raise KeyError(f"未知技能: {skill_id}")
    return SKILLS[skill_id]


def scaled_power(skill: SkillDef, scale: float) -> float:
    """按熟练度缩放技能威力。

    倍率类（attack）缩放「偏离 1 的部分」，与 scaled_bonus 的 mul 处理一致：
        满熟练 power=2.6 -> 2.6；六成熟练 -> 1 + 1.6×0.6 = 1.96
    占比类（heal/guard/recover）直接乘：
        满熟练 power=0.22 -> 0.22；六成熟练 -> 0.132
    """
    if skill.kind == "attack":
        return 1 + (skill.power - 1) * scale
    return skill.power * scale
