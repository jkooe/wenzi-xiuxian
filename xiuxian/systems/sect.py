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

RANKS = (("外门", 0), ("内门", 200), ("真传", 800), ("长老", 2000))

# v2：门派贡献每日自动获得（基础 5 + 职位加成），不需专门刷取战斗
CONTRIBUTION_BASE = 5
CONTRIBUTION_RANK_BONUS = {"外门": 0, "内门": 2, "真传": 5, "长老": 10}


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

    def on_bind(self) -> None:
        self.game.bus.on(TOPIC_DAY_END, self.on_day_end)
        self.game.bus.on(TOPIC_COMBAT_VICTORY, self.on_victory)

    # ---------- 状态 ----------
    @property
    def sect(self) -> SectDef | None:
        return SECTS.get(self.sect_key) if self.sect_key else None

    RANK_SPEED = {"外门": 0.02, "内门": 0.05, "真传": 0.08, "长老": 0.12}

    def rank(self) -> str:
        name = "散修"
        for r, need in RANKS:
            if self.contribution >= need:
                name = r
        return name

    def rank_speed(self) -> float:
        """门派职位带来的常驻修炼速度加成：职位越高，可用的修炼场地越好。"""
        return self.RANK_SPEED.get(self.rank(), 0.0)

    def apply_buff(self) -> None:
        sect = self.sect
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
        """门派与师承的效果交给全局聚合器（含职位修炼场地与灵室租用）。"""
        if self.sect_key:
            # 职位修炼场地：常驻，随晋升提升
            agg.add("sect:rank", ArtEffect("cultivate_speed", 0.0),
                    self.rank_speed())
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
        p = self.player
        if self.sect_key:
            return [f"你已是{self.sect.name}弟子，不可朝三暮四。"]
        sect = SECTS.get(sect_key)
        if not sect:
            return [f"无此门派。可选：{'、'.join(SECTS)}"]
        if not RealmRegistry.within(p.realm_key, min_realm=sect.min_realm):
            return [f"{sect.name} 收徒门槛：{RealmRegistry.get(sect.min_realm).name} 以上。"]
        if p.spirit_stones < sect.join_cost:
            return [f"入门需 {sect.join_cost} 灵石，你囊中羞涩。"]

        p.spirit_stones -= sect.join_cost
        self.sect_key = sect.key
        self.apply_buff()
        p.flags["sect"] = True
        return [f"你拜入{sect.name}，门规森严，好自为之。（每日俸禄 {sect.stipend} 灵石）"]

    def stipend(self) -> list[str]:
        sect = self.sect
        if not sect:
            return ["你尚无门派。"]
        if self.last_stipend_day == self.game.day:
            return ["今日俸禄已领。"]
        self.last_stipend_day = self.game.day
        self.player.spirit_stones += sect.stipend
        return [f"领取{sect.name}俸禄，灵石 +{sect.stipend}。"]

    def chamber(self) -> list[str]:
        """租用门派灵室：消耗贡献，限时提升修炼速度（buff，打坐时生效）。"""
        if not self.sect_key:
            return ["你尚无门派，无权使用门派灵室。（sect join <key>）"]
        p = self.player
        if p.flags.get("chamber_active"):
            left = p.attributes.active_buffs()
            return ["你已在使用门派灵室静修，待时效过后再来。"]
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
        sect = self.sect
        if not sect:
            return
        self.player.spirit_stones += sect.stipend
        self.log(f"{sect.name}发放俸禄，灵石 +{sect.stipend}。")
        # v2：贡献每日自动获得（基础 5 + 职位加成），不需专门刷取
        gain = CONTRIBUTION_BASE + CONTRIBUTION_RANK_BONUS.get(self.rank(), 0)
        self.contribution += gain
        self.log(f"门派贡献 +{gain}（当前 {self.contribution}，职位 {self.rank()}）")
        # 药王谷：丹毒消解更快
        if sect.key == "yaowang":
            self.player.pill_poison = max(0.0, self.player.pill_poison - 2)

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
                self.game.emit_logs(self.info())
            elif args[0] == "list":
                for s in SECTS.values():
                    self.log(f"[{s.key}] {s.name}：{s.desc}（门槛 {RealmRegistry.get(s.min_realm).name}，"
                             f"入门 {s.join_cost} 灵石，日俸 {s.stipend}）")
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

    # ---------- 持久化 ----------
    def to_dict(self) -> dict:
        return {
            "sect_key": self.sect_key,
            "contribution": self.contribution,
            "last_stipend_day": self.last_stipend_day,
            "master_key": self.master_key,
            "mentored_day": self.mentored_day,
            "pending_mentor": self.pending_mentor,
        }

    def load_state(self, data: dict) -> None:
        self.sect_key = data.get("sect_key")
        self.contribution = int(data.get("contribution", 0))
        self.last_stipend_day = int(data.get("last_stipend_day", 0))
        mk = data.get("master_key")
        self.master_key = mk if mk in master_config.MASTERS else None
        self.mentored_day = int(data.get("mentored_day", 0))
        self.pending_mentor = float(data.get("pending_mentor", 0.0) or 0.0)
        if self.sect_key:
            self.apply_buff()
