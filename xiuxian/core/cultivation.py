"""修炼与突破系统。

核心循环的两条腿：
    cultivate(hours)   打坐吐纳 -> 修为增长 -> 时间流逝 -> 精力消耗
    breakthrough()     修为圆满 -> 判定成败 -> 属性跃迁 -> 广播给其他系统

突破判定通过事件总线开放给外部：
    丹药（config/items.breakthrough_bonus）、渡劫（systems/tribulation）、
    门派护法、阵法加成，都可以往 payload["bonus"] 上叠加，不需要改这里一行代码。
"""

from __future__ import annotations

from typing import Any

from ..config.realms import BY_KEY, RealmRegistry, power_of
from .attributes import COMPREHENSION, LUCK, MAX_HP, MAX_MP, PHYSIQUE, Modifier
from .numfmt import fmt_num
from .base_system import (
    Command,
    GameSystem,
    TOPIC_AFTER_BREAKTHROUGH,
    TOPIC_BEFORE_BREAKTHROUGH,
    TOPIC_HOUR_PASSED,
    TOPIC_PRACTICE,
)

# 修炼产出：以「当前层需求的比例」为基准，而不是挂境界序号的指数。
# 旧公式按境界 ×1.6/×3 增长、需求按 ×6~10 增长，增速不匹配导致比值每境衰减约 6.7 倍：
# 炼气一次打坐直接打满（108%），渡劫之后形同虚设（0.00008%）。改为按需求比例后，
# 打坐在任何境界都有稳定意义，且与需求曲线天然同步。
MEDITATE_EXP_RATIO = 0.0020       # 每时辰产出 = 本层需求 × 0.2%
COMPREHENSION_REF = 1.5           # 悟性倍率基准：悟性 25 时倍率为 1.0（(1+25/50)/1.5）
STAMINA_PER_HOUR = 1.5            # 修炼精力消耗（v2 放宽：打坐不再是精力大户）
# 精力是「主动操作频率闸门」（v2 放宽版）：上限 100，离线/跨日自然恢复，
# 只限制主动操作（猎妖/论道/探索/炼丹/秘境），完全不限制挂机收益。
REST_STAMINA_PER_HOUR = 2.0       # 调息恢复（v2：恢复 2 / 时辰，在线离线均恢复）
BREAKTHROUGH_STAMINA = 30.0
BREAKTHROUGH_HOURS = 2.0
# 后台周天运转（被动修炼）：做其他事（战斗/秘境/赶路/炼丹）时，游戏时间推进
# 也会自动涨修为——产出为主动打坐的 PASSIVE_RATIO 倍，不耗精力、无顿悟/熟练度。
# 主动打坐/闭关是 100% + 顿悟 + 熟练度，仍是高收益路径。
PASSIVE_RATIO = 0.6
# 冲关冷却：失败后需调养若干日才能再冲。
# 没有这道闸，玩家可以每天硬冲大境界，失败扣的丹毒（+3）跑不过每日消退（-1），
# 丹毒越滚越高、渡劫成功率越冲越低，最后卡死在某个境界出不去。
COOLDOWN_MAJOR = 3
COOLDOWN_MINOR = 1
COOLDOWN_FLAG = "bt_cd_until"

# v2 突破加成上限：各来源封顶 + 合计封顶（防止叠满把成功率拉爆到 98% 上限）
PILL_BONUS_CAP = 0.15           # 丹药加成 ≤ +15pp
ART_BONUS_CAP = 0.10            # 功法加成 ≤ +10pp
MENTOR_BONUS_CAP = 0.05         # 师门指点 ≤ +5pp
TOTAL_BONUS_CAP = 0.30          # 所有来源合计 ≤ +30pp

# v2 失败温和化：只散当前层 20%~40% 修为 + 轻伤（调息恢复）+ 失败补偿
FAIL_LOSS_MIN = 0.20            # 失败散去修为下限（当前层 20%）
FAIL_LOSS_MAX = 0.40            # 失败散去修为上限（当前层 40%）
LIGHT_WOUND_BUFF = "buff:轻伤"  # 轻伤状态（修炼速度 -20%，调息 4 时辰自然恢复）
LIGHT_WOUND_HOURS = 4.0
LIGHT_WOUND_PENALTY = 0.20
COMPENSATION_FLAG = "bt_compensation"   # 失败补偿：+2pp/次，可叠加至 +10pp
COMPENSATION_STEP = 0.02
COMPENSATION_MAX = 0.10

# 闭关挂机：单次闭关上限（挂机收益有界，避免一次闭关清空内容）
IDLE_MAX_DAYS = 30


# v2 软上限：修炼速率总加成 +80%，超出部分每 1% 仅算 0.3%（防指数失控）
SPEED_SOFT_CAP = 0.80
SPEED_OVER_CAP_RATIO = 0.30
# 顿悟概率钳制上限 15%（v2）
INSIGHT_MAX_RATE = 0.15
INSIGHT_BASE_RATE = 0.05


def soft_cap(value: float, cap: float, over_ratio: float) -> float:
    """软上限：cap 以内全效，超出部分按 over_ratio 折算（不硬顶，鼓励仍有边际收益）。"""
    if value <= cap:
        return value
    return cap + (value - cap) * over_ratio


class CultivationSystem(GameSystem):
    id = "cultivation"
    name = "修炼"

    def __init__(self) -> None:
        super().__init__()
        self._active_meditating = False      # 主动打坐期间暂停被动周天，防止双倍叠加
        self._idle_mode = False              # 闭关挂机期间跳过时间预算（收益不受精力限制同理）

    def on_bind(self) -> None:
        # 后台周天运转：任何时间推进（战斗/秘境/赶路/炼丹/休息）都会带被动修为
        self.game.bus.on(TOPIC_HOUR_PASSED, self._on_hour_passed)

    # ---------- 被动修炼（后台周天） ----------
    def _exp_need(self, p) -> float:
        """本层需求（绝巅时为 inf，退回上一层需求，防 inf 污染产出）。"""
        need = p.exp_required()
        if need != float("inf"):
            return need
        return RealmRegistry.stage_exp_required(p.realm_def, max(0, p.stage - 1))

    def _hourly_gain(self, p, rng=None) -> float:
        """单位时辰修炼产出 = 本层需求 × 基础比例 × 悟性倍率 × 灵气密度 × 神通加成。

        主动打坐传 rng（有随机浮动），被动周天取期望（无随机）。
        以需求比例为基准，使打坐在任何境界都有稳定意义（不再是后期形同虚设）。
        """
        density = self.game.location_info()["density"]
        # 境界神通：道体/仙域等对修炼产出的加成（挂机友好，纯增益）
        power = power_of(p.realm_key)
        cult_bonus = power.cultivation_bonus if power else 0.0
        comprehension_scale = (1 + p.comprehension / 50.0) / COMPREHENSION_REF
        # 修炼速度加成：来自「法财侣地师」全部来源，由 core/bonus.py 统一聚合
        # v2 软上限：+80% 以内全效，超出部分每 1% 仅算 0.3%
        art_speed = soft_cap(self.game.bonuses.value("cultivate_speed"),
                             SPEED_SOFT_CAP, SPEED_OVER_CAP_RATIO)
        gain = (self._exp_need(p) * MEDITATE_EXP_RATIO * comprehension_scale
                * density * (1.0 + cult_bonus) * (1.0 + art_speed))
        if rng is not None:
            gain *= rng.between(0.88, 1.14)
        # 灵力枯竭则事倍功半（被动也适用，与主动一致）
        if p.mp < p.max_mp * 0.1:
            gain *= 0.6
        # 轻伤（突破失败）：修炼略缓 -20%，调息可愈
        if any(b.source == LIGHT_WOUND_BUFF for b in p.attributes.active_buffs()):
            gain *= (1.0 - LIGHT_WOUND_PENALTY)
        return gain

    def _on_hour_passed(self, payload: dict) -> None:
        """后台周天运转：做其他事时时间推进自动涨修为（无顿悟/熟练度/精力消耗）。"""
        if self._active_meditating:
            return                           # 主动打坐/闭关期间不叠加被动
        p = self.game.player
        if not p.alive:
            return
        hours = float(payload.get("hours", 0))
        gain = self._hourly_gain(p) * hours * PASSIVE_RATIO
        if gain > 0:
            # 静默结算：不产生日志，避免战斗/秘境刷屏。
            # 不能用「修为 +0」那类防噪阈值——前期本层需求小，被动产出本就只有零点几。
            p.add_exp(gain)

    # ---------- 修炼 ----------
    def cultivate(self, hours: float = 4.0, ignore_stamina: bool = False) -> list[str]:
        """打坐修炼。ignore_stamina=True 为挂机路径（闭关/离线/现实时钟）：
        角色自行安排打坐-休息循环，不受精力预算限制——精力只约束主动操作。"""
        p = self.game.player
        if not p.alive:
            return ["你已身死道消，无法修炼。"]
        hours = max(1.0, min(float(hours), 24.0))
        if not ignore_stamina and p.stamina < STAMINA_PER_HOUR:
            return ["精力枯竭，先歇息吧。（rest 4）"]

        real_hours = hours if ignore_stamina else min(hours, p.stamina / STAMINA_PER_HOUR)
        if not ignore_stamina:
            p.spend_stamina(real_hours * STAMINA_PER_HOUR)

        rng = self.game.rng
        gain = 0.0
        for _ in range(int(round(real_hours))):
            gain += self._hourly_gain(p, rng)
            p.heal_mp(p.max_mp * 0.05)      # 吐纳回气

        real = p.add_exp(gain)
        logs = [f"吐纳 {real_hours:.0f} 小时，修为 +{fmt_num(real)}" if real >= 0.5
                else f"吐纳 {real_hours:.0f} 小时，修为本已圆满，点滴不进"]

        # 顿悟：气运触发（已至绝巅时 exp_required 为 inf，跳过，防 inf 污染修为）。
        # 每 4 时辰判定一次：顿悟收益与操作粒度无关——cultivate(24) 一次长坐
        # 与 cultivate(4)×6 次短修收益一致，挂机与手动不产生 6 倍差。
        # 仙界「仙缘」更盛：顿悟比例 8% -> 12%，与挂机养老的长线定位匹配。
        # v2：概率钳制上限 15%（clamp(5% + 气运/1000 + 加成, 2%, 15%)）
        insight_bonus = self.game.bonuses.value("insight_rate")
        rolls = max(1, int(round(real_hours / 4.0)))
        for _ in range(rolls):
            rate = min(INSIGHT_MAX_RATE,
                       INSIGHT_BASE_RATE + p.luck / 1000.0 + insight_bonus)
            if rng.chance(rate):
                need = p.exp_required()
                if need != float("inf"):
                    ratio = 0.12 if RealmRegistry.in_immortal_realm(p.realm_key) else 0.08
                    bonus = need * ratio
                    p.add_exp(bonus)
                    logs.append("心中豁然开朗，似有所悟！额外修为 +%s" % fmt_num(bonus))

        # 广播「练功」：功法系统据此累积熟练度（赶路、休息、斗法不涨）
        #   handler 往 payload["logs"] 里塞提示，由这里统一收回，
        #   否则事件总线的返回值无处可去，熟练度涨了玩家却看不到。
        practiced = self.game.bus.emit(TOPIC_PRACTICE, {"hours": real_hours, "logs": []})
        logs.extend(practiced.get("logs", []))

        # 主动打坐期间暂停被动周天（advance_time 会广播 hour_passed，防止双倍叠加）
        self._active_meditating = True
        try:
            logs.extend(self.game.advance_time(real_hours))
        finally:
            self._active_meditating = False
        if p.can_breakthrough():
            logs.append(f">>> 修为已满，可冲击【{p.next_target_name()}】（breakthrough）")
        return logs

    def rest(self, hours: float = 6.0) -> list[str]:
        p = self.game.player
        hours = max(1.0, min(float(hours), 24.0))
        p.stamina = min(100.0, p.stamina + hours * REST_STAMINA_PER_HOUR)
        p.heal_hp(p.max_hp * 0.08 * hours)
        p.heal_mp(p.max_mp * 0.15 * hours)
        if p.pill_poison > 0:
            p.pill_poison = max(0.0, p.pill_poison - hours * 0.2)
        logs = [f"静卧调息 {hours:.0f} 小时，精力与气血缓缓恢复。"]
        # 轻伤（突破失败）：调息可愈——轻伤 buff 有时效，调息推进时间即自然恢复
        wounded = any(b.source == LIGHT_WOUND_BUFF for b in p.attributes.active_buffs())
        logs.extend(self.game.advance_time(hours))
        if wounded and not any(b.source == LIGHT_WOUND_BUFF
                               for b in p.attributes.active_buffs()):
            logs.append("旧伤尽愈，经脉复通，神清气爽。")
        # v2 探索疲惫：休息 4 时辰清零（连续探索刷机缘的泄压阀）
        from .event_system import EXPLORE_TIRED_FLAG, EXPLORE_TIRED_REST_HOURS
        if p.flags.get(EXPLORE_TIRED_FLAG, 0):
            if hours >= EXPLORE_TIRED_REST_HOURS:
                p.flags.pop(EXPLORE_TIRED_FLAG, None)
                logs.append("歇够了，神思清明，可再外出探查。")
        return logs

    # ---------- 突破 ----------
    def success_rate(self, is_major: bool, extra_bonus: float = 0.0) -> float:
        p = self.game.player
        if is_major:
            target = RealmRegistry.next_realm(p.realm_key)
            base = target.major_success if target else 0.2
        else:
            target = None
            base = 0.93

        # 比例式修正：三维越高乘数越大，且永远不因数值膨胀而顶格（保留 [0.05, 0.98] 安全阀）。
        # 这是对旧公式（base + 0.01×(体质-10) + ...）的修正——旧式在体质≥18 时就被
        # clamp 顶格到 98%，三维差异在后期完全失效；比例式让三维在仙界依然可解释、有贡献。
        power = power_of(p.realm_key)
        pw_bonus = power.breakthrough_bonus if power else 0.0
        # 突破成功率加成（max 规则，多来源只取最高）——v2 各来源封顶：功法 ≤ +10pp
        art_rate = min(ART_BONUS_CAP, self.game.bonuses.value("breakthrough_rate"))
        # 师长指点：一次性加成，判定后即消耗（sect mentor 换取）——v2 封顶 ≤ +5pp
        sect_sys = self.game.systems.get("sect")
        mentor = sect_sys.consume_mentor() if hasattr(sect_sys, "consume_mentor") else 0.0
        mentor = min(MENTOR_BONUS_CAP, mentor)
        # 失败补偿（+2pp/次，可叠加至 +10pp）：温和化——越挫越勇
        compensation = min(COMPENSATION_MAX, float(p.flags.get(COMPENSATION_FLAG, 0.0)))
        # 所有来源合计封顶 ≤ +30pp（超出无效）
        total_bonus = min(TOTAL_BONUS_CAP, extra_bonus + pw_bonus + art_rate + mentor)
        rate = (
            base
            * (1.0
               + 0.0015 * (p.physique - 10)
               + 0.0010 * (p.comprehension - 10)
               + 0.0005 * (p.luck - 10))
            - 0.015 * (p.pill_poison / 10.0)
            + total_bonus
            + compensation
        )
        # 仙界法则软门槛：未达门槛则大幅拉低成功率。
        # 只降不禁 —— 玩家仍可硬冲，符合 v2「失败温和化、不阻断」的一贯哲学（见 config/laws.py）。
        law_sys = self.game.systems.get("law")
        if law_sys is not None:
            if is_major and target is not None:
                penalty, _ = law_sys.gate_penalty(target.key)
                rate -= penalty
            elif not is_major:
                # 末境（混元）圆满：走终局门槛。
                # 没有这一条，末境就完全不受法则约束 —— 混元只剩快速修为、8 天收尾（第 27 批实测）。
                penalty, _ = law_sys.final_gate_penalty()
                rate -= penalty
        return max(0.05, min(0.98, rate))

    def breakthrough(self) -> list[str]:
        p = self.game.player
        game = self.game

        if not p.alive:
            return ["你已身死道消。"]
        if not p.can_breakthrough():
            need = p.exp_required()
            return [f"修为不足，尚需 {fmt_num(need - p.exp)} 点。（{p.progress_ratio() * 100:.1f}%）"]
        if p.stamina < BREAKTHROUGH_STAMINA:
            return [f"状态不佳，突破需 {BREAKTHROUGH_STAMINA:.0f} 精力，先休息。"]
        if p.is_realm_max():
            return ["已臻绝巅，再无前路。"]

        cd = self.cooldown_left()
        if cd > 0:
            return [f"心神未复，需再调养 {cd} 日方可再度冲关。"
                    f"（趁此间隙打坐、打猎攒丹药，或 art practice 打磨功法）"]

        is_major = p.is_major_breakthrough()
        target_key = RealmRegistry.next_realm(p.realm_key).key if is_major else p.realm_key
        target_realm = BY_KEY[target_key].name

        # 丹药加成（突破成功才消耗，失败不扣）
        pill_bonus, pills_to_consume = self.pill_info(p, target_key)

        # 广播：让渡劫 / 门派 / 阵法等系统叠加成功率或直接拦截
        payload: dict[str, Any] = {
            "target_realm": target_realm,
            "target_key": target_key,
            "is_major": is_major,
            "bonus": pill_bonus,
            "logs": [],
            "blocked": False,
            "block_reason": "",
        }
        game.bus.emit(TOPIC_BEFORE_BREAKTHROUGH, payload)

        logs: list[str] = [f"=== 冲击【{p.next_target_name()}】==="]
        # 只预警不拦截：残血冲关是玩家自己的选择，但得让他知道代价
        hp_ratio = p.hp / max(1.0, p.max_hp)
        if hp_ratio < 0.4:
            logs.append(f"※ 气血仅余 {hp_ratio * 100:.0f}%，此刻冲关，反噬恐有性命之忧。（建议先 rest）")
        logs.extend(payload.get("logs", []))
        if pills_to_consume:
            from ..config import items as item_config
            detail = "、".join(
                f"{item_config.get_item(i).name}×{n}" for i, n in pills_to_consume.items()
            )
            logs.append(f"服下 {detail}，药力护持。")
        if payload.get("blocked"):
            # 被拦截（多半是渡劫未过）同样要冷却，否则可以一天内反复冲天劫
            logs.append(f"突破中断：{payload.get('block_reason', '未知原因')}")
            self._set_cooldown(is_major)
            logs.append(f"需调养 {COOLDOWN_MAJOR if is_major else COOLDOWN_MINOR} 日方可再度冲关。")
            logs.extend(game.advance_time(1))       # 即便被拦，时间照样流逝
            return logs

        rate = self.success_rate(is_major, float(payload.get("bonus", 0.0)))
        logs.append(f"推演天机：成功率 {rate * 100:.1f}%")
        # 法则软门槛提示：可解释性 —— 让玩家知道成功率被什么拖累、以及怎么补
        if is_major:
            law_sys = game.systems.get("law")
            if law_sys is not None:
                _, note = law_sys.gate_penalty(target_key)
                if note:
                    logs.append(f"※ {note}（wudao 静悟可补足）")

        p.spend_stamina(BREAKTHROUGH_STAMINA)
        success = game.rng.chance(rate)

        if success:
            # 突破成功：清零失败补偿（已兑现的越挫越勇到此为止）
            p.flags.pop(COMPENSATION_FLAG, None)
            logs.extend(self._advance_stage(is_major))
            logs.extend(self._consume_pills(p, pills_to_consume))
        else:
            logs.extend(self._fail_penalty(is_major))
            logs.append("所服丹药未能奏效，药力散去。")

        logs.extend(game.advance_time(BREAKTHROUGH_HOURS))
        game.bus.emit(
            TOPIC_AFTER_BREAKTHROUGH,
            {"success": success, "target_realm": target_realm, "is_major": is_major},
        )
        return logs

    def _advance_stage(self, is_major: bool) -> list[str]:
        p = self.game.player
        old_name = p.realm_name

        if is_major:
            # 跨境界：属性成长改用新境界的参数，天然形成量级跃迁
            grow_realm = RealmRegistry.next_realm(p.realm_key)
            p.realm_key = grow_realm.key
            p.stage = 0
            # 授予新境界的神通（炼气/筑基为入门阶段无神通；source 前缀 realm: 便于读档重建）
            power = power_of(p.realm_key)
            if power:
                p.attributes.add_modifier(
                    Modifier(source=f"realm:{p.realm_key}",
                             add=dict(power.add), mul=dict(power.mul))
                )
        else:
            grow_realm = p.realm_def
            p.stage += 1

        p.exp = 0.0
        grow = 1.0 + p.physique / 50.0
        p.attributes.grow_base(MAX_HP, grow_realm.hp_per_stage * grow)
        p.attributes.grow_base(MAX_MP, grow_realm.mp_per_stage)
        p.attributes.grow_base("atk", grow_realm.atk_per_stage)
        p.attributes.grow_base("def", grow_realm.def_per_stage)
        p.attributes.grow_base("spirit", grow_realm.spirit_per_stage)

        if is_major:
            p.full_restore()
            p.pill_poison = max(0.0, p.pill_poison - 20)
        else:
            p.heal_hp(p.max_hp * 0.5)
            p.heal_mp(p.max_mp * 0.5)

        # 渡劫圆满 -> 人仙：凡界终点即飞升，用专属横幅拉开仪式感
        if is_major and p.realm_key == "human_immortal":
            out = [f"霞光万道，天门洞开，{old_name} -> {p.realm_name}！你飞升仙界！"]
        else:
            out = [f"金光乍现，{old_name} -> {p.realm_name}！"]
        # 跨大境界多一句「境界铭文」（纯文案，见 config/realms.py REVELATIONS）
        if is_major:
            from ..config.realms import REVELATIONS
            msg = REVELATIONS.get(p.realm_key, "")
            if msg:
                out.append(f"　　{msg}")
        out.append(f"气血上限 {fmt_num(p.max_hp)} / 灵力上限 {fmt_num(p.max_mp)} / 攻击 {fmt_num(p.atk)} / 防御 {fmt_num(p.defense)}")
        if power_of(p.realm_key):
            pw = power_of(p.realm_key)
            out.append(f"顿悟境界神通【{pw.name}】：{pw.desc}")
        out.append(f"寿元增至 {p.lifespan} 载")
        return out

    # ---------- 冲关冷却 ----------
    def cooldown_left(self) -> int:
        """距下次可冲关还剩几日，0 表示随时可冲。"""
        until = self.game.player.flags.get(COOLDOWN_FLAG, 0)
        return max(0, int(until) - self.game.day)

    def _set_cooldown(self, is_major: bool) -> None:
        days = COOLDOWN_MAJOR if is_major else COOLDOWN_MINOR
        self.game.player.flags[COOLDOWN_FLAG] = self.game.day + days

    def _fail_penalty(self, is_major: bool) -> list[str]:
        """v2 温和失败：只散当前层 20%~40% 修为 + 轻伤（调息 4 时辰恢复）
        + 失败补偿（+2pp 突破率，可叠加至 +10pp）。绝不掉境界。"""
        p = self.game.player
        out = ["气息紊乱，突破失败！"]
        self._set_cooldown(is_major)
        out.append(f"需调养 {COOLDOWN_MAJOR if is_major else COOLDOWN_MINOR} 日方可再度冲关。")
        # 散去当前层 20%~40% 修为（随机，温和：不掉境界）
        loss_ratio = self.game.rng.between(FAIL_LOSS_MIN, FAIL_LOSS_MAX)
        lost = p.exp * loss_ratio
        p.exp = max(0.0, p.exp - lost)
        out.append(f"修为散去 {fmt_num(lost)}（剩余 {fmt_num(p.exp)}，未损境界根基）")

        # 轻伤：修炼速度 -20%，调息 4 时辰后自然恢复
        existing = [b for b in p.attributes.active_buffs() if b.source == LIGHT_WOUND_BUFF]
        if existing:
            out.append("旧伤未愈，此番又添新创，伤势更重了些。")
        p.attributes.add_modifier(
            Modifier(source=LIGHT_WOUND_BUFF, add={}, mul={},
                     hours_left=LIGHT_WOUND_HOURS)
        )
        out.append(f"经脉受创，染上轻伤（调息 {LIGHT_WOUND_HOURS:.0f} 时辰可愈，期间修炼略缓）")

        # 失败补偿：越挫越勇，+2pp 可叠加至 +10pp（突破成功时清零）
        comp = float(p.flags.get(COMPENSATION_FLAG, 0.0))
        if comp < COMPENSATION_MAX:
            comp = min(COMPENSATION_MAX, comp + COMPENSATION_STEP)
            p.flags[COMPENSATION_FLAG] = comp
            out.append(f"道心反而更坚，下次突破成功率 +{comp * 100:.0f}pp"
                       f"（最高 +{COMPENSATION_MAX * 100:.0f}pp，突破成功即清零）")
        return out

    # ---------- 丹药加成 ----------
    MAX_PILLS_PER_KIND = 2      # 同种丹药最多叠两颗，避免无脑囤药

    @classmethod
    def pill_info(cls, player, target_realm_key: str) -> tuple[float, dict[str, int]]:
        """计算背包丹药对本次突破的加成，返回 (加成, {item_id: 消耗数量})。只读，不扣物品。

        v2：丹药加成封顶 +15pp（PILL_BONUS_CAP），防止叠药把成功率拉爆。
        """
        from ..config import items as item_config

        bonus_total = 0.0
        consume: dict[str, int] = {}
        for item_id, count in player.inventory.all():
            item = item_config.get_item(item_id)
            b = item.breakthrough_bonus.get(target_realm_key)
            if b is None:
                b = item.breakthrough_bonus.get("*", 0.0)
            if b > 0 and count > 0:
                n = min(count, cls.MAX_PILLS_PER_KIND)
                bonus_total += b * n
                consume[item_id] = n
        # 封顶：超出部分无效（药照吃，加成不再涨——防无脑囤药）
        return min(PILL_BONUS_CAP, bonus_total), consume

    @staticmethod
    def _consume_pills(player, consume: dict[str, int]) -> list[str]:
        from ..config import items as item_config

        logs: list[str] = []
        for item_id, n in consume.items():
            if player.inventory.remove(item_id, n):
                logs.append(f"消耗 {item_config.get_item(item_id).name} ×{n}")
        return logs

    # ---------- 闭关挂机 ----------
    def idle(self, days: float = 0.0, ignore_stamina: bool = False) -> list[str]:
        """闭关：自动打坐-休息循环，收益与手动打坐完全一致（同一套 cultivate 逻辑）。

        days=0 表示闭关到「修为圆满」自动停（突破是决策点，留给玩家，不自动冲关）；
        days>0 闭关指定天数，支持小数（如 0.25 = 6 时辰），按游戏小时精确打坐，
        上限 IDLE_MAX_DAYS（防止一次清空内容）。

        ignore_stamina：
          False（玩家主动闭关）——打坐照常消耗精力，受每日行动预算约束，不能无限刷；
          True （挂机结算：离线/现实时钟/被动周天）——角色自行安排打坐-休息循环，免精力。
        """
        p = self.game.player
        if not p.alive:
            return ["你已身死道消。"]
        start_h = self.game.day * 24.0 + self.game.hour
        target_h = None if days <= 0 else start_h + min(float(days), float(IDLE_MAX_DAYS)) * 24.0
        start_exp = p.exp
        start_stamina = p.stamina
        start_key = (p.realm_key, p.stage)
        insights = 0
        wudao_hours = 0.0          # 仙界：修为圆满后转为悟道累计的时辰

        def _now_h() -> float:
            return self.game.day * 24.0 + self.game.hour

        while not self.game.over:
            if target_h is not None and _now_h() >= target_h - 1e-9:
                break
            if p.can_breakthrough():
                # 仙界：修为圆满但法则未达突破门槛 → 转为悟道，挂机不空转。
                # 这正是「仙界时间主要花在悟道上」的落地：卡境时也有事可做。
                law_sys = self.game.systems.get("law")
                if law_sys is not None and law_sys.should_keep_wudao():
                    remaining = (target_h - _now_h()) if target_h is not None else 8.0
                    hours = max(1.0, min(8.0, remaining))
                    law_sys.auto_wudao(hours)
                    wudao_hours += hours
                    continue
                # 冲关冷却中：闭关调息等待，否则时间不推进、玩家只能干等。
                # （修为已满 + 法则达标 + 冷却未过 = 挂机会空转，必须推进时间）
                if self.cooldown_left() > 0:
                    remaining = (target_h - _now_h()) if target_h is not None else 8.0
                    hours = max(1.0, min(8.0, remaining))
                    self.rest(hours)
                    continue
                break                    # 修为圆满：停下，把突破交给玩家
            if p.stamina < 6 and not ignore_stamina:
                remaining = (target_h - _now_h()) if target_h is not None else 8.0
                self.rest(max(1.0, min(8.0, remaining)))   # 休息时长也按剩余目标截断
            else:
                remaining = (target_h - _now_h()) if target_h is not None else 24.0
                hours = max(1.0, min(24.0, remaining))
                # 主动闭关受精力预算约束；挂机结算（ignore_stamina）免精力
                inner = self.cultivate(hours, ignore_stamina=ignore_stamina)
                insights += sum(1 for ln in inner if "豁然开朗" in ln)

        days_spent = (_now_h() - start_h) / 24.0
        gain = p.exp - start_exp
        if days_spent >= 1:
            head = f"=== 闭关 {days_spent:.1f} 日 ==="
        elif days_spent > 0.02:
            head = f"=== 闭关 {days_spent * 24:.0f} 时辰 ==="
        else:
            head = "=== 闭关不足一日 ==="
        out = [head]
        if gain >= 0.5:
            out.append(f"静心吐纳，修为 +{fmt_num(gain)}（{fmt_num(start_exp)} -> {fmt_num(p.exp)}）")
        else:
            out.append("气息沉凝，修为并未寸进。")
        if insights:
            out.append(f"期间顿悟 {insights} 次，妙悟自生。")
        if wudao_hours >= 12.0:
            out.append(f"　修为圆满而法则未臻，转静悟法则 {wudao_hours / 24.0:.1f} 日"
                       f"（laws 查看进度）")
        if not ignore_stamina:
            out.append(f"精力 {start_stamina:.0f} -> {p.stamina:.0f}（闭关耗神，恢复靠调息与跨日）")
        if (p.realm_key, p.stage) != start_key:
            out.append(f"境界蜕变：{start_key[0]} -> {p.realm_key}！")   # 仅防御性，闭关不突破
        if p.can_breakthrough():
            out.append(f">>> 修为已满（{fmt_num(p.exp)}/{fmt_num(p.exp_required())}），"
                       f"可冲击【{p.next_target_name()}】（breakthrough）")
        return out

    # ---------- 命令 ----------
    def commands(self) -> list[Command]:
        def _cultivate(args: list[str]) -> None:
            hours = float(args[0]) if args else 4.0
            self.game.emit_logs(self.cultivate(hours))

        def _break(args: list[str]) -> None:
            self.game.emit_logs(self.breakthrough())

        def _rest(args: list[str]) -> None:
            hours = float(args[0]) if args else 6.0
            self.game.emit_logs(self.rest(hours))

        def _idle(args: list[str]) -> None:
            days = float(args[0]) if args else 0.0
            self.game.emit_logs(self.idle(days))

        return [
            Command("cultivate", "打坐修炼", "cultivate [小时数]", _cultivate),
            Command("breakthrough", "冲击下一境界", "breakthrough", _break),
            Command("rest", "调息恢复", "rest [小时数]", _rest),
            Command("idle", "闭关挂机", "idle [天数]（无参闭关至修为圆满）", _idle),
        ]
