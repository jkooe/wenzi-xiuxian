"""洞府系统（「地」）：居住之所，灵气之本。

规则：
    1. 购置 —— 花灵石置办洞府，需满足境界门槛
    2. 升级 —— 沿 upgrade_to 链升到更高品阶（差价 + 境界门槛）
    3. 维护 —— 每日结算时扣维护灵石；付不起则洞府荒废（加成失效，仍可补缴复灵）
    4. 加成 —— 走 core/bonus.py 全局聚合，与功法/道侣/师门共用一套效果类型与叠加规则
"""

from __future__ import annotations

from typing import Any

from ..config import estates as estate_config
from ..config.realms import RealmRegistry
from ..core.base_system import TOPIC_DAY_END, Command, GameSystem

SOURCE = "estate:"


class EstateSystem(GameSystem):
    id = "estate"
    name = "洞府"

    def __init__(self) -> None:
        super().__init__()
        self.estate_key: str | None = None
        self.ruined: bool = False          # 是否因欠维护而荒废
        self.last_upkeep_day: int = 0

    @property
    def estate(self):
        return estate_config.get_estate(self.estate_key) if self.estate_key else None

    def on_bind(self) -> None:
        self.game.bus.on(TOPIC_DAY_END, self._on_day_end)

    # ---------- 加成 ----------
    def collect_bonuses(self, agg) -> None:
        """把洞府效果交给全局聚合器（荒废时不提供加成）。"""
        estate = self.estate
        if not estate or self.ruined:
            return
        for eff in estate.effects:
            agg.add(f"{SOURCE}{estate.key}", eff, eff.value)

    def _refresh(self) -> None:
        self.game.rebuild_bonuses()

    # ---------- 行为 ----------
    def buy(self, key: str) -> list[str]:
        p = self.player
        try:
            estate = estate_config.get_estate(key)
        except KeyError:
            return [f"并无「{key}」这处居所。（estate list 查看）"]
        if self.estate_key:
            return [f"你已置办{self.estate.name}，先 estate sell 处置旧居，或 estate upgrade 扩建。"]
        if not RealmRegistry.within(p.realm_key, min_realm=estate.min_realm):
            return [f"{estate.name} 需 {RealmRegistry.get(estate.min_realm).name} 以上方可置办。"]
        if p.spirit_stones < estate.price:
            return [f"灵石不足，{estate.name} 需 {estate.price}（现有 {p.spirit_stones}）。"]

        p.spirit_stones -= estate.price
        self.estate_key = key
        self.ruined = False
        self._refresh()
        return [f"置办{estate.name}（{estate.rank}），灵气 {self._speed_text()}。"
                f"（每日维护 {estate.upkeep} 灵石）", f"　{estate.desc}"]

    def upgrade(self) -> list[str]:
        estate = self.estate
        if not estate:
            return ["你尚无洞府。（estate list 查看，estate buy <key> 置办）"]
        if not estate.upgrade_to:
            return [f"{estate.name} 已是顶阶洞天，无可再扩。"]
        nxt = estate_config.get_estate(estate.upgrade_to)
        p = self.player
        if not RealmRegistry.within(p.realm_key, min_realm=nxt.min_realm):
            return [f"扩建需 {RealmRegistry.get(nxt.min_realm).name} 以上。"]
        cost = max(0, nxt.price - estate.price)
        if p.spirit_stones < cost:
            return [f"灵石不足，扩建需 {cost}（现有 {p.spirit_stones}）。"]

        p.spirit_stones -= cost
        self.estate_key = nxt.key
        self.ruined = False
        self._refresh()
        return [f"洞府扩建为{nxt.name}（{nxt.rank}），灵气 {self._speed_text()}。"
                f"（每日维护 {nxt.upkeep}）"]

    def restore(self) -> list[str]:
        """v2 自动维护：欠费自动暂停、灵石充足自动恢复，无需手动补缴。"""
        estate = self.estate
        if not estate:
            return ["你并无洞府。"]
        if not self.ruined:
            return [f"{estate.name} 灵气如常，无需补缴。"]
        return [f"{estate.name} 阵法暂停运转中——备足维护灵石（{estate.upkeep}/日）后会自动恢复，无需手动打理。"]

    def sell(self) -> list[str]:
        estate = self.estate
        if not estate:
            return ["你并无洞府可处置。"]
        refund = int(estate.price * 0.5)
        self.player.spirit_stones += refund
        self.estate_key = None
        self.ruined = False
        self._refresh()
        return [f"变卖{estate.name}，得灵石 {refund}。（自此漂泊无定所）"]

    def _on_day_end(self, payload: dict) -> None:
        """每日结算（v2 自动维护）：灵石充足自动扣维护，不足自动暂停（不荒废）。"""
        estate = self.estate
        if not estate:
            return
        day = int(payload.get("day", self.game.day))
        if day == self.last_upkeep_day:
            return
        self.last_upkeep_day = day
        p = self.player
        if p.spirit_stones >= estate.upkeep:
            p.spirit_stones -= estate.upkeep
            if self.ruined:                     # 灵石到位自动恢复
                self.ruined = False
                self.game.rebuild_bonuses()
                self.log(f"{estate.name} 重聚灵气，阵法恢复运转。")
        else:
            if not self.ruined:
                self.ruined = True
                self.game.rebuild_bonuses()
                self.log(f"灵石不足支付 {estate.name} 维护，阵法自动暂停"
                         f"（灵气加成暂失效，灵石备足后自会恢复）")
            else:
                self.log(f"{estate.name} 阵法仍在暂停中，灵石备足后自会恢复")

    # ---------- 展示 ----------
    def _speed_text(self) -> str:
        estate = self.estate
        if not estate:
            return "—"
        speed = next((e.value for e in estate.effects if e.type == "cultivate_speed"), 0.0)
        return f"修炼 +{speed * 100:.0f}%"

    def info(self) -> list[str]:
        estate = self.estate
        if not estate:
            return ["你尚无洞府，随处而栖。（estate list 查看，estate buy <key> 置办）"]
        mark = "　【阵法暂停】" if self.ruined else ""
        lines = [f"{estate.name}（{estate.rank}）{mark}",
                 f"　{estate.desc}",
                 f"　灵气：{self._speed_text()}　每日维护 {estate.upkeep} 灵石"]
        if estate.upgrade_to:
            nxt = estate_config.get_estate(estate.upgrade_to)
            lines.append(f"　可扩建：{nxt.name}（{nxt.rank}），需 {max(0, nxt.price - estate.price)} 灵石")
        return lines

    def catalog(self) -> list[str]:
        p = self.player
        lines = ["洞府（estate buy <key>）："]
        for e in estate_config.ESTATES.values():
            if self.estate_key == e.key:
                state = "★已置办"
            elif not RealmRegistry.within(p.realm_key, min_realm=e.min_realm):
                state = f"境界不足（需{RealmRegistry.get(e.min_realm).name}）"
            elif p.spirit_stones < e.price:
                state = "灵石不足"
            else:
                state = "可置办"
            lines.append(f"  {e.name}（{e.rank}）{e.price} 灵石　维护 {e.upkeep}/日　{self._speed_line(e)}　[{state}]　{e.key}")
            lines.append(f"　　　{e.desc}")
        return lines

    @staticmethod
    def _speed_line(e) -> str:
        speed = next((x.value for x in e.effects if x.type == "cultivate_speed"), 0.0)
        return f"修炼 +{speed * 100:.0f}%"

    # ---------- 命令 ----------
    def commands(self) -> list[Command]:
        def _estate(args: list[str]) -> None:
            sub = args[0].lower() if args else ""
            if sub in ("list", "目录"):
                self.game.emit_logs(self.catalog())
            elif sub in ("buy", "置办"):
                self.game.emit_logs(self.buy(args[1] if len(args) > 1 else ""))
            elif sub in ("upgrade", "扩建"):
                self.game.emit_logs(self.upgrade())
            elif sub in ("restore", "补缴"):
                self.game.emit_logs(self.restore())
            elif sub in ("sell", "变卖"):
                self.game.emit_logs(self.sell())
            else:
                self.game.emit_logs(self.info())
                self.log("  estate list 目录　estate buy <key>　estate upgrade　estate restore　estate sell")

        return [Command("estate", "洞府（地）", "estate [list|buy|upgrade|restore|sell]", _estate)]

    # ---------- 持久化 ----------
    def to_dict(self) -> dict[str, Any]:
        return {
            "estate_key": self.estate_key,
            "ruined": self.ruined,
            "last_upkeep_day": self.last_upkeep_day,
        }

    def load_state(self, data: dict[str, Any]) -> None:
        key = data.get("estate_key")
        self.estate_key = key if key in estate_config.ESTATES else None
        self.ruined = bool(data.get("ruined", False))
        self.last_upkeep_day = int(data.get("last_upkeep_day", 0))
