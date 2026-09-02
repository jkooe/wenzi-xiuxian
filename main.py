"""命令行入口。

    python main.py                      进入交互模式（新开档）
    python main.py --name 李青玄 --seed 42
    python main.py --load 1             读取 1 号存档
    python main.py --demo               跑一遍核心循环演示（无交互）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from xiuxian.core.save import SaveError  # noqa: E402
from xiuxian.factory import create_game, load_game  # noqa: E402
from xiuxian.ui.cli import CLI  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="文字修仙 —— 可扩展的修仙文字游戏框架")
    ap.add_argument("--name", default="无名", help="角色名")
    ap.add_argument("--seed", type=int, default=None, help="随机种子（固定则结果可复现）")
    ap.add_argument("--load", type=int, metavar="SLOT", help="读取指定存档槽位")
    ap.add_argument("--slot", type=int, default=1, help="默认存档槽位")
    ap.add_argument("--demo", action="store_true", help="运行核心循环演示")
    args = ap.parse_args()

    if args.demo:
        import demo
        sys.argv = [sys.argv[0], "--seed", str(args.seed or 42), "--name", args.name]
        demo.main()
        return

    try:
        game = load_game(args.load) if args.load else create_game(args.name, args.seed)
    except SaveError as exc:
        print(f"读档失败：{exc}")
        return

    print("文字修仙 · 框架演示版")
    CLI(game).run()


if __name__ == "__main__":
    main()
