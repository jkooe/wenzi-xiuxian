"""秘境探索系统（线性层数推进）。

四条规则：
    1. 层内抽签 —— 每层随机是「妖兽 / 机缘 / 遗宝 / 静室」，底层必是守关之敌
    2. 进度存档 —— 随时 dungeon flee 退出，回来从原层继续；通关后按秘境冷却日数方可再入
    3. 战败退一层 —— 濒死护持后被打退一层，已得之物不吐出来，可整备再战（不掉装备境界）
    4. 底层大奖 —— 守关之敌倒下给一次性厚赏，是秘境的主要收益来源

难度锚点（实现上最容易想错的地方）：
    敌人属性是按玩家境界等比缩放的（combat.spawn），所以「第 7 层」和「第 1 层」
    若同 tier，打起来一样难，层数就成了摆设。秘境的难度只靠 tier 拉开：
    浅层 weak → 中层 normal → 深层 elite → 底层 boss，层数只是 tier 的载体。

奖励一律走 core/effects.py 的效果 DSL，修为用 exp_ratio（按当前境界需求比例）：
    写固定值在炼气期能直接飞升、到渡劫期等于零——和「灵石挂在指数增长量上」是同一类坑。
"""

from __future__ import annotations

from typing import Any

from ..config import dungeons as dungeon_config
from ..config.dungeons import (
    KIND_BATTLE,
    KIND_BOSS,
    KIND_EVENT,
    KIND_LABELS,
    KIND_REST,
    KIND_TREASURE,
    TIER_LABELS,
)
from ..config.realms import RealmRegistry
from ..core.base_system import Command, GameSystem, TOPIC_COMBAT_VICTORY
from ..core.effects import apply_effects
from ..core.event_system import SCENE_DUNGEON
from .combat import FIGHT_STAMINA, TIERS

HOURS_PER_FLOOR = 1.0        # 层间穿行耗时（战斗层另算，fight 内部已推 2 时辰）
FLOOR_EXP_RATIO = 0.015      # 每通过一层的修为 = 本层需求 × 1.5%（闯关修为）
DEFEAT_HP_RATIO = 0.115      # 战后气血低于此比例视为「败退」而非「久战不下」
RETREAT_HEAL_HP = 0.25       # 战败退层后的整备回复（防残血连败的死亡螺旋）
RETREAT_HEAL_MP = 0.20
LOSE_STREAK_WARN = 3         # 连败几次后明说「此路不通」：进度存档不该变成无限刷败退

# 层类型的中英文别名，供命令识别
SUB_ENTER = ("enter", "in", "进入", "入", "进")
SUB_NEXT = ("next", "n", "下", "深", "深入", "继续")
SUB_FLEE = ("flee", "out", "退", "退出", "撤")
SUB_ABANDON = ("abandon", "give", "弃", "放弃", "重置")
SUB_LIST = ("list", "all", "目录", "列表")


class DungeonSystem(GameSystem):
    id = "dungeon"
    name = "秘境"

    def __init__(self) -> None:
        super().__init__()
        self.run: dict[str, Any] | None = None   # 未完成的进度：{id, floor, awaiting}
        self.cooldowns: dict[str, int] = {}      # 秘境 id -> 可以再入的日期
        self.cleared: dict[str, int] = {}        # 秘境 id -> 通关次数
        self.deepest: dict[str, int] = {}        # 秘境 id -> 历史最深层
        self._won_last = False                   # 最近一场战斗是否获胜（靠事件总线回传）

    # ---------- 装配 ----------
    def on_bind(self) -> None:
        # 战斗胜负只能靠总线回传：fight() 只返回日志，不返回结果
        self.game.bus.on(TOPIC_COMBAT_VICTORY, self._mark_victory)
        self.game.register_effect("dungeon", self.effect_dungeon)

    def _mark_victory(self, payload: dict[str, Any]) -> None:
        self._won_last = True

    # ---------- 查询 ----------
    @property
    def current(self):
        """当前所在秘境的定义，不在秘境中则为空。"""
        if not self.run:
            return None
        try:
            return dungeon_config.get_dungeon(self.run["id"])
        except KeyError:
            return None

    def is_locked(self, dungeon) -> str:
        """返回不可进入的原因，空串表示可入。"""
        p = self.player
        if not RealmRegistry.within(p.realm_key, min_realm=dungeon.min_realm):
            need = RealmRegistry.get(dungeon.min_realm).name
            return f"修为不足，此处需 {need} 以上（你现为 {p.realm_name}）"
        until = self.cooldowns.get(dungeon.id, 0)
        if self.game.day < until:
            return f"{dungeon.name} 尚在闭息，还需 {until - self.game.day} 日方可再入"
        return ""

    # ---------- 进入 ----------
    def enter(self, dungeon_id: str) -> list[str]:
        p = self.player
        if not p.alive:
            return ["你已身死道消。"]
        try:
            dungeon = dungeon_config.get_dungeon(dungeon_id)
        except KeyError:
            return [f"世间并无「{dungeon_id}」这处秘境。（dungeon list 查看）"]

        if self.run and self.run["id"] != dungeon.id:
            old = dungeon_config.get_dungeon(self.run["id"])
            return [f"你已在 {old.name} 第 {self.run['floor']}/{old.depth} 层，"
                    f"先 dungeon flee 退出，或 dungeon abandon 放弃此处进度。"]

        locked = self.is_locked(dungeon)
        if locked and not self.run:
            return [locked]

        if self.run:                       # 接着上次的进度继续
            logs = [f"重返 {dungeon.name}，直入第 {self.run['floor']}/{dungeon.depth} 层。"]
        else:
            self.run = {"id": dungeon.id, "floor": 1, "awaiting": False, "streak": 0}
            logs = [f"你把气息敛入丹田，一步跨入 {dungeon.name}。",
                    f"　{dungeon.desc}"]
        logs.extend(self._floor_intro(dungeon))
        logs.extend(self.game.advance_time(1))
        return logs

    def _floor_intro(self, dungeon) -> list[str]:
        floor_no = self.run["floor"]
        floor = dungeon.floors[floor_no - 1]
        tag = TIER_LABELS.get(floor.tier, floor.tier)
        logs = ["", f"— 第 {floor_no}/{dungeon.depth} 层 · {floor.name} —"]
        logs.append(f"　{floor.desc}")
        if floor.is_boss():
            logs.append("　此处气息压得人喘不过气，退路已在身后合拢。")
        logs.append(f"　层内凶险：{tag}　{self._danger_text(floor)}（dungeon next 深入）")
        return logs

    def _danger_text(self, floor) -> str:
        """预估本层妖兽强度，让玩家自己判断进不进得去。

        只按 spawn() 的公式折算中位值，不真的生成敌人（避免白白消耗随机序列）。
        """
        p = self.player
        mult = TIERS.get(floor.tier, 1.0)
        hp = p.max_hp * 0.75 * mult
        atk = p.atk * 0.55 * mult
        return f"（妖兽约气血 {hp:.0f}、攻击 {atk:.0f}；你气血 {p.hp:.0f}/{p.max_hp:.0f}、攻击 {p.atk:.0f}）"

    # ---------- 深入 ----------
    def next(self) -> list[str]:
        p = self.player
        if not p.alive:
            return ["你已身死道消。"]
        if not self.run:
            return ["你并不在秘境之中。（dungeon list 查看可去之处）"]

        dungeon = dungeon_config.get_dungeon(self.run["id"])
        event_system = self.game.systems.get("event")

        # 上次抽到机缘，先了断再走
        if self.run.get("awaiting"):
            if event_system and event_system.pending:
                return ["眼前这段机缘尚待决断。", *(event_system.show_pending()),
                        "　choose N 做出选择（choose 0 抽身离去），再 dungeon next 继续深入。"]
            self.run["awaiting"] = False
            logs = ["你收摄心神，继续前行。"]
            logs.extend(self._advance(dungeon))
            return logs

        if event_system and event_system.pending:
            return ["尚有未决之事。（choose N 了断后再 dungeon next）"]

        # 精力门槛必须自己先查：fight() 缺精力时直接 return，敌人满血存活，
        # 会被误判成「战败退一层」；事件层同理，trigger 缺精力时会白嫖一次前进。
        if p.stamina < dungeon.stamina:
            return [f"精力不济，难以举步（需 {dungeon.stamina}，现有 {p.stamina:.0f}）。",
                    "　rest 调息，或 dungeon flee 退出——进度不会丢。"]

        floor_no = self.run["floor"]
        floor = dungeon.floors[floor_no - 1]

        if floor.is_boss():
            return self._resolve_floor(dungeon, floor, KIND_BOSS)

        kind = self.game.rng.weighted_choice(
            list(floor.kinds.keys()), weight_of=lambda k: floor.kinds[k]
        )
        return self._resolve_floor(dungeon, floor, kind)

    def _resolve_floor(self, dungeon, floor, kind: str) -> list[str]:
        logs = [f"【{KIND_LABELS.get(kind, kind)}】"]
        if kind in (KIND_BATTLE, KIND_BOSS):
            combat = self.game.systems.get("combat")
            if combat is None:
                return ["此间并无争斗可言。"]
            if self.player.stamina < FIGHT_STAMINA:
                return [f"气血尚可，精力却已见底（需 {FIGHT_STAMINA}，"
                        f"现有 {self.player.stamina:.0f}），这一层暂且过不去。",
                        "　rest 调息，或 dungeon flee 退出——进度不会丢。"]
            logs.extend(self._fight(combat, floor.tier))
            outcome = "win" if self._won_last else ("lose" if self._exhausted() else "draw")
            if outcome == "win":
                logs.extend(self._advance(dungeon))
            elif outcome == "lose":
                logs.extend(self._retreat(dungeon))
            else:
                logs.append("　久战不下，你暂且退开，这一层仍未过去。")
                logs.extend(self.game.advance_time(HOURS_PER_FLOOR))
            return logs

        if kind == KIND_EVENT:
            event_system = self.game.systems.get("event")
            if event_system is None:
                logs.extend(self._advance(dungeon))
                return logs
            # 场景标签隔离事件池，免得在洞天里撞见「宗门招徒」这类户外机缘
            self.run["awaiting"] = True
            logs.extend(event_system.trigger(force=True, scene=SCENE_DUNGEON))
            if not event_system.pending:
                self.run["awaiting"] = False
                logs.extend(self._advance(dungeon))
            else:
                logs.append("　choose N 决定如何应对，再 dungeon next 继续深入。")
            return logs

        if kind == KIND_TREASURE:
            if not self.player.spend_stamina(dungeon.stamina):
                return [f"精力不济，难以翻检此间遗藏。（rest 调息，或 dungeon flee 暂退）"]
            found = self.game.rng.choice(dungeon.treasures or ((),))
            gained = apply_effects(self.game, list(found))
            if gained:
                logs.extend("　" + line for line in gained)
            else:
                logs.append("　石室空空如也，只有一层薄灰。")
            logs.extend(self._advance(dungeon))
            return logs

        if kind == KIND_REST:
            p = self.player
            p.heal_hp(p.max_hp * 0.35)
            p.heal_mp(p.max_mp * 0.50)
            # 静室回精力属于「预算外恢复」，收窄到 10（原本 30 与每日预算设计冲突过大）；
            # 主要收益改为回气血/灵力，探索福利仍在，但不再能靠它绕过精力预算。
            p.stamina = min(100.0, p.stamina + 10.0)
            logs.append("　一间尚且完好的静室，你盘膝调息了片刻。"
                        f"（气血 {p.hp:.0f}/{p.max_hp:.0f}　灵力 {p.mp:.0f}/{p.max_mp:.0f}"
                        f"　精力 {p.stamina:.0f}）")
            logs.extend(self._advance(dungeon))
            return logs

        return logs

    def _fight(self, combat, tier: str) -> list[str]:
        """开战并回传日志。胜负由事件总线标记，这里不改写战斗系统。"""
        self._won_last = False
        enemy = combat.spawn(tier)
        return ["　" + line for line in combat.fight(enemy)]

    def _exhausted(self) -> bool:
        """战后是否濒死。

        combat 的濒死护持会把气血压回 10%，这是「打输了」的唯一外部痕迹。
        判据是近似的：若玩家战前本就残血，平局也可能被算成败退，
        但两者都不算通过本层，差别只在退不退一层。
        """
        p = self.player
        return p.hp <= p.max_hp * DEFEAT_HP_RATIO

    # ---------- 推进与退却 ----------
    def _advance(self, dungeon) -> list[str]:
        """通过一层：给「闯关修为」并按深度记录。"""
        from ..core.effects import apply_effects

        apply_effects(self.game, [{"type": "exp_ratio", "value": FLOOR_EXP_RATIO}])
        floor_no = self.run["floor"]
        self.deepest[dungeon.id] = max(self.deepest.get(dungeon.id, 0), floor_no)
        if floor_no >= dungeon.depth:
            return self._complete(dungeon)

        self.run["floor"] = floor_no + 1
        self.run["streak"] = 0        # 过一层即清零连败计数
        logs = [f"　你越过此层，向下走去。（{floor_no} → {floor_no + 1}）"]
        logs.extend(self.game.advance_time(HOURS_PER_FLOOR))
        logs.extend(self._floor_intro(dungeon))
        return logs

    def _retreat(self, dungeon) -> list[str]:
        p = self.player
        floor_no = self.run["floor"]
        logs = ["　你被逼退回上一层，好在所得之物都还在。"
                "（战败只退一层，不掉装备、不掉境界）"]
        if floor_no > 1:
            self.run["floor"] = floor_no - 1
            back = dungeon.floors[floor_no - 2]
            logs.append(f"　退至第 {floor_no - 1} 层「{back.name}」，先整备再战。")
        else:
            logs.append("　已在第一层，无路可退——先 rest 调息，或 dungeon flee 暂避锋芒。")
        # 退层即整备：否则残血硬冲会一路连败、连退到底，形成无从翻身的螺旋
        healed_hp = p.heal_hp(p.max_hp * RETREAT_HEAL_HP)
        healed_mp = p.heal_mp(p.max_mp * RETREAT_HEAL_MP)
        logs.append(f"　靠在石壁上调息片刻，气血 +{healed_hp:.0f}　灵力 +{healed_mp:.0f}"
                    f"（{p.hp:.0f}/{p.max_hp:.0f}）。")
        streak = self.run.get("streak", 0) + 1
        self.run["streak"] = streak
        if streak >= LOSE_STREAK_WARN:
            logs.append(f"　连败 {streak} 次——此处已非你眼下能撼动之地。"
                        f"dungeon flee 退出，历练些时日再来，进度不会丢。")
        logs.extend(self.game.advance_time(HOURS_PER_FLOOR))
        return logs

    def _complete(self, dungeon) -> list[str]:
        p = self.player
        logs = ["", f"=== 通关 {dungeon.name}（{dungeon.depth} 层）==="]
        logs.extend("　" + line for line in apply_effects(self.game, list(dungeon.boss_reward)))
        self.cleared[dungeon.id] = self.cleared.get(dungeon.id, 0) + 1
        self.cooldowns[dungeon.id] = self.game.day + dungeon.cooldown
        self.run = None
        logs.append(f"　{dungeon.name} 重归沉寂，{dungeon.cooldown} 日后方可再来"
                    f"（累计通关 {self.cleared[dungeon.id]} 次）。")
        # 修为满层后 add_exp 不再累积，此时通关的修为奖励等于白拿，明说比让玩家蒙在鼓里好
        if p.can_breakthrough():
            logs.append("　你修为已满，此行的修为奖赏无处落袋——先 breakthrough 冲关，再入秘境。")
        logs.extend(self.game.advance_time(HOURS_PER_FLOOR))
        self.game.check_game_over()
        return logs

    # ---------- 退出 / 放弃 ----------
    def flee(self) -> list[str]:
        if not self.run:
            return ["你本就不在秘境之中。"]
        dungeon = dungeon_config.get_dungeon(self.run["id"])
        logs = [f"你循着来路退出 {dungeon.name}，"
                f"进度停在第 {self.run['floor']}/{dungeon.depth} 层"
                f"（下次 dungeon enter {dungeon.id} 由此继续）。"]
        logs.extend(self.game.advance_time(1))
        return logs

    def abandon(self) -> list[str]:
        if not self.run:
            return ["并无未了的秘境之行。"]
        dungeon = dungeon_config.get_dungeon(self.run["id"])
        floor_no = self.run["floor"]
        self.run = None
        self.cooldowns[dungeon.id] = self.game.day + dungeon.cooldown
        return [f"你断了 {dungeon.name} 的念想，自第 {floor_no} 层抽身而退。"
                f"（进度清空，{dungeon.cooldown} 日后方可重入）"]

    # ---------- 展示 ----------
    def info(self) -> list[str]:
        lines = ["秘境："]
        if self.run:
            dungeon = dungeon_config.get_dungeon(self.run["id"])
            awaiting = "（机缘待决，choose N 后可继续）" if self.run.get("awaiting") else ""
            lines.append(f"　所在：{dungeon.name}　第 {self.run['floor']}/{dungeon.depth} 层{awaiting}")
            lines.append(f"　历史最深：第 {self.deepest.get(dungeon.id, 0)} 层"
                         f"　通关 {self.cleared.get(dungeon.id, 0)} 次")
        else:
            lines.append("　你此刻并不在任何秘境之中。")
        lines.append("　dungeon list 查看去处　dungeon enter <id> 进入　"
                     "dungeon next 深入　dungeon flee 退出")
        return lines

    def catalog(self) -> list[str]:
        p = self.player
        lines = ["秘境目录（dungeon enter <id> 进入）："]
        for dungeon in dungeon_config.DUNGEONS.values():
            locked = self.is_locked(dungeon)
            if self.run and self.run["id"] == dungeon.id:
                state = f"进行中：第 {self.run['floor']}/{dungeon.depth} 层"
            elif locked:
                state = locked
            else:
                state = "可入"
            depth = f"{dungeon.depth} 层"
            lines.append(f"　【{dungeon.name}】{depth}　需 "
                         f"{RealmRegistry.get(dungeon.min_realm).name}　[{state}]　{dungeon.id}")
            lines.append(f"　　　{dungeon.desc}")
            lines.append(f"　　　冷却 {dungeon.cooldown} 日"
                         f"　通关 {self.cleared.get(dungeon.id, 0)} 次"
                         f"　最深 {self.deepest.get(dungeon.id, 0)}/{dungeon.depth} 层")
        return lines

    # ---------- 效果 DSL ----------
    def effect_dungeon(self, player, eff: dict) -> list[str]:
        """事件 JSON 里写 {"type": "dungeon", "id": "luoyun", "floor": 2} 直接送入某层。"""
        did = eff.get("id", "")
        if did not in dungeon_config.DUNGEONS:
            return [f"[未知秘境: {did}]"]
        dungeon = dungeon_config.get_dungeon(did)
        floor_no = max(1, min(int(eff.get("floor", 1)), dungeon.depth))
        # 不清冷却：事件若能把玩家直接送进秘境并清掉冷却，就等于绕开「通关后 N 日」的限制，
        # 反复触发事件即可无限刷 boss 奖励。保留冷却检查，由 is_locked 决定是否放行。
        self.run = {"id": dungeon.id, "floor": floor_no, "awaiting": False, "streak": 0}
        logs = [f"眼前光景一变，你已身在 {dungeon.name}。"]
        logs.extend(self._floor_intro(dungeon))
        return logs

    # ---------- 命令 ----------
    def commands(self) -> list[Command]:
        def _dungeon(args: list[str]) -> None:
            sub = (args[0].lower() if args else "")
            if sub in SUB_LIST:
                self.game.emit_logs(self.catalog())
            elif sub in SUB_ENTER:
                if len(args) < 2:
                    self.game.emit_logs(self.catalog())
                    return
                self.game.emit_logs(self.enter(args[1]))
            elif sub in SUB_NEXT:
                self.game.emit_logs(self.next())
            elif sub in SUB_FLEE:
                self.game.emit_logs(self.flee())
            elif sub in SUB_ABANDON:
                self.game.emit_logs(self.abandon())
            else:
                self.game.emit_logs(self.info())
                self.log("  dungeon list 目录　dungeon enter <id> 进入　"
                         "dungeon next 深入　dungeon flee 退出　dungeon abandon 放弃")

        return [
            Command("dungeon", "秘境探索（enter/next/flee）",
                    "dungeon [list|enter|next|flee|abandon]", _dungeon),
        ]

    # ---------- 持久化 ----------
    def to_dict(self) -> dict[str, Any]:
        return {
            "run": dict(self.run) if self.run else None,
            "cooldowns": dict(self.cooldowns),
            "cleared": dict(self.cleared),
            "deepest": dict(self.deepest),
        }

    def load_state(self, data: dict[str, Any]) -> None:
        run = data.get("run")
        if isinstance(run, dict) and run.get("id") in dungeon_config.DUNGEONS:
            dungeon = dungeon_config.get_dungeon(run["id"])
            self.run = {
                "id": run["id"],
                "floor": max(1, min(int(run.get("floor", 1)), dungeon.depth)),
                "awaiting": bool(run.get("awaiting", False)),
                "streak": max(0, int(run.get("streak", 0))),
            }
        else:
            self.run = None
        self.cooldowns = {k: int(v) for k, v in (data.get("cooldowns") or {}).items()
                          if k in dungeon_config.DUNGEONS}
        self.cleared = {k: int(v) for k, v in (data.get("cleared") or {}).items()
                        if k in dungeon_config.DUNGEONS}
        self.deepest = {k: int(v) for k, v in (data.get("deepest") or {}).items()
                        if k in dungeon_config.DUNGEONS}
