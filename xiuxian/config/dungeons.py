"""秘境配置：数据驱动，加新秘境只改这里，不动代码。

设计要点：
    1. 线性层数推进 —— 每层抽签决定是「妖兽 / 机缘 / 宝箱 / 静室」，底层必是守关之敌
    2. 难度靠 tier 拉开，不靠层数 —— 敌人属性是按玩家境界等比缩放的（见 combat.spawn），
       所以第 7 层和第 1 层如果同 tier，打起来一样难。层数深 = tier 高，这是唯一锚点
    3. 奖励写效果 DSL —— 复用 core/effects.py 的同一套语法，事件、丹药、秘境共用一个结算器
    4. 修为奖励一律用 exp_ratio（按当前境界需求比例），不用绝对值：
       固定值在炼气期能直接飞升、在渡劫期等于零，与「灵石挂在指数增长量上」是同一类坑
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 层类型（抽签用）
KIND_BATTLE = "battle"      # 妖兽拦路
KIND_EVENT = "event"        # 机缘际遇，走 EventSystem
KIND_TREASURE = "treasure"  # 洞天遗宝
KIND_REST = "rest"          # 静室，可整备
KIND_BOSS = "boss"          # 守关之敌，只出现在最后一层

KIND_LABELS: dict[str, str] = {
    KIND_BATTLE: "妖兽",
    KIND_EVENT: "机缘",
    KIND_TREASURE: "遗宝",
    KIND_REST: "静室",
    KIND_BOSS: "守关",
}

# 层内凶险的中文名（对应 combat.TIERS 的键）
TIER_LABELS: dict[str, str] = {
    "weak": "寻常",
    "normal": "凶险",
    "elite": "险恶",
    "boss": "守关",
}


@dataclass(frozen=True)
class FloorDef:
    """一层。kinds 为层类型权重，最后一层只写 boss。"""

    name: str
    desc: str
    kinds: dict[str, float] = field(default_factory=dict)
    tier: str = "normal"      # 本层战斗难度（weak / normal / elite / boss）

    def is_boss(self) -> bool:
        return KIND_BOSS in self.kinds


@dataclass(frozen=True)
class DungeonDef:
    id: str
    name: str
    min_realm: str                                  # 境界门槛
    desc: str
    floors: tuple[FloorDef, ...]
    cooldown: int                                   # 通关后需隔几日方可再入
    treasures: tuple[tuple[dict, ...], ...] = ()    # 宝箱池，随机抽一组
    boss_reward: tuple[dict, ...] = ()              # 通关底层的大奖
    stamina: int = 20                               # 非战斗层的探索消耗（战斗层由 fight 自扣）

    @property
    def depth(self) -> int:
        return len(self.floors)


# ---------- 宝箱池（三座秘境共用一套写法，价值按秘境档位区分）----------

_T_LOW: tuple[tuple[dict, ...], ...] = (
    ({"type": "stone", "value": 120},
     {"type": "item", "id": "spirit_herb", "count": 3}),
    ({"type": "item", "id": "healing_pill", "count": 2},
     {"type": "exp_ratio", "value": 0.12}),
    ({"type": "item", "id": "spirit_water", "count": 1},
     {"type": "log", "text": "一泓灵泉藏在石缝里，饮之通体舒泰。"}),
    ({"type": "item", "id": "qi_gathering_pill", "count": 2},
     {"type": "stone", "value": 60}),
)

_T_MID: tuple[tuple[dict, ...], ...] = (
    ({"type": "stone", "value": 400},
     {"type": "item", "id": "beast_core", "count": 2}),
    ({"type": "item", "id": "foundation_pill", "count": 1},
     {"type": "exp_ratio", "value": 0.18}),
    ({"type": "item", "id": "spirit_water", "count": 2},
     {"type": "attr", "key": "physique", "value": 1}),
    ({"type": "buff", "id": "dungeon_ward", "name": "洞天护持", "hours": 72,
      "add": {"defense": 4}},
     {"type": "stone", "value": 200}),
)

_T_HIGH: tuple[tuple[dict, ...], ...] = (
    ({"type": "stone", "value": 1200},
     {"type": "item", "id": "beast_core", "count": 4}),
    ({"type": "item", "id": "golden_core_pill", "count": 1},
     {"type": "exp_ratio", "value": 0.25}),
    ({"type": "attr", "key": "physique", "value": 2},
     {"type": "item", "id": "spirit_water", "count": 3}),
    ({"type": "buff", "id": "dungeon_aura", "name": "太虚罡气", "hours": 120,
      "add": {"atk": 8, "defense": 8}},
     {"type": "stone", "value": 600}),
)

# ---------- 仙界宝箱池（锚定「秘境入门境界」的一场常规战利量级，不挂指数派生量） ----------
# 灵石绝对值按入门境界的一场 normal 战斗产出锚定：人仙约 962/场、金仙约 3939/场；
# 宝箱给 2~5 倍一场的量级，随秘境档次递增（与凡界三档做法同构）。
# 修为一律 exp_ratio，保证仙界后期不掉链子。
_T_IMMORTAL: tuple[tuple[dict, ...], ...] = (
    ({"type": "stone", "value": 3000},
     {"type": "item", "id": "immortal_core", "count": 2}),
    ({"type": "item", "id": "immortal_pill", "count": 1},
     {"type": "exp_ratio", "value": 0.30}),
    ({"type": "item", "id": "immortal_herb", "count": 3},
     {"type": "attr", "key": "comprehension", "value": 1}),
    ({"type": "buff", "id": "fairy_ward", "name": "仙宫护持", "hours": 168,
      "add": {"defense": 60, "atk": 40}},
     {"type": "stone", "value": 1500}),
)

_T_MYSTIC: tuple[tuple[dict, ...], ...] = (
    ({"type": "stone", "value": 10000},
     {"type": "item", "id": "immortal_core", "count": 4}),
    ({"type": "item", "id": "immortal_pill", "count": 2},
     {"type": "exp_ratio", "value": 0.45}),
    ({"type": "attr", "key": "comprehension", "value": 2},
     {"type": "item", "id": "immortal_herb", "count": 5}),
    ({"type": "buff", "id": "mystic_ward", "name": "劫云护体", "hours": 240,
      "add": {"atk": 120, "defense": 120, "speed": 60}},
     {"type": "stone", "value": 5000}),
)


DUNGEONS: dict[str, DungeonDef] = {
    # ---------- 一、落云秘境：炼气期即可闯 ----------
    "luoyun": DungeonDef(
        id="luoyun",
        name="落云秘境",
        min_realm="qi_refining",
        desc="云生足下，殿宇半塌。传闻是某位炼气大圆满修士的别府，如今只余妖猿盘踞。",
        cooldown=3,
        stamina=12,
        floors=(
            FloorDef("云雾外谷", "谷口云气翻涌，视野不过十丈，脚下的兽道被踩得发亮。",
                     {KIND_BATTLE: 6, KIND_EVENT: 2, KIND_TREASURE: 2, KIND_REST: 1},
                     "weak"),
            FloorDef("白骨雾林", "林中挂着不知多久的兽骨，云气里浮着淡淡的腥味。",
                     {KIND_BATTLE: 5, KIND_EVENT: 2, KIND_TREASURE: 3, KIND_REST: 1},
                     "normal"),
            # 炼气期秘境的守关只用 elite：boss 档（2.6 倍）在低境界几乎是必败，
            # 敌人属性按玩家等比缩放，倍率一高谁都扛不住，与境界无关
            FloorDef("落云主殿", "殿门半开，一只丈许高的白猿蹲坐于丹墀之上，正拿一只玉匣把玩。",
                     {KIND_BOSS: 1}, "elite"),
        ),
        treasures=_T_LOW,
        boss_reward=(
            {"type": "log", "text": "白猿哀鸣倒地，玉匣脱手滚落——原来是前主人留下的遗藏。"},
            {"type": "exp_ratio", "value": 0.40},
            {"type": "stone", "value": 300},
            {"type": "item", "id": "spirit_water", "count": 2},
            {"type": "item", "id": "qi_gathering_pill", "count": 3},
        ),
    ),

    # ---------- 二、万兽深渊：筑基起步 ----------
    "wanshou": DungeonDef(
        id="wanshou",
        name="万兽深渊",
        min_realm="foundation",
        desc="一道裂入地脉的巨渊，兽吼昼夜不息。越往下走，骨殖越新。",
        cooldown=5,
        stamina=12,
        floors=(
            FloorDef("断魂崖", "崖壁上有旧年的剑痕，也有新鲜的抓印。",
                     {KIND_BATTLE: 6, KIND_EVENT: 2, KIND_TREASURE: 2, KIND_REST: 1},
                     "weak"),
            FloorDef("兽骨道", "白骨铺成一条路，踩上去咯吱作响，两侧的眼珠在暗处转动。",
                     {KIND_BATTLE: 6, KIND_EVENT: 2, KIND_TREASURE: 2, KIND_REST: 1},
                     "normal"),
            FloorDef("血池", "一池暗红，热气蒸腾，池底沉着几件未曾腐朽的法器。",
                     {KIND_BATTLE: 4, KIND_EVENT: 3, KIND_TREASURE: 4, KIND_REST: 1},
                     "normal"),
            FloorDef("兽王窟", "洞窟深处传来低沉的喘息，地面随呼吸微微起伏。",
                     {KIND_BATTLE: 5, KIND_EVENT: 2, KIND_TREASURE: 3, KIND_REST: 1},
                     "elite"),
            FloorDef("深渊之底", "巨兽盘卧，鳞甲上覆着一层地火灼出的焦痕。它睁开了眼。",
                     {KIND_BOSS: 1}, "boss"),
        ),
        treasures=_T_MID,
        boss_reward=(
            {"type": "log", "text": "兽王伏诛，渊底的地脉之气扑面而来。"},
            {"type": "exp_ratio", "value": 0.55},
            {"type": "stone", "value": 900},
            {"type": "item", "id": "beast_core", "count": 3},
            {"type": "item", "id": "foundation_pill", "count": 1},
        ),
    ),

    # ---------- 三、太虚洞天：金丹起步 ----------
    "taixu": DungeonDef(
        id="taixu",
        name="太虚洞天",
        min_realm="core",
        desc="一方悬于虚空的小世界，据说是某位道主坐化前斩下的洞天。星河流转，不见日月。",
        cooldown=7,
        stamina=12,
        floors=(
            FloorDef("太虚门外", "一道石门悬在虚空里，门楣上的字迹已被岁月磨平。",
                     {KIND_BATTLE: 5, KIND_EVENT: 3, KIND_TREASURE: 2, KIND_REST: 2},
                     "normal"),
            FloorDef("星陨荒原", "满地都是未熄的陨铁，踩上去烫脚，远处有铁兽在啃食陨石。",
                     {KIND_BATTLE: 6, KIND_EVENT: 2, KIND_TREASURE: 2, KIND_REST: 1},
                     "normal"),
            FloorDef("幻月湖", "湖面映出的不是头顶的星空，而是你自己的背影——那背影先你一步转身。",
                     {KIND_BATTLE: 4, KIND_EVENT: 4, KIND_TREASURE: 3, KIND_REST: 1},
                     "elite"),
            FloorDef("剑冢", "插满断剑的山丘，每一柄都还在低鸣，剑意割得人脸生疼。",
                     {KIND_BATTLE: 6, KIND_EVENT: 2, KIND_TREASURE: 4, KIND_REST: 1},
                     "elite"),
            FloorDef("道碑林", "无字石碑林立，碑上刻着一位道主未尽的道。有碑灵在林间游荡。",
                     {KIND_BATTLE: 4, KIND_EVENT: 5, KIND_TREASURE: 3, KIND_REST: 2},
                     "elite"),
            FloorDef("虚空裂隙", "脚下的路断在虚空里，只余几块浮岛，裂隙深处有什么在呼吸。",
                     {KIND_BATTLE: 5, KIND_EVENT: 3, KIND_TREASURE: 4, KIND_REST: 1},
                     "elite"),
            FloorDef("太虚殿", "殿中只有一道盘坐的残念，它抬起眼，与你四目相对。",
                     {KIND_BOSS: 1}, "boss"),
        ),
        treasures=_T_HIGH,
        boss_reward=(
            {"type": "log", "text": "残念溃散前留下一句：「道无止境，好自为之。」"},
            {"type": "exp_ratio", "value": 0.70},
            {"type": "stone", "value": 2400},
            {"type": "item", "id": "beast_core", "count": 5},
            {"type": "item", "id": "golden_core_pill", "count": 1},
            {"type": "attr", "key": "physique", "value": 2},
        ),
    ),

    # ---------- 四、瑶池仙园：人仙起步（飞升后的第一个仙界秘境） ----------
    "yaochi": DungeonDef(
        id="yaochi",
        name="瑶池仙园",
        min_realm="human_immortal",
        desc="飞升后俯瞰凡尘之外的一方仙园，据传是某位仙君的遗府。云霞为径，仙鹤往来，池中隐约伏着太古异种。",
        cooldown=9,
        stamina=18,
        floors=(
            FloorDef("云径", "云霞铺就的小径悬于半空，两侧尽是叫不出名字的仙草。",
                     {KIND_BATTLE: 5, KIND_EVENT: 2, KIND_TREASURE: 3, KIND_REST: 2},
                     "normal"),
            FloorDef("瑶池畔", "一泓碧池倒映着天上宫阙，池底沉着一枚枚温润的仙石。",
                     {KIND_BATTLE: 5, KIND_EVENT: 3, KIND_TREASURE: 3, KIND_REST: 2},
                     "normal"),
            FloorDef("仙鹤洲", "成群仙鹤盘踞沙洲，见生人踏入，纷纷亮出铁喙。",
                     {KIND_BATTLE: 6, KIND_EVENT: 2, KIND_TREASURE: 2, KIND_REST: 2},
                     "elite"),
            FloorDef("蟠桃林", "虬枝蟠结的古桃林，树梢挂着几枚泛着红晕的灵果，风一吹便轻轻摇晃。",
                     {KIND_BATTLE: 4, KIND_EVENT: 4, KIND_TREASURE: 4, KIND_REST: 2},
                     "elite"),
            FloorDef("仙君寝殿", "殿门虚掩，一缕仙威压得人喘不过气——那位仙君似乎只是『外出』了。",
                     {KIND_BOSS: 1}, "boss"),
        ),
        treasures=_T_IMMORTAL,
        boss_reward=(
            {"type": "log", "text": "寝殿空无一人，案上留下一卷残破的仙谱和一枚蟠桃仙果。"},
            {"type": "exp_ratio", "value": 1.00},
            {"type": "stone", "value": 6000},
            {"type": "item", "id": "immortal_core", "count": 4},
            {"type": "item", "id": "immortal_pill", "count": 1},
            {"type": "attr", "key": "comprehension", "value": 3},
        ),
    ),

    # ---------- 五、万劫云海：金仙起步（仙界高档秘境） ----------
    "wanjie": DungeonDef(
        id="wanjie",
        name="万劫云海",
        min_realm="gold_immortal",
        desc="一片由无数劫云汇聚而成的混沌海域，传说通往道的尽头。云海翻涌处沉睡着渡劫失败的太古仙人残念。",
        cooldown=14,
        stamina=24,
        floors=(
            FloorDef("劫云滩", "脚下是细碎的劫云凝成的滩涂，踩上去如陷流沙。",
                     {KIND_BATTLE: 5, KIND_EVENT: 3, KIND_TREASURE: 2, KIND_REST: 2},
                     "elite"),
            FloorDef("雷池", "一池雷光凝成的液体噼啪作响，池心立着一根焦黑的枯木。",
                     {KIND_BATTLE: 6, KIND_EVENT: 2, KIND_TREASURE: 3, KIND_REST: 2},
                     "elite"),
            FloorDef("残念之森", "一缕缕渡劫失败者的残念凝成树影，低语着不甘的呓语。",
                     {KIND_BATTLE: 5, KIND_EVENT: 4, KIND_TREASURE: 3, KIND_REST: 2},
                     "elite"),
            FloorDef("道花谷", "整片山谷开满由道韵凝成的奇花，摘一朵便是无上机缘，却未必摘得下。",
                     {KIND_BATTLE: 4, KIND_EVENT: 5, KIND_TREASURE: 4, KIND_REST: 2},
                     "elite"),
            FloorDef("万劫主殿", "劫云深处一座殿宇沉浮，殿前盘坐着一位阖目长眠的残仙——它动了。",
                     {KIND_BOSS: 1}, "boss"),
        ),
        treasures=_T_MYSTIC,
        boss_reward=(
            {"type": "log", "text": "残仙身影渐渐消散，化作点点道韵没入你眉心——这是它渡劫未尽的感悟。"},
            {"type": "exp_ratio", "value": 1.60},
            {"type": "stone", "value": 16000},
            {"type": "item", "id": "immortal_core", "count": 8},
            {"type": "item", "id": "immortal_pill", "count": 2},
            {"type": "attr", "key": "comprehension", "value": 5},
        ),
    ),
}


def get_dungeon(dungeon_id: str) -> DungeonDef:
    if dungeon_id not in DUNGEONS:
        raise KeyError(f"未知秘境: {dungeon_id}")
    return DUNGEONS[dungeon_id]


def unlocked_for(realm_key: str) -> list[DungeonDef]:
    """按玩家境界列出已解锁的秘境（顺序固定，便于展示）。"""
    from .realms import RealmRegistry

    out = []
    for d in DUNGEONS.values():
        if RealmRegistry.within(realm_key, min_realm=d.min_realm):
            out.append(d)
    return out
