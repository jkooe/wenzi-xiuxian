"""技能系统：让功法真正打出去。

定位：
    功法此前只影响属性（被动加成），战斗仍是普攻 + 暴击，灵力在战斗中完全闲置。
    本系统把「灵力」这条线接活：修习功法即得签名技能，战斗中自动施放。

为什么是「自动施法 + 策略预设」而不是「回合手动选技能」：
    fight(enemy) -> list[str] 是非交互式契约，一次打完全场返回日志。调用方有四处：
        effect_battle（事件 {"type":"battle"} 自动开战，无处输入）
        hunt 命令
        dungeon.next()（秘境层内战斗）
        测试与长线推演脚本（依赖一次调用打完）
    改成回合手动会让这四处全部失效，且事件、秘境里的自动战斗根本没有输入入口。
    因此决策权交给玩家预设的「策略」，战斗仍是一次调用打完。

职责边界：
    本系统只做「决策 + 付费 + 计时」，不碰敌人数据——
    choose_action() 返回一个计划（含已缩放的威力与已扣除的灵力），
    由战斗系统负责结算伤害/治疗并写日志，因为它才持有 enemy 与日志格式。

与熟练度的关系：
    技能威力按所属功法的熟练度缩放（复用 config/arts.py 的 proficiency_scale），
    刚学会六成、练满十成，与属性加成的取舍逻辑完全一致：
    换新功法不等于立刻变强。
"""

from __future__ import annotations

from typing import Any

from ..config import arts as art_config
from ..config import skills as skill_config
from ..core.base_system import Command, GameSystem

# 三档策略
STRATEGIES = ("conservative", "balanced", "aggressive")
STRATEGY_LABELS = {
    "conservative": "保守",
    "balanced": "均衡",
    "aggressive": "激进",
}
STRATEGY_HINTS = {
    "保守": "保命优先，气血 55% 以下治疗、70% 以下护体，留 40% 灵力不动",
    "均衡": "见机行事，气血 35% 以下治疗、45% 以下护体，留 20% 灵力",
    "激进": "倾泻灵力只求杀伤，气血 25% 以下才治疗，不放护体",
}

# 每档策略的灵力保留线：放完之后灵力比例不得低于此值
MP_RESERVE = {"conservative": 0.40, "balanced": 0.20, "aggressive": 0.0}
# 触发治疗 / 护体的气血比例线（激进档 guard 线为 0，即从不护体）
HEAL_BELOW = {"conservative": 0.55, "balanced": 0.35, "aggressive": 0.25}
GUARD_BELOW = {"conservative": 0.70, "balanced": 0.45, "aggressive": 0.0}
# 灵力低于此比例时，优先回灵（回灵本身不耗灵力，是「蓝空了」的出口）
RECOVER_BELOW = 0.15
# 敌人残血到此比例以下，优先补刀而非自保（激进/均衡档）
FINISH_BELOW = 0.20

_STRATEGY_ALIASES = {
    "conservative": "conservative", "conserv": "conservative", "保守": "conservative",
    "balanced": "balanced", "balance": "balanced", "均衡": "balanced",
    "aggressive": "aggressive", "aggro": "aggressive", "激进": "aggressive",
}


class SkillSystem(GameSystem):
    id = "skill"
    name = "技能"

    def __init__(self) -> None:
        super().__init__()
        self.strategy: str = "balanced"
        # skill_id -> 剩余冷却回合。每场战斗开始时清空，不进存档。
        self.cooldowns: dict[str, int] = {}

    # ---------- 战斗节拍 ----------
    def begin_battle(self) -> None:
        """每场战斗开打前清空冷却。"""
        self.cooldowns.clear()

    def end_battle(self) -> None:
        """战斗结束后也清空。

        冷却只对「本场战斗」有意义，战后残留会让 skill 面板一直挂着
        「[冷却中 剩 N]」，看起来像永久失效。
        """
        self.cooldowns.clear()

    def next_round(self) -> None:
        """每个回合开始时走一格冷却。"""
        for sid in list(self.cooldowns):
            self.cooldowns[sid] -= 1
            if self.cooldowns[sid] <= 0:
                del self.cooldowns[sid]

    # ---------- 可用技能 ----------
    def known(self) -> list[dict[str, Any]]:
        """已修习功法带来的全部技能（不管冷却与灵力）。

        返回 [{"skill", "art", "scale", "power", "cost"}]，power 已按熟练度缩放。
        """
        arts_sys = self.game.systems.get("arts")
        if arts_sys is None:
            return []
        p = self.player
        out: list[dict[str, Any]] = []
        for art_id, prof in arts_sys.learned.items():
            art = art_config.get_art(art_id)
            scale = art_config.proficiency_scale(prof, art.max_proficiency)
            for sid in art.skills:
                try:
                    sk = skill_config.get_skill(sid)
                except KeyError:
                    continue
                out.append({
                    "skill": sk,
                    "art": art_id,
                    "scale": scale,
                    "power": skill_config.scaled_power(sk, scale),
                    "cost": int(round(p.max_mp * sk.mp_cost)),
                })
        return out

    def _usable(self, reserve: float) -> list[dict[str, Any]]:
        """过滤掉冷却中与「付不起 / 会跌破灵力保留线」的技能。"""
        p = self.player
        floor = p.max_mp * reserve
        out = []
        for item in self.known():
            if self.cooldowns.get(item["skill"].id, 0) > 0:
                continue
            cost = item["cost"]
            # 回灵不耗灵力（cost=0），因此永远付得起——这正是它在蓝空时仍有意义的缘故
            if cost > 0 and (cost > p.mp or p.mp - cost < floor):
                continue
            out.append(item)
        return out

    # ---------- 决策 ----------
    def choose_action(self, enemy_hp_ratio: float = 1.0) -> dict[str, Any] | None:
        """本回合要不要放技能、放哪个。

        返回计划 dict（含已按熟练度缩放的 power），同时**已扣除灵力并开始冷却**；
        返回 None 表示这回合老老实实普攻。

        结算（伤害/治疗/写日志）由战斗系统负责——它才持有 enemy。
        """
        p = self.player
        hp_ratio = p.hp / max(1.0, p.max_hp)
        mp_ratio = p.mp / max(1.0, p.max_mp)

        reserve = MP_RESERVE.get(self.strategy, 0.20)
        cands = self._usable(reserve)
        if not cands:
            return None

        heal_line = HEAL_BELOW.get(self.strategy, 0.35)
        guard_line = GUARD_BELOW.get(self.strategy, 0.45)

        # 敌人残血：能补刀就补刀，别再自保（保守档除外，它优先活着）
        finishing = (self.strategy != "conservative" and enemy_hp_ratio <= FINISH_BELOW)
        # 护体对任何策略都是浪费：敌人快死了，减伤救不了你，多点输出才能结束战斗。
        # 不这么处理，保守档会一路护体到打满 40 回合仍未分胜负。
        finishing_guard = enemy_hp_ratio <= FINISH_BELOW

        if not finishing:
            # 1) 保命：气血见底先治
            if hp_ratio < heal_line:
                pick = self._pick(cands, "heal")
                if pick:
                    return self._commit(pick)
            # 2) 其次护体（激进档 guard_line 为 0，走不到这里）
            if hp_ratio < guard_line and not finishing_guard:
                pick = self._pick(cands, "guard")
                if pick:
                    return self._commit(pick)
            # 3) 灵力见底：回灵不耗蓝，是唯一还能做的事
            if mp_ratio < RECOVER_BELOW:
                pick = self._pick(cands, "recover")
                if pick:
                    return self._commit(pick)

        # 4) 攻伐：优先威力最高的一击
        pick = self._pick(cands, "attack", strongest=True)
        if pick:
            return self._commit(pick)

        # 5) 没有攻击技能可用（多半在冷却）：辅助技能只在「不浪费」时才占回合
        for kind in ("recover", "heal", "guard"):
            pick = self._pick(cands, kind)
            if pick and self._worth(kind, pick, hp_ratio, mp_ratio, guard_line):
                return self._commit(pick)
        return None

    @staticmethod
    def _worth(kind: str, plan: dict[str, Any], hp_ratio: float,
               mp_ratio: float, guard_line: float) -> bool:
        """攻击技能都放不出时，这个辅助技能值不值得占掉一回合。

        满血放疗伤、满蓝放回灵是纯浪费（日志里就是「气血回复 1」），
        那时宁可老老实实普攻。判据：八成药力能真正落进去才放。
        """
        if kind == "guard":
            return hp_ratio < guard_line
        if kind == "heal":
            return hp_ratio + plan["power"] * 0.8 <= 1.0
        return mp_ratio + plan["power"] * 0.8 <= 1.0

    @staticmethod
    def _pick(cands: list[dict[str, Any]], kind: str,
              strongest: bool = False) -> dict[str, Any] | None:
        pool = [c for c in cands if c["skill"].kind == kind]
        if not pool:
            return None
        return max(pool, key=lambda c: c["power"]) if strongest else pool[0]

    def _commit(self, plan: dict[str, Any]) -> dict[str, Any]:
        """扣灵力并起冷却。

        冷却记的是「本回合结束后还要压几回合」，所以存 cooldown + 1：
        下回合开始 next_round() 先减 1，减到 0 才解禁，
        这样配置里写 cooldown=2 就真的是压 2 回合。
        """
        p = self.player
        sk = plan["skill"]
        if plan["cost"] > 0:
            p.mp = max(0.0, p.mp - plan["cost"])
        if sk.cooldown > 0:
            self.cooldowns[sk.id] = sk.cooldown + 1
        return plan

    # ---------- 展示 ----------
    @staticmethod
    def _power_text(sk, power: float) -> str:
        if sk.kind == "attack":
            return f"威力 {power * 100:.0f}%"
        if sk.kind == "heal":
            return f"回复气血 {power * 100:.0f}%"
        if sk.kind == "guard":
            return f"减伤 {power * 100:.0f}%"
        return f"回复灵力 {power * 100:.0f}%"

    def info(self) -> list[str]:
        """技能一览 + 当前策略。"""
        label = STRATEGY_LABELS[self.strategy]
        items = self.known()
        if not items:
            return [
                "你尚未修习任何功法，身无半点技艺，只凭本能出手。",
                "（art list 查看功法　art learn <id> 修习，即得该功法签名技能）",
            ]

        lines = [f"可用技能（施法策略：{label}　skill strategy 切换）："]
        for item in items:
            sk = item["skill"]
            art = art_config.get_art(item["art"])
            cd = f"冷却 {sk.cooldown}" if sk.cooldown else "无冷却"
            kind = skill_config.SKILL_KINDS.get(sk.kind, sk.kind)
            cost = "不耗灵力" if item["cost"] <= 0 else f"灵力 {sk.mp_cost * 100:.0f}%"
            left = self.cooldowns.get(sk.id, 0)
            cd_note = f"　[冷却中 剩 {left}]" if left > 0 else ""
            lines.append(
                f"  {sk.name}（{kind}）　{cost}　{cd}　"
                f"{self._power_text(sk, item['power'])}{cd_note}"
            )
            lines.append(
                f"　　　{sk.desc}"
            )
            lines.append(
                f"　　　出自《{art.name}》　当前发挥 {item['scale'] * 100:.0f}%"
            )
        lines.append(f"  策略「{label}」：{STRATEGY_HINTS[label]}")
        return lines

    # ---------- 命令 ----------
    def commands(self) -> list[Command]:
        def _skill(args: list[str]) -> None:
            sub = args[0].lower() if args else ""
            if sub in ("strategy", "策略"):
                if len(args) < 2:
                    self.log("用法：skill strategy <保守|均衡|激进>")
                    return
                key = _STRATEGY_ALIASES.get(args[1].lower()) \
                    or _STRATEGY_ALIASES.get(args[1])
                if key is None:
                    self.log(f"没有「{args[1]}」这种策略，可选：保守 / 均衡 / 激进")
                    return
                self.strategy = key
                self.log(f"施法策略改为「{STRATEGY_LABELS[key]}」。")
                self.game.emit_logs(self.info())
                return
            self.game.emit_logs(self.info())
            self.log("  skill strategy <保守|均衡|激进> 切换施法策略")

        return [Command("skill", "技能（可设施法策略）",
                        "skill [strategy 保守|均衡|激进]", _skill)]

    # ---------- 持久化 ----------
    def to_dict(self) -> dict[str, Any]:
        # 冷却是每场战斗的临时状态，不进存档
        return {"strategy": self.strategy}

    def load_state(self, data: dict[str, Any]) -> None:
        strategy = data.get("strategy")
        self.strategy = strategy if strategy in STRATEGIES else "balanced"
        self.cooldowns.clear()
