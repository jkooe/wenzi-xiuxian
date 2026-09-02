"""剧情事件系统：数据驱动，所有事件写在 data/events.json。

事件 = 条件（谁能遇到） + 权重（多常见） + 选项（每个选项带前置条件与效果）
效果复用 core/effects.py 的 DSL，因此事件可以直接发物品、改属性、触发战斗。

新增剧情：只改 JSON，不动代码。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config.realms import RealmRegistry
from .base_system import Command, GameSystem, TOPIC_EVENT_RESOLVED
from .effects import apply_effects

DEFAULT_EVENT_FILE = Path(__file__).resolve().parents[2] / "data" / "events.json"

# 场景标签：事件 JSON 的 conditions.where 用。内核只知道标签名，不知道「秘境」是什么。
SCENE_OUTDOOR = "outdoor"     # 走街串巷、荒野历练
SCENE_DUNGEON = "dungeon"     # 秘境层内（避免「宗门招徒」之类的户外事件出现在洞天里）

# v2 探索疲惫：连续探索 EXPLORE_TIRED_THRESHOLD 次后机缘率减半，
# 休息 EXPLORE_TIRED_REST_HOURS 时辰可清零（防反复 explore 刷机缘）。
EXPLORE_TIRED_THRESHOLD = 3
EXPLORE_TIRED_REST_HOURS = 4
EXPLORE_TIRED_FLAG = "explore_tired"


@dataclass
class ChoiceDef:
    id: str
    text: str
    effects: list[dict[str, Any]] = field(default_factory=list)
    require: dict[str, Any] = field(default_factory=dict)
    hint: str = ""          # 不满足条件时显示的原因

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChoiceDef":
        return cls(
            id=data["id"],
            text=data["text"],
            effects=list(data.get("effects", [])),
            require=dict(data.get("require", {})),
            hint=data.get("hint", ""),
        )


# 历练修为：外出探查的修为收益与每日上限（防刷）
EXPLORE_EXP_RATIO = 0.03          # 基础 = 本层需求 × 3%
EXPLORE_EXP_RATIO_MAX = 0.06      # 稀有机缘上限 6%
EXPLORE_DAILY_LIMIT = 5           # 每日最多 5 次有效历练


@dataclass
class EventDef:
    id: str
    title: str
    text: str
    weight: float = 10.0
    conditions: dict[str, Any] = field(default_factory=dict)
    choices: list[ChoiceDef] = field(default_factory=list)
    once: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EventDef":
        return cls(
            id=data["id"],
            title=data["title"],
            text=data.get("text", ""),
            weight=float(data.get("weight", 10.0)),
            conditions=dict(data.get("conditions", {})),
            choices=[ChoiceDef.from_dict(c) for c in data.get("choices", [])],
            once=bool(data.get("once", False)),
        )


class EventSystem(GameSystem):
    id = "event"
    name = "机缘事件"

    def __init__(self, event_file: Path | None = None) -> None:
        super().__init__()
        self.event_file = Path(event_file) if event_file else DEFAULT_EVENT_FILE
        self.events: list[EventDef] = []
        self.pending: EventDef | None = None      # 等待玩家选择的事件
        self.seen: dict[str, int] = {}            # 事件触发次数

    # ---------- 加载 ----------
    def load_events(self) -> None:
        if not self.event_file.exists():
            self.events = []
            return
        raw = json.loads(self.event_file.read_text(encoding="utf-8"))
        self.events = [EventDef.from_dict(e) for e in raw.get("events", [])]

    def on_bind(self) -> None:
        if not self.events:
            self.load_events()

    # ---------- 条件判定 ----------
    def check_conditions(self, cond: dict[str, Any], scene: str | None = None) -> bool:
        if not cond:
            return True
        p = self.game.player
        game = self.game

        if "where" in cond and cond["where"] != (scene or SCENE_OUTDOOR):
            return False

        if not RealmRegistry.within(
            p.realm_key, cond.get("min_realm"), cond.get("max_realm")
        ):
            return False

        for key, minimum in (cond.get("min_attr") or {}).items():
            if p.attributes.value(key) < minimum:
                return False

        if "location" in cond:
            locs = cond["location"]
            locs = locs if isinstance(locs, list) else [locs]
            if game.location not in locs:
                return False

        for key, val in (cond.get("flags") or {}).items():
            if p.flags.get(key) != val:
                return False
        for key, val in (cond.get("not_flags") or {}).items():
            if p.flags.get(key) == val:
                return False

        if "max_hp_ratio" in cond and p.hp / max(1.0, p.max_hp) > cond["max_hp_ratio"]:
            return False
        return True

    def check_require(self, require: dict[str, Any]) -> tuple[bool, str]:
        """选项前置条件，返回 (是否满足, 原因)。"""
        if not require:
            return True, ""
        p = self.game.player

        if "item" in require:
            item_id = require["item"]["id"]
            count = int(require["item"].get("count", 1))
            if not p.inventory.has(item_id, count):
                from ..config import items as item_config
                return False, f"需 {item_config.get_item(item_id).name} ×{count}"

        if "stone" in require and p.spirit_stones < require["stone"]:
            return False, f"需灵石 {require['stone']}"

        if "stamina" in require and p.stamina < require["stamina"]:
            return False, f"需精力 {require['stamina']}"

        if "mp" in require and p.mp < require["mp"]:
            return False, f"需灵力 {require['mp']}"

        if "hp" in require and p.hp < require["hp"]:
            return False, f"需气血 {require['hp']}"

        if "min_realm" in require and not RealmRegistry.within(
            p.realm_key, min_realm=require["min_realm"]
        ):
            return False, "修为不足"

        for key, minimum in (require.get("min_attr") or {}).items():
            if p.attributes.value(key) < minimum:
                return False, "资质不足"
        return True, ""

    # ---------- 触发与结算 ----------
    def available_events(self, scene: str | None = None) -> list[EventDef]:
        out = []
        for e in self.events:
            if e.once and self.seen.get(e.id):
                continue
            if self.check_conditions(e.conditions, scene):
                out.append(e)
        return out

    def roll(self, force: bool = False, scene: str | None = None) -> EventDef | None:
        """按地点事件率决定是否遭遇机缘。scene 为场景标签，用于隔离事件池。

        v2 探索疲惫：连续探索 EXPLORE_TIRED_THRESHOLD 次后机缘率减半，
        休息 EXPLORE_TIRED_REST_HOURS 时辰可清零（防反复 explore 刷机缘）。
        """
        if not force:
            rate = self.game.location_info()["event_rate"]
            streak = int(self.game.player.flags.get("explore_tired", 0))
            if streak >= EXPLORE_TIRED_THRESHOLD:
                rate *= 0.5                  # 疲惫：机缘率减半
            if not self.game.rng.chance(rate):
                return None
        pool = self.available_events(scene)
        return self.game.rng.weighted_choice(pool)

    def trigger(self, force: bool = False, scene: str | None = None) -> list[str]:
        """探索入口：消耗精力 -> 时间推进 -> 触发事件。"""
        p = self.game.player
        if not p.alive:
            return ["你已身死道消。"]
        if self.pending:
            return ["尚有未决之事，先做个了断。（choose N）"]
        if not p.spend_stamina(10):
            return ["精力不济，难以外出。（rest 6）"]

        logs = ["你踏出洞府，四下探查……"]
        event = self.roll(force=force, scene=scene)
        # 日常追踪：探索（v2 日常之一）
        daily = self.game.systems.get("daily")
        if daily is not None:
            daily.track("explore")
        # v2 探索疲惫：每次探索计数 +1（force 也计入，防刷）
        streak = int(p.flags.get(EXPLORE_TIRED_FLAG, 0)) + 1
        p.flags[EXPLORE_TIRED_FLAG] = streak
        if event is None:
            logs.extend(self.game.advance_time(3))
            tired = "（连番探查，心神已疲——休息 4 时辰可缓）" if streak >= EXPLORE_TIRED_THRESHOLD else ""
            logs.append("四处转了转，一无所获。" + tired)
            return logs

        self.seen[event.id] = self.seen.get(event.id, 0) + 1
        self.pending = event
        self._grant_training_exp(event)
        logs.extend(self.game.advance_time(2))
        logs.append(f"【{event.title}】{event.text}")
        logs.extend(self._choice_lines(event))
        return logs

    def _grant_training_exp(self, event: EventDef) -> None:
        """历练修为：外出体悟本身就是一种修行，按本层需求比例给，且有每日上限。

        防刷：探索已消耗精力（20/次），再叠加每日上限，双闸防「无限探查刷修为」。
        """
        from .effects import apply_effects

        p = self.player
        left = p.daily_left(self.game.day, "explore", EXPLORE_DAILY_LIMIT)
        if left <= 0:
            return
        p.bump_daily(self.game.day, "explore")
        # 稀有机缘体悟更深：按事件稀有度（若配置）在 [3%, 6%] 之间浮动
        rarity = float(getattr(event, "rarity", 1.0) or 1.0)
        ratio = min(EXPLORE_EXP_RATIO_MAX, EXPLORE_EXP_RATIO * rarity)
        apply_effects(self.game, [{"type": "exp_ratio", "value": ratio}])

    def _choice_lines(self, event: EventDef) -> list[str]:
        lines = []
        for i, c in enumerate(event.choices, 1):
            ok, reason = self.check_require(c.require)
            suffix = "" if ok else f"（{reason or '不可选'}）"
            lines.append(f"  {i}. {c.text}{suffix}")
        if not event.choices:
            lines.append("  （无可选项，choose 0 继续）")
        return lines

    def show_pending(self) -> list[str]:
        if not self.pending:
            return ["当前没有待决事件。"]
        return [f"【{self.pending.title}】{self.pending.text}",
                *self._choice_lines(self.pending)]

    def choose(self, index: int) -> list[str]:
        """玩家选择第 index 个选项（1-based，0 表示跳过无选项事件）。"""
        event = self.pending
        if event is None:
            return ["当前没有待决事件。"]

        if index == 0:
            self.pending = None
            return ["你转身离去。"]

        if index < 1 or index > len(event.choices):
            return [f"请输入 1 ~ {len(event.choices)} 之间的序号。"]

        choice = event.choices[index - 1]
        ok, reason = self.check_require(choice.require)
        if not ok:
            return [f"无法选择：{reason}"]

        self.pending = None
        logs = [f"> {choice.text}"]
        logs.extend(apply_effects(self.game, choice.effects))
        self.game.bus.emit(
            TOPIC_EVENT_RESOLVED, {"event_id": event.id, "choice_id": choice.id}
        )
        if not self.game.player.alive:
            logs.append("你眼前一黑，道消身陨。")
        return logs

    # ---------- 命令 ----------
    def commands(self) -> list[Command]:
        def _explore(args: list[str]) -> None:
            self.game.emit_logs(self.trigger(force="force" in args))

        def _choose(args: list[str]) -> None:
            if not args:
                self.game.emit_logs(self.show_pending())
                return
            self.game.emit_logs(self.choose(int(args[0])))

        return [
            Command("explore", "外出探查，触发机缘", "explore [force]", _explore),
            Command("choose", "对当前事件做出选择", "choose N", _choose),
        ]

    # ---------- 持久化 ----------
    def to_dict(self) -> dict[str, Any]:
        return {
            "seen": dict(self.seen),
            "pending_id": self.pending.id if self.pending else None,
        }

    def load_state(self, data: dict[str, Any]) -> None:
        self.seen = dict(data.get("seen", {}))
        if not self.events:
            self.load_events()
        pid = data.get("pending_id")
        if pid:
            self.pending = next((e for e in self.events if e.id == pid), None)
