"""SQLite 数据层：账号表 + 存档表。零部署，单文件数据库即服务端。

演进路线：阶段0 用 SQLite 跑通「账号 + 存档上云」；并发上来换 Postgres 时，
只需替换本文件的 connect / SQL，存档 payload 结构
（version / saved_at / note / summary / data）保持不变，
因此 Game.to_dict / from_dict 这套序列化可原样复用，无需动玩法代码。

并发策略：2~3 人在线，用「每操作开连接 + 全局写锁」足够，不必引 Redis。
"""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

# 保证项目根（含 xiuxian 包）在 import 路径上
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from xiuxian.core.game import Game  # noqa: E402
from xiuxian.core.offline import claim as claim_offline  # noqa: E402
from xiuxian.core.numfmt import fmt_num  # noqa: E402
from xiuxian.factory import default_systems, settle_offline  # noqa: E402

DB_PATH = _ROOT / "data" / "xiuxian.db"
_WRITE_LOCK = threading.Lock()
SAVE_VERSION = 1


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with _WRITE_LOCK:
        conn = _connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    username   TEXT PRIMARY KEY,
                    pwhash     TEXT NOT NULL,
                    salt       TEXT NOT NULL,
                    token      TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS saves (
                    username TEXT PRIMARY KEY,
                    data     TEXT NOT NULL,
                    saved_at TEXT NOT NULL
                );
                """
            )
            conn.commit()
        finally:
            conn.close()


# ---------- 账号 ----------
def create_account(username: str, pwhash: str, salt: str, token: str) -> None:
    with _WRITE_LOCK:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO accounts(username, pwhash, salt, token, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (username, pwhash, salt, token, _now()),
            )
            conn.commit()
        finally:
            conn.close()


def get_account(username: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT username, pwhash, salt, token FROM accounts WHERE username=?",
            (username,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def set_token(username: str, token: str) -> None:
    with _WRITE_LOCK:
        conn = _connect()
        try:
            conn.execute(
                "UPDATE accounts SET token=? WHERE username=?", (token, username)
            )
            conn.commit()
        finally:
            conn.close()


def get_username_by_token(token: str) -> str | None:
    if not token:
        return None
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT username FROM accounts WHERE token=?", (token,)
        ).fetchone()
    finally:
        conn.close()
    return row["username"] if row else None


# ---------- 存档 ----------
def _build_payload(game: Game, note: str) -> dict[str, Any]:
    return {
        "version": SAVE_VERSION,
        "saved_at": _now(),
        "note": note,
        "summary": {
            "name": game.player.name,
            "realm": game.player.realm_name,
            "day": game.day,
            "location": game.location,
        },
        "data": game.to_dict(),
    }


def save_game(game: Game, username: str, note: str = "") -> None:
    """把当前游戏状态写入 SQLite（覆盖该用户名下的唯一存档）。即「上云」。"""
    payload = _build_payload(game, note)
    blob = json.dumps(payload, ensure_ascii=False)
    with _WRITE_LOCK:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO saves(username, data, saved_at) VALUES(?, ?, ?) "
                "ON CONFLICT(username) DO UPDATE SET "
                "data=excluded.data, saved_at=excluded.saved_at",
                (username, blob, payload["saved_at"]),
            )
            conn.commit()
        finally:
            conn.close()


def load_payload(username: str) -> dict[str, Any] | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT data FROM saves WHERE username=?", (username,)
        ).fetchone()
    finally:
        conn.close()
    return json.loads(row["data"]) if row else None


def load_game(username: str) -> Game:
    """读档：复用 Game.from_dict + 离线结算 + claim，并写回（幂等）。

    离线收益结算口径与 factory.load_game 一致；结算后立刻落库，
    保证「重新登录 = 领取离线挂机修为」且不会重复结算。
    """
    payload = load_payload(username)
    if payload is None:
        raise FileNotFoundError(f"无存档：{username}")
    game = Game.from_dict(payload["data"], default_systems)
    logs = settle_offline(game, payload.get("saved_at", ""))
    if logs:
        daily = game.systems.get("daily")
        if daily is not None:
            daily.track("claim")
        got = claim_offline(game.offline, game.player.add_exp)
        if got > 0:
            logs.append(f"　修为 +{fmt_num(got)}")
        elif game.offline.pending_exp > 0:
            logs.append(
                f"　本层修为已满，{fmt_num(game.offline.pending_exp)} 修为留存待领"
                f"（breakthrough 突破后 claim）"
            )
        game.emit_logs(logs)
        save_game(game, username, note="offline")
    return game


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
