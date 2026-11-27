#!/usr/bin/env bash
set -euo pipefail

PLUGIN_NAME="antigravity-fork-session"
TARGET_ROOT="${AGY_PLUGINS_DIR:-${HOME}/.gemini/config/plugins}"
TARGET_DIR="${TARGET_ROOT}/${PLUGIN_NAME}"

if [[ -L "${TARGET_DIR}" ]]; then
  rm -f "${TARGET_DIR}"
  echo "Removed symlink: ${TARGET_DIR}"
elif [[ -d "${TARGET_DIR}" ]]; then
  rm -rf "${TARGET_DIR}"
  echo "Removed plugin directory: ${TARGET_DIR}"
else
  echo "Plugin not found at ${TARGET_DIR}"
fi
