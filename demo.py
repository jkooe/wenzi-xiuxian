"""最小可运行示例：演示「修炼 -> 突破 -> 机缘事件 -> 斗法 -> 嗑药 -> 存取档」完整闭环。

固定随机种子，输出可复现。运行：

    python demo.py
    python demo.py --seed 7 --name 王铁柱
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from xiuxian.core.game import Game  # noqa: E402
from xiuxian.core.save import SaveManager  # noqa: E402
from xiuxian.factory import create_game, load_game  # noqa: E402
from xiuxian.ui import panel  # noqa: E402


def step(title: str) -> None:
    print(f"\n{'=' * 60}\n  {title}\n{'=' * 60}")


def show(logs: list[str]) -> None:
    """打印一行行动日志。"""
    print(panel.log_block(logs))


def run(game: Game, line: str) -> list[str]:
    """走一遍命令分发（与 CLI 同一条路径），返回本条指令产生的日志。"""
    parts = line.split()
    cmd, args = parts[0].lower(), parts[1:]
    game.drain_logs()                          # 清掉上一步残留，只看本条指令的输出
    for c in game.commands():
        if c.name == cmd and c.handler:
            c.handler(args)
            return game.drain_logs()
    return [f"  未知指令：{cmd}"]


def main() -> None:
    ap = argparse.ArgumentParser(description="文字修仙核心循环演示")
    ap.add_argument("--seed", type=int, default=42, help="随机种子（固定则结果可复现）")
    ap.add_argument("--name", default="李青玄", help="角色名")
    ap.add_argument("--slot", type=int, default=9,
                    help="演示用存档槽位（默认 9，不覆盖玩家存档；每次先清空避免离线结算干扰）")
    args = ap.parse_args()

    game = create_game(name=args.name, seed=args.seed)
    cult = game.system("cultivation")
    events = game.system("event")
    combat = game.system("combat")

    step(f"0. 开档（seed={args.seed}）")
    print(panel.status_panel(game))

    # ---------- 1. 修炼到修为圆满 ----------
    step("1. 闭关修炼，直至修为圆满")
    for i in range(40):
        if game.player.can_breakthrough():
            break
        logs = cult.cultivate(4)
        if i % 3 == 0 or game.player.can_breakthrough():
            show(logs)
        else:
            game.drain_logs()
        if game.player.stamina < 6:
            show(cult.rest(8))
    print(panel.status_panel(game))

    # ---------- 2. 突破（失败则重修再来）----------
    step("2. 冲击下一境界（失败则修养重来，最多 6 次）")
    for attempt in range(1, 7):
        before = (game.player.realm_key, game.player.stage)
        show(cult.breakthrough())
        if game.over or (game.player.realm_key, game.player.stage) != before:
            break                                  # 成功（或已殒命）
        while cult.cooldown_left() > 0:            # 失败：先熬过冲关冷却
            show(cult.rest(8))
        # 精力枯竭时 cultivate 会直接返回且不推进时间 —— 循环必须自带 guard 并在
        # 精力见底时先 rest，否则会原地空转（本项目踩过的同类坑）。
        guard = 0
        while not game.player.can_breakthrough() and guard < 300:
            if game.player.stamina < 6:
                show(cult.rest(8))
            else:
                cult.cultivate(4)
                game.drain_logs()
            guard += 1
        print(f"\n  —— 修为复满，第 {attempt + 1} 次尝试 ——")
    print(panel.status_panel(game))

    # ---------- 3. 机缘事件 ----------
    step("3. 外出探查，触发机缘事件（force 保证必出）")
    show(game.travel("落云山脉"))
    show(events.trigger(force=True))
    if events.pending:
        show(events.choose(1))
    print(panel.status_panel(game))

    # ---------- 3.5 功法与装备 ----------
    step("3.5 修习功法、穿戴装备（属性即时生效）")
    arts = game.system("arts")
    equip = game.system("equipment")
    game.player.spirit_stones += 2000          # 为演示计，先予灵石
    show(arts.learn("qingxin"))
    show(cult.cultivate(4))                    # 打坐顺带打磨熟练度
    for item_id in ("qingfeng_sword", "chainmail", "windwalker_boots"):
        game.player.inventory.add(item_id, 1)
        show(equip.equip(item_id))
    show(arts.practice(4))                     # 专修功法，只涨熟练度
    print(panel.status_panel(game))

    # ---------- 4. 斗法 ----------
    step("4. 遭遇妖兽，回合制斗法")
    show(cult.rest(8))                         # 专修耗光精力，先调息
    show(combat.fight(combat.spawn("normal")))

    # ---------- 4.5 采药与开炉炼丹 ----------
    step("4.5 采药、换购丹方、开炉炼丹（成败与品阶当场可见）")
    alchemy = game.system("alchemy")
    show(cult.rest(8))                         # 炼丹极耗灵力，先调息满
    show(alchemy.gather(6))                    # 丹道的上游：没有灵草就无米下锅
    show(alchemy.learn("cleansing_pill"))      # 后期灵石的一大去处
    show(alchemy.refine("qi_pill", 3))
    show(alchemy.refine("heal_pill", 2))
    print(panel.inventory_panel(game))

    # ---------- 4.8 秘境探索 ----------
    step("4.8 探秘境：层内抽签（妖兽/机缘/遗宝/静室），底层守关")
    dungeon = game.system("dungeon")
    show(dungeon.catalog())
    show(cult.rest(8))
    show(dungeon.enter("luoyun"))
    for _ in range(14):
        if events.pending:                     # 层内机缘：先做个了断再走
            show(events.choose(1))
            continue
        if not dungeon.run:
            break
        if game.player.stamina < 30 or game.player.hp < game.player.max_hp * 0.55:
            show(cult.rest(8))
            continue
        show(dungeon.next())
    if dungeon.run:
        show(dungeon.flee())                   # 进度存档，下次 enter 由此继续
    show(dungeon.info())
    print(panel.status_panel(game))

    # ---------- 4.9 任务与声望 ----------
    step("4.9 任务自动追踪：斗法/机缘/突破都已被自动计入，达成即领赏")
    quest = game.system("quest")
    show(quest.overview())
    # 再猎几头妖兽凑齐「斩妖卫道」（普通档即可），奖励当场到账
    for _ in range(3):
        if game.player.stamina < 30:
            show(cult.rest(8))
        show(combat.fight(combat.spawn("normal")))
    show(quest.overview())
    print(panel.status_panel(game))

    # ---------- 4.10 坊市（灵石出口）----------
    step("4.10 坊市：买入丹药、卖出材料，灵石有了去处")
    market = game.system("market")
    show(market.catalog("pill"))
    show(market.buy("qi_gathering_pill", 2))
    game.player.inventory.add("beast_core", 3)     # 演示用：补点可售材料
    show(market.sell("beast_core", 2))
    print(panel.status_panel(game))

    # ---------- 4.11 功法主动技能 ----------
    step("4.11 修习功法即得签名技能，战斗中按预设策略自动施放")
    skill = game.system("skill")
    show(run(game, "skill"))                   # 此时只有 3.5 修习的清心诀带来「清心咒」
    game.player.spirit_stones += 2000          # 为演示计，备足灵石
    show(arts.learn("gengjin"))                # 黄阶攻伐功法，signature 技能「庚金一击」
    show(run(game, "skill"))
    show(cult.rest(8))                         # 调息回满，看得清灵力消耗
    show(combat.fight(combat.spawn("normal"))) # 日志里应出现「施展庚金一击」
    show(run(game, "skill strategy 激进"))     # 换策略：少留灵力、多打输出
    show(run(game, "skill strategy 火星文"))   # 非法策略应回退并提示
    print(panel.status_panel(game))            # 状态面板已显示技能与当前策略

    # ---------- 4.12 飞升仙界（境界非终点，仙途继续）----------
    step("4.12 渡劫圆满，飞升仙界（仙途不止步于凡界）")
    # 完整走完凡界需数百日，此处直接快进到渡劫后期演示飞升节点
    game.player.realm_key = "ascension"
    game.player.stage = 2
    show(cult._advance_stage(True))          # 渡劫后期 -> 人仙初期（飞升横幅 + 铭文 + 仙体神通）
    print(panel.status_panel(game))          # 面板显示仙界境界与「仙体」神通

    # ---------- 4.13 闭关挂机（收益与手动打坐完全一致）----------
    step("4.13 闭关挂机：自动打坐-休息循环，无需反复敲命令")
    show(cult.idle(3))                   # 闭关 3 日：打坐→精力耗尽→休息→继续，收益同一套逻辑

    # ---------- 5. 丹药与背包 ----------
    step("5. 服用丹药（品阶不同，药力不同）")
    print(panel.inventory_panel(game))
    for target in ("qi_gathering_pill#high", "qi_gathering_pill",
                   "qi_gathering_pill#low"):
        if game.player.inventory.has(target):
            show(game.use_item(target))
            break
    if game.player.inventory.has("healing_pill"):
        show(game.use_item("healing_pill"))

    # ---------- 6. 存档 / 读档 ----------
    step("6. 存档 -> 读档，校验状态一致")
    saves = SaveManager()
    saves.delete(args.slot)                 # 清空旧演示档，避免读到上次的离线收益
    path = saves.save(game, args.slot, note="demo")
    print(f"  存档文件：{path.name}")
    before = game.status_lines()

    restored = load_game(args.slot)
    after = restored.status_lines()
    print("\n  读档后状态：")
    print(panel.status_panel(restored))
    print("\n  一致性校验：", "通过" if before == after else "不一致（见下）")
    if before != after:
        for a, b in zip(before, after):
            print(f"    {a}  |  {b}")

    # 读档后继续推进，验证随机源与系统状态可用
    print("\n  读档后继续行动：")
    show(restored.system("cultivation").rest(8))
    if restored.player.can_breakthrough():
        show(restored.system("cultivation").breakthrough())
    else:
        for _ in range(3):
            show(restored.system("cultivation").cultivate(4))

    step("演示结束")
    print(f"  最终：{restored.player.full_title}　第 {restored.day} 日　"
          f"灵石 {restored.player.spirit_stones}")
    print("  提示：运行 python main.py 进入完整交互模式。")


if __name__ == "__main__":
    main()
