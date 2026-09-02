"""任务与声望系统（数据驱动，写在 data/quests.json）。

设计要点：
    1. 任务把已有的行为（斗法 / 机缘 / 突破）转化为可交付目标——
       订阅 combat_victory / event_resolved / after_breakthrough 计数，
       不另写结算逻辑，全部复用 core/effects.py 的效果 DSL 发奖励。
    2. 达成即自动领赏：目标满足的瞬间立刻发放奖励并累加声望，
       玩家不需要手动「交付」，契合文字修仙「挂机养成」的节奏。
    3. 声望是门派维度的长久数值：完成某门派任务累加其声望，
       高阶任务可用 min_rep 设门槛（见 q_sect_honor），形成「刷声望 → 解锁」的循环。
    4. 可重复任务完成后立即续接（进度清空），构成日常委托；
       一次性任务完成后永久消失。

track 与总线主题的对应（实现上最容易想错的地方）：
    combat       -> TOPIC_COMBAT_VICTORY     payload 含 tier，可按难度档过滤
    event        -> TOPIC_EVENT_RESOLVED     payload 含 event_id，可按具体事件过滤
    breakthrough  -> TOPIC_AFTER_BREAKTHROUGH payload 含 success / is_major
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import items as item_config
from ..config.realms import RealmRegistry
from ..core.attributes import ATTR_LABELS
from ..core.base_system import (
    Command,
    GameSystem,
    TOPIC_AFTER_BREAKTHROUGH,
    TOPIC_COMBAT_VICTORY,
    TOPIC_EVENT_RESOLVED,
)
from ..core.effects import apply_effects

DEFAULT_QUEST_FILE = Path(__file__).resolve().parents[2] / "data" / "quests.json"


@dataclass
class ObjectiveDef:
    track: str                       # combat / event / breakthrough
    target: int                     # 需要达成次数
    label: str = ""                 # 展示用短名（如「斩妖」）
    match: dict[str, Any] = field(default_factory=dict)   # 过滤条件

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ObjectiveDef":
        return cls(
            track=d["track"],
            target=int(d["target"]),
            label=d.get("label", ""),
            match=dict(d.get("match", {})),
        )


@dataclass
class QuestDef:
    id: str
    name: str
    faction: str
    desc: str
    objectives: list[ObjectiveDef]
    reward: list[dict[str, Any]]
    rep: int = 0
    once: bool = False
    min_realm: str = ""
    min_rep: dict[str, int] = field(default_factory=dict)   # {门派: 所需声望}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "QuestDef":
        return cls(
            id=d["id"],
            name=d["name"],
            faction=d.get("faction", "散修"),
            desc=d.get("desc", ""),
            objectives=[ObjectiveDef.from_dict(o) for o in d.get("objectives", [])],
            reward=list(d.get("reward", [])),
            rep=int(d.get("rep", 0)),
            once=bool(d.get("once", False)),
            min_realm=d.get("min_realm", ""),
            min_rep=dict(d.get("min_rep", {})),
        )


class QuestSystem(GameSystem):
    id = "quest"
    name = "任务"

    def __init__(self, quest_file: Path | None = None) -> None:
        super().__init__()
        self.quest_file = Path(quest_file) if quest_file else DEFAULT_QUEST_FILE
        self.defs: dict[str, QuestDef] = {}
        self.accepted: dict[str, dict[str, Any]] = {}   # id -> {progress: [int], done: bool}
        self.completed_once: set[str] = set()
        self.reputation: dict[str, int] = {}

    # ---------- 加载 ----------
    def load_quests(self) -> None:
        if not self.quest_file.exists():
            self.defs = {}
            return
        raw = json.loads(self.quest_file.read_text(encoding="utf-8"))
        self.defs = {q["id"]: QuestDef.from_dict(q) for q in raw.get("quests", [])}

    def on_bind(self) -> None:
        if not self.defs:
            self.load_quests()
        self.game.bus.on(TOPIC_COMBAT_VICTORY, self._on_combat)
        self.game.bus.on(TOPIC_EVENT_RESOLVED, self._on_event)
        self.game.bus.on(TOPIC_AFTER_BREAKTHROUGH, self._on_breakthrough)
        self._auto_accept()

    # ---------- 可用性 ----------
    def _rep_ok(self, q: QuestDef) -> bool:
        return all(self.reputation.get(fac, 0) >= need for fac, need in q.min_rep.items())

    def is_available(self, q: QuestDef) -> bool:
        p = self.player
        if q.min_realm and not RealmRegistry.within(p.realm_key, min_realm=q.min_realm):
            return False
        if q.once and q.id in self.completed_once:
            return False
        if q.id in self.accepted:
            return False
        return self._rep_ok(q)

    def available(self) -> list[QuestDef]:
        return [q for q in self.defs.values() if self.is_available(q)]

    def _auto_accept(self) -> None:
        for q in self.available():
            self._accept(q.id)

    def _accept(self, qid: str) -> bool:
        q = self.defs.get(qid)
        if q is None or not self.is_available(q):
            return False
        self.accepted[qid] = {"progress": [0] * len(q.objectives), "done": False}
        return True

    # ---------- 计数 ----------
    def _match_objective(self, q: QuestDef, oi: int, payload: dict[str, Any]) -> bool:
        o = q.objectives[oi]
        m = o.match
        if o.track == "combat":
            tier = payload.get("tier", "normal")
            if "min_tier" in m:
                from .combat import TIERS
                if TIERS.get(tier, 1.0) < TIERS.get(m["min_tier"], 1.0):
                    return False
            if "tier" in m and tier != m["tier"]:
                return False
            return True
        if o.track == "event":
            if "event_id" in m and payload.get("event_id") != m["event_id"]:
                return False
            return True
        if o.track == "breakthrough":
            if not payload.get("success"):
                return False
            if m.get("major") and not payload.get("is_major"):
                return False
            return True
        return False

    def _on_combat(self, payload: dict[str, Any]) -> None:
        self._count("combat", payload)

    def _on_event(self, payload: dict[str, Any]) -> None:
        self._count("event", payload)

    def _on_breakthrough(self, payload: dict[str, Any]) -> None:
        self._count("breakthrough", payload)

    def _count(self, track: str, payload: dict[str, Any]) -> None:
        # 用 list() 拷贝键，允许 _complete 中途续接/移除任务
        for qid, st in list(self.accepted.items()):
            if st["done"]:
                continue
            q = self.defs[qid]
            changed = False
            for oi, o in enumerate(q.objectives):
                if o.track == track and st["progress"][oi] < o.target:
                    if self._match_objective(q, oi, payload):
                        st["progress"][oi] += 1
                        changed = True
            if changed:
                self._check_complete(qid)

    def _check_complete(self, qid: str) -> None:
        q = self.defs[qid]
        st = self.accepted.get(qid)
        if st is None or st["done"]:
            return
        if all(st["progress"][oi] >= q.objectives[oi].target for oi in range(len(q.objectives))):
            self._complete(qid)

    def _complete(self, qid: str) -> None:
        q = self.defs[qid]
        st = self.accepted[qid]
        st["done"] = True
        logs = [f"【任务达成】{q.name}：{q.desc}"]
        gained = apply_effects(self.game, list(q.reward))
        logs.extend("　" + g for g in gained)
        if q.rep:
            self.reputation[q.faction] = self.reputation.get(q.faction, 0) + q.rep
            logs.append(f"　{q.faction}声望 +{q.rep}（当前 {self.reputation[q.faction]}）")
        self.log("\n".join(logs))

        if q.once:
            self.completed_once.add(qid)
            self.accepted.pop(qid, None)
        else:
            # 可重复任务：完成后立刻续接，清空进度，形成日常委托循环
            self.accepted.pop(qid, None)
            self._accept(qid)

        # 声望提升可能解锁更高阶任务（如 q_sect_honor 的 min_rep），
        # 顺手把新可用的任务接进来，不必等下次读档
        self._auto_accept()

    # ---------- 展示 ----------
    def _reward_text(self, q: QuestDef) -> str:
        parts: list[str] = []
        for eff in q.reward:
            t = eff.get("type")
            if t == "stone":
                parts.append(f"灵石 {int(eff.get('value', 0)):+d}")
            elif t == "exp":
                parts.append(f"修为 {int(eff.get('value', 0)):+d}")
            elif t == "exp_ratio":
                parts.append(f"修为 +{float(eff.get('value', 0)) * 100:.0f}%")
            elif t == "item":
                name = item_config.get_item(eff["id"]).name
                parts.append(f"{name} ×{int(eff.get('count', 1))}")
            elif t == "attr":
                parts.append(f"{ATTR_LABELS.get(eff.get('key', ''), eff.get('key', ''))} "
                             f"+{eff.get('value', 0):g}")
            else:
                parts.append(t)
        return "、".join(parts) if parts else "（无）"

    def overview(self) -> list[str]:
        lines = ["任务（达成自动领赏，按门派累加声望）："]
        if self.accepted:
            for qid, st in self.accepted.items():
                q = self.defs[qid]
                bits = []
                for oi, o in enumerate(q.objectives):
                    label = o.label or o.track
                    bits.append(f"{label} {st['progress'][oi]}/{o.target}")
                lines.append(f"  ▶ {q.name}（{q.faction}）　{'　'.join(bits)}")
                lines.append(f"      奖励：{self._reward_text(q)}")
        else:
            lines.append("  暂无可接任务。")
        reps = {f: r for f, r in self.reputation.items() if r}
        if reps:
            lines.append("  声望：" + "、".join(f"{f} {r}" for f, r in reps.items()))
        done = [self.defs[q].name for q in self.completed_once if q in self.defs]
        if done:
            lines.append("  已完成（一次性）：" + "、".join(done))
        return lines

    # ---------- 命令 ----------
    def commands(self) -> list[Command]:
        def _quest(args: list[str]) -> None:
            self.game.emit_logs(self.overview())

        return [
            Command("quest", "任务与声望（自动追踪，达成即领赏）", "quest [list]", _quest),
        ]

    # ---------- 持久化 ----------
    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": {
                qid: {"progress": list(st["progress"]), "done": st["done"]}
                for qid, st in self.accepted.items()
            },
            "completed_once": sorted(self.completed_once),
            "reputation": dict(self.reputation),
        }

    def load_state(self, data: dict[str, Any]) -> None:
        if not self.defs:
            self.load_quests()
        self.accepted = {}
        for qid, st in (data.get("accepted") or {}).items():
            if qid in self.defs:
                prog = st.get("progress", [])
                self.accepted[qid] = {"progress": list(prog), "done": bool(st.get("done", False))}
        self.completed_once = set(data.get("completed_once", []))
        self.reputation = dict(data.get("reputation", {}))
        self._auto_accept()     # 续接可重复 / 新解锁的任务
