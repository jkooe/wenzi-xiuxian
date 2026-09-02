"""切磋论道：以神识论道取代动刀动枪的修为途径。

与 hunt 的分工：
    hunt   —— 真刀真枪，产出灵石/材料/装备（修为收益已下调，与打坐同量级）
    duel   —— 论道切磋，产出**修为**（本层需求 2~4%），但每日有场次上限

设计要点
--------
1. **判定用悟性/神识而非战斗数值**：切磋不是打架，用「悟性 + 神识」对撼对手的道行，
   掷骰定胜负——这样它和战斗系统是两条独立曲线，不会互相挤占。
2. **防刷双闸**：消耗精力（DUEL_STAMINA）+ 每日场次上限（DUEL_DAILY_LIMIT）。
3. **修为按需求比例**（exp_ratio），与打坐/历练/丹药同一套口径，不会后期失效。
4. **败也有得**：输了给「败悟」修为（较少），避免玩家因连败完全无收益而劝退。
"""

from __future__ import annotations

from ..core.base_system import Command, GameSystem
from ..core.numfmt import fmt_num

DUEL_STAMINA = 5                  # 切磋精力消耗（v2 放宽：论道 5/次）
DUEL_DAILY_LIMIT = 5              # 每日切磋场次上限
WIN_EXP_RATIO = 0.04              # 胜：本层需求 × 4%
LOSE_EXP_RATIO = 0.02             # 负/平：本层需求 × 2%（败悟）
BASE_WIN_RATE = 0.55              # 基础胜率，再按悟性/神识修正

SPAR_WIN_LINES = (
    "你道心澄澈，一言指出对方破绽，{foe} 颔首认输。",
    "论道三巡，{foe} 被你问得哑口无言，拱手称服。",
    "你以神识化境，{foe} 只觉眼前一花，已知高下。",
)
SPAR_LOSE_LINES = (
    "{foe} 道行深厚，你略逊一筹，然心中亦有体悟。",
    "论道至夜，你终是棋差一着，{foe} 笑道：「道友差点火候。」",
    "你神识略滞，被 {foe} 抢了先机，只得认负。",
)


class DuelSystem(GameSystem):
    id = "duel"
    name = "论道"

    def spar(self, tier: str = "normal") -> list[str]:
        p = self.player
        if not p.alive:
            return ["你已身死道消。"]

        left = p.daily_left(self.game.day, "duel", DUEL_DAILY_LIMIT)
        if left <= 0:
            return [f"今日论道已尽（每日 {DUEL_DAILY_LIMIT} 场），"
                    f"道友明日再来。（论道耗神，不可贪多）"]
        if not p.spend_stamina(DUEL_STAMINA):
            return [f"精力不济，难以凝神论道（需 {DUEL_STAMINA}，"
                    f"现有 {p.stamina:.0f}），先 rest 调息。"]

        p.bump_daily(self.game.day, "duel")
        rng = self.game.rng

        # 对手道行：与玩家同阶（tier 只影响叙事与胜率微调）
        foe_name = rng.choice(("玄清散人", "青竹道人", "寒山客", "白云子",
                               "听松真人", "独钓翁"))
        tier_bonus = {"weak": 0.08, "normal": 0.0, "elite": -0.10,
                      "boss": -0.20}.get(tier, 0.0)
        # 胜率 = 基础 + 悟性/神识修正 + 难度修正，夹在 [0.15, 0.9]
        rate = (BASE_WIN_RATE
                + (p.comprehension - 10) * 0.006
                + (p.attributes.value("spirit") - 5) * 0.0008
                + tier_bonus)
        rate = max(0.15, min(0.90, rate))
        won = rng.chance(rate)

        from ..core.effects import apply_effects
        ratio = WIN_EXP_RATIO if won else LOSE_EXP_RATIO
        need = p.exp_required()
        if need == float("inf"):                 # 绝巅：退回上一层需求，防 inf
            from ..config.realms import RealmRegistry
            need = RealmRegistry.stage_exp_required(p.realm_def,
                                                    max(0, p.stage - 1))
        expected = need * ratio
        before = p.exp
        gained = apply_effects(self.game, [{"type": "exp_ratio", "value": ratio}])
        actual = p.exp - before if p.exp >= before else 0.0

        lines = [rng.choice(SPAR_WIN_LINES if won else SPAR_LOSE_LINES).format(foe=foe_name)]
        lines.extend(gained)
        if actual < expected - 0.5:
            lines.append("　（本层修为将满，余下体悟无处落袋）")
        lines.append(f"　今日尚可论道 {max(0, left - 1)} 场"
                     f"（预计修为 {fmt_num(expected)}）")
        logs = self.game.advance_time(1)
        lines.extend(logs)
        # 日常追踪：论道
        daily = self.game.systems.get("daily")
        if daily is not None:
            daily.track("duel")
        return lines

    def commands(self) -> list[Command]:
        def _spar(args: list[str]) -> None:
            tier = args[0] if args and args[0] in ("weak", "normal", "elite", "boss") \
                else "normal"
            self.game.emit_logs(self.spar(tier))

        return [Command("duel", "论道切磋（修为途径）",
                        "duel [weak|normal|elite|boss]", _spar)]
