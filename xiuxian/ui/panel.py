"""纯展示层：把 Game 的数据渲染成终端文本。

所有渲染集中在这里，换 Web / GUI 只需重写本文件，内核不动。
"""

from __future__ import annotations

import unicodedata

from ..config import arts as art_config
from ..config import companions as companion_config
from ..config import dungeons as dungeon_config
from ..config import items as item_config
from ..config.realms import LOCATIONS, power_of
from ..core.base_system import Command
from ..core.game import Game
from ..core.numfmt import fmt_num
from ..systems.skill import STRATEGY_LABELS

LINE = "─" * 58


def status_panel(game: Game) -> str:
    p = game.player
    # 装备与功法为可选模块，裁剪掉就不显示，不影响其余渲染
    extras: list[str] = []
    art_line = _art_line(game)
    if art_line:
        extras.append(art_line)
    equip_line = _equip_line(game)
    if equip_line:
        extras.append(equip_line)
    dungeon_line = _dungeon_line(game)
    if dungeon_line:
        extras.append(dungeon_line)
    quest_line = _quest_line(game)
    if quest_line:
        extras.append(quest_line)
    skill_line = _skill_line(game)
    if skill_line:
        extras.append(skill_line)
    power_line = _power_line(game)
    if power_line:
        extras.append(power_line)
    for line in _foundation_lines(game):
        extras.append(line)

    lines = [
        LINE,
        f"  {p.full_title}",
        f"  {game.time_text()}　{LOCATIONS.get(game.location, {}).get('density', 1.0):.1f} 倍灵气",
        LINE,
        f"  气血 {fmt_num(p.hp)}/{fmt_num(p.max_hp)}　灵力 {fmt_num(p.mp)}/{fmt_num(p.max_mp)}　精力 {p.stamina:.0f}/100",
        f"  攻击 {fmt_num(p.atk)}　防御 {fmt_num(p.defense)}　身法 {fmt_num(p.speed)}　神识 {fmt_num(p.attributes.int_value('spirit'))}",
        f"  悟性 {p.comprehension:.0f}　根骨 {p.physique:.0f}　气运 {p.luck:.0f}　丹毒 {p.pill_poison:.0f}",
        f"  灵石 {fmt_num(p.spirit_stones)}　寿元 {p.age}/{fmt_num(p.lifespan)}",
        *(_offline_line(game) or []),
        "  " + _progress_bar(p.progress_ratio()),
        f"  修为 {_exp_text(p)}　→ {p.next_target_name()}",
        *extras,
        LINE,
    ]
    buffs = p.attributes.active_buffs()
    if buffs:
        lines.append("  状态：" + "、".join(b.source.replace("buff:", "") for b in buffs))
    return "\n".join(lines)


def _foundation_lines(game: Game) -> list[str]:
    """修仙五要「法财侣地师」的根基概览（各系统可选装配，裁掉就不显示）。"""
    lines: list[str] = []
    est = game.systems.get("estate")
    if est is not None and est.estate_key:
        name = est.estate.name
        state = "（荒废）" if est.ruined else ""
        lines.append(f"  洞府 {name}{state}　灵气 {est._speed_text()}　维护 {est.estate.upkeep}/日")
    ast = game.systems.get("asset")
    if ast is not None and ast.owned:
        net = sum(ast.daily_stones(k) for k in ast.owned) - ast.daily_upkeep()
        stalled = "　有分号停摆" if any(ast.stalled.values()) else ""
        lines.append(f"  产业 {len(ast.owned)} 处　每日净收 灵石 {net}{stalled}")
    comp = game.systems.get("companion")
    if comp is not None and comp.met:
        parts = [f"{companion_config.get_companion(k).name}（{comp._scale_text(k)}）"
                 for k in comp.met]
        lines.append(f"  道侣 {'、'.join(parts)}")
    sect = game.systems.get("sect")
    if sect is not None and (sect.sect_key or sect.master_key):
        master = sect.master.name if sect.master else "未拜师"
        rank = sect.rank()
        lines.append(f"  师门 {sect.sect.name if sect.sect else '散修'} · {rank}　师长 {master}")
    return lines


def _art_line(game: Game) -> str:
    """已装备功法（可多件，显示品阶·等阶与当前威力）。"""
    sys_ = game.systems.get("arts")
    if sys_ is None or not sys_.equipped:
        return ""
    parts = []
    for art_id in sys_.equipped:
        art = art_config.get_art(art_id)
        prof = sys_.learned.get(art_id, 0)
        level = art_config.level_of(prof, art)
        parts.append(f"{art.name}（{art.rank}·{level}阶）{sys_._percent(art_id):.0f}%")
    return f"  运转 {'　'.join(parts)}（{len(sys_.equipped)}/{sys_.slot_cap()}）"


def _equip_line(game: Game) -> str:
    sys_ = game.systems.get("equipment")
    if sys_ is None or not game.player.equipment:
        return ""
    worn = []
    for slot, label in item_config.EQUIP_SLOTS.items():
        item_id = game.player.equipment.get(slot)
        if item_id:
            worn.append(f"{label}·{item_config.get_item(item_id).name}")
    return "  穿戴 " + "　".join(worn) if worn else ""


def _dungeon_line(game: Game) -> str:
    sys_ = game.systems.get("dungeon")
    run = getattr(sys_, "run", None)
    if not run:
        return ""
    dungeon = dungeon_config.get_dungeon(run["id"])
    tail = "（机缘待决）" if run.get("awaiting") else ""
    return f"  秘境 {dungeon.name}　第 {run['floor']}/{dungeon.depth} 层{tail}"


def _quest_line(game: Game) -> str:
    sys_ = game.systems.get("quest")
    if sys_ is None:
        return ""
    active = [qid for qid, st in sys_.accepted.items() if not st["done"]]
    reps = {f: r for f, r in sys_.reputation.items() if r}
    if not active and not reps:
        return ""
    parts: list[str] = []
    if active:
        parts.append(f"任务 {len(active)} 进行中")
    if reps:
        parts.append("声望 " + "、".join(f"{f} {r}" for f, r in reps.items()))
    return "  " + "　".join(parts)


def _skill_line(game: Game) -> str:
    """已修习功法带来的技能与当前施法策略。未装配或无技能则不显示。"""
    sys_ = game.systems.get("skill")
    if sys_ is None:
        return ""
    items = sys_.known()
    if not items:
        return ""
    names = "、".join(item["skill"].name for item in items)
    label = STRATEGY_LABELS[sys_.strategy]
    return f"  技能 {names}（施法策略：{label}）"


def _power_line(game: Game) -> str:
    """当前境界神通（金丹起每境一个，见 config/realms.py POWERS）。"""
    power = power_of(game.player.realm_key)
    return f"  神通 {power.name}：{power.desc}" if power else ""


def _offline_line(game: Game) -> list[str]:
    """待领修为（离线挂机池）非空时才显示一行。"""
    st = game.offline
    if st.pending_exp > 0.5:
        return [f"  待领修为 {fmt_num(st.pending_exp)}（claim 领取，突破后可继续领）"]
    return []


def _exp_text(p) -> str:
    """已至绝巅时 exp_required() 是 inf，直接格式化会打出「inf」。修为大数走中文单位。"""
    need = p.exp_required()
    return f"{fmt_num(p.exp)}（已至绝巅）" if need == float("inf") else f"{fmt_num(p.exp)}/{fmt_num(need)}"


def _progress_bar(ratio: float, width: int = 20) -> str:
    filled = int(round(max(0.0, min(1.0, ratio)) * width))
    return f"[{'█' * filled}{'·' * (width - filled)}] {ratio * 100:5.1f}%"


def inventory_panel(game: Game) -> str:
    inv = game.player.inventory
    if inv.is_empty():
        return "  囊中空空如也。"
    lines = [f"  灵石 {game.player.spirit_stones}"]
    for item_id, count in inv.all():
        item = item_config.get_item(item_id)
        tag = ""
        if item.usable:
            tag = "（可服用）"
        if item.breakthrough_bonus:
            targets = "、".join(item.breakthrough_bonus)
            tag += f"（助突破：{targets} +{list(item.breakthrough_bonus.values())[0]:.0%}）"
        # 品阶已写在名称与描述里（如「上品·聚气丹」（上品，药力 140%）），此处不再重复标注
        lines.append(f"  {item.name} ×{count}  [{item.kind}] {item.desc}{tag}")
    return "\n".join(lines)


def help_panel(commands: list[Command], builtin: list[tuple[str, str, str]]) -> str:
    lines = ["  内核命令："]
    for name, usage, desc in builtin:
        lines.append(f"    {_pad(usage, 30)} {desc}")
    lines.append("  玩法命令：")
    for c in commands:
        lines.append(f"    {_pad(c.usage, 30)} {c.desc}")
    return "\n".join(lines)


def _pad(text: str, width: int) -> str:
    """按显示宽度补齐：中文占两列，len() 只算一列，直接 f'{x:<30}' 会歪。"""
    return text + " " * max(1, width - _display_width(text))


def _display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def locations_panel() -> str:
    lines: list[str] = []
    for name, info in LOCATIONS.items():
        density = info.get("density", 1.0)
        event_rate = info.get("event_rate", 0.4)
        danger = info.get("danger", 0.0)     # v2 凶险机制已取消，默认无危险
        gate = info.get("min_realm", "")
        if gate:
            from ..config.realms import RealmRegistry
            gate = f"　需 {RealmRegistry.get(gate).name} 以上"
        lines.append(f"  {name:<8} 灵气 ×{density}　机缘 {event_rate:.0%}　凶险 {danger:.0%}{gate}")
    return "\n".join(lines)


def log_block(logs: list[str]) -> str:
    return "\n".join(f"  {line}" for line in logs) if logs else ""
