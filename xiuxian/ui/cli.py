"""终端交互层：命令解析 + 回合展示。

表现层不含任何规则，只做三件事：读输入 -> 调 Game/系统 -> 打印结果。

现实时钟（全程跟现实时间走）：命令间隙流逝的现实时间会在下一次命令前
自动结算为闭关打坐（在线倍率 1.5，见 factory.ONLINE_DAYS_PER_HOUR）——
挂着不动也一直打坐，回来敲任意键即看到收益。
"""

from __future__ import annotations

import time

from ..config import dungeons as dungeon_config
from ..core.game import Game
from ..core.save import SaveError, SaveManager
from ..factory import ONLINE_DAYS_PER_HOUR, load_game, settle_realtime
from . import panel

BUILTIN = [
    ("status", "status", "查看状态面板"),
    ("bag", "bag", "查看储物袋"),
    ("claim", "claim", "领取离线挂机待领修为"),
    ("use", "use <物品id>", "服用/使用物品"),
    ("travel", "travel <地点>", "前往指定地点"),
    ("map", "map", "查看已知地点"),
    ("save", "save [槽位]", "存档（默认槽位 1）"),
    ("load", "load [槽位]", "读档"),
    ("slots", "slots", "列出所有存档"),
    ("help", "help", "帮助"),
    ("quit", "quit", "退出（自动存档到槽位 1）"),
    ("enter", "回车", "自动打坐 4 时辰；idle 闭关可无人值守"),
    ("idle-auto", "idle-auto on/off", "一键托管：自动打坐-休息循环（修为圆满自动停）"),
]


class CLI:
    def __init__(self, game: Game) -> None:
        self.game = game
        self.saves = SaveManager()
        self.last_clock = time.time()        # 现实时钟起点：命令间隙流逝的时间会结算为打坐
        self._auto_idle = False              # 一键托管（idle-auto）：主循环自动闭关

    # ---------- Web 版入口 ----------
    def run_line(self, line: str) -> list[str]:
        """执行一行命令（含现实时钟结算），返回全部输出行。

        CLI 交互版走 run() 打印；Web 版用这里捕获输出（redirect_stdout 复用
        完全相同的 dispatch 逻辑，两条入口行为一致）。
        """
        import contextlib
        import io

        self._settle_online()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            if not line.strip():
                self.dispatch("cultivate 4")
            else:
                self.dispatch(line)
            self.game.check_game_over()
        text = buf.getvalue().rstrip("\n")
        return text.split("\n") if text else []

    # ---------- 主循环 ----------
    def run(self) -> None:
        print(panel.status_panel(self.game))
        print("输入 help 查看命令。")
        while self.game.running and not self.game.over:
            try:
                line = input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            self._settle_online()            # 先把「挂着不动」的现实时间结算成闭关打坐
            if self._auto_idle:
                # 一键托管：任何输入前先自动闭关一段（修为圆满自动停）
                self.dispatch("idle 1")
                if not line:
                    continue
            if not line:
                self.dispatch("cultivate 4")     # 空回车 = 自动打坐（挂机连按回车即持续修炼）
                continue
            self.dispatch(line)
            self.game.check_game_over()

        if self.game.over:
            print(f"\n{panel.LINE}\n  {self.game.end_reason}\n  本局就此作罢。\n{panel.LINE}")

    def _settle_online(self) -> None:
        """现实时钟结算：距上次命令流逝的现实时间按在线倍率折算为闭关打坐。"""
        now = time.time()
        seconds = now - self.last_clock
        self.last_clock = now
        if seconds < 60:                     # 正常操作间隙不结算，避免秒级噪音
            return
        logs = settle_realtime(self.game, seconds, ONLINE_DAYS_PER_HOUR)
        if logs:
            hours = seconds / 3600.0
            head = f"—— 时光流转 {hours:.1f} 时辰（游戏内约 {hours * ONLINE_DAYS_PER_HOUR:.1f} 日）——"
            self.game.emit_logs([head] + logs)
            print(panel.log_block(self.game.drain_logs()))
            print()

    # ---------- 分发 ----------
    def dispatch(self, line: str) -> None:
        parts = line.split()
        cmd, args = parts[0].lower(), parts[1:]

        for c in self.game.commands():
            if c.name == cmd and c.handler:
                c.handler(args)
                self._after()
                return

        if cmd in ("status", "st"):
            print(panel.status_panel(self.game))
        elif cmd == "bag":
            print(panel.inventory_panel(self.game))
        elif cmd == "claim":
            self._act(self._claim())
        elif cmd == "use":
            self._use(args)
        elif cmd == "travel":
            self._act(self.game.travel(args[0]) if args else ["用法：travel <地点>"])
        elif cmd == "map":
            print(panel.locations_panel())
        elif cmd in ("idle-auto", "auto", "托管"):
            if args and args[0].lower() in ("on", "开", "1", "true"):
                self._auto_idle = True
                print("  一键托管已开启：自动打坐-休息循环（修为圆满自动停，不自动突破/嗑药）。")
                print("  输入 idle-auto off 关闭。")
            else:
                self._auto_idle = False
                print("  一键托管已关闭。")
        elif cmd == "save":
            self._save(args)
        elif cmd == "load":
            self._load(args)
        elif cmd == "slots":
            self._slots()
        elif cmd in ("help", "?"):
            print(panel.help_panel(self.game.commands(), BUILTIN))
        elif cmd in ("quit", "exit", "q"):
            self.game.running = False
            self._save(["1"])
        else:
            print(f"  未知指令：{cmd}（输入 help 查看）")
            return
        self._after()

    def _after(self) -> None:
        """统一输出本回合日志，并提示待决事件 / 可突破。"""
        logs = self.game.drain_logs()
        if logs:
            print(panel.log_block(logs))
            print()

        p = self.game.player
        event_sys = self.game.systems.get("event")
        cult_sys = self.game.systems.get("cultivation")
        cooling = cult_sys.cooldown_left() if cult_sys else 0

        if event_sys and getattr(event_sys, "pending", None):
            print("  [[ 有事件待你抉择，输入 choose N ]]")
        elif p.can_breakthrough() and cooling > 0:
            print(f"  [[ 修为圆满，但心神未复，还需调养 {cooling} 日才能冲关 ]]")
        elif p.can_breakthrough() and p.stamina >= 30:
            print("  [[ 修为圆满，可输入 breakthrough 冲击下一境界 ]]")
        elif p.can_breakthrough():
            print("  [[ 修为圆满，但精力不足 30，先 rest 6 再突破 ]]")

        dungeon_sys = self.game.systems.get("dungeon")
        if dungeon_sys and getattr(dungeon_sys, "run", None):
            run = dungeon_sys.run
            dungeon = dungeon_config.get_dungeon(run["id"])
            print(f"  [[ 尚在 {dungeon.name} 第 {run['floor']}/{dungeon.depth} 层："
                  f"dungeon next 深入 / dungeon flee 退出 ]]")

    # ---------- 内置动作 ----------
    def _act(self, logs: list[str]) -> None:
        self.game.emit_logs(logs)

    def _claim(self) -> list[str]:
        """领取离线挂机的待领修为（本层已满时领不进去，余额留池）。"""
        from ..core.numfmt import fmt_num
        from ..core.offline import claim as claim_offline

        state = self.game.offline
        if state.pending_exp <= 0:
            return ["并无待领修为。" if not self.game.player.alive else "待领修为空空如也。"]
        got = claim_offline(state, self.game.player.add_exp)
        # 日常追踪：领取离线收益
        daily = self.game.systems.get("daily")
        if daily is not None:
            daily.track("claim")
        if got <= 0:
            return [f"本层修为已满，{fmt_num(state.pending_exp)} 修为留存待领"
                    f"（breakthrough 突破后再来）"]
        return [f"领取离线挂机修为 +{fmt_num(got)}"
                f"（剩余待领 {fmt_num(state.pending_exp)}）"]

    def _use(self, args: list[str]) -> None:
        if not args:
            print(panel.inventory_panel(self.game))
            return
        self.game.emit_logs(self.game.use_item(args[0]))

    def _save(self, args: list[str]) -> None:
        slot = int(args[0]) if args and args[0].isdigit() else 1
        path = self.saves.save(self.game, slot)
        print(f"  已存档至槽位 {slot}：{path.name}")

    def _load(self, args: list[str]) -> None:
        slot = int(args[0]) if args and args[0].isdigit() else 1
        try:
            self.game = load_game(slot)
        except SaveError as exc:
            print(f"  读档失败：{exc}")
            return
        print(panel.status_panel(self.game))
        print(f"  已读取槽位 {slot}。")

    def _slots(self) -> None:
        slots = self.saves.list_slots()
        if not slots:
            print("  暂无存档。")
            return
        for s in slots:
            if s.get("corrupted"):
                print(f"  槽位 {s['slot']}：存档损坏")
                continue
            sm = s.get("summary", {})
            print(f"  槽位 {s['slot']}：{sm.get('name', '?')} · {sm.get('realm', '?')} · "
                  f"第{sm.get('day', '?')}日 · {s.get('saved_at')}")
