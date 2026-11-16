#!/data/data/com.termux/files/usr/bin/bash
# nanoMuse on one phone, with no computer: the app's model, or the whole runtime.
#
# Run it in Termux on Android.  It installs Ollama (a model server that runs on the phone)
# and a model, and it can add the Python runtime and the web app too.  After the first
# download nothing needs the internet: no relay, no computer, no account.
#
#   curl -fsSL https://raw.githubusercontent.com/nano-muse/nanoMuse/main/scripts/phone/install.sh | bash
#   curl -fsSL .../scripts/phone/install.sh | bash -s -- --app
#   bash scripts/phone/install.sh --runtime --source /sdcard/nanoMuse/repo
#
# The two routes and their limits are written down in docs/phone-only.md.

set -euo pipefail

MODE="runtime"
MODEL="${NANOMUSE_PHONE_MODEL:-}"
REPO_URL="${NANOMUSE_REPO_URL:-https://github.com/nano-muse/nanoMuse.git}"
SOURCE="${NANOMUSE_PHONE_SOURCE:-}"
BUNDLE=""
PORT="${NANOMUSE_PHONE_PORT:-8787}"
DISTRO="debian"
SKIP_MODEL=0
NO_BOOT=0
NO_START=0
DRY_RUN=0
ASSUME_YES=0

TERMUX_PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
TERMUX_HOME="${HOME:-/data/data/com.termux/files/home}"
DATA_DIR="${NANOMUSE_DATA_DIR:-$TERMUX_HOME/.nanomuse}"
OLLAMA_URL="http://127.0.0.1:11434"

usage() {
  cat <<'EOF'
nanoMuse on one phone, with no computer.

Usage: bash install.sh [--app | --runtime] [options]

  --app            Install only the model server and a model, for the nanoMuse app.
  --runtime        Install the Python runtime and its web app too (the default).
  --model NAME     The Ollama model to download (default: picked from the phone's memory).
  --source DIR     Install from this checkout instead of cloning the repository.
  --bundle FILE    Unpack a bundle made by `nanomuse phone package`: code, data and
                   workspace together, so the phone keeps this computer's chats and files.
  --repo URL       Clone this repository (default: the nanoMuse GitHub repository).
  --port N         The web app's port on 127.0.0.1 (default: 8787).
  --skip-model     Do not download a model (it is already on the phone).
  --no-boot        Do not write the Termux:Boot script.
  --no-start       Prepare everything but do not start it.
  --dry-run        Print what would happen; change nothing.
  -y, --yes        Do not ask anything (nothing asks today; kept for scripts).
  -h, --help       This text.

After it finishes:
  nanomuse-phone up       start the model and the app
  nanomuse-phone status   what is running
  nanomuse-phone open     open the web app
  nanomuse-phone logs     the last lines of both logs

Install Termux from F-Droid, not Google Play.  Termux:Boot brings nanoMuse back after a
restart; Termux:API puts reminders in the notification shade.  The first setup downloads
the model and needs Wi-Fi; after that the phone works offline.
EOF
}

say() { printf '%s\n' "$*"; }
note() { printf '\033[1;36m%s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$*" >&2; }
die() { printf '\033[1;31m%s\033[0m\n' "$*" >&2; exit 1; }

run() {
  if [ "$DRY_RUN" = 1 ]; then
    printf '  + %s\n' "$*"
  else
    "$@"
  fi
}

run_shell() {
  if [ "$DRY_RUN" = 1 ]; then
    printf '  + %s\n' "$*"
  else
    bash -lc "$*"
  fi
}

is_termux() {
  [ -n "${TERMUX_VERSION:-}" ] || [ -d "/data/data/com.termux" ] || [ -d "$TERMUX_PREFIX" ]
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --app) MODE="app" ;;
    --runtime | --web) MODE="runtime" ;;
    --model) [ "$#" -ge 2 ] || die "--model needs a name"; MODEL="$2"; shift ;;
    --repo) [ "$#" -ge 2 ] || die "--repo needs a URL"; REPO_URL="$2"; shift ;;
    --source) [ "$#" -ge 2 ] || die "--source needs a directory"; SOURCE="$2"; shift ;;
    --bundle) [ "$#" -ge 2 ] || die "--bundle needs a file"; BUNDLE="$2"; shift ;;
    --port) [ "$#" -ge 2 ] || die "--port needs a number"; PORT="$2"; shift ;;
    --skip-model) SKIP_MODEL=1 ;;
    --no-boot) NO_BOOT=1 ;;
    --no-start) NO_START=1 ;;
    --dry-run | --print-plan) DRY_RUN=1 ;;
    -y | --yes) ASSUME_YES=1 ;;
    -h | --help) usage; exit 0 ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
  shift
done

case "$PORT" in
  '' | *[!0-9]*) die "--port must be a number" ;;
esac

if ! is_termux && [ "$DRY_RUN" = 0 ]; then
  die "This installer is for Termux on Android. On a computer, use install.sh."
fi

mkdir -p "$DATA_DIR/logs" "$DATA_DIR/run" 2>/dev/null || true

pick_model() {
  local kb gb
  kb="$(awk '/^MemTotal:/{print $2}' /proc/meminfo 2>/dev/null || true)"
  gb=0
  if [ -n "$kb" ]; then gb=$((kb / 1024 / 1024)); fi
  if [ "$gb" -ge 10 ]; then
    MODEL="qwen3:8b"
  elif [ "$gb" -ge 6 ]; then
    MODEL="qwen3:4b"
  else
    MODEL="qwen2.5:3b"
  fi
  say "This phone has about ${gb} GB of memory; choosing $MODEL."
}

install_ollama() {
  if command -v ollama >/dev/null 2>&1; then
    return 0
  fi
  note "Installing Ollama (the model server)..."
  if ! run pkg install -y ollama; then
    warn "The main Termux repository has no Ollama package; adding tur-repo."
    run pkg install -y tur-repo
    run pkg update -y
    run pkg install -y ollama
  fi
  if [ "$DRY_RUN" = 0 ] && ! command -v ollama >/dev/null 2>&1; then
    die "Ollama was not installed. Open Termux, run 'pkg update && pkg install ollama', and run this installer again."
  fi
}

model_up() {
  curl -fsS --max-time 2 "$OLLAMA_URL/api/tags" >/dev/null 2>&1
}

wait_for_model() {
  local i=0
  while [ "$i" -lt 90 ]; do
    if model_up; then return 0; fi
    sleep 1
    i=$((i + 1))
  done
  return 1
}

start_ollama() {
  if model_up; then return 0; fi
  note "Starting Ollama..."
  if [ "$DRY_RUN" = 1 ]; then
    say "  + nohup ollama serve >> $DATA_DIR/logs/ollama.log 2>&1 &"
    return 0
  fi
  nohup ollama serve >>"$DATA_DIR/logs/ollama.log" 2>&1 &
  echo $! >"$DATA_DIR/run/ollama.pid"
  if ! wait_for_model; then
    die "Ollama did not answer on $OLLAMA_URL. See $DATA_DIR/logs/ollama.log."
  fi
}

pull_model() {
  [ "$SKIP_MODEL" = 1 ] && return 0
  note "Downloading $MODEL (once; this is the large download)..."
  start_ollama
  run ollama pull "$MODEL"
}

free_mb() {
  df -Pk "$TERMUX_HOME" 2>/dev/null | awk 'NR==2{print $4}' || echo 0
}

check_space() {
  [ "$SKIP_MODEL" = 1 ] && return 0
  local mb
  mb="$(free_mb)"
  if [ "${mb:-0}" -lt 4096 ] 2>/dev/null; then
    warn "Only ${mb} MB free. A 4B model and the runtime want about 4 GB or more."
    warn "Free some space, or pass --model qwen2.5:3b (about 2 GB) or --skip-model."
    if [ "$ASSUME_YES" = 0 ] && [ "$DRY_RUN" = 0 ]; then
      die "Not enough free space."
    fi
  fi
}

app_route() {
  say ""
  say "The nanoMuse app can now use a model on this phone."
  say ""
  say "1. Install the app: https://github.com/nano-muse/nanoMuse/releases/latest"
  say "   Open it and choose 'Use your own API key instead' (no account is needed)."
  say "2. Settings → Models → Chat → Add a provider → Ollama."
  say "   Address: $OLLAMA_URL/v1"
  say "   Model:   $MODEL"
  say "3. For Operating the screen, download a model that reads pictures and pick it there:"
  say "   ollama pull qwen2.5vl:3b"
  say ""
  say "Keep Termux open while the app works: Ollama runs inside it. A wake lock was taken"
  say "so Android keeps it available; 'termux-wake-unlock' releases it."
  say "Install Termux:Boot and Termux:API from F-Droid, and allow Termux to run in the"
  say "background in Android's battery settings, so the model is there after a restart."
  say ""
  say "This route needs no Python, no computer and no account. The app's own agent and"
  say "data stay on the phone; the model answers from Termux."
}

guest_script() {
  local file="$1"
  cat >"$file" <<'GUEST'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends python3 python3-venv python3-pip git ca-certificates

SRC_DIR=""
if [ -n "${NANOMUSE_INSTALL_BUNDLE:-}" ]; then
  rm -rf /opt/nanomuse-bundle
  mkdir -p /opt/nanomuse-bundle
  tar -xzf "$NANOMUSE_INSTALL_BUNDLE" -C /opt/nanomuse-bundle
  if [ -d /opt/nanomuse-bundle/data ]; then
    cp -a /opt/nanomuse-bundle/data/. /root/.nanomuse/
  fi
  if [ -d /opt/nanomuse-bundle/workspace ]; then
    mkdir -p /root/.nanomuse/workspace
    cp -a /opt/nanomuse-bundle/workspace/. /root/.nanomuse/workspace/
  fi
  SRC_DIR=/opt/nanomuse-bundle/repo
elif [ -n "${NANOMUSE_INSTALL_SOURCE:-}" ]; then
  rm -rf /opt/nanomuse
  mkdir -p /opt/nanomuse
  tar -xzf "$NANOMUSE_INSTALL_SOURCE" -C /opt/nanomuse
  SRC_DIR=/opt/nanomuse
elif [ -d /opt/nanomuse/.git ]; then
  git -C /opt/nanomuse pull --ff-only || true
  SRC_DIR=/opt/nanomuse
else
  rm -rf /opt/nanomuse
  git clone --depth 1 "$NANOMUSE_INSTALL_REPO" /opt/nanomuse
  SRC_DIR=/opt/nanomuse
fi

rm -rf "$SRC_DIR/.venv"
python3 -m venv "$SRC_DIR/.venv"
"$SRC_DIR/.venv/bin/pip" install --upgrade pip wheel
"$SRC_DIR/.venv/bin/pip" install -e "$SRC_DIR"
cd "$SRC_DIR"
.venv/bin/nanomuse phone setup \
  --data-dir /root/.nanomuse \
  --model "$NANOMUSE_INSTALL_MODEL" \
  --port "$NANOMUSE_INSTALL_PORT" \
  --service --force
GUEST
}

runtime_route() {
  note "Installing the Debian container for the runtime..."
  if ! proot-distro list -q 2>/dev/null | grep -qx "$DISTRO"; then
    run proot-distro install "$DISTRO"
  fi

  local guest_file="$DATA_DIR/install-guest.sh"
  guest_script "$guest_file"

  local source_env=""
  if [ -n "$SOURCE" ]; then
    if [ -f "$SOURCE/pyproject.toml" ]; then
      :
    elif [ -f "$SOURCE/repo/pyproject.toml" ]; then
      SOURCE="$SOURCE/repo"
    else
      die "--source must name the nanoMuse checkout (the folder with pyproject.toml)."
    fi
    note "Copying $SOURCE into the container..."
    if [ "$DRY_RUN" = 0 ]; then
      tar -C "$SOURCE" -czf "$TERMUX_HOME/nanomuse-src.tgz" \
        --exclude=.git --exclude=.venv --exclude=__pycache__ .
    else
      say "  + tar -C $SOURCE -czf $TERMUX_HOME/nanomuse-src.tgz ..."
    fi
    source_env="/root/nanomuse-src.tgz"
  fi

  local bundle_env=""
  if [ -n "$BUNDLE" ]; then
    [ -f "$BUNDLE" ] || die "--bundle: $BUNDLE not found"
    note "Copying the bundle into the container..."
    if [ "$DRY_RUN" = 0 ]; then
      cp "$BUNDLE" "$TERMUX_HOME/nanomuse-bundle.tar.gz"
    else
      say "  + cp $BUNDLE $TERMUX_HOME/nanomuse-bundle.tar.gz"
    fi
    bundle_env="/root/nanomuse-bundle.tar.gz"
  fi

  note "Installing Python and the nanoMuse runtime inside Debian (a few minutes)..."
  if [ "$DRY_RUN" = 1 ]; then
    say "  + proot-distro login $DISTRO --shared-home --shared-tmp -- bash -s < guest script"
    say ""
    sed 's/^/    /' "$guest_file"
    say ""
  else
    local envs=(
      --env "NANOMUSE_INSTALL_MODEL=$MODEL"
      --env "NANOMUSE_INSTALL_PORT=$PORT"
      --env "NANOMUSE_INSTALL_REPO=$REPO_URL"
    )
    if [ -n "$source_env" ]; then
      envs+=(--env "NANOMUSE_INSTALL_SOURCE=$source_env")
    fi
    if [ -n "$bundle_env" ]; then
      envs+=(--env "NANOMUSE_INSTALL_BUNDLE=$bundle_env")
    fi
    proot-distro login "$DISTRO" --shared-home --shared-tmp "${envs[@]}" -- \
      bash -s <"$guest_file"
  fi

  if [ "$NO_BOOT" = 1 ] && [ "$DRY_RUN" = 0 ]; then
    rm -f "$TERMUX_HOME/.termux/boot/nanomuse.sh"
  fi

  if [ "$SKIP_MODEL" = 0 ]; then
    note "Downloading the embedding model for recall by meaning..."
    run ollama pull nomic-embed-text || true
  fi

  if [ "$NO_START" = 0 ]; then
    note "Starting nanoMuse..."
    if [ "$DRY_RUN" = 1 ]; then
      say "  + $TERMUX_PREFIX/bin/nanomuse-phone up"
      say "  + $TERMUX_PREFIX/bin/nanomuse-phone open"
    else
      "$TERMUX_PREFIX/bin/nanomuse-phone" up
      "$TERMUX_PREFIX/bin/nanomuse-phone" open || true
    fi
  fi

  say ""
  say "nanoMuse is on this phone."
  say "  Web app:  http://127.0.0.1:$PORT/"
  say "  Stop:     nanomuse-phone down"
  say "  Status:   nanomuse-phone status"
  say "  Logs:     nanomuse-phone logs"
  say ""
  say "Install Termux:Boot and Termux:API from F-Droid. Allow Termux to run in the"
  say "background in Android's battery settings, or Android will stop it when the phone"
  say "sleeps. The first start needs Wi-Fi; after that it works offline."
  say ""
  say "To restore a data export kept in Downloads, run 'termux-setup-storage' once and"
  say "allow it, then 'nanomuse-phone restore ~/storage/downloads/FILE.zip'."
}

# ------------------------------------------------------------------------------ main
if [ "$DRY_RUN" = 0 ]; then
  note "Updating the Termux package lists..."
  pkg update -y
  pkg install -y curl tar
else
  say "  + pkg update -y"
  say "  + pkg install -y curl tar"
fi

case "$MODE" in
  app)
    install_ollama
    [ -n "$MODEL" ] || pick_model
    check_space
    pull_model
    run pkg install -y termux-api
    if command -v termux-wake-lock >/dev/null 2>&1; then
      run termux-wake-lock || true
    fi
    app_route
    ;;
  runtime)
    install_ollama
    [ -n "$MODEL" ] || pick_model
    check_space
    pull_model
    run pkg install -y git proot-distro termux-api
    runtime_route
    ;;
  *)
    die "unknown mode: $MODE"
    ;;
esac

say ""
say "Done."
