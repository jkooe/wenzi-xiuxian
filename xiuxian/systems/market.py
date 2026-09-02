"""坊市系统：让灵石有去处。

两条规则：
    1. 买 —— 按当前浮动价购入物品（丹药 / 材料 / 装备 / 天材地宝），是后期灵石的消费出口
    2. 卖 —— 把储物袋里的物品按浮动价的折扣售出，妖丹、灵草等产出的回收渠道

价格浮动是为了让「每天刷同一个秘境」不至于把灵石堆成死数：
    每日 ±DRIFT 漂移，并夹在基准价的 [DRIFT_LO, DRIFT_HI] 倍之间，避免无限通胀或崩盘。
买卖本身不耗时辰（市集交易即时完成）。
"""

from __future__ import annotations

from typing import Any

from ..config import items as item_config
from ..core.base_system import Command, GameSystem, TOPIC_DAY_END

PRICE_DRIFT = 0.15          # 每日 ±15% 浮动
SELL_RATIO = 0.6            # 售出价为当前买入价的 60%
DRIFT_LO, DRIFT_HI = 0.5, 1.8

# v2 防套利：每日买入/卖出各限 3 次 + 交易影响价格（大量买推高、大量卖压低）
DAILY_BUY_LIMIT = 3
DAILY_SELL_LIMIT = 3
PRICE_PER_TRADE = 0.02      # 每笔交易使价格朝交易方向偏移 2%（夹在 [0.5, 1.8] 内）

# 物品按 kind 分栏展示
_KIND_GROUPS: dict[str, str] = {
    "pill": "丹药",
    "material": "材料",
    "treasure": "天材地宝",
    "equip": "装备",
}


class MarketSystem(GameSystem):
    id = "market"
    name = "坊市"

    def __init__(self) -> None:
        self.prices: dict[str, float] = {}      # item_id -> 当前买入价
        self.last_day: int = 0

    # ---------- 装配 ----------
    def on_bind(self) -> None:
        self.game.bus.on(TOPIC_DAY_END, self._on_day_end)
        self._init_prices()

    def _init_prices(self) -> None:
        for iid, item in item_config.ITEMS.items():
            if item.price > 0:
                self.prices[iid] = float(item.price)

    # ---------- 每日刷新 ----------
    def _on_day_end(self, payload: dict[str, Any]) -> None:
        self.last_day = int(payload.get("day", self.last_day))
        rng = self.game.rng
        for iid, item in item_config.ITEMS.items():
            if item.price <= 0:
                continue
            cur = self.prices.get(iid, float(item.price))
            drift = rng.between(-PRICE_DRIFT, PRICE_DRIFT)
            new = cur * (1.0 + drift)
            # 夹在基准价的 [0.5, 1.8] 倍之间，防止偏离太远
            lo, hi = item.price * DRIFT_LO, item.price * DRIFT_HI
            self.prices[iid] = round(max(lo, min(hi, new)), 1)

    # ---------- 价格查询 ----------
    def buy_price(self, item_id: str) -> float:
        if item_id in self.prices:
            return self.prices[item_id]
        return float(item_config.get_item(item_id).price)

    def sell_price(self, item_id: str) -> float:
        return round(self.buy_price(item_id) * SELL_RATIO)

    # ---------- 交易 ----------
    def buy(self, item_id: str, count: int = 1) -> list[str]:
        p = self.player
        if not p.alive:
            return ["你已身死道消。"]
        try:
            item = item_config.get_item(item_id)
        except KeyError:
            return [f"世间并无「{item_id}」此物。"]
        if item.price <= 0:
            return [f"坊市并不售卖「{item.name}」——此乃无价之物。"]
        count = max(1, int(count))
        # v2 防套利：每日买入限 3 次
        left = p.daily_left(self.game.day, "market_buy", DAILY_BUY_LIMIT)
        if left < count:
            return [f"今日坊市购入已近上限（每日 {DAILY_BUY_LIMIT} 次，今日还可 {max(0, left)} 次）。"
                    f"（防囤积倒卖，明日再来）"]
        total = int(self.buy_price(item_id) * count)
        if p.spirit_stones < total:
            return [f"灵石不足，购 {item.name} ×{count} 需 {total}，你仅有 {p.spirit_stones}。"]
        p.spirit_stones -= total
        p.inventory.add(item_id, count)
        p.bump_daily(self.game.day, "market_buy", count)
        # v2 价格反馈：大量买入推高价格（倒买倒卖无利可图）
        for _ in range(count):
            cur = self.prices.get(item_id, float(item.price))
            new = cur * (1.0 + PRICE_PER_TRADE)
            lo, hi = item.price * DRIFT_LO, item.price * DRIFT_HI
            self.prices[item_id] = round(max(lo, min(hi, new)), 1)
        return [f"于坊市购入 {item.name} ×{count}（单价 {self.buy_price(item_id):.0f}），"
                f"耗灵石 {total}，余 {p.spirit_stones}。货价因你而动，略有上浮。"]

    def sell(self, item_id: str, count: int = 1) -> list[str]:
        p = self.player
        if not p.alive:
            return ["你已身死道消。"]
        try:
            item = item_config.get_item(item_id)
        except KeyError:
            return [f"世间并无「{item_id}」此物。"]
        count = max(1, int(count))
        # v2 防套利：每日卖出限 3 次
        left = p.daily_left(self.game.day, "market_sell", DAILY_SELL_LIMIT)
        if left < count:
            return [f"今日坊市卖出已近上限（每日 {DAILY_SELL_LIMIT} 次，今日还可 {max(0, left)} 次）。"
                    f"（防囤积倒卖，明日再来）"]
        if not p.inventory.has(item_id, count):
            return [f"储物袋中并无 {item.name} ×{count}。"]
        gained = int(self.sell_price(item_id) * count)
        p.inventory.remove(item_id, count)
        p.spirit_stones += gained
        p.bump_daily(self.game.day, "market_sell", count)
        # v2 价格反馈：大量卖出压低价格
        for _ in range(count):
            cur = self.prices.get(item_id, float(item.price))
            new = cur * (1.0 - PRICE_PER_TRADE)
            lo, hi = item.price * DRIFT_LO, item.price * DRIFT_HI
            self.prices[item_id] = round(max(lo, min(hi, new)), 1)
        return [f"于坊市售出 {item.name} ×{count}（单价 {self.sell_price(item_id):.0f}），"
                f"得灵石 {gained}，余 {p.spirit_stones}。货价因你而动，略有下探。"]

    # ---------- 展示 ----------
    def catalog(self, kind: str | None = None) -> list[str]:
        lines = ["坊市货品（market buy <id> [数量] 购入，market sell <id> [数量] 售出）："]
        for k, label in _KIND_GROUPS.items():
            if kind and kind != k:
                continue
            items = [(iid, it) for iid, it in item_config.ITEMS.items()
                     if it.kind == k and it.price > 0]
            if not items:
                continue
            lines.append(f"  ▌{label}")
            for iid, it in items:
                bp = self.buy_price(iid)
                sp = self.sell_price(iid)
                lines.append(f"    {it.name}（{iid}）　买 {bp:.0f}　卖 {sp:.0f}　{it.desc}")
        return lines

    def sellable(self) -> list[str]:
        p = self.player
        lines = ["袋中可售出之物："]
        any_ = False
        for iid, count in p.inventory.all():
            try:
                it = item_config.get_item(iid)
            except KeyError:
                continue
            if it.price <= 0:
                continue
            any_ = True
            lines.append(f"    {it.name} ×{count}（{iid}）　卖价 {self.sell_price(iid):.0f}")
        if not any_:
            lines.append("    袋中并无可售之物。")
        return lines

    # ---------- 命令 ----------
    def commands(self) -> list[Command]:
        def _market(args: list[str]) -> None:
            sub = (args[0].lower() if args else "")
            if sub in ("sell", "卖"):
                if len(args) < 2:
                    self.game.emit_logs(self.sellable())
                    return
                cnt = int(args[2]) if len(args) > 2 else 1
                self.game.emit_logs(self.sell(args[1], cnt))
            elif sub in ("buy", "买"):
                if len(args) < 2:
                    self.game.emit_logs(self.catalog())
                    return
                cnt = int(args[2]) if len(args) > 2 else 1
                self.game.emit_logs(self.buy(args[1], cnt))
            elif sub in ("list", "ls", "目录", "货品"):
                self.game.emit_logs(self.catalog(args[1] if len(args) > 1 else None))
            else:
                self.game.emit_logs(self.catalog())
                self.log("  market list [pill|material|treasure|equip] 看货　"
                         "market buy <id> [数量] 购入　market sell <id> [数量] 售出")

        return [
            Command("market", "坊市：买卖灵材丹药", "market [list|buy|sell]", _market),
        ]

    # ---------- 持久化 ----------
    def to_dict(self) -> dict[str, Any]:
        return {"prices": dict(self.prices), "last_day": self.last_day}

    def load_state(self, data: dict[str, Any]) -> None:
        self.prices = {k: float(v) for k, v in (data.get("prices") or {}).items()}
        self.last_day = int(data.get("last_day", 0))
        if not self.prices:
            self._init_prices()
