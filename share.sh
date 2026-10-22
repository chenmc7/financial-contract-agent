#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"
PORT="${CONTRACTGUARD_SHARE_PORT:-8900}"

export CONTRACTGUARD_SHARE_MODE=1
export CONTRACTGUARD_DEMO_MODE=0
export CONTRACTGUARD_PORT="$PORT"
# 公网隧道与本机服务共用同一份数据，合同、人工修正和分析结果会即时同步。
export CONTRACTGUARD_DB_PATH="$PROJECT_DIR/data/contractguard.db"
export CONTRACTGUARD_UPLOAD_DIR="$PROJECT_DIR/uploads"
./demo.sh &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT INT TERM

echo "[ContractGuard] 正在等待本地服务通过健康检查…"
for _ in $(seq 1 45); do
  if curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
    echo "[ContractGuard] 本地服务正常，数据持久化已启用。"
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "[ContractGuard] 本地服务启动失败，请查看上方错误。" >&2
    exit 1
  fi
  sleep 1
done
curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null

if command -v cloudflared >/dev/null 2>&1; then
  CLOUDFLARED="$(command -v cloudflared)"
else
  mkdir -p .tools
  CLOUDFLARED="$PROJECT_DIR/.tools/cloudflared"
  if [ ! -x "$CLOUDFLARED" ]; then
    case "$(uname -m)" in
      arm64) ASSET="cloudflared-darwin-arm64.tgz" ;;
      x86_64) ASSET="cloudflared-darwin-amd64.tgz" ;;
      *) echo "不支持的 Mac 架构：$(uname -m)。请手动安装 cloudflared。" >&2; exit 1 ;;
    esac
    echo "[ContractGuard] 未发现 cloudflared，正在下载项目内的免安装版本…"
    DOWNLOAD_TMP=".tools/cloudflared.tgz.part"
    if ! curl -fL --connect-timeout 10 --max-time 120 --retry 2 \
      "https://github.com/cloudflare/cloudflared/releases/latest/download/${ASSET}" -o "$DOWNLOAD_TMP"; then
      rm -f "$DOWNLOAD_TMP"
      if command -v brew >/dev/null 2>&1; then
        echo "[ContractGuard] 自动下载失败。请先运行：brew install cloudflared，然后重新执行 ./share.sh" >&2
      else
        echo "[ContractGuard] 自动下载失败。请从 Cloudflare 官方下载页安装 cloudflared，再重新执行 ./share.sh：" >&2
        echo "https://developers.cloudflare.com/tunnel/downloads/" >&2
      fi
      exit 1
    fi
    mv "$DOWNLOAD_TMP" .tools/cloudflared.tgz
    tar -xzf .tools/cloudflared.tgz -C .tools
    chmod +x "$CLOUDFLARED"
  fi
fi

echo "[ContractGuard] 公网实例与本机共用合同数据库和上传目录，本机修改会同步到公网。"
echo "[ContractGuard] 正在创建临时公网地址；终端出现 https://*.trycloudflare.com 后即可发给组员。"
echo "[ContractGuard] 保持此窗口运行；按 Ctrl+C 会同时关闭公网共享和本地服务。"
"$CLOUDFLARED" tunnel --no-autoupdate --protocol http2 --url "http://127.0.0.1:${PORT}"
