#!/usr/bin/env bash
# 双击本文件即可启动 ContractGuard（浏览器自动打开 http://127.0.0.1:8899）
# 默认使用真实数据模式；需要演示样例时请显式运行 demo.sh。
# 关闭此终端窗口即停止服务。
cd "$(dirname "$0")"
export CONTRACTGUARD_DEMO_MODE=0
export CONTRACTGUARD_HOST=127.0.0.1
export CONTRACTGUARD_PORT=8899

PYTHON_BIN=""
for CANDIDATE in ".venv/bin/python" "python3"; do
  if command -v "$CANDIDATE" >/dev/null 2>&1; then
    PYTHON_BIN="$CANDIDATE"
    break
  fi
done
if [ -z "$PYTHON_BIN" ]; then
  echo "[ContractGuard] 未找到 Python，请先安装 Python 3。"
  read -r -p "按回车键关闭…" _
  exit 1
fi

echo "[ContractGuard] 启动中（真实数据模式）… 浏览器将自动打开 http://127.0.0.1:8899"
sleep 1
( sleep 3 && open "http://127.0.0.1:8899" ) &
exec "$PYTHON_BIN" server.py
