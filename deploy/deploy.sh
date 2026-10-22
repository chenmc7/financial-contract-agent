#!/usr/bin/env bash
# ============================================================
# ContractGuard 一键部署脚本（Ubuntu 22.04 / Debian 12）
# 用法：
#   1) 把整个 contractguard 目录传到服务器：rsync 或 scp
#      scp -r ~/Desktop/商策大赛/contractguard root@<服务器IP>:/opt/
#   2) 在服务器上执行：
#      bash /opt/contractguard/deploy/deploy.sh
#   3) 按提示填入 DeepSeek API Key
# ============================================================
set -euo pipefail

APP_DIR="/opt/contractguard"
SERVICE_NAME="contractguard"

echo "[1/5] 检查 root 权限"
if [ "$(id -u)" -ne 0 ]; then
  echo "请用 root 执行：sudo bash $0" >&2
  exit 1
fi

echo "[2/5] 安装系统依赖（python3-venv / curl / ufw）"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip curl ufw >/dev/null

echo "[3/5] 创建独立 Python 环境并安装依赖"
cd "$APP_DIR"
if [ ! -x ".venv/bin/python" ]; then
  python3 -m venv .venv
fi
# 用官方 PyPI，绕过失效镜像
.venv/bin/pip install --index-url https://pypi.org/simple -r requirements.txt >/dev/null

echo "[4/5] 配置 API Key（DeepSeek，国内直连无需梯子）"
ENV_FILE="$APP_DIR/.env"
if [ -f "$ENV_FILE" ] && grep -q "CONTRACTGUARD_API_KEY=sk-" "$ENV_FILE"; then
  echo "已检测到 .env 中的 API Key，跳过。"
else
  read -r -p "请输入 DeepSeek API Key（sk-...，在 https://platform.deepseek.com 创建）：" DEEPSEEK_KEY
  if [ -z "${DEEPSEEK_KEY:-}" ]; then
    echo "未输入 Key，将使用内置智能引擎兜底（分析仍可用，AI 问答受限）。"
  else
    printf 'CONTRACTGUARD_API_KEY=%s\n' "$DEEPSEEK_KEY" > "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo "已写入 $ENV_FILE"
  fi
fi

echo "[5/5] 安装 systemd 服务（开机自启 + 崩溃自动重启）"
cp "$APP_DIR/deploy/contractguard.service" "/etc/systemd/system/$SERVICE_NAME.service"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

# 防火墙放行端口（若用云厂商安全组，也请放行 TCP 8900）
ufw allow 8900/tcp >/dev/null 2>&1 || true

sleep 3
echo ""
echo "============================================================"
if curl -fsS "http://127.0.0.1:8900/api/health" >/dev/null 2>&1; then
  IP="$(hostname -I | awk '{print $1}')"
  echo "✅ 部署成功！访问地址：http://$IP:8900"
  echo "   发给组员，国内任何网络均可直接打开（无需梯子）。"
  echo "   查看状态：systemctl status contractguard"
  echo "   查看日志：journalctl -u contractguard -f"
else
  echo "❌ 服务未正常启动，请检查：journalctl -u contractguard -n 50"
fi
echo "============================================================"
