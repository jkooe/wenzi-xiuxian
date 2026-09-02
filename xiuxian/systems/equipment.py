"""装备系统（简版：纯属性，六个部位）。

属性注入完全走 Modifier：
    穿戴 -> add_modifier(Modifier(source="equip:兵器", add=..., mul=...))
    卸下 -> remove_modifier("equip:兵器")
因此战斗、修炼、渡劫读到的属性自动生效，不需要任何适配代码。

source 统一带 "equip:" 前缀，读档后可按前缀整体重建，避免存档里残留的旧修正器与
实际穿戴不一致。
"""

from __future__ import annotations

from ..config import items as item_config
from ..config.realms import RealmRegistry
from ..core.attributes import Modifier
from ..core.base_system import Command, GameSystem

SOURCE_PREFIX = "equip:"


class EquipmentSystem(GameSystem):
    id = "equipment"
    name = "装备"

    # ---------- 属性重建 ----------
    def rebuild(self) -> None:
        """按 player.equipment 重建全部装备修正器（幂等，读档后调用）。"""
        p = self.player
        p.attributes.remove_modifiers_with_prefix(SOURCE_PREFIX)
        for slot, item_id in p.equipment.items():
            self._inject(item_id, slot)
        self._clamp()

    def _inject(self, item_id: str, slot: str) -> None:
        item = item_config.get_item(item_id)
        self.player.attributes.add_modifier(
            Modifier(
                source=f"{SOURCE_PREFIX}{slot}",
                add=dict(item.equip_add),
                mul=dict(item.equip_mul),
            )
        )

    def _clamp(self) -> None:
        """装备改变上限后，当前气血灵力不得超过新上限。"""
        p = self.player
        p.hp = min(p.hp, p.max_hp)
        p.mp = min(p.mp, p.max_mp)

    # ---------- 穿戴 / 卸下 ----------
    def equip(self, item_id: str) -> list[str]:
        p = self.player
        if not p.alive:
            return ["你已身死道消。"]

        item = item_config.get_item(item_id)
        if item.kind != "equip" or not item.equip_slot:
            return [f"「{item.name}」不是可穿戴之物。"]
        if not p.inventory.has(item_id):
            return [f"你身上没有「{item.name}」。"]
        if item.min_realm and not RealmRegistry.within(p.realm_key, min_realm=item.min_realm):
            need = RealmRegistry.get(item.min_realm).name
            return [f"修为不足，「{item.name}」需 {need} 以上方可驱使。"]

        slot = item.equip_slot
        before = self._snapshot()
        if p.equipment.get(slot):
            self._unequip_to_bag(slot)          # 同部位先卸下，旧装备回背包

        p.inventory.remove(item_id, 1)
        p.equipment[slot] = item_id
        self._inject(item_id, slot)
        self._clamp()

        logs = [f"穿戴【{item.name}】（{item_config.EQUIP_SLOTS[slot]}）"]
        logs.extend(self._diff(before))
        return logs

    def unequip(self, slot_name: str) -> list[str]:
        p = self.player
        slot = self._normalize_slot(slot_name)
        if slot is None:
            return [f"无此部位。可选：{'、'.join(item_config.EQUIP_SLOTS.values())}"]
        if not p.equipment.get(slot):
            return [f"{item_config.EQUIP_SLOTS[slot]}之位空空如也。"]

        before = self._snapshot()
        name = self._unequip_to_bag(slot)
        self._clamp()
        logs = [f"卸下【{name}】，收入储物袋。"]
        logs.extend(self._diff(before))
        return logs

    def _unequip_to_bag(self, slot: str) -> str:
        p = self.player
        item_id = p.equipment.pop(slot)
        p.attributes.remove_modifier(f"{SOURCE_PREFIX}{slot}")
        p.inventory.add(item_id, 1)
        return item_config.get_item(item_id).name

    @staticmethod
    def _normalize_slot(name: str) -> str | None:
        slots = item_config.EQUIP_SLOTS
        if name in slots:
            return name
        for key, label in slots.items():
            if label == name:
                return key
        return None

    # ---------- 展示 ----------
    def _snapshot(self) -> dict[str, float]:
        p = self.player
        return {"max_hp": p.max_hp, "max_mp": p.max_mp, "atk": p.atk,
                "def": p.defense, "speed": p.speed}

    def _diff(self, before: dict[str, float]) -> list[str]:
        from ..core.attributes import ATTR_LABELS
        after = self._snapshot()
        parts = []
        for key, old in before.items():
            delta = after[key] - old
            if abs(delta) >= 0.5:
                parts.append(f"{ATTR_LABELS[key]} {delta:+.0f}")
        return [f"属性变动：{'　'.join(parts)}"] if parts else []

    def info(self) -> list[str]:
        p = self.player
        if not p.equipment:
            return ["你身无长物，仅着一袭布衣。"]
        lines = ["已穿戴："]
        for slot, label in item_config.EQUIP_SLOTS.items():
            item_id = p.equipment.get(slot)
            if not item_id:
                lines.append(f"  {label}：——")
                continue
            item = item_config.get_item(item_id)
            bonus = self._describe(item)
            lines.append(f"  {label}：{item.name}　{bonus}")
        return lines

    @staticmethod
    def _describe(item) -> str:
        from ..core.attributes import ATTR_LABELS
        parts = []
        for key, val in item.equip_add.items():
            parts.append(f"{ATTR_LABELS.get(key, key)} {val:+g}")
        for key, val in item.equip_mul.items():
            parts.append(f"{ATTR_LABELS.get(key, key)} {(val - 1) * 100:+.0f}%")
        return "、".join(parts) if parts else "无加成"

    def equippable_in_bag(self) -> list[tuple[str, int]]:
        """背包里可穿戴的装备（供 equip 无参数时展示）。"""
        p = self.player
        out = []
        for item_id, count in p.inventory.all():
            item = item_config.get_item(item_id)
            if item.kind == "equip" and item.equip_slot:
                out.append((item_id, count))
        return out

    # ---------- 命令 ----------
    def commands(self) -> list[Command]:
        def _equip(args: list[str]) -> None:
            if not args:
                self.game.emit_logs(self.info())
                bag = self.equippable_in_bag()
                if bag:
                    from ..config import items as ic
                    detail = "、".join(f"{ic.get_item(i).name}×{n}（{i}）" for i, n in bag)
                    self.log(f"  可穿戴：{detail}")
                else:
                    self.log("  储物袋中没有可穿戴之物。")
                return
            self.game.emit_logs(self.equip(args[0]))

        def _unequip(args: list[str]) -> None:
            if not args:
                self.game.emit_logs(self.info())
                return
            self.game.emit_logs(self.unequip(args[0]))

        return [
            Command("equip", "穿戴装备（无参查看）", "equip [物品id]", _equip),
            Command("unequip", "卸下装备", "unequip <部位>", _unequip),
        ]

    # ---------- 持久化 ----------
    def load_state(self, data: dict) -> None:
        self.rebuild()      # equipment 存在角色上，这里只需重建属性修正器
