#!/bin/bash

set -euo pipefail

SERVICE_NAME="hifleet-agent.service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="${COZE_WORKSPACE_PATH:-$(dirname "$SCRIPT_DIR")}"
TEMPLATE_PATH="${WORK_DIR}/deploy/hifleet-agent.service"
TARGET_PATH="/etc/systemd/system/${SERVICE_NAME}"
RUN_SCRIPT="${WORK_DIR}/scripts/start_system_service.sh"

if [ ! -f "${TEMPLATE_PATH}" ]; then
  echo "未找到服务模板: ${TEMPLATE_PATH}"
  exit 1
fi

if [ ! -f "${RUN_SCRIPT}" ]; then
  echo "未找到启动脚本: ${RUN_SCRIPT}"
  exit 1
fi

chmod +x "${RUN_SCRIPT}"

TMP_FILE="$(mktemp)"
trap 'rm -f "${TMP_FILE}"' EXIT

sed \
  -e "s|^User=.*|User=$(id -un)|" \
  -e "s|^Group=.*|Group=$(id -gn)|" \
  -e "s|^WorkingDirectory=.*|WorkingDirectory=${WORK_DIR}|" \
  -e "s|^ExecStart=.*|ExecStart=/bin/bash ${RUN_SCRIPT}|" \
  "${TEMPLATE_PATH}" > "${TMP_FILE}"

sudo cp "${TMP_FILE}" "${TARGET_PATH}"
sudo systemctl daemon-reload
sudo systemctl enable --now "${SERVICE_NAME}"

echo "系统服务已安装并启动: ${SERVICE_NAME}"
echo "查看状态: sudo systemctl status ${SERVICE_NAME}"
echo "查看日志: sudo journalctl -u ${SERVICE_NAME} -f"
