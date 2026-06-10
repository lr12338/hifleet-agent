#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="${COZE_WORKSPACE_PATH:-$(dirname "$SCRIPT_DIR")}"
export COZE_WORKSPACE_PATH="$WORK_DIR"

# 系统服务固定端口（按需求统一为 10123）
PORT=10123

# 激活虚拟环境（存在则使用）
if [ -f "${WORK_DIR}/.venv/bin/activate" ]; then
  # shellcheck disable=SC1090
  source "${WORK_DIR}/.venv/bin/activate"
fi

# 不在 shell 中 source .env，避免空格/特殊字符导致解析失败。
# 环境变量由 src/main.py 内的 python-dotenv 统一加载。

# 一体化启动：若前端构建产物不存在，自动构建后台管理前端
FRONTEND_DIR="${WORK_DIR}/frontend"
ADMIN_UI_DIST="${FRONTEND_DIR}/dist/index.html"
BUILD_ADMIN_FRONTEND="${BUILD_ADMIN_FRONTEND:-1}"
FORCE_ADMIN_FRONTEND_BUILD="${FORCE_ADMIN_FRONTEND_BUILD:-0}"

if [ "${BUILD_ADMIN_FRONTEND}" = "1" ] && [ -f "${FRONTEND_DIR}/package.json" ]; then
  if [ ! -f "${ADMIN_UI_DIST}" ] || [ "${FORCE_ADMIN_FRONTEND_BUILD}" = "1" ]; then
    echo "[start_system_service] Building admin frontend..."
    if ! command -v npm >/dev/null 2>&1; then
      echo "[start_system_service] npm not found, skip admin frontend build."
    else
      pushd "${FRONTEND_DIR}" >/dev/null
      if [ ! -d "node_modules" ]; then
        npm install
      fi
      npm run build
      popd >/dev/null
    fi
  fi
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi

exec "$PYTHON_BIN" "${WORK_DIR}/src/main.py" -m http -p "${PORT}"
