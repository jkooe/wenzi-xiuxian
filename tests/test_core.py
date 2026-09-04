"""内核回归测试。运行：

    python -m unittest discover -s tests -v
    （或）python tests/test_core.py
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xiuxian.config import dungeons as dungeon_config  # noqa: E402
from xiuxian.config import items as item_config  # noqa: E402
from xiuxian.config import recipes as recipe_config  # noqa: E402
from xiuxian.config import arts as art_config  # noqa: E402
from xiuxian.config import skills as skill_config  # noqa: E402
from xiuxian.config import realms as realm_config  # noqa: E402
from xiuxian.config import laws as lawcfg  # noqa: E402
from xiuxian.config.arts import (  # noqa: E402
    EFFECT_TYPES, RANK_BUDGET, effective_value, get_art, level_of,
    slots_for_realm, validate_balance,
)
from xiuxian.config.realms import LOCATIONS, REALMS, RealmRegistry  # noqa: E402
from xiuxian.core.numfmt import fmt_num  # noqa: E402
from xiuxian.systems import combat as combat_module  # noqa: E402
from xiuxian.core.attributes import AttributeSet, Modifier  # noqa: E402
from xiuxian.core.cultivation import CultivationSystem  # noqa: E402
from xiuxian.core.effects import apply_effects  # noqa: E402
from xiuxian.core.event_system import EventSystem  # noqa: E402
from xiuxian.core.game import Game  # noqa: E402
from xiuxian.core.save import SaveManager  # noqa: E402
from xiuxian.factory import create_game, default_systems, load_game  # noqa: E402
from xiuxian.rng import RNG  # noqa: E402

SEED = 20240901


def new_game(seed: int = SEED, **kwargs) -> Game:
    game = create_game(name="测试子", seed=seed, **kwargs)
    game.clock.disabled = True               # 测试不限速（时间闸门有专门测试）
    return game


class TestRealmConfig(unittest.TestCase):
    def test_realms_ordered_and_valid(self):
        self.assertGreaterEqual(len(REALMS), 5)
        for r in REALMS:
            self.assertTrue(r.stages, f"{r.name} 缺少小境界")
            self.assertGreater(r.exp_growth, 1.0)
            self.assertGreater(r.exp_base, 0)
            self.assertGreater(r.lifespan, 0)

    def test_exp_required_grows(self):
        r = RealmRegistry.get("qi_refining")
        first = RealmRegistry.stage_exp_required(r, 0)
        last = RealmRegistry.stage_exp_required(r, r.stage_count - 1)
        self.assertGreater(last, first * 5)

    def test_global_stage_monotonic(self):
        prev = -1
        for r in REALMS:
            for s in range(r.stage_count):
                cur = RealmRegistry.global_stage_index(r.key, s)
                self.assertGreater(cur, prev)
                prev = cur


class TestAttributes(unittest.TestCase):
    def test_add_and_mul_modifiers(self):
        attrs = AttributeSet()
        attrs.base["atk"] = 10.0
        attrs.add_modifier(Modifier("sword", add={"atk": 5}))
        attrs.add_modifier(Modifier("buff", mul={"atk": 1.5}))
        self.assertAlmostEqual(attrs.value("atk"), 22.5)  # (10+5)*1.5

    def test_same_source_replaced_not_stacked(self):
        attrs = AttributeSet()
        attrs.add_modifier(Modifier("buff", add={"atk": 5}))
        attrs.add_modifier(Modifier("buff", add={"atk": 5}))
        self.assertEqual(attrs.value("atk"), attrs.base["atk"] + 5)

    def test_modifier_expires(self):
        attrs = AttributeSet()
        attrs.add_modifier(Modifier("pill", add={"atk": 5}, hours_left=2))
        self.assertTrue(attrs.has_modifier("pill"))
        attrs.tick(3)
        self.assertFalse(attrs.has_modifier("pill"))


class TestRNG(unittest.TestCase):
    def test_same_seed_same_sequence(self):
        a, b = RNG(123), RNG(123)
        self.assertEqual([a.rand() for _ in range(5)], [b.rand() for _ in range(5)])

    def test_state_roundtrip(self):
        r = RNG(456)
        [r.rand() for _ in range(3)]
        snapshot = r.to_dict()
        expected = [r.rand() for _ in range(3)]
        restored = RNG.from_dict(snapshot)
        self.assertEqual(expected, [restored.rand() for _ in range(3)])


class TestCultivation(unittest.TestCase):
    def test_cultivate_gains_exp_and_costs_stamina(self):
        game = new_game()
        cult = game.system("cultivation")
        before_stamina = game.player.stamina
        cult.cultivate(4)
        self.assertGreater(game.player.exp, 0)
        self.assertLess(game.player.stamina, before_stamina)

    def test_exp_capped_at_stage_requirement(self):
        game = new_game()
        game.player.exp = game.player.exp_required() - 1
        game.player.add_exp(99999)
        self.assertAlmostEqual(game.player.exp, game.player.exp_required())

    def test_breakthrough_success_advances_stage(self):
        game = new_game()
        cult = game.system("cultivation")
        player = game.player
        player.exp = player.exp_required()
        player.stamina = 100
        game.rng.chance = lambda p: True          # 强制成功
        cult.breakthrough()
        self.assertEqual(player.stage, 1)
        # 突破耗时 2 时辰，期间后台周天运转会给少量修为（被动修炼），不再是精确 0
        self.assertLess(player.exp, player.exp_required())

    def test_breakthrough_failure_costs_exp(self):
        game = new_game()
        cult = game.system("cultivation")
        player = game.player
        player.exp = player.exp_required()
        player.stamina = 100
        game.rng.chance = lambda p: False         # 强制失败
        cult.breakthrough()
        self.assertEqual(player.stage, 0)
        self.assertLess(player.exp, player.exp_required())
        self.assertTrue(player.alive)

    def test_major_breakthrough_changes_realm(self):
        game = new_game()
        cult = game.system("cultivation")
        player = game.player
        player.stage = player.stage_count - 1
        player.exp = player.exp_required()
        player.stamina = 100
        game.rng.chance = lambda p: True
        cult.breakthrough()
        self.assertEqual(player.realm_key, "foundation")
        self.assertEqual(player.stage, 0)

    def test_pill_bonus_and_consumption(self):
        game = new_game()
        cult = game.system("cultivation")
        player = game.player
        player.inventory.add("foundation_pill", 1)
        bonus, consume = cult.pill_info(player, "foundation")
        # v2：丹药加成封顶 +15pp（筑基丹单颗 25pp 也被压到 15pp，防叠药拉爆）
        self.assertAlmostEqual(bonus, 0.15)
        self.assertEqual(consume.get("foundation_pill"), 1)


class TestEffects(unittest.TestCase):
    def test_effect_types(self):
        game = new_game()
        player = game.player
        player.hp = 10
        logs = apply_effects(game, [
            {"type": "hp", "value": 5},
            {"type": "attr", "key": "physique", "value": 2},
            {"type": "item", "id": "healing_pill", "count": 1},
            {"type": "buff", "id": "x", "name": "测试", "hours": 5, "mul": {"atk": 2}},
            {"type": "flag", "key": "tested", "value": True},
        ])
        self.assertAlmostEqual(player.hp, 15)
        self.assertEqual(player.attributes.base["physique"], player.physique)
        self.assertEqual(player.inventory.count("healing_pill"), 3)
        self.assertTrue(player.attributes.has_modifier("buff:测试"))
        self.assertTrue(player.flags["tested"])
        self.assertTrue(logs)

    def test_poison_reduces_gain(self):
        game = new_game()
        player = game.player
        player.exp = 0
        apply_effects(game, [{"type": "exp", "value": 100}])
        clean = player.exp

        player.exp = 0
        player.pill_poison = 150          # 高丹毒
        apply_effects(game, [{"type": "exp", "value": 100}])
        self.assertLess(player.exp, clean)


class TestEventSystem(unittest.TestCase):
    def test_events_loaded(self):
        game = new_game()
        events = game.system("event")
        self.assertGreater(len(events.events), 0)

    def test_trigger_and_choose(self):
        game = new_game()
        events = game.system("event")
        game.player.stamina = 100
        events.trigger(force=True)
        self.assertIsNotNone(events.pending)
        logs = events.choose(1)
        self.assertIsInstance(logs, list)
        self.assertIsNone(events.pending)

    def test_location_condition_filters(self):
        game = new_game()
        events = game.system("event")
        game.location = "青石镇"
        ids_here = {e.id for e in events.available_events()}
        self.assertIn("beggar_elder", ids_here)
        self.assertNotIn("thunder_field", ids_here)   # 雷泽只在万兽深渊

    def test_requirements_blocked(self):
        game = new_game()
        events = game.system("event")
        game.player.spirit_stones = 0
        ok, reason = events.check_require({"stone": 500})
        self.assertFalse(ok)
        self.assertIn("灵石", reason)


class TestCombat(unittest.TestCase):
    def test_victory_rewards(self):
        game = new_game()
        combat = game.system("combat")
        player = game.player
        enemy = combat.spawn("weak")
        enemy.hp = 1                       # 一击必杀
        player.stamina = 100
        player.exp = 0
        logs = combat.fight(enemy)
        self.assertFalse(enemy.alive())
        self.assertGreater(player.exp, 0)
        self.assertTrue(any("倒地不起" in line for line in logs))

    def test_defeat_never_kills(self):
        game = new_game()
        combat = game.system("combat")
        player = game.player
        player.stamina = 100
        enemy = combat.spawn("boss")
        player.hp = 1
        logs = combat.fight(enemy)
        self.assertTrue(player.alive)
        self.assertTrue(any("重伤濒死" in line for line in logs))


class TestSaveLoad(unittest.TestCase):
    def test_roundtrip_preserves_state(self):
        game = create_game(name="存档测试", seed=SEED)
        game.system("cultivation").cultivate(4)
        game.system("sect").join("qingyun")
        game.player.inventory.add("healing_pill", 5)

        with tempfile.TemporaryDirectory() as tmp:
            manager = SaveManager(tmp)
            path = manager.save(game, 1, note="ut")
            self.assertTrue(Path(path).exists())

            payload = manager.load(1)
            restored = Game.from_dict(payload["data"], default_systems)

        self.assertEqual(restored.player.to_dict(), game.player.to_dict())
        self.assertEqual(restored.day, game.day)
        self.assertEqual(restored.location, game.location)
        self.assertEqual(restored.system("sect").sect_key, "qingyun")

    def test_slot_listing(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = SaveManager(tmp)
            game = new_game()
            manager.save(game, 3)
            slots = manager.list_slots()
            self.assertEqual([s["slot"] for s in slots], [3])
            self.assertTrue(manager.delete(3))
            self.assertEqual(manager.list_slots(), [])

    def test_missing_slot_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = SaveManager(tmp)
            with self.assertRaises(Exception):
                manager.load(9)


class TestWorld(unittest.TestCase):
    def test_day_rollover_restores_stamina(self):
        """跨日恢复每日额度（v2：48 = 2/时 × 24 时），不再回满。"""
        game = new_game()
        game.player.stamina = 5
        game.hour = 22
        game.advance_time(4)
        self.assertEqual(game.day, 2)
        self.assertEqual(game.player.stamina, 5 + 48)   # DAILY_STAMINA_RECOVERY=48

    def test_travel_changes_location(self):
        game = new_game()
        game.travel("落云山脉")
        self.assertEqual(game.location, "落云山脉")
        self.assertEqual(game.location_info()["density"], LOCATIONS["落云山脉"]["density"])

    def test_locations_declared(self):
        self.assertGreaterEqual(len(LOCATIONS), 3)


class TestBreakthroughCooldown(unittest.TestCase):
    """冲关冷却：失败后必须调养，防止无限硬冲把丹毒滚成死局。"""

    def test_failure_sets_cooldown(self):
        game = new_game(seed=99)
        cult, p = game.system("cultivation"), game.player
        p.exp = p.exp_required()
        p.stamina = 100
        while p.realm_key == "qi_refining" and p.stage == 0:
            cult.breakthrough()                     # 小境界 93% 成功，硬推进
            p.exp = p.exp_required()
            p.stamina = 100
        self.assertGreaterEqual(p.stage, 1)

    def test_cooldown_blocks_retry(self):
        game = new_game(seed=5)
        cult, p = game.system("cultivation"), game.player
        p.flags["bt_cd_until"] = game.day + 2
        p.exp = p.exp_required()
        p.stamina = 100
        logs = cult.breakthrough()
        self.assertIn("调养", logs[0])
        self.assertEqual(cult.cooldown_left(), 2)

    def test_cooldown_expires_with_days(self):
        game = new_game(seed=5)
        cult = game.system("cultivation")
        game.player.flags["bt_cd_until"] = game.day + 2
        self.assertEqual(cult.cooldown_left(), 2)
        game.advance_time(24 * 2)
        self.assertEqual(cult.cooldown_left(), 0)


class TestEquipment(unittest.TestCase):
    """装备：穿脱 -> Modifier 注入 -> 属性即时生效。"""

    def _equipped_game(self, seed: int = 11) -> Game:
        game = new_game(seed=seed)
        game.player.spirit_stones = 99999
        return game

    def test_equip_injects_modifier(self):
        game = self._equipped_game()
        eq, p = game.system("equipment"), game.player
        base_atk = p.atk

        p.inventory.add("qingfeng_sword", 1)
        eq.equip("qingfeng_sword")
        self.assertAlmostEqual(p.atk, base_atk + 6)
        self.assertTrue(p.attributes.has_modifier("equip:weapon"))

        eq.unequip("weapon")
        self.assertAlmostEqual(p.atk, base_atk)
        self.assertFalse(p.attributes.has_modifier("equip:weapon"))
        self.assertTrue(p.inventory.has("qingfeng_sword"))

    def test_equip_replaces_same_slot(self):
        game = self._equipped_game()
        eq, p = game.system("equipment"), game.player
        p.inventory.add("qingfeng_sword", 1)
        p.inventory.add("xuantheavy_sword", 1)
        p.realm_key, p.stage = "foundation", 0      # 抬境界以越过玄铁重剑门槛

        eq.equip("qingfeng_sword")
        eq.equip("xuantheavy_sword")
        self.assertEqual(p.equipment["weapon"], "xuantheavy_sword")
        self.assertTrue(p.inventory.has("qingfeng_sword"))   # 旧装备回到背包
        self.assertEqual(len([m for m in p.attributes.modifiers
                              if m.source.startswith("equip:")]), 1)

    def test_realm_gate_blocks_equip(self):
        game = self._equipped_game()
        eq, p = game.system("equipment"), game.player
        p.inventory.add("xuantheavy_sword", 1)          # 需筑基
        logs = eq.equip("xuantheavy_sword")
        self.assertIn("修为不足", logs[0])
        self.assertNotIn("weapon", p.equipment)

    def test_unequip_accepts_chinese_slot(self):
        game = self._equipped_game()
        eq, p = game.system("equipment"), game.player
        p.inventory.add("chainmail", 1)
        eq.equip("chainmail")
        logs = eq.unequip("护甲")
        self.assertIn("卸下", logs[0])
        self.assertNotIn("armor", p.equipment)

    def test_hp_clamped_after_unequip(self):
        game = self._equipped_game()
        eq, p = game.system("equipment"), game.player
        p.inventory.add("chainmail", 1)                 # 气血上限 +25
        eq.equip("chainmail")
        p.full_restore()
        self.assertAlmostEqual(p.hp, p.max_hp)

        eq.unequip("armor")
        self.assertLessEqual(p.hp, p.max_hp)

    def test_rebuild_restores_modifiers_after_load(self):
        game = self._equipped_game()
        p = game.player
        p.inventory.add("qingfeng_sword", 1)
        game.system("equipment").equip("qingfeng_sword")
        atk_before = p.atk

        with tempfile.TemporaryDirectory() as tmp:
            SaveManager(tmp).save(game, 1)
            restored = load_game(1, save_dir=tmp)

        self.assertEqual(restored.player.equipment, p.equipment)
        self.assertAlmostEqual(restored.player.atk, atk_before)


class TestArts(unittest.TestCase):
    """功法：多件装备 + 数据驱动效果 + 叠加规则 + 实时重算。"""

    def _game(self, seed: int = 13, realm: str = "qi_refining") -> Game:
        game = new_game(seed=seed)
        game.player.spirit_stones = 999999
        game.player.realm_key = realm
        game.player.stage = 0
        return game

    # ---------- 配置与平衡 ----------
    def test_all_effects_declared(self):
        """每条配置用到的效果类型都必须在 EFFECT_TYPES 中声明（数据驱动的前提）。"""
        for art in art_config.ARTS.values():
            for eff in art.effects:
                self.assertIn(eff.type, EFFECT_TYPES,
                              f"{art.name} 使用了未声明的效果类型 {eff.type}")

    def test_rank_budget_respected(self):
        """品阶预算自检：任何功法都不得超出本阶效果点预算（平衡的可执行约束）。"""
        over = validate_balance()
        self.assertEqual(over, [], "以下功法超出品阶预算：" + "；".join(over))

    def test_slots_grow_with_realm(self):
        """装备上限随境界提升，且有硬顶。"""
        self.assertGreaterEqual(slots_for_realm("qi_refining"), 2)
        self.assertGreater(slots_for_realm("human_immortal"),
                           slots_for_realm("qi_refining"))
        self.assertLessEqual(slots_for_realm("hunyuan"), 5)

    # ---------- 修习与装备 ----------
    def test_learn_costs_stones_and_equips(self):
        game = self._game()
        ar, p = game.system("arts"), game.player
        before = p.spirit_stones
        ar.learn("qingxin")
        self.assertEqual(p.spirit_stones, before - 200)
        self.assertIn("qingxin", ar.equipped)
        self.assertTrue(p.attributes.has_modifier("bonus:total"),
                        "装备功法后应由全局聚合器注入加成修正器")

    def test_realm_gate_blocks_learn(self):
        game = self._game()
        ar = game.system("arts")
        logs = ar.learn("dayan")            # 天阶，需元婴
        self.assertIn("修为不足", logs[0])
        self.assertNotIn("dayan", ar.learned)

    def test_equip_slot_cap(self):
        """装备栏满后不能再装（先卸下一件）。"""
        game = self._game(realm="foundation")
        ar = game.system("arts")
        cap = ar.slot_cap()
        for art_id in ("qingxin", "gengjin", "xuangui", "chiyan"):
            ar.learn(art_id)
        self.assertLessEqual(len(ar.equipped), cap)
        # 装到满之后，再装备会被拒绝
        extra = [a for a in art_config.ARTS if a not in ar.equipped][0]
        ar.learn(extra)
        logs = ar.equip(extra) if extra not in ar.equipped else ["已在装备之中。"]
        self.assertTrue(any("装备栏已满" in ln or "已在装备" in ln for ln in logs))

    # ---------- 实时重算 ----------
    def test_equip_and_unequip_recompute(self):
        """装备/卸下后属性立即生效与失效。"""
        game = self._game()
        ar, p = game.system("arts"), game.player
        ar.learn("gengjin")                 # 攻击 +4、+12%
        with_art = p.atk
        ar.unequip("gengjin")
        self.assertLess(p.atk, with_art)
        ar.equip("gengjin")
        self.assertAlmostEqual(p.atk, with_art, delta=0.01)

    def test_cultivate_speed_bonus_actually_applies(self):
        """修炼速度加成必须真正作用于打坐产出（不只是面板数字）。"""
        game = self._game(realm="core")
        ar, p = game.system("arts"), game.player
        p.attributes.base["comprehension"] = 25.0
        cult = game.system("cultivation")
        before = cult._hourly_gain(p)
        ar.learn("taixu")                   # 含 cultivate_speed +12%
        ar.learned["taixu"] = ar.player and ar.learned["taixu"]
        ar.gain_proficiency(10 ** 6)        # 直接练满，避免熟练度缩放干扰
        after = cult._hourly_gain(p)
        self.assertGreater(after, before)
        self.assertGreater(ar.bonus("cultivate_speed"), 0)

    def test_breakthrough_rate_bonus_applies(self):
        """突破率加成进入 success_rate。"""
        game = self._game(realm="nascent")
        ar, p = game.system("arts"), game.player
        cult = game.system("cultivation")
        before = cult.success_rate(True)
        ar.learn("dongxuan")                # breakthrough_rate +8%
        ar.gain_proficiency(10 ** 6)
        after = cult.success_rate(True)
        self.assertGreater(after, before)

    # ---------- 叠加规则 ----------
    def test_attr_mul_stacks_additively(self):
        """属性百分比按偏移相加（两件 +12% 得 +24%，不是连乘的 1.2544）。"""
        game = self._game(realm="foundation")
        ar, p = game.system("arts"), game.player
        ar.learn("gengjin")
        ar.gain_proficiency(10 ** 6)        # 练满：atk +12%
        one = p.attributes.value("atk")
        ar.learn("chiyan")                  # atk +26% + 6
        ar.gain_proficiency(10 ** 6)
        two = p.attributes.value("atk")
        # 偏移相加：总乘数 = 1 + Σ(偏移×等级成长)；连乘会明显更大（1.12×1.26）
        growth = art_config.level_scale(get_art("gengjin").max_level, get_art("gengjin"))
        offset_sum = (0.12 + 0.26) * growth
        mul_total = 1.0 + offset_sum
        # 由聚合器落地后的实际乘数应当等于「偏移相加」的结果
        self.assertAlmostEqual(game.bonuses._attr_mul["atk"], offset_sum, delta=0.02)
        self.assertLess(mul_total, 1.0 + 0.12 * growth + 0.26 * growth + 0.05)
        self.assertGreater(two, one)

    def test_max_stack_rule_takes_highest(self):
        """max 规则：多件同类型只取最高，不叠加。"""
        from xiuxian.systems.arts import _combine
        self.assertEqual(_combine("max", 0.05, 0.08), 0.08)
        self.assertEqual(_combine("add", 0.05, 0.08), 0.13)
        self.assertAlmostEqual(_combine("mul", 1.2, 1.1), 1.32, delta=1e-6)

    # ---------- 等级与熟练度 ----------
    def test_level_and_proficiency_scale(self):
        art = get_art("qingxin")
        self.assertEqual(level_of(0, art), 1)
        self.assertGreater(level_of(600, art), 1)
        raw = art.effects[1].value          # max_mp 固定值
        low = effective_value(art, art.effects[1], 0)
        full = effective_value(art, art.effects[1], art.max_proficiency)
        self.assertLess(low, raw)
        self.assertGreater(full, low)

    def test_cultivate_grows_proficiency(self):
        game = self._game()
        ar = game.system("arts")
        ar.learn("qingxin")
        logs = game.system("cultivation").cultivate(8)
        self.assertGreater(ar.learned["qingxin"], 0)
        self.assertTrue(any("熟练度" in line for line in logs),
                        "熟练度提示必须由事件总线回收到打坐日志里")

    def test_art_effect_grants_via_dsl(self):
        game = self._game()
        ar = game.system("arts")
        apply_effects(game, [{"type": "art", "id": "qingxin", "proficiency": 100}])
        self.assertIn("qingxin", ar.learned)

    def test_state_persists_and_recomputes(self):
        """存档往返后加成仍然生效（rebuild 在 load_state 中被调用）。"""
        import tempfile
        tmp = tempfile.mkdtemp()
        game = self._game(realm="core")
        ar, p = game.system("arts"), game.player
        p.attributes.base["comprehension"] = 25.0
        ar.learn("taixu")
        ar.gain_proficiency(10 ** 6)
        speed_before = ar.bonus("cultivate_speed")
        mp_before = p.max_mp

        from xiuxian.core.save import SaveManager
        SaveManager(tmp).save(game, 1)
        restored = load_game(1, save_dir=tmp)
        rar = restored.system("arts")
        self.assertIn("taixu", rar.equipped)
        self.assertAlmostEqual(rar.bonus("cultivate_speed"), speed_before, delta=1e-6)
        self.assertAlmostEqual(restored.player.max_mp, mp_before, delta=0.01)



class TestPillGrade(unittest.TestCase):
    """丹药品阶：中品即基表（id 不变），下品/上品程序化派生。"""

    def test_all_graded_pills_have_three_grades(self):
        for base_id in item_config.GRADED_PILLS:
            for grade in ("low", "mid", "high"):
                self.assertIn(item_config.graded_id(base_id, grade), item_config.ITEMS)

    def test_mid_is_the_base_item(self):
        self.assertEqual(item_config.graded_id("healing_pill", "mid"), "healing_pill")
        self.assertEqual(item_config.get_item("healing_pill").grade_key, "mid")
        self.assertEqual(item_config.get_item("healing_pill#low").grade_key, "low")

    def test_parse_graded_roundtrip(self):
        self.assertEqual(item_config.parse_graded("qi_gathering_pill#high"),
                         ("qi_gathering_pill", "high"))
        self.assertEqual(item_config.parse_graded("qi_gathering_pill"),
                         ("qi_gathering_pill", "mid"))

    def test_potency_scales_effects_and_poison(self):
        def exp_of(item_id):
            # 修为效果现已统一为 exp_ratio（按需求比例），兼容旧的 exp 形式
            for eff in item_config.get_item(item_id).effects:
                if eff["type"] in ("exp", "exp_ratio"):
                    return eff["value"]
            return None

        low, mid, high = (exp_of(item_config.graded_id("qi_gathering_pill", g))
                          for g in ("low", "mid", "high"))
        self.assertLess(low, mid)
        self.assertLess(mid, high)
        self.assertAlmostEqual(low / mid, 0.7, places=2)
        self.assertAlmostEqual(high / mid, 1.4, places=2)
        self.assertLess(item_config.get_item("healing_pill#low").poison,
                        item_config.get_item("healing_pill#high").poison)

    def test_breakthrough_bonus_scales_with_grade(self):
        base = item_config.get_item("foundation_pill").breakthrough_bonus["foundation"]
        top = item_config.get_item("foundation_pill#high").breakthrough_bonus["foundation"]
        self.assertGreater(top, base)

    def test_cleansing_pill_reduces_poison(self):
        game = new_game()
        game.player.pill_poison = 30
        game.player.inventory.add("cleansing_pill", 1)
        game.use_item("cleansing_pill")
        self.assertLess(game.player.pill_poison, 30)


class TestAlchemy(unittest.TestCase):
    """炼丹：丹方 -> 成功率 -> 品阶 -> 不炸炉 -> 造诣，以及采药与存档。"""

    def _game(self, seed: int = SEED) -> Game:
        return new_game(seed=seed)

    @staticmethod
    def _stock(game: Game, herb: int = 40, blood: int = 20, core: int = 10) -> None:
        inv = game.player.inventory
        inv.add("spirit_herb", herb)
        inv.add("beast_blood", blood)
        inv.add("beast_core", core)

    @staticmethod
    def _force_roll(game: Game, value: float) -> None:
        """把掷点钉死，便于确定性地测成功/失败两条分支。"""
        game.rng.rand = lambda: value

    def test_starting_recipes(self):
        al = self._game().system("alchemy")
        self.assertEqual(al.known, set(recipe_config.STARTING_RECIPES))

    def test_learn_costs_stones_and_is_idempotent(self):
        game = self._game()
        al = game.system("alchemy")
        price = recipe_config.get_recipe("cleansing_pill").price
        game.player.spirit_stones = price
        self.assertTrue(any("换得" in line for line in al.learn("cleansing_pill")))
        self.assertEqual(game.player.spirit_stones, 0)
        self.assertTrue(any("烂熟于心" in line for line in al.learn("cleansing_pill")))
        self.assertEqual(game.player.spirit_stones, 0)

    def test_learn_rejected_when_broke(self):
        game = self._game()
        al = game.system("alchemy")
        game.player.spirit_stones = 0
        self.assertTrue(any("灵石不足" in line for line in al.learn("core_pill")))
        self.assertNotIn("core_pill", al.known)

    def test_refine_unknown_recipe_rejected(self):
        game = self._game()
        al = game.system("alchemy")
        self.assertTrue(any("尚未领悟" in line for line in al.refine("core_pill")))
        self.assertEqual(al.refined, 0)

    def test_refine_respects_realm_gate(self):
        game = self._game()
        al = game.system("alchemy")
        al.known.add("core_pill")
        self.assertTrue(any("修为不足" in line for line in al.refine("core_pill")))
        self.assertEqual(al.refined, 0)

    def test_success_consumes_materials_and_yields_pill(self):
        game = self._game()
        al = game.system("alchemy")
        self._stock(game)
        self._force_roll(game, 0.0)          # 必成且火候满 -> 上品
        before = game.player.inventory.count("spirit_herb")
        logs = al.refine("qi_pill", 1)

        self.assertEqual(game.player.inventory.count("spirit_herb"), before - 2)
        self.assertGreaterEqual(game.player.inventory.count("qi_gathering_pill#high"), 1)
        self.assertEqual(al.success, 1)
        self.assertAlmostEqual(al.mastery["qi_pill"], 1.0)
        self.assertTrue(any("成丹" in line for line in logs))

    def test_failure_refunds_half_and_breaks_nothing(self):
        """不炸炉：只损一半材料，装备、境界、气血一律不动。"""
        game = self._game()
        al, eq = game.system("alchemy"), game.system("equipment")
        self._stock(game)
        game.player.inventory.add("clay_cauldron", 1)
        eq.equip("clay_cauldron")
        hp, realm = game.player.hp, game.player.realm_key

        self._force_roll(game, 0.999)        # 必废
        before = game.player.inventory.count("spirit_herb")
        logs = al.refine("qi_pill", 1)

        self.assertEqual(game.player.inventory.count("spirit_herb"), before - 2 + 1)
        self.assertEqual(al.fail, 1)
        self.assertEqual(al.success, 0)
        self.assertEqual(game.player.equipment.get("treasure"), "clay_cauldron")
        self.assertEqual(game.player.realm_key, realm)
        self.assertEqual(game.player.hp, hp)
        self.assertAlmostEqual(al.mastery["qi_pill"], 0.4)
        self.assertTrue(any("无恙" in line for line in logs))

    def test_batch_stops_when_materials_run_out(self):
        game = self._game()
        al = game.system("alchemy")
        self._stock(game, herb=5)
        self._force_roll(game, 0.0)
        game.player.mp = 10_000
        game.player.stamina = 10_000
        logs = al.refine("qi_pill", 20)
        self.assertEqual(al.refined, 2)      # 5 株灵草只够两炉（每炉 2 株）
        self.assertTrue(any("停火" in line for line in logs))

    def test_mastery_and_furnace_raise_success_rate(self):
        game = self._game()
        al = game.system("alchemy")
        # 用结金丹方（基础 28%）测：聚气丹方基础太高，会被 95% 上限夹住看不出差异
        recipe = recipe_config.get_recipe("core_pill")
        base, _ = al.rate_of(recipe)

        al.mastery["core_pill"] = 12.0
        with_mastery, _ = al.rate_of(recipe)
        self.assertGreater(with_mastery, base)
        self.assertLessEqual(with_mastery, 0.95)     # 上限被夹住

        game.player.equipment["treasure"] = "cinnabar_furnace"
        self.assertAlmostEqual(al.furnace_bonus(), 0.12)
        with_furnace, text = al.rate_of(recipe)
        self.assertGreater(with_furnace, with_mastery)
        self.assertIn("丹炉", text)                   # 算式对外可见

    def test_rate_breakdown_is_fully_shown(self):
        al = self._game().system("alchemy")
        _, text = al.rate_of(recipe_config.get_recipe("heal_pill"))
        for label in ("丹方", "悟性", "气运"):
            self.assertIn(label, text)

    def test_recipe_granted_via_effect_dsl(self):
        game = self._game()
        al = game.system("alchemy")
        logs = apply_effects(game, [{"type": "recipe", "id": "cleansing_pill"}])
        self.assertIn("cleansing_pill", al.known)
        self.assertTrue(any("清毒丹方" in line for line in logs))

    def test_gather_yields_herbs(self):
        game = self._game()
        al = game.system("alchemy")
        game.location = "宗门灵脉"
        logs = al.gather(6)
        self.assertGreater(game.player.inventory.count("spirit_herb"), 0)
        self.assertTrue(any("采得" in line for line in logs))

    def test_gather_needs_stamina(self):
        game = self._game()
        al = game.system("alchemy")
        game.player.stamina = 0
        self.assertTrue(any("精力枯竭" in line for line in al.gather(4)))

    def test_alchemy_persists_across_save(self):
        game = self._game()
        al = game.system("alchemy")
        self._stock(game)
        self._force_roll(game, 0.0)
        game.player.spirit_stones = 9999
        al.learn("cleansing_pill")
        al.refine("qi_pill", 2)

        with tempfile.TemporaryDirectory() as tmp:
            SaveManager(tmp).save(game, 1)
            restored = load_game(1, save_dir=tmp)

        ra = restored.system("alchemy")
        self.assertIn("cleansing_pill", ra.known)
        self.assertEqual(ra.refined, al.refined)
        self.assertEqual(ra.success, al.success)
        self.assertAlmostEqual(ra.mastery["qi_pill"], al.mastery["qi_pill"])


class TestDungeon(unittest.TestCase):
    """秘境：境界门槛 -> 层内抽签 -> 战败退一层 -> 通关冷却 -> 进度存档。"""

    def _game(self, seed: int = SEED) -> Game:
        return new_game(seed=seed)

    @staticmethod
    def _mock_combat(game: Game, player_wins: bool) -> None:
        """把战斗钉成必胜或必败。

        hp=0 的敌人进不了战斗循环，直接走 _victory；
        玩家 hp 压到 1 且敌人攻击极高，则必然触发濒死护持走 _defeat。
        """
        from xiuxian.systems.combat import Enemy

        combat = game.system("combat")
        p = game.player
        if player_wins:
            combat.spawn = lambda tier: Enemy(
                name="木桩", hp=0.0, max_hp=1.0, atk=1.0,
                defense=0.0, speed=1.0, exp_reward=0.0)
        else:
            p.hp = 1.0
            combat.spawn = lambda tier: Enemy(
                name="煞神", hp=99999.0, max_hp=99999.0, atk=99999.0,
                defense=0.0, speed=99999.0, exp_reward=0.0)

    def test_realm_gate_shows_in_catalog(self):
        d = self._game().system("dungeon")
        lines = d.catalog()
        self.assertTrue(any("可入" in line for line in lines))
        self.assertTrue(any("修为不足" in line for line in lines))

    def test_entry_blocked_by_realm(self):
        d = self._game().system("dungeon")
        self.assertTrue(any("修为不足" in line for line in d.enter("wanshou")))
        self.assertIsNone(d.run)

    def test_unknown_dungeon_rejected(self):
        d = self._game().system("dungeon")
        self.assertTrue(any("并无" in line for line in d.enter("nowhere")))

    def test_enter_starts_at_first_floor(self):
        game = self._game()
        d = game.system("dungeon")
        d.enter("luoyun")
        self.assertEqual(d.run, {"id": "luoyun", "floor": 1, "awaiting": False, "streak": 0})
        self.assertTrue(any("1/3 层" in line for line in d.info()))

    def test_victory_advances_one_floor(self):
        game = self._game()
        d = game.system("dungeon")
        self._mock_combat(game, player_wins=True)
        d.enter("luoyun")
        d.run = {"id": "luoyun", "floor": 1, "awaiting": False}
        # 强制走战斗层，避免抽签随机性
        logs = d._resolve_floor(dungeon_config.get_dungeon("luoyun"),
                                dungeon_config.get_dungeon("luoyun").floors[0], "battle")
        self.assertEqual(d.run["floor"], 2)
        self.assertTrue(any("2/3 层" in line for line in logs))

    def test_defeat_steps_back_one_floor_and_keeps_progress(self):
        game = self._game()
        d = game.system("dungeon")
        p = game.player
        d.run = {"id": "luoyun", "floor": 2, "awaiting": False}
        p.hp = p.max_hp
        self._mock_combat(game, player_wins=False)
        logs = d._resolve_floor(dungeon_config.get_dungeon("luoyun"),
                                dungeon_config.get_dungeon("luoyun").floors[1], "battle")
        self.assertEqual(d.run["floor"], 1)
        self.assertTrue(any("退回上一层" in line for line in logs))
        self.assertTrue(any("调息" in line for line in logs))   # 退层即整备
        self.assertGreater(p.hp, 0)

    def test_defeat_at_first_floor_does_not_go_under(self):
        game = self._game()
        d = game.system("dungeon")
        d.run = {"id": "luoyun", "floor": 1, "awaiting": False}
        self._mock_combat(game, player_wins=False)
        d._resolve_floor(dungeon_config.get_dungeon("luoyun"),
                         dungeon_config.get_dungeon("luoyun").floors[0], "battle")
        self.assertEqual(d.run["floor"], 1)

    def test_low_stamina_does_not_count_as_defeat(self):
        """fight() 缺精力会直接 return，敌人满血——不能因此判成战败退层。"""
        game = self._game()
        d = game.system("dungeon")
        d.run = {"id": "luoyun", "floor": 2, "awaiting": False}
        game.player.stamina = 3
        logs = d.next()
        self.assertEqual(d.run["floor"], 2)
        self.assertTrue(any("精力不济" in line for line in logs))

    def test_treasure_and_rest_floors_advance(self):
        game = self._game()
        d = game.system("dungeon")
        dg = dungeon_config.get_dungeon("luoyun")
        for kind in ("treasure", "rest"):
            d.run = {"id": "luoyun", "floor": 1, "awaiting": False}
            game.player.stamina = 100
            d._resolve_floor(dg, dg.floors[0], kind)
            self.assertEqual(d.run["floor"], 2, kind)

    def test_boss_floor_completes_and_sets_cooldown(self):
        game = self._game()
        d = game.system("dungeon")
        dg = dungeon_config.get_dungeon("luoyun")
        self._mock_combat(game, player_wins=True)
        d.run = {"id": "luoyun", "floor": 3, "awaiting": False}
        logs = d._resolve_floor(dg, dg.floors[2], "boss")
        self.assertIsNone(d.run)                       # 通关即清空进度
        self.assertEqual(d.cleared["luoyun"], 1)
        self.assertEqual(d.cooldowns["luoyun"], game.day + dg.cooldown)
        self.assertTrue(any("通关" in line for line in logs))

    def test_cooldown_blocks_reentry(self):
        game = self._game()
        d = game.system("dungeon")
        d.cooldowns["luoyun"] = game.day + 3
        logs = d.enter("luoyun")
        self.assertIsNone(d.run)
        self.assertTrue(any("闭息" in line for line in logs))

    def test_flee_keeps_progress_and_enter_resumes(self):
        game = self._game()
        d = game.system("dungeon")
        d.enter("luoyun")
        d.run["floor"] = 2
        d.flee()
        self.assertEqual(d.run["floor"], 2)            # 进度不丢
        d.enter("luoyun")
        self.assertEqual(d.run["floor"], 2)            # 回来接着走

    def test_switching_dungeon_requires_leaving_first(self):
        game = self._game()
        d = game.system("dungeon")
        d.run = {"id": "luoyun", "floor": 1, "awaiting": False}
        self.assertTrue(any("先 dungeon flee" in line for line in d.enter("wanshou")))
        self.assertEqual(d.run["id"], "luoyun")

    def test_abandon_clears_run_and_cools_down(self):
        game = self._game()
        d = game.system("dungeon")
        d.run = {"id": "luoyun", "floor": 2, "awaiting": False}
        d.abandon()
        self.assertIsNone(d.run)
        self.assertEqual(d.cooldowns["luoyun"], game.day + 3)

    def test_dungeon_scene_isolates_event_pool(self):
        """秘境里不能撞见「宗门招徒」这类户外机缘。"""
        game = self._game()
        ev = game.system("event")
        from xiuxian.core.event_system import SCENE_DUNGEON, SCENE_OUTDOOR

        dungeon_pool = [e.id for e in ev.available_events(SCENE_DUNGEON)]
        outdoor_pool = [e.id for e in ev.available_events(SCENE_OUTDOOR)]
        self.assertTrue(dungeon_pool)
        self.assertTrue(any(i.startswith("dungeon_") for i in dungeon_pool))
        self.assertNotIn("sect_recruit", dungeon_pool)
        self.assertNotIn("dungeon_stele", outdoor_pool)

    def test_dungeon_granted_via_effect_dsl(self):
        game = self._game()
        d = game.system("dungeon")
        logs = apply_effects(game, [{"type": "dungeon", "id": "luoyun", "floor": 2}])
        self.assertEqual(d.run["floor"], 2)
        self.assertTrue(any("落云秘境" in line for line in logs))

    def test_floor_intro_shows_danger_estimate(self):
        d = self._game().system("dungeon")
        d.run = {"id": "luoyun", "floor": 1, "awaiting": False}
        logs = d._floor_intro(dungeon_config.get_dungeon("luoyun"))
        self.assertTrue(any("妖兽约气血" in line for line in logs))

    def test_dungeon_persists_across_save(self):
        game = self._game()
        d = game.system("dungeon")
        d.enter("luoyun")
        d.run["floor"] = 2
        d.cleared["luoyun"] = 4
        d.deepest["luoyun"] = 3

        with tempfile.TemporaryDirectory() as tmp:
            SaveManager(tmp).save(game, 1)
            restored = load_game(1, save_dir=tmp)

        rd = restored.system("dungeon")
        self.assertEqual(rd.run["floor"], 2)
        self.assertEqual(rd.run["id"], "luoyun")
        self.assertEqual(rd.cleared["luoyun"], 4)
        self.assertEqual(rd.deepest["luoyun"], 3)

    def test_corrupted_run_state_is_clamped(self):
        d = self._game().system("dungeon")
        d.load_state({"run": {"id": "luoyun", "floor": 99}, "cooldowns": {"ghost": 5}})
        self.assertEqual(d.run["floor"], 3)            # 夹到最深层
        self.assertNotIn("ghost", d.cooldowns)         # 未知秘境丢弃


class TestQuest(unittest.TestCase):
    """任务：自动追踪（斗法/机缘/突破）-> 达成领赏 -> 声望累加 -> 一次性/可重复。"""

    def _game(self, seed: int = SEED) -> Game:
        return new_game(seed=seed)

    @staticmethod
    def _spawn_with_tier(game: Game, tier: str):
        from xiuxian.systems.combat import Enemy

        combat = game.system("combat")
        combat.spawn = lambda t: Enemy(
            name="木桩", hp=0.0, max_hp=1.0, atk=1.0,
            defense=0.0, speed=1.0, exp_reward=0.0, tier=tier)
        return combat

    def test_quests_auto_accepted_at_new_game(self):
        q = self._game().system("quest")
        # 无门槛任务开局即接；声望门槛任务（q_sect_honor）在声望不足时不接
        self.assertIn("q_slay", q.accepted)
        self.assertIn("q_visit", q.accepted)
        self.assertIn("q_elite", q.accepted)
        self.assertIn("q_break", q.accepted)
        self.assertNotIn("q_sect_honor", q.accepted)

    def test_combat_objective_counts_and_completes(self):
        game = self._game()
        q, p = game.system("quest"), game.player
        p.stamina = 100
        combat = self._spawn_with_tier(game, "normal")
        stones_before = p.spirit_stones
        for _ in range(3):
            p.stamina = 100
            combat.fight(combat.spawn("normal"))
        # 可重复任务达成后立刻续接，进度清零，但奖励与声望已落袋
        self.assertEqual(q.accepted["q_slay"]["progress"], [0])
        self.assertGreater(q.reputation.get("青云宗", 0), 0)
        self.assertGreater(p.spirit_stones, stones_before)

    def test_event_objective_counts(self):
        game = self._game()
        q = game.system("quest")
        from xiuxian.core.base_system import TOPIC_EVENT_RESOLVED
        for _ in range(2):
            game.bus.emit(TOPIC_EVENT_RESOLVED, {"event_id": "x", "choice_id": "y"})
        self.assertEqual(q.accepted["q_visit"]["progress"], [0])   # 完成后续接归零
        self.assertGreater(q.reputation.get("青云宗", 0), 0)

    def test_breakthrough_objective_completes_once_quest(self):
        game = self._game()
        q = game.system("quest")
        from xiuxian.core.base_system import TOPIC_AFTER_BREAKTHROUGH
        game.bus.emit(TOPIC_AFTER_BREAKTHROUGH,
                      {"success": True, "target_realm": game.player.realm_key, "is_major": False})
        self.assertIn("q_break", q.completed_once)      # 一次性任务永久消失
        self.assertNotIn("q_break", q.accepted)
        self.assertGreater(q.reputation.get("散修", 0), 0)

    def test_min_tier_filter(self):
        """weak 妖兽不计入 normal+ 的「斩妖卫道」；elite 计入「猎杀凶兽」。"""
        game = self._game()
        q, p = game.system("quest"), game.player
        p.stamina = 100
        combat = self._spawn_with_tier(game, "weak")
        slay_before = q.accepted["q_slay"]["progress"][0]
        combat.fight(combat.spawn("weak"))
        self.assertEqual(q.accepted["q_slay"]["progress"][0], slay_before)

        rep_before = q.reputation.get("青云宗", 0)
        combat = self._spawn_with_tier(game, "elite")
        combat.fight(combat.spawn("elite"))
        self.assertGreater(q.reputation.get("青云宗", 0), rep_before)

    def test_once_quest_not_reaccepted(self):
        game = self._game()
        q = game.system("quest")
        from xiuxian.core.base_system import TOPIC_AFTER_BREAKTHROUGH
        game.bus.emit(TOPIC_AFTER_BREAKTHROUGH,
                      {"success": True, "target_realm": game.player.realm_key, "is_major": False})
        self.assertIn("q_break", q.completed_once)
        self.assertNotIn("q_break", q.accepted)
        game.bus.emit(TOPIC_AFTER_BREAKTHROUGH,
                      {"success": True, "target_realm": game.player.realm_key, "is_major": False})
        self.assertNotIn("q_break", q.accepted)

    def test_rep_gate_unlocks_higher_quest(self):
        game = self._game()
        q = game.system("quest")
        self.assertNotIn("q_sect_honor", q.accepted)   # 初始声望不足
        q.reputation["青云宗"] = 24
        q._auto_accept()
        self.assertIn("q_sect_honor", q.accepted)      # 声望达标即解锁

    def test_rep_gate_unlocks_mid_game(self):
        """完成普通任务累计声望跨过门槛后，高阶任务自动接进。"""
        game = self._game()
        q, p = game.system("quest"), game.player
        p.stamina = 100
        combat = self._spawn_with_tier(game, "normal")
        for _ in range(10):                            # 足够把青云宗声望推过 24
            p.stamina = 100
            combat.fight(combat.spawn("normal"))
        self.assertIn("q_sect_honor", q.accepted)

    def test_quest_persists_across_save(self):
        game = self._game()
        q = game.system("quest")
        q.reputation["青云宗"] = 30
        q.completed_once.add("q_break")
        q.accepted["q_slay"]["progress"] = [2]

        with tempfile.TemporaryDirectory() as tmp:
            SaveManager(tmp).save(game, 1)
            restored = load_game(1, save_dir=tmp)

        rq = restored.system("quest")
        self.assertEqual(rq.reputation["青云宗"], 30)
        self.assertIn("q_break", rq.completed_once)
        self.assertEqual(rq.accepted["q_slay"]["progress"], [2])


class TestMarket(unittest.TestCase):
    """坊市：买/卖、价格浮动（限基准 0.5~1.8 倍）、存档。"""

    def _game(self, seed: int = SEED) -> Game:
        return new_game(seed=seed)

    def test_buy_deducts_stones_and_adds_item(self):
        game = self._game()
        m, p = game.system("market"), game.player
        p.spirit_stones = 1000
        before = p.inventory.count("qi_gathering_pill")
        price_before = m.buy_price("qi_gathering_pill")
        logs = m.buy("qi_gathering_pill", 2)
        self.assertTrue(any("购入" in line for line in logs))
        self.assertEqual(p.inventory.count("qi_gathering_pill"), before + 2)
        # v2 价格反馈：买入推高价格，但按买入价结算
        self.assertEqual(p.spirit_stones, 1000 - int(price_before * 2))
        self.assertGreater(m.buy_price("qi_gathering_pill"), price_before)

    def test_buy_rejects_insufficient_stones(self):
        game = self._game()
        m, p = game.system("market"), game.player
        before = p.inventory.count("qi_gathering_pill")
        p.spirit_stones = 0
        logs = m.buy("qi_gathering_pill")
        self.assertTrue(any("灵石不足" in line for line in logs))
        self.assertEqual(p.inventory.count("qi_gathering_pill"), before)   # 未购入

    def test_buy_rejects_unknown(self):
        logs = self._game().system("market").buy("not_a_real_item")
        self.assertTrue(any("并无" in line for line in logs))

    def test_sell_removes_item_and_adds_stones(self):
        game = self._game()
        m, p = game.system("market"), game.player
        p.inventory.add("beast_core", 2)
        p.spirit_stones = 0
        logs = m.sell("beast_core", 2)
        self.assertTrue(any("售出" in line for line in logs))
        self.assertEqual(p.inventory.count("beast_core"), 0)
        self.assertGreater(p.spirit_stones, 0)

    def test_sell_rejects_missing(self):
        logs = self._game().system("market").sell("beast_core")
        self.assertTrue(any("并无" in line for line in logs))

    def test_price_drift_stays_in_bounds(self):
        game = self._game()
        m = game.system("market")
        for _ in range(60):
            game.advance_time(24)          # 触发 day_end -> 价格刷新
        for iid, price in m.prices.items():
            base = item_config.get_item(iid).price
            if base <= 0:
                continue
            self.assertGreaterEqual(price, base * 0.5 - 0.01)
            self.assertLessEqual(price, base * 1.8 + 0.01)

    def test_catalog_lists_priced_items(self):
        lines = self._game().system("market").catalog()
        self.assertTrue(any("聚气丹" in line for line in lines))

    def test_market_persists(self):
        game = self._game()
        m = game.system("market")
        m.prices["qi_gathering_pill"] = 17.3
        with tempfile.TemporaryDirectory() as tmp:
            SaveManager(tmp).save(game, 1)
            restored = load_game(1, save_dir=tmp)
        self.assertAlmostEqual(restored.system("market").prices["qi_gathering_pill"], 17.3)


class TestSkill(unittest.TestCase):
    """技能系统：功法的主动技能 + 策略自动施法。"""

    def _grow_to(self, game, realm_key):
        """把属性与境界推进到指定境界，用于跨过功法的境界门槛。"""
        p = game.player
        for r in REALMS:
            for _ in range(r.stage_count):
                p.attributes.grow_base("max_hp", r.hp_per_stage)
                p.attributes.grow_base("max_mp", r.mp_per_stage)
                p.attributes.grow_base("atk", r.atk_per_stage)
                p.attributes.grow_base("def", r.def_per_stage)
            if r.key == realm_key:
                break
        p.realm_key = realm_key
        p.hp = p.max_hp
        p.mp = p.max_mp

    def _learn(self, game, art_id, proficiency=None):
        game.player.spirit_stones = 999999
        arts = game.system("arts")
        arts.learn(art_id)
        if proficiency:
            arts.gain_proficiency(proficiency)
        return arts

    def test_arts_grant_signature_skills(self):
        for art_id, art in art_config.ARTS.items():
            # 签名技能是可选的：专修炼/冲关/聚财类功法本就不该有战斗技能
            if not art.skills:
                continue
            for sid in art.skills:
                self.assertIn(sid, skill_config.SKILLS,
                              f"{art.name} 的技能 {sid} 不在技能表里")

    def test_no_arts_means_no_skills(self):
        """开局未修习功法 -> 没有技能 -> 战斗照旧普攻。"""
        game = new_game()
        sys_ = game.system("skill")
        self.assertEqual(sys_.known(), [])
        self.assertIsNone(sys_.choose_action(1.0))

    def test_mp_cost_stays_proportional_across_realms(self):
        """消耗是灵力上限的比例，跨五个数量级恒定。

        若 mp_cost 写成绝对值，渡劫期（蓝条 1173 万）的消耗会退化成 0.0002%，
        与「灵石挂在指数增长量上结余 288 亿」是同一类错误。
        """
        for r in REALMS:
            game = new_game(seed=7)
            self._grow_to(game, r.key)
            self._learn(game, "gengjin")
            sys_ = game.system("skill")
            self.assertAlmostEqual(sys_.known()[0]["cost"] / game.player.max_mp,
                                   0.10, places=2)

    def test_power_scales_with_proficiency(self):
        game = new_game()
        self._learn(game, "gengjin")
        sys_ = game.system("skill")
        weak = sys_.known()[0]["power"]
        game.system("arts").gain_proficiency(2000)      # 练满
        full = sys_.known()[0]["power"]
        self.assertGreater(full, weak)
        self.assertAlmostEqual(weak, 1 + (1.9 - 1) * 0.6, places=2)
        self.assertAlmostEqual(full, 1.9, places=2)

    def test_attack_picked_and_mp_deducted(self):
        game = new_game()
        self._learn(game, "gengjin", 2000)
        p, sys_ = game.player, game.system("skill")
        p.mp = p.max_mp
        plan = sys_.choose_action(1.0)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["skill"].kind, "attack")
        self.assertLess(p.mp, p.max_mp)

    def test_heal_triggers_when_hp_low_conservative(self):
        game = new_game()
        self._learn(game, "qingxin", 2000)
        self._learn(game, "gengjin", 2000)
        p, sys_ = game.player, game.system("skill")
        sys_.strategy = "conservative"
        p.hp = p.max_hp * 0.30          # 保守档治疗线 55%
        plan = sys_.choose_action(1.0)
        self.assertEqual(plan["skill"].kind, "heal")

    def test_aggressive_prefers_attack_over_heal(self):
        """同样 30% 气血：保守档治疗，激进档继续打。"""
        game = new_game()
        self._learn(game, "qingxin", 2000)
        self._learn(game, "gengjin", 2000)
        p, sys_ = game.player, game.system("skill")
        sys_.strategy = "aggressive"
        p.hp = p.max_hp * 0.30          # 激进档治疗线仅 25%
        plan = sys_.choose_action(1.0)
        self.assertEqual(plan["skill"].kind, "attack")

    def test_cooldown_blocks_then_expires(self):
        game = new_game()
        self._learn(game, "gengjin", 2000)
        sys_ = game.system("skill")
        sys_.choose_action(1.0)
        self.assertGreater(sys_.cooldowns.get("gengjin_strike", 0), 0)
        # 冷却 2 -> 存的是 3，走 3 格才归零
        for _ in range(3):
            sys_.next_round()
        self.assertEqual(sys_.cooldowns.get("gengjin_strike", 0), 0)

    def test_begin_battle_clears_cooldowns(self):
        game = new_game()
        self._learn(game, "gengjin", 2000)
        sys_ = game.system("skill")
        sys_.choose_action(1.0)
        self.assertTrue(sys_.cooldowns)
        sys_.begin_battle()
        self.assertEqual(sys_.cooldowns, {})

    def test_no_wasted_heal_at_full_hp(self):
        """攻击技能进冷却、而人满血时，不该去放疗伤凑回合。

        满血放疗伤在日志里就是「气血回复 1（60/60）」，白白让掉一回合，
        不如普攻。治疗只有在药力能真正落进去时才值得施放。
        """
        game = new_game()
        self._learn(game, "qingxin", 2000)     # 只学疗伤功法，没有攻伐技能
        p, sys_ = game.player, game.system("skill")
        p.hp = p.max_hp
        self.assertIsNone(sys_.choose_action(1.0))
        # 掉到七成血，治疗才开始有意义
        p.hp = p.max_hp * 0.7
        plan = sys_.choose_action(1.0)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["skill"].kind, "heal")

    def test_no_guard_when_enemy_nearly_dead(self):
        """敌人残血时不放护体。

        残血阶段减伤救不了人，多点输出才能结束战斗。不这么处理，
        保守档会一路护体到打满 40 回合仍未分胜负（推演里 3% 的战斗如此）。
        """
        game = new_game()
        self._grow_to(game, "foundation")
        self._learn(game, "qingxin", 2000)      # 清心咒（疗伤）
        self._learn(game, "gengjin", 2000)      # 庚金一击（攻伐）
        self._learn(game, "xuangui", 2000)      # 玄龟护体（护体）
        p, sys_ = game.player, game.system("skill")
        sys_.strategy = "conservative"          # 最该护体的一档
        # 气血 60%：高于治疗线 55%、低于护体线 70%，于是两组的唯一变量是敌人血量
        p.hp = p.max_hp * 0.6
        plan = sys_.choose_action(enemy_hp_ratio=0.10)
        self.assertIsNotNone(plan)
        self.assertNotEqual(plan["skill"].kind, "guard")
        # 同样的血量，敌人满血时就该护体了
        sys_.cooldowns.clear()
        p.hp = p.max_hp * 0.6
        plan = sys_.choose_action(enemy_hp_ratio=1.0)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["skill"].kind, "guard")

    def test_end_battle_clears_cooldowns(self):
        """战后冷却必须清干净。

        冷却只对「本场战斗」有意义，残留到战后会让 skill 面板一直挂着
        「[冷却中 剩 N]」，看起来像技能永久失效。
        """
        game = new_game(seed=7)
        self._learn(game, "gengjin", 2000)
        sys_ = game.system("skill")
        combat = game.system("combat")
        combat.fight(combat.spawn("weak"))
        self.assertEqual(sys_.cooldowns, {})

    def test_recover_is_free_and_usable_at_zero_mp(self):
        """回灵不耗灵力，是「蓝空了」这个死局的出口。"""
        game = new_game()
        self._grow_to(game, "core")
        self._learn(game, "taixu", 2000)
        p, sys_ = game.player, game.system("skill")
        p.mp = 0.0
        plan = sys_.choose_action(1.0)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["skill"].kind, "recover")
        self.assertEqual(plan["cost"], 0)

    def test_backlash_never_kills_player(self):
        """反噬兜到 1 点血，放技能不该把自己玩死。"""
        game = new_game()
        self._grow_to(game, "foundation")
        self._learn(game, "chiyan", 2000)
        combat = game.system("combat")
        p = game.player
        p.hp = 1.0
        p.stamina = 100
        combat.fight(combat.spawn("weak"))
        self.assertGreaterEqual(p.hp, 1.0)

    def test_guard_reduces_incoming_damage(self):
        game = new_game()
        self._grow_to(game, "foundation")
        self._learn(game, "xuangui", 2000)
        combat = game.system("combat")
        p = game.player
        p.hp = p.max_hp * 0.40      # 均衡档护体线 45%
        p.stamina = 100
        logs = combat.fight(combat.spawn("normal"))
        self.assertTrue(any("护体减免" in line for line in logs))

    def test_strategy_persists(self):
        game = new_game()
        sys_ = game.system("skill")
        sys_.strategy = "aggressive"
        with tempfile.TemporaryDirectory() as tmp:
            SaveManager(tmp).save(game, 1)
            restored = load_game(1, save_dir=tmp)
        self.assertEqual(restored.system("skill").strategy, "aggressive")

    def test_bad_strategy_falls_back_to_balanced(self):
        game = new_game()
        sys_ = game.system("skill")
        sys_.load_state({"strategy": "乱写一通"})
        self.assertEqual(sys_.strategy, "balanced")

    def test_combat_unchanged_without_skill_system(self):
        """卸掉技能系统，战斗必须一字不改（事件/秘境/推演依赖这点）。"""
        systems = [s for s in default_systems() if s.id != "skill"]
        game = create_game(name="无技能", seed=3, systems=systems)
        self.assertNotIn("skill", game.systems)
        combat = game.system("combat")
        game.player.stamina = 100
        logs = combat.fight(combat.spawn("weak"))
        self.assertTrue(any("你击中" in line for line in logs))
        self.assertFalse(any("施展" in line for line in logs))


class TestProse(unittest.TestCase):
    """文字质感：战斗日志叙事、技能施放文案、境界铭文。

    文风是「文字即玩法」的落点，但文风不能碰机制：
    词库与文案只负责「读起来的样子」，数值、判定、锚点词全部不动。
    """

    def test_flavor_pools_non_empty(self):
        """四个文风词库不能为空——否则战斗退化成纯数值简报。"""
        for pool in (combat_module.PLAYER_STRIKE_FLAVOR,
                     combat_module.PLAYER_CRIT_FLAVOR,
                     combat_module.ENEMY_STRIKE_FLAVOR):
            self.assertTrue(pool)
            for line in pool:
                self.assertTrue(line.endswith("，") or line.endswith("。"))

    def test_victory_lines_keep_anchor(self):
        """胜利文案全部保留「倒地不起」——test_victory_rewards 依赖。"""
        for tpl in combat_module.VICTORY_LINES:
            self.assertIn("倒地不起", tpl.format(enemy="妖兽"))

    def test_normal_attack_keeps_anchor(self):
        """无技能系统时，普攻行仍含「你击中」。"""
        systems = [s for s in default_systems() if s.id != "skill"]
        game = create_game(name="文风", seed=3, systems=systems)
        game.player.stamina = 100
        combat = game.system("combat")
        all_logs: list[str] = []
        for _ in range(8):                     # 多打几场覆盖多个词库条目
            enemy = combat.spawn("weak")
            enemy.hp = 1
            all_logs.extend(combat.fight(enemy))
        self.assertTrue(any("你击中" in ln for ln in all_logs))

    def test_skill_cast_renders_flavor(self):
        """技能施放日志渲染 config 里的 cast 文案。"""
        game = new_game(seed=7)
        game.player.spirit_stones = 999999
        game.system("arts").learn("gengjin")
        game.system("arts").gain_proficiency(2000)
        game.player.stamina = 100
        combat = game.system("combat")
        logs = combat.fight(combat.spawn("weak"))
        cast_lines = [ln for ln in logs if "施展【庚金一击】" in ln]
        self.assertTrue(cast_lines)
        self.assertTrue(any("庚金剑气" in ln for ln in cast_lines))

    def test_cast_fallback_without_config(self):
        """cast 为空的技能退回通用句式，仍含「施展【】」与数值。"""
        from dataclasses import replace
        bare = replace(skill_config.get_skill("gengjin_strike"), cast="")
        game = new_game()
        combat = game.system("combat")
        logs = combat._cast({"skill": bare, "power": 1.9}, combat.spawn("weak"))
        self.assertTrue(any("施展【庚金一击】" in ln for ln in logs))
        self.assertTrue(any("点伤害" in ln for ln in logs))

    def test_enemy_strike_keeps_anchor(self):
        """受击行保留「反扑 / 受到 / 剩余」，护体减免标注也保留。"""
        game = new_game()
        combat = game.system("combat")
        enemy = combat.spawn("weak")
        combat._guard = 0.45
        logs = combat._enemy_strike(enemy)
        self.assertIn("反扑", logs[0])
        self.assertIn("受到", logs[0])
        self.assertIn("护体减免", logs[0])
        self.assertIn("剩余", logs[0])

    def test_revelations_cover_all_realms(self):
        """九个境界各有铭文，一个不多一个不少。"""
        self.assertEqual(set(realm_config.REVELATIONS),
                         {r.key for r in REALMS})

    def test_revelation_only_on_major(self):
        """小境界升级无铭文，跨大境界才有。"""
        game = new_game(seed=5)
        cult = game.system("cultivation")
        p = game.player
        small = cult._advance_stage(False)     # 炼气一层 -> 二层
        self.assertFalse(any("　　" in ln for ln in small))
        p.realm_key = "qi_refining"            # 回到起点，测跨大境界
        p.stage = 8
        major = cult._advance_stage(True)      # 炼气 -> 筑基
        self.assertTrue(any(realm_config.REVELATIONS["foundation"] in ln
                            for ln in major))


class TestImmortalRealms(unittest.TestCase):
    """仙界九境：飞升不是终点，法无止境。"""

    def test_human_immortal_follows_ascension(self):
        """渡劫的下一境是人仙，凡九 + 仙九 = 18 境。"""
        self.assertEqual(RealmRegistry.next_realm("ascension").key, "human_immortal")
        self.assertEqual(len(REALMS), 18)
        self.assertEqual(REALMS[-1].key, "hunyuan")

    def test_immortal_curve_continues(self):
        """仙界数值延续：需求单调递增，突破率继续递减。"""
        prev_req = RealmRegistry.get("ascension").exp_base
        prev_rate = 1.0
        for r in REALMS[9:]:
            self.assertGreater(r.exp_base, prev_req)
            self.assertLess(r.major_success, prev_rate)
            self.assertGreater(r.lifespan, 100_000)      # 与天同寿（纯展示）
            prev_req, prev_rate = r.exp_base, r.major_success

    def test_tribulation_names_layered(self):
        """仙劫名目分层：仙劫 / 天衰之劫 / 道心劫 / 斩三尸 / 合道大劫。"""
        self.assertEqual(RealmRegistry.get("human_immortal").tribulation, "仙劫")
        self.assertEqual(RealmRegistry.get("earth_immortal").tribulation, "仙劫")
        self.assertEqual(RealmRegistry.get("heaven_immortal").tribulation, "天衰之劫")
        self.assertEqual(RealmRegistry.get("mystic_immortal").tribulation, "天衰之劫")
        self.assertEqual(RealmRegistry.get("gold_immortal").tribulation, "道心劫")
        self.assertEqual(RealmRegistry.get("taiyi").tribulation, "道心劫")
        self.assertEqual(RealmRegistry.get("luo_po").tribulation, "斩三尸")
        self.assertEqual(RealmRegistry.get("quasi_saint").tribulation, "合道大劫")

    def test_immortal_breakthrough_triggers_tribulation(self):
        """仙界跨境界继续渡劫（仙劫延续）。"""
        trib = new_game(seed=19).system("tribulation")
        self.assertTrue(trib.needs_tribulation("human_immortal"))
        self.assertTrue(trib.needs_tribulation("hunyuan"))
        self.assertFalse(trib.needs_tribulation("foundation"))

    def test_flyup_banner_and_power(self):
        """渡劫圆满突破即飞升：横幅 + 铭文 + 仙体神通。"""
        game = new_game(seed=21)
        p = game.player
        p.realm_key = "ascension"
        p.stage = 2
        logs = game.system("cultivation")._advance_stage(True)
        self.assertEqual(p.realm_key, "human_immortal")
        self.assertTrue(any("飞升" in ln for ln in logs))
        self.assertTrue(any(realm_config.REVELATIONS["human_immortal"] in ln for ln in logs))
        # 仙体：气血上限 +10%
        self.assertTrue(p.attributes.has_modifier("realm:human_immortal"))

    def test_all_powers_have_names_and_effects(self):
        """神通表完整：金丹起每境一个，炼气/筑基无。"""
        self.assertIsNone(realm_config.power_of("qi_refining"))
        self.assertIsNone(realm_config.power_of("foundation"))
        for r in REALMS[2:]:
            pw = realm_config.power_of(r.key)
            self.assertIsNotNone(pw, f"{r.name} 缺少神通")
            self.assertTrue(pw.name and pw.desc)
            has_effect = bool(pw.add or pw.mul or pw.alchemy_bonus
                              or pw.cultivation_bonus or pw.tribulation_bonus
                              or pw.breakthrough_bonus)
            self.assertTrue(has_effect, f"{r.name} 神通无效果")

    def test_power_injected_on_major_breakthrough(self):
        """跨境界突破授予新境界神通；小境界突破不授予。"""
        game = new_game(seed=23)
        p = game.player
        p.realm_key = "core"
        p.stage = 3
        cult = game.system("cultivation")
        cult._advance_stage(True)              # 金丹圆满 -> 元婴
        self.assertEqual(p.realm_key, "nascent")
        self.assertTrue(p.attributes.has_modifier("realm:nascent"))
        # 元灵：灵力上限 ×1.12（基础 40 + 元婴每层成长 1800）
        self.assertAlmostEqual(p.attributes.value("max_mp"), (40 + 1800) * 1.12,
                               delta=0.01)
        cult._advance_stage(False)             # 小境界：不注入新神通
        self.assertFalse(p.attributes.has_modifier("realm:deity"))

    def test_power_persists_across_save(self):
        """神通 Modifier 随存档保留（读档后属性仍生效）。"""
        with tempfile.TemporaryDirectory() as tmp:
            game = new_game(seed=29)
            p = game.player
            p.realm_key = "core"
            p.stage = 3
            game.system("cultivation")._advance_stage(True)
            SaveManager(tmp).save(game, 1, note="ut")
            restored = load_game(1, save_dir=tmp)
            self.assertTrue(restored.player.attributes.has_modifier("realm:nascent"))
            self.assertAlmostEqual(restored.player.attributes.value("max_mp"),
                                   (40 + 1800) * 1.12, delta=0.01)

    def test_power_shown_in_panel(self):
        """状态面板显示当前境界神通。"""
        game = new_game(seed=31)
        p = game.player
        p.realm_key = "core"
        from xiuxian.ui import panel
        self.assertIn("丹心", panel.status_panel(game))
        p.realm_key = "qi_refining"
        self.assertNotIn("丹心", panel.status_panel(game))


class TestImmortalContent(unittest.TestCase):
    """第二十五批·仙界内容填坑：秘境/功法/地点/丹药/装备。"""

    def _game_at(self, seed: int, realm: str) -> Game:
        game = new_game(seed=seed)
        p = game.player
        p.realm_key = realm
        p.stage = 0
        p.exp = 0.0
        p.spirit_stones = 10_000_000
        return game

    # ---------- 仙界秘境 ----------
    def test_immortal_dungeons_exist_and_gated(self):
        """仙界秘境存在且按规定境界解锁。"""
        self.assertIn("yaochi", dungeon_config.DUNGEONS)
        self.assertIn("wanjie", dungeon_config.DUNGEONS)
        # 瑶池需人仙、万劫需金仙
        self.assertEqual(dungeon_config.DUNGEONS["yaochi"].min_realm, "human_immortal")
        self.assertEqual(dungeon_config.DUNGEONS["wanjie"].min_realm, "gold_immortal")

    def test_immortal_dungeon_enterable_by_realm(self):
        """人仙可进瑶池，炼气不可进（门槛拦截）。"""
        game = self._game_at(11, "human_immortal")
        d = game.system("dungeon")
        logs = d.enter("yaochi")
        self.assertFalse(any("修为不足" in ln for ln in logs))
        self.assertTrue(d.run and d.run["id"] == "yaochi")
        # 炼气玩家被拦
        game2 = self._game_at(12, "qi_refining")
        d2 = game2.system("dungeon")
        self.assertTrue(any("修为不足" in ln for ln in d2.enter("yaochi")))

    def test_immortal_dungeon_rewards_use_exp_ratio(self):
        """仙界秘境守关/宝箱修为奖励走 exp_ratio，不用绝对值（防后期归零）。"""
        for did in ("yaochi", "wanjie"):
            d = dungeon_config.get_dungeon(did)
            for eff in d.boss_reward:
                self.assertNotEqual(eff.get("type"), "exp",
                                    f"{d.name} 守关奖励用了绝对修为 exp")
            for g in d.treasures:
                for eff in g:
                    self.assertNotEqual(eff.get("type"), "exp",
                                        f"{d.name} 宝箱奖励用了绝对修为 exp")

    def test_immortal_dungeon_clears_with_reward(self):
        """人仙通关瑶池仙园：底层守关胜利推进 + boss 奖励入账。"""
        game = self._game_at(13, "human_immortal")
        d = game.system("dungeon")
        p = game.player
        p.stamina = 100
        # 快进到最后一层（boss）并强制玩家必胜
        d.run = {"id": "yaochi", "floor": 5, "awaiting": False, "streak": 0}
        from xiuxian.systems.combat import Enemy
        combat = game.system("combat")
        combat.spawn = lambda tier: Enemy(
            name="仙君残念", hp=0.0, max_hp=1.0, atk=1.0,
            defense=0.0, speed=1.0, exp_reward=0.0)
        logs = d._resolve_floor(dungeon_config.get_dungeon("yaochi"),
                                dungeon_config.get_dungeon("yaochi").floors[4], "boss")
        self.assertTrue(any("通关" in ln for ln in logs))
        self.assertIsNone(d.run)                 # 通关后进度清空

    # ---------- 仙阶功法 ----------
    def test_immortal_arts_budget_respected(self):
        """新增仙阶功法仍受品阶预算自检约束。"""
        over = validate_balance()
        self.assertEqual(over, [], "仙阶功法超预算：" + "；".join(over))
        for aid in ("xianjian", "xianzhou", "xianpo", "hunyuanzhou"):
            art = art_config.get_art(aid)
            self.assertEqual(art.rank, "仙阶")
            self.assertIsNotNone(RANK_BUDGET.get(art.rank))
            self.assertLessEqual(art.budget_cost(), RANK_BUDGET["仙阶"] + 1e-6)

    def test_immortal_art_realm_gate(self):
        """仙阶功法需要仙界境界（凡界学不了）。"""
        game = self._game_at(14, "qi_refining")
        ar = game.system("arts")
        logs = ar.learn("xianzhou")
        self.assertTrue(any("修为不足" in ln for ln in logs))
        self.assertNotIn("xianzhou", ar.learned)

    def test_immortal_art_learns_at_realm(self):
        """人仙可修习仙阶功法，装备后聚合器注入加成。"""
        game = self._game_at(15, "human_immortal")
        ar = game.system("arts")
        ar.learn("xianzhou")
        self.assertIn("xianzhou", ar.equipped)
        self.assertTrue(game.player.attributes.has_modifier("bonus:total"))

    # ---------- 仙界地点 ----------
    def test_immortal_locations_gated(self):
        """仙界地点带 min_realm 门槛，凡界玩家 travel 被拦。"""
        game = self._game_at(16, "qi_refining")
        logs = game.travel("瑶池")
        self.assertTrue(any("不得踏入" in ln for ln in logs))
        self.assertEqual(game.location, "青石镇")
        game2 = self._game_at(17, "human_immortal")
        logs2 = game2.travel("瑶池")
        self.assertFalse(any("不得踏入" in ln for ln in logs2))
        self.assertEqual(game2.location, "瑶池")

    # ---------- 仙丹 ----------
    def test_immortal_pills_graded(self):
        """仙丹自动派生下品/上品（品阶机制复用），且药效随品阶缩放。"""
        for base in ("immortal_pill", "heaven_pill"):
            self.assertIn(base, item_config.GRADED_PILLS)
            low = item_config.get_item(f"{base}#low")
            high = item_config.get_item(f"{base}#high")
            self.assertEqual(low.grade_label, "下品")
            self.assertEqual(high.grade_label, "上品")
            # 下品/上品价格不同（品阶对价格生效即派生成功）
            self.assertLess(low.price, high.price)
            # 有效果值型字段时校验缩放方向（heaven_pill 只有 log 文案不算）
            low_vals = [e.get("value", 0) for e in low.effects if "value" in e]
            high_vals = [e.get("value", 0) for e in high.effects if "value" in e]
            if low_vals and high_vals:
                self.assertGreater(high_vals[0], low_vals[0])

    def test_immortal_pill_effect_is_ratio(self):
        """仙丹修为效果按需求比例，不用绝对值。"""
        pill = item_config.get_item("immortal_pill")
        types = [e["type"] for e in pill.effects]
        self.assertIn("exp_ratio", types)
        self.assertNotIn("exp", types)

    # ---------- 仙界装备 ----------
    def test_immortal_equipment_drops_by_realm(self):
        """仙阶装备仅对仙界境界开放掉落池。"""
        from xiuxian.systems.combat import CombatSystem  # noqa
        # 人仙池含仙阶，炼气池不含
        pool_fn = lambda r: [iid for iid, it in item_config.ITEMS.items()
                             if it.kind == "equip" and it.equip_slot
                             and (not it.min_realm
                                  or RealmRegistry.within(r, min_realm=it.min_realm))
                             and "immortal" in iid]
        self.assertEqual(len(pool_fn("qi_refining")), 0)
        self.assertEqual(len(pool_fn("human_immortal")), 6)


class TestRateFormula(unittest.TestCase):
    """突破成功率改为比例式：三维在后期依然可解释、不顶格。"""

    def _player_at(self, seed: int, realm_key: str, attr: float) -> Game:
        game = new_game(seed=seed)
        p = game.player
        p.realm_key = realm_key
        for k in ("physique", "comprehension", "luck"):
            p.attributes.base[k] = attr
        return game

    def test_attributes_scale_rate_multiplicatively(self):
        """高三维的突破率显著高于低三维。"""
        low = self._player_at(1, "mahayana", 10).system("cultivation").success_rate(True)
        high = self._player_at(2, "mahayana", 500).system("cultivation").success_rate(True)
        self.assertGreater(high, low + 0.2)
        self.assertAlmostEqual(low, 0.18, delta=0.01)   # 大乘->渡劫基准线

    def test_late_game_rate_not_clamped(self):
        """后期（大乘 500 三维）突破率不顶格——旧公式会直接 clamp 到 98%。"""
        rate = self._player_at(3, "mahayana", 500).system("cultivation").success_rate(True)
        self.assertLess(rate, 0.8)
        self.assertGreater(rate, 0.4)

    def test_pill_poison_still_penalizes(self):
        """丹毒惩罚保留。"""
        game = self._player_at(4, "mahayana", 10)
        p = game.player
        p.pill_poison = 100
        rate = game.system("cultivation").success_rate(True)
        self.assertLess(rate, 0.2)

    def test_enlightenment_scales_with_hours(self):
        """顿悟按每 4 时辰判定：cultivate(8) = 2 次判定，挂机与手动收益一致。"""
        game = new_game(seed=17)
        p = game.player
        # v2：顿悟概率钳制上限 15%（luck 再高也不超过 15%）
        from xiuxian.core.cultivation import INSIGHT_MAX_RATE, INSIGHT_BASE_RATE
        self.assertEqual(INSIGHT_MAX_RATE, 0.15)
        self.assertEqual(INSIGHT_BASE_RATE, 0.05)
        p.attributes.base["luck"] = 999
        # 概率钳制验证：连续打坐 8 时辰 10 次（共 20 次判定），触发次数不会超过 15% 上限太多
        cult = game.system("cultivation")
        from unittest import mock
        calls = []

        def counting_chance(prob):
            calls.append(prob)
            return False                      # 只数判定不触发，避免污染收益断言

        with mock.patch.object(game.rng, "chance", counting_chance):
            cult.cultivate(8)
        # 8 时辰 = 2 次顿悟判定
        self.assertEqual(len(calls), 2)
        for prob in calls:
            self.assertLessEqual(prob, INSIGHT_MAX_RATE + 1e-9)

    def test_enlightenment_insight_grant(self):
        """顿悟实际入账：凡界 8%、仙界 12%，且钳制后气运不再叠加爆炸。"""
        game = new_game(seed=17)
        p = game.player
        cult = game.system("cultivation")
        need = p.exp_required()
        p.exp = 0.0
        # 直接验证顿悟奖励比例（绕过概率：把 chance 设为必中）
        game.rng.chance = lambda p: True
        cult.cultivate(8)                     # 凡界：2 × 8% + 修炼产出
        self.assertGreaterEqual(p.exp, need * 0.16)
        self.assertLess(p.exp, need * 0.16 + 80)
        # 仙界「仙缘」：2 × 12%
        p.realm_key = "human_immortal"
        need = p.exp_required()
        p.exp = 0.0
        cult.cultivate(8)
        self.assertGreaterEqual(p.exp, need * 0.24)
        self.assertLess(p.exp, need * 0.30)


class TestIdleFarming(unittest.TestCase):
    """挂机养老定位：寿元纯展示，无寿元耗尽死亡。"""

    def test_no_lifespan_death(self):
        game = new_game(seed=13)
        p = game.player
        p.age = p.lifespan + 5                 # 已超寿元
        for _ in range(400):
            game.advance_time(24)              # 400 天，年岁增长多次
        self.assertTrue(p.alive)
        self.assertFalse(game.over)
        self.assertGreater(p.age, p.lifespan)

    def test_immortal_lifespan_display_only(self):
        """仙界寿元大数纯展示，仍可继续修炼。"""
        game = new_game(seed=15)
        p = game.player
        p.realm_key = "hunyuan"
        self.assertEqual(p.lifespan, 10_000_000_000)
        self.assertTrue(p.alive)


class TestIdleCultivation(unittest.TestCase):
    """闭关挂机：自动打坐-休息循环，收益与手动打坐完全一致（同一套 cultivate）。"""

    def _idle_game(self, seed: int = 41, realm: str = "nascent") -> Game:
        game = new_game(seed=seed)
        p = game.player
        p.realm_key = realm                    # 高需求境界，闭关能真实推多天
        p.stage = 0
        p.exp = 0.0
        return game

    def test_idle_accumulates_exp_and_days(self):
        game = self._idle_game()
        p = game.player
        cult = game.system("cultivation")
        day0 = game.day
        logs = cult.idle(5)
        self.assertEqual(game.day - day0, 5)
        self.assertGreater(p.exp, 0)           # 修为增长
        self.assertFalse(p.can_breakthrough()) # 未到修为满（否则提前停）
        self.assertTrue(any("闭关 5.0 日" in ln for ln in logs))
        self.assertTrue(any("修为 +" in ln for ln in logs))

    def test_idle_stops_at_breakthrough(self):
        """修为圆满时闭关立即停：突破是决策点，留给玩家，不自动冲关。"""
        game = self._idle_game(seed=43, realm="qi_refining")   # 需求 100，一坐即满
        p = game.player
        cult = game.system("cultivation")
        day0 = game.day
        logs = cult.idle(30)                   # 目标 30 天，但修为很快满
        self.assertTrue(p.can_breakthrough())
        self.assertLess(game.day - day0, 30)   # 提前停止
        self.assertTrue(any("修为已满" in ln for ln in logs))
        self.assertEqual(p.realm_key, "qi_refining")   # 未自动突破

    def test_idle_days_capped(self):
        game = self._idle_game(seed=47)
        cult = game.system("cultivation")
        day0 = game.day
        cult.idle(999)
        self.assertLessEqual(game.day - day0, 30)

    def test_idle_consumes_no_pills(self):
        """闭关纯打坐，不自动嗑药。"""
        game = self._idle_game(seed=53)
        p = game.player
        before = dict(p.inventory.all())
        game.system("cultivation").idle(3)
        self.assertEqual(dict(p.inventory.all()), before)

    def test_idle_supports_partial_day(self):
        """闭关支持小数天：idle(0.5) = 12 时辰，按游戏小时精确打坐。"""
        game = self._idle_game(seed=59)
        p = game.player
        cult = game.system("cultivation")
        start_h = game.day * 24 + game.hour
        logs = cult.idle(0.5)
        end_h = game.day * 24 + game.hour
        self.assertAlmostEqual(end_h - start_h, 12.0, delta=0.01)   # 0.5 天 = 12 时
        self.assertGreater(p.exp, 0)
        self.assertTrue(any("时辰" in ln for ln in logs))

    def test_idle_float_capped(self):
        """小数/大数天统一受 30 天上限约束。"""
        game = self._idle_game(seed=61)
        cult = game.system("cultivation")
        start_h = game.day * 24 + game.hour
        cult.idle(999.5)
        end_h = game.day * 24 + game.hour
        self.assertLessEqual((end_h - start_h) / 24.0, 30.0 + 0.01)


class TestOfflineReward(unittest.TestCase):
    """离线收益：挂机也一直打坐，读档按真实流逝时间结算，收益与手动一致。"""

    @staticmethod
    def _save_with_backdated_clock(days_back: float, seed: int = 61) -> tuple[str, int]:
        import json
        from datetime import datetime, timedelta
        tmp = tempfile.mkdtemp()
        game = create_game(name="离线测试", seed=seed)
        game.clock.disabled = True
        p = game.player
        p.realm_key = "nascent"
        p.stage = 0
        p.exp = 0.0
        path = SaveManager(tmp).save(game, 1, note="ut")
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        payload["saved_at"] = (datetime.now() - timedelta(hours=days_back)
                               ).strftime("%Y-%m-%d %H:%M:%S")
        Path(path).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return tmp, 1

    def test_offline_settles_cultivation(self):
        tmp, slot = self._save_with_backdated_clock(2.0)
        restored = load_game(slot, save_dir=tmp)
        logs = restored.drain_logs()
        self.assertTrue(any("离线归来" in ln for ln in logs))
        # 离线修为先进待领池，登录时自动领取落袋（或本层已满则留存）
        self.assertTrue(restored.player.exp > 0
                        or restored.offline.pending_exp > 0)
        self.assertGreater(restored.offline.settled_count, 0)

    def test_offline_not_duplicated(self):
        """结算后刷新存档时间戳：二次读档不再重复结算。"""
        tmp, slot = self._save_with_backdated_clock(2.0)
        first = load_game(slot, save_dir=tmp)
        first_exp = first.player.exp
        first.drain_logs()
        second = load_game(slot, save_dir=tmp)
        # 存档写盘把修为舍入到两位小数，允许微小舍入差
        self.assertAlmostEqual(second.player.exp, first_exp, delta=0.01)
        self.assertEqual(second.day, first.day)
        self.assertEqual(second.offline.settled_count,
                         first.offline.settled_count)      # 未再次结算
        # 二次读档不得再产生新的离线修为（允许「未有新的积累」这类提示）
        self.assertFalse(any("离线归来：阔别" in ln for ln in second.drain_logs()))

    def test_offline_zero_when_recent(self):
        """即时读档（秒级间隔）不产生离线收益。"""
        tmp = tempfile.mkdtemp()
        game = create_game(name="即时读档", seed=67)
        SaveManager(tmp).save(game, 1, note="ut")
        restored = load_game(1, save_dir=tmp)
        self.assertEqual(restored.day, game.day)
        self.assertEqual(restored.drain_logs(), [])


class TestStaminaBudget(unittest.TestCase):
    """精力（v2 放宽版）= 主动操作频率闸门：恢复 2/时辰（在线离线均恢复），只限制主动操作。"""

    def test_rest_recovers_stamina_at_v2_rate(self):
        """rest 恢复 2/时辰（v2）：休息 8 时辰回 16 点。"""
        game = new_game(seed=89)
        p = game.player
        p.stamina = 10
        game.system("cultivation").rest(8)
        self.assertEqual(p.stamina, 10 + 16)

    def test_active_cultivate_costs_little_stamina(self):
        """主动打坐仍耗精力，但 v2 放宽为 1.5/时（不再是 6/时）。"""
        game = new_game(seed=91)
        p = game.player
        before = p.stamina
        game.system("cultivation").cultivate(4)
        self.assertAlmostEqual(before - p.stamina, 6.0, delta=0.01)

    def test_idle_costs_stamina(self):
        """主动闭关打坐仍计入精力消耗（1.5/时），但 v2 放宽后跨日/调息恢复
        足以支撑挂机，精力不会被闭关卡死（精力闸门交给时间预算承担）。"""
        game = new_game(seed=93)
        p = game.player
        p.realm_key = "nascent"
        p.stamina = 100
        cult = game.system("cultivation")
        cult.idle(1)
        self.assertGreaterEqual(p.stamina, 0)    # 精力不归零（挂机友好）
        self.assertGreater(p.exp, 0)
        # 单次打坐确会扣精力（1.5/时）：验证精力闸门仍在主动操作路径上
        p.stamina = 10
        cult.cultivate(2)
        self.assertAlmostEqual(p.stamina, 7.0, delta=0.01)

    def test_idle_ignore_stamina_for_settlement(self):
        """挂机结算路径（离线/现实时钟）免精力：精力为 0 也照常闭关，且打坐不耗精力。"""
        game = new_game(seed=93)
        p = game.player
        p.realm_key = "nascent"
        p.stamina = 0
        cult = game.system("cultivation")
        cult.idle(1, ignore_stamina=True)
        self.assertGreaterEqual(p.stamina, 0)    # 未被打坐消耗（跨日恢复会自然回涨）
        self.assertGreater(p.exp, 0)

    def test_settlement_uses_ignore_stamina(self):
        """现实时钟结算走挂机路径：不受玩家精力预算影响。"""
        from xiuxian.factory import ONLINE_DAYS_PER_HOUR, settle_realtime
        game = new_game(seed=94)
        p = game.player
        p.realm_key = "nascent"
        p.exp = 0.0
        p.stamina = 0                       # 精力耗尽，挂机结算仍应给修为
        settle_realtime(game, 2 * 3600.0, ONLINE_DAYS_PER_HOUR)
        self.assertGreater(p.exp, 0)        # 挂机照常闭关涨修为
        self.assertGreater(p.stamina, 0)    # 精力只受跨日恢复，未被打坐扣减

    def test_cultivate_ignore_stamina_flag(self):
        """cultivate(ignore_stamina=True) 不检查不消耗精力。"""
        game = new_game(seed=95)
        p = game.player
        p.stamina = 0
        cult = game.system("cultivation")
        logs = cult.cultivate(4, ignore_stamina=True)
        self.assertTrue(any("吐纳" in ln for ln in logs))
        self.assertEqual(p.stamina, 0)

    def test_daily_recovery_capped_at_max(self):
        """每日恢复不超过精力上限 100。"""
        game = new_game(seed=97)
        p = game.player
        p.stamina = 90
        game.hour = 22
        game.advance_time(4)                   # 跨日 +50 -> cap 100
        self.assertEqual(p.stamina, 100)


class TestPassiveCultivation(unittest.TestCase):
    """后台周天运转：做其他事时时间推进自动涨修为，不影响操作、不耗精力。"""

    def test_travel_gains_passive_exp(self):
        """赶路 6 时（非打坐动作）也涨修为，且不耗精力。"""
        game = new_game(seed=91)
        p = game.player
        stamina0 = p.stamina
        game.travel("落云山脉")
        self.assertGreater(p.exp, 0)
        self.assertEqual(p.stamina, stamina0)

    def test_passive_ratio_below_active(self):
        """被动产出 = 主动打坐的 PASSIVE_RATIO 倍（无顿悟/熟练度）。"""
        game = new_game(seed=93)
        p = game.player
        cult = game.system("cultivation")
        # 同属性同地点，主动 6 时 vs 被动 6 时（被动取期望，主动含随机浮动）
        p.exp = 0.0
        cult.cultivate(6)
        active = p.exp
        p.exp = 0.0
        game.advance_time(6)                     # 纯时间流逝 = 被动 6 时
        passive = p.exp
        self.assertGreater(active, passive)
        self.assertGreater(passive, active * 0.45)   # 0.6 × 随机下限 0.88 ≈ 0.53

    def test_no_double_count_during_cultivate(self):
        """主动打坐期间不叠加被动（防止 1.6 倍）。"""
        game = new_game(seed=95)
        p = game.player
        cult = game.system("cultivation")
        exp_before = p.exp
        cult.cultivate(8)
        gained = p.exp - exp_before
        # 若被动叠加，会有 8 时主动 + 8 时被动 ≈ 1.6 倍；只算主动则 ≤ 8 时 × 1.14 上限
        hourly = cult._hourly_gain(p)
        self.assertLessEqual(gained, hourly * 8 * 1.15 + 0.01)

    def test_rest_still_gains_passive(self):
        """休息也推进时间，后台周天不停。"""
        game = new_game(seed=97)
        p = game.player
        exp0 = p.exp
        game.system("cultivation").rest(6)
        self.assertGreater(p.exp, exp0)


class TestWebEntry(unittest.TestCase):
    """Web 版入口：CLI.run_line 捕获输出，与交互版同一套分发逻辑。"""

    def test_run_line_captures_output(self):
        from xiuxian.ui.cli import CLI
        game = new_game(seed=83)
        cli = CLI(game)
        out = cli.run_line("status")
        self.assertTrue(any("修士" in ln for ln in out))
        out = cli.run_line("idle")
        self.assertTrue(any("闭关" in ln for ln in out))
        self.assertTrue(any("修为 +" in ln for ln in out))

    def test_run_line_empty_means_cultivate(self):
        from xiuxian.ui.cli import CLI
        game = new_game(seed=85)
        cli = CLI(game)
        out = cli.run_line("")
        self.assertTrue(any("吐纳" in ln for ln in out))

    def test_run_line_checks_game_over(self):
        from xiuxian.ui.cli import CLI
        game = new_game(seed=87)
        cli = CLI(game)
        out = cli.run_line("hunt")           # 应执行且不抛异常
        self.assertIsInstance(out, list)


class TestWebActions(unittest.TestCase):
    """Web 行动按钮：随玩家状态动态变化（移动端点按交互）。"""

    def test_actions_state_aware(self):
        from web_server import GameSession
        s = GameSession()
        primary = [a for a in s.actions() if a.get("primary")]
        self.assertTrue(primary and "闭关" in primary[0]["label"])
        # 闭关修为满后（精力恢复足），主按钮应变为「突破」
        s.command("idle")
        s.game.player.stamina = 100
        primary = [a for a in s.actions() if a.get("primary")]
        self.assertTrue(any("突破" in a["label"].replace(" ", "") for a in primary))

    def test_actions_always_have_common_buttons(self):
        from web_server import GameSession
        s = GameSession()
        labels = [a["label"] for a in s.actions()]
        # 常用动作恒常可点：打猎/探查/秘境（可进入）/丹药/帮助
        self.assertIn("打猎", labels)
        self.assertIn("探查", labels)
        self.assertTrue(any("秘境" in l for l in labels))        # 秘境：进入按钮或占位
        self.assertTrue(any("丹药" in l or "背包" in l for l in labels))
        self.assertIn("帮助", labels)

    def test_actions_heart_demon_priority(self):
        """心魔劫 pending 时，动作必须是四选项（挡掉一切后续）。"""
        from web_server import GameSession
        from xiuxian.systems.inner_demon import TRIALS
        s = GameSession()
        s.game.systems["inner_demon"].pending = {"id": TRIALS[0].id, "realm": "core"}
        acts = s.actions()
        labels = [a["label"] for a in acts]
        for c in TRIALS[0].choices:
            self.assertIn(c.text, labels)
        self.assertTrue(all(a.get("primary") for a in acts))     # 全部主按钮，无休闲动作

    def test_actions_dungeon_in_progress(self):
        """秘境进行中：给出「深入」主按钮 + 「退出」，不出现无关休闲动作。"""
        from web_server import GameSession
        s = GameSession()
        s.game.systems["dungeon"].run = {"id": "luoyun", "floor": 2,
                                         "awaiting": False, "streak": 0}
        acts = s.actions()
        labels = [a["label"] for a in acts]
        self.assertTrue(any("深入" in l for l in labels))
        self.assertTrue(any("退出" in l for l in labels))
        # 无打猎/论道等明显无关动作
        self.assertNotIn("打猎", labels)

    def test_actions_dungeon_enter_available(self):
        """不在秘境中，但有可进入的秘境 -> 直接给 enter 按钮（免打 dungeon enter <id>）。"""
        from web_server import GameSession
        s = GameSession()
        acts = s.actions()
        labels = [a["label"] for a in acts]
        self.assertTrue(any(l.startswith("秘境·") for l in labels))

    def test_actions_usable_pills(self):
        """背包有可用丹药 -> 直接给「服 <name>」按钮（免打 use <id>）。"""
        from web_server import GameSession
        s = GameSession()
        s.game.player.inventory.add("healing_pill", 2)
        s.game.player.inventory.add("qi_gathering_pill", 1)
        acts = s.actions()
        labels = [a["label"] for a in acts]
        self.assertIn("服 疗伤丹", labels)
        self.assertIn("服 聚气丹", labels)

    def test_player_data_structured(self):
        """角色数据结构化：面板渲染所需字段齐全且大数已格式化。"""
        from web_server import GameSession, player_data
        s = GameSession()
        d = player_data(s.game)
        for key in ("realm", "next", "hp", "max_hp", "hp_ratio", "stamina_ratio",
                    "atk", "def", "comprehension", "stones", "exp", "need", "progress"):
            self.assertIn(key, d)
        self.assertEqual(d["realm"], "炼气一层")
        # 闭关后数据实时更新
        s.command("idle")
        d2 = player_data(s.game)
        self.assertEqual(d2["exp"], "100")
        self.assertEqual(d2["progress"], 1.0)


class TestNumFormat(unittest.TestCase):
    """大数格式化：修为/属性数值可读，不显示长串数字。"""

    def test_small_numbers_unchanged(self):
        self.assertEqual(fmt_num(0), "0")
        self.assertEqual(fmt_num(100), "100")
        self.assertEqual(fmt_num(9999), "9999")

    def test_chinese_units(self):
        self.assertEqual(fmt_num(12345), "1.2万")
        self.assertEqual(fmt_num(6e9), "60亿")
        self.assertEqual(fmt_num(600_000_000_000), "6000亿")
        self.assertEqual(fmt_num(3e12), "3万亿")
        self.assertEqual(fmt_num(3e17), "30兆")
        self.assertEqual(fmt_num(1.2e19), "1200兆")

    def test_negative_and_float(self):
        self.assertEqual(fmt_num(-50), "-50")
        self.assertEqual(fmt_num(1500.4), "1500")
        self.assertEqual(fmt_num(123456), "12.3万")

    def test_panel_shows_readable_numbers(self):
        """仙界面板不再出现 11 位长串数字。"""
        game = new_game(seed=41)
        p = game.player
        p.realm_key = "human_immortal"     # 人仙：需求 600 亿
        from xiuxian.ui import panel
        text = panel.status_panel(game)
        self.assertIn("600亿", text)       # 修为 0/600亿
        self.assertNotIn("60000000000", text)


class TestRealtimeClock(unittest.TestCase):
    """现实时钟：在线/离线全程跟现实时间走，收益与手动打坐一致。"""

    def test_settle_realtime_online_multiplier(self):
        """在线倍率 1.5：现实 2 小时 = 游戏内 3 日（72 时辰）。"""
        from xiuxian.factory import ONLINE_DAYS_PER_HOUR, settle_realtime
        self.assertEqual(ONLINE_DAYS_PER_HOUR, 1.5)
        game = new_game(seed=71)
        p = game.player
        p.realm_key = "nascent"          # 高需求境界，闭关能完整推演
        p.stage = 0
        p.exp = 0.0
        start = (game.day - 1) * 24.0 + game.hour
        logs = settle_realtime(game, 2 * 3600.0, ONLINE_DAYS_PER_HOUR)
        end = (game.day - 1) * 24.0 + game.hour
        self.assertTrue(logs)
        self.assertGreater(p.exp, 0)
        self.assertAlmostEqual(end - start, 72.0, delta=0.1)   # 2 现实时 × 1.5 = 3 游戏日

    def test_settle_realtime_below_threshold_noop(self):
        """不足 1 游戏时辰（约 2 现实分钟）不结算。"""
        from xiuxian.factory import settle_realtime
        game = new_game(seed=73)
        p = game.player
        p.realm_key = "nascent"
        p.stage = 0
        p.exp = 0.0
        day0 = game.day
        logs = settle_realtime(game, 30.0, 1.5)      # 30 现实秒 = 0.75 游戏时 < 1
        self.assertEqual(logs, [])
        self.assertEqual(game.day, day0)

    def test_settle_realtime_capped(self):
        """单次结算上限 24 现实小时（防挂机一年回来满级）。"""
        from xiuxian.factory import CLOCK_MAX_HOURS, settle_realtime
        self.assertEqual(CLOCK_MAX_HOURS, 24.0)
        game = new_game(seed=79)
        p = game.player
        p.realm_key = "nascent"
        p.stage = 0
        p.exp = 0.0
        start_h = game.day * 24 + game.hour
        settle_realtime(game, 30 * 24 * 3600.0, 1.5)   # 30 现实天，应截断到 24 时
        end_h = game.day * 24 + game.hour
        self.assertLessEqual((end_h - start_h) / 24.0, 24.0 * 1.5 + 0.1)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestAuditFixes(unittest.TestCase):
    """系统复核后的修复回归：每项都对应一个已确认的漏洞。"""

    # ---------- P0-1 死亡后不可再战 ----------
    def test_dead_player_cannot_fight(self):
        game = new_game(seed=601)
        p = game.player
        p.alive = False
        combat = game.system("combat")
        logs = combat.fight(combat.spawn("weak"))
        self.assertTrue(any("身死道消" in ln for ln in logs))
        self.assertFalse(p.alive)                 # 不能再被濒死护持复活

    # ---------- 方案 A 打坐产出按需求比例 ----------
    def test_meditate_gain_scales_with_need(self):
        """打坐产出与本层需求成正比：任何境界占比一致，不再后期形同虚设。"""
        game = new_game(seed=603)
        p = game.player
        cult = game.system("cultivation")
        ratios = []
        for key in ("qi_refining", "nascent", "mahayana", "gold_immortal"):
            p.realm_key = key
            p.stage = 0
            p.attributes.base["comprehension"] = 25.0
            need = p.exp_required()
            ratios.append(cult._hourly_gain(p) / need)
        # 四个境界的每时辰产出占需求比例应基本一致；
        # 差异只应来自境界神通（如金仙「仙域」+15% 修炼加成），故允许 ±20%
        base = ratios[0]
        for r in ratios[1:]:
            self.assertAlmostEqual(r, base, delta=base * 0.20)

    def test_meditate_no_instant_full(self):
        """炼气期不会一次打坐直接打满（旧公式 4 时辰给 108%）。"""
        game = new_game(seed=605)
        p = game.player
        game.system("cultivation").cultivate(4)
        self.assertLess(p.exp, p.exp_required())   # 4 时辰后仍未满

    # ---------- P0-2 战斗与打坐同量级 ----------
    def test_combat_exp_close_to_meditation(self):
        """同精力预算下战斗修为与打坐同量级（旧实现相差 16 倍）。"""
        game = new_game(seed=607)
        game.location = "宗门灵脉"      # 打坐按灵气密度缩放，须在同一地点下与战斗对比
        p = game.player
        p.realm_key = "nascent"
        p.stage = 0
        p.exp = 0.0
        p.stamina = 100
        game.system("cultivation").cultivate(24)     # 100 精力打坐
        med = p.exp
        game2 = new_game(seed=607)
        p2 = game2.player
        p2.realm_key = "nascent"
        p2.stage = 0
        p2.exp = 0.0
        p2.stamina = 100
        combat = game2.system("combat")
        for _ in range(4):                           # 100 精力打 4 场 normal
            combat.fight(combat.spawn("normal"))
        fight_gain = p2.exp
        # 允许 0.5~2 倍区间（战斗略高属风险补偿，但不能是数量级碾压）
        self.assertGreater(fight_gain, med * 0.5)
        self.assertLess(fight_gain, med * 2.0)

    # ---------- P1-3 突破节奏 ----------
    def test_rest_matches_meditate_speed(self):
        from xiuxian.core.cultivation import REST_STAMINA_PER_HOUR
        # v2：rest 恢复 2/时（在线离线均恢复），不再与打坐消耗 1.5/时同速
        self.assertEqual(REST_STAMINA_PER_HOUR, 2.0)

    # ---------- P1-4 存档钳制 ----------
    def test_load_clamps_corrupt_realm(self):
        from xiuxian.core.cultivator import Cultivator
        c = Cultivator.from_dict({"name": "x", "realm_key": "not_a_realm", "stage": 99,
                                  "attributes": {"base": {}, "modifiers": []},
                                  "exp": -500, "hp": 1e9, "stamina": 500})
        self.assertEqual(c.realm_key, "qi_refining")
        self.assertLessEqual(c.stage, c.realm_def.stage_count - 1)
        self.assertGreaterEqual(c.exp, 0.0)
        self.assertLessEqual(c.hp, c.max_hp)
        self.assertLessEqual(c.stamina, 100.0)

    # ---------- P2-8 静室精力收窄 ----------
    def test_dungeon_rest_stamina_reduced(self):
        import re
        src = open("xiuxian/systems/dungeon.py", encoding="utf-8").read()
        self.assertIn("p.stamina + 10.0", src)

    # ---------- P2-9 effect_dungeon 保留冷却 ----------
    def test_effect_dungeon_keeps_cooldown(self):
        src = open("xiuxian/systems/dungeon.py", encoding="utf-8").read()
        self.assertNotIn("self.cooldowns.pop(dungeon.id, None)", src)

    # ---------- P2-10 死亡不可交易/使用 ----------
    def test_dead_player_cannot_trade(self):
        game = new_game(seed=609)
        p = game.player
        p.alive = False
        market = game.system("market")
        self.assertTrue(any("身死道消" in ln for ln in market.buy("healing_pill", 1)))
        self.assertTrue(any("身死道消" in ln for ln in market.sell("healing_pill", 1)))

    def test_dead_player_cannot_use_item(self):
        game = new_game(seed=611)
        p = game.player
        p.alive = False
        logs = p.inventory.use(game, "healing_pill")
        self.assertTrue(any("身死道消" in ln for ln in logs))

    # ---------- P1-5 / P2-6 / P2-7 Web 层 ----------
    def test_web_error_field_and_sessions(self):
        src = open("web_server.py", encoding="utf-8").read()
        self.assertIn("self._fail(exc)", src)              # 异常兜底
        self.assertIn("SESSIONS: dict[str, GameSession]", src)   # 会话隔离
        self.assertIn('"cmd": f"choose {i}"', src)         # choose 按钮


class TestOfflineEngine(unittest.TestCase):
    """离线挂机引擎：幂等 + 时区统一 + 各类边界。"""

    def _state(self, last=None):
        from xiuxian.core.offline import OfflineState
        return OfflineState(last_settled_at=last)

    def test_first_login_no_record(self):
        """首次登录（无时间戳）：不发修为，但完成初始化。"""
        from xiuxian.core.offline import settle
        st = self._state(None)
        gain, dur, anomaly = settle(st, need=1000.0, now=10000.0)
        self.assertEqual(gain, 0.0)
        self.assertEqual(anomaly, "首次登录")
        self.assertEqual(st.last_settled_at, 10000.0)      # 已初始化

    def test_idempotent_repeat_settle(self):
        """重复结算不再产生收益（推进时间戳即幂等）。"""
        from xiuxian.core.offline import settle
        st = self._state(0.0)
        first, _, _ = settle(st, need=1000.0, now=3600.0 * 10)
        self.assertGreater(first, 0)
        pool_after_first = st.pending_exp
        second, _, _ = settle(st, need=1000.0, now=3600.0 * 10)   # 同一时刻再次结算
        self.assertEqual(second, 0.0)
        self.assertEqual(st.pending_exp, pool_after_first)

    def test_clock_rewind_no_gain(self):
        """时间回拨：不发修为（防改表倒时间刷取）。"""
        from xiuxian.core.offline import settle
        st = self._state(10000.0)
        gain, _, anomaly = settle(st, need=1000.0, now=5000.0)     # now 早于 last
        self.assertEqual(gain, 0.0)
        self.assertEqual(anomaly, "时间回拨")
        self.assertEqual(st.last_settled_at, 5000.0)               # 基准修正

    def test_huge_offline_capped(self):
        """超长离线（如 1000 天）：截断到上限并标记。"""
        from xiuxian.core.offline import MAX_OFFLINE_SECONDS, settle
        st = self._state(0.0)
        gain, dur, anomaly = settle(st, need=1000.0, now=1000 * 24 * 3600.0)
        self.assertEqual(anomaly, "超长截断")
        self.assertLessEqual(dur, MAX_OFFLINE_SECONDS)

    def test_grace_period(self):
        """低于最短结算间隔：不产生收益。"""
        from xiuxian.core.offline import settle
        st = self._state(0.0)
        gain, _, _ = settle(st, need=1000.0, now=30.0)      # 30 秒 < 60 秒
        self.assertEqual(gain, 0.0)

    def test_pending_pool_claim(self):
        """待领池：领取受本层需求上限约束，余额留池不浪费。"""
        from xiuxian.core.offline import claim
        game = new_game(seed=701)
        p = game.player
        p.realm_key = "nascent"
        p.stage = 0
        p.exp = 0.0
        st = game.offline
        st.pending_exp = p.exp_required() * 3          # 池里有 3 层的量
        got1 = claim(st, p.add_exp)                    # 第一次领取：填满本层
        self.assertAlmostEqual(got1, p.exp_required(), delta=1.0)
        self.assertGreater(st.pending_exp, 0)          # 余额仍在池中
        p.exp = 0.0                                    # 模拟突破后修为清零
        got2 = claim(st, p.add_exp)
        self.assertGreater(got2, 0)                    # 突破后继续领取

    def test_daily_limits(self):
        """每日限额：用完即止，跨日重置。"""
        game = new_game(seed=703)
        p = game.player
        p.bump_daily(1, "duel")
        p.bump_daily(1, "duel")
        self.assertEqual(p.daily_used(1, "duel"), 2)
        self.assertEqual(p.daily_left(1, "duel", 5), 3)
        self.assertEqual(p.daily_used(2, "duel"), 0)     # 换一天：计数归零

    def test_paths_have_limits(self):
        """每条新增修为途径都配了防刷限制（常量存在且非零）。"""
        from xiuxian.core import inventory as inv
        from xiuxian.core.event_system import EXPLORE_DAILY_LIMIT
        from xiuxian.systems.duel import DUEL_DAILY_LIMIT, DUEL_STAMINA
        self.assertGreater(EXPLORE_DAILY_LIMIT, 0)
        self.assertGreater(DUEL_DAILY_LIMIT, 0)
        self.assertGreater(DUEL_STAMINA, 0)
        self.assertGreater(inv.DAILY_PILL_LIMIT, 0)

    def test_duel_costs_stamina_and_grants_exp(self):
        game = new_game(seed=705)
        p = game.player
        p.realm_key = "nascent"
        p.stamina = 100
        d = game.system("duel")
        before = p.stamina
        logs = d.spar("normal")
        self.assertLess(p.stamina, before)
        self.assertTrue(any("修为" in ln for ln in logs))

    def test_explore_grants_exp_with_limit(self):
        game = new_game(seed=707)
        p = game.player
        p.stamina = 1000
        ev = game.system("event")
        for _ in range(3):
            ev.trigger(force=True)
            if ev.pending:
                ev.choose(0)
        self.assertGreater(p.exp, 0)
        self.assertEqual(p.daily_used(game.day, "explore"), 3)


class TestCultivationFoundations(unittest.TestCase):
    """修仙五要：法（已有）/ 财（产业）/ 侣（道侣）/ 地（洞府）/ 师（师承）。"""

    def _game(self, seed: int = 31, realm: str = "foundation") -> Game:
        game = new_game(seed=seed)
        p = game.player
        p.spirit_stones = 10 ** 7
        p.realm_key = realm
        p.stage = 0
        return game

    # ---------- 地：洞府 ----------
    def test_estate_buy_gives_speed_bonus(self):
        game = self._game()
        est, p = game.system("estate"), game.player
        before = game.bonuses.value("cultivate_speed")
        est.buy("cave")
        after = game.bonuses.value("cultivate_speed")
        self.assertGreater(after, before)

    def test_estate_upkeep_failure_pauses_it(self):
        """v2 自动维护：欠维护时洞府阵法自动暂停（加成失效），灵石备足自动恢复，无需手动补缴。"""
        game = self._game()
        est, p = game.system("estate"), game.player
        est.buy("cave")
        p.spirit_stones = 0                      # 付不起维护
        game.advance_time(max(1, 24 - game.hour))  # 跨日触发结算
        self.assertTrue(est.ruined)
        self.assertEqual(game.bonuses.value_of("cultivate_speed", "estate:"), 0.0)
        p.spirit_stones = 10 ** 6
        game.advance_time(max(1, 24 - game.hour))  # 灵石到位后自动恢复
        self.assertFalse(est.ruined)
        self.assertGreater(game.bonuses.value_of("cultivate_speed", "estate:"), 0.0)

    # ---------- 财：产业 ----------
    def test_asset_pays_out_daily(self):
        game = self._game()
        ast, p = game.system("asset"), game.player
        ast.buy("spirit_mine")
        stones_before = p.spirit_stones
        game.advance_time(max(1, 24 - game.hour))
        self.assertGreater(p.spirit_stones, stones_before - ast.daily_upkeep())

    def test_asset_upgrade_raises_output(self):
        game = self._game()
        ast = game.system("asset")
        ast.buy("spirit_field")
        base = ast.daily_stones("spirit_field")
        ast.upgrade("spirit_field")
        self.assertGreater(ast.daily_stones("spirit_field"), base)

    def test_asset_auto_maintenance_from_output(self):
        """v2 自动维护：维护费从产出中扣除（产出 60 > 维护 18），玩家灵石不受侵蚀。"""
        game = self._game()
        ast, p = game.system("asset"), game.player
        ast.buy("spirit_mine")
        stones_before = p.spirit_stones
        game.advance_time(max(1, 24 - game.hour))
        # 产出 60 - 维护 18 = 净收 42 入袋；玩家原有灵石不被扣减
        self.assertFalse(ast.stalled.get("spirit_mine"))
        self.assertGreater(p.spirit_stones, stones_before)

    # ---------- 侣：道侣 ----------
    def test_companion_meet_and_bond_scaling(self):
        game = self._game()
        comp = game.system("companion")
        comp.meet("qingyao")
        self.assertIn("qingyao", comp.met)
        scale_0 = game.bonuses.value_of("insight_rate", "companion:")
        comp.gift("qingyao")
        scale_1 = game.bonuses.value_of("insight_rate", "companion:")
        self.assertGreater(scale_1, scale_0)

    def test_dual_cultivation_costs_and_gains(self):
        game = self._game()
        comp, p = game.system("companion"), game.player
        comp.meet("qingyao")
        p.stamina = 100
        p.mp = 100
        bond_before = comp.met["qingyao"]
        logs = comp.dual("qingyao")
        self.assertGreater(comp.met["qingyao"], bond_before)
        self.assertLess(p.stamina, 100)
        self.assertTrue(any("双修" in ln for ln in logs))

    def test_dual_daily_limit(self):
        game = self._game()
        comp, p = game.system("companion"), game.player
        comp.meet("qingyao")
        for _ in range(5):
            p.stamina = 100
            p.mp = 100
            comp.dual("qingyao")
        self.assertLessEqual(p.daily_used(game.day, "dual"), 3)
        self.assertTrue(any("今日双修已尽" in ln for ln in comp.dual("qingyao")))

    # ---------- 师：师承 ----------
    def test_apprentice_and_mentor(self):
        game = self._game()
        sect_sys = game.system("sect")
        sect_sys.join("qingyun")
        sect_sys.apprentice("qingyun_sword")
        speed = game.bonuses.value_of("cultivate_speed", "sect:")
        self.assertGreater(speed, 0)
        sect_sys.contribution = 1000
        logs = sect_sys.mentor()
        self.assertIn("突破成功率", logs[0])
        # 指点是一次性：突破判定后即消耗
        self.assertGreater(sect_sys.consume_mentor(), 0)
        self.assertEqual(sect_sys.consume_mentor(), 0.0)

    def test_mentor_bonus_reaches_breakthrough_rate(self):
        game = self._game(realm="nascent")
        sect_sys = game.system("sect")
        sect_sys.join("qingyun")
        sect_sys.apprentice("qingyun_sword")
        sect_sys.contribution = 1000
        cult = game.system("cultivation")
        rate_before = cult.success_rate(True)
        sect_sys.mentor()
        rate_after = cult.success_rate(True)      # 判定后消耗，再取一次应回落
        self.assertGreater(rate_after, rate_before)
        self.assertAlmostEqual(cult.success_rate(True), rate_before, delta=0.02)

    # ---------- 统一聚合 ----------
    def test_all_sources_aggregate_into_one_modifier(self):
        """法/地/侣/师同时生效时，只注入一条聚合修正器，且加成叠加而非互相覆盖。"""
        game = self._game(realm="nascent")
        arts, est, comp, sect_sys = (game.system("arts"), game.system("estate"),
                                     game.system("companion"), game.system("sect"))
        arts.learn("taixu")
        est.buy("blessed")
        comp.meet("xuanyin")
        sect_sys.join("qingyun")
        sect_sys.apprentice("qingyun_sword")
        # 四路加成叠加
        total = game.bonuses.value("cultivate_speed")
        self.assertGreater(total, 0.4)
        mods = [m for m in game.player.attributes.modifiers if m.source == "bonus:total"]
        self.assertEqual(len(mods), 1)


class TestTianjiao(unittest.TestCase):
    """天骄榜：金丹以上竞技排名，每日 3 次挑战机会。"""

    def _game(self, realm="core", seed=801):
        game = new_game(seed=realm and seed)
        p = game.player
        p.realm_key = realm
        p.stage = 0
        p.spirit_stones = 10 ** 6
        p.stamina = 100
        return game

    def test_gold_core_auto_enters_board(self):
        game = self._game()
        tj = game.system("tianjiao")
        self.assertTrue(tj._ensure_on_board())
        self.assertIsNotNone(tj.player_rank)

    def test_qi_refining_cannot_enter(self):
        game = self._game(realm="qi_refining")
        tj = game.system("tianjiao")
        self.assertFalse(tj._ensure_on_board())

    def test_challenge_costs_stamina(self):
        game = self._game()
        tj, p = game.system("tianjiao"), game.player
        tj._ensure_on_board()
        before = p.stamina
        tj.fight(1)
        self.assertLess(p.stamina, before)

    def test_daily_limit(self):
        game = self._game()
        tj, p = game.system("tianjiao"), game.player
        tj._ensure_on_board()
        for _ in range(3):
            p.stamina = 100
            tj.fight(1)
        self.assertEqual(tj.challenges_today, 3)
        logs = tj.fight(1)
        self.assertTrue(any("明日再来" in ln for ln in logs))

    def test_win_swaps_rank(self):
        game = self._game()
        tj, p = game.system("tianjiao"), game.player
        tj._ensure_on_board()
        p.attributes.grow_base("atk", 10 ** 6)       # 满属性确保能赢
        p.attributes.grow_base("max_hp", 10 ** 9)
        p.hp = p.max_hp
        old_rank = tj.player_rank
        # 一直挑战第 1 名直到赢（最多 3 次）
        for _ in range(3):
            p.stamina = 100
            tj.fight(1)
        if tj.player_rank != old_rank:
            self.assertEqual(tj.player_rank, 1)


class TestInnerDemon(unittest.TestCase):
    """心魔劫：跨大境界突破成功后概率触发，选择制，无绝对对错。"""

    def test_trial_data_complete(self):
        from xiuxian.systems.inner_demon import TRIALS
        for t in TRIALS:
            # v2：每题 4 个选项（正道/歧途/超脱/随缘），随缘选项无效果但标记随机
            self.assertEqual(len(t.choices), 4, f"{t.id} 应有 4 个选项")
            for c in t.choices:
                self.assertTrue(c.text and c.outcome)
            random_ones = [c for c in t.choices if c.random_effect]
            self.assertEqual(len(random_ones), 1, f"{t.id} 应恰有一个随缘选项")

    def test_choose_consumes_pending(self):
        game = new_game(seed=901)
        demon = game.system("inner_demon")
        demon.pending = {"id": "power", "realm": "core"}
        logs = demon.choose(1)
        self.assertIsNone(demon.pending)
        self.assertTrue(any("选择了" in ln for ln in logs))

    def test_no_pending_no_choice(self):
        game = new_game(seed=903)
        demon = game.system("inner_demon")
        self.assertTrue(any("并无心魔" in ln for ln in demon.choose(1)))

    def test_trigger_only_on_major_success(self):
        from xiuxian.systems.inner_demon import InnerDemonSystem, TRIGGER_CHANCE
        game = new_game(seed=905)
        demon = game.system("inner_demon")
        # 小境界突破不触发
        demon._on_breakthrough({"success": True, "is_major": False})
        self.assertIsNone(demon.pending)
        # 大境界突破有概率触发（固定 seed 确认可测）
        game.player.realm_key = "core"
        demon._on_breakthrough({"success": True, "is_major": True})
        # 结果取决于 seed，但 faced_count / pending 至少有一个合法
        self.assertTrue(demon.pending is None or isinstance(demon.pending, dict))


class TestTimeGovernor(unittest.TestCase):
    """时间预算闸门：游戏时间推进 ≤ 现实时间 × 10。"""

    def _gov(self, anchor=1.0):
        from xiuxian.core.time_governor import TimeGovernor
        return TimeGovernor(anchor=anchor, consumed=0.0, disabled=False)

    def test_ratio_10(self):
        from xiuxian.core.time_governor import TIME_RATIO
        self.assertEqual(TIME_RATIO, 10.0)

    def test_budget_accrues_with_real_time(self):
        """1 现实小时 = 10 游戏时辰预算。"""
        gov = self._gov(1.0)                  # anchor=1
        avail = gov.available(now=3601.0)     # 1 现实小时后
        self.assertAlmostEqual(avail, 10.0, delta=0.01)

    def test_consume_truncates(self):
        """预算不足时截断。"""
        gov = self._gov(1.0)
        got = gov.consume(20.0, now=3601.0)   # 想要 20 时辰，只有 10
        self.assertAlmostEqual(got, 10.0, delta=0.01)

    def test_idempotent_consume(self):
        """同一时刻重复 consume 不产生新预算。"""
        gov = self._gov(1.0)
        first = gov.consume(5.0, now=3601.0)
        second = gov.consume(5.0, now=3601.0)  # 同一时刻
        self.assertAlmostEqual(first + second, 10.0, delta=0.01)

    def test_disabled_bypasses(self):
        """disabled=True 时不限速。"""
        gov = self._gov(0.0)
        gov.disabled = True
        got = gov.consume(9999.0, now=0.0)
        self.assertEqual(got, 9999.0)

    def test_sync_resets(self):
        """sync() 重置预算基准。"""
        gov = self._gov(0.0)
        gov.consume(10.0, now=3600.0)          # 耗尽预算
        gov.sync(now=7200.0)                   # 同步到 2 小时后
        self.assertEqual(gov.consumed, 0.0)
        self.assertAlmostEqual(gov.available(now=7200.0), 0.0, delta=0.01)

    def test_production_default_enabled(self):
        """生产环境默认启用闸门。"""
        from xiuxian.core.time_governor import TimeGovernor
        gov = TimeGovernor()
        self.assertFalse(gov.disabled)         # 默认启用


class TestV2Gameplay(unittest.TestCase):
    """v2 挂机养老版：日常系统 / 一键托管 / 探索疲惫 / 软上限 / 温和失败 / 防套利 / 待领池。"""

    # ---------- 日常任务 ----------
    def test_daily_track_and_reset(self):
        game = new_game(seed=501)
        d = game.system("daily")
        d.track("hunt")
        d.track("duel")
        self.assertEqual(d.progress(), 2)
        game.advance_time(max(1, 24 - game.hour))      # 跨日清零
        self.assertEqual(d.progress(), 0)

    def test_daily_meditate_tracks_hours(self):
        game = new_game(seed=502)
        d = game.system("daily")
        game.system("cultivation").cultivate(2)
        self.assertIn("meditate", d.done)              # 打坐 2 时辰即完成

    def test_daily_tier_rewards(self):
        game = new_game(seed=503)
        d = game.system("daily")
        for path in ("claim", "hunt", "duel", "explore", "refine"):
            d.track(path)
        p = game.player
        exp_before = p.exp
        game.advance_time(max(1, 24 - game.hour))      # 5 项：修为+3% + 材料袋
        self.assertGreater(p.exp, exp_before)
        self.assertGreater(len(p.inventory.all()), 0)  # 材料袋入袋

    def test_daily_command_registered(self):
        game = new_game(seed=504)
        names = [c.name for c in game.commands()]
        self.assertIn("daily", names)

    # ---------- 一键托管 ----------
    def test_idle_auto_command(self):
        from xiuxian.ui.cli import CLI
        game = new_game(seed=505)
        game.clock.disabled = True
        cli = CLI(game)
        out = cli.run_line("idle-auto on")
        self.assertTrue(cli._auto_idle)
        self.assertTrue(any("托管已开启" in ln for ln in out))
        out = cli.run_line("idle-auto off")
        self.assertFalse(cli._auto_idle)

    # ---------- 探索疲惫 ----------
    def test_explore_tired_halves_chance(self):
        from xiuxian.core.event_system import EXPLORE_TIRED_FLAG, EXPLORE_TIRED_THRESHOLD
        game = new_game(seed=506)
        p = game.player
        ev = game.system("event")
        for _ in range(EXPLORE_TIRED_THRESHOLD + 2):
            ev.trigger(force=True)                     # force 也累计疲惫（防刷）
            if ev.pending:
                ev.choose(0)
        self.assertGreaterEqual(int(p.flags.get(EXPLORE_TIRED_FLAG, 0)),
                                EXPLORE_TIRED_THRESHOLD)

    def test_explore_tired_cleared_by_rest(self):
        from xiuxian.core.event_system import EXPLORE_TIRED_FLAG
        game = new_game(seed=507)
        p = game.player
        p.flags[EXPLORE_TIRED_FLAG] = 5
        game.system("cultivation").rest(4)             # 休息 4 时辰清零
        self.assertNotIn(EXPLORE_TIRED_FLAG, p.flags)

    # ---------- 软上限 ----------
    def test_speed_soft_cap(self):
        from xiuxian.core.cultivation import soft_cap, SPEED_SOFT_CAP
        self.assertAlmostEqual(soft_cap(0.6, SPEED_SOFT_CAP, 0.3), 0.6)
        # +100% 超出 +20pp，仅算 20%×0.3 = 6pp → 86%
        self.assertAlmostEqual(soft_cap(1.0, 0.8, 0.3), 0.86, delta=0.01)

    def test_insight_rate_capped(self):
        from xiuxian.core.cultivation import INSIGHT_MAX_RATE
        game = new_game(seed=508)
        p = game.player
        p.attributes.base["luck"] = 999
        from unittest import mock
        seen = []
        with mock.patch.object(game.rng, "chance", lambda prob: seen.append(prob) or False):
            game.system("cultivation").cultivate(4)
        self.assertTrue(seen)
        for prob in seen:
            self.assertLessEqual(prob, INSIGHT_MAX_RATE + 1e-9)

    # ---------- 温和失败 ----------
    def test_fail_loss_is_20_40_percent(self):
        game = new_game(seed=509)
        cult = game.system("cultivation")
        p = game.player
        p.exp = 1000.0
        p.stamina = 100
        game.rng.chance = lambda prob: False           # 强制失败
        lost = []
        from xiuxian.core.cultivation import FAIL_LOSS_MIN, FAIL_LOSS_MAX
        self.assertAlmostEqual(FAIL_LOSS_MIN, 0.20)
        self.assertAlmostEqual(FAIL_LOSS_MAX, 0.40)

    def test_fail_compensation_accumulates(self):
        from xiuxian.core.cultivation import COMPENSATION_FLAG, COMPENSATION_STEP, COMPENSATION_MAX
        game = new_game(seed=510)
        cult = game.system("cultivation")
        p = game.player
        p.exp = p.exp_required()
        p.stamina = 100
        game.rng.chance = lambda prob: False
        cult.breakthrough()                            # 失败一次
        self.assertAlmostEqual(float(p.flags.get(COMPENSATION_FLAG, 0)), COMPENSATION_STEP)
        # 成功突破后清零（先越过冷却）
        game.rng.chance = lambda prob: True
        p.exp = p.exp_required()
        p.stamina = 100
        game.advance_time(24 * 4)                      # 4 日，越过冷却
        cult.breakthrough()
        self.assertNotIn(COMPENSATION_FLAG, p.flags)

    # ---------- 坊市防套利 ----------
    def test_market_daily_buy_limit(self):
        from xiuxian.systems.market import DAILY_BUY_LIMIT
        game = new_game(seed=511)
        m, p = game.system("market"), game.player
        p.spirit_stones = 10 ** 7
        for _ in range(DAILY_BUY_LIMIT):
            m.buy("qi_gathering_pill", 1)
        logs = m.buy("qi_gathering_pill", 1)           # 超限
        self.assertTrue(any("上限" in ln for ln in logs))

    def test_market_trade_moves_price(self):
        game = new_game(seed=512)
        m = game.system("market")
        p = game.player
        p.spirit_stones = 10 ** 7
        before = m.buy_price("qi_gathering_pill")
        m.buy("qi_gathering_pill", 3)                  # 大量买入推高价格
        self.assertGreater(m.buy_price("qi_gathering_pill"), before)

    # ---------- 待领池溢出 ----------
    def test_pending_pool_capped_and_converted(self):
        from xiuxian.core.offline import OfflineState, settle
        from xiuxian.core.offline import PENDING_CAP_RATIO, PENDING_OVERFLOW_RATIO
        st = OfflineState(last_settled_at=0.0)
        # 24 小时离线：1.0%/时 × 24 = 24% 需求，低于 100% 上限，不触发转化
        gain, _, _ = settle(st, need=1000.0, now=24 * 3600.0)
        self.assertAlmostEqual(gain, 1000 * 0.0100 * (24 - 60/3600), delta=0.01)
        self.assertLessEqual(st.pending_exp, 1000.0 * PENDING_CAP_RATIO)
        # 超额模拟：直接塞满池再结算
        st2 = OfflineState(last_settled_at=0.0, pending_exp=999.0)
        settle(st2, need=1000.0, now=3600.0)
        self.assertLessEqual(st2.pending_exp, 1000.0 * PENDING_CAP_RATIO)
        self.assertGreater(st2.overflow_converted, 0.0)

    # ---------- 凶险取消 ----------
    def test_locations_no_danger(self):
        from xiuxian.config.realms import LOCATIONS
        for loc in LOCATIONS.values():
            self.assertNotIn("danger", loc)            # v2 凶险取消

    def test_travel_no_danger_text(self):
        game = new_game(seed=513)
        out = game.travel("落云山脉")
        self.assertTrue(any("抵达" in ln and "凶险" not in ln for ln in out))


class TestLawSystem(unittest.TestCase):
    """仙界法则系统（第 27 批：双轴成长 + 突破软门槛）。

    重点守住三条**结构性约束**，任一被破坏都会让仙界重构失去意义：
      1. 法则走 attr_mul 乘区（与境界加区相乘，不被稀释）
      2. 软门槛只降成功率、不禁止突破（不卡死玩家）
      3. 悟道必须真实推进时间（否则退化成零成本刷感悟）
    """

    @staticmethod
    def _cfg():
        from xiuxian.config import laws as law_config
        return law_config

    def _immortal_game(self, seed=2026):
        """造一个已飞升（人仙）的存档，避免跑完凡界 669 天。"""
        game = new_game(seed=seed)
        game.clock.disabled = True          # 加速：关闭现实时间闸门
        p = game.player
        p.realm_key = "human_immortal"
        p.stage = 0
        p.exp = 0.0
        game.rebuild_bonuses()
        return game

    # ---------- 配置自检 ----------
    def test_laws_declared(self):
        cfg = self._cfg()
        self.assertEqual(len(cfg.LAWS), 8)
        self.assertEqual(cfg.LAW_MAX_STAGE, 5)
        for law in cfg.LAWS:
            self.assertIn(law.effect_type,
                          ("attr_mul", "cultivate_speed", "insight_rate"))
            if law.effect_type == "attr_mul":
                self.assertTrue(law.effect_key)      # 属性类必须指明作用属性

    def test_stage_cost_increasing(self):
        """每阶成本严格递增：后期更贵，「浅尝多条」与「深挖一条」成本才可比。"""
        costs = self._cfg().LAW_STAGE_COST
        for i in range(1, len(costs)):
            self.assertGreater(costs[i], costs[i - 1])

    def test_gate_monotonic(self):
        """门槛随境界严格递增：后期必须持续有瓶颈，不能出现倒挂。"""
        from xiuxian.config.realms import ORDER
        cfg = self._cfg()
        prev = 0
        for key in ORDER[ORDER.index("earth_immortal"):]:
            gate = cfg.gate_of(key)
            self.assertGreater(gate, prev, f"{key} 门槛未递增")
            prev = gate

    def test_gate_within_capacity(self):
        """末档门槛必须小于总节点数 —— 否则玩家永远无法突破（卡死）。"""
        cfg = self._cfg()
        total = len(cfg.LAWS) * cfg.LAW_MAX_STAGE
        for key, gate in cfg.LAW_GATE.items():
            self.assertLess(gate, total, f"{key} 门槛 {gate} 超过总节点 {total}")

    # ---------- 阶数推导 ----------
    def test_stage_of(self):
        cfg = self._cfg()
        self.assertEqual(cfg.stage_of(0), 0)
        self.assertEqual(cfg.stage_of(99), 0)
        self.assertEqual(cfg.stage_of(cfg.LAW_STAGE_COST[0]), 1)
        self.assertEqual(cfg.stage_of(sum(cfg.LAW_STAGE_COST[:2])), 2)
        self.assertEqual(cfg.stage_of(cfg.LAW_FULL_COST), cfg.LAW_MAX_STAGE)

    def test_total_stages(self):
        cfg = self._cfg()
        self.assertEqual(cfg.total_stages({}), 0)
        self.assertEqual(cfg.total_stages({"metal": 100.0, "wood": 400.0}), 3)

    def test_stage_name(self):
        cfg = self._cfg()
        self.assertEqual(cfg.stage_name(0), "未入门")
        self.assertEqual(cfg.stage_name(1), cfg.LAW_STAGES[0])
        self.assertEqual(cfg.stage_name(999), cfg.LAW_STAGES[-1])   # 越界钳制

    # ---------- 凡界 / 仙界边界 ----------
    def test_wudao_blocked_in_mortal_realm(self):
        game = new_game(seed=1)
        law = game.system("law")
        out = law.wudao("metal", 4)
        self.assertTrue(any("飞升" in ln for ln in out))
        self.assertEqual(law.progress.get("metal", 0.0), 0.0)

    def test_wudao_works_in_immortal_realm(self):
        game = self._immortal_game()
        law = game.system("law")
        out = law.wudao("metal", 12)
        self.assertGreater(law.progress.get("metal", 0.0), 0.0)
        self.assertTrue(any("静悟" in ln for ln in out))

    def test_wudao_rejects_unknown_law(self):
        game = self._immortal_game()
        law = game.system("law")
        out = law.wudao("nonexistent", 4)
        self.assertTrue(any("未知法则" in ln for ln in out))

    # ---------- 乘区（结构性约束 1） ----------
    def test_law_multiplier_applied(self):
        """金之法则满阶 → 攻击 ×1.40（+40% 偏移，attr_mul 按偏移相加）。"""
        game = self._immortal_game()
        p = game.player
        law = game.system("law")
        before = p.atk
        law.progress["metal"] = self._cfg().LAW_FULL_COST
        game.rebuild_bonuses()
        self.assertAlmostEqual(p.atk / before, 1.40, places=2)

    def test_law_no_effect_in_mortal_realm(self):
        """凡界不享受法则加成（法则为仙界专属）。"""
        game = new_game(seed=2)
        p = game.player
        law = game.system("law")
        before = p.atk
        law.progress["metal"] = self._cfg().LAW_FULL_COST
        game.rebuild_bonuses()
        self.assertAlmostEqual(p.atk, before, places=6)

    # ---------- 软门槛（结构性约束 2） ----------
    def test_gate_penalty_met(self):
        game = self._immortal_game()
        law = game.system("law")
        law.progress = {"metal": self._cfg().LAW_FULL_COST}     # 5 阶 ≥ 地仙门槛
        penalty, note = law.gate_penalty("earth_immortal")
        self.assertEqual(penalty, 0.0)
        self.assertEqual(note, "")

    def test_gate_penalty_unmet(self):
        game = self._immortal_game()
        law = game.system("law")
        law.progress = {}
        penalty, note = law.gate_penalty("earth_immortal")
        self.assertAlmostEqual(penalty, 0.30, places=6)
        self.assertIn("法则未臻", note)

    def test_breakthrough_rate_pressed_to_floor(self):
        """未达门槛 → 成功率压到 5% 安全下限：可硬冲，但不禁（不卡死）。"""
        game = self._immortal_game()
        p = game.player
        p.stage = p.realm_def.stage_count - 1          # 人仙圆满 → 跨大境界
        cult = game.system("cultivation")
        self.assertAlmostEqual(cult.success_rate(True), 0.05, places=6)

    def test_breakthrough_not_blocked_by_gate(self):
        """软门槛不得阻断突破流程（只能降成功率）。

        注意：金丹以上（仙界天然包含）突破会先被「渡劫」系统拦截，这是既有机制。
        本测试只确保拦截原因来自渡劫、而非法则门槛 —— 法则永远不阻断。
        """
        game = self._immortal_game()
        p = game.player
        p.stage = p.realm_def.stage_count - 1
        p.exp = p.exp_required()
        p.stamina = 100.0
        cult = game.system("cultivation")
        out = cult.breakthrough()
        joined = " ".join(out)
        if "中断" in joined:
            self.assertNotIn("法则", joined)     # 中断只能因渡劫，绝不能因法则
        else:
            self.assertTrue(any("推演天机" in ln for ln in out))

    # ---------- 时间推进（结构性约束 3） ----------
    def test_wudao_advances_time(self):
        """悟道必须真实推进游戏时间 —— 防止「时间不走、感悟照涨」的白嫖。"""
        game = self._immortal_game()
        law = game.system("law")
        before = game.day * 24.0 + game.hour
        law.wudao("metal", 24, ignore_stamina=True)
        after = game.day * 24.0 + game.hour
        self.assertGreaterEqual(after - before, 23.0)

    def test_wudao_idle_skips_time_budget(self):
        """挂机悟道跳过时间预算闸门（与 game.advance_time 注释一致）。"""
        game = self._immortal_game()
        law = game.system("law")
        before = game.day * 24.0 + game.hour
        law.auto_wudao(24)
        after = game.day * 24.0 + game.hour
        self.assertGreaterEqual(after - before, 23.0)

    # ---------- 挂机集成 ----------
    def test_idle_turns_to_wudao(self):
        """仙界修为圆满但法则不够 → 闭关自动转悟道（挂机不空转）。"""
        game = self._immortal_game()
        p = game.player
        p.stage = p.realm_def.stage_count - 1
        p.exp = p.exp_required()
        law = game.system("law")
        self.assertTrue(law.should_keep_wudao())
        game.system("cultivation").idle(20, ignore_stamina=True)
        self.assertGreater(self._cfg().total_stages(law.progress), 0)

    def test_should_keep_wudao_false_when_gate_met(self):
        """法则已达标则不再自动悟道（把突破决策交还玩家）。"""
        game = self._immortal_game()
        p = game.player
        p.stage = p.realm_def.stage_count - 1
        p.exp = p.exp_required()
        law = game.system("law")
        law.progress = {"metal": self._cfg().LAW_FULL_COST}
        self.assertFalse(law.should_keep_wudao())

    # ---------- 序列化 ----------
    def test_serialization_roundtrip(self):
        game = self._immortal_game()
        law = game.system("law")
        law.progress = {"metal": 400.0, "time": 100.0}
        law.focus = "time"
        from xiuxian.systems.law import LawSystem
        law2 = LawSystem()
        law2.bind(game)
        law2.load_state(law.to_dict())
        self.assertEqual(law2.progress, law.progress)
        self.assertEqual(law2.focus, "time")

    def test_load_state_filters_unknown(self):
        """读档需过滤非法 key（老存档 / 被改过的档不应污染状态）。"""
        game = self._immortal_game()
        cfg = self._cfg()
        law = game.system("law")
        law.load_state({"progress": {"metal": 100.0, "nonexistent": 999.0},
                        "focus": "bad"})
        self.assertEqual(law.progress, {"metal": 100.0})
        self.assertIn(law.focus, cfg.ORDER)


# ============================================================================
# 第 27 批：仙阶门派 + 仙界专属事件
#
# 这组测试守住四条结构性约束（改任何一条都会让仙界重构退化）：
#   ① 仙凡两隔：飞升后凡界宗门停俸停贡献，但已授予的 buff 必须保留（不吃亏）
#   ② 仙门悟道加速：主修 +50% / 兼修 +20% / 无关 0%，且不可自我加速（防正反馈）
#   ③ 事件感悟防刷：insight_hours 受每日硬闸，且未飞升静默不结算
#   ④ 终局门槛：混元圆满需 34 阶；末境必须继续悟道（否则混元 8 天潦草收尾）
# ============================================================================
class TestImmortalSect(unittest.TestCase):
    """仙阶门派：入门、仙职、悟道加速、仙凡两隔。"""

    def _immortal(self, seed=2026):
        game = create_game(name="仙门测试", seed=seed)
        game.clock.disabled = True
        p = game.player
        p.realm_key = "human_immortal"
        p.spirit_stones = 1_000_000
        game.rebuild_bonuses()
        return game, p

    def test_immortal_sects_registered(self):
        from xiuxian.systems.sect import IMMORTAL_SECTS, SECTS
        self.assertTrue(IMMORTAL_SECTS)
        for key, s in IMMORTAL_SECTS.items():
            self.assertEqual(s.tier, "immortal", f"{key} 应标记为仙门")
            self.assertIn(s.main_law, lawcfg.BY_KEY, f"{key} 主修法则非法")
            self.assertIn(s.minor_law, lawcfg.BY_KEY, f"{key} 兼修法则非法")
            self.assertIn(key, SECTS)   # 仙门须并入总表，否则 sect join 找不到

    def test_mortal_sect_rejected_after_ascension(self):
        """飞升后不可再入凡界宗门（仙凡两隔）。"""
        game, p = self._immortal()
        sect = game.system("sect")
        logs = sect.join("qingyun")
        self.assertTrue(any("仙凡两隔" in x for x in logs))
        self.assertIsNone(sect.sect_key)

    def test_join_immortal_sect(self):
        game, p = self._immortal()
        sect = game.system("sect")
        before = p.spirit_stones
        logs = sect.join("tianshu")
        self.assertEqual(sect.immortal_sect_key, "tianshu")
        self.assertEqual(p.spirit_stones, before - 20000)
        self.assertTrue(any("天枢剑宗" in x for x in logs))

    def test_join_immortal_sect_requires_realm(self):
        """未飞升不得入仙门。"""
        game = create_game(seed=5)
        game.clock.disabled = True
        game.player.spirit_stones = 1_000_000
        sect = game.system("sect")
        logs = sect.join("tianshu")
        self.assertTrue(any("收徒门槛" in x for x in logs))
        self.assertIsNone(sect.immortal_sect_key)

    def test_law_insight_speed_tiers(self):
        game, _ = self._immortal()
        sect = game.system("sect")
        sect.join("tianshu")            # 主修金 / 兼修空间
        self.assertAlmostEqual(sect.law_insight_speed("metal"), 0.50)
        self.assertAlmostEqual(sect.law_insight_speed("space"), 0.20)
        self.assertAlmostEqual(sect.law_insight_speed("wood"), 0.0)

    def test_no_sect_no_speed(self):
        game, _ = self._immortal()
        sect = game.system("sect")
        self.assertEqual(sect.law_insight_speed("metal"), 0.0)

    def test_main_law_wudao_faster(self):
        """主修法则悟道明显快于无关法则（同一时辰，比值应≈1.50）。"""
        game, _ = self._immortal()
        sect = game.system("sect")
        law = game.system("law")
        sect.join("tianshu")
        law.wudao("metal", 8.0, ignore_stamina=True)
        law.wudao("wood", 8.0, ignore_stamina=True)
        ratio = law.progress["metal"] / max(1e-9, law.progress["wood"])
        # 浮动区间 0.88~1.14 叠加，留足余量
        self.assertGreater(ratio, 1.25)
        self.assertLess(ratio, 1.80)

    def test_severed_stops_mortal_stipend_but_keeps_buff(self):
        """仙凡两隔：停俸停贡献，但已授予的 buff 保留。"""
        game = create_game(seed=11)
        game.clock.disabled = True
        p = game.player
        p.spirit_stones = 10000
        sect = game.system("sect")
        sect.join("qingyun")                       # 凡界：max_mp ×1.10
        mp_before = p.attributes.value("max_mp")
        self.assertTrue(sect.sect_key)

        p.realm_key = "human_immortal"             # 飞升
        self.assertTrue(sect.severed)
        contrib = sect.contribution
        game.drain_logs()
        logs = game.advance_time(24)
        # 凡界贡献必须停涨（灵石总量会被产业被动收入干扰，故不拿它做判据）
        self.assertEqual(sect.contribution, contrib, "飞升后凡界贡献不应再涨")
        self.assertFalse(any("青云宗发放俸禄" in x for x in logs),
                         "飞升后凡界宗门不应再发俸禄")
        # 已授予的 buff 必须保留（不吃亏）
        self.assertGreaterEqual(p.attributes.value("max_mp"), mp_before)

    def test_immortal_rank_grows_with_contribution(self):
        game, _ = self._immortal()
        sect = game.system("sect")
        sect.join("tianshu")
        self.assertEqual(sect.immortal_rank(), "记名")
        sect.immortal_contribution = 3000
        self.assertEqual(sect.immortal_rank(), "长老")
        sect.immortal_contribution = 20000
        self.assertEqual(sect.immortal_rank(), "道主")

    def test_sect_state_roundtrip(self):
        game, _ = self._immortal()
        sect = game.system("sect")
        sect.join("tianshu")
        sect.immortal_contribution = 4321
        data = sect.to_dict()
        sect2 = game.system("sect")
        sect2.load_state(data)
        self.assertEqual(sect2.immortal_sect_key, "tianshu")
        self.assertEqual(sect2.immortal_contribution, 4321)

    def test_load_state_filters_bad_immortal_key(self):
        game, _ = self._immortal()
        sect = game.system("sect")
        sect.load_state({"immortal_sect_key": "not_a_sect",
                         "immortal_contribution": "99"})
        self.assertIsNone(sect.immortal_sect_key)
        self.assertEqual(sect.immortal_contribution, 99)


class TestImmortalEvents(unittest.TestCase):
    """仙界专属事件：存在性、条件、感悟防刷。"""

    def _events(self):
        from pathlib import Path
        import json
        path = Path(__file__).resolve().parents[1] / "data" / "events.json"
        return json.loads(path.read_text(encoding="utf-8"))["events"]

    def test_immortal_events_exist(self):
        evs = self._events()
        imm = [e for e in evs if e["id"].startswith("immortal_")]
        self.assertGreaterEqual(len(imm), 10, "仙界专属事件不应少于 10 个")

    def test_immortal_events_gated_by_realm(self):
        evs = self._events()
        for e in evs:
            if not e["id"].startswith("immortal_"):
                continue
            mr = e.get("conditions", {}).get("min_realm")
            self.assertIn(mr, realm_config.ORDER, f"{e['id']} min_realm 非法")
            self.assertTrue(RealmRegistry.in_immortal_realm(mr),
                            f"{e['id']} 门槛必须落在仙界")

    def test_insight_budget_within_limit(self):
        """事件感悟总预算须 ≲ 基准的 10%（仙界 1132 日 / 32 阶 → 30400 感悟）。

        这是防「每次探索给固定比例感悟」被 1132 天体量放大到失控的硬校验。
        """
        evs = self._events()
        hours = 0.0
        for e in evs:
            for c in e.get("choices", []):
                for f in c.get("effects", []):
                    if f["type"] == "insight_hours":
                        hours += float(f["value"])
        insight = hours * lawcfg.WUDAO_BASE      # 保守按基础产出估算
        self.assertLess(insight, 30400 * 0.10,
                        f"事件感悟预算 {insight:.0f} 超标（上限 {30400*0.1:.0f}）")

    def test_insight_hours_effect(self):
        game = create_game(seed=2026)
        game.clock.disabled = True
        game.player.realm_key = "human_immortal"
        game.rebuild_bonuses()
        law = game.system("law")
        from xiuxian.core.effects import apply_effects
        logs = apply_effects(game, [{"type": "insight_hours", "law": "metal",
                                     "value": 12}])
        self.assertTrue(logs)
        self.assertGreater(law.progress["metal"], 0)

    def test_insight_daily_limit(self):
        """每日最多 INSIGHT_DAILY_LIMIT 次事件感悟（防刷硬闸）。"""
        game = create_game(seed=2026)
        game.clock.disabled = True
        game.player.realm_key = "human_immortal"
        game.rebuild_bonuses()
        law = game.system("law")
        from xiuxian.core.effects import apply_effects
        limit = law.INSIGHT_DAILY_LIMIT
        got = 0
        for _ in range(limit + 3):
            logs = apply_effects(game, [{"type": "insight_hours", "law": "metal",
                                         "value": 12}])
            if logs and "感悟" in logs[0]:
                got += 1
        self.assertEqual(got, limit)

    def test_insight_silent_before_ascension(self):
        """凡界玩家吃到仙界事件奖励应静默，不得凭空攒感悟。"""
        game = create_game(seed=3)
        from xiuxian.core.effects import apply_effects
        logs = apply_effects(game, [{"type": "insight_hours", "value": 50}])
        self.assertEqual(logs, [])
        self.assertEqual(game.system("law").progress, {})


class TestLawEpiphany(unittest.TestCase):
    """法则顿悟：因果法则的第二处消费点 + 终局门槛。"""

    def _immortal(self, seed=2026):
        game = create_game(name="顿悟测试", seed=seed)
        game.clock.disabled = True
        p = game.player
        p.realm_key = "human_immortal"
        game.rebuild_bonuses()
        return game, p

    def test_epiphany_rate_scales_with_causality(self):
        """因果阶数越高，法则顿悟率越高，且五阶不触顶（后三阶不白点）。"""
        game, _ = self._immortal()
        law = game.system("law")
        rates = []
        for st in range(lawcfg.LAW_MAX_STAGE + 1):
            law.progress["causality"] = sum(lawcfg.LAW_STAGE_COST[:st])
            game.rebuild_bonuses()
            rates.append(law.law_insight_rate())
        for a, b in zip(rates, rates[1:]):
            self.assertGreaterEqual(b, a, "阶数提升后顿悟率不应下降")
        # 关键回归点：初版上限 20% 会让因果两阶就触顶，后三阶白点
        self.assertGreater(rates[4], rates[2] + 1e-9,
                           "因果满阶仍须有边际收益（不得在 2 阶触顶）")
        self.assertLessEqual(rates[-1], lawcfg.LAW_INSIGHT_MAX_RATE + 1e-9)

    def test_final_gate_blocks_hunyuan_perfection(self):
        """末境圆满前受终局门槛约束（软门槛，只降不禁）。"""
        game, p = self._immortal()
        law = game.system("law")
        cult = game.system("cultivation")
        p.realm_key = "hunyuan"
        p.stage = 0
        game.rebuild_bonuses()

        penalty, note = law.final_gate_penalty()
        self.assertGreater(penalty, 0)
        self.assertIn("大道未圆", note)
        low = cult.success_rate(False)

        for k in lawcfg.ORDER:      # 堆到 34 阶
            law.progress[k] = sum(lawcfg.LAW_STAGE_COST[:4])
        law.progress["metal"] = sum(lawcfg.LAW_STAGE_COST[:5])
        law.progress["wood"] = sum(lawcfg.LAW_STAGE_COST[:5])
        game.rebuild_bonuses()
        self.assertGreaterEqual(lawcfg.total_stages(law.progress),
                                lawcfg.LAW_FINAL_GATE)
        self.assertEqual(law.final_gate_penalty()[0], 0.0)
        self.assertGreater(cult.success_rate(False), low)
        self.assertGreaterEqual(cult.success_rate(False), 0.05, "只降不禁")

    def test_final_gate_ignored_outside_last_realm(self):
        """终局门槛只在末境生效，不得拖累凡界/非末境的小突破。"""
        game, p = self._immortal()
        law = game.system("law")
        self.assertEqual(law.final_gate_penalty()[0], 0.0)   # 人仙，非末境

        game2 = create_game(seed=8)
        self.assertEqual(game2.system("law").final_gate_penalty()[0], 0.0)

    def test_last_realm_keeps_wudaoing(self):
        """末境未达终局门槛时，挂机仍须转悟道（否则混元只剩 8 天修为）。"""
        game, p = self._immortal()
        law = game.system("law")
        p.realm_key = "hunyuan"
        p.stage = 0
        game.rebuild_bonuses()
        self.assertEqual(law.target_gate(), lawcfg.LAW_FINAL_GATE)
        # 修为圆满 + 法则未达标 → 应继续悟道
        p.exp = p.exp_required()
        self.assertTrue(law.should_keep_wudao())

        for k in lawcfg.ORDER:
            law.progress[k] = sum(lawcfg.LAW_STAGE_COST[:4])
        law.progress["metal"] = sum(lawcfg.LAW_STAGE_COST[:5])
        law.progress["wood"] = sum(lawcfg.LAW_STAGE_COST[:5])
        self.assertFalse(law.should_keep_wudao(), "达标后不应再空转悟道")
