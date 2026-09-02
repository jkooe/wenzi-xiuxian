"""日常任务系统（v2）：7 项轻量日常，全部可跳过，做了给锦上添花的奖励。

设计原则（不肝）：
    - 所有日常可跳过，不做没有任何损失
    - 完成 3/5/7 项给阶梯奖励（修为/材料/宝箱），不滚动不累积，当日清零
    - 奖励不具排他性：不做满也不会落后多少

任务清单（7 项，约 45-60 分钟）：
    1. 领取离线收益（自动）
    2. 打坐 2 时辰
    3. 猎妖 1 次
    4. 论道 1 次
    5. 探索 1 次
    6. 炼丹 1 炉
    7. 拜访道侣/师长 1 次

实现：各系统在成功动作处调用 `game.system("daily").track(path)`（侵入一行）；
每日结算（day_end）按完成数发奖并重置。存档字段 {last_day, done}。
"""

from __future__ import annotations

from typing import Any

from ..core.base_system import Command, GameSystem, TOPIC_DAY_END, TOPIC_PRACTICE
from ..core.numfmt import fmt_num

# 7 项日常：path -> (标签, 说明)
TASKS: tuple[tuple[str, str, str], ...] = (
    ("claim", "领离线", "领取离线收益"),
    ("meditate", "打坐", "打坐 2 时辰"),
    ("hunt", "猎妖", "猎妖 1 次"),
    ("duel", "论道", "论道 1 次"),
    ("explore", "探索", "探索 1 次"),
    ("refine", "炼丹", "炼丹 1 炉"),
    ("visit", "拜访", "拜访道侣/师长"),
)

MEDITATE_NEED_HOURS = 2.0     # 打坐 2 时辰算完成

# 阶梯奖励（按完成数）：
REWARD_TIERS = (
    (3, 0.01, None),                       # 3 项：修为 +1%
    (5, 0.03, "材料袋"),                   # 5 项：修为 +3% + 随机材料袋
    (7, 0.05, "每日宝箱"),                 # 7 项：修为 +5% + 每日宝箱
)

BAG_ITEMS = ("beast_blood", "spirit_herb", "beast_core")   # 随机材料袋
CHEST_STONES_BASE = 200                                     # 每日宝箱灵石
CHEST_ITEMS = ("spirit_herb", "beast_core", "healing_pill")


class DailySystem(GameSystem):
    id = "daily"
    name = "日常"

    def __init__(self) -> None:
        super().__init__()
        self.last_day: int = 0
        self.done: set[str] = set()
        self.meditate_hours: float = 0.0    # 当日已打坐时辰（累积）

    def on_bind(self) -> None:
        self.game.bus.on(TOPIC_DAY_END, self._on_day_end)
        self.game.bus.on(TOPIC_PRACTICE, self._on_practice)

    # ---------- 追踪 ----------
    def track(self, path: str) -> None:
        """标记某日常当日完成（幂等）。各系统在成功动作处调用。"""
        if path in self.done:
            return
        self.done.add(path)

    def _on_practice(self, payload: dict) -> None:
        """打坐时辰累积（TOPIC_PRACTICE 由 cultivate 广播）。"""
        self.meditate_hours += float(payload.get("hours", 0))
        if self.meditate_hours >= MEDITATE_NEED_HOURS:
            self.track("meditate")

    def progress(self) -> int:
        return len(self.done)

    # ---------- 每日结算 ----------
    def _on_day_end(self, payload: dict) -> None:
        day = int(payload.get("day", self.game.day))
        if day == self.last_day:
            return
        completed = len(self.done)
        if completed:
            logs = self._grant_rewards(completed)
            self.log(f"—— 日常结算：完成 {completed}/7 项 ——")
            for ln in logs:
                self.log(f"　{ln}")
        # 清零，进入新的一天
        self.last_day = day
        self.done = set()
        self.meditate_hours = 0.0

    def _grant_rewards(self, completed: int) -> list[str]:
        """按完成数给阶梯奖励（取达到的最高档）。"""
        p = self.game.player
        logs: list[str] = []
        reward_ratio = 0.0
        chest = False
        bag = False
        for need, ratio, extra in REWARD_TIERS:
            if completed >= need:
                reward_ratio = max(reward_ratio, ratio)
                if extra == "材料袋":
                    bag = True
                elif extra == "每日宝箱":
                    chest = True

        if reward_ratio > 0:
            need = p.exp_required()
            if need == float("inf"):
                from ..config.realms import RealmRegistry
                need = RealmRegistry.stage_exp_required(p.realm_def, max(0, p.stage - 1))
            gained = p.add_exp(need * reward_ratio)
            if gained >= 0.5:
                logs.append(f"勤修不辍，额外修为 +{fmt_num(gained)}（完成 {completed} 项的奖励）")

        if bag:
            item = self.game.rng.choice(BAG_ITEMS)
            count = self.game.rng.randint(1, 2)
            p.inventory.add(item, count)
            from ..config import items as item_config
            logs.append(f"随机材料袋：拾获 {item_config.get_item(item).name} ×{count}")

        if chest:
            stones = CHEST_STONES_BASE + 50 * self.game.rng.randint(0, 3)
            p.spirit_stones += stones
            logs.append(f"每日宝箱：灵石 +{stones}")
            got = []
            for iid in CHEST_ITEMS:
                p.inventory.add(iid, 1)
                from ..config import items as item_config
                got.append(item_config.get_item(iid).name)
            logs.append(f"每日宝箱：收获 {'、'.join(got)}")
        return logs

    # ---------- 展示 ----------
    def overview(self) -> list[str]:
        lines = [f"日常任务（今日完成 {self.progress()}/7，全部可跳过）："]
        for path, label, desc in TASKS:
            done = "✓" if path in self.done else "·"
            if path == "meditate":
                detail = f"（{self.meditate_hours:.0f}/{MEDITATE_NEED_HOURS:.0f} 时辰）" \
                    if self.meditate_hours < MEDITATE_NEED_HOURS else ""
                lines.append(f"  {done} {label}：{desc}{detail}")
            else:
                lines.append(f"  {done} {label}：{desc}")
        lines.append(f"　阶梯奖励：3 项修为+1%　5 项 +3%+材料袋　7 项 +5%+每日宝箱")
        return lines

    # ---------- 命令 ----------
    def commands(self) -> list[Command]:
        def _daily(args: list[str]) -> None:
            self.game.emit_logs(self.overview())

        return [Command("daily", "日常任务进度", "daily list", _daily)]

    # ---------- 持久化 ----------
    def to_dict(self) -> dict[str, Any]:
        return {"last_day": self.last_day, "done": sorted(self.done),
                "meditate_hours": round(self.meditate_hours, 2)}

    def load_state(self, data: dict[str, Any]) -> None:
        self.last_day = int(data.get("last_day", 0))
        self.done = set(data.get("done") or [])
        self.meditate_hours = float(data.get("meditate_hours", 0.0) or 0.0)
