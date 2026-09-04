"""装配工厂：把内核与玩法系统拼成一台能跑的游戏。

想裁剪玩法（比如不要门派），改 default_systems() 即可，内核无需任何改动。
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from .core.base_system import GameSystem, TOPIC_NEW_GAME
from .config.realms import RealmRegistry, power_of
from .core.cultivator import roll_cultivator
from .core.game import SAVE_DIR, Game
from .core.save import SaveManager
from .rng import RNG

# 现实时钟结算（挂机也一直打坐，全程跟现实时间走）：
#   在线倍率 1.5：现实 1 小时 = 游戏内 1.5 日（活跃有加成）
#   离线倍率 1.0：现实 1 小时 = 游戏内 1 日
# 单次结算上限 24 现实小时（防挂机一年回来直接满级）；idle 再兜底 30 游戏日。
ONLINE_DAYS_PER_HOUR = 1.5
OFFLINE_DAYS_PER_HOUR = 1.0
CLOCK_MAX_HOURS = 24.0
# v2：离线加成只生效 50%（挂机收益是「主菜」，但不能被神通/功法加成放大到失控）
OFFLINE_BONUS_HALF = 0.5


def default_systems() -> list[GameSystem]:
    from .core.cultivation import CultivationSystem
    from .core.event_system import EventSystem
    from .systems.alchemy import AlchemySystem
    from .systems.arts import ArtSystem
    from .systems.combat import CombatSystem
    from .systems.asset import AssetSystem
    from .systems.companion import CompanionSystem
    from .systems.daily import DailySystem
    from .systems.duel import DuelSystem
    from .systems.estate import EstateSystem
    from .systems.tianjiao import TianjiaoSystem
    from .systems.inner_demon import InnerDemonSystem
    from .systems.dungeon import DungeonSystem
    from .systems.equipment import EquipmentSystem
    from .systems.market import MarketSystem
    from .systems.quest import QuestSystem
    from .systems.sect import SectSystem
    from .systems.skill import SkillSystem
    from .systems.tribulation import TribulationSystem
    from .systems.law import LawSystem

    return [
        CultivationSystem(),
        EventSystem(),
        EquipmentSystem(),
        ArtSystem(),
        SkillSystem(),
        AlchemySystem(),
        DungeonSystem(),
        CombatSystem(),
        DuelSystem(),          # 论道切磋：修为途径（与战斗/探索并列）
        TribulationSystem(),
        LawSystem(),               # 法则：仙界第二成长轴（悟道 → 乘区 + 突破软门槛）
        SectSystem(),
        EstateSystem(),           # 地：洞府灵气
        AssetSystem(),            # 财：产业被动收入
        CompanionSystem(),        # 侣：道侣双修
        TianjiaoSystem(),         # 天骄榜：排名竞技
        InnerDemonSystem(),       # 心魔劫：突破后的心境考验
        QuestSystem(),
        MarketSystem(),
        DailySystem(),            # 日常任务（v2：7 项可跳过，锦上添花）
    ]


def create_game(
    name: str = "无名",
    seed: int | None = None,
    systems: list[GameSystem] | None = None,
    location: str = "青石镇",
) -> Game:
    """开新档。seed 固定即可复现整局随机过程（用于测试与演示）。"""
    rng = RNG(seed)
    player = roll_cultivator(name, rng)
    game = Game(player=player, rng=rng, location=location)
    for s in (systems if systems is not None else default_systems()):
        game.add_system(s)
    game.bus.emit(TOPIC_NEW_GAME, {})
    return game


def settle_realtime(game: Game, seconds: float, days_per_hour: float) -> list[str]:
    """按现实流逝时间结算打坐收益（在线/离线共用，倍率不同）。

    全程跟随现实时钟：现实秒数 × 倍率 = 游戏小时，推进闭关打坐。
    收益与手动打坐完全一致（同一套 idle -> cultivate 逻辑），不做任何区分。
    不足 1 游戏时辰（挂机不到约 2 分钟）不结算，避免秒级噪音。
    返回闭关日志，调用方自行加文案头。
    """
    hours = max(0.0, seconds / 3600.0)
    hours = min(hours, CLOCK_MAX_HOURS)
    game_hours = hours * days_per_hour * 24.0
    if game_hours < 1.0:
        return []
    # 挂机结算：角色自行闭关打坐-休息，不受玩家精力预算约束
    return game.system("cultivation").idle(game_hours / 24.0, ignore_stamina=True)


def settle_offline(game: Game, saved_at: str = "") -> list[str]:
    """读档时结算离线挂机修为（幂等，见 core/offline.py）。

    时间基准统一用 UTC epoch（game.offline.last_settled_at）；老存档没有该字段时，
    回退用 saved_at（本地时间字符串）换算，并把它当作首次基准，随后切换到 UTC 基准。
    """
    from .core.numfmt import fmt_num
    from .core.offline import OfflineState, MIN_SETTLE_SECONDS, settle as do_settle

    p = game.player
    if not p.alive:
        return []
    # 老存档兼容：无 UTC 基准但有 saved_at，则用 saved_at 作为上次结算时刻
    if game.offline.last_settled_at is None and saved_at:
        try:
            saved = datetime.strptime(saved_at, "%Y-%m-%d %H:%M:%S")
            game.offline = OfflineState(last_settled_at=saved.timestamp())
        except ValueError:
            pass

    power = power_of(p.realm_key)
    bonus = power.cultivation_bonus if power else 0.0
    # v2：所有加成对离线只生效一半（离线收益率 = 主动的 50%）
    bonus *= OFFLINE_BONUS_HALF
    need = p.exp_required()
    if need == float("inf"):                      # 已至绝巅：退回上一层需求，防 inf
        need = RealmRegistry.stage_exp_required(p.realm_def, max(0, p.stage - 1))

    gain, duration, anomaly = do_settle(game.offline, need, cultivation_bonus=bonus)
    # v2：精力离线也恢复（2 / 时辰，上限 100）——上线大概率满精力，主动操作不受离线惩罚
    offline_hours = duration / 3600.0
    p.stamina = min(100.0, p.stamina + offline_hours * 2.0)
    notes = []
    if game.offline.anomaly == "首次登录":
        notes.append("—— 首次登录，已开始记录离线挂机 ——")
    elif gain <= 0:
        if anomaly:
            notes.append(f"（离线结算跳过：{anomaly}）")
        elif duration < MIN_SETTLE_SECONDS and game.offline.pending_exp <= 0:
            pass
        else:
            notes.append("—— 离线归来：未有新的积累 ——")
        return notes
    else:
        hours = duration / 3600.0
        head = f"—— 离线归来：阔别 {hours:.1f} 时辰，后台挂机修为 +{fmt_num(gain)} ——"
        notes.append(head)
        if anomaly:
            notes.append(f"　（{anomaly}：按上限结算）")
    if game.offline.pending_exp > 0:
        notes.append(f"　待领修为 {fmt_num(game.offline.pending_exp)}"
                     f"（claim 领取，突破后可继续领）")
    return notes


def load_game(
    slot: int = 1,
    systems: list[GameSystem] | None = None,
    save_dir: str | None = None,
) -> Game:
    """读档：重建 Game 与各系统，回放私有状态，并按离线时长结算打坐收益。

    结算后会立即重写存档（更新 saved_at），防止同一存档被反复读档重复结算。
    """
    manager = SaveManager(save_dir or SAVE_DIR)
    payload = manager.load(slot)
    factory: Callable[[], list[GameSystem]] = (
        lambda: systems if systems is not None else default_systems()
    )
    game = Game.from_dict(payload["data"], factory)
    logs = settle_offline(game, payload.get("saved_at", ""))
    if logs:
        # 登录即自动领取：能落袋的立刻到账（受本层需求上限约束），
        # 本层已满的部分留在待领池中，等突破后再 claim，不浪费离线积累。
        from .core.numfmt import fmt_num
        from .core.offline import claim as claim_offline
        got = claim_offline(game.offline, game.player.add_exp)
        # 日常追踪：领取离线收益（v2 日常 #1，自动完成）
        daily = game.systems.get("daily")
        if daily is not None:
            daily.track("claim")
        if got > 0:
            logs.append(f"　修为 +{fmt_num(got)}")
        elif game.offline.pending_exp > 0:
            logs.append(f"　本层修为已满，{fmt_num(game.offline.pending_exp)} 修为留存待领"
                        f"（breakthrough 突破后 claim）")
        game.emit_logs(logs)
        manager.save(game, slot, note="offline")   # 结算后写回，保证幂等
    return game
