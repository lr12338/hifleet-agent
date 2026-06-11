#!/usr/bin/env bash
set -euo pipefail

PRIMARY_IMAGE="${HIFLEET_PY_SANDBOX_IMAGE:-hifleet/python-sandbox:3.11}"
CANDIDATES_RAW="${HIFLEET_PY_SANDBOX_IMAGE_CANDIDATES:-$PRIMARY_IMAGE}"
IFS=',' read -r -a CANDIDATES <<< "$CANDIDATES_RAW"

for image in "${CANDIDATES[@]}"; do
  image="${image## }"
  image="${image%% }"
  [[ -z "$image" ]] && continue
  echo "[prepare_employee_sandbox_image] trying $image"
  if sudo -n docker image inspect "$image" >/dev/null 2>&1; then
    echo "[prepare_employee_sandbox_image] already available: $image"
    exit 0
  fi
  if sudo -n docker pull "$image"; then
    echo "[prepare_employee_sandbox_image] pulled: $image"
    exit 0
  fi
  echo "[prepare_employee_sandbox_image] failed: $image" >&2
done

echo "[prepare_employee_sandbox_image] no candidate image available" >&2
exit 1
