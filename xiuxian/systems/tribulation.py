"""渡劫系统：金丹及以上跨境界突破时，先过天劫这一关（仙界延续，名目分层）。

通过订阅 TOPIC_BEFORE_BREAKTHROUGH 介入，不改动 CultivationSystem 一行代码：
    天劫未渡 -> payload["blocked"] = True，突破直接中止
    天劫已渡 -> payload["bonus"] += 0.05，并有几率获得「劫余」buff

劫名按目标境界数据驱动（config/realms.py 的 tribulation 字段）：
    凡界/仙界低阶「天劫/仙劫」、天仙起「天衰之劫」、金仙起「道心劫」、
    大罗进阶「斩三尸」、准圣->混元「合道大劫」——同一条判定，名目随境界更替。
"""

from __future__ import annotations

from ..config.realms import RealmRegistry, TIER4_AND_ABOVE, power_of
from ..core.attributes import SPIRIT
from ..core.base_system import Command, GameSystem, TOPIC_BEFORE_BREAKTHROUGH


class TribulationSystem(GameSystem):
    id = "tribulation"
    name = "天劫"

    def __init__(self) -> None:
        super().__init__()
        self.passed: list[str] = []     # 已渡过的天劫（境界 key）

    def on_bind(self) -> None:
        self.game.bus.on(TOPIC_BEFORE_BREAKTHROUGH, self.on_before_breakthrough)

    def needs_tribulation(self, target_key: str) -> bool:
        return target_key in TIER4_AND_ABOVE

    def success_rate(self) -> float:
        """天劫独立判定：看防御、神识与气运，丹毒扣分；劫体神通加成。

        裸装约 45%，靠功法/丹药/护身法宝/境界神通可推到 80%+ —— 给玩家留出明确的准备空间。
        """
        p = self.player
        power = power_of(p.realm_key)
        trib_bonus = power.tribulation_bonus if power else 0.0
        rate = (
            0.42
            + p.defense / (p.max_hp * 0.8 + 1) * 0.18      # 防御占气血比
            + min(p.attributes.value(SPIRIT), 200) / 200 * 0.18
            + min(p.luck, 100) / 100 * 0.12
            - p.pill_poison / 300
            + trib_bonus
        )
        return max(0.10, min(0.88, rate))

    def on_before_breakthrough(self, payload: dict) -> None:
        target_key = payload.get("target_key", "")
        if not payload.get("is_major") or not self.needs_tribulation(target_key):
            return

        p = self.player
        target = RealmRegistry.get(target_key)
        trib_name = target.tribulation
        rate = self.success_rate()
        payload["logs"].append(f"{trib_name}感应：乌云汇聚，雷劫将至（渡劫成功率 {rate * 100:.1f}%）")

        if self.game.rng.chance(rate):
            self.passed.append(target_key)
            payload["logs"].append(f"雷霆加身而不灭，你硬生生扛下{trib_name}！")
            payload["bonus"] = payload.get("bonus", 0.0) + 0.05
            if self.game.rng.chance(0.4):
                from ..core.attributes import Modifier
                p.attributes.add_modifier(
                    Modifier(source="buff:劫余", mul={"def": 1.15, "max_hp": 1.10}, hours_left=24 * 30)
                )
                payload["logs"].append("劫云余泽入体，获得状态【劫余】（30 日）。")
        else:
            hurt = p.max_hp * 0.35
            p.damage(hurt, reason="天劫")
            p.pill_poison += 3
            payload["logs"].append(f"{trib_name}劈落，护身法宝尽碎，气血 -{hurt:.0f}，丹毒 +3。")
            payload["blocked"] = True
            payload["block_reason"] = f"{trib_name}未渡，元气大伤，只得暂缓突破"
            if p.alive:
                # 劫后余生：给一点补偿，避免纯挫败感
                p.attributes.grow_base(SPIRIT, 1)
                payload["logs"].append("然祸福相依，你于生死间窥得一线天机，神识 +1。")

    # ---------- 命令 ----------
    def commands(self) -> list[Command]:
        def _preview(args: list[str]) -> None:
            p = self.player
            if p.is_stage_max():
                nxt = RealmRegistry.next_realm(p.realm_key)
                target_key = nxt.key if nxt else p.realm_key
            else:
                target_key = p.realm_key
            if self.needs_tribulation(target_key):
                self.log(f"冲击【{target_key}】将引动天劫，当前渡劫成功率 {self.success_rate() * 100:.1f}%")
            else:
                self.log("以你当前境界，突破尚不会引动天劫。")

        return [Command("tribulation", "查看天劫概率", "tribulation", _preview)]

    # ---------- 持久化 ----------
    def to_dict(self) -> dict:
        return {"passed": list(self.passed)}

    def load_state(self, data: dict) -> None:
        self.passed = list(data.get("passed", []))
