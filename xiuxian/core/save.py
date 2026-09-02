"""存档与读档。

存档内容 = 角色 + 世界 + 随机源 + 各玩法系统的私有状态。
版本号 + 迁移钩子：后续加字段时老档仍可读。
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .game import SAVE_DIR, Game

SAVE_VERSION = 1

# 迁移表：[(from_version, to_version, migrate_fn)]
MIGRATIONS: list[tuple[int, int, Callable[[dict[str, Any]], dict[str, Any]]]] = []


class SaveError(Exception):
    pass


class SaveManager:
    def __init__(self, save_dir: Path | str = SAVE_DIR) -> None:
        self.save_dir = Path(save_dir)
        self.save_dir.mkdir(parents=True, exist_ok=True)

    def path(self, slot: int = 1) -> Path:
        return self.save_dir / f"save_{slot}.json"

    # ---------- 写 ----------
    def save(self, game: Game, slot: int = 1, note: str = "") -> Path:
        payload = {
            "version": SAVE_VERSION,
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "note": note,
            "summary": {
                "name": game.player.name,
                "realm": game.player.realm_name,
                "day": game.day,
                "location": game.location,
            },
            "data": game.to_dict(),
        }
        path = self.path(slot)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)          # 原子替换，避免写一半断电毁档
        return path

    # ---------- 读 ----------
    def load(self, slot: int = 1) -> dict[str, Any]:
        path = self.path(slot)
        if not path.exists():
            raise SaveError(f"存档不存在：{path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SaveError(f"存档损坏：{path}（{exc}）") from exc
        return self.migrate(payload)

    def migrate(self, payload: dict[str, Any]) -> dict[str, Any]:
        version = int(payload.get("version", 0))
        if version > SAVE_VERSION:
            raise SaveError(f"存档版本 {version} 高于当前程序 {SAVE_VERSION}，请升级后读取")
        while version < SAVE_VERSION:
            nxt = None
            for from_v, to_v, fn in MIGRATIONS:
                if from_v == version:
                    nxt = (to_v, fn)
                    break
            if nxt is None:
                raise SaveError(f"缺少 v{version} 的迁移规则")
            to_v, fn = nxt
            payload = fn(payload)
            payload["version"] = to_v
            version = to_v
        return payload

    # ---------- 管理 ----------
    def list_slots(self, max_slot: int = 9) -> list[dict[str, Any]]:
        out = []
        for slot in range(1, max_slot + 1):
            path = self.path(slot)
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                out.append({"slot": slot, "corrupted": True})
                continue
            out.append({
                "slot": slot,
                "saved_at": payload.get("saved_at", "?"),
                "note": payload.get("note", ""),
                "summary": payload.get("summary", {}),
                "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime)),
            })
        return out

    def delete(self, slot: int) -> bool:
        path = self.path(slot)
        if path.exists():
            path.unlink()
            return True
        return False
