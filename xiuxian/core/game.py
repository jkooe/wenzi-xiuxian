"""游戏主体：状态容器 + 时间推进 + 系统编排。

Game 只做三件事：
    1. 持有世界状态（角色、时间、地点、随机源、日志）
    2. 推进时间并在关键节点广播事件
    3. 聚合各玩法系统暴露的命令
具体规则一律下沉到 systems，Game 不写任何玩法逻辑。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..config.realms import LOCATIONS
from .base_system import (
    Command,
    EventBus,
    GameSystem,
    TOPIC_DAY_END,
    TOPIC_HOUR_PASSED,
    TOPIC_LOADED,
    TOPIC_NEW_GAME,
)
from ..rng import RNG
from .cultivator import MAX_STAMINA, Cultivator
from .bonus import BonusAggregator
from .time_governor import TimeGovernor
from .offline import OfflineState

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAVE_DIR = PROJECT_ROOT / "saves"
HOURS_PER_DAY = 24
DAYS_PER_YEAR = 360
# 每日精力恢复（v2 放宽版：恢复 2/时辰，在线离线均恢复）：
# 跨日恢复 48（= 2 × 24 时）+ 离线按流逝时长恢复 + 调息恢复 2/时。
# 精力只限制主动操作频率（猎妖/论道/探索/炼丹/秘境），不限制挂机收益。
DAILY_STAMINA_RECOVERY = 48.0


class Game:
    def __init__(
        self,
        player: Cultivator,
        rng: RNG | None = None,
        location: str = "青石镇",
    ) -> None:
        self.player = player
        self.rng = rng or RNG()
        self.bus = EventBus()
        self.systems: dict[str, GameSystem] = {}
        self._system_order: list[str] = []

        self.location = location
        self.day = 1
        self.hour = 6

        self.logs: list[str] = []
        self.offline = OfflineState()          # 离线挂机累计（时间戳 + 待领修为池）
        self.bonuses = BonusAggregator()       # 全局加成聚合（法/财/侣/地/师各来源统一汇总）
        self.clock = TimeGovernor()            # 时间预算（1 现实小时 = 10 游戏时辰）
        self._loading = False                  # 读档中：期间不夹气血/灵力，避免中间态误削
        self.effect_handlers: dict[str, Callable[[Any, dict], list[str]]] = {}
        self.running = True
        self.over = False
        self.end_reason = ""

    # ---------- 系统装配 ----------
    def add_system(self, system: GameSystem) -> None:
        if not system.id:
            raise ValueError(f"{type(system).__name__} 缺少 id")
        system.bind(self)
        self.systems[system.id] = system
        self._system_order.append(system.id)

    def system(self, system_id: str) -> GameSystem:
        return self.systems[system_id]

    def rebuild_bonuses(self) -> None:
        """重新收集「法财侣地师」各来源的效果并真正生效（幂等）。

        各玩法系统实现 `collect_bonuses(aggregator)` 把自己的效果交给聚合器；
        装卸、升级、好感变化、拜师、读档后调用这里即可 —— 新增来源无需改这里。
        """
        self.bonuses.clear()
        for sid in self._system_order:
            collect = getattr(self.systems[sid], "collect_bonuses", None)
            if callable(collect):
                collect(self.bonuses)
        self.bonuses.rebuild(self.player)
        # 唯一出口处才夹：此时所有来源的加成已注入完毕
        self.player.hp = min(self.player.hp, self.player.max_hp)
        self.player.mp = min(self.player.mp, self.player.max_mp)

    def commands(self) -> list[Command]:
        out: list[Command] = []
        for sid in self._system_order:
            out.extend(self.systems[sid].commands())
        return out

    def register_effect(self, etype: str, handler: Callable[[Any, dict], list[str]]) -> None:
        """注册自定义效果类型，供事件/物品 DSL 调用。"""
        self.effect_handlers[etype] = handler

    # ---------- 日志 ----------
    def log(self, text: str) -> None:
        if text:
            self.logs.append(str(text))

    def emit_logs(self, logs: list[str]) -> None:
        for line in logs:
            self.log(line)

    def drain_logs(self) -> list[str]:
        out, self.logs = self.logs, []
        return out

    # ---------- 世界 ----------
    def location_info(self) -> dict[str, float]:
        return LOCATIONS.get(self.location, {"density": 1.0, "event_rate": 0.4})

    def travel(self, location: str) -> list[str]:
        if location not in LOCATIONS:
            return [f"无此去处。可选：{'、'.join(LOCATIONS)}"]
        if location == self.location:
            return [f"你已在{location}。"]
        # 门槛判断必须读「目标地点」的信息，而非当前所在地（location_info 查的是 self.location）
        info = LOCATIONS[location]
        min_realm = info.get("min_realm")
        if min_realm:
            from ..config.realms import RealmRegistry
            cur = RealmRegistry.index_of(self.player.realm_key)
            need = RealmRegistry.index_of(min_realm)
            if cur < need:
                return [f"此地隐于仙凡之间，非 {RealmRegistry.get(min_realm).name} 之上不得踏入。"]
        old = self.location
        self.location = location
        logs = [f"自{old}启程，抵达{location}（灵气 ×{info['density']}）。"]
        logs.extend(self.advance_time(6))
        return logs

    def time_text(self) -> str:
        # round 而非 int 截断：与存档 round(hour, 2) 对齐，避免 20.999999 显示成 20、
        # 读档（21.0）显示成 21 的假性不一致
        return f"第 {self.day} 日 {int(round(self.hour)):02d} 时"

    # ---------- 时间推进 ----------
    def advance_time(self, hours: float, bypass_budget: bool = False) -> list[str]:
        """推进游戏时间。

        bypass_budget=True 时跳过时间预算闸门（闭关挂机/离线结算/被动周天等
        非玩家主动操作的时间推进不受预算限制）。
        """
        logs: list[str] = []
        if hours <= 0:
            return logs

        if not bypass_budget:
            allowed = self.clock.consume(hours)
            if allowed < hours:
                hours = allowed
                if hours <= 0:
                    remaining = self.clock.status_text()
                    tip = f"　（{remaining}）" if remaining else "　（稍候片刻，时间自会流逝）"
                    return [f"时间之力暂时耗尽。{tip}"]

        self.hour += hours
        expired = self.player.attributes.tick(hours)
        logs.extend(expired)
        self.bus.emit(TOPIC_HOUR_PASSED, {"hours": hours})

        while self.hour >= HOURS_PER_DAY:
            self.hour -= HOURS_PER_DAY
            logs.extend(self._new_day())
            if self.over:
                break
        return logs

    def _new_day(self) -> list[str]:
        p = self.player
        self.day += 1
        logs = [f"—— 第 {self.day} 日 ——"]

        p.stamina = min(MAX_STAMINA, p.stamina + DAILY_STAMINA_RECOVERY)   # 每日行动预算缓慢恢复
        p.heal_hp(p.max_hp * 0.20)
        p.heal_mp(p.max_mp * 0.30)
        if p.pill_poison > 0:
            p.pill_poison = max(0.0, p.pill_poison - 1.0)

        if self.day % DAYS_PER_YEAR == 0:
            p.age += 1
            logs.append(f"年岁增长，今岁 {p.age}。寿元 {p.lifespan} 载。")
            # 挂机养老定位：寿元纯展示，不因年岁耗尽而陨落

        self.bus.emit(TOPIC_DAY_END, {"day": self.day})
        return logs

    # ---------- 玩家动作 ----------
    def use_item(self, item_id: str) -> list[str]:
        if not self.player.alive:
            return ["你已身死道消。"]
        return self.player.inventory.use(self, item_id)

    def check_game_over(self) -> list[str]:
        if self.over:
            return []
        if not self.player.alive:
            self.over = True
            self.end_reason = f"{self.player.name} 道消身陨，止步于 {self.player.realm_name}。"
            return [self.end_reason]
        return []

    # ---------- 状态展示（数据层，渲染交给 ui）----------
    def status_lines(self) -> list[str]:
        from .numfmt import fmt_num

        p = self.player
        need = p.exp_required()
        if need == float("inf"):
            progress = "圆满"
        else:
            progress = f"{fmt_num(p.exp)}/{fmt_num(need)}（{p.progress_ratio() * 100:.1f}%）"
        return [
            f"{p.full_title}",
            f"寿元 {p.age}/{fmt_num(p.lifespan)} 载",
            f"气血 {fmt_num(p.hp)}/{fmt_num(p.max_hp)}　灵力 {fmt_num(p.mp)}/{fmt_num(p.max_mp)}　精力 {p.stamina:.0f}/100",
            f"攻击 {fmt_num(p.atk)}　防御 {fmt_num(p.defense)}　身法 {fmt_num(p.speed)}　神识 {fmt_num(p.attributes.int_value('spirit'))}",
            f"悟性 {p.comprehension:.0f}　根骨 {p.physique:.0f}　气运 {p.luck:.0f}　丹毒 {p.pill_poison:.0f}",
            f"修为 {progress}　下一境界：{p.next_target_name()}",
            f"灵石 {fmt_num(p.spirit_stones)}　地点 {self.location}　{self.time_text()}",
        ]

    # ---------- 序列化 ----------
    def to_dict(self) -> dict[str, Any]:
        return {
            "player": self.player.to_dict(),
            "rng": self.rng.to_dict(),
            "world": {
                "day": self.day,
                "hour": round(self.hour, 2),
                "location": self.location,
            },
            "systems": {sid: self.systems[sid].to_dict() for sid in self._system_order},
            "offline": self.offline.to_dict(),
            "clock": self.clock.to_dict(),
        }

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], systems_factory: Callable[[], list[GameSystem]]
    ) -> "Game":
        player = Cultivator.from_dict(data["player"])
        world = data.get("world", {})
        game = cls(
            player=player,
            rng=RNG.from_dict(data["rng"]),
            location=world.get("location", "青石镇"),
        )
        game.day = int(world.get("day", 1))
        game.hour = float(world.get("hour", 6))
        game.offline = OfflineState.from_dict(data.get("offline"))
        game.clock = TimeGovernor.from_dict(data.get("clock"))
        game.clock.sync()                      # 读档即同步预算基准（离线收益由 offline.py 处理）

        # 装配阶段就会触发 rebuild（on_bind / load_state），此时加成尚未齐全，
        # 必须先置 loading 标志：否则中间态的 clamp 会把当前气血/灵力按「无加成的上限」误削。
        game._loading = True
        try:
            for s in systems_factory():
                game.add_system(s)
        finally:
            game._loading = False

        game._loading = True                   # 各系统逐个恢复，期间同样不做 clamp
        try:
            for sid, sdata in (data.get("systems") or {}).items():
                if sid in game.systems:
                    game.systems[sid].load_state(sdata or {})
        finally:
            game._loading = False

        game.rebuild_bonuses()                 # 全部就绪后统一重算

        # 兜底：重算过程中若某次中间态按「无加成上限」夹过当前气血/灵力，此处按存档值恢复，
        # 保证「读档前后当前值一致」（上限仍以最终重算结果为准，超出部分仍会被夹住）。
        saved = data.get("player") or {}
        if "mp" in saved and "hp" in saved:
            game.player.mp = min(float(saved["mp"]), game.player.max_mp)
            game.player.hp = min(float(saved["hp"]), game.player.max_hp)
        game.bus.emit(TOPIC_LOADED, {})
        return game
