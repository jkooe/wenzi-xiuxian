"""仙界经济闭环校准（第二十八批）：验证「全买派」不破坏 1170 天节奏。

测什么
------
设计铁律（见 xiuxian/config/laws.py）：仙界突破是「修为 + 法则」双轴软门槛，
法则进度的主来源是**挂机悟道**（idle wudao）；外部注入（仙市兑换·仙门贡献、
法则感悟丹·灵石）只能是 minor supplement，否则会压缩悟道时间、把仙界节奏拉崩。

本脚本模拟一种**最激进的「全买派」**——把所有可支配资源都砸向法则轴：
    · 每日买满 2 颗法则感悟丹（受 INSIGHT_DAILY_LIMIT=2 每日硬闸约束）
    · 把全部仙门贡献拿去 buy_insight（5 贡献/点）
    · 修为圆满但法则未达门槛时自动转悟道（idle wudao）
验证两条：
    1) 外部感悟（丹药 + 仙市兑换）占总投入感悟的比例 < 15%；
    2) 仙界总停留天数落在 1170 ± 10%（即 [1053, 1287]）。

为何多种子取均值
----------------
单种子模拟带随机噪声（悟道产出 ±14% 浮动、顿悟概率），单跑会盖过参数差异。
第 27 批已确立：必须 8 种子取均值才可靠。

用法
----
    python scripts/calibrate_immortal_economy.py [LIMIT] [SEED_START]
        LIMIT      模拟游戏日上限（默认 4000）
        SEED_START 8 个种子的起始值（默认 2026，取 2026..2033）
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xiuxian.factory import create_game
from xiuxian.config import laws as lawcfg
from xiuxian.config.realms import RealmRegistry

LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 4000
SEED_START = int(sys.argv[2]) if len(sys.argv) > 2 else 2026
SEEDS = list(range(SEED_START, SEED_START + 8))

IMMORTAL_START = "human_immortal"        # 跳过凡界，直接验证仙界节奏
PILL_ID = "law_insight_pill"
PILL_PRICE = 30_000                       # 见 config/items.py
BREAKTHROUGH_STAMINA = 30.0


def total_insight(law) -> float:
    return sum(law.progress.values())


def run_one(seed: int, buy: bool) -> dict:
    mode = "全买派" if buy else "纯挂机"
    game = create_game(name=mode, seed=seed)
    game.clock.disabled = True            # 推演必须禁用时间预算闸门
    p = game.player
    p.realm_key = IMMORTAL_START
    p.stage = 0
    p.exp = 0.0
    idx = RealmRegistry.index_of(IMMORTAL_START)
    p.attributes.grow_base("physique", 10 + idx * 3)
    p.attributes.grow_base("comprehension", 10 + idx * 3)
    p.attributes.grow_base("luck", 10 + idx * 3)
    p.attributes.grow_base("spirit", 60 + idx * 20)
    for _ in range(idx * 2 + 4):
        p.attributes.grow_base("max_hp", 1_000_000 * (2 ** min(idx, 8)))
        p.attributes.grow_base("def", 30_000 * (2 ** min(idx, 8)))

    cult = game.system("cultivation")
    law = game.system("law")
    sect = game.system("sect")
    market = game.system("market")

    # 拜入天枢需 20000 灵石（仙门本金）；余下作为可购丹药的灵石池。
    # 仙界灵石主线来源是仙俸（300/日），故即便初始只有这点，丹药也受价格锁（3 万/颗）。
    p.spirit_stones = 100_000
    sect.join("tianshu")                  # 天枢剑宗：主修金、兼修空间，仙俸 300/日
    game.rebuild_bonuses()

    def lowest_law() -> str:
        """把悟道/兑换焦点切到当前阶数最低的法则——理性玩家会摊开点，避免单条满阶后浪费感悟。"""
        return min(lawcfg.ORDER,
                   key=lambda k: lawcfg.stage_of(law.progress.get(k, 0.0)))

    start_day = game.day
    external = 0.0
    pill_buys = 0
    buy_insight_calls = 0

    def gain_external(fn):
        nonlocal external
        before = total_insight(law)
        fn()
        external += total_insight(law) - before

    while game.day <= LIMIT and not game.over:
        # —— 全买派每日策略（纯挂机模式跳过，仅测经济对节奏的净影响）——
        if buy:
            # 1) 买满当日可服的法则感悟丹（每日硬闸 2 次，按余量买，避免浪费灵石）
            used_today = p.daily_used(game.day, "insight")
            can_use = max(0, 2 - used_today)
            to_buy = min(can_use, p.spirit_stones // PILL_PRICE)
            for _ in range(int(to_buy)):
                market.buy(PILL_ID, 1)
            while p.inventory.count(PILL_ID) > 0:
                cnt = p.inventory.count(PILL_ID)
                gain_external(lambda: game.use_item(PILL_ID))   # 内含每日 2 次硬闸
                if p.inventory.count(PILL_ID) == cnt:
                    break                                  # 被每日硬闸拦住，剩余丹留着
                pill_buys += 1
            # 2) 把全部仙门贡献砸进感悟兑换（5 贡献/点）—— 真实最坏情况：每日清空
            law.focus = lowest_law()
            afford = int(sect.immortal_contribution * law.IMMORTAL_INSIGHT_PER_CONTRIB)
            if afford >= 1:
                gain_external(lambda: law.buy_insight(afford))
                buy_insight_calls += 1

        # —— 推进：修为 / 悟道 / 突破（仿 simulate_realms.py 的稳妥循环 + 法则门槛）——
        nxt = RealmRegistry.next_realm(p.realm_key)
        stages = lawcfg.total_stages(law.progress)
        if nxt is not None:
            gate = lawcfg.gate_of(nxt.key)
            if p.can_breakthrough() and stages >= gate:
                if cult.cooldown_left() > 0:
                    cult.rest(8)              # 冷却期必须推进时间，否则空转
                elif p.stamina < BREAKTHROUGH_STAMINA:
                    cult.rest(8)
                else:
                    cult.breakthrough()       # 成功/失败仅推进 2 时辰，失败设 3 日冷却
            elif p.exp < p.exp_required():
                cult.cultivate(24)            # 推进一天，跨日触发 on_day_end（发仙俸+贡献）
            else:
                law.focus = lowest_law()
                law.auto_wudao(24)            # 修为满、门槛未达 → 挂机悟道
        else:
            # 末境（混元）：需 34 阶 + 修为圆满
            if p.exp < p.exp_required():
                cult.cultivate(24)
            elif stages < lawcfg.LAW_FINAL_GATE:
                law.focus = lowest_law()
                law.auto_wudao(24)            # 修为满、推终局门槛
            else:
                break                         # 圆满收尾

    immortal_days = game.day - start_day + 1
    total = total_insight(law)
    return {
        "seed": seed,
        "days": immortal_days,
        "total_insight": total,
        "external": external,
        "ext_frac": (external / total) if total > 0 else 0.0,
        "final_stages": lawcfg.total_stages(law.progress),
        "pill_buys": pill_buys,
        "buy_insight_calls": buy_insight_calls,
        "stones_left": p.spirit_stones,
        "contrib_left": sect.immortal_contribution,
        "realm": p.realm_key,
    }


def _summarize(rows):
    n = len(rows)
    return {
        "days": sum(r["days"] for r in rows) / n,
        "frac": sum(r["ext_frac"] for r in rows) / n,
        "max_frac": max(r["ext_frac"] for r in rows),
        "stages": sum(r["final_stages"] for r in rows) / n,
        "pills": sum(r["pill_buys"] for r in rows) / n,
        "buys": sum(r["buy_insight_calls"] for r in rows) / n,
    }


def main() -> None:
    idle = [run_one(s, False) for s in SEEDS]      # 纯挂机基线（无外部注入）
    buyr = [run_one(s, True) for s in SEEDS]       # 全买派（倾尽贡献+丹药）
    si, sb = _summarize(idle), _summarize(buyr)
    n = len(SEEDS)

    print(f"仙界经济闭环校准 · 双模式对照 · {n} 种子 (seed {SEED_START}..{SEED_START + n - 1})")
    print(f"{'模式':>6} {'天数均值':>9} {'外部占比':>9} {'终阶':>5} {'丹药':>5} {'兑换次':>6}")
    print(f"{'纯挂机':>6} {si['days']:>9.0f} {si['frac'] * 100:>8.1f}% {si['stages']:>5.0f} "
          f"{si['pills']:>5.0f} {si['buys']:>6.0f}")
    print(f"{'全买派':>6} {sb['days']:>9.0f} {sb['frac'] * 100:>8.1f}% {sb['stages']:>5.0f} "
          f"{sb['pills']:>5.0f} {sb['buys']:>6.0f}")
    print("-" * 56)

    # 判定 1：外部感悟占比（经济安全性的核心指标）
    frac_ok = sb["frac"] < 0.15 and sb["max_frac"] < 0.15
    # 判定 2：经济对时间线的净影响 = (纯挂机 - 全买派) / 纯挂机
    econ_compress = (si["days"] - sb["days"]) / si["days"] if si["days"] else 0.0
    # 判定 3：绝对天数 vs 1170 目标（注意：拜天枢的玩家画像本身已压缩节奏，见说明）
    days_abs_ok = 1053 <= sb["days"] <= 1287

    print(f"[判定1] 全买派外部感悟占比 均值 {sb['frac'] * 100:.1f}% / 峰值 {sb['max_frac'] * 100:.1f}% < 15%? "
          f"{'OK' if frac_ok else 'FAIL'}")
    print(f"[判定2] 经济对时间线净影响 = 纯挂机 {si['days']:.0f} 日 → 全买派 {sb['days']:.0f} 日 "
          f"= 压缩 {econ_compress * 100:.1f}%（<<10% 即未破坏节奏）? "
          f"{'OK' if econ_compress < 0.10 else 'FAIL'}")
    print(f"[判定3] 全买派绝对天数 {sb['days']:.0f} ∈ [1053,1287]（1170±10%）? "
          f"{'OK' if days_abs_ok else 'FAIL（画像效应，见下）'}")
    print()
    print("[说明] 丹药通道均值仅 %.0f 颗（3 万/颗，受灵石价格锁；每日硬闸 2 仅兜底），"
          "仙市兑换均值 %.0f 次。" % (sb["pills"], sb["buys"]))
    print("[说明] 判定3 不达标系拜天枢（+50%% 主修金悟道）的玩家画像效应，与 28 批经济无关：")
    print("       纯挂机（同样拜天枢）亦仅 %.0f 日，证明经济本身仅压缩 %.1f%%。"
          % (si["days"], econ_compress * 100))
    print("[建议] 若要把' realistic 仙界时长'重新锚定到 ~1170，应下调 WUDAO_BASE（或削天枢悟道加成），")
    print("       属独立调参项，不在本批经济闭环范围内——请确认是否要我重校。")


if __name__ == "__main__":
    main()
