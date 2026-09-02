"""道侣系统（「侣」）：同道相伴，双修共参。

规则：
    1. 结识 —— 花礼金结交道友，需满足境界门槛（高阶道友不理会低阶修士）
    2. 情分 —— 双修、并肩对敌、赠礼都会增长情分；情分决定「发挥几成」（0.5 → 1.0）
    3. 双修 —— 消耗精力与灵力，换修为与情分（比独修更快，但有每日次数限制）
    4. 加成 —— 走 core/bonus.py 全局聚合，与功法/洞府/师门共用一套效果类型与叠加规则
"""

from __future__ import annotations

from typing import Any

from ..config import companions as comp_config
from ..config.realms import RealmRegistry
from ..core.base_system import TOPIC_COMBAT_VICTORY, Command, GameSystem

SOURCE = "companion:"

DUAL_STAMINA = 20          # 双修精力消耗
DUAL_MP = 10               # 双修灵力消耗
DUAL_DAILY_LIMIT = 3       # 每日双修次数
DUAL_BOND_GAIN = 40        # 每次双修情分增长
VICTORY_BOND_GAIN = 5      # 并肩对敌的情分增长
GIFT_COST = 200            # 赠礼灵石
GIFT_BOND_GAIN = 60        # 赠礼情分增长

# v2 道侣边际递减：第 1 位 100% / 第 2 位 60% / 第 3 位 30% / 第 4 位 15%
# 每位道侣加成 = 基础值 × 情分(0.5~1.0) × 边际递减系数
BOND_DIMINISHING = (1.0, 0.6, 0.3, 0.15)


class CompanionSystem(GameSystem):
    id = "companion"
    name = "道侣"

    def __init__(self) -> None:
        super().__init__()
        self.met: dict[str, int] = {}      # companion_key -> 情分

    def on_bind(self) -> None:
        self.game.bus.on(TOPIC_COMBAT_VICTORY, self._on_victory)

    # ---------- 加成 ----------
    def collect_bonuses(self, agg) -> None:
        """把已结交道侣的效果交给全局聚合器（按情分 × 边际递减缩放）。"""
        for order, key in enumerate(self.met):       # met 为 dict，插入顺序即结识顺序
            c = comp_config.get_companion(key)
            scale = comp_config.bond_scale(self.met[key], c.bond_max)
            diminishing = BOND_DIMINISHING[min(order, len(BOND_DIMINISHING) - 1)]
            for eff in c.effects:
                agg.add(f"{SOURCE}{key}", eff, eff.value * scale * diminishing)

    def _refresh(self) -> None:
        self.game.rebuild_bonuses()

    def bonus(self, etype: str) -> float:
        return self.game.bonuses.value_of(etype, SOURCE)

    # ---------- 行为 ----------
    def meet(self, key: str) -> list[str]:
        p = self.player
        try:
            c = comp_config.get_companion(key)
        except KeyError:
            return [f"并无「{key}」此人。（companion list 查看）"]
        if key in self.met:
            return [f"{c.name} 已是你的道友。（companion dual {key} 双修）"]
        if not RealmRegistry.within(p.realm_key, min_realm=c.min_realm):
            return [f"{c.name} 只与 {RealmRegistry.get(c.min_realm).name} 之上的道友论交。"]
        if p.spirit_stones < c.meet_cost:
            return [f"礼金不足，结识{c.name} 需 {c.meet_cost} 灵石（现有 {p.spirit_stones}）。"]

        p.spirit_stones -= c.meet_cost
        self.met[key] = 0
        self._refresh()
        # 日常追踪：拜访道侣
        daily = self.game.systems.get("daily")
        if daily is not None:
            daily.track("visit")
        return [f"以 {c.meet_cost} 灵石为礼，结交{c.name}（{c.title}）。",
                f"　{c.desc}",
                f"　情分初起，加成仅五成（companion dual 双修可增进情分）"]

    def dual(self, key: str | None = None) -> list[str]:
        """双修：换修为与情分，每日有次数上限。"""
        p = self.player
        if not p.alive:
            return ["你已身死道消。"]
        target = key or (next(iter(self.met), None))
        if not target or target not in self.met:
            return ["你尚无同道。（companion list 查看，companion meet <key> 结识）"]
        if self.game.player.daily_left(self.game.day, "dual", DUAL_DAILY_LIMIT) <= 0:
            return [f"今日双修已尽（每日 {DUAL_DAILY_LIMIT} 次），道法自然，不宜贪多。"]

        c = comp_config.get_companion(target)
        if p.stamina < DUAL_STAMINA:
            return [f"精力不济，难以双修（需 {DUAL_STAMINA}，现有 {p.stamina:.0f}）。"]
        if p.mp < DUAL_MP:
            return [f"灵力不济，难以运功（需 {DUAL_MP}，现有 {p.mp:.0f}）。"]

        p.bump_daily(self.game.day, "dual")
        p.spend_stamina(DUAL_STAMINA)
        p.mp = max(0.0, p.mp - DUAL_MP)

        # 双修修为：按本层需求比例，受功法/洞府等「修炼速度」加成影响（共用同一套加成）
        from ..core.cultivation import CultivationSystem
        cult = self.game.systems.get("cultivation")
        if isinstance(cult, CultivationSystem):
            need = p.exp_required()
            if need == float("inf"):
                from ..config.realms import RealmRegistry as RR
                need = RR.stage_exp_required(p.realm_def, max(0, p.stage - 1))
            gain = need * 0.06 * (1.0 + self.game.bonuses.value("cultivate_speed"))
            real = p.add_exp(gain)
        else:
            real = 0.0

        # 情分增长（上限封顶）
        before = self.met[target]
        self.met[target] = min(c.bond_max, before + DUAL_BOND_GAIN)
        self._refresh()

        logs = [f"与{c.name}对坐双修，阴阳相济，气息交融。"]
        if real >= 0.5:
            logs.append(f"　修为 +{real:.0f}")
        else:
            logs.append("　本层修为将满，此番只得益友之情")
        logs.append(f"　情分 {before} → {self.met[target]}/{c.bond_max}"
                    f"（当前加成 {self._scale_text(target)}）")
        if self.met[target] >= c.bond_max:
            logs.append(f"　与{c.name}情分圆满，心意相通，十成加成尽显。")
        logs.extend(self.game.advance_time(2))
        return logs

    def gift(self, key: str) -> list[str]:
        """赠礼：花灵石换情分（比双修快，但纯消耗灵石 —— 「财」换「侣」）。"""
        if key not in self.met:
            return ["你与此人并无交情。（companion list 查看）"]
        c = comp_config.get_companion(key)
        p = self.player
        if p.spirit_stones < GIFT_COST:
            return [f"灵石不足，一份薄礼需 {GIFT_COST}（现有 {p.spirit_stones}）。"]

        p.spirit_stones -= GIFT_COST
        before = self.met[key]
        self.met[key] = min(c.bond_max, before + GIFT_BOND_GAIN)
        self._refresh()
        return [f"备下 {GIFT_COST} 灵石的薄礼赠与{c.name}，情分 {before} → {self.met[key]}（{self._scale_text(key)}）。"]

    def _on_victory(self, payload: dict) -> None:
        """并肩对敌，情分渐生。"""
        if not self.met:
            return
        sink = payload.setdefault("logs", [])
        parts = []
        for key in list(self.met):
            c = comp_config.get_companion(key)
            before = self.met[key]
            self.met[key] = min(c.bond_max, before + VICTORY_BOND_GAIN)
        self.game.rebuild_bonuses()
        for key in self.met:
            parts.append(f"{comp_config.get_companion(key).name}情分 +{VICTORY_BOND_GAIN}")
        sink.append("　" + "、".join(parts))

    # ---------- 展示 ----------
    def _scale_text(self, key: str) -> str:
        c = comp_config.get_companion(key)
        return f"{comp_config.bond_scale(self.met[key], c.bond_max) * 100:.0f}%"

    def info(self) -> list[str]:
        if not self.met:
            return ["你孤身一人，未有同道。（companion list 查看，companion meet <key> 结识）"]
        lines = ["道侣："]
        for key, bond in self.met.items():
            c = comp_config.get_companion(key)
            filled = int(round(bond / c.bond_max * 12))
            bar = f"[{'█' * filled}{'·' * (12 - filled)}]"
            lines.append(f"  {c.name}（{c.title}）　{bar} 情分 {bond}/{c.bond_max}　加成 {self._scale_text(key)}")
        return lines

    def catalog(self) -> list[str]:
        p = self.player
        lines = ["可结交之道友（companion meet <key>）："]
        for c in comp_config.COMPANIONS.values():
            if c.key in self.met:
                state = "★已结交"
            elif not RealmRegistry.within(p.realm_key, min_realm=c.min_realm):
                state = f"境界不足（需{RealmRegistry.get(c.min_realm).name}）"
            elif p.spirit_stones < c.meet_cost:
                state = "礼金不足"
            else:
                state = "可结识"
            lines.append(f"  {c.name}（{c.title}）礼金 {c.meet_cost}　[{state}]　{c.key}")
            lines.append(f"　　　{c.desc}")
            effs = "、".join(self._eff_text(e) for e in c.effects)
            lines.append(f"　　　情分圆满：{effs}")
        return lines

    @staticmethod
    def _eff_text(eff) -> str:
        from ..config.arts import EFFECT_TYPES
        from ..core.attributes import ATTR_LABELS
        meta = EFFECT_TYPES.get(eff.type)
        if meta is None:
            return str(eff.type)
        value = eff.value * 100 if meta.unit == "%" else eff.value
        label = ATTR_LABELS.get(eff.key, eff.key) if eff.key else meta.desc
        return f"{label} {value:+.0f}{meta.unit}"

    # ---------- 命令 ----------
    def commands(self) -> list[Command]:
        def _comp(args: list[str]) -> None:
            sub = args[0].lower() if args else ""
            if sub in ("list", "目录"):
                self.game.emit_logs(self.catalog())
            elif sub in ("meet", "结识"):
                self.game.emit_logs(self.meet(args[1] if len(args) > 1 else ""))
            elif sub in ("dual", "双修"):
                self.game.emit_logs(self.dual(args[1] if len(args) > 1 else None))
            elif sub in ("gift", "赠礼"):
                self.game.emit_logs(self.gift(args[1] if len(args) > 1 else ""))
            else:
                self.game.emit_logs(self.info())
                self.log("  companion list 目录　companion meet <key>　companion dual [key]　companion gift <key>")

        return [Command("companion", "道侣（侣）", "companion [list|meet|dual|gift]", _comp)]

    # ---------- 持久化 ----------
    def to_dict(self) -> dict[str, Any]:
        return {"met": dict(self.met)}

    def load_state(self, data: dict[str, Any]) -> None:
        self.met = {k: int(v) for k, v in (data.get("met") or {}).items()
                    if k in comp_config.COMPANIONS}
