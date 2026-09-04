"""仙界法则系统：悟道攒感悟 → 点亮法则 → 提供乘区，并作为仙界突破的软门槛。

与既有系统的衔接（全部走现有通道，核心计算零改动）
------------------------------------------------
- 加成：实现 `collect_bonuses(agg)`，把已点亮阶数转成 `ArtEffect` 交给全局聚合器，
        由 bonus.py 统一聚合并注入 Modifier（法则即 attr_mul 乘区）。
- 突破：向 cultivation 暴露 `gate_penalty()`，未达门槛时扣成功率（软门槛，不禁突破）。
- 挂机：`should_keep_wudao()` / `auto_wudao()` 供 cultivation.idle 调用，
        仙界修为圆满但法则不够时自动转悟道，挂机不空转。

状态存在系统私有字段（progress / focus），走 to_dict / load_state 持久化，
不动 Cultivator 存档结构 —— 老档读进来默认零进度，直接可玩。
"""

from __future__ import annotations

from typing import Any

from ..config import laws as law_config
from ..config.arts import ArtEffect
from ..config.realms import RealmRegistry
from ..core.base_system import Command, GameSystem
from ..core.numfmt import fmt_num


class LawSystem(GameSystem):
    id = "law"
    name = "法则"

    SOURCE = "law:"

    def __init__(self) -> None:
        super().__init__()
        # 法则进度：law_key -> 累计感悟值（阶数由感悟值推导，不单独存）
        self.progress: dict[str, float] = {}
        # 当前专注法则：挂机悟道 / `wudao` 不带参数时使用
        self.focus: str = law_config.ORDER[0]
        self._epiphany: bool = False      # 本次悟道是否触发过法则顿悟（供日志用）

    # ---------- 加成聚合 ----------
    def collect_bonuses(self, agg) -> None:
        """把已点亮的法则阶数（主属性 + 阶梯副词条）交给全局聚合器（仅仙界生效）。

        主属性走 LawDef.effect_type/effect_key（如 atk / cultivate_speed / insight_rate），
        副词条走 config.laws.LAW_AFFIXES：每条法则按已达成的每一阶，解锁该阶的额外词条。
        两者都经现有加成管线（attr_mul 偏移求和、attr_add 累加），零改核心计算。
        """
        if not RealmRegistry.in_immortal_realm(self.player.realm_key):
            return  # 凡界不窥法则
        for key in law_config.ORDER:
            stage = law_config.stage_of(self.progress.get(key, 0.0))
            if stage <= 0:
                continue
            law = law_config.BY_KEY[key]
            # 主属性 / 功能效果（每阶 LAW_STAGE_VALUE）
            agg.add(
                f"{self.SOURCE}{key}",
                ArtEffect(type=law.effect_type, key=law.effect_key),
                law_config.LAW_STAGE_VALUE * stage,
            )
            # 阶梯副词条：已达成的每一阶逐一解锁
            for k in range(1, stage + 1):
                for affix in law_config.LAW_AFFIXES.get(key, ())[k - 1]:
                    atype, akey, aval = affix
                    agg.add(
                        f"{self.SOURCE}{key}:affix{k}",
                        ArtEffect(type=atype, key=akey),
                        aval,
                    )

    def _refresh(self) -> None:
        self.game.rebuild_bonuses()

    # ---------- 悟道 ----------
    def _sect_speed(self, law_key: str) -> float:
        """门派对某条法则的悟道加速（主修 +50% / 兼修 +20%），无门派则 0。

        走 sect 系统查询而非在此硬编码门派表，仙门数据仍只存在于 sect.py。
        """
        sect_sys = self.game.systems.get("sect")
        if sect_sys is None:
            return 0.0
        getter = getattr(sect_sys, "law_insight_speed", None)
        return float(getter(law_key)) if callable(getter) else 0.0

    def _hourly_insight(self, p, rng=None, law_key: str | None = None) -> float:
        """每时辰感悟产出 = 基数 × 悟性倍率 × 门派加成 × 浮动。

        注意：**刻意不让因果法则加速悟道**。
        首版设计为「因果法则加速悟道、时间法则加速打坐」，看似对称，实则正反馈失控：
        点因果 → 悟道变快 → 更快点满 → 后期每境反而只要 40 天（比前期还快），
        节奏曲线倒挂（第 27 批实测：金仙 57 / 太乙 45 / 大罗 40 天）。
        故此处只保留悟性与门派影响，因果法则回归纯「顿悟概率」定位 —— 加速器各管一路，
        但都不加速自己的获取过程。

        门派加成同理受约束：只加速「主修/兼修」这 1~2 条，不加速获取门派本身的过程。
        """
        comprehension_scale = (1 + p.comprehension / 50.0) / law_config.WUDAO_COMPREHENSION_REF
        gain = law_config.WUDAO_BASE * comprehension_scale
        if law_key:
            gain *= 1.0 + self._sect_speed(law_key)
        if rng is not None:
            gain *= rng.between(0.88, 1.14)
        return gain

    # ---------- 外部注入（事件 / 丹药 / 任务） ----------
    # 每日事件感悟次数上限：仙界跨 1132 天，任何「每次行动给感悟」的设计都会被时间放大，
    # 这是防刷硬闸（与探索修为的 EXPLORE_DAILY_LIMIT 同理）。
    INSIGHT_DAILY_LIMIT = 2

    # ---------- 仙市·法则兑换（仙门贡献 → 法则进度，第 28 批经济闭环） ----------
    # 仙门贡献日结（IMMORTAL_CONTRIBUTION_BASE=8 + 仙职加成，约 8~31/日），几乎只进不出
    # （第 27 批仅灵室一个消耗点）。此处开出「贡献 → 法则轴」的消耗出口，让双轴资源博弈成环：
    #   玩家可选择「花时辰悟道」还是「花贡献直接换感悟」——二者此消彼长。
    #
    # 定价推导（须不破坏 1170 天节奏，详见 calibrate 验证）：
    #   仙门贡献全程可获 ≈ 均值 15/日 × 1048 悟道天 ≈ 15720。
    #   若全部砸进「感悟兑换」(RATE=0.20 → 5 贡献/感悟)：≈ 3144 感悟 = 总需 30400 的 10.3%。
    #   → 仅占一成，法则门槛仍主要靠悟道，节奏不被破坏。
    #   与仙职阈值(800/2500/6000/12000)竞争：花贡献推法则 = 牺牲升职(俸禄/场地/buff)，
    #   构成核心张力。
    IMMORTAL_INSIGHT_PER_CONTRIB = 0.20   # 兑换率：每 1 点感悟需 1/0.20 = 5 贡献
    IMMORTAL_EPIPHANY_COST = 200         # 法则顿悟机缘：固定花费
    IMMORTAL_EPIPHANY_INSIGHT = 40       # 一次机缘给的感悟（≈ 同 RATE 的便利版，不需悟道耗时）
    IMMORTAL_STAGE_PREMIUM = 3.0         # 直接推阶溢价：本阶感悟成本 ×3 折算成贡献（稀有奢侈）

    def hourly_insight(self, law_key: str | None = None) -> float:
        """当前每时辰悟道产出（不含随机浮动）——供 insight_hours 效果计价。"""
        key = law_key if law_key in law_config.BY_KEY else self.focus
        return self._hourly_insight(self.player, None, key)

    def law_insight_rate(self) -> float:
        """法则顿悟概率：基础 + 因果法则的 insight_rate，钳在上限内。

        因果法则（insight_rate）此前只作用于打坐顿悟，而打坐顿悟硬顶 15%、
        基础 5% + 气运 ~0.8%，两阶即触顶 —— 后三阶白点（第 27 批实测）。
        此处为它开出第二处消费点：静悟时的「法则顿悟」，让五阶都有边际收益。
        """
        bonus = self.game.bonuses.value("insight_rate")
        return min(
            law_config.LAW_INSIGHT_MAX_RATE,
            law_config.LAW_INSIGHT_BASE_RATE + bonus,
        )

    def gain_insight(self, law_key: str | None, amount: float) -> tuple[float, int, int]:
        """外部来源注入感悟，返回 (实收, 原阶, 新阶)。

        未飞升则拒收（返回全 0）——凡界玩家吃到仙界事件奖励不应凭空积累。
        """
        if not RealmRegistry.in_immortal_realm(self.player.realm_key):
            return 0.0, 0, 0
        key = law_key if law_key in law_config.BY_KEY else self.focus
        amount = max(0.0, float(amount))
        before = law_config.stage_of(self.progress.get(key, 0.0))
        self.progress[key] = self.progress.get(key, 0.0) + amount
        after = law_config.stage_of(self.progress[key])
        if after > before:
            self._refresh()
        return amount, before, after

    def law_name(self, law_key: str | None = None) -> str:
        key = law_key if law_key in law_config.BY_KEY else self.focus
        return law_config.BY_KEY[key].name

    # ---------- 仙市·法则兑换 ----------
    def _sect_sys(self):
        """取仙门系统（不存在返回 None）。"""
        return self.game.systems.get("sect")

    def _spend_immortal_contribution(self, amount: int) -> bool:
        """扣仙门贡献，成功返回 True。余额不足或不在仙界返回 False。"""
        sect = self._sect_sys()
        if sect is None:
            return False
        cur = getattr(sect, "immortal_contribution", 0)
        if cur < amount:
            return False
        sect.immortal_contribution = cur - amount
        return True

    def buy_insight(self, amount: float) -> list[str]:
        """仙市·感悟兑换：花仙门贡献给专注法则换感悟（5 贡献/感悟）。"""
        p = self.player
        if not RealmRegistry.in_immortal_realm(p.realm_key):
            return ["凡俗之躯，难入仙市。需飞升仙界方可兑换法则。"]
        if not self._sect_sys() or not self._sect_sys().immortal_sect_key:
            return ["你尚未拜入仙门，无仙门贡献可耗。（sect join <仙门>）"]
        amount = max(1.0, float(amount))
        cost = int(round(amount / self.IMMORTAL_INSIGHT_PER_CONTRIB))
        if not self._spend_immortal_contribution(cost):
            have = getattr(self._sect_sys(), "immortal_contribution", 0)
            return [f"仙门贡献不足，兑换 {fmt_num(amount)} 感悟需 {cost}"
                    f"（现有 {have}）。"]
        real, before, after = self.gain_insight(self.focus, amount)
        logs = [f"于仙市以仙门贡献 {cost} 兑换【{self.law_name()}】感悟 +{fmt_num(real)}"]
        if after > before:
            logs.append(f"　【{self.law_name()}】更进一步："
                        f"{law_config.stage_name(before)} → {law_config.stage_name(after)}！")
        return logs

    def buy_epiphany(self) -> list[str]:
        """仙市·法则顿悟机缘：花固定贡献，立即得一笔感悟（不必悟道耗时）。"""
        p = self.player
        if not RealmRegistry.in_immortal_realm(p.realm_key):
            return ["凡俗之躯，难入仙市。需飞升仙界方可兑换法则。"]
        if not self._sect_sys() or not self._sect_sys().immortal_sect_key:
            return ["你尚未拜入仙门，无仙门贡献可耗。（sect join <仙门>）"]
        cost = self.IMMORTAL_EPIPHANY_COST
        if not self._spend_immortal_contribution(cost):
            have = getattr(self._sect_sys(), "immortal_contribution", 0)
            return [f"仙门贡献不足，顿悟机缘需 {cost}（现有 {have}）。"]
        real, before, after = self.gain_insight(self.focus, self.IMMORTAL_EPIPHANY_INSIGHT)
        logs = [f"于仙市求得【{self.law_name()}】顿悟机缘（仙门贡献 -{cost}），"
                f"感悟 +{fmt_num(real)}"]
        if after > before:
            logs.append(f"　【{self.law_name()}】更进一步："
                        f"{law_config.stage_name(before)} → {law_config.stage_name(after)}！")
        return logs

    def buy_stage(self) -> list[str]:
        """仙市·直接推阶：花（本阶剩余感悟 × 溢价）贡献，专注法则立即升一阶。"""
        p = self.player
        if not RealmRegistry.in_immortal_realm(p.realm_key):
            return ["凡俗之躯，难入仙市。需飞升仙界方可兑换法则。"]
        if not self._sect_sys() or not self._sect_sys().immortal_sect_key:
            return ["你尚未拜入仙门，无仙门贡献可耗。（sect join <仙门>）"]
        key = self.focus
        stage = law_config.stage_of(self.progress.get(key, 0.0))
        if stage >= law_config.LAW_MAX_STAGE:
            return [f"【{self.law_name()}】已至主宰，无可再推。"]
        need = law_config.cost_to_next(self.progress.get(key, 0.0))
        cost = int(round(need / self.IMMORTAL_INSIGHT_PER_CONTRIB * self.IMMORTAL_STAGE_PREMIUM))
        if not self._spend_immortal_contribution(cost):
            have = getattr(self._sect_sys(), "immortal_contribution", 0)
            return [f"仙门贡献不足，推一阶需 {cost}（现有 {have}）。"]
        real, before, after = self.gain_insight(key, need)
        return [f"于仙市强推【{self.law_name()}】一阶（仙门贡献 -{cost}），"
                f"{law_config.stage_name(before)} → {law_config.stage_name(after)}！"
                f"（感悟 +{fmt_num(real)}）"]

    def stage_label(self, stage: int) -> str:
        return law_config.stage_name(stage)

    def wudao(
        self,
        law_key: str | None = None,
        hours: float = 4.0,
        ignore_stamina: bool = False,
    ) -> list[str]:
        """悟道：投入时辰换取指定法则的感悟值，推进时间（被动周天照常涨修为）。"""
        p = self.player
        if not p.alive:
            return ["你已身死道消，无法悟道。"]
        if not RealmRegistry.in_immortal_realm(p.realm_key):
            return ["凡俗之躯，难窥法则真容。需飞升仙界（人仙）后方可悟道。"]

        if law_key:
            if law_key not in law_config.BY_KEY:
                opts = "、".join(law_config.ORDER)
                return [f"未知法则「{law_key}」。可选：{opts}"]
            self.focus = law_key
        else:
            law_key = self.focus

        hours = max(1.0, min(float(hours), law_config.WUDAO_MAX_HOURS))
        if not ignore_stamina and p.stamina < law_config.WUDAO_STAMINA_PER_HOUR:
            return ["精力枯竭，先歇息吧。（rest 4）"]
        real_hours = (
            hours if ignore_stamina
            else min(hours, p.stamina / law_config.WUDAO_STAMINA_PER_HOUR)
        )
        if not ignore_stamina:
            p.spend_stamina(real_hours * law_config.WUDAO_STAMINA_PER_HOUR)

        rng = self.game.rng
        gain = 0.0
        rolls = max(1, int(round(real_hours / law_config.LAW_INSIGHT_ROLL_HOURS)))
        rate = self.law_insight_rate()
        for _ in range(int(round(real_hours))):
            gain += self._hourly_insight(p, rng, law_key)

        # 法则顿悟：概率受因果法则驱动，一次给相当于 N 时辰的产出。
        # 每 LAW_INSIGHT_ROLL_HOURS 时辰判定一次（与打坐顿悟同款设计）——
        # 收益与操作粒度无关，挂机 wudao(24) 与手动 wudao(4)×6 结果一致。
        for _ in range(rolls):
            if rng.chance(rate):
                gain += self._hourly_insight(p, None, law_key) * law_config.LAW_INSIGHT_BONUS_HOURS
                self._epiphany = True

        # 推进时间：挂机路径（ignore_stamina）跳过时间预算闸门（见 game.advance_time 注释）。
        # 若闸门截断了时长，感悟按「实际推进时辰」等比折算 ——
        # 否则会出现「时间没走、感悟照涨」的零成本刷法则（第 27 批实测踩到，务必保留此折算）。
        start_h = self.game.day * 24.0 + self.game.hour
        advance_logs = self.game.advance_time(real_hours, bypass_budget=ignore_stamina)
        advanced = (self.game.day * 24.0 + self.game.hour) - start_h
        if advanced <= 0:
            if not ignore_stamina:
                p.stamina = min(100.0,
                                p.stamina + real_hours * law_config.WUDAO_STAMINA_PER_HOUR)
            return advance_logs + ["时间之力暂时耗尽，静悟未果。"]
        if advanced < real_hours - 1e-9:
            gain *= advanced / real_hours
            real_hours = advanced

        law = law_config.BY_KEY[law_key]
        before = law_config.stage_of(self.progress.get(law_key, 0.0))
        self.progress[law_key] = self.progress.get(law_key, 0.0) + gain
        after = law_config.stage_of(self.progress[law_key])

        logs = [f"静悟【{law.name}】{real_hours:.0f} 时辰，感悟 +{fmt_num(gain)}"]
        if getattr(self, "_epiphany", False):
            self._epiphany = False
            logs.append("　心有所感，因果牵动——【法则顿悟】感悟大进！")
        if after > before:
            logs.append(
                f"　【{law.name}】更进一步："
                f"{law_config.stage_name(before)} → {law_config.stage_name(after)}！"
            )
            self._refresh()  # 阶数变化 → 重算乘区
        else:
            nxt = law_config.cost_to_next(self.progress[law_key])
            if nxt > 0:
                logs.append(f"　距下一阶尚需感悟 {fmt_num(nxt)}。")
        logs.extend(advance_logs)
        return logs

    # ---------- 挂机集成 ----------
    def target_gate(self) -> int:
        """当前应冲刺的门槛：未到末境看下一境，末境看终局门槛。"""
        p = self.player
        nxt = RealmRegistry.next_realm(p.realm_key)
        if nxt is not None:
            return law_config.gate_of(nxt.key)
        # 末境：圆满前还需过终局门槛（否则混元只剩快速修为，8 天潦草收尾）
        if p.is_realm_max():
            return 0
        return law_config.LAW_FINAL_GATE

    def should_keep_wudao(self) -> bool:
        """修为已圆满、但法则未达门槛 → 挂机应继续悟道而非空转。"""
        p = self.player
        if not RealmRegistry.in_immortal_realm(p.realm_key):
            return False
        if not p.can_breakthrough():
            return False
        gate = self.target_gate()
        return gate > 0 and law_config.total_stages(self.progress) < gate

    def auto_wudao(self, hours: float = 4.0) -> list[str]:
        """挂机悟道（免精力）：角色自行静悟，收益与手动一致。"""
        return self.wudao(law_key=self.focus, hours=hours, ignore_stamina=True)

    # ---------- 突破软门槛 ----------
    def final_gate_penalty(self) -> tuple[float, str]:
        """末境（混元）圆满的终局门槛惩罚，返回 (惩罚值, 说明文案)。

        仅在「已处末境且尚未圆满」时生效 —— 凡界、以及仙界非末境的小突破一律返回 0，
        否则会把所有小突破都拖下水（凡界玩家 0 阶，必然触罚）。
        """
        p = self.player
        if not RealmRegistry.in_immortal_realm(p.realm_key):
            return 0.0, ""
        if not RealmRegistry.is_last(p.realm_key) or p.is_realm_max():
            return 0.0, ""
        gate = law_config.LAW_FINAL_GATE
        cur = law_config.total_stages(self.progress)
        if cur >= gate:
            return 0.0, ""
        return (
            law_config.LAW_FINAL_GATE_PENALTY,
            f"大道未圆（{cur}/{gate} 阶），混元难证，成功率 "
            f"-{law_config.LAW_FINAL_GATE_PENALTY * 100:.0f}pp",
        )

    def gate_penalty(self, target_realm_key: str) -> tuple[float, str]:
        """突破到目标境界的法则门槛惩罚，返回 (惩罚值, 说明文案)。

        达标返回 (0.0, "")；未达标返回 (LAW_GATE_PENALTY, 说明)。
        """
        gate = law_config.gate_of(target_realm_key)
        if gate <= 0:
            return 0.0, ""
        cur = law_config.total_stages(self.progress)
        if cur >= gate:
            return 0.0, ""
        return (
            law_config.LAW_GATE_PENALTY,
            f"法则未臻（{cur}/{gate} 阶），大道不全，成功率 "
            f"-{law_config.LAW_GATE_PENALTY * 100:.0f}pp",
        )

    # ---------- 面板 ----------
    def panel(self) -> list[str]:
        """法则面板：八条法则的阶数 / 进度 / 当前提供的加成。"""
        p = self.player
        if not RealmRegistry.in_immortal_realm(p.realm_key):
            return ["=== 法则 ===", "　凡俗之躯，难窥法则。飞升仙界后方可悟道。"]

        total = law_config.total_stages(self.progress)
        out = [f"=== 法则（累计 {total} 阶 / 上限 {len(law_config.ORDER) * law_config.LAW_MAX_STAGE}）==="]
        for key in law_config.ORDER:
            law = law_config.BY_KEY[key]
            insight = self.progress.get(key, 0.0)
            stage = law_config.stage_of(insight)
            name = law_config.stage_name(stage)
            mark = "★" if key == self.focus else "　"
            line = f"{mark}{law.name}　{name}"
            if stage > 0:
                line += f"　（{law.desc}　{law_config.LAW_STAGE_VALUE * stage * 100:.0f}%）"
            else:
                line += f"　（{law.desc}）"
            out.append(line)
        # 末境：显示终局门槛（混元圆满），否则显示下一境门槛
        p = self.player
        if RealmRegistry.is_last(p.realm_key) and not p.is_realm_max():
            gate = law_config.LAW_FINAL_GATE
            ok = "已达标" if total >= gate else f"未达标（差 {gate - total} 阶）"
            out.append(f"【混元圆满】终局需 {gate} 阶：{ok}")
        else:
            nxt = RealmRegistry.next_realm(p.realm_key)
            if nxt is not None:
                gate = law_config.gate_of(nxt.key)
                if gate > 0:
                    ok = "已达标" if total >= gate else f"未达标（差 {gate - total} 阶）"
                    out.append(f"下一境【{nxt.name}】需 {gate} 阶：{ok}")
        return out

    # ---------- 持久化 ----------
    def to_dict(self) -> dict[str, Any]:
        return {"progress": dict(self.progress), "focus": self.focus}

    def load_state(self, data: dict[str, Any]) -> None:
        self.progress = {
            k: float(v) for k, v in dict(data.get("progress", {})).items()
            if k in law_config.BY_KEY
        }
        focus = data.get("focus")
        self.focus = focus if focus in law_config.BY_KEY else law_config.ORDER[0]

    # ---------- 命令 ----------
    def commands(self) -> list[Command]:
        def _wudao(args: list[str]) -> None:
            # wudao [法则] [时辰] —— 首个可解析为法则名的参数即法则，否则为时辰
            law_key: str | None = None
            hours = 4.0
            rest: list[str] = []
            for a in args:
                if law_key is None and a in law_config.BY_KEY:
                    law_key = a
                else:
                    rest.append(a)
            if rest:
                try:
                    hours = float(rest[0])
                except ValueError:
                    pass
            self.game.emit_logs(self.wudao(law_key, hours))

        def _laws(args: list[str]) -> None:
            self.game.emit_logs(self.panel())

        def _focus(args: list[str]) -> None:
            if not args or args[0] not in law_config.BY_KEY:
                opts = "、".join(law_config.ORDER)
                self.game.emit_logs([f"用法：law focus <法则>　可选：{opts}"])
                return
            self.focus = args[0]
            law = law_config.BY_KEY[args[0]]
            self.game.emit_logs([f"此后静悟以【{law.name}】为主（挂机亦按此推进）。"])

        def _buy(args: list[str]) -> None:
            # law buy insight <n> | law buy epiphany | law buy stage
            if not args:
                self.game.emit_logs([
                    "用法：law buy insight <感悟数>　仙门贡献换感悟（5 贡献/点）",
                    "　　　law buy epiphany　　　求顿悟机缘（固定贡献，即时感悟）",
                    "　　　law buy stage　　　　　强推专注法则一阶（溢价，稀有）",
                ])
                return
            sub = args[0].lower()
            if sub == "insight":
                n = 100.0
                if len(args) > 1:
                    try:
                        n = float(args[1])
                    except ValueError:
                        pass
                self.game.emit_logs(self.buy_insight(n))
            elif sub == "epiphany":
                self.game.emit_logs(self.buy_epiphany())
            elif sub == "stage":
                self.game.emit_logs(self.buy_stage())
            else:
                self.game.emit_logs([f"未知兑换「{sub}」。可用：insight / epiphany / stage"])

        return [
            Command("wudao", "静悟法则（仙界）", "wudao [法则] [时辰]", _wudao),
            Command("laws", "查看法则面板", "laws", _laws),
            Command("law focus", "设置专注法则", "law focus <法则>", _focus),
            Command("law buy", "仙市·法则兑换（消耗仙门贡献）", "law buy <insight|epiphany|stage>", _buy),
        ]
