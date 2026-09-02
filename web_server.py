"""Web 版入口：手机浏览器即可试玩（安卓/iOS/电脑同网访问）。

    python web_server.py [端口]        # 默认 8000，监听 0.0.0.0
    ./run.sh web                       # 或走一键启动

手机连同一 WiFi 后，浏览器打开 http://<本机IP>:8000 即可游玩。
挂机友好：页面每 30 秒自动刷新状态，命令间隙的现实时钟结算照常生效
（复用 CLI 的 _settle_online，在线倍率 1.5 不变）。

注意：仅供局域网/本机测试，无鉴权，请勿暴露到公网。
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from xiuxian.factory import create_game  # noqa: E402
from xiuxian.ui import panel  # noqa: E402
from xiuxian.ui.cli import CLI  # noqa: E402
from xiuxian.config import arts as art_config  # noqa: E402
from xiuxian.config import dungeons as dungeon_config  # noqa: E402
from xiuxian.config import items as item_config  # noqa: E402
from xiuxian.config.realms import RealmRegistry  # noqa: E402


def _load_page() -> str:
    """从 xiuxian/ui/web/ 加载页面骨架 + 设计令牌 + 组件样式 + 组件逻辑。

    组件化后页面资源独立成文件（tokens.css / app.css / app.js / page.html），
    改 UI 无需动服务端逻辑；启动时读入内嵌，保持单文件零依赖、无外部请求。
    """
    web_dir = Path(__file__).resolve().parent / "xiuxian" / "ui" / "web"
    html = (web_dir / "page.html").read_text(encoding="utf-8")
    tokens = (web_dir / "tokens.css").read_text(encoding="utf-8")
    app_css = (web_dir / "app.css").read_text(encoding="utf-8")
    app_js = (web_dir / "app.js").read_text(encoding="utf-8")
    return (html.replace("/*TOKENS_CSS*/", tokens)
                .replace("/*APP_CSS*/", app_css)
                .replace("/*APP_JS*/", app_js))


PAGE = _load_page()




def player_data(game) -> dict:
    """结构化角色数据（Web 面板渲染用；数值已 fmt_num 格式化，大数可读）。"""
    from xiuxian.core.numfmt import fmt_num
    from xiuxian.config import arts as art_config
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
    # 已装备功法（可多件）：带品阶，前端据此高亮「仙阶」
    arts: list[dict] = []
    if arts_sys is not None:
        for art_id in getattr(arts_sys, "equipped", []):
            art = art_config.get_art(art_id)
            arts.append({"id": art.id, "name": art.name, "rank": art.rank})
    return {
        "name": p.name,
        "title": p.realm_def.title,
        "realm": p.realm_name,
        # 仙界身份（Web 端「仙」徽章 / 栏目归属用）
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
    """秘境目录结构化数据（Web 弹窗渲染用）。

    复用 dungeon 系统的 is_locked / catalog 口径，避免前后端两套判断逻辑：
       - 每座秘境给：名称 / 描述 / 层数 / 门槛境界名 / 冷却 / 当前状态（可入/门槛/闭息/进行中）
       - 附带本次通关（boss）的修为比例与灵石量级，前端据此渲染奖励摘要
    序按配置顺序（落云 -> 万兽 -> 太虚 -> 瑶池 -> 万劫），仙界秘境天然排在最后。
    """
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
        # 守关奖励摘要：只取第一个 exp_ratio 与 stone，作为「通关可得」的定性提示
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


class GameSession:
    """服务端唯一的游戏会话：复用 CLI 分发逻辑 + 现实时钟结算。"""

    def __init__(self) -> None:
        self.game = create_game(name="修士", seed=None)
        self.cli = CLI(self.game)

    def command(self, line: str) -> list[str]:
        return self.cli.run_line(line)

    def status(self) -> str:
        self.cli._settle_online()                # 页面刷新也结算挂机收益
        return panel.status_panel(self.game)

    def actions(self) -> list[dict]:
        """根据玩家当前状态给出行动按钮（移动端点按交互，免打字）。

        设计原则：把「玩家此刻卡在哪一步」的动态下一步放在最前面（primary），
        静态休闲动作（打猎/论道/双修/探查等）排在其后，尽量避免必须输命令才能继续。
        上下文优先级：心魔劫 > 秘境进行中 > 待决机缘 > 可突破 > 有丹药可服 > 休闲动作。
        """
        p = self.game.player
        cult = self.game.system("cultivation")
        event_sys = self.game.systems.get("event")
        demon_sys = self.game.systems.get("inner_demon")
        dungeon = self.game.systems.get("dungeon")
        acts: list[dict] = []
        if not p.alive:
            acts.append({"cmd": "status", "label": "查看状态"})
            return acts

        # ---- 1. 心魔劫：突破后的四选一（最紧急，挡住一切后续） ----
        if demon_sys is not None and demon_sys.pending:
            from xiuxian.systems.inner_demon import TRIALS
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
            acts.append({"cmd": f"dungeon next", "label": f"深入 · {d_name} {floor}/{depth}",
                         "primary": True})
            acts.append({"cmd": "dungeon flee", "label": "退出秘境"})
            if d_run.get("awaiting"):
                # 层内机缘待决：先把事件选项做出来（choose N）
                acts = []
                if event_sys is not None and event_sys.pending:
                    for i, c in enumerate(event_sys.pending.choices, 1):
                        ok, reason = event_sys.check_require(c.require)
                        if not ok:
                            continue
                        acts.append({"cmd": f"choose {i}", "label": f"择·{c.text[:8]}",
                                     "primary": True})
                    acts.append({"cmd": "choose 0", "label": "抽身离去"})
                acts.append({"cmd": f"dungeon next", "label": f"继续深入 · {d_name}"})
                acts.append({"cmd": "dungeon flee", "label": "退出秘境"})
            # 返回前置的秘境动作 + 其它
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
        # 每日服药限额提示（非按钮，仅信息）
        if pill_list:
            from xiuxian.core.inventory import DAILY_PILL_LIMIT
            used = p.daily_used(self.game.day, "pill")
            acts.append({"cmd": "bag", "label": f"丹药({used}/{DAILY_PILL_LIMIT})"})
            for pi in pill_list:
                acts.append({"cmd": pi["cmd"], "label": pi["label"]})

        # ---- 6. 休闲动作（静态） ----
        from xiuxian.config.realms import RealmRegistry as _RR
        dungeon_acts: list[dict] = []
        # 不在秘境中时：把「可进入的秘境」直接做成 enter 按钮（免打 dungeon enter <id>）
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
        # 统一升级为「秘境目录」：前端收到 open_catalog 标志后调 /api/catalog 弹窗展示
        # （含仙阶秘境与门槛，凡界玩家也能看到飞升后的去处）
        acts.append({"cmd": "dungeon list", "label": "秘境目录", "open_catalog": True})
        acts += [
            {"cmd": "art list", "label": "功法"},
            {"cmd": "daily list", "label": "日常"},
            {"cmd": "status", "label": "状态"},
            {"cmd": "help", "label": "帮助"},
        ]
        return acts


# P2-7: 按会话隔离。同一台电脑上的多个设备/标签页各玩各的角色，
# 不再因为全局单实例而互相覆盖状态。
SESSIONS: dict[str, GameSession] = {}


def session_for(sid: str) -> GameSession:
    if sid not in SESSIONS:
        SESSIONS[sid] = GameSession()
    return SESSIONS[sid]


def _sid_from_cookie(handler: "Handler") -> str:
    """从 Cookie 取会话 id；没有就新建一个并回写（同一浏览器固定同一个角色）。"""
    cookie = handler.headers.get("Cookie", "") or ""
    for part in cookie.split(";"):
        k, _, v = part.strip().partition("=")
        if k == "sid" and v:
            return v[:64]
    import uuid
    sid = uuid.uuid4().hex
    handler._new_cookie = sid          # 由 _send_headers 统一回写
    return sid


class Handler(BaseHTTPRequestHandler):
    _new_cookie: str | None = None

    # ---------- P1-5：HTTP 边界兜住所有下游异常 ----------
    def do_GET(self) -> None:
        try:
            if self.path == "/api/status":
                sess = session_for(_sid_from_cookie(self))
                self._json({"status": sess.status(),
                            "data": player_data(sess.game),
                            "actions": sess.actions()})
            elif self.path == "/api/catalog":
                sess = session_for(_sid_from_cookie(self))
                self._json({"catalog": catalog_data(sess.game)})
            else:
                body = PAGE.encode("utf-8")
                self.send_response(200)
                self._send_headers("text/html; charset=utf-8", len(body))
                self.end_headers()
                self.wfile.write(body)
        except Exception as exc:                       # 任何异常都不该变成空响应
            self._fail(exc)

    def do_POST(self) -> None:
        try:
            if self.path == "/api/command":
                length = int(self.headers.get("Content-Length", 0))
                try:
                    data = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                    line = str(data.get("line", ""))[:200]
                except (ValueError, UnicodeDecodeError):
                    line = ""
                sess = session_for(_sid_from_cookie(self))
                self._json({"logs": sess.command(line),
                            "time": sess.game.time_text()})
            else:
                self.send_error(404)
        except Exception as exc:
            self._fail(exc)

    def _fail(self, exc: Exception) -> None:
        """结构化返回错误，前端能显示原因而不是「连接中断」。"""
        import traceback
        traceback.print_exc()
        self._json({"error": f"{type(exc).__name__}: {exc}"}, code=500)

    def _send_headers(self, content_type: str, length: int) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        if self._new_cookie:
            self.send_header("Set-Cookie", f"sid={self._new_cookie}; Path=/; Max-Age=86400")
            self._new_cookie = None

    def _json(self, obj: dict, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._send_headers("application/json; charset=utf-8", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:   # 静默访问日志
        pass


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8000
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"文字修仙 Web 版已启动：http://0.0.0.0:{port}")
    print("手机连同一 WiFi，用浏览器打开 http://<本机IP>:" + str(port) + "（本机 IP 可用 ifconfig/ipconfig 查询）")
    print("按 Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
