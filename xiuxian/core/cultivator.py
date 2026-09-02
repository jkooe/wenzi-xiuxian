"""修士（主角 / 未来的 NPC 通用）。

只负责「我是谁、我现在什么状态」，不含任何玩法规则。
修炼、突破、战斗等规则分别在 cultivation.py 和 systems/ 下。
"""

from __future__ import annotations

from typing import Any

from ..config.realms import BY_KEY, RealmRegistry
from .attributes import AttributeSet, COMPREHENSION, LUCK, MAX_HP, MAX_MP, PHYSIQUE
from .inventory import Inventory

MAX_STAMINA = 100.0


class Cultivator:
    def __init__(
        self,
        name: str,
        realm_key: str = "qi_refining",
        stage: int = 0,
        attributes: AttributeSet | None = None,
    ) -> None:
        self.name = name
        self.realm_key = realm_key
        self.stage = stage
        self.exp = 0.0               # 当前小境界的修为进度
        self.total_exp = 0.0         # 累计修为（仅统计展示）
        self.attributes = attributes or AttributeSet()
        self.inventory = Inventory()
        self.equipment: dict[str, str] = {}   # 部位 -> 物品 id（属性由装备系统注入 Modifier）

        # 200 是刻意卡的线：够修习一门黄阶功法（200），不够买兵器（300+），
        # 开局就逼玩家在「功法 / 门派 / 攒钱买装备」之间做取舍
        self.spirit_stones = 200
        self.pill_poison = 0.0
        self.age = 16
        # 每日限额：{日期: {途径: 已用次数}}。各修为途径共用这一套计数防刷，
        # 按 day 索引，跨日自动失效，无需额外清理逻辑。
        self.daily: dict[str, dict[str, int]] = {}
        self.flags: dict[str, Any] = {}
        self.alive = True

        self.hp = self.max_hp
        self.mp = self.max_mp
        self.stamina = MAX_STAMINA

    # ---------- 境界 ----------
    @property
    def realm_def(self):
        return BY_KEY[self.realm_key]

    @property
    def realm_name(self) -> str:
        return RealmRegistry.full_name(self.realm_key, self.stage)

    @property
    def full_title(self) -> str:
        r = self.realm_def
        return f"{self.realm_name}·{r.title} {self.name}"

    @property
    def stage_count(self) -> int:
        return self.realm_def.stage_count

    def is_stage_max(self) -> bool:
        return self.stage >= self.stage_count - 1

    def is_realm_max(self) -> bool:
        return RealmRegistry.is_last(self.realm_key) and self.is_stage_max()

    def clamp_state(self) -> None:
        """读档/反序列化后钳制非法数值。

        存档可能被手改、损坏或跨版本不兼容：非法 realm_key 会让 realm_def 抛 KeyError、
        越界 stage 会让 realm_name 抛 IndexError，两者都导致整个存档读不出来（进度丢失）。
        这里在反序列化层统一兜住：非法值回退/夹紧到合法区间，保证「能读档」优先于「精确」。
        """
        if self.realm_key not in BY_KEY:
            self.realm_key = "qi_refining"
        self.stage = max(0, min(int(self.stage), self.realm_def.stage_count - 1))
        need = self.exp_required()
        self.exp = max(0.0, float(self.exp))
        if need != float("inf"):
            self.exp = min(self.exp, need)
        self.hp = max(0.0, min(float(self.hp), self.max_hp))
        self.mp = max(0.0, min(float(self.mp), self.max_mp))
        self.stamina = max(0.0, min(float(self.stamina), MAX_STAMINA))
        self.spirit_stones = max(0, int(self.spirit_stones))
        self.pill_poison = max(0.0, float(self.pill_poison))
        self.age = max(0, int(self.age))

    def global_stage(self) -> int:
        return RealmRegistry.global_stage_index(self.realm_key, self.stage)

    def exp_required(self) -> float:
        """升到下一层所需修为。已满级返回 inf。"""
        if self.is_realm_max():
            return float("inf")
        return RealmRegistry.stage_exp_required(self.realm_def, self.stage)

    def progress_ratio(self) -> float:
        need = self.exp_required()
        if need == float("inf"):
            return 1.0
        return min(1.0, self.exp / need)

    def can_breakthrough(self) -> bool:
        return self.alive and self.exp >= self.exp_required()

    def next_target_name(self) -> str:
        """下一层的名字（用于突破提示）。"""
        if self.is_realm_max():
            return "已至绝巅"
        r = self.realm_def
        if self.is_stage_max():
            nxt = RealmRegistry.next_realm(self.realm_key)
            return f"{nxt.name}{nxt.stages[0]}" if nxt else "已至绝巅"
        return f"{r.name}{r.stages[self.stage + 1]}"

    def is_major_breakthrough(self) -> bool:
        """是否跨大境界（如炼气九层 -> 筑基初期）。"""
        return self.is_stage_max()

    # ---------- 属性快捷访问 ----------
    @property
    def max_hp(self) -> float:
        return self.attributes.value(MAX_HP)

    @property
    def max_mp(self) -> float:
        return self.attributes.value(MAX_MP)

    @property
    def atk(self) -> float:
        return self.attributes.value("atk")

    @property
    def defense(self) -> float:
        return self.attributes.value("def")

    @property
    def speed(self) -> float:
        return self.attributes.value("speed")

    @property
    def comprehension(self) -> float:
        return self.attributes.value(COMPREHENSION)

    @property
    def physique(self) -> float:
        return self.attributes.value(PHYSIQUE)

    @property
    def luck(self) -> float:
        return self.attributes.value(LUCK)

    @property
    def lifespan(self) -> int:
        return self.realm_def.lifespan

    # ---------- 每日限额（防刷） ----------
    def daily_used(self, day: int, path: str) -> int:
        """某途径当天已用次数。"""
        return int(self.daily.get(str(day), {}).get(path, 0))

    def daily_left(self, day: int, path: str, limit: int) -> int:
        return max(0, int(limit) - self.daily_used(day, path))

    def bump_daily(self, day: int, path: str, amount: int = 1) -> int:
        """累加某途径当天用量，返回累加后的次数。"""
        key = str(day)
        slot = self.daily.setdefault(key, {})
        slot[path] = int(slot.get(path, 0)) + int(amount)
        self._trim_daily(day)
        return slot[path]

    def _trim_daily(self, current_day: int) -> None:
        """只保留最近 3 天的计数，避免存档无谓膨胀。"""
        keep = {str(d) for d in range(max(0, current_day - 2), current_day + 1)}
        self.daily = {k: v for k, v in self.daily.items() if k in keep}

    # ---------- 状态变化 ----------
    def add_exp(self, amount: float) -> float:
        """增加修为，返回实际增加量（满层后不再累积）。"""
        need = self.exp_required()
        if need == float("inf"):
            return 0.0
        real = min(amount, max(0.0, need - self.exp))
        self.exp += real
        self.total_exp += real
        return real

    def heal_hp(self, amount: float) -> float:
        before = self.hp
        self.hp = min(self.max_hp, self.hp + amount)
        return self.hp - before

    def heal_mp(self, amount: float) -> float:
        before = self.mp
        self.mp = min(self.max_mp, self.mp + amount)
        return self.mp - before

    def damage(self, amount: float, reason: str = "") -> None:
        self.hp = max(0.0, self.hp - amount)
        if self.hp <= 0:
            self.alive = False

    def spend_stamina(self, amount: float) -> bool:
        if self.stamina < amount:
            return False
        self.stamina -= amount
        return True

    def full_restore(self) -> None:
        self.hp = self.max_hp
        self.mp = self.max_mp
        self.stamina = MAX_STAMINA

    # ---------- 序列化 ----------
    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "realm_key": self.realm_key,
            "stage": self.stage,
            "exp": round(self.exp, 2),
            "total_exp": round(self.total_exp, 2),
            "hp": round(self.hp, 2),
            "mp": round(self.mp, 2),
            "stamina": round(self.stamina, 2),
            "attributes": self.attributes.to_dict(),
            "inventory": self.inventory.to_dict(),
            "equipment": dict(self.equipment),
            "spirit_stones": self.spirit_stones,
            "pill_poison": round(self.pill_poison, 2),
            "age": self.age,
            "daily": {day: dict(paths) for day, paths in self.daily.items()},
            "flags": dict(self.flags),
            "alive": self.alive,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Cultivator":
        obj = cls(
            name=data["name"],
            realm_key=data["realm_key"],
            stage=int(data["stage"]),
            attributes=AttributeSet.from_dict(data["attributes"]),
        )
        obj.exp = float(data.get("exp", 0))
        obj.total_exp = float(data.get("total_exp", 0))
        obj.hp = float(data.get("hp", obj.max_hp))
        obj.mp = float(data.get("mp", obj.max_mp))
        obj.stamina = float(data.get("stamina", MAX_STAMINA))
        obj.inventory = Inventory.from_dict(data.get("inventory", {}))
        # 老档没有 equipment 字段，用空字典兜底，无需版本迁移
        obj.equipment = dict(data.get("equipment", {}))
        obj.spirit_stones = int(data.get("spirit_stones", 0))
        obj.pill_poison = float(data.get("pill_poison", 0))
        obj.age = int(data.get("age", 16))
        # 老档没有 daily 字段，用空字典兜底（不升 SAVE_VERSION）
        obj.daily = {day: dict(paths) for day, paths in (data.get("daily") or {}).items()}
        obj.flags = dict(data.get("flags", {}))
        obj.alive = bool(data.get("alive", True))
        obj.clamp_state()          # 反序列化层兜住脏数据：非法境界/越界层数/负值一律夹紧
        return obj


def roll_cultivator(name: str, rng, fixed: dict[str, float] | None = None) -> Cultivator:
    """创建初始角色：三维（悟性/根骨/气运）随机，其余固定。"""
    attrs = AttributeSet()
    fixed = fixed or {}
    for key, low, high in ((COMPREHENSION, 5, 20), (PHYSIQUE, 5, 20), (LUCK, 5, 20)):
        attrs.base[key] = float(fixed.get(key, rng.randint(low, high)))

    c = Cultivator(name=name, attributes=attrs)
    c.inventory.add("qi_gathering_pill", 3)
    c.inventory.add("healing_pill", 2)
    c.inventory.add("jade_bottle", 1)
    c.full_restore()
    return c
