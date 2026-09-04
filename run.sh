#!/bin/bash
# 文字修仙一键启动：自动探测可用的 Python 3.10+（macOS 自带 python3 可能是 3.9，语法不支持）
#
# 用法：
#   ./run.sh                 # 进入交互模式（等价 python main.py）
#   ./run.sh --name 李青玄   # 自定义角色名
#   ./run.sh demo.py         # 跑自动演示
#   ./run.sh -m unittest discover -s tests -v   # 跑测试
set -e
cd "$(dirname "$0")"

# 联网修仙服务端（FastAPI + uvicorn，账号 + 存档上云）
#   ./run.sh server            # 默认 0.0.0.0:8000
#   ./run.sh server 9000       # 指定端口
if [[ "${1:-}" == "server" ]]; then
  shift || true
  PORT="${1:-8000}"
  if [[ ! -x ./.venv/bin/python ]]; then
    echo "错误：未找到 .venv，请先创建并安装依赖：" >&2
    echo "  python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt" >&2
    exit 1
  fi
  echo "启动联网修仙服务端（FastAPI + uvicorn）于 0.0.0.0:$PORT"
  exec ./.venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port "$PORT"
fi

# 候选解释器按优先级排列（workbuddy 托管 > Homebrew > 系统 PATH）
CANDIDATES=(
  /Users/mantianfuyun/.workbuddy/binaries/python/versions/3.13.12/bin/python3
  /opt/homebrew/bin/python3
  python3
)

for py in "${CANDIDATES[@]}"; do
  if command -v "$py" >/dev/null 2>&1; then
    # 数字比较主次版本：需 Python 3.10+
    vmaj=$("$py" -c 'import sys; print(sys.version_info.major)' 2>/dev/null || echo 0)
    vmin=$("$py" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null || echo 0)
    if (( vmaj > 3 || (vmaj == 3 && vmin >= 10) )); then
      if [[ "${1:-}" == "web" ]]; then
        shift
        echo "Using Python $vmaj.$vmin: $py"
        exec "$py" web_server.py "$@"
      fi
      echo "Using Python $vmaj.$vmin: $py"
      exec "$py" "${@:-main.py}"
    fi
  fi
done

echo "错误：未找到 Python 3.10+，请先安装：brew install python@3.12" >&2
exit 1
