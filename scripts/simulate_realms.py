"""境界推进节奏模拟：验证凡界/仙界数值。

用法：
    python scripts/simulate_realms.py [LIMIT] [SEED] [START_KEY]
        LIMIT      模拟游戏日上限（默认 3000）
        SEED       随机种子（默认 2026）
        START_KEY  起始境界 key（默认 qi_refining；传 ascension 跳过凡界，直接验证仙界节奏）

策略：纯挂机——修炼(含顿悟) -> 冲关(含渡劫/仙劫) -> 冷却调息。不嗑药、不打猎、不探索。
顿悟按「当前层需求 8%」给修为，比例恒定，因此仙界每层天数应与凡界后期同构。

注意：被冲关冷却拦截的突破不推进时间，循环必须显式 rest 熬冷却，否则死循环。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xiuxian.factory import create_game
from xiuxian.config.realms import BY_KEY, ORDER, RealmRegistry

LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
SEED = int(sys.argv[2]) if len(sys.argv) > 2 else 2026
START_KEY = sys.argv[3] if len(sys.argv) > 3 else "qi_refining"

game = create_game(name="挂机人", seed=SEED)
game.clock.disabled = True          # 推演脚本必须显式禁用时间预算闸门，否则模拟时间不流逝
cult = game.system("cultivation")
trib = game.system("tribulation")
p = game.player

if START_KEY != "qi_refining":
    # 快进到指定境界：补一套该阶段的合理属性（悟性/根骨/气运随境界成长，神识与防御按比例）
    p.realm_key = START_KEY
    p.stage = 0
    p.exp = 0.0
    idx = RealmRegistry.index_of(START_KEY)
    p.attributes.grow_base("physique", 10 + idx * 3)
    p.attributes.grow_base("comprehension", 10 + idx * 3)
    p.attributes.grow_base("luck", 10 + idx * 3)
    p.attributes.grow_base("spirit", 60 + idx * 20)
    for _ in range(idx * 2 + 4):          # 属性累积到当前境界的大致水平
        p.attributes.grow_base("max_hp", 1_000_000 * (2 ** min(idx, 8)))
        p.attributes.grow_base("def", 30_000 * (2 ** min(idx, 8)))

reached: list[tuple[int, str, int, float, float]] = [(1, p.realm_key, p.stage, 0.0, 0.0)]

def snapshot_rates() -> tuple[float, float]:
    nxt = RealmRegistry.next_realm(p.realm_key)
    bt = cult.success_rate(True, 0.0) if nxt else 0.0
    t = trib.success_rate() if (nxt and nxt.key in ORDER[2:]) else 0.0
    return bt, t

def record() -> None:
    """只记录「大境界变化」节点（跨境界突破才算一次里程碑），层内升级并入节点。"""
    cur = (game.day, p.realm_key, p.stage)
    if cur[1] != reached[-1][1] or (cur[1] == reached[-1][1] and cur[2] == 0):
        bt, t = snapshot_rates()
        reached.append((game.day, p.realm_key, p.stage, bt, t))

while game.day <= LIMIT and not game.over:
    if p.stamina < 6:
        cult.rest(8)
    elif p.can_breakthrough():
        if cult.cooldown_left() > 0:
            cult.rest(8)                      # 冲关冷却：调息养伤熬过去
        elif p.stamina < 30:
            cult.rest(8)
        else:
            before = (p.realm_key, p.stage)
            cult.breakthrough()
            if (p.realm_key, p.stage) != before:
                record()
    else:
        cult.cultivate(24)
        record()

print(f"seed={SEED}  起始={BY_KEY[START_KEY].name}  模拟 {LIMIT} 游戏日")
print(f"{'第几日':>7}  {'境界':<8} {'层':<4}  {'突破率':>6} {'渡劫率':>6}")
for day, key, stage, bt, t in reached:
    r = BY_KEY[key]
    bt_s = f"{bt*100:.0f}%" if bt else "-"
    t_s = f"{t*100:.0f}%" if t else "-"
    print(f"{day:>7}  {r.name:<8} {r.stages[stage]:<4}  {bt_s:>6} {t_s:>6}")

print(f"\n最终：{p.full_title}　第 {game.day} 日")
