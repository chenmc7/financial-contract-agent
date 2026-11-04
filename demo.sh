#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

PYTHON_BIN=""
for CANDIDATE in ".venv/bin/python" "python3" \
  "/Library/Frameworks/Python.framework/Versions/3.14/bin/python3" \
  "/opt/homebrew/bin/python3" "/usr/local/bin/python3"; do
  if command -v "$CANDIDATE" >/dev/null 2>&1 && \
    "$CANDIDATE" -c "import fastapi, uvicorn, multipart" >/dev/null 2>&1; then
    PYTHON_BIN="$CANDIDATE"
    break
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  BOOTSTRAP_PYTHON="$(command -v python3 || true)"
  if [ -z "$BOOTSTRAP_PYTHON" ]; then
    echo "[ContractGuard] 未找到 Python 3，请先安装 Python 3.11 或更高版本。" >&2
    exit 1
  fi
  echo "[ContractGuard] 首次运行，正在安装独立运行环境（使用官方 PyPI，绕过失效镜像）…"
  if [ ! -x ".venv/bin/python" ]; then
    "$BOOTSTRAP_PYTHON" -m venv .venv
  fi
  .venv/bin/python -m pip install --index-url https://pypi.org/simple -r requirements.txt
  PYTHON_BIN=".venv/bin/python"
fi

export CONTRACTGUARD_DEMO_MODE="${CONTRACTGUARD_DEMO_MODE:-1}"
export CONTRACTGUARD_HOST=127.0.0.1
export CONTRACTGUARD_PORT="${CONTRACTGUARD_PORT:-8899}"

echo "[ContractGuard] 本地演示：http://127.0.0.1:${CONTRACTGUARD_PORT}"
echo "[ContractGuard] 健康检查：http://127.0.0.1:${CONTRACTGUARD_PORT}/api/health"
echo "[ContractGuard] SQLite 数据保存在 ${CONTRACTGUARD_DB_PATH:-data/contractguard.db}，重启不会丢失。"
exec "$PYTHON_BIN" server.py
