"""背包与丹药使用。

丹毒（pill_poison）机制：嗑药会累积丹毒，丹毒越高药效越弱（见 effects._scale_by_poison），
逼迫玩家在「猛嗑丹药冲关」和「稳步打磨根基」之间做取舍。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..config import items as item_config

if TYPE_CHECKING:
    from .game import Game

DAILY_PILL_LIMIT = 8           # 每日服药上限（修为防刷）


class Inventory:
    """物品容器：{item_id: count}。"""

    def __init__(self, data: dict[str, int] | None = None) -> None:
        self.items: dict[str, int] = dict(data or {})

    # ---------- 增删查 ----------
    def add(self, item_id: str, count: int = 1) -> None:
        item_config.get_item(item_id)  # 校验存在
        self.items[item_id] = self.items.get(item_id, 0) + count

    def remove(self, item_id: str, count: int = 1) -> bool:
        if self.items.get(item_id, 0) < count:
            return False
        self.items[item_id] -= count
        if self.items[item_id] <= 0:
            del self.items[item_id]
        return True

    def count(self, item_id: str) -> int:
        return self.items.get(item_id, 0)

    def has(self, item_id: str, count: int = 1) -> bool:
        return self.count(item_id) >= count

    def all(self) -> list[tuple[str, int]]:
        return sorted(self.items.items())

    def is_empty(self) -> bool:
        return not self.items

    # ---------- 使用 ----------
    def use(self, game: "Game", item_id: str) -> list[str]:
        """服用/使用一件物品，返回日志。"""
        from .effects import apply_effects

        item = item_config.get_item(item_id)
        if not game.player.alive:
            return ["你已身死道消。"]
        if not self.has(item_id):
            return [f"你没有「{item.name}」"]
        if not item.usable:
            return [f"「{item.name}」不可直接使用"]
        # 丹药是修为途径之一，必须有每日上限：否则可以无限嗑药绕过精力预算
        # （丹毒只压药效、不压次数，光靠丹毒挡不住批量服用）
        if item.kind == "pill":
            used = game.player.daily_used(game.day, "pill")
            if used >= DAILY_PILL_LIMIT:
                return [f"今日服药已达 {DAILY_PILL_LIMIT} 次之数，再服恐伤根基。"
                        f"（丹毒亦会累积，明日再服）"]
            game.player.bump_daily(game.day, "pill")

        self.remove(item_id, 1)
        logs = [f"使用【{item.name}】"]
        logs.extend(apply_effects(game, list(item.effects)))
        if item.poison:
            game.player.pill_poison += item.poison
            logs.append(f"丹毒 +{item.poison:g}（当前 {game.player.pill_poison:.0f}）")
        return logs

    # ---------- 序列化 ----------
    def to_dict(self) -> dict[str, Any]:
        return dict(self.items)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Inventory":
        return cls({k: int(v) for k, v in (data or {}).items()})
