"""战斗系统（回合制）。

设计要点：
    敌人属性按玩家当前境界等比缩放，不需要为每个境界维护一张数值表，
    因此从炼气到渡劫都用同一套代码，只调 TIERS 系数即可改难度。
    战斗入口有两个：
        1. 事件 DSL 里的 {"type": "battle"}（注册为自定义效果）
        2. 玩家主动 hunt 命令
    濒死护持：hp 归零不直接判死，改为重伤 + 损失修为与灵石，适合长线养成。
"""

from __future__ import annotations

from ..core.numfmt import fmt_num

import math
from dataclasses import dataclass, field

from ..core.base_system import Command, GameSystem, TOPIC_COMBAT_VICTORY

BEASTS = ("青纹狼", "赤角鹿", "黑风豹", "铁背蜈蚣", "噬灵鼠", "幽冥蛇", "独角犀兕")

TIERS: dict[str, float] = {
    "weak": 0.6,
    "normal": 1.0,
    "elite": 1.6,
    "boss": 2.6,
}

FIGHT_STAMINA = 8           # 开战所需精力（v2 放宽：猎妖 8/次）

# 战利灵石：基础值 + 难度加成，再按大境界序号几何递增（炼气 14 → 渡劫 600 左右）
STONE_BASE = 8.0
STONE_PER_MULT = 6.0
STONE_GROWTH = 1.6

# ---------- 文风素材（文字即玩法的落点，可自行扩充） ----------
# 战斗日志不是数值简报。每回合从下面抽一个前缀/文案，让同一套数值
# 打出来千变万化。注意保留锚点词：
#   普攻行必须含「你击中」；技能行必须含「施展【」；
#   受击行保留「反扑/受到/剩余」；胜利首句必须含「倒地不起」
# （测试、demo、推演脚本都依赖这些锚点）。
PLAYER_STRIKE_FLAVOR = (
    "剑意凝于一线，", "虚晃一枪，", "劲风呼啸，", "身形一晃，",
    "寒光乍现，", "欺身而进，",
)
PLAYER_CRIT_FLAVOR = (
    "剑光如虹，", "你窥得破绽，", "势若奔雷，",
)
ENEMY_STRIKE_FLAVOR = (
    "獠牙森然，", "利爪破空，", "腥风扑面，", "它低吼一声，",
    "黑影一闪，", "它骤然暴起，",
)
VICTORY_LINES = (
    "{enemy} 哀鸣一声，倒地不起。",
    "{enemy} 惨嚎一声，轰然倒地不起。",
    "{enemy} 挣扎数息，终是轰然倒地不起。",
    "{enemy} 发出一声悲鸣，缓缓倒地不起。",
)


def stones_for_realm(realm_key: str, mult: float = 1.0, art_bonus: float = 0.0) -> int:
    """灵石产出的唯一公式，战斗与秘境共用。

    基准是境界序号而非修为需求——修为需求随境界指数膨胀，
    照它切比例会让后期产出失控（推演曾出现结余 288 亿）。

    v2 软上限：灵石加成 +300% 以内全效，超出部分每 1% 仅算 20%。
    """
    from ..config.realms import RealmRegistry

    idx = RealmRegistry.index_of(realm_key)
    # v2 软上限：+300% 封顶，超出部分仅算 20%
    if art_bonus > 3.0:
        art_bonus = 3.0 + (art_bonus - 3.0) * 0.20
    return int((STONE_BASE + STONE_PER_MULT * mult)
               * (STONE_GROWTH ** idx) * (1.0 + art_bonus))


@dataclass
class Enemy:
    name: str
    hp: float
    max_hp: float
    atk: float
    defense: float
    speed: float
    exp_reward: float
    drops: list[tuple[str, int, float]] = field(default_factory=list)
    mult: float = 1.0              # 难度系数，用于结算灵石等产出
    tier: str = "normal"          # 难度档（weak/normal/elite/boss），供任务按档过滤

    def alive(self) -> bool:
        return self.hp > 0


class CombatSystem(GameSystem):
    id = "combat"
    name = "斗法"

    MAX_ROUNDS = 40
    # 护体减伤：由 guard 类技能在本回合置位，敌人下一次出手时消耗掉。
    # 用类属性兜底，避免 _enemy_strike 在 fight() 之外被调用时取不到值。
    _guard: float = 0.0

    def on_bind(self) -> None:
        # 注册自定义效果：事件 JSON 里写 {"type": "battle", "tier": "normal"} 即可开战
        self.game.register_effect("battle", self.effect_battle)

    # ---------- 生成敌人 ----------
    def spawn(self, tier: str = "normal") -> Enemy:
        p = self.player
        mult = TIERS.get(tier, 1.0)
        rng = self.game.rng
        name = rng.choice(BEASTS)
        hp = p.max_hp * (0.6 + 0.3 * rng.rand()) * mult
        atk = p.atk * (0.45 + 0.2 * rng.rand()) * mult
        return Enemy(
            name=name,
            hp=hp,
            max_hp=hp,
            atk=atk,
            defense=p.defense * 0.35 * mult,
            speed=p.speed * (0.8 + 0.4 * rng.rand()),
            exp_reward=self._exp_base() * (0.020 + 0.012 * rng.rand()) * mult,
            drops=self._drops(mult),
            mult=mult,
            tier=tier,
        )

    def _exp_base(self) -> float:
        """战利修为的参照基数。

        常规用「升下一层所需修为」，但已至绝巅时它是 inf，
        直接乘系数会让 int() 抛 OverflowError，这里退回上一层的需求。
        """
        from ..config.realms import RealmRegistry

        need = self.player.exp_required()
        if need != float("inf"):
            return need
        stage = max(0, self.player.stage - 1)
        return RealmRegistry.stage_exp_required(self.player.realm_def, stage)

    def _drops(self, mult: float) -> list[tuple[str, int, float]]:
        """掉落表：材料固定，装备按玩家境界从配置表里筛（不维护每境界掉落表）。"""
        from ..config import items as item_config
        from ..config.realms import RealmRegistry

        drops = [
            ("beast_core", 1, 0.35 * mult),
            ("beast_blood", 1, 0.55),
            ("spirit_herb", 1, 0.25),
        ]
        pool = [
            item_id for item_id, it in item_config.ITEMS.items()
            if it.kind == "equip" and it.equip_slot
            and (not it.min_realm
                 or RealmRegistry.within(self.player.realm_key, min_realm=it.min_realm))
        ]
        if pool:
            drops.append((self.game.rng.choice(pool), 1, 0.10 * mult))
        return drops

    # ---------- 战斗 ----------
    def effect_battle(self, player, eff: dict) -> list[str]:
        tier = eff.get("tier", "normal")
        return self.fight(self.spawn(tier))

    def fight(self, enemy: Enemy) -> list[str]:
        p = self.player
        logs = [f"遭遇【{enemy.name}】！气血 {fmt_num(enemy.hp)}　攻击 {fmt_num(enemy.atk)}"]

        # 已身死则不可再战：否则循环因 p.alive=False 不进入、直接落到 _defeat()，
        # 把濒死护持变成「死亡后复活通道」，死亡惩罚与 game.over 全部失效。
        if not p.alive:
            logs.append("你已身死道消，无力再战。")
            return logs

        if not p.spend_stamina(FIGHT_STAMINA):
            logs.append("精力不济，你无心恋战。")
            return logs

        # 技能系统：可选装配。未装配时战斗行为与原来完全一致——
        # 事件自动战斗、秘境推进、既有测试与推演脚本都不受影响。
        skill_sys = self.game.systems.get("skill")
        self._guard = 0.0
        if skill_sys is not None:
            skill_sys.begin_battle()

        round_no = 0
        first = p.speed >= enemy.speed
        logs.append("你抢得先机。" if first else f"{enemy.name} 身法更快，抢先出手。")

        while enemy.alive() and p.alive and round_no < self.MAX_ROUNDS:
            round_no += 1
            if skill_sys is not None:
                skill_sys.next_round()
            if first:
                logs.extend(self._player_strike(enemy))
                if not enemy.alive():
                    break
                logs.extend(self._enemy_strike(enemy))
            else:
                logs.extend(self._enemy_strike(enemy))
                if not p.alive:
                    break
                logs.extend(self._player_strike(enemy))

        if not enemy.alive():
            logs.extend(self._victory(enemy))
        elif not p.alive:
            logs.extend(self._defeat(enemy))
        else:
            logs.append("久战不下，双方各自退开。")

        # 收尾：清掉本场的冷却与护体，免得「冷却中」漏到战后的 skill 面板上
        if skill_sys is not None:
            skill_sys.end_battle()
        self._guard = 0.0

        logs.extend(self.game.advance_time(2))
        return logs

    def _player_strike(self, enemy: Enemy) -> list[str]:
        p = self.player
        rng = self.game.rng

        # 先问技能系统要不要放技能；它返回 None 就照旧普攻
        skill_sys = self.game.systems.get("skill")
        if skill_sys is not None:
            plan = skill_sys.choose_action(enemy.hp / max(1.0, enemy.max_hp))
            if plan is not None:
                return self._cast(plan, enemy)

        dmg = max(1.0, p.atk * rng.between(0.9, 1.15) - enemy.defense * 0.5)
        crit = rng.chance(0.12)
        if crit:
            dmg *= 1.8
        enemy.hp -= dmg
        # 残血向上取整：还剩 0.3 点也是活着，显示成 0 会让人以为已经倒下
        left = max(0, math.ceil(enemy.hp)) if enemy.alive() else 0
        if crit:
            flavor = rng.choice(PLAYER_CRIT_FLAVOR)
            return [f"{flavor}一击要害！{enemy.name} 受到 {fmt_num(dmg)} 点伤害"
                    f"（暴击），剩余 {left}"]
        flavor = rng.choice(PLAYER_STRIKE_FLAVOR)
        return [f"{flavor}你击中 {enemy.name}，造成 {fmt_num(dmg)} 点伤害，剩余 {left}"]

    def _cast(self, plan: dict, enemy: Enemy) -> list[str]:
        """结算技能。

        灵力已在 SkillSystem.choose_action 里扣过，这里只管效果与日志。
        power 也已按所属功法的熟练度缩放过。
        """
        p = self.player
        rng = self.game.rng
        sk = plan["skill"]
        power = plan["power"]
        # 叙事文案来自 config/skills.py 的 cast 字段（attack 类可带 {enemy} 占位），
        # 未配置（如第三方功法）则退回「施展【名】」的通用句式。
        cast = sk.cast.format(enemy=enemy.name) if sk.cast else ""
        head = f"你施展【{sk.name}】"
        lead = f"{head}，{cast}。" if cast else f"{head}。"

        if sk.kind == "attack":
            dmg = max(1.0, p.atk * power * rng.between(0.95, 1.10)
                      - enemy.defense * 0.5)
            crit = rng.chance(0.12)
            if crit:
                dmg *= 1.8
            enemy.hp -= dmg
            left = max(0, math.ceil(enemy.hp)) if enemy.alive() else 0
            logs = [f"{lead}{enemy.name} 受到 {fmt_num(dmg)} 点伤害"
                    f"{'（暴击）' if crit else ''}，剩余 {left}"]
            if sk.hp_cost_ratio > 0:
                backlash = p.max_hp * sk.hp_cost_ratio
                # 反噬不该直接把自己玩死，兜到 1 点血
                p.hp = max(1.0, p.hp - backlash)
                logs.append(f"强行动法反噬己身，气血 -{fmt_num(backlash)}"
                            f"（剩余 {fmt_num(p.hp)}）")
            return logs

        if sk.kind == "heal":
            got = p.heal_hp(p.max_hp * power)
            return [f"{lead}气血回复 {fmt_num(got)}（{fmt_num(p.hp)}/{fmt_num(p.max_hp)}）"]

        if sk.kind == "guard":
            self._guard = power
            return [f"{lead}下次受创减免 {power * 100:.0f}%"]

        if sk.kind == "recover":
            got = p.heal_mp(p.max_mp * power)
            return [f"{lead}灵力回复 {fmt_num(got)}（{fmt_num(p.mp)}/{fmt_num(p.max_mp)}）"]

        return [head]

    def _enemy_strike(self, enemy: Enemy) -> list[str]:
        p = self.player
        rng = self.game.rng
        dmg = max(1.0, enemy.atk * rng.between(0.9, 1.15) - p.defense * 0.5)
        note = ""
        if self._guard > 0:
            dmg *= 1.0 - self._guard
            note = f"（护体减免 {self._guard * 100:.0f}%）"
            self._guard = 0.0      # 护到下次出手为止，用掉即失效
        p.damage(dmg, reason="战斗")
        flavor = rng.choice(ENEMY_STRIKE_FLAVOR)
        return [f"{flavor}{enemy.name} 反扑，你受到 {fmt_num(dmg)} 点伤害{note}，"
                f"剩余 {fmt_num(max(0, p.hp))}"]

    def _stones_for(self, enemy: Enemy) -> int:
        stone_bonus = self.game.bonuses.value("stone_gain")     # 来自功法/产业/门派等全部来源
        return stones_for_realm(self.player.realm_key, enemy.mult, stone_bonus)

    def _victory(self, enemy: Enemy) -> list[str]:
        p = self.player
        logs = [self.game.rng.choice(VICTORY_LINES).format(enemy=enemy.name)]
        gained = p.add_exp(enemy.exp_reward)
        if gained >= 0.5:
            logs.append(f"修为 +{fmt_num(gained)}")

        for item_id, count, chance in enemy.drops:
            if self.game.rng.chance(chance):
                p.inventory.add(item_id, count)
                from ..config import items as item_config
                logs.append(f"拾获 {item_config.get_item(item_id).name} ×{count}")

        stones = self._stones_for(enemy)
        if stones > 0:
            p.spirit_stones += stones
            logs.append(f"灵石 +{stones}")

        # handler 往 payload["logs"] 里塞各自的收获（如功法熟练度）
        won = self.game.bus.emit(
            TOPIC_COMBAT_VICTORY,
            {"enemy": enemy.name, "exp": enemy.exp_reward, "danger": enemy.atk,
             "tier": enemy.tier, "logs": []},
        )
        logs.extend(won.get("logs", []))
        return logs

    def _defeat(self, enemy: Enemy) -> list[str]:
        """濒死护持：不死，但付出代价。"""
        p = self.player
        p.alive = True
        p.hp = max(1.0, p.max_hp * 0.1)
        lost_exp = p.exp * 0.2
        p.exp = max(0.0, p.exp - lost_exp)
        lost_stones = min(p.spirit_stones, 50)
        p.spirit_stones -= lost_stones
        return [
            f"你重伤濒死，千钧一发之际遁走，{enemy.name} 未再追击。",
            f"修为散去 {fmt_num(lost_exp)}，灵石遗失 {fmt_num(lost_stones)}。",
        ]

    # ---------- 命令 ----------
    def commands(self) -> list[Command]:
        def _hunt(args: list[str]) -> None:
            tier = args[0] if args and args[0] in TIERS else "normal"
            self.game.emit_logs(self.fight(self.spawn(tier)))
            # 日常追踪：猎妖（无论胜负都算「做了」）
            daily = self.game.systems.get("daily")
            if daily is not None:
                daily.track("hunt")

        return [Command("hunt", "外出猎妖", "hunt [weak|normal|elite|boss]", _hunt)]
