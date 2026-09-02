"""可插拔玩法模块的基类、事件总线与命令描述。

新增玩法（战斗 / 门派 / 渡劫 / 炼丹 / 秘境）的标准做法：
    1. 继承 GameSystem，实现 id / name
    2. 在 bind() 里用 self.game.bus.on(...) 订阅关心的时机
    3. 需要玩家输入就实现 commands() 返回命令
    4. 需要持久化就实现 to_dict() / load_state()
    5. 在 create_game() 的 systems 列表里加一行

内核完全不知道这些系统的存在，只负责按 topic 广播。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, ClassVar

if TYPE_CHECKING:
    from .game import Game


# ---------- 广播时机（topic）----------
TOPIC_NEW_GAME = "new_game"                  # payload: {}
TOPIC_LOADED = "loaded"                      # payload: {}
TOPIC_BEFORE_BREAKTHROUGH = "before_breakthrough"
#   payload: {"target_realm": str, "is_major": bool, "bonus": float,
#             "logs": list[str], "blocked": bool, "block_reason": str}
TOPIC_AFTER_BREAKTHROUGH = "after_breakthrough"
#   payload: {"success": bool, "target_realm": str, "is_major": bool}
TOPIC_DAY_END = "day_end"                    # payload: {"day": int}
TOPIC_HOUR_PASSED = "hour_passed"            # payload: {"hours": float}
TOPIC_PRACTICE = "practice"                  # 打坐练功时广播，payload: {"hours": float}
#   与 hour_passed 的区别：hour_passed 任何时间流逝都触发（赶路、休息、战斗），
#   practice 只在玩家主动吐纳时触发，功法熟练度挂在这上面才合理。
TOPIC_EVENT_RESOLVED = "event_resolved"      # payload: {"event_id": str, "choice_id": str}
TOPIC_COMBAT_VICTORY = "combat_victory"      # payload: {"enemy": str, "exp": float, "danger": float}


class EventBus:
    """极简发布订阅：handler 直接就地修改 payload，达到「拦截器」效果。"""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[[dict[str, Any]], None]]] = {}

    def on(self, topic: str, handler: Callable[[dict[str, Any]], None]) -> None:
        self._handlers.setdefault(topic, []).append(handler)

    def emit(self, topic: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = payload if payload is not None else {}
        for handler in self._handlers.get(topic, []):
            handler(data)
        return data


@dataclass
class Command:
    """CLI / Web 都能消费的命令描述。"""

    name: str
    desc: str
    usage: str = ""
    handler: Callable[[list[str]], None] | None = None


class GameSystem:
    """所有玩法模块的父类。"""

    id: ClassVar[str] = ""
    name: ClassVar[str] = ""

    def __init__(self) -> None:
        self.game: "Game | None" = None

    # ---------- 生命周期 ----------
    def bind(self, game: "Game") -> None:
        self.game = game
        self.on_bind()

    def on_bind(self) -> None:
        """在此订阅事件总线，子类按需重写。"""

    def commands(self) -> list[Command]:
        return []

    # ---------- 持久化 ----------
    def to_dict(self) -> dict[str, Any]:
        return {}

    def load_state(self, data: dict[str, Any]) -> None:
        """默认忽略，子类按需重写。"""

    # ---------- 便捷属性 ----------
    @property
    def player(self):
        assert self.game is not None
        return self.game.player

    def log(self, text: str) -> None:
        if self.game:
            self.game.log(text)
