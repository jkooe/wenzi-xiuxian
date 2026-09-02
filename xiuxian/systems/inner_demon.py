"""心魔劫：跨大境界突破成功后的心境考验。

设计要点
--------
1. **触发**：订阅 after_breakthrough，跨大境界突破成功后 30% 概率触发（金丹起）。
2. **形式**：文字三选一 + 「随缘」——正道（小增益）/ 歧途（大增益+副作用）/
   超脱（看心境，随机好或坏）/ 随缘（随机抽取任一选项效果，无偏斜，不逼决策）。
3. **防刷**：自然触发不可主动刷；每次突破最多一次。
4. **后果走效果 DSL**：数据驱动，扩展只需加新题。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.base_system import (
    Command, GameSystem, TOPIC_AFTER_BREAKTHROUGH, TOPIC_COMBAT_VICTORY,
)
from ..core.effects import apply_effects

TRIGGER_CHANCE = 0.30        # 触发概率


@dataclass(frozen=True)
class DemonChoice:
    """一个选项：文本 + 效果 + 叙事。"""
    text: str                     # 选项文字
    outcome: str                  # 结果叙事
    effects: tuple[dict, ...] = ()  # 效果 DSL（走 apply_effects）
    random_effect: bool = False   # 随缘：随机抽取本试炼任一选项的效果（无偏斜）


@dataclass(frozen=True)
class DemonTrial:
    """一道心魔考验。"""
    id: str
    realm_min: str                # 最低触发境界
    text: str                     # 情境描述
    choices: tuple[DemonChoice, ...] = ()


# 心魔考验题库（修仙哲学：不同选择代表不同的道，无绝对对错）
TRIALS: tuple[DemonTrial, ...] = (
    DemonTrial(
        "power", "core",
        "心魔化作另一个「你」，伸出手指：「你修为今日之高，皆因杀了那个懦弱的自己。"
        "现在，只要你吞了我，你还能再进一步。」",
        choices=(
            DemonChoice("正道", "你摇头：「我不靠吞噬任何人。」心魔发出冷笑，却在你坚定的道心前如冰雪消融。",
                        ({"type": "attr", "key": "comprehension", "value": 1},)),
            DemonChoice("歧途", "你伸手抓住心魔，将其吞噬。一阵剧痛后，你感到体内涌动着陌生的力量——以及一丝挥之不去的寒意。",
                        ({"type": "attr", "key": "atk", "value": 5},
                         {"type": "poison", "value": 10})),
            DemonChoice("超脱", "你闭上眼：「你非我，我非你。你我皆过客。」当你再睁眼，心魔已无影无踪——但你记不清方才想到了什么。",
                        ({"type": "exp_ratio", "value": 0.05},)),
            DemonChoice("随缘", "你既不取正道，也不择歧途，只道：「天意如何，我便如何。」心魔随缘而散，留下什么全看天意。",
                        (), random_effect=True),
        ),
    ),
    DemonTrial(
        "attachment", "nascent",
        "心魔化作你此生挚爱之人，泪眼婆娑：「如果你继续修炼，我们就要分离了。"
        "留下来陪我，好不好？」",
        choices=(
            DemonChoice("正道", "你伸手替对方理了理鬓发：「正因为有你，我才能走得更远。」心魔化作的幻影在你掌中消散。",
                        ({"type": "attr", "key": "comprehension", "value": 1},)),
            DemonChoice("歧途", "你停下来。片刻之后，你发现幻影消失了——但你确实浪费了三年。",
                        ({"type": "attr", "key": "luck", "value": 2},
                         {"type": "exp_ratio", "value": -0.10})),
            DemonChoice("超脱", "你看着幻影，嘴角浮起一丝微笑：「我不留你，也不留我。」幻影消失时，你心中多了一分通透。",
                        ({"type": "exp_ratio", "value": 0.08},)),
            DemonChoice("随缘", "你握住幻影的手，闭目不语。缘来缘去，皆由天定。",
                        (), random_effect=True),
        ),
    ),
    DemonTrial(
        "fear", "void",
        "心魔不再说话。它只是让你看到了一幅画面：数百年后，你的道侣已化作黄土，"
        "而你依然年轻——独自坐在洞府中，面对无尽的岁月。",
        choices=(
            DemonChoice("正道", "你看了很久，然后起身继续打坐。「正因岁月无尽，才更要走下去。」",
                        ({"type": "attr", "key": "comprehension", "value": 2},)),
            DemonChoice("歧途", "你将这幅画面撕碎：「我命由我不由天。」心魔第一次露出了恐惧的神色——然后消散。",
                        ({"type": "attr", "key": "atk", "value": 3},
                         {"type": "attr", "key": "luck", "value": -1})),
            DemonChoice("超脱", "你笑着说：「那就多收几个道侣。」心魔愣住了——然后也不由自主地笑了。",
                        ({"type": "exp_ratio", "value": 0.06},
                         {"type": "attr", "key": "luck", "value": 1})),
            DemonChoice("随缘", "你坦然看着那幅画面，任由岁月如水流过心间。得之我幸，失之我命。",
                        (), random_effect=True),
        ),
    ),
)


class InnerDemonSystem(GameSystem):
    id = "inner_demon"
    name = "心魔劫"

    def __init__(self) -> None:
        super().__init__()
        self.pending: dict[str, Any] | None = None    # 当前待抉择的心魔
        self.faced_count: int = 0

    def on_bind(self) -> None:
        self.game.bus.on(TOPIC_AFTER_BREAKTHROUGH, self._on_breakthrough)

    def _on_breakthrough(self, payload: dict) -> None:
        import random
        if not payload.get("success") or not payload.get("is_major"):
            return
        p = self.player
        if not RealmRegistry_min_core(p.realm_key):
            return
        if self.pending is not None:
            return                                     # 已有待决心魔
        if not self.game.rng.chance(TRIGGER_CHANCE):
            return

        trial = self._roll_trial(p.realm_key)
        if trial is None:
            return
        self.pending = {"id": trial.id, "realm": p.realm_key}
        self.log("　※ 心魔劫 ※")
        self.log(f"　{trial.text}")
        for i, c in enumerate(trial.choices, 1):
            self.log(f"　　{i}. {c.text}")

    def _roll_trial(self, realm_key: str):
        import random
        from ..config.realms import RealmRegistry
        idx = RealmRegistry.index_of(realm_key)
        candidates = [t for t in TRIALS
                      if RealmRegistry.index_of(t.realm_min) <= idx]
        if not candidates:
            return None
        self.game.rng.shuffle(candidates)
        return candidates[0]

    def choose(self, index: int) -> list[str]:
        if self.pending is None:
            return ["并无心魔当前。"]
        trial = next(t for t in TRIALS if t.id == self.pending["id"])
        if index < 1 or index > len(trial.choices):
            return [f"请选择 1~{len(trial.choices)}（choose 0 亦可退却）。"]
        choice = trial.choices[index - 1]
        self.pending = None
        self.faced_count += 1

        logs = [f"　你选择了：「{choice.text}」"]
        logs.append(f"　{choice.outcome}")
        # 随缘：随机抽取任一选项的效果（含自身），无偏斜——不逼玩家决策
        if choice.random_effect:
            pool = [c.effects for c in trial.choices if c.effects]
            effects = list(self.game.rng.choice(pool)) if pool else []
            logs.append("　天数难测，祸福相依——一切随缘。")
        else:
            effects = list(choice.effects)
        logs.extend(apply_effects(self.game, effects))
        self.game.rebuild_bonuses()
        return logs

    def commands(self) -> list[Command]:
        def _demon(args: list[str]) -> None:
            if self.pending is None:
                self.log("心中一片澄明，并无心魔当前。")
                return
            idx = int(args[0]) if args and args[0].isdigit() else 0
            self.game.emit_logs(self.choose(idx))

        return [Command("demon", "心魔劫（突破后概率触发）", "demon <选项>", _demon)]

    # ---------- 持久化 ----------
    def to_dict(self) -> dict[str, Any]:
        return {"pending": self.pending, "faced_count": self.faced_count}

    def load_state(self, data: dict[str, Any]) -> None:
        self.pending = data.get("pending")
        self.faced_count = int(data.get("faced_count", 0))


def RealmRegistry_min_core(realm_key: str) -> bool:
    """金丹及以上才可能触发心魔劫。"""
    from ..config.realms import RealmRegistry
    return RealmRegistry.within(realm_key, min_realm="core")
