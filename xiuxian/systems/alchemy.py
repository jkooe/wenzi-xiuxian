"""炼丹系统（丹道）。

五条规则：
    1. 丹方 —— 开局只会两张粗浅丹方，其余用灵石换或靠机缘（也是后期灵石的一大去处）
    2. 开炉 —— 交材料、耗精力灵力与时间，掷一次点定成败
    3. 品阶 —— 掷点低于成功率即成丹，低得越多火候越足：
               品质值 = 归一余量 + 火候修正（造诣 + 丹炉）
               ≥66% 上品、≥33% 中品，其余下品。药效 140% / 100% / 70%
    4. 不炸炉 —— 失手只损材料（每种退回一半，向下取整），不损装备、不掉修为境界
    5. 造诣 —— 每成一炉涨 1 点、失手涨 0.4 点，每点 +2% 成功率，上限 12 点（+24%）

成功率构成对外完全可见（dan refine 每次开炉都会把算式打出来）：
    丹方基础 + 造诣 + 悟性 + 气运 + 丹炉，最后夹在 [5%, 95%]

与其余系统的接点：
    丹毒（inventory）—— 上品药力猛、丹毒也重，清毒丹方专治嗑药过度
    突破加成（cultivation）—— 自产筑基丹/结金丹，不必全靠买
    效果 DSL —— 注册 "recipe"，事件 JSON 里写 {"type":"recipe","id":"core_pill"} 即可传丹方
"""

from __future__ import annotations

from typing import Any

from ..config import items as item_config
from ..config import recipes as recipe_config
from ..config.realms import RealmRegistry, power_of
from ..core.base_system import Command, GameSystem

# ---------- 成功率构成（全部可调）----------
MASTERY_PER_SUCCESS = 1.0      # 每成一炉涨 1 点造诣
MASTERY_PER_FAILURE = 0.4      # 失手也有进境，只是慢
MASTERY_RATE_BONUS = 0.02      # 每点造诣 +2% 成功率
MASTERY_CAP = 12.0             # 造诣上限（+24%）
COMPREHENSION_REF = 10.0       # 悟性基准线，高于此有加成
COMPREHENSION_WEIGHT = 0.015   # 每点悟性 ±1.5%
LUCK_WEIGHT = 0.004            # 每点气运 +0.4%
RATE_MIN, RATE_MAX = 0.05, 0.95

# ---------- 品阶判定 ----------
# 掷点在 [0, 成功率] 上均匀分布，所以归一余量本身均匀——品阶若只看余量，
# 造诣与丹炉就只影响成败、不影响丹的品质。这里再叠一层「火候修正」：
#     品质值 = 归一余量 + 造诣加成 + 丹炉加成
# 分工明确：成败看悟性气运，火候（品阶）看手艺与家伙。
QUALITY_PER_MASTERY = 0.02     # 每点造诣 +2% 品质值
QUALITY_CAP = 0.20             # 火候修正上限（+20%）
GRADE_MARGIN_HIGH = 0.66       # 品质值 ≥ 此值出上品
GRADE_MARGIN_MID = 0.33        # ≥ 此值出中品，否则下品

REFUND_DIVISOR = 2             # 失手退还：每种材料退回 count // 2
FAILURE_EXP_RATIO = 0.3        # 失手仍得三成修为，权作火候上的领悟
MAX_BATCH = 20                 # 一次最多连开几炉

# ---------- 采药 ----------
GATHER_STAMINA_PER_HOUR = 6.0
GATHER_MP_PER_HOUR = 2.0
GATHER_HERB_PER_HOUR = 0.6     # 基准产出，再乘地点灵气与气运
GATHER_EXP_PER_HOUR = 2.0
GATHER_RARE_BASE = 0.12        # 采到稀有材料的基础概率
GATHER_MAX_HOURS = 12.0


class AlchemySystem(GameSystem):
    id = "alchemy"
    name = "丹道"

    def __init__(self) -> None:
        super().__init__()
        self.known: set[str] = set(recipe_config.STARTING_RECIPES)
        self.mastery: dict[str, float] = {}   # recipe_id -> 造诣（浮点，失手也算 0.4 点）
        self.refined = 0
        self.success = 0
        self.fail = 0

    # ---------- 装配 ----------
    def on_bind(self) -> None:
        self.game.register_effect("recipe", self.effect_recipe)

    # ---------- 丹方 ----------
    def learn(self, recipe_id: str, free: bool = False) -> list[str]:
        """领悟一张丹方。free=True 时不收灵石（事件传方用）。"""
        p = self.player
        try:
            recipe = recipe_config.get_recipe(recipe_id)
        except KeyError:
            return [f"世间并无「{recipe_id}」这张丹方。"]
        if recipe.id in self.known:
            return [f"《{recipe.name}》早已烂熟于心。"]
        if not free:
            if recipe.price <= 0:
                return [f"《{recipe.name}》无处可购，只能靠机缘。"]
            if p.spirit_stones < recipe.price:
                return [f"灵石不足，《{recipe.name}》需 {recipe.price} 灵石"
                        f"（现有 {p.spirit_stones}）。"]
            p.spirit_stones -= recipe.price

        self.known.add(recipe.id)
        out = item_config.get_item(recipe.output)
        logs = [
            f"参悟《{recipe.name}》。" if free
            else f"以 {recipe.price} 灵石换得《{recipe.name}》。"
        ]
        logs.append(f"  可炼：{out.name}　基础成功率 {recipe.base_rate:.0%}"
                    f"　每炉 {recipe.hours:g} 时辰")
        return logs

    # ---------- 成功率 ----------
    def furnace_bonus(self) -> float:
        """身上的丹炉提供的成功率加成（法宝位与葫芦二选一，是个取舍）。"""
        total = 0.0
        for item_id in self.player.equipment.values():
            if item_config.has_item(item_id):
                total += item_config.get_item(item_id).alchemy_bonus
        return total

    def rate_parts(self, recipe) -> list[tuple[str, float]]:
        """拆出每一项加成，供展示与自检（逻辑可解释：公式不藏在黑箱里）。"""
        p = self.player
        m = min(MASTERY_CAP, self.mastery.get(recipe.id, 0.0))
        parts: list[tuple[str, float]] = [
            ("丹方", recipe.base_rate),
            ("造诣", m * MASTERY_RATE_BONUS),
            ("悟性", (p.comprehension - COMPREHENSION_REF) * COMPREHENSION_WEIGHT),
            ("气运", p.luck * LUCK_WEIGHT),
            ("丹炉", self.furnace_bonus()),
        ]
        # 境界神通：金丹「丹心」炼丹成功率 +6%
        power = power_of(p.realm_key)
        if power and power.alchemy_bonus:
            parts.append((f"神通·{power.name}", power.alchemy_bonus))
        return parts

    def rate_of(self, recipe) -> tuple[float, str]:
        """返回 (成功率, 算式文本)。"""
        parts = self.rate_parts(recipe)
        raw = sum(v for _, v in parts)
        rate = max(RATE_MIN, min(RATE_MAX, raw))
        text = f"{rate:.0%}（" + "　".join(
            [f"丹方 {parts[0][1]:.0%}"]
            + [f"{label} {v:+.0%}" for label, v in parts[1:] if abs(v) >= 0.005]
        ) + "）"
        return rate, text

    # ---------- 开炉 ----------
    def refine(self, recipe_id: str, times: int = 1) -> list[str]:
        p = self.player
        if not p.alive:
            return ["你已身死道消。"]
        try:
            recipe = recipe_config.get_recipe(recipe_id)
        except KeyError:
            return [f"并无「{recipe_id}」这张丹方。（dan list 查看）"]
        if recipe.id not in self.known:
            return [f"你尚未领悟《{recipe.name}》。（dan learn {recipe.id}）"]
        if not RealmRegistry.within(p.realm_key, min_realm=recipe.min_realm):
            need = RealmRegistry.get(recipe.min_realm).name
            return [f"修为不足，此丹需 {need} 以上方能开炉。"]

        times = max(1, min(int(times), MAX_BATCH))
        out = item_config.get_item(recipe.output)
        logs = [f"架起丹炉，欲炼《{recipe.name}》→ {out.name}　共 {times} 炉"]

        made: dict[str, int] = {"high": 0, "mid": 0, "low": 0}
        failed = 0
        started = 0
        for i in range(times):
            if not p.alive:
                logs.append("气息断绝，炼丹中止。")
                break
            blocking = self._blocker(recipe)
            if blocking:
                logs.append(f"第 {i + 1} 炉停火：{blocking}")
                break
            logs.append(f"第 {i + 1} 炉：")
            grade, lines = self._refine_one(recipe)
            logs.extend(lines)
            started += 1
            if grade:
                made[grade] += 1
            else:
                failed += 1
            self.game.check_game_over()

        if started == 0:
            logs.append("收炉：一炉未开。")
            return logs
        # 日常追踪：炼丹（开过炉就算）
        daily = self.game.systems.get("daily")
        if daily is not None:
            daily.track("refine")
        summary = [f"{item_config.GRADES[g].label} {made[g]} 枚"
                   for g in item_config.GRADE_ORDER if made[g]]
        if failed:
            summary.append(f"废 {failed} 炉")
        logs.append("收炉：" + "、".join(summary)
                    + f"（点火 {started}/{times} 炉"
                    + f"，累计开炉 {self.refined}，成 {self.success} 废 {self.fail}）")
        return logs

    def _blocker(self, recipe) -> str:
        """返回不可开炉的原因，空串表示可以继续。"""
        p = self.player
        for item_id, count in recipe.inputs.items():
            if not p.inventory.has(item_id, count):
                need = item_config.get_item(item_id).name
                return f"{need}不足（需 {count}，现有 {p.inventory.count(item_id)}）"
        if p.stamina < recipe.stamina:
            return f"精力不济（需 {recipe.stamina:.0f}，现有 {p.stamina:.0f}），先 rest 歇息"
        if p.mp < recipe.mp:
            return f"灵力不济（需 {recipe.mp:.0f}，现有 {p.mp:.0f}），先 rest 歇息"
        return ""

    def _refine_one(self, recipe) -> tuple[str | None, list[str]]:
        """开一炉。返回 (品阶 or None, 日志)。"""
        p = self.player
        rate, text = self.rate_of(recipe)

        for item_id, count in recipe.inputs.items():
            p.inventory.remove(item_id, count)
        p.spend_stamina(recipe.stamina)
        p.mp = max(0.0, p.mp - recipe.mp)
        self.refined += 1

        roll = self.game.rng.rand()
        detail = f"　成功率 {text}　掷点 {roll:.3f}"

        if roll < rate:
            margin = (rate - roll) / max(1e-6, rate)
            quality, q_extra = self._quality(recipe, margin)
            if quality >= GRADE_MARGIN_HIGH:
                grade = "high"
            elif quality >= GRADE_MARGIN_MID:
                grade = "mid"
            else:
                grade = "low"
            out_id = item_config.graded_id(recipe.output, grade)
            p.inventory.add(out_id, 1)
            self.success += 1
            self._gain_mastery(recipe.id, MASTERY_PER_SUCCESS)
            gained = p.add_exp(recipe.exp)

            g = item_config.GRADES[grade]
            lines = ["　火候渐足，炉中隐隐有光。" + detail,
                     f"　成丹！{item_config.get_item(out_id).name} ×1"
                     f"（{g.label}，药力 {g.potency:.0%}）"
                     f"　品质 {quality:.0%} ＝ 余量 {margin:.0%}"
                     + (f" ＋ 火候 {q_extra:+.0%}" if q_extra >= 0.005 else ""),
                     f"　《{recipe.name}》造诣 {self.mastery[recipe.id]:.1f}"
                     f"（成功率 +{min(MASTERY_CAP, self.mastery[recipe.id]) * MASTERY_RATE_BONUS:.0%}"
                     f"　火候 +{min(QUALITY_CAP, self.mastery[recipe.id] * QUALITY_PER_MASTERY):.0%}）"]
            if gained >= 0.5:
                lines.append(f"　修为 +{gained:.0f}")
        else:
            self.fail += 1
            self._gain_mastery(recipe.id, MASTERY_PER_FAILURE)
            gained = p.add_exp(recipe.exp * FAILURE_EXP_RATIO)
            lines = ["　丹炉一声闷响，火光骤灭。" + detail,
                     "　药力散尽，好在炉子无恙。"]
            refunds = []
            for item_id, count in recipe.inputs.items():
                back = count // REFUND_DIVISOR
                if back:
                    p.inventory.add(item_id, back)
                    refunds.append(f"{item_config.get_item(item_id).name} ×{back}")
            if refunds:
                lines.append("　从炉灰中捡回：" + "、".join(refunds))
            lines.append(f"　《{recipe.name}》造诣 {self.mastery[recipe.id]:.1f}"
                         f"（虽未成丹，火候上总算有了点心得）")
            if gained >= 0.5:
                lines.append(f"　修为 +{gained:.0f}")
            grade = None

        lines.extend(self.game.advance_time(recipe.hours))
        return grade, lines

    def _quality(self, recipe, margin: float) -> tuple[float, float]:
        """品质值 = 归一余量 + 火候修正（造诣 + 丹炉），返回 (品质值, 火候修正)。"""
        extra = min(QUALITY_CAP,
                    self.mastery.get(recipe.id, 0.0) * QUALITY_PER_MASTERY
                    + self.furnace_bonus())
        return max(0.0, min(1.0, margin + extra)), extra

    def _gain_mastery(self, recipe_id: str, amount: float) -> None:
        self.mastery[recipe_id] = min(MASTERY_CAP,
                                      self.mastery.get(recipe_id, 0.0) + amount)

    # ---------- 采药 ----------
    def gather(self, hours: float = 4.0) -> list[str]:
        """外出采药：丹道的上游，没有它炼丹就是无米之炊。"""
        p = self.player
        if not p.alive:
            return ["你已身死道消。"]
        hours = max(1.0, min(float(hours), GATHER_MAX_HOURS))
        if p.stamina < GATHER_STAMINA_PER_HOUR:
            return ["精力枯竭，先歇息吧。（rest 4）"]
        if p.mp < GATHER_MP_PER_HOUR:
            return ["灵力不济，难以辨识药性。（rest 4）"]

        real = min(hours,
                   p.stamina / GATHER_STAMINA_PER_HOUR,
                   p.mp / GATHER_MP_PER_HOUR)
        p.spend_stamina(real * GATHER_STAMINA_PER_HOUR)
        p.mp = max(0.0, p.mp - real * GATHER_MP_PER_HOUR)

        info = self.game.location_info()
        density = info["density"]
        expected = real * GATHER_HERB_PER_HOUR * density * (1 + p.luck / 200.0)
        herbs = int(expected)
        if self.game.rng.chance(expected - herbs):
            herbs += 1

        logs = [f"于{self.game.location}四下寻药 {real:.0f} 时辰。"]
        if herbs > 0:
            p.inventory.add("spirit_herb", herbs)
            logs.append(f"　采得 灵草 ×{herbs}")
        else:
            logs.append("　走了半日，只采到几把枯草。")

        if self.game.rng.chance(GATHER_RARE_BASE * (1 + p.luck / 100.0) * (density / 2.0)):
            got = self.game.rng.choice(self._rare_pool(density))
            p.inventory.add(got, 1)
            logs.append(f"　运气不错，竟在草丛里觅得 {item_config.get_item(got).name} ×1！")

        # v2：凶险机制已取消——采药不再有遇险伤害（地点只影响灵气与机缘率）

        gained = p.add_exp(real * GATHER_EXP_PER_HOUR * density)
        if gained >= 0.5:
            logs.append(f"　修为 +{gained:.0f}")
        logs.extend(self.game.advance_time(real))
        return logs

    @staticmethod
    def _rare_pool(density: float) -> list[str]:
        """越凶险的地方越可能采到好东西；灵泉水只在灵气最盛处可得。"""
        if density < 1.5:
            return ["beast_blood"]
        if density < 2.5:
            return ["beast_blood", "beast_blood", "beast_core"]
        return ["beast_blood", "beast_core", "beast_core", "spirit_water"]

    # ---------- 展示 ----------
    def info(self) -> list[str]:
        total = self.success + self.fail
        lines = ["丹道："]
        lines.append(f"　已悟丹方 {len(self.known)}/{len(recipe_config.RECIPES)}"
                     f"　开炉 {self.refined} 次"
                     + (f"　成丹率 {self.success / total:.0%}" if total else ""))
        bonus = self.furnace_bonus()
        lines.append(f"　丹炉加成 {bonus:+.0%}"
                     + ("" if bonus else "（未携带丹炉，dan 前先 equip 一件）"))
        if not self.known:
            lines.append("　你一张丹方也不识得。")
            return lines

        lines.append("　已悟丹方（dan refine <id> [炉数] 开炉）：")
        for rid in sorted(self.known, key=lambda r: -self.mastery.get(r, 0.0)):
            recipe = recipe_config.get_recipe(rid)
            rate, _ = self.rate_of(recipe)
            m = self.mastery.get(rid, 0.0)
            out = item_config.get_item(recipe.output)
            lines.append(f"　　《{recipe.name}》 → {out.name}"
                         f"　造诣 {m:.1f}/{MASTERY_CAP:.0f} {self._bar(m)}"
                         f"　现成功率 {rate:.0%}")
            lines.append(f"　　　{self._materials(recipe)}")
        return lines

    def catalog(self) -> list[str]:
        p = self.player
        lines = ["丹方目录（dan learn <id> 换购，dan refine <id> 开炉）："]
        for recipe in sorted(recipe_config.RECIPES.values(),
                             key=lambda r: (r.price, r.base_rate)):
            if recipe.id in self.known:
                state = f"已悟（造诣 {self.mastery.get(recipe.id, 0.0):.1f}）"
            elif recipe.price <= 0:
                state = "机缘方得"
            elif not RealmRegistry.within(p.realm_key, min_realm=recipe.min_realm):
                need = RealmRegistry.get(recipe.min_realm).name
                state = f"修为不足（需{need}）"
            elif p.spirit_stones < recipe.price:
                state = f"灵石不足（{recipe.price}）"
            else:
                state = f"可换购（{recipe.price} 灵石）"
            out = item_config.get_item(recipe.output)
            lines.append(f"　《{recipe.name}》 → {out.name}　基础 {recipe.base_rate:.0%}　[{state}]")
            lines.append(f"　　　{recipe.desc}")
            lines.append(f"　　　{self._materials(recipe)}"
                         f"　每炉 {recipe.hours:g} 时辰　精力 {recipe.stamina:.0f}　灵力 {recipe.mp:.0f}"
                         f"　{recipe.id}")
        return lines

    @staticmethod
    def _materials(recipe) -> str:
        return "＋".join(f"{item_config.get_item(i).name}×{c}"
                        for i, c in recipe.inputs.items())

    @staticmethod
    def _bar(value: float, maximum: float = MASTERY_CAP, width: int = 12) -> str:
        filled = int(round(max(0.0, min(1.0, value / max(1.0, maximum))) * width))
        return f"[{'█' * filled}{'·' * (width - filled)}]"

    # ---------- 效果 DSL ----------
    def effect_recipe(self, player, eff: dict) -> list[str]:
        """事件 JSON 里写 {"type": "recipe", "id": "core_pill"} 即可传丹方。"""
        rid = eff.get("id", "")
        if rid not in recipe_config.RECIPES:
            return [f"[未知丹方: {rid}]"]
        if rid in self.known:
            self._gain_mastery(rid, float(eff.get("mastery", 3.0)))
            return [f"《{recipe_config.get_recipe(rid).name}》早已悟得，"
                    f"此番印证，造诣增至 {self.mastery[rid]:.1f}。"]
        return self.learn(rid, free=True)

    # ---------- 命令 ----------
    def commands(self) -> list[Command]:
        def _dan(args: list[str]) -> None:
            sub = args[0].lower() if args else ""
            if sub in ("list", "all", "目录"):
                self.game.emit_logs(self.catalog())
            elif sub in ("learn", "悟", "换购"):
                if len(args) < 2:
                    self.log("用法：dan learn <丹方id>（dan list 查看目录）")
                    return
                self.game.emit_logs(self.learn(args[1]))
            elif sub in ("refine", "炼", "开炉"):
                if len(args) < 2:
                    self.log("用法：dan refine <丹方id> [炉数]（dan 查看已悟丹方）")
                    return
                times = int(args[2]) if len(args) > 2 and args[2].isdigit() else 1
                self.game.emit_logs(self.refine(args[1], times))
            elif sub in ("gather", "采药", "采"):
                hours = float(args[1]) if len(args) > 1 else 4.0
                self.game.emit_logs(self.gather(hours))
            else:
                self.game.emit_logs(self.info())
                self.log("  dan list 丹方目录　dan learn <id> 换购　"
                         "dan refine <id> [炉数] 开炉　dan gather [时辰] 采药")

        return [
            Command("dan", "丹道（list/learn/refine/gather）", "dan [list|learn|refine|gather]", _dan),
        ]

    # ---------- 持久化 ----------
    def to_dict(self) -> dict[str, Any]:
        return {
            "known": sorted(self.known),
            "mastery": {k: round(v, 2) for k, v in self.mastery.items()},
            "refined": self.refined,
            "success": self.success,
            "fail": self.fail,
        }

    def load_state(self, data: dict[str, Any]) -> None:
        self.known = {r for r in (data.get("known") or []) if r in recipe_config.RECIPES}
        self.known |= set(recipe_config.STARTING_RECIPES)
        self.mastery = {k: float(v) for k, v in (data.get("mastery") or {}).items()
                        if k in recipe_config.RECIPES}
        self.refined = int(data.get("refined", 0))
        self.success = int(data.get("success", 0))
        self.fail = int(data.get("fail", 0))
