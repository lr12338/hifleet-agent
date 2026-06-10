#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="${COZE_WORKSPACE_PATH:-$(dirname "$SCRIPT_DIR")}"
export COZE_WORKSPACE_PATH="$WORK_DIR"

# 默认自动构建管理前端后再启动主服务
export BUILD_ADMIN_FRONTEND="${BUILD_ADMIN_FRONTEND:-1}"

exec /bin/bash "${WORK_DIR}/scripts/start_system_service.sh"
