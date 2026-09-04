"""门派系统（示例扩展）：入门、俸禄、贡献、晋升。

演示一个玩法模块如何完整地接入框架：
    - 订阅 TOPIC_DAY_END 发每日俸禄
    - 订阅 TOPIC_COMBAT_VICTORY 累积贡献
    - 提供 sect 命令族
    - 私有状态（门派、贡献、俸禄领取日）走 to_dict / load_state 持久化
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config.arts import ArtEffect
from ..config import masters as master_config
from ..config.realms import RealmRegistry
from ..core.attributes import Modifier
from ..core.base_system import Command, GameSystem, TOPIC_COMBAT_VICTORY, TOPIC_DAY_END


@dataclass(frozen=True)
class SectDef:
    key: str
    name: str
    desc: str
    min_realm: str = "qi_refining"
    join_cost: int = 0
    stipend: int = 15          # 每日灵石
    buff_add: dict = None      # type: ignore[assignment]
    buff_mul: dict = None      # type: ignore[assignment]
    tier: str = "mortal"       # mortal（凡界宗门） / immortal（仙阶门派）
    main_law: str = ""         # 仙门主修法则（laws.py 的 key）
    minor_law: str = ""        # 仙门兼修法则


SECTS: dict[str, SectDef] = {
    "qingyun": SectDef(
        "qingyun", "青云宗", "中正平和，剑修正宗，守序安稳。",
        join_cost=100, stipend=20, buff_mul={"max_mp": 1.10},
    ),
    "xuesha": SectDef(
        "xuesha", "血煞门", "以杀证道，攻伐凌厉，代价是心性。",
        join_cost=100, stipend=15, buff_mul={"atk": 1.15}, buff_add={"luck": -2},
    ),
    "yaowang": SectDef(
        "yaowang", "药王谷", "丹道传家，丹毒消解更快。",
        join_cost=100, stipend=18, buff_add={"physique": 2},
    ),
    "tianji": SectDef(
        "tianji", "天机阁", "推演天机，买卖消息，突破与机缘最是灵通。",
        join_cost=500, min_realm="foundation", stipend=40, buff_mul={"comprehension": 1.08},
    ),
    "jianzhong": SectDef(
        "jianzhong", "剑冢", "万剑埋冢，唯剑痴可入，攻伐一途登峰造极。",
        join_cost=800, min_realm="foundation", stipend=35, buff_mul={"atk": 1.10},
    ),
    "wanbao": SectDef(
        "wanbao", "万宝楼", "以商入道，灵石充裕，修炼资源从不短缺。",
        join_cost=600, min_realm="foundation", stipend=60, buff_add={"luck": 2},
    ),
}

# ---------------- 仙阶门派（飞升后方可拜入） ----------------
# 与凡界宗门并存：飞升后凡界门派自动「仙凡两隔」（停俸停贡献，已获 buff 保留），
# 仙界贡献与仙职独立累积 —— 这样仙职有完整成长曲线，而非飞升即满级。
#
# 数值口径（可调基线，均经第 27 批模拟校准）：
#   join_cost  ≈ 该门派 60~250 日俸禄，定位是「门槛」不是「卡人」（挂机养老品类不卡入门）
#   stipend    凡界 15~60/日，仙界取 ~10 倍（仙界灵石量级整体上一个数量级）
#   main_law   悟道速度 +50%；minor_law +20%；未列名的法则无加成
#   因果法则刻意不给任何仙门加成 —— 叙事上「因果须跳出宗门窠臼」，
#   机制上它是顿悟概率位，不参与速度竞争，避免形成「必选门派」。
IMMORTAL_SECTS: dict[str, SectDef] = {
    "tianshu": SectDef(
        "tianshu", "天枢剑宗", "剑气凌霄，一念断金。主修金之法则，兼修空间。",
        min_realm="human_immortal", join_cost=20_000, stipend=300,
        buff_mul={"atk": 1.15}, tier="immortal",
        main_law="metal", minor_law="space",
    ),
    "changsheng": SectDef(
        "changsheng", "长生道宫", "长生久视，枯木回春。主修木之法则，兼修水。",
        min_realm="human_immortal", join_cost=20_000, stipend=320,
        buff_mul={"max_hp": 1.20}, tier="immortal",
        main_law="wood", minor_law="water",
    ),
    "fentian": SectDef(
        "fentian", "焚天殿", "心火燎原，身随意走。主修火之法则，兼修金。",
        min_realm="earth_immortal", join_cost=60_000, stipend=450,
        buff_mul={"speed": 1.15}, tier="immortal",
        main_law="fire", minor_law="metal",
    ),
    "houtu": SectDef(
        "houtu", "厚土门", "厚德载物，岿然不动。主修土之法则，兼修木。",
        min_realm="earth_immortal", join_cost=60_000, stipend=430,
        buff_mul={"def": 1.20}, tier="immortal",
        main_law="earth", minor_law="wood",
    ),
    "taixu": SectDef(
        "taixu", "太虚观", "须弥芥子，咫尺天涯。主修空间法则，兼修时间。",
        min_realm="heaven_immortal", join_cost=150_000, stipend=600,
        buff_mul={"spirit": 1.20}, tier="immortal",
        main_law="space", minor_law="time",
    ),
}

SECTS.update(IMMORTAL_SECTS)

# 仙门悟道加速
IMMORTAL_MAIN_LAW_SPEED = 0.50      # 主修法则悟道速度 +50%
IMMORTAL_MINOR_LAW_SPEED = 0.20     # 兼修法则 +20%

RANKS = (("外门", 0), ("内门", 200), ("真传", 800), ("长老", 2000))
# 仙职：贡献需求按仙界 1132 天 / 日均贡献 ~20 反推，道主约在仙界 60% 进度处达成
IMMORTAL_RANKS = (("记名", 0), ("真传", 800), ("长老", 2500), ("首座", 6000), ("道主", 12000))

# v2：门派贡献每日自动获得（基础 5 + 职位加成），不需专门刷取战斗
CONTRIBUTION_BASE = 5
CONTRIBUTION_RANK_BONUS = {"外门": 0, "内门": 2, "真传": 5, "长老": 10}
IMMORTAL_CONTRIBUTION_BASE = 8
IMMORTAL_CONTRIBUTION_RANK_BONUS = {"记名": 3, "真传": 6, "长老": 10, "首座": 15, "道主": 20}


class SectSystem(GameSystem):
    id = "sect"
    name = "门派"

    def __init__(self) -> None:
        super().__init__()
        self.sect_key: str | None = None
        self.contribution: int = 0
        self.last_stipend_day: int = 0
        self.master_key: str | None = None          # 师承
        self.mentored_day: int = 0                  # 上次受指点的日期（每日一次）
        self.pending_mentor: float = 0.0            # 指点带来的本次突破加成（一次性）
        # 仙阶门派：与凡界门派并存（飞升后凡界门派「仙凡两隔」，仙界贡献与仙职独立累积）
        self.immortal_sect_key: str | None = None
        self.immortal_contribution: int = 0

    def on_bind(self) -> None:
        self.game.bus.on(TOPIC_DAY_END, self.on_day_end)
        self.game.bus.on(TOPIC_COMBAT_VICTORY, self.on_victory)

    # ---------- 状态 ----------
    @property
    def sect(self) -> SectDef | None:
        return SECTS.get(self.sect_key) if self.sect_key else None

    @property
    def immortal_sect(self) -> SectDef | None:
        return IMMORTAL_SECTS.get(self.immortal_sect_key) if self.immortal_sect_key else None

    @property
    def severed(self) -> bool:
        """是否已「仙凡两隔」——飞升后凡界宗门停俸停贡献（已获 buff 与师承保留）。"""
        return RealmRegistry.in_immortal_realm(self.player.realm_key)

    RANK_SPEED = {"外门": 0.02, "内门": 0.05, "真传": 0.08, "长老": 0.12}
    IMMORTAL_RANK_SPEED = {"记名": 0.05, "真传": 0.10, "长老": 0.15, "首座": 0.20, "道主": 0.25}

    def rank(self) -> str:
        name = "散修"
        for r, need in RANKS:
            if self.contribution >= need:
                name = r
        return name

    def immortal_rank(self) -> str:
        name = "无"
        for r, need in IMMORTAL_RANKS:
            if self.immortal_contribution >= need:
                name = r
        return name

    def law_insight_speed(self, law_key: str) -> float:
        """仙门对某条法则的悟道加速：主修 +50%，兼修 +20%，其余 0。

        供 LawSystem._hourly_insight 查询 —— 门派数据仍只存在本文件，法则系统不认门派表。
        """
        sect = self.immortal_sect
        if not sect or not law_key:
            return 0.0
        if law_key == sect.main_law:
            return IMMORTAL_MAIN_LAW_SPEED
        if law_key == sect.minor_law:
            return IMMORTAL_MINOR_LAW_SPEED
        return 0.0

    def rank_speed(self) -> float:
        """门派职位带来的常驻修炼速度加成：职位越高，可用的修炼场地越好。"""
        return self.RANK_SPEED.get(self.rank(), 0.0)

    def immortal_rank_speed(self) -> float:
        return self.IMMORTAL_RANK_SPEED.get(self.immortal_rank(), 0.0) if self.immortal_sect_key else 0.0

    def apply_buff(self, sect: SectDef | None = None) -> None:
        """把门派专属加成写进属性管线（永久生效，仙凡两隔后仍保留）。"""
        sect = sect if sect is not None else self.sect
        if not sect:
            return
        self.player.attributes.add_modifier(
            Modifier(
                source=f"sect:{sect.name}",
                add=dict(sect.buff_add or {}),
                mul=dict(sect.buff_mul or {}),
            )
        )

    # 门派修炼场地（灵室）：贡献租用，限时提升修炼速度
    CHAMBER_COST = 50          # 租用贡献
    CHAMBER_HOURS = 24         # 时效（时辰）
    CHAMBER_SPEED = 0.30       # 灵室加成

    def collect_bonuses(self, agg) -> None:
        """门派与师承的效果交给全局聚合器（含职位修炼场地与灵室租用）。

        凡界宗门：飞升后「仙凡两隔」，职位场地不再生效（俸禄与贡献同步停发）。
        仙阶门派：职位场地 + 门派专属乘区，与凡界 buff 可共存（后者走 apply_buff 永久生效）。
        """
        if self.sect_key and not self.severed:
            # 职位修炼场地：常驻，随晋升提升
            agg.add("sect:rank", ArtEffect("cultivate_speed", 0.0),
                    self.rank_speed())
        isect = self.immortal_sect
        if isect:
            agg.add("sect:immortal_rank", ArtEffect("cultivate_speed", 0.0),
                    self.immortal_rank_speed())
        master = self.master
        if master:
            for eff in master.effects:
                agg.add(f"sect:master:{master.key}", eff, eff.value)
        # 灵室租用：限时 buff（存于 flags，由 attributes.tick 自然过期）
        if self.player.flags.get("chamber_active"):
            agg.add("sect:chamber", ArtEffect("cultivate_speed", 0.0),
                    self.CHAMBER_SPEED)

    def bonus(self, etype: str) -> float:
        return self.game.bonuses.value_of(etype, "sect:")

    @property
    def master(self):
        return master_config.get_master(self.master_key) if self.master_key else None

    # ---------- 行为 ----------
    def apprentice(self, master_key: str) -> list[str]:
        """拜师：需先入门，并达到该师承要求的职位。"""
        if not self.sect_key:
            return ["你尚无门派，何以拜师。（sect join <key>）"]
        if self.master_key:
            return [f"你已拜{self.master.name}为师，不可二心。"]
        try:
            master = master_config.get_master(master_key)
        except KeyError:
            return [f"并无「{master_key}」这位师长。（sect masters 查看）"]
        if master.sect != self.sect_key:
            return [f"{master.name} 非本门师长。"]
        ranks = [r for r, _ in RANKS]
        if ranks.index(self.rank()) < ranks.index(master.min_rank):
            return [f"{master.name} 只收 {master.min_rank} 以上弟子（你现为{self.rank()}）。"]

        self.master_key = master.key
        self.game.rebuild_bonuses()
        # 日常追踪：拜访师长
        daily = self.game.systems.get("daily")
        if daily is not None:
            daily.track("visit")
        return [f"拜入{master.name}（{master.title}）门下，得其指点。",
                f"　{master.desc}"]

    def masters(self) -> list[str]:
        """本门可拜的师长一览。"""
        if not self.sect_key:
            return ["你尚无门派。（sect join <key>）"]
        lines = [f"{self.sect.name}师长（sect apprentice <key>）："]
        ranks = [r for r, _ in RANKS]
        for m in master_config.masters_of(self.sect_key):
            ok = ranks.index(self.rank()) >= ranks.index(m.min_rank)
            if self.master_key == m.key:
                state = "★已拜"
            elif not ok:
                state = f"职位不足（需{m.min_rank}）"
            else:
                state = "可拜"
            lines.append(f"  {m.name}（{m.title}）[{state}]　{m.key}")
            lines.append(f"　　　{m.desc}")

    def mentor(self) -> list[str]:
        """请师父指点：消耗贡献，换本次突破的一次性成功率加成（每日一次）。"""
        master = self.master
        if not master:
            return ["你尚无师长。（sect apprentice <key> 拜师）"]
        if self.mentored_day == self.game.day:
            return ["今日已得指点，贪多嚼不烂。"]
        if self.contribution < master.mentor_cost:
            return [f"贡献不足，一次指点需 {master.mentor_cost}（现有 {self.contribution}）。"]

        self.contribution -= master.mentor_cost
        self.mentored_day = self.game.day
        self.pending_mentor = master.mentor_breakthrough
        return [f"{master.name}为你讲道解惑，下次突破成功率 +{master.mentor_breakthrough * 100:.0f}%（仅下一次生效）。"]

    def consume_mentor(self) -> float:
        """突破判定时取用并存的一次性指点加成。"""
        value = self.pending_mentor
        self.pending_mentor = 0.0
        return value

    def join(self, sect_key: str) -> list[str]:
        """入门：凡界宗门入 sect_key，仙阶门派入 immortal_sect_key（两者并存）。"""
        p = self.player
        sect = SECTS.get(sect_key)
        if not sect:
            return [f"无此门派。可选：{'、'.join(SECTS)}"]
        if not RealmRegistry.within(p.realm_key, min_realm=sect.min_realm):
            return [f"{sect.name} 收徒门槛：{RealmRegistry.get(sect.min_realm).name} 以上。"]
        if sect.tier == "immortal":
            if self.immortal_sect_key:
                return [f"你已是{self.immortal_sect.name}弟子，仙门不可朝三暮四。"]
            if p.spirit_stones < sect.join_cost:
                return [f"拜入{sect.name}需 {sect.join_cost} 灵石，你囊中羞涩。"]
            p.spirit_stones -= sect.join_cost
            self.immortal_sect_key = sect.key
            self.apply_buff(sect)
            self.game.rebuild_bonuses()
            return [
                f"你拜入{sect.name}，仙籍在册。（日俸 {sect.stipend} 灵石）",
                f"　主修【{self._law_label(sect.main_law)}】悟道 +"
                f"{IMMORTAL_MAIN_LAW_SPEED * 100:.0f}%，"
                f"兼修【{self._law_label(sect.minor_law)}】+"
                f"{IMMORTAL_MINOR_LAW_SPEED * 100:.0f}%",
            ]

        # 凡界宗门（飞升后不再收徒）
        if self.sect_key:
            return [f"你已是{self.sect.name}弟子，不可朝三暮四。"]
        if self.severed:
            return ["你已飞升仙界，与凡界宗门仙凡两隔。（sect list 查看仙阶门派）"]
        if p.spirit_stones < sect.join_cost:
            return [f"入门需 {sect.join_cost} 灵石，你囊中羞涩。"]

        p.spirit_stones -= sect.join_cost
        self.sect_key = sect.key
        self.apply_buff()
        p.flags["sect"] = True
        return [f"你拜入{sect.name}，门规森严，好自为之。（每日俸禄 {sect.stipend} 灵石）"]

    @staticmethod
    def _law_label(law_key: str) -> str:
        from ..config import laws as law_config
        return law_config.BY_KEY[law_key].name if law_key in law_config.BY_KEY else "—"

    def stipend(self) -> list[str]:
        sect = self.immortal_sect if self.severed else self.sect
        if not sect:
            return ["你尚无门派。"]
        if self.last_stipend_day == self.game.day:
            return ["今日俸禄已领。"]
        self.last_stipend_day = self.game.day
        self.player.spirit_stones += sect.stipend
        return [f"领取{sect.name}俸禄，灵石 +{sect.stipend}。"]

    def chamber(self) -> list[str]:
        """租用门派灵室：消耗贡献，限时提升修炼速度（buff，打坐时生效）。"""
        if not (self.sect_key or self.immortal_sect_key):
            return ["你尚无门派，无权使用门派灵室。（sect join <key>）"]
        # 仙界用仙门贡献，凡界用宗门贡献（飞升后凡界贡献已停涨，不应还能透支）
        use_immortal = self.severed or not self.sect_key
        p = self.player
        if p.flags.get("chamber_active"):
            return ["你已在使用门派灵室静修，待时效过后再来。"]
        if use_immortal:
            if self.immortal_contribution < self.CHAMBER_COST:
                return [f"仙门贡献不足，租用灵室需 {self.CHAMBER_COST}"
                        f"（现有 {self.immortal_contribution}）。"]
            self.immortal_contribution -= self.CHAMBER_COST
        else:
            if self.contribution < self.CHAMBER_COST:
                return [f"贡献不足，租用灵室需 {self.CHAMBER_COST}（现有 {self.contribution}）。"]
            self.contribution -= self.CHAMBER_COST
        p.flags["chamber_active"] = True
        from ..core.attributes import Modifier
        p.attributes.add_modifier(
            Modifier(source="buff:灵室", add={}, mul={}, hours_left=self.CHAMBER_HOURS)
        )
        # 灵室实际效果走 collect_bonuses（由 flags 标记），这里刷新聚合
        self.game.rebuild_bonuses()
        return [f"租下门派灵室，闭关 {self.CHAMBER_HOURS} 时辰——"
                f"修炼速度 +{self.CHAMBER_SPEED * 100:.0f}%（贡献 -{self.CHAMBER_COST}）。"]

    # ---------- 事件订阅 ----------
    def on_day_end(self, payload: dict) -> None:
        # 灵室时效由 attributes.tick 过期，此处兜底同步标记
        if self.player.flags.get("chamber_active"):
            if not any(b.source == "buff:灵室" for b in self.player.attributes.active_buffs()):
                self.player.flags.pop("chamber_active", None)
                self.game.rebuild_bonuses()
        # 凡界宗门：飞升后仙凡两隔，停俸停贡献（已授予的 buff 与师承保留，不吃亏）
        sect = self.sect
        if sect and not self.severed:
            self.player.spirit_stones += sect.stipend
            self.log(f"{sect.name}发放俸禄，灵石 +{sect.stipend}。")
            # v2：贡献每日自动获得（基础 5 + 职位加成），不需专门刷取
            gain = CONTRIBUTION_BASE + CONTRIBUTION_RANK_BONUS.get(self.rank(), 0)
            self.contribution += gain
            self.log(f"门派贡献 +{gain}（当前 {self.contribution}，职位 {self.rank()}）")
            # 药王谷：丹毒消解更快
            if sect.key == "yaowang":
                self.player.pill_poison = max(0.0, self.player.pill_poison - 2)

        isect = self.immortal_sect
        if isect:
            self.player.spirit_stones += isect.stipend
            self.log(f"{isect.name}发放仙俸，灵石 +{isect.stipend}。")
            igain = (
                IMMORTAL_CONTRIBUTION_BASE
                + IMMORTAL_CONTRIBUTION_RANK_BONUS.get(self.immortal_rank(), 0)
            )
            self.immortal_contribution += igain
            self.log(
                f"仙门贡献 +{igain}（当前 {self.immortal_contribution}，"
                f"仙职 {self.immortal_rank()}）"
            )

    def on_victory(self, payload: dict) -> None:
        if not self.sect_key:
            return
        gain = max(1, int(payload.get("danger", 1) / 2))
        self.contribution += gain
        self.log(f"门派贡献 +{gain}（当前 {self.contribution}，职位 {self.rank()}）")

    # ---------- 命令 ----------
    def commands(self) -> list[Command]:
        def _sect(args: list[str]) -> None:
            if not args or args[0] == "info":
                logs = self.immortal_info() if self.severed else self.info()
                # 仙界同时展示凡界旧宗（仙凡两隔，仅作纪念）
                if self.severed and self.sect_key:
                    logs.append("")
                    logs.append(f"　（凡界旧宗：{self.sect.name} · {self.rank()}——仙凡两隔，已无往来）")
                self.game.emit_logs(logs)
            elif args[0] == "list":
                self.game.emit_logs(self.list_sects())
            elif args[0] == "join":
                self.game.emit_logs(self.join(args[1] if len(args) > 1 else ""))
            elif args[0] in ("masters", "师长"):
                self.game.emit_logs(self.masters())
            elif args[0] in ("apprentice", "拜师"):
                self.game.emit_logs(self.apprentice(args[1] if len(args) > 1 else ""))
            elif args[0] in ("mentor", "指点"):
                self.game.emit_logs(self.mentor())
            elif args[0] in ("chamber", "灵室"):
                self.game.emit_logs(self.chamber())
            elif args[0] == "stipend":
                self.game.emit_logs(self.stipend())
            else:
                self.log("用法：sect [info|list|join <key>|stipend]")

        return [Command("sect", "门派事务", "sect [info|list|join|stipend]", _sect)]

    def list_sects(self) -> list[str]:
        """门派一览：凡界宗门与仙阶门派分栏，仙门额外标出主修/兼修法则。"""
        out = ["=== 凡界宗门 ==="]
        for s in SECTS.values():
            if s.tier != "mortal":
                continue
            gate = RealmRegistry.get(s.min_realm).name
            out.append(
                f"[{s.key}] {s.name}：{s.desc}（门槛 {gate}，"
                f"入门 {s.join_cost} 灵石，日俸 {s.stipend}）"
            )
        out.append("")
        out.append("=== 仙阶门派（飞升后可入）===")
        for s in IMMORTAL_SECTS.values():
            gate = RealmRegistry.get(s.min_realm).name
            out.append(
                f"[{s.key}] {s.name}：{s.desc}（门槛 {gate}，"
                f"入门 {s.join_cost} 灵石，日俸 {s.stipend}）"
            )
            out.append(
                f"　　主修【{self._law_label(s.main_law)}】"
                f"+{IMMORTAL_MAIN_LAW_SPEED * 100:.0f}%　"
                f"兼修【{self._law_label(s.minor_law)}】"
                f"+{IMMORTAL_MINOR_LAW_SPEED * 100:.0f}%"
            )
        return out

    def info(self) -> list[str]:
        sect = self.sect
        if not sect:
            return ["你乃散修，无门无派。（sect list 查看门派）"]
        return [
            f"{sect.name} · {self.rank()}弟子",
            f"贡献 {self.contribution}　日俸 {sect.stipend} 灵石",
            f"师长：{self.master.name}（{self.master.title}）" if self.master else "师长：尚未拜师（sect masters 查看）",
            f"修炼场地：职位加成 {self.rank_speed() * 100:.0f}%　灵室 {'使用中' if self.player.flags.get('chamber_active') else f'可租（{self.CHAMBER_COST} 贡献）'}",
            f"{sect.desc}",
        ]

    def immortal_info(self) -> list[str]:
        """仙门面板：仙职、贡献、悟道加成。"""
        s = self.immortal_sect
        if not s:
            return ["你尚无仙门归属。（sect list 查看仙阶门派，sect join <key> 拜入）"]
        return [
            f"{s.name} · {self.immortal_rank()}",
            f"仙门贡献 {self.immortal_contribution}　日俸 {s.stipend} 灵石",
            f"修炼场地：仙职加成 {self.immortal_rank_speed() * 100:.0f}%",
            f"悟道：主修【{self._law_label(s.main_law)}】"
            f"+{IMMORTAL_MAIN_LAW_SPEED * 100:.0f}%　"
            f"兼修【{self._law_label(s.minor_law)}】"
            f"+{IMMORTAL_MINOR_LAW_SPEED * 100:.0f}%",
            f"{s.desc}",
        ]

    # ---------- 持久化 ----------
    def to_dict(self) -> dict:
        return {
            "sect_key": self.sect_key,
            "contribution": self.contribution,
            "last_stipend_day": self.last_stipend_day,
            "master_key": self.master_key,
            "mentored_day": self.mentored_day,
            "pending_mentor": self.pending_mentor,
            "immortal_sect_key": self.immortal_sect_key,
            "immortal_contribution": self.immortal_contribution,
        }

    def load_state(self, data: dict) -> None:
        self.sect_key = data.get("sect_key")
        self.contribution = int(data.get("contribution", 0))
        self.last_stipend_day = int(data.get("last_stipend_day", 0))
        mk = data.get("master_key")
        self.master_key = mk if mk in master_config.MASTERS else None
        self.mentored_day = int(data.get("mentored_day", 0))
        self.pending_mentor = float(data.get("pending_mentor", 0.0) or 0.0)
        isk = data.get("immortal_sect_key")
        self.immortal_sect_key = isk if isk in IMMORTAL_SECTS else None
        self.immortal_contribution = int(data.get("immortal_contribution", 0) or 0)
        if self.sect_key:
            self.apply_buff()
        if self.immortal_sect_key:
            self.apply_buff(self.immortal_sect)
