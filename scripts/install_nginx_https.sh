#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="${COZE_WORKSPACE_PATH:-$(dirname "$SCRIPT_DIR")}"

SERVER_NAME="${1:-8.153.87.6}"
SSL_CERT_PATH="${SSL_CERT_PATH:-/etc/nginx/cert/_.hifleet.com_cert_chain.pem}"
SSL_KEY_PATH="${SSL_KEY_PATH:-/etc/nginx/cert/_.hifleet.com_key.key}"

TEMPLATE_PATH="${WORK_DIR}/deploy/nginx/hifleet-agent-https.conf.template"
TARGET_PATH="/etc/nginx/sites-enabled/hifleet-agent-https.conf"

if [ ! -f "${TEMPLATE_PATH}" ]; then
  echo "未找到模板文件: ${TEMPLATE_PATH}"
  exit 1
fi

if [ ! -f "${SSL_CERT_PATH}" ]; then
  echo "未找到证书文件: ${SSL_CERT_PATH}"
  exit 1
fi

if [ ! -f "${SSL_KEY_PATH}" ]; then
  echo "未找到私钥文件: ${SSL_KEY_PATH}"
  exit 1
fi

TMP_FILE="$(mktemp)"
trap 'rm -f "${TMP_FILE}"' EXIT

sed \
  -e "s|__SERVER_NAME__|${SERVER_NAME}|g" \
  -e "s|__SSL_CERT_PATH__|${SSL_CERT_PATH}|g" \
  -e "s|__SSL_KEY_PATH__|${SSL_KEY_PATH}|g" \
  "${TEMPLATE_PATH}" > "${TMP_FILE}"

sudo cp "${TMP_FILE}" "${TARGET_PATH}"
sudo nginx -t

# 统一使用 systemd 托管 nginx；若系统未启用 systemd nginx，再回退 HUP master
if systemctl list-unit-files | awk '{print $1}' | grep -q '^nginx\.service$'; then
  if [ "$(systemctl is-active nginx 2>/dev/null || true)" = "active" ]; then
    sudo systemctl reload nginx
  else
    sudo systemctl start nginx
  fi
else
  NGINX_MASTER_PID="$(ps -ef | awk '/nginx: master process/ && !/awk/ {print $2; exit}')"
  if [ -z "${NGINX_MASTER_PID}" ]; then
    echo "未找到 nginx master 进程，无法重载。"
    exit 1
  fi
  sudo kill -HUP "${NGINX_MASTER_PID}"
fi

echo "Nginx HTTPS 站点已部署：${TARGET_PATH}"
echo "SERVER_NAME=${SERVER_NAME}"
echo "反向代理目标: http://127.0.0.1:10123"
echo "验证命令: curl -k https://${SERVER_NAME}/health"
