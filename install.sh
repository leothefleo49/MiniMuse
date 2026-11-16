#!/usr/bin/env bash
# nanoMuse One-Command Installer for Linux, macOS & Android Termux
# Usage: curl -fsSL https://raw.githubusercontent.com/nanoMuse/nanoMuse/main/install.sh | bash

set -e

echo -e "\033[1;36m==========================================================\033[0m"
echo -e "\033[1;36m                nanoMuse One-Click Setup                  \033[0m"
echo -e "\033[1;36m==========================================================\033[0m"
echo -e "An open-source personal AI agent for every device you own.\n"

INSTALL_DIR="${HOME}/nanomuse"

# Detect OS
OS_TYPE="$(uname -s)"
IS_TERMUX=false
if [ -n "${TERMUX_VERSION}" ] || [ -d "/data/data/com.termux" ]; then
  IS_TERMUX=true
fi

echo -e "\033[1;33m[1/4] Checking environment ($OS_TYPE, Termux: $IS_TERMUX)...\033[0m"

# A phone is its own host.  The phone installer knows Termux, the model that runs there and
# the Debian container the runtime lives in; this script keeps its old shape for computers.
# docs/phone-only.md has both routes.
if [ "$IS_TERMUX" = true ]; then
  HERE="$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)"
  if [ -n "$HERE" ] && [ -f "$HERE/scripts/phone/install.sh" ]; then
    exec bash "$HERE/scripts/phone/install.sh" "$@"
  fi
  pkg install -y curl >/dev/null 2>&1 || true
  RAW="${NANOMUSE_RAW_BASE:-https://raw.githubusercontent.com/nano-muse/nanoMuse/main}"
  echo -e "\033[1;36mStarting the phone installer...\033[0m"
  curl -fsSL "$RAW/scripts/phone/install.sh" | bash -s -- "$@"
  exit $?
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo -e "\033[1;31mPython 3 is required. Please install python3 (3.11+).\033[0m"
  exit 1
fi

echo -e "\033[1;33m[2/4] Setting up directory in ${INSTALL_DIR}...\033[0m"
mkdir -p "${INSTALL_DIR}"
cd "${INSTALL_DIR}"

if [ ! -f "pyproject.toml" ] && [ ! -d ".git" ]; then
  if command -v git >/dev/null 2>&1; then
    git clone https://github.com/nanoMuse/nanoMuse.git .
  fi
fi

echo -e "\033[1;33m[3/4] Preparing virtual environment and dependencies...\033[0m"
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install --upgrade pip --quiet
if [ -f "pyproject.toml" ]; then
  pip install -e . --quiet
else
  pip install nanomuse --quiet
fi

echo -e "\033[1;32m[4/4] Starting nanoMuse...\033[0m"
echo -e "\033[1;36mnanoMuse is ready! Open http://127.0.0.1:8787 in your browser or install the PWA.\033[0m\n"

if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "http://127.0.0.1:8787" &
elif command -v open >/dev/null 2>&1; then
  open "http://127.0.0.1:8787" &
fi

python3 -m nanomuse.server
