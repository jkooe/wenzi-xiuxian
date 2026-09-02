"""功法系统（多件装备 + 数据驱动效果 + 实时重算）。

四条规则
--------
1. **修习**：花灵石 + 满足境界门槛，记入 learned，熟练度从 0 起
2. **装备**：同时可装备多件（上限随境界提升，`art equip/unequip`），卸下即时失效
3. **效果聚合**：所有已装备功法的效果按类型声明的 stack 规则聚合
   （add 相加 / mul 相乘 / max 取最高），再按熟练度与等级缩放
4. **实时重算**：装备/卸下/熟练度变化都触发 `rebuild()` —— 移除旧 `art:` 修正器、
   注入新的，并刷新「非属性类」加成缓存，供修炼/突破/灵石/悟道各处查询

**新增一种效果类型只需三步**（核心逻辑零改动，见 README 示例）：
    ① config/arts.py：`EFFECT_TYPES` 声明（叠加规则、说明、平衡折价）
    ② systems/arts.py：`EFFECT_APPLIERS` 注册一个「聚合值如何生效」的函数
    ③ 消费端：读一次 `arts.bonus("<新类型>")` 并参与计算

与其他系统的耦合全靠事件总线：
    订阅 practice       —— 打坐时涨熟练度
    订阅 combat_victory —— 斗法取胜涨熟练度
    注册效果 "art"      —— 事件 JSON 里写 {"type": "art", "id": "qingxin"} 即可传功
"""

from __future__ import annotations

from typing import Any, Callable

from ..config import arts as art_config
from ..config.arts import (
    EFFECT_TYPES,
    STACK_ADD,
    STACK_MAX,
    STACK_MUL,
    ArtDef,
    ArtEffect,
)
from ..config.realms import RealmRegistry
from ..core.attributes import ATTR_LABELS, Modifier
from ..core.base_system import (
    TOPIC_COMBAT_VICTORY,
    TOPIC_PRACTICE,
    Command,
    GameSystem,
)

SOURCE_PREFIX = "art:"

PROF_PER_HOUR = 6.0             # 打坐时熟练度增长基数
PRACTICE_PROF_PER_HOUR = 10.0   # 专修功法时的增长基数（不涨修为，只磨功法）
COMBAT_PROF_GAIN = 15.0         # 斗法取胜的增长基数
PRACTICE_STAMINA_PER_HOUR = 8.0
PRACTICE_MP_PER_HOUR = 4.0      # 运转功法消耗灵力

# 强效类型的加成上限：多件满练功法叠加后的封顶（防「满配」把某条曲线拉爆）。
# 突破成功率刻意压得很低 —— 突破是决策点，功法只该「略微增加把握」而不是抹平难度。
BONUS_CAPS: dict[str, float] = {
    "cultivate_speed": 0.60,
    "breakthrough_rate": 0.06,
    "stone_gain": 0.50,
    "insight_rate": 0.15,
}


# 每种叠加规则的初始值（幺元）：add 从 0 起、mul 从 1 起、max 从 -inf 起
_STACK_IDENTITY = {STACK_ADD: 0.0, STACK_MUL: 1.0, STACK_MAX: float("-inf")}


def _combine(rule: str, current: float, incoming: float) -> float:
    """按叠加规则把新值并入当前聚合值。"""
    if rule == STACK_MUL:
        return current * incoming
    if rule == STACK_MAX:
        return max(current, incoming)
    return current + incoming


class ArtSystem(GameSystem):
    id = "arts"
    name = "功法"

    def __init__(self) -> None:
        super().__init__()
        self.learned: dict[str, int] = {}          # art_id -> 熟练度
        self.equipped: list[str] = []              # 已装备功法（生效中的）
        # 加成不再由本系统各自缓存 —— 统一交给 core/bonus.py 的 BonusAggregator

    # ---------- 装配 ----------
    def on_bind(self) -> None:
        self.game.bus.on(TOPIC_PRACTICE, self._on_practice)
        self.game.bus.on(TOPIC_COMBAT_VICTORY, self._on_victory)
        self.game.register_effect("art", self.effect_art)
        self.rebuild()

    # ---------- 修习 ----------
    def learn(self, art_id: str, free: bool = False) -> list[str]:
        """修习一门功法。free=True 时不收灵石（事件传功用）。"""
        p = self.player
        if not p.alive:
            return ["你已身死道消。"]
        try:
            art = art_config.get_art(art_id)
        except KeyError:
            return [f"世间并无「{art_id}」这门功法。"]
        if art_id in self.learned:
            return [f"你已修习《{art.name}》，勤加打磨方为正道。"]
        if not RealmRegistry.within(p.realm_key, min_realm=art.min_realm):
            need = RealmRegistry.get(art.min_realm).name
            return [f"修为不足，《{art.name}》需 {need} 以上方可修习。"]
        # 门派功法：以贡献兑换，唯本门弟子可修（「师」与「法」的联动）
        sect_pay = bool(art.sect and art.cost_contribution > 0)
        if sect_pay:
            sect_sys = self.game.systems.get("sect")
            if sect_sys is None or sect_sys.sect_key != art.sect:
                return [f"《{art.name}》乃门派秘传，唯本门弟子可修。"]
            if sect_sys.contribution < art.cost_contribution:
                return [f"贡献不足，《{art.name}》需 {art.cost_contribution} 贡献"
                        f"（现有 {sect_sys.contribution}）。"]
        elif not free:
            if p.spirit_stones < art.price:
                return [f"灵石不足，《{art.name}》需 {art.price} 灵石（现有 {p.spirit_stones}）。"]

        if sect_pay:
            sect_sys = self.game.systems.get("sect")
            sect_sys.contribution -= art.cost_contribution
            self.learned[art_id] = 0
            logs = [f"以 {art.cost_contribution} 门派贡献，换得《{art.name}》（{art.rank}）。"]
        else:
            if not free:
                p.spirit_stones -= art.price
            self.learned[art_id] = 0
            logs = [f"参悟《{art.name}》（{art.rank}），略有心得。" if free
                    else f"取出 {art.price} 灵石，换得《{art.name}》（{art.rank}）。"]
        logs.append(f"  {art.desc}")
        if len(self.equipped) < self.slot_cap():
            self.equipped.append(art_id)
            logs.append(f"已装备（{len(self.equipped)}/{self.slot_cap()}），"
                        f"威力 {self._percent(art_id):.0f}%。")
        else:
            logs.append(f"装备栏已满（{self.slot_cap()}），art equip {art_id} 可换装。")
        self.rebuild()
        logs.extend(self._diff_effect_summary())
        return logs

    # ---------- 装备 / 卸下 ----------
    def slot_cap(self) -> int:
        """当前境界允许的装备数量上限。"""
        return art_config.slots_for_realm(self.player.realm_key)

    def equip(self, art_id: str) -> list[str]:
        try:
            art = art_config.get_art(art_id)
        except KeyError:
            return [f"世间并无「{art_id}」这门功法。"]
        if art_id not in self.learned:
            return [f"你尚未修习《{art.name}》。（art learn {art_id}）"]
        if art_id in self.equipped:
            return [f"《{art.name}》已在装备之中。"]
        if len(self.equipped) >= self.slot_cap():
            return [f"装备栏已满（{self.slot_cap()}/{self.slot_cap()}），"
                    f"先 art unequip <id> 卸下一门。"]

        before = self._snapshot()
        self.equipped.append(art_id)
        self.rebuild()
        logs = [f"运转《{art.name}》（{art.rank}），"
                f"已装备 {len(self.equipped)}/{self.slot_cap()}。"]
        logs.extend(self._diff(before))
        return logs

    def unequip(self, art_id: str) -> list[str]:
        try:
            art = art_config.get_art(art_id)
        except KeyError:
            return [f"世间并无「{art_id}」这门功法。"]
        if art_id not in self.equipped:
            return [f"《{art.name}》本就不在装备之中。"]
        before = self._snapshot()
        self.equipped.remove(art_id)
        self.rebuild()
        logs = [f"收起《{art.name}》，不再运转。"]
        logs.extend(self._diff(before))
        return logs

    # ---------- 效果聚合与实时重算 ----------
    def _applier(self, etype: str) -> Callable[["ArtSystem", ArtEffect, float], None]:
        """取该类型的「应用函数」（见 EFFECT_APPLIERS）。未注册的类型记入通用缓存。"""
        return EFFECT_APPLIERS.get(etype, _apply_generic)

    def collect_bonuses(self, agg) -> None:
        """把已装备功法的效果交给全局聚合器（熟练度与等级缩放在此完成）。"""
        for art_id in self.equipped:
            if art_id not in self.learned:
                continue
            art = art_config.get_art(art_id)
            prof = self.learned[art_id]
            agg.add_many(
                f"art:{art_id}",
                art.effects,
                lambda eff, art=art, prof=prof: art_config.effective_value(
                    art, eff, prof,
                    realm_scale=art_config.realm_attr_scale(self.player.realm_key),
                ),
            )

    def rebuild(self) -> None:
        """装备/卸下/熟练度变化后重算加成（交给全局聚合器统一处理）。"""
        self.game.rebuild_bonuses()

    def _clamp(self) -> None:
        """卸下功法可能压低上限，当前气血/灵力不得超过新上限。"""
        p = self.player
        p.hp = min(p.hp, p.max_hp)
        p.mp = min(p.mp, p.max_mp)

    def bonus(self, etype: str) -> float:
        """查询「功法来源」的非属性类加成（修炼速度 / 突破率 / 灵石 / 悟道……）。"""
        return self.game.bonuses.value_of(etype, "art:")

    # ---------- 熟练度 ----------
    def gain_proficiency(self, amount: float, art_id: str | None = None) -> list[str]:
        """给功法涨熟练度（默认涨全部已装备功法），返回日志。"""
        targets = [art_id] if art_id else list(self.equipped)
        logs: list[str] = []
        for target in targets:
            if target is None or target not in self.learned:
                continue
            art = art_config.get_art(target)
            cur = self.learned[target]
            if cur >= art.max_proficiency:
                continue
            gained = amount * art.practice_gain
            new_val = min(art.max_proficiency, cur + int(gained))
            if new_val == cur:
                continue
            old_level = art_config.level_of(cur, art)
            self.learned[target] = new_val
            new_level = art_config.level_of(new_val, art)
            logs.append(f"《{art.name}》熟练度 {cur} → {new_val}"
                        f"（{self._percent(target):.0f}%）")
            if new_level > old_level:
                logs.append(f"　《{art.name}》精进至第 {new_level} 层，威力更胜往昔！")
            if new_val >= art.max_proficiency:
                logs.append(f"　《{art.name}》已臻圆满，十成功力尽在掌握！")
        if logs:
            self.rebuild()
        return logs

    def practice(self, hours: float = 4.0) -> list[str]:
        """专修功法：不涨修为，只磨熟练度。"""
        p = self.player
        if not p.alive:
            return ["你已身死道消。"]
        if not self.equipped:
            return ["你尚无运转中的功法。（art list 查看，art learn <id> 修习）"]
        hours = max(1.0, min(float(hours), 24.0))
        if p.stamina < PRACTICE_STAMINA_PER_HOUR:
            return ["精力枯竭，先歇息吧。（rest 4）"]
        if p.mp < PRACTICE_MP_PER_HOUR:
            return ["灵力不济，难以运转功法。（rest 4）"]

        real_hours = min(hours,
                         p.stamina / PRACTICE_STAMINA_PER_HOUR,
                         p.mp / PRACTICE_MP_PER_HOUR)
        p.spend_stamina(real_hours * PRACTICE_STAMINA_PER_HOUR)
        p.mp = max(0.0, p.mp - real_hours * PRACTICE_MP_PER_HOUR)

        amount = real_hours * PRACTICE_PROF_PER_HOUR * (1 + p.comprehension / 100.0)
        logs = [f"静心运转功法 {real_hours:.0f} 小时。"]
        logs.extend(self.gain_proficiency(amount))
        logs.extend(self.game.advance_time(real_hours))
        return logs

    def _on_practice(self, payload: dict[str, Any]) -> None:
        hours = float(payload.get("hours", 0.0))
        if hours <= 0:
            return
        insight = 1 + self.player.comprehension / 100.0
        sink = payload.setdefault("logs", [])
        sink.extend(self.gain_proficiency(hours * PROF_PER_HOUR * insight))

    def _on_victory(self, payload: dict[str, Any]) -> None:
        danger = float(payload.get("danger", 1.0))
        scale = 1.0 + min(1.5, danger / max(1.0, self.player.atk))
        sink = payload.setdefault("logs", [])
        sink.extend(self.gain_proficiency(COMBAT_PROF_GAIN * scale))

    # ---------- 展示 ----------
    def _percent(self, art_id: str) -> float:
        art = art_config.get_art(art_id)
        prof = self.learned.get(art_id, 0)
        return (art_config.proficiency_scale(prof, art.max_proficiency)
                * art_config.level_scale(art_config.level_of(prof, art), art) * 100)

    def _snapshot(self) -> dict[str, float]:
        p = self.player
        return {key: p.attributes.value(key)
                for key in ("max_hp", "max_mp", "atk", "def", "speed",
                            "spirit", "comprehension", "physique", "luck")}

    def _diff(self, before: dict[str, float]) -> list[str]:
        after = self._snapshot()
        parts = []
        for key, old in before.items():
            delta = after[key] - old
            if abs(delta) >= 0.5:
                parts.append(f"{ATTR_LABELS[key]} {delta:+.0f}")
        return [f"属性变动：{'　'.join(parts)}"] if parts else []

    def _diff_effect_summary(self) -> list[str]:
        """装备/卸下后，把「功法来源」的非属性加成汇总成一行（可解释性）。"""
        parts = []
        for etype in sorted(EFFECT_TYPES):
            value = self.game.bonuses.value_of(etype, "art:")
            meta = EFFECT_TYPES.get(etype)
            if meta and abs(value) >= 1e-6:
                shown = value * 100 if meta.unit == "%" else value
                parts.append(f"{meta.desc} {shown:+.0f}{meta.unit}")
        return [f"当前功法加成：{'、'.join(parts)}"] if parts else []

    @staticmethod
    def _describe_effect(art: ArtDef, eff: ArtEffect, at_max: bool = True) -> str:
        meta = EFFECT_TYPES.get(eff.type)
        if meta is None:
            return f"{eff.type} {eff.value}"
        value = eff.value if at_max else eff.value * 0.6
        if meta.unit == "%":
            text = f"{value * 100:+.0f}%"
        else:
            text = f"{value:+.0f}"
        label = ATTR_LABELS.get(eff.key, eff.key) if eff.key else meta.desc
        return f"{label} {text}"

    def info(self) -> list[str]:
        """已修习功法一览（含装备状态、等级、熟练度、效果）。"""
        if not self.learned:
            return ["你身无半点功法传承，只凭本能吐纳。",
                    "（art list 查看可修习之功法，art learn <id> 修习）"]
        lines = [f"功法（装备 {len(self.equipped)}/{self.slot_cap()}）："]
        for art_id, prof in sorted(self.learned.items(), key=lambda kv: -kv[1]):
            art = art_config.get_art(art_id)
            mark = "★" if art_id in self.equipped else "　"
            level = art_config.level_of(prof, art)
            bar = self._bar(prof, art.max_proficiency)
            lines.append(f"  {mark}{art.name}（{art.rank}·{level}阶）　{bar} {prof}/{art.max_proficiency}")
            effects = "、".join(self._describe_effect(art, e, at_max=(prof >= art.max_proficiency))
                               for e in art.effects)
            lines.append(f"　　　{effects}")
        return lines

    @staticmethod
    def _bar(value: int, maximum: int, width: int = 12) -> str:
        filled = int(round(max(0.0, min(1.0, value / max(1, maximum))) * width))
        return f"[{'█' * filled}{'·' * (width - filled)}]"

    def catalog(self) -> list[str]:
        """功法目录：品阶、价格、门槛、圆满效果、可修习状态。"""
        p = self.player
        lines = ["功法目录（art learn <id> 修习，art equip <id> 装备）："]
        for art in sorted(art_config.ARTS.values(),
                          key=lambda a: (art_config.RANKS_ORDER.index(a.rank), a.price)):
            if art.id in self.learned:
                state = "★已装备" if art.id in self.equipped else "已修习"
            elif not RealmRegistry.within(p.realm_key, min_realm=art.min_realm):
                state = f"修为不足（需{RealmRegistry.get(art.min_realm).name}）"
            elif p.spirit_stones < art.price:
                state = "灵石不足"
            else:
                state = "可修习"
            lines.append(f"  {art.name}（{art.rank}）{art.price} 灵石　[{state}]　{art.id}")
            lines.append(f"　　　{art.desc}")
            effects = "、".join(self._describe_effect(art, e) for e in art.effects)
            lines.append(f"　　　圆满：{effects}　练成速度 ×{art.practice_gain}")
        return lines

    # ---------- 效果 DSL ----------
    def effect_art(self, player, eff: dict) -> list[str]:
        """事件 JSON：{"type": "art", "id": "qingxin", "proficiency": 120}。"""
        art_id = eff.get("id", "")
        try:
            art = art_config.get_art(art_id)
        except KeyError:
            return [f"[未知功法: {art_id}]"]
        if art_id in self.learned:
            logs = self.gain_proficiency(float(eff.get("proficiency", 120)), art_id)
            return logs or [f"《{art.name}》已至圆满，再无进境。"]
        logs = self.learn(art_id, free=True)
        amount = float(eff.get("proficiency", 0))
        if amount > 0:
            logs.extend(self.gain_proficiency(amount, art_id))
        return logs

    # ---------- 命令 ----------
    def commands(self) -> list[Command]:
        def _art(args: list[str]) -> None:
            sub = args[0].lower() if args else ""
            if sub in ("list", "all", "目录"):
                self.game.emit_logs(self.catalog())
            elif sub in ("learn", "修习"):
                if len(args) < 2:
                    self.log("用法：art learn <功法id>（art list 查看目录）")
                    return
                self.game.emit_logs(self.learn(args[1]))
            elif sub in ("equip", "装备"):
                if len(args) < 2:
                    self.log("用法：art equip <功法id>")
                    return
                self.game.emit_logs(self.equip(args[1]))
            elif sub in ("unequip", "卸下"):
                if len(args) < 2:
                    self.log("用法：art unequip <功法id>")
                    return
                self.game.emit_logs(self.unequip(args[1]))
            elif sub in ("practice", "练", "专修"):
                hours = float(args[1]) if len(args) > 1 else 4.0
                self.game.emit_logs(self.practice(hours))
            else:
                self.game.emit_logs(self.info())
                self.log("  art list 目录　art learn <id> 修习　art equip/unequip <id> 装卸　"
                         "art practice [小时] 专修")

        return [Command("art", "功法（list/learn/equip/unequip/practice）",
                        "art [list|learn|equip|unequip|practice]", _art)]

    # ---------- 持久化 ----------
    def to_dict(self) -> dict[str, Any]:
        return {"learned": dict(self.learned), "equipped": list(self.equipped)}

    def load_state(self, data: dict[str, Any]) -> None:
        self.learned = {k: int(v) for k, v in (data.get("learned") or {}).items()
                        if k in art_config.ARTS}
        self.equipped = [a for a in (data.get("equipped") or []) if a in self.learned]
        # 老档兼容：只有 main（单一主修）时，把它当作唯一的装备
        if not self.equipped:
            main = data.get("main")
            if main in self.learned:
                self.equipped = [main]
        self.rebuild()


# ---------- 效果应用函数（新增类型只需在这里加一条） ----------
def _apply_attr_add(system: ArtSystem, eff: ArtEffect, value: float) -> None:
    """属性固定值加成：累加到 add 表。"""
    system._attr_add[eff.key] = system._attr_add.get(eff.key, 0.0) + value


def _apply_attr_mul(system: ArtSystem, eff: ArtEffect, value: float) -> None:
    """属性百分比加成：value 是「偏移和」（0.2 = +20%），最终乘数 = 1 + 偏移和。

    刻意按偏移相加而非连乘：两件 +20% 得 +40%（1.4）而不是 1.44 —— 多件功法不会叠成指数爆炸。
    """
    system._attr_mul[eff.key] = system._attr_mul.get(eff.key, 0.0) + value


def _apply_generic(system: ArtSystem, eff: ArtEffect, value: float) -> None:
    """通用：非属性类加成进缓存，并套用该类型的封顶（消费端 `arts.bonus(type)` 读取）。"""
    cap = BONUS_CAPS.get(eff.type)
    if cap is not None:
        value = min(value, cap)
    system._bonus[eff.type] = value


# 类型 -> 应用函数。新增效果类型时，在这里登记一行即可（聚合与重算逻辑无需改动）
EFFECT_APPLIERS: dict[str, Callable[[ArtSystem, ArtEffect, float], None]] = {
    "attr_add": _apply_attr_add,
    "attr_mul": _apply_attr_mul,
    "cultivate_speed": _apply_generic,
    "breakthrough_rate": _apply_generic,
    "stone_gain": _apply_generic,
    "insight_rate": _apply_generic,
}
