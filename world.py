"""世界状态（阶段1a 同服可见）：在线列表 + 战力榜。进程内实现，不引 Redis。

设计口径（与方案文档一致）：
- **在线列表**：WORLD 持有在服会话引用（app.py 登录建会话时注册、离山时注销），
  /api/online 读内存返回。
- **战力榜**：在线玩家从会话读实时属性（权威），离线玩家从 SQLite 存档读
  序列化属性——两者复用同一套 `combat_power` 口径（web_views），离线不掉榜。

在线判定（阶段1a 近似口径）：有在服会话即算在线。仅关浏览器不登山的会话会
滞留，实时上下线感知留给阶段 1b（WebSocket）。
"""

from __future__ import annotations

from typing import Any

import db
import web_views

# username -> MpSession 会话引用（由 app.py 注册/注销）
WORLD: dict[str, Any] = {}


def register(username: str, session) -> None:
    WORLD[username] = session


def unregister(username: str) -> None:
    WORLD.pop(username, None)


def online_list() -> list[dict]:
    """在线列表：道号 + 境界 + 战力，按战力倒序。"""
    out = []
    for username, sess in WORLD.items():
        p = sess.game.player
        out.append({
            "username": username,
            "realm": p.realm_name,
            "power": int(web_views.combat_power(p)),
            "online": True,
        })
    out.sort(key=lambda x: -x["power"])
    return out


def rank_board(top_n: int = 20) -> list[dict]:
    """战力榜：覆盖在线 + 离线玩家，按综合战力倒序取前 top_n。

    在线会话的属性比存档新（操作即落库有 15s 节流），故在线条目以会话为准，
    存档仅补离线玩家，避免重复计数。
    """
    from xiuxian.core.attributes import AttributeSet
    from xiuxian.config.realms import BY_KEY, RealmRegistry

    entries: dict[str, dict] = {}

    # 1) 在线：会话实时属性（权威）
    for username, sess in WORLD.items():
        p = sess.game.player
        entries[username] = {
            "username": username,
            "realm": p.realm_name,
            "power": int(web_views.combat_power(p)),
            "online": True,
        }

    # 2) 离线：从存档反序列化属性计算战力
    for username, payload in db.list_all_saves():
        if username in entries:
            continue
        try:
            pdata = payload["data"]["player"]
            attrs = AttributeSet.from_dict(pdata["attributes"])
            realm_key = pdata.get("realm_key", "qi_refining")
            stage = int(pdata.get("stage", 0))
            realm_name = (RealmRegistry.full_name(realm_key, stage)
                          if realm_key in BY_KEY else realm_key)
            entries[username] = {
                "username": username,
                "realm": realm_name,
                "power": int(web_views.combat_power_from_attrs(attrs)),
                "online": False,
            }
        except Exception:
            continue        # 坏档/缺字段跳过，不拖累榜单

    return sorted(entries.values(), key=lambda x: -x["power"])[:top_n]
