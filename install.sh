#!/usr/bin/env bash
# Build & launcher installer for "Intervention"
set -euo pipefail

sudo apt update
sudo apt install -y python3 python3-venv python3-pip

echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

curl -LsSf https://astral.sh/uv/install.sh | sh

curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash

export NVM_DIR="$([ -z "${XDG_CONFIG_HOME-}" ] && printf %s "${HOME}/.nvm" || printf %s "${XDG_CONFIG_HOME}/nvm")"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh" # This loads nvm

nvm install --lts
nvm use lts
corepack enable

corepack prepare yarn@stable --activate

sudo snap install go --classic

# --- Resolve paths ---
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"   # ~/Desktop/intervention
START_SRC="$PROJECT_ROOT/start.go"                                # adjust if different
START_BIN="$DESKTOP_DIR/start-go"
APP_NAME="intervention"
LAUNCHER_NAME="$APP_NAME.desktop"
LAUNCHER_APP="$HOME/.local/share/applications/$LAUNCHER_NAME"
LAUNCHER_DESK="$DESKTOP_DIR/$LAUNCHER_NAME"
LOG_FILE="$DESKTOP_DIR/${APP_NAME}.log"

# Ensure your venv's python path persists for shells you open later
# (You previously asked to put this in ~/.bashrc.)
if ! grep -Fq '/home/p/Desktop/intervention/agent/.venv/bin/python3' "$HOME/.bashrc" 2>/dev/null; then
  echo 'export PATH="/home/p/Desktop/intervention/agent/.venv/bin/python3:$PATH"' >> "$HOME/.bashrc"
fi

# --- Ensure Yarn is installed and on PATH (user-local) ---
ensure_yarn() {
  if command -v yarn >/dev/null 2>&1; then
    return 0
  fi

  # Try Corepack first (comes with modern Node)
  if command -v corepack >/dev/null 2>&1; then
    corepack enable || true
    corepack prepare yarn@stable --activate || true
  fi

  # If still missing, install yarn user-locally with npm (no sudo)
  if ! command -v yarn >/dev/null 2>&1; then
    if command -v npm >/dev/null 2>&1; then
      mkdir -p "$HOME/.local"
      npm config set prefix "$HOME/.local"
      npm install -g yarn
      # Make sure ~/.local/bin is on PATH for future shells
      if ! grep -Fq 'export PATH="$HOME/.local/bin:$PATH"' "$HOME/.bashrc" 2>/dev/null; then
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
      fi
    else
      echo "[X] npm not found; install Node/npm or enable Corepack so Yarn is available."
      exit 1
    fi
  fi

  # Sanity log
  echo "[*] Yarn version: $(yarn --version 2>/dev/null || echo 'not found')"
}
ensure_yarn

# --- Detect yarn location and export it ---
YARN_BIN="$(command -v yarn 2>/dev/null || true)"
if [[ -n "${YARN_BIN}" ]]; then
  YARN_DIR="$(dirname "$YARN_BIN")"
  # Export for current run
  export PATH="$YARN_DIR:$PATH"
  # Persist for future shells
  if ! grep -Fq "$YARN_DIR" "$HOME/.bashrc" 2>/dev/null; then
    echo "export PATH=\"$YARN_DIR:\$PATH\"" >> "$HOME/.bashrc"
  fi
  echo "[*] Using yarn at: $YARN_BIN"
else
  echo "[X] yarn still not found after ensure_yarn" >&2
  exit 1
fi

# --- Optional: build Python things if ./agent exists ---
if [[ -d "$PROJECT_ROOT/agent" ]]; then
  echo "[*] Building Python env in: $PROJECT_ROOT/agent"
  pushd "$PROJECT_ROOT/agent" >/dev/null
  uv python install 3.11
  rm -rf .venv
  uv venv .venv -p 3.11
  # shellcheck disable=SC1091
  source .venv/bin/activate
  uv pip install -r requirements.txt
  uv pip install -e .
  uv pip install -e third_party/DynamixelSDK/python
  uv pip install tyro
  deactivate || true
  popd >/dev/null
else
  echo "[!] Skipping Python build: $PROJECT_ROOT/agent not found"
fi

# --- Optional: build UI if ./ui exists ---
if [[ -d "$PROJECT_ROOT/ui" ]]; then
  echo "[*] Building UI in: $PROJECT_ROOT/ui"
  pushd "$PROJECT_ROOT/ui" >/dev/null
  # yarn should now exist
  yarn install
  yarn run build
  popd >/dev/null
else
  echo "[!] Skipping UI build: $PROJECT_ROOT/ui not found"
fi

# --- Build Go starter binary ---
echo "[*] Building Go binary -> $START_BIN"
mkdir -p "$DESKTOP_DIR"
if [[ ! -f "$START_SRC" ]]; then
  echo "[X] start.go not found at: $START_SRC"
  exit 1
fi
go build -o "$START_BIN" "$START_SRC"
chmod +x "$START_BIN"

# --- Write .desktop launcher into applications dir ---
echo "[*] Writing launcher: $LAUNCHER_APP"
mkdir -p "$(dirname "$LAUNCHER_APP")"
cat >"$LAUNCHER_APP" <<EOF
[Desktop Entry]
Type=Application
Name=Intervention
Comment=Launch Intervention (opens a terminal, logs to $LOG_FILE)
Terminal=true
# IMPORTANT: source ~/.bashrc so PATH changes (venv, yarn dir) apply here
Exec=bash -lc 'export PATH="'"$YARN_DIR"':$PATH"; source "$HOME/.bashrc"; echo -e "\n--- $(date) ---" >> "'"$LOG_FILE"'"; "'"$START_BIN"'" >> "'"$LOG_FILE"'" 2>&1 || { echo "Exited with code $? (see '"$LOG_FILE"')"; read -p "Press Enter to close..."; }'
# Working directory for the app
Path=$PROJECT_ROOT
# Optional icon:
# Icon=$PROJECT_ROOT/icon.png
EOF

# --- Copy to Desktop, trust, and chmod ---
echo "[*] Copying launcher to Desktop: $LAUNCHER_DESK"
cp -f "$LAUNCHER_APP" "$LAUNCHER_DESK"
chmod +x "$LAUNCHER_APP" "$LAUNCHER_DESK"

if command -v gio >/dev/null 2>&1; then
  gio set "$LAUNCHER_DESK" "metadata::trusted" yes || true
fi

# --- Validate (non-fatal) ---
desktop-file-validate "$LAUNCHER_APP" || true

mkdir -p $DESKTOP_DIR/datasets

echo "-------------------------------------------"
echo "Done."
echo "Launcher on Desktop: $LAUNCHER_DESK"
echo "App menu entry     : $LAUNCHER_APP"
echo "Binary             : $START_BIN"
echo "Logs               : $LOG_FILE"
echo "Note: launcher sources ~/.bashrc; yarn directory has been added to PATH."
