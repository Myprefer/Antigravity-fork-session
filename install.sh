#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_NAME="agy-session-manager"
TARGET_ROOT="${AGY_PLUGINS_DIR:-${HOME}/.gemini/config/plugins}"
TARGET_DIR="${TARGET_ROOT}/${PLUGIN_NAME}"

MODE="copy"
if [[ "${1:-}" == "--symlink" ]]; then
  MODE="symlink"
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 (3.9+) is required but not found in PATH." >&2
  exit 1
fi

mkdir -p "${TARGET_ROOT}"

if [[ -L "${TARGET_DIR}" ]]; then
  rm -f "${TARGET_DIR}"
elif [[ -d "${TARGET_DIR}" ]]; then
  echo "Updating existing installation at ${TARGET_DIR}..."
  rm -rf "${TARGET_DIR}"
fi

if [[ "${MODE}" == "symlink" ]]; then
  ln -s "${REPO_DIR}" "${TARGET_DIR}"
  echo "Installed (symlink): ${TARGET_DIR} -> ${REPO_DIR}"
else
  mkdir -p "${TARGET_DIR}"
  cp -R "${REPO_DIR}/plugin.json" \
        "${REPO_DIR}/README.md" \
        "${REPO_DIR}/assets" \
        "${REPO_DIR}/skills" \
        "${REPO_DIR}/sidecars" \
        "${TARGET_DIR}/"
  find "${TARGET_DIR}" -name "__pycache__" -type d -exec rm -rf {} +
  chmod +x "${TARGET_DIR}/sidecars/session-manager/server.py"
  echo "Installed: ${TARGET_DIR}"
fi

echo ""
echo "Next steps:"
echo "  1. Open Antigravity Desktop -> Customizations and enable '${PLUGIN_NAME}' (and its Sidecar)."
echo "  2. Click '+' in the right-hand side pane and select '会话 Fork 管理'."
