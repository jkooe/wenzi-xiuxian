"""世界状态（阶段1a/1b 同服可见 + 实时通道）：在线列表、战力榜、世界聊天。

进程内实现，不引 Redis（仅 2~3 人在线，避免过度设计）。

设计口径（与方案文档一致）：
- **战力榜**：在线玩家从会话读实时属性（权威），离线玩家从 SQLite 存档读
  序列化属性——两者复用同一套 `combat_power` 口径（web_views），离线不掉榜。
- **在线判定（阶段1b）**：以 WebSocket 活连接为准（WS_CONNECTIONS），
  上线/下线实时广播，替代阶段 1a 的「有会话即在线」近似口径与 4s 轮询。
- **世界聊天**：/ws/chat 连接后发言广播给全部活连接。
"""

from __future__ import annotations

from typing import Any

import db
import web_views

# username -> MpSession 会话引用（由 app.py 注册/注销）
WORLD: dict[str, Any] = {}

# 阶段1b：username -> 活 WebSocket 连接集合（同一账号可多端多连接）
WS_CONNECTIONS: dict[str, set] = {}


def register(username: str, session) -> None:
    WORLD[username] = session


def unregister(username: str) -> None:
    WORLD.pop(username, None)


# ---------- 阶段1b：WebSocket 实时通道 ----------
def is_online(username: str) -> bool:
    """实时在线判定：有活 WS 连接即在线。"""
    return username in WS_CONNECTIONS


def ws_connect(username: str, ws) -> bool:
    """注册 WS 连接。返回 True 表示该账号首个活连接（触发上线广播）。"""
    conns = WS_CONNECTIONS.setdefault(username, set())
    first = not conns
    conns.add(ws)
    return first


def ws_disconnect(username: str, ws) -> bool:
    """注销 WS 连接。返回 True 表示该账号最后一个连接断开（触发下线广播）。"""
    conns = WS_CONNECTIONS.get(username)
    if not conns:
        return False
    conns.discard(ws)
    if conns:
        return False
    WS_CONNECTIONS.pop(username, None)
    return True


async def broadcast(msg: dict, skip_username: str | None = None) -> None:
    """广播消息给所有活连接（发送失败的连接顺手清掉）。"""
    for username, conns in list(WS_CONNECTIONS.items()):
        if username == skip_username:
            continue
        for ws in list(conns):
            try:
                await ws.send_json(msg)
            except Exception:
                conns.discard(ws)


def online_list() -> list[dict]:
    """在线列表：道号 + 境界 + 战力，按战力倒序（以 WS 活连接为准）。"""
    out = []
    for username in WS_CONNECTIONS:
        sess = WORLD.get(username)
        if sess is None:
            continue
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

    # 1) 在线（阶段1b 以 WS 活连接为准）：会话实时属性（权威）
    for username in WS_CONNECTIONS:
        sess = WORLD.get(username)
        if sess is None:
            continue
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
