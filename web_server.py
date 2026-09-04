"""Web 版入口：手机浏览器即可试玩（安卓/iOS/电脑同网访问）。

    python web_server.py [端口]        # 默认 8000，监听 0.0.0.0
    ./run.sh web                       # 或走一键启动

手机连同一 WiFi 后，浏览器打开 http://<本机IP>:8000 即可游玩。
挂机友好：页面每 30 秒自动刷新状态，命令间隙的现实时钟结算照常生效
（复用 CLI 的 _settle_online，在线倍率 1.5 不变）。

注意：仅供局域网/本机测试，无鉴权，请勿暴露到公网。
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from xiuxian.factory import create_game  # noqa: E402
from xiuxian.ui import panel  # noqa: E402
from xiuxian.ui.cli import CLI  # noqa: E402
from xiuxian.config import arts as art_config  # noqa: E402
from xiuxian.config import dungeons as dungeon_config  # noqa: E402
from xiuxian.config import items as item_config  # noqa: E402
from xiuxian.config.realms import RealmRegistry  # noqa: E402
from web_views import player_data, catalog_data, actions_data  # noqa: E402


def _load_page() -> str:
    """从 xiuxian/ui/web/ 加载页面骨架 + 设计令牌 + 组件样式 + 组件逻辑。

    组件化后页面资源独立成文件（tokens.css / app.css / app.js / page.html），
    改 UI 无需动服务端逻辑；启动时读入内嵌，保持单文件零依赖、无外部请求。
    """
    web_dir = Path(__file__).resolve().parent / "xiuxian" / "ui" / "web"
    html = (web_dir / "page.html").read_text(encoding="utf-8")
    tokens = (web_dir / "tokens.css").read_text(encoding="utf-8")
    app_css = (web_dir / "app.css").read_text(encoding="utf-8")
    app_js = (web_dir / "app.js").read_text(encoding="utf-8")
    return (html.replace("/*TOKENS_CSS*/", tokens)
                .replace("/*APP_CSS*/", app_css)
                .replace("/*APP_JS*/", app_js))


PAGE = _load_page()








class GameSession:
    """服务端唯一的游戏会话：复用 CLI 分发逻辑 + 现实时钟结算。"""

    def __init__(self) -> None:
        self.game = create_game(name="修士", seed=None)
        self.cli = CLI(self.game)

    def command(self, line: str) -> list[str]:
        return self.cli.run_line(line)

    def status(self) -> str:
        self.cli._settle_online()                # 页面刷新也结算挂机收益
        return panel.status_panel(self.game)

    def actions(self) -> list[dict]:
        return actions_data(self.game)


# P2-7: 按会话隔离。同一台电脑上的多个设备/标签页各玩各的角色，
# 不再因为全局单实例而互相覆盖状态。
SESSIONS: dict[str, GameSession] = {}


def session_for(sid: str) -> GameSession:
    if sid not in SESSIONS:
        SESSIONS[sid] = GameSession()
    return SESSIONS[sid]


def _sid_from_cookie(handler: "Handler") -> str:
    """从 Cookie 取会话 id；没有就新建一个并回写（同一浏览器固定同一个角色）。"""
    cookie = handler.headers.get("Cookie", "") or ""
    for part in cookie.split(";"):
        k, _, v = part.strip().partition("=")
        if k == "sid" and v:
            return v[:64]
    import uuid
    sid = uuid.uuid4().hex
    handler._new_cookie = sid          # 由 _send_headers 统一回写
    return sid


class Handler(BaseHTTPRequestHandler):
    _new_cookie: str | None = None

    # ---------- P1-5：HTTP 边界兜住所有下游异常 ----------
    def do_GET(self) -> None:
        try:
            if self.path == "/api/status":
                sess = session_for(_sid_from_cookie(self))
                self._json({"status": sess.status(),
                            "data": player_data(sess.game),
                            "actions": sess.actions()})
            elif self.path == "/api/catalog":
                sess = session_for(_sid_from_cookie(self))
                self._json({"catalog": catalog_data(sess.game)})
            else:
                body = PAGE.encode("utf-8")
                self.send_response(200)
                self._send_headers("text/html; charset=utf-8", len(body))
                self.end_headers()
                self.wfile.write(body)
        except Exception as exc:                       # 任何异常都不该变成空响应
            self._fail(exc)

    def do_POST(self) -> None:
        try:
            if self.path == "/api/command":
                length = int(self.headers.get("Content-Length", 0))
                try:
                    data = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                    line = str(data.get("line", ""))[:200]
                except (ValueError, UnicodeDecodeError):
                    line = ""
                sess = session_for(_sid_from_cookie(self))
                self._json({"logs": sess.command(line),
                            "time": sess.game.time_text()})
            else:
                self.send_error(404)
        except Exception as exc:
            self._fail(exc)

    def _fail(self, exc: Exception) -> None:
        """结构化返回错误，前端能显示原因而不是「连接中断」。"""
        import traceback
        traceback.print_exc()
        self._json({"error": f"{type(exc).__name__}: {exc}"}, code=500)

    def _send_headers(self, content_type: str, length: int) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        if self._new_cookie:
            self.send_header("Set-Cookie", f"sid={self._new_cookie}; Path=/; Max-Age=86400")
            self._new_cookie = None

    def _json(self, obj: dict, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._send_headers("application/json; charset=utf-8", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:   # 静默访问日志
        pass


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8000
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"文字修仙 Web 版已启动：http://0.0.0.0:{port}")
    print("手机连同一 WiFi，用浏览器打开 http://<本机IP>:" + str(port) + "（本机 IP 可用 ifconfig/ipconfig 查询）")
    print("按 Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
