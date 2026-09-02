"""天骄榜：与同辈天骄争夺排名，按名次领每日俸禄。

规则：
    1. 榜上有名 —— 金丹以上自动入榜（垫底），此前榜上只有 NPC 天骄
    2. 挑战 —— 向名次高于自己的天骄发起切磋（真刀真枪），胜则互换名次
    3. 俸禄 —— 每日按名次发放灵石与修为，排名越高收益越丰厚
    4. 限制 —— 每日挑战 3 次；NPC 天骄会缓慢进步（防止一次爬顶后永远躺平）

防刷：每日挑战上限 + 敌人按玩家属性等比缩放（赢了靠实力，不靠数值碾压）。
"""

from __future__ import annotations

from typing import Any

from ..config.realms import RealmRegistry
from ..core.base_system import Command, GameSystem, TOPIC_DAY_END
from ..core.numfmt import fmt_num

FIGHT_STAMINA = 25
DAILY_LIMIT = 3

# NPC 天骄：境界决定强度区间，power 为战力系数（越高越难缠）
NPC_TIANJIAO = (
    ("萧无涯", "nascent", 3.2),
    ("洛清寒", "deity", 2.8),
    ("顾长风", "void", 2.4),
    ("孟青璃", "integration", 2.0),
    ("苏沐雪", "mahayana", 1.8),
    ("燕十三", "ascension", 1.6),
    ("白秋水", "human_immortal", 1.5),
    ("段孤鸿", "earth_immortal", 1.4),
    ("楚天阔", "heaven_immortal", 1.3),
    ("姬无夜", "mystic_immortal", 1.2),
)

# 名次俸禄：名次 1 -> 最佳
RANK_STONES = [500, 350, 250, 180, 130, 100, 80, 60, 50, 40]


class TianjiaoSystem(GameSystem):
    id = "tianjiao"
    name = "天骄榜"

    def __init__(self) -> None:
        super().__init__()
        # 排名表：名次(1-based) -> {"name": str, "npc": bool, "power": float}
        # NPC 初始占据第 1~10 名，玩家金丹以上入榜垫底
        self.board: list[dict[str, Any]] = []
        self.player_rank: int | None = None
        self.challenges_today: int = 0
        self.last_reward_day: int = 0

    def on_bind(self) -> None:
        if not self.board:
            for i, (name, realm, power) in enumerate(NPC_TIANJIAO):
                self.board.append({"name": name, "npc": True, "power": power,
                                   "realm": realm})
            self.board.append({"name": "…", "npc": False, "power": 0.0, "realm": ""})
        self.game.bus.on(TOPIC_DAY_END, self._on_day_end)

    # ---------- 入榜 / 排名 ----------
    def _ensure_on_board(self) -> bool:
        """金丹以上自动入榜（垫底）。返回是否在榜。"""
        p = self.player
        if self.player_rank is not None:
            return True
        if not RealmRegistry.within(p.realm_key, min_realm="core"):
            return False
        # 玩家插到最后一名 NPC 之后
        self.player_rank = len([b for b in self.board if b.get("npc")]) + 1
        while len(self.board) < self.player_rank:
            self.board.append({"name": p.name, "npc": False, "power": 0.0,
                               "realm": p.realm_key})
        self.board[self.player_rank - 1] = {
            "name": p.name, "npc": False, "power": 0.0, "realm": p.realm_key}
        return True

    def _npc_power(self, rank: int) -> float:
        """指定名次天骄的战力系数。

        v2：NPC 与玩家完全同步成长——战力由玩家当前属性 × power 系数决定，
        玩家变强 NPC 同步变强（差距永远可追），不再随时间缓慢增长。
        """
        npc = self.board[rank - 1]
        return float(npc.get("power", 1.0))

    # ---------- 挑战 ----------
    def fight(self, target_rank: int) -> list[str]:
        p = self.player
        if not p.alive:
            return ["你已身死道消。"]
        if not self._ensure_on_board():
            return ["天骄榜乃金丹以上修士的竞技场，你尚无资格入榜。"]
        if self.challenges_today >= DAILY_LIMIT:
            return [f"今日已挑战 {DAILY_LIMIT} 次，天骄榜需要时间沉淀。明日再来。"]
        if target_rank >= self.player_rank:
            return [f"你当前排名第 {self.player_rank}，只能挑战排名高于你的天骄。"]
        if target_rank < 1 or target_rank > len(self.board):
            return ["此名次无人在榜。"]

        target = self.board[target_rank - 1]
        if not target.get("npc"):
            return ["此位是你自己先前的名次。"]

        p.bump_daily(self.game.day, "tianjiao")
        self.challenges_today += 1
        if not p.spend_stamina(FIGHT_STAMINA):
            return [f"精力不济，难以应战（需 {FIGHT_STAMINA}，现有 {p.stamina:.0f}）。"]

        combat = self.game.systems.get("combat")
        mult = self._npc_power(target_rank)
        enemy = combat.spawn("normal")
        # 天骄战力：属性按 power 系数放大，让排名差距有意义
        enemy.hp = p.max_hp * 0.75 * mult
        enemy.max_hp = enemy.hp
        enemy.atk = p.atk * 0.55 * mult

        logs = [f"【天骄榜】你向排名第 {target_rank} 的{target['name']}发起挑战！"]
        logs.extend(combat.fight(enemy))

        won = not enemy.alive()
        if won:
            # 互换名次
            self.board[self.player_rank - 1], self.board[target_rank - 1] = \
                self.board[target_rank - 1], self.board[self.player_rank - 1]
            self.player_rank = target_rank
            logs.append(f"　★ 你击败{target['name']}，天骄榜排名升至第 {self.player_rank}！")
        else:
            logs.append(f"　{target['name']} 实力深不可测，你败下阵来。")

        logs.extend(self.game.advance_time(2))
        return logs

    # ---------- 每日俸禄 ----------
    def _on_day_end(self, payload: dict) -> None:
        if not self._ensure_on_board():
            return
        day = int(payload.get("day", self.game.day))
        if day == self.last_reward_day:
            return
        self.last_reward_day = day
        self.challenges_today = 0

        rank = self.player_rank
        stones = RANK_STONES[min(rank, len(RANK_STONES)) - 1] if rank <= len(RANK_STONES) else 20
        ratio = max(0.01, 0.30 / rank)             # 排名越高修为越多
        self.player.spirit_stones += stones
        self.player.add_exp(self.player.exp_required() * ratio)
        self.log(f"天骄榜第 {rank} 名俸禄：灵石 +{stones}，修为 +{ratio * 100:.0f}% 需求。")

    # ---------- 展示 ----------
    def board_text(self) -> list[str]:
        lines = ["═══ 天骄榜 ═══"]
        for i, b in enumerate(self.board[:12], 1):
            mark = " ←你" if i == self.player_rank else ""
            name = b["name"]
            realm = b.get("realm", "")
            realm_name = RealmRegistry.get(realm).name if realm in RealmRegistry.BY_KEY else "？"
            lines.append(f"  {i:>2}. {name}（{realm_name or '？'}）{mark}")
        if self.player_rank is None:
            lines.append("  …… 金丹以上可入榜。")
        lines.append(f"  今日剩余挑战 {DAILY_LIMIT - self.challenges_today} 次")
        return lines

    def commands(self) -> list[Command]:
        def _tianjiao(args: list[str]) -> None:
            sub = args[0].lower() if args else ""
            if sub in ("list", "榜"):
                self.game.emit_logs(self.board_text())
            elif sub in ("fight", "挑战"):
                rank = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
                self.game.emit_logs(self.fight(rank))
            else:
                self.game.emit_logs(self.board_text())
                self.log("  tianjiao list 榜单　tianjiao fight <名次> 挑战")

        return [Command("tianjiao", "天骄榜（排名竞技）",
                        "tianjiao [list|fight <名次>]", _tianjiao)]

    # ---------- 持久化 ----------
    def to_dict(self) -> dict[str, Any]:
        return {
            "board": self.board,
            "player_rank": self.player_rank,
            "challenges_today": self.challenges_today,
            "last_reward_day": self.last_reward_day,
        }

    def load_state(self, data: dict[str, Any]) -> None:
        self.board = list(data.get("board") or [])
        if not self.board:
            self.on_bind()
        self.player_rank = data.get("player_rank")
        self.challenges_today = int(data.get("challenges_today", 0))
        self.last_reward_day = int(data.get("last_reward_day", 0))
