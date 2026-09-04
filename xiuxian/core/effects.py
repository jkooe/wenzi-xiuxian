"""效果 DSL：把一份效果声明作用到角色身上。

事件选项、丹药、任务奖励、战斗掉落全部复用这套语法，新增玩法不需要新增结算代码。

支持的效果类型（dict 形式）：
    {"type": "exp",        "value": 100}        修为 +100
    {"type": "exp_ratio",  "value": 0.2}        修为 +当前层需求的20%
    {"type": "hp"|"mp"|"stamina", "value": -20} 数值变化
    {"type": "hp_ratio"|"mp_ratio", "value": 0.5} 按上限百分比变化
    {"type": "attr", "key": "physique", "value": 2}   永久提升基础属性
    {"type": "item", "id": "healing_pill", "count": 2, "action": "add"|"remove"}
    {"type": "stone", "value": 50}              灵石
    {"type": "buff", "id": "...", "name": "...", "hours": 24, "add": {}, "mul": {}}
    {"type": "cleanse_buff", "source": "..."}   移除指定 buff
    {"type": "flag", "key": "...", "value": True}
    {"type": "poison", "value": 5}              丹毒
    {"type": "log", "text": "..."}              纯文本
    {"type": "insight", "law": "metal", "value": 150}        仙界法则感悟 +150（绝对值）
    {"type": "insight_hours", "law": "metal", "value": 12}   感悟 +「静悟 12 时辰」的产出量

insight 系列是 exp / exp_ratio 在「法则轴」上的对应物，但计价基准刻意不同：

    exp_ratio  挂「本层修为需求」的百分比 —— 因为修为需求随境界指数膨胀，必须跟着涨。
    insight_*  挂「悟道产出速率」          —— 因为悟道产出是**恒定**的（WUDAO_BASE 固定
                2.0/时辰，不随阶位缩放），事件奖励若按「下一阶需求」百分比给，后期一阶
                6000 感悟 × 12% = 720 = 15 天产出，一次探索顶半个月，节奏直接崩。

    推导（第 27 批实测基准）：仙界 1132 天 / 32 阶 / 总感悟 30400 → 日均 26.9 感悟。
    故事件感悟总预算须 ≲ 3000（≈10%），超出即需回头调 LAW_GATE 补偿。

    law 缺省时给当前专注法则；未飞升则静默不结算（凡界玩家不该凭空攒感悟）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from ..config import items as item_config
from .numfmt import fmt_num

if TYPE_CHECKING:  # 仅类型提示，避免循环依赖
    from .game import Game


class EffectTarget(Protocol):
    """效果作用对象需具备的接口（Cultivator 实现）。"""

    inventory: Any
    flags: dict[str, Any]

    def add_exp(self, amount: float) -> None: ...
    def heal_hp(self, amount: float) -> float: ...
    def heal_mp(self, amount: float) -> float: ...
    def damage(self, amount: float, reason: str = "") -> None: ...


MIN_VISIBLE = 0.5   # 变化量小于此值不产生日志，避免「修为 +0」这类噪音


def apply_effects(game: "Game", effects: list[dict[str, Any]], target: Any = None) -> list[str]:
    """执行一组效果，返回可读日志。target 缺省为玩家。"""
    player = target if target is not None else game.player
    logs: list[str] = []
    for eff in effects or []:
        logs.extend(_apply_one(game, player, eff))
    return logs


def _scale_by_poison(player: Any, value: float) -> float:
    """丹毒会削弱丹药类收益，最低保留 30%。"""
    poison = getattr(player, "pill_poison", 0.0)
    scale = max(0.3, 1.0 - poison / 200.0)
    return value * scale


def _apply_one(game: "Game", player: Any, eff: dict[str, Any]) -> list[str]:
    etype = eff.get("type")
    value = float(eff.get("value", 0) or 0)

    if etype == "exp":
        real = player.add_exp(_scale_by_poison(player, value))
        return [f"修为 +{fmt_num(real)}"] if real >= MIN_VISIBLE else []

    if etype == "exp_ratio":
        need = player.exp_required()
        if need == float("inf"):
            return []          # 已至绝巅，修为无处可加（inf 参与运算会污染存档）
        return _apply_one(game, player, {"type": "exp", "value": need * value})

    if etype in ("hp", "mp", "stamina"):
        if etype == "hp":
            if value >= 0:
                healed = player.heal_hp(_scale_by_poison(player, value))
                return [f"气血 +{healed:.0f}"] if healed >= MIN_VISIBLE else []
            player.damage(-value, reason="事件")
            return [f"气血 -{abs(value):.0f}"]
        if etype == "mp":
            healed = player.heal_mp(_scale_by_poison(player, value))
            return [f"灵力 +{healed:.0f}"] if healed >= MIN_VISIBLE else []
        player.stamina = max(0.0, player.stamina + value)
        return [f"精力 {'+' if value >= 0 else ''}{value:.0f}"] if abs(value) >= MIN_VISIBLE else []

    if etype in ("hp_ratio", "mp_ratio"):
        key = "max_hp" if etype == "hp_ratio" else "max_mp"
        label = "气血" if key == "max_hp" else "灵力"
        amount = player.attributes.value(key) * value * _scale_by_poison(player, 1.0)
        if value >= 0:
            healed = player.heal_hp(amount) if key == "max_hp" else player.heal_mp(amount)
            return [f"{label} +{healed:.0f}"] if healed >= MIN_VISIBLE else []
        player.damage(abs(amount), reason="事件")
        return [f"{label} -{abs(amount):.0f}"]

    if etype == "attr":
        key = eff["key"]
        player.attributes.grow_base(key, value)
        from .attributes import ATTR_LABELS
        return [f"{ATTR_LABELS.get(key, key)} 永久 +{value:g}"]

    if etype == "item":
        item_id = eff["id"]
        count = int(eff.get("count", 1))
        action = eff.get("action", "add")
        name = item_config.get_item(item_id).name
        if action == "add":
            player.inventory.add(item_id, count)
            return [f"获得 {name} ×{count}"]
        if player.inventory.remove(item_id, count):
            return [f"失去 {name} ×{count}"]
        return [f"{name} 不足，未能扣除"]

    if etype == "stone":
        player.spirit_stones = max(0, player.spirit_stones + int(value))
        return [f"灵石 {'+' if value >= 0 else ''}{int(value)}"]

    if etype == "buff":
        from .attributes import Modifier
        bid = eff.get("id", eff.get("name", "buff"))
        source = f"buff:{eff.get('name', bid)}"
        player.attributes.add_modifier(
            Modifier(
                source=source,
                add=dict(eff.get("add", {})),
                mul=dict(eff.get("mul", {})),
                hours_left=float(eff.get("hours", 24)),
            )
        )
        return [f"获得状态【{eff.get('name', bid)}】（{eff.get('hours', 24)} 小时）"]

    if etype == "cleanse_buff":
        source = eff.get("source", "")
        player.attributes.remove_modifier(source)
        return [f"状态【{source}】已解除"]

    if etype == "flag":
        player.flags[eff["key"]] = eff.get("value", True)
        return []

    if etype == "poison":
        player.pill_poison = max(0.0, player.pill_poison + value)
        if value >= 0:
            return [f"丹毒 +{value:g}（当前 {player.pill_poison:.0f}）"]
        return [f"丹毒 {value:g}（化毒后 {player.pill_poison:.0f}）"]

    if etype in ("insight", "insight_hours"):
        law_sys = game.systems.get("law")
        if law_sys is None:
            return []
        law_key = eff.get("law")
        # insight_hours：给「静悟 N 时辰」的产出量（含门派主修/兼修加成）
        amount = value
        if etype == "insight_hours":
            left = player.daily_left(game.day, "insight", law_sys.INSIGHT_DAILY_LIMIT)
            if left <= 0:
                return ["（静悟机缘已尽，今日难再有所得）"]
            player.bump_daily(game.day, "insight")
            amount = law_sys.hourly_insight(law_key) * value
        real, before, after = law_sys.gain_insight(law_key, amount)
        if real < MIN_VISIBLE:
            return []
        law_name = law_sys.law_name(law_key)
        line = f"【{law_name}】感悟 +{fmt_num(real)}"
        if after > before:
            line += f"（{law_sys.stage_label(before)} → {law_sys.stage_label(after)}）"
        return [line]

    if etype == "log":
        return [str(eff.get("text", ""))]

    # 自定义效果：由玩法系统注册（如战斗系统注册 "battle"）
    handler = getattr(game, "effect_handlers", {}).get(etype)
    if handler:
        return handler(player, eff)

    return [f"[未实现的效果: {etype}]"]
