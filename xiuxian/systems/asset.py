"""产业系统（「财」）：坐地生财，以财养道。

规则：
    1. 购置 —— 花灵石置办产业，需满足境界门槛
    2. 升级 —— 投资提升等级（线性成长，非复利），产出与维护同步增长
    3. 结算 —— 每日结算：先扣维护，再发产出；灵石不够维护则该产业停摆（不派发）
    4. 产出 —— 灵石直接入袋，材料入背包（供炼丹/坊市变现，形成「财 → 丹 → 修为」的循环）
"""

from __future__ import annotations

from typing import Any

from ..config import assets as asset_config
from ..config import items as item_config
from ..config.realms import RealmRegistry
from ..core.base_system import TOPIC_DAY_END, Command, GameSystem


class AssetSystem(GameSystem):
    id = "asset"
    name = "产业"

    def __init__(self) -> None:
        super().__init__()
        self.owned: dict[str, int] = {}    # asset_key -> 等级
        self.stalled: dict[str, bool] = {} # 是否因欠维护停摆
        self.last_payout_day: int = 0

    def on_bind(self) -> None:
        self.game.bus.on(TOPIC_DAY_END, self._on_day_end)

    # ---------- 产出计算 ----------
    def daily_stones(self, key: str) -> int:
        asset = asset_config.get_asset(key)
        level = self.owned.get(key, 1)
        return int(asset.stones * asset_config.level_scale(level, asset))

    def daily_items(self, key: str) -> list[tuple[str, int]]:
        asset = asset_config.get_asset(key)
        level = self.owned.get(key, 1)
        scale = asset_config.level_scale(level, asset)
        return [(iid, max(1, int(count * scale))) for iid, count in asset.items]

    def daily_upkeep(self) -> int:
        total = 0
        for key, level in self.owned.items():
            asset = asset_config.get_asset(key)
            total += int(asset.upkeep * asset_config.level_scale(level, asset))
        return total

    # ---------- 行为 ----------
    def buy(self, key: str) -> list[str]:
        p = self.player
        try:
            asset = asset_config.get_asset(key)
        except KeyError:
            return [f"并无「{key}」这般产业。（asset list 查看）"]
        if key in self.owned:
            return [f"{asset.name} 已在名下，asset upgrade {key} 可增资扩建。"]
        if not RealmRegistry.within(p.realm_key, min_realm=asset.min_realm):
            return [f"{asset.name} 需 {RealmRegistry.get(asset.min_realm).name} 以上方可经营。"]
        if p.spirit_stones < asset.price:
            return [f"灵石不足，{asset.name} 需 {asset.price}（现有 {p.spirit_stones}）。"]

        p.spirit_stones -= asset.price
        self.owned[key] = 1
        self.stalled[key] = False
        return [f"置办{asset.name}，日产灵石 {self.daily_stones(key)}、"
                f"{self._items_text(key)}。（每日维护共 {self.daily_upkeep()}）", f"　{asset.desc}"]

    def upgrade(self, key: str) -> list[str]:
        p = self.player
        if key not in self.owned:
            return [f"你名下并无此项产业。（asset list 查看）"]
        asset = asset_config.get_asset(key)
        level = self.owned[key]
        if level >= asset.max_level:
            return [f"{asset.name} 已至 {asset.max_level} 级，规模再大反难约束。"]
        cost = asset_config.upgrade_price(asset, level)
        if p.spirit_stones < cost:
            return [f"灵石不足，扩建需 {cost}（现有 {p.spirit_stones}）。"]

        p.spirit_stones -= cost
        self.owned[key] = level + 1
        return [f"{asset.name} 增资至 {level + 1} 级：日产灵石 {self.daily_stones(key)}、"
                f"{self._items_text(key)}。"]

    def sell(self, key: str) -> list[str]:
        if key not in self.owned:
            return ["你名下并无此项产业。"]
        asset = asset_config.get_asset(key)
        level = self.owned.pop(key)
        self.stalled.pop(key, None)
        refund = int(asset.price * 0.5 + asset_config.upgrade_price(asset, 1) * 0.3 * max(0, level - 1))
        self.player.spirit_stones += refund
        return [f"变卖{asset.name}（{level} 级），得灵石 {refund}。"]

    def _on_day_end(self, payload: dict) -> None:
        """每日结算（v2 自动维护）：先产出，维护费从产出中扣除，不足自动暂停而非荒废。"""
        if not self.owned:
            return
        day = int(payload.get("day", self.game.day))
        if day == self.last_payout_day:
            return
        self.last_payout_day = day

        p = self.player
        upkeep = self.daily_upkeep()
        stones_total = sum(self.daily_stones(key) for key in self.owned)
        # 维护费从产出中自动扣除：产出不足则暂停（无需玩家手动补缴，不会荒废）
        if stones_total < upkeep:
            for key in self.owned:
                self.stalled[key] = True
            self.log(f"产业产出不足维持（产出 {stones_total} < 维护 {upkeep}），"
                     f"各处分号今日自动歇业，产出灵石足够时自会恢复。")
            return

        net = stones_total - upkeep
        p.spirit_stones += net
        got: list[str] = []
        for key in self.owned:
            self.stalled[key] = False
            for iid, count in self.daily_items(key):
                p.inventory.add(iid, count)
                got.append(f"{item_config.get_item(iid).name}×{count}")
        line = f"产业结算：灵石 +{net}（产出 {stones_total}，自动维护 -{upkeep}）"
        if got:
            line += "，" + "、".join(got)
        self.log(line + "。")

    # ---------- 展示 ----------
    def _items_text(self, key: str) -> str:
        pairs = self.daily_items(key)
        return "、".join(f"{item_config.get_item(i).name}×{c}" for i, c in pairs) or "无"

    def info(self) -> list[str]:
        if not self.owned:
            return ["你名下并无产业，只靠历练糊口。（asset list 查看，asset buy <key> 置办）"]
        lines = [f"产业（{len(self.owned)} 处，每日维护合计 {self.daily_upkeep()} 灵石）："]
        for key, level in self.owned.items():
            asset = asset_config.get_asset(key)
            mark = "　【停摆】" if self.stalled.get(key) else ""
            lines.append(f"  {asset.name}（{level}/{asset.max_level} 级）{mark}")
            lines.append(f"　　　日产：灵石 {self.daily_stones(key)}、{self._items_text(key)}")
        lines.append(f"　预计每日净收：灵石 {sum(self.daily_stones(k) for k in self.owned) - self.daily_upkeep()}")
        return lines

    def catalog(self) -> list[str]:
        p = self.player
        lines = ["产业（asset buy <key>）："]
        for a in asset_config.ASSETS.values():
            if a.key in self.owned:
                state = f"★已置办（{self.owned[a.key]} 级）"
            elif not RealmRegistry.within(p.realm_key, min_realm=a.min_realm):
                state = f"境界不足（需{RealmRegistry.get(a.min_realm).name}）"
            elif p.spirit_stones < a.price:
                state = "灵石不足"
            else:
                state = "可置办"
            lines.append(f"  {a.name}　{a.price} 灵石　维护 {a.upkeep}/日　日产灵石 {a.stones}　[{state}]　{a.key}")
            lines.append(f"　　　{a.desc}")
            items = "、".join(f"{item_config.get_item(i).name}×{c}" for i, c in a.items) or "无"
            lines.append(f"　　　日产材料：{items}　满级 {a.max_level} 级")
        return lines

    # ---------- 命令 ----------
    def commands(self) -> list[Command]:
        def _asset(args: list[str]) -> None:
            sub = args[0].lower() if args else ""
            if sub in ("list", "目录"):
                self.game.emit_logs(self.catalog())
            elif sub in ("buy", "置办"):
                self.game.emit_logs(self.buy(args[1] if len(args) > 1 else ""))
            elif sub in ("upgrade", "增资"):
                self.game.emit_logs(self.upgrade(args[1] if len(args) > 1 else ""))
            elif sub in ("sell", "变卖"):
                self.game.emit_logs(self.sell(args[1] if len(args) > 1 else ""))
            else:
                self.game.emit_logs(self.info())
                self.log("  asset list 目录　asset buy <key>　asset upgrade <key>　asset sell <key>")

        return [Command("asset", "产业（财）", "asset [list|buy|upgrade|sell]", _asset)]

    # ---------- 持久化 ----------
    def to_dict(self) -> dict[str, Any]:
        return {
            "owned": dict(self.owned),
            "stalled": dict(self.stalled),
            "last_payout_day": self.last_payout_day,
        }

    def load_state(self, data: dict[str, Any]) -> None:
        self.owned = {k: int(v) for k, v in (data.get("owned") or {}).items()
                      if k in asset_config.ASSETS}
        self.stalled = {k: bool(v) for k, v in (data.get("stalled") or {}).items()
                        if k in self.owned}
        self.last_payout_day = int(data.get("last_payout_day", 0))
