"""Web 视图数据层：把 Game 状态翻译成前端要的结构化 dict。

从 web_server.py 抽出，单机版（web_server.py）与联网版（app.py）共用同一套口径，
避免前后端两套判断逻辑分叉。纯函数，无副作用。
"""

from __future__ import annotations

from xiuxian.config import arts as art_config
from xiuxian.config import dungeons as dungeon_config
from xiuxian.config import items as item_config
from xiuxian.config.realms import RealmRegistry


def player_data(game) -> dict:
    """结构化角色数据（Web 面板渲染用；数值已 fmt_num 格式化，大数可读）。"""
    from xiuxian.core.numfmt import fmt_num
    from xiuxian.config.realms import power_of

    p = game.player
    need = p.exp_required()
    arts_sys = game.systems.get("arts")
    quest = game.systems.get("quest")
    power = power_of(p.realm_key)
    art_name = ""
    if arts_sys and getattr(arts_sys, "main_art", ""):
        art_name = art_config.get_art(arts_sys.main_art).name
    equip_names = [item_config.get_item(i).name for i in p.equipment.values()]
    arts: list[dict] = []
    if arts_sys is not None:
        for art_id in getattr(arts_sys, "equipped", []):
            art = art_config.get_art(art_id)
            arts.append({"id": art.id, "name": art.name, "rank": art.rank})
    return {
        "name": p.name,
        "title": p.realm_def.title,
        "realm": p.realm_name,
        "realm_is_immortal": RealmRegistry.in_immortal_realm(p.realm_key),
        "realm_family": "仙界" if RealmRegistry.in_immortal_realm(p.realm_key) else "凡界",
        "day": game.day,
        "location": game.location,
        "density": f"{game.location_info()['density']:.1f}",
        "next": p.next_target_name(),
        "hp": fmt_num(p.hp), "max_hp": fmt_num(p.max_hp),
        "hp_ratio": round(p.hp / max(p.max_hp, 1), 3),
        "mp": fmt_num(p.mp), "max_mp": fmt_num(p.max_mp),
        "mp_ratio": round(p.mp / max(p.max_mp, 1), 3),
        "stamina": f"{p.stamina:.0f}", "stamina_ratio": round(p.stamina / 100.0, 3),
        "atk": fmt_num(p.atk), "def": fmt_num(p.defense), "speed": fmt_num(p.speed),
        "spirit": fmt_num(p.attributes.int_value("spirit")),
        "comprehension": f"{p.comprehension:.0f}", "physique": f"{p.physique:.0f}",
        "luck": f"{p.luck:.0f}", "poison": f"{p.pill_poison:.0f}",
        "stones": fmt_num(p.spirit_stones),
        "age": p.age, "lifespan": fmt_num(p.lifespan),
        "exp": fmt_num(p.exp),
        "need": "圆满" if need == float("inf") else fmt_num(need),
        "progress": 1.0 if need == float("inf") else round(p.progress_ratio(), 3),
        "power": power.name if power else "",
        "art": art_name,
        "arts": arts,
        "equip": equip_names,
        "quests": len([q for q, st in (quest.accepted if quest else {}).items()
                       if not st.get("done")]),
        "buffs": [b.source.replace("buff:", "") for b in p.attributes.active_buffs()],
    }


def catalog_data(game) -> list[dict]:
    """秘境目录结构化数据（Web 弹窗渲染用）。"""
    from xiuxian.config.realms import RealmRegistry as _RR

    d_sys = game.systems.get("dungeon")
    out: list[dict] = []
    for d in dungeon_config.DUNGEONS.values():
        state = "可入"
        locked = ""
        if d_sys is not None:
            locked = d_sys.is_locked(d)
            if locked:
                state = locked
            elif getattr(d_sys, "run", None) and d_sys.run.get("id") == d.id:
                state = f"进行中：第 {d_sys.run['floor']}/{d.depth} 层"
        exp_q = ""
        stone_q = ""
        for eff in d.boss_reward:
            if eff.get("type") == "exp_ratio" and not exp_q:
                exp_q = f"修为 +{eff['value'] * 100:.0f}% 需求"
            elif eff.get("type") == "stone" and not stone_q:
                stone_q = f"灵石 {eff['value']}"
        out.append({
            "id": d.id,
            "name": d.name,
            "desc": d.desc,
            "depth": d.depth,
            "min_realm": _RR.get(d.min_realm).name,
            "min_realm_key": d.min_realm,
            "cooldown": d.cooldown,
            "stamina": d.stamina,
            "state": state,
            "is_immortal": _RR.in_immortal_realm(d.min_realm),
            "reward": "　".join(x for x in (exp_q, stone_q) if x),
        })
    return out


def actions_data(game) -> list[dict]:
    """根据玩家当前状态给出行动按钮（移动端点按交互，免打字）。

    上下文优先级：心魔劫 > 秘境进行中 > 待决机缘 > 可突破 > 有丹药可服 > 休闲动作。
    """
    from xiuxian.config.realms import RealmRegistry as _RR
    from xiuxian.core.inventory import DAILY_PILL_LIMIT
    from xiuxian.systems.inner_demon import TRIALS

    p = game.player
    cult = game.system("cultivation")
    event_sys = game.systems.get("event")
    demon_sys = game.systems.get("inner_demon")
    dungeon = game.systems.get("dungeon")
    acts: list[dict] = []
    if not p.alive:
        acts.append({"cmd": "status", "label": "查看状态"})
        return acts

    # ---- 1. 心魔劫：突破后的四选一（最紧急，挡住一切后续） ----
    if demon_sys is not None and demon_sys.pending:
        trial_def = next((t for t in TRIALS if t.id == demon_sys.pending.get("id")), None)
        if trial_def:
            for i, c in enumerate(trial_def.choices, 1):
                acts.append({"cmd": f"demon {i}", "label": c.text, "primary": True})
            return acts

    # ---- 2. 秘境进行中：深入 / 退出（进度不丢） ----
    if dungeon is not None and getattr(dungeon, "run", None):
        d_run = dungeon.run
        try:
            d_name = dungeon_config.get_dungeon(d_run["id"]).name
            floor = d_run.get("floor", 1)
            depth = dungeon_config.get_dungeon(d_run["id"]).depth
        except Exception:
            d_name, floor, depth = "秘境", "?", "?"
        acts.append({"cmd": "dungeon next",
                     "label": f"深入 · {d_name} {floor}/{depth}", "primary": True})
        acts.append({"cmd": "dungeon flee", "label": "退出秘境"})
        if d_run.get("awaiting"):
            acts = []
            if event_sys is not None and event_sys.pending:
                for i, c in enumerate(event_sys.pending.choices, 1):
                    ok, reason = event_sys.check_require(c.require)
                    if not ok:
                        continue
                    acts.append({"cmd": f"choose {i}", "label": f"择·{c.text[:8]}",
                                 "primary": True})
                acts.append({"cmd": "choose 0", "label": "抽身离去"})
            acts.append({"cmd": "dungeon next", "label": f"继续深入 · {d_name}"})
            acts.append({"cmd": "dungeon flee", "label": "退出秘境"})
        return acts

    # ---- 3. 待决机缘（户外事件探索触发） ----
    if event_sys is not None and event_sys.pending:
        for i, c in enumerate(event_sys.pending.choices, 1):
            ok, reason = event_sys.check_require(c.require)
            if not ok:
                continue
            acts.append({"cmd": f"choose {i}", "label": f"择·{c.text[:8]}",
                         "primary": True})
        acts.append({"cmd": "choose 0", "label": "抽身离去"})
        return acts

    # ---- 4. 突破 / 冲关冷却 / 精力不足 ----
    cooling = cult.cooldown_left() if cult else 0
    primary_cmd, primary_label = "", ""
    if p.can_breakthrough():
        if cooling > 0:
            primary_cmd, primary_label = "rest 6", f"调息（冷却 {cooling} 日）"
        elif p.stamina >= 30:
            primary_cmd, primary_label = "breakthrough", "突 破"
        else:
            primary_cmd, primary_label = "rest 6", "休息至可突破"
    elif p.stamina < 30:
        primary_cmd, primary_label = "rest 6", "休息恢复精力"
    else:
        primary_cmd, primary_label = "idle", "闭关打坐"
    if primary_cmd:
        acts.append({"cmd": primary_cmd, "label": primary_label, "primary": True})

    # ---- 5. 背包有可用丹药：直接可服用（免打 use <id>） ----
    pill_list: list[dict] = []
    for item_id, n in p.inventory.all():
        item = item_config.get_item(item_id)
        if item.usable and n > 0:
            pill_list.append({"cmd": f"use {item_id}", "label": f"服 {item.name}", "n": n})
    if pill_list:
        used = p.daily_used(game.day, "pill")
        acts.append({"cmd": "bag", "label": f"丹药({used}/{DAILY_PILL_LIMIT})"})
        for pi in pill_list:
            acts.append({"cmd": pi["cmd"], "label": pi["label"]})

    # ---- 6. 休闲动作（静态） ----
    dungeon_acts: list[dict] = []
    if dungeon is not None and not getattr(dungeon, "run", None):
        for _id, _d in dungeon_config.DUNGEONS.items():
            locked = dungeon.is_locked(_d)
            if not locked:
                dungeon_acts.append({"cmd": f"dungeon enter {_id}",
                                     "label": f"秘境·{_d.name}"})
    acts += [
        {"cmd": "hunt", "label": "打猎"},
        {"cmd": "duel", "label": "论道"},
        {"cmd": "companion dual", "label": "双修"},
        {"cmd": "estate upgrade", "label": "扩建洞府"},
        {"cmd": "asset upgrade spirit_field", "label": "增资产业"},
        {"cmd": "sect mentor", "label": "师门指点"},
        {"cmd": "explore", "label": "探查"},
        {"cmd": "dan gather 4", "label": "采药"},
    ]
    acts += dungeon_acts
    acts.append({"cmd": "dungeon list", "label": "秘境目录", "open_catalog": True})
    acts += [
        {"cmd": "art list", "label": "功法"},
        {"cmd": "daily list", "label": "日常"},
        {"cmd": "status", "label": "状态"},
        {"cmd": "help", "label": "帮助"},
    ]
    return acts
