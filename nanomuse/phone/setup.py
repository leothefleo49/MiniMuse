"""The phone as the whole installation: a local model, the web app, no computer.

A phone can run nanoMuse by itself.  On Android the app already carries the agent; the
missing piece is a model that needs neither a computer nor the internet.  The other way is
the runtime and its web app, started in Termux and talking to a model served on the same
phone.  This module holds the parts both routes share: which model fits the phone, the
configuration that points the runtime at it, the service script that keeps it running, and
a safe restore of the data export written by another nanoMuse.

Nothing here installs a package or starts a process.  ``scripts/phone/install.sh`` does
that in Termux; the functions below are written so they can be tested anywhere.

The phone routes are described in ``docs/phone-only.md``.
"""

from __future__ import annotations

import io
import json
import os
import shlex
import stat
import tarfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Ollama serves an OpenAI-compatible API on this address.  A model server started by
#: another app on the phone (or by ``llama-server``) can be used instead; the setup command
#: takes ``--base-url`` for that.
OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"

#: The Termux paths, used when the command runs outside Termux's own shell (for example
#: inside the Debian container the installer creates).  They are the stable paths Android
#: gives every Termux installation.
TERMUX_PREFIX = Path("/data/data/com.termux/files/usr")
TERMUX_HOME = Path("/data/data/com.termux/files/home")


@dataclass(frozen=True)
class ModelChoice:
    """One model that can answer on a phone, and what it needs."""

    name: str
    label: str
    download_gb: float
    min_ram_gb: float
    vision: bool
    note: str


#: The models the installer offers.  They are Ollama names, so ``ollama pull <name>`` is the
#: whole download.  Sizes are the quantised weights, about what the phone stores; a phone
#: also needs room for the context and the app.  Qwen 3 has native tool calling, which the
#: agent needs; Qwen 2.5 VL reads pictures, which the phone's hands need.
MODELS: tuple[ModelChoice, ...] = (
    ModelChoice(
        name="qwen2.5:3b",
        label="Qwen 2.5 3B",
        download_gb=1.9,
        min_ram_gb=4.0,
        vision=False,
        note="The small one: a 4 GB phone, simple chat, the shortest answers.",
    ),
    ModelChoice(
        name="qwen3:4b",
        label="Qwen 3 4B",
        download_gb=2.6,
        min_ram_gb=6.0,
        vision=False,
        note="The default: tools and thinking, fits a 6 GB phone.",
    ),
    ModelChoice(
        name="qwen3:8b",
        label="Qwen 3 8B",
        download_gb=5.2,
        min_ram_gb=10.0,
        vision=False,
        note="Sharper and slower; a phone with 10 GB or more.",
    ),
    ModelChoice(
        name="qwen2.5vl:3b",
        label="Qwen 2.5 VL 3B",
        download_gb=3.2,
        min_ram_gb=6.0,
        vision=True,
        note="Reads pictures; use it for Operating the screen in the app, or for pictures.",
    ),
)

DEFAULT_MODEL = "qwen3:4b"

#: The first line of every file this module writes, so a later run and a person can tell
#: a generated phone configuration from a hand-written one.
MARKER = "# nanoMuse phone setup"


def read_ram_gb(meminfo: Path = Path("/proc/meminfo")) -> float:
    """The phone's main memory in GB, from ``/proc/meminfo``; 0 when it cannot be read."""
    try:
        for line in meminfo.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) / 1024 / 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


def choose_model(ram_gb: float, requested: str = "") -> ModelChoice:
    """The model to use: the one asked for, else the largest that fits ``ram_gb``.

    An unknown amount of memory is treated as a 6 GB phone, the common size.  A model of
    the person's own is returned as it is, with no size claims, so ``--model`` can name
    anything Ollama serves.
    """
    requested = requested.strip()
    if requested:
        for model in MODELS:
            if model.name == requested:
                return model
        return ModelChoice(requested, requested, 0.0, 0.0, False, "Chosen by you.")
    if ram_gb <= 0:
        ram_gb = 6.0
    fits = [m for m in MODELS if m.min_ram_gb <= ram_gb and not m.vision]
    return max(fits, key=lambda m: m.download_gb) if fits else MODELS[0]


def is_termux(environ: dict[str, str] | None = None) -> bool:
    """Whether this command runs on the phone, in Termux (directly or in its container)."""
    env = os.environ if environ is None else environ
    if env.get("TERMUX_VERSION") or env.get("TERMUX_APP_PID"):
        return True
    prefix = env.get("PREFIX", "")
    if prefix and "com.termux" in prefix:
        return True
    # proot-distro keeps Termux's $PREFIX reachable inside the container, and the path is
    # the app's private directory: both are true only on the phone.
    return (TERMUX_PREFIX / "bin").is_dir()


def termux_prefix(environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    value = env.get("PREFIX", "")
    if value and "com.termux" in value:
        return Path(value)
    return TERMUX_PREFIX


def termux_home(environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    home = env.get("HOME", "")
    # Inside the installer's Debian container ``HOME`` is /root, bridged to Termux's home
    # by --shared-home; the variable below is set by the installer for the same reason.
    value = env.get("NANOMUSE_TERMUX_HOME", "")
    if value:
        return Path(value)
    if home and "com.termux" in home:
        return Path(home)
    return TERMUX_HOME


def _toml(value: str | Path) -> str:
    """A TOML basic string; JSON's escaping is valid TOML for the paths used here."""
    return json.dumps(str(value), ensure_ascii=False)


def config_toml(
    data_dir: Path,
    workspace: Path,
    model: str,
    *,
    port: int = 8787,
    base_url: str = OLLAMA_BASE_URL,
    embedding_model: str = "nomic-embed-text",
) -> str:
    """The runtime's configuration for a phone that has only itself to talk to.

    The server listens on the loopback address, so no other device on the same Wi-Fi (or
    any other app) can open the app; the access token is still on.  The relay is not
    required, conversation sync is off, and the update check is off: nothing leaves the
    phone while it is offline.  Recall by meaning uses the small embedding model on the
    same Ollama, so the memory index also works with no network.
    """
    port = int(port)
    return f"""{MARKER}
# A phone-only install: the model runs beside this runtime, on 127.0.0.1.
# Generated by `nanomuse phone setup`; a hand-written config can be taken back from
# config.toml.before-phone next to this file.

data_dir = {_toml(data_dir)}
log_level = "INFO"

[llm]
provider = "openai"
model = {_toml(model)}
base_url = {_toml(base_url)}
api_key = "ollama"
tool_mode = "auto"
vision = "auto"
max_tokens = 2048
temperature = 0.6
timeout = 180
stream = true

[agent]
name = "nanoMuse"
workspace = {_toml(workspace)}
max_steps = 30

[server]
host = "127.0.0.1"
port = {port}
auth = true
update_check = false

[cloud]
required = false
models = false
sync = false

[hub]
enabled = false

[memory]
embeddings = "auto"
embedding_model = {_toml(embedding_model)}
embedding_base_url = {_toml(base_url)}
embedding_api_key = "ollama"

[sentinel]
mode = "ask"
always_ask_tools = ["send_email", "shell"]
"""


def app_settings(existing: dict | None, model: str, *, base_url: str = OLLAMA_BASE_URL) -> dict:
    """The app-managed settings that select the local model, merged over what is there.

    The web app layers ``<data_dir>/app-settings.json`` over ``config.toml`` so a phone can
    be set up without editing a file.  ``cloud_mode = "local"`` also keeps the sign-in door
    away: the phone works signed out, with the local model.  Unknown keys are kept as they
    are, because the same file holds the connectors and the rest of the app's choices.
    """
    data = dict(existing or {})
    llm = dict(data.get("llm") or {})
    llm.update(
        {
            "provider": "openai",
            "model": model,
            "base_url": base_url.rstrip("/"),
            "api_key": "ollama",
            "tool_mode": "auto",
            "vision": "auto",
        }
    )
    data["llm"] = llm
    data["cloud_mode"] = "local"
    cloud = dict(data.get("cloud") or {})
    cloud.update({"required": False, "models": False, "sync": False})
    data["cloud"] = cloud
    return data


def _write_private(path: Path, text: str) -> None:
    """Write a file whole, with owner-only permissions, and never leave a half file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.touch(mode=0o600, exist_ok=True)
    tmp.write_text(text, encoding="utf-8")
    try:
        from nanomuse.private import keep_private

        keep_private(tmp)
    except OSError:  # pragma: no cover - a platform without POSIX modes
        pass
    tmp.replace(path)


def write_config(path: Path, text: str) -> Path | None:
    """Write the phone configuration, keeping a hand-written one as ``*.before-phone``.

    Returns the backup path when one was made.  A file this module already wrote is
    replaced without a second backup.
    """
    backup: Path | None = None
    if path.is_file():
        old = path.read_text(encoding="utf-8", errors="replace")
        if old != text and not old.lstrip().startswith(MARKER):
            backup = path.with_name(path.name + ".before-phone")
            _write_private(backup, old)
    _write_private(path, text)
    return backup


def merge_app_settings(path: Path, model: str, *, base_url: str = OLLAMA_BASE_URL) -> dict:
    """Read ``app-settings.json``, add the local model, and write it back atomically."""
    from nanomuse.config import load_app_settings, save_app_settings

    data = load_app_settings(path.parent)
    if path.is_file():
        backup = path.with_name(path.name + ".before-phone")
        if not backup.exists():
            _write_private(backup, path.read_text(encoding="utf-8", errors="replace"))
    merged = app_settings(data, model, base_url=base_url)
    save_app_settings(path.parent, merged)
    return merged


def _q(value: str | Path | int) -> str:
    # The generated scripts run in Termux, where a path uses "/".  ``Path`` on Windows
    # renders "\", and the test run on that platform must see the same script a phone gets.
    return shlex.quote(str(value).replace("\\", "/"))


SERVICE_TEMPLATE = """#!/bin/bash
@MARKER@
# The phone's own switchboard: one command starts the model, the runtime and the wake lock.
# It is generated by `nanomuse phone setup`; change the configuration in
# ~/.nanomuse/config.toml, not in this file.

set -u

PREFIX=@PREFIX@
HOME_DIR=@HOME@
DISTRO=@DISTRO@
DATA="$HOME_DIR/.nanomuse"
RUN="$DATA/run"
LOGS="$DATA/logs"
MODEL=@MODEL@
PORT=@PORT@
MODEL_PORT=@MODEL_PORT@

mkdir -p "$RUN" "$LOGS"

say() { printf '%s\\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

model_up() { curl -fsS --max-time 2 "http://127.0.0.1:$MODEL_PORT/api/tags" >/dev/null 2>&1; }
web_up() { curl -fsS --max-time 3 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; }

wait_for() {
  _n="$1"; shift
  _i=0
  while [ "$_i" -lt "$_n" ]; do
    if "$@"; then return 0; fi
    sleep 1
    _i=$((_i + 1))
  done
  return 1
}

start_model() {
  if model_up; then return 0; fi
  if ! have ollama; then
    say "Ollama is not installed. Run the phone installer again."
    return 1
  fi
  say "Starting the model (Ollama)..."
  nohup ollama serve >>"$LOGS/ollama.log" 2>&1 &
  echo $! >"$RUN/ollama.pid"
  if wait_for 90 model_up; then return 0; fi
  say "The model did not start; see $LOGS/ollama.log"
  return 1
}

pull_model() {
  start_model || return 1
  if curl -fsS --max-time 3 "http://127.0.0.1:$MODEL_PORT/api/tags" 2>/dev/null | grep -Fq "\\"$MODEL\\""; then
    return 0
  fi
  say "Downloading $MODEL (once; this needs the internet)..."
  ollama pull "$MODEL"
}

guest() { proot-distro login "$DISTRO" --shared-home --shared-tmp -- bash -lc "$*"; }

serve_foreground() {
  guest "cd /opt/nanomuse && exec .venv/bin/nanomuse serve -c /root/.nanomuse/config.toml --host 127.0.0.1 --port $PORT"
}

supervise_serve() {
  while [ -f "$RUN/enabled" ]; do
    serve_foreground >>"$LOGS/serve.log" 2>&1
    sleep 5
  done
}

start_web() {
  if web_up; then return 0; fi
  : >"$RUN/enabled"
  if [ -f "$RUN/supervisor.pid" ] && kill -0 "$(cat "$RUN/supervisor.pid")" 2>/dev/null; then
    return 0
  fi
  say "Starting nanoMuse..."
  nohup "$0" _supervise >>"$LOGS/supervisor.log" 2>&1 &
  echo $! >"$RUN/supervisor.pid"
  if wait_for 90 web_up; then return 0; fi
  say "nanoMuse did not answer; see $LOGS/serve.log"
  return 1
}

stop_web() {
  rm -f "$RUN/enabled"
  if [ -f "$RUN/supervisor.pid" ]; then
    kill "$(cat "$RUN/supervisor.pid")" 2>/dev/null || true
    rm -f "$RUN/supervisor.pid"
  fi
  proot-distro kill "$DISTRO" >/dev/null 2>&1 || true
}

stop_model() {
  if [ -f "$RUN/ollama.pid" ]; then
    kill "$(cat "$RUN/ollama.pid")" 2>/dev/null || true
    rm -f "$RUN/ollama.pid"
  fi
}

up() {
  if have termux-wake-lock; then termux-wake-lock 2>/dev/null || true; fi
  start_model || return 1
  start_web || return 1
  say "nanoMuse is running at http://127.0.0.1:$PORT/"
}

down() {
  stop_web
  stop_model
  if have termux-wake-unlock; then termux-wake-unlock 2>/dev/null || true; fi
  say "nanoMuse is stopped."
}

status() {
  if model_up; then say "model:    up (http://127.0.0.1:$MODEL_PORT)"; else say "model:    down"; fi
  if web_up; then say "nanoMuse: up (http://127.0.0.1:$PORT)"; else say "nanoMuse: down"; fi
  if have termux-notification; then say "notify:   Termux:API"; fi
}

logs() { tail -n 40 "$LOGS/serve.log" "$LOGS/ollama.log" 2>/dev/null || true; }

open_app() {
  _token="$(cat "$DATA/server_token" 2>/dev/null || true)"
  _url="http://127.0.0.1:$PORT/"
  if have termux-open-url; then
    if [ -n "$_token" ]; then _url="$_url?token=$_token"; fi
    termux-open-url "$_url"
    return 0
  fi
  say "$_url"
  say "The access token is in $DATA/server_token; paste it on the page."
}

update() {
  guest "cd /opt/nanomuse && (git pull --ff-only || true) && .venv/bin/pip install -e ."
  down
  up
}

model_set() {
  _model="$1"
  case "$_model" in
    *[!A-Za-z0-9._:/-]*|"") say "usage: nanomuse-phone model NAME"; return 2 ;;
  esac
  MODEL="$_model"
  pull_model || return 1
  guest "cd /opt/nanomuse && .venv/bin/nanomuse phone setup --model $(printf '%q' "$MODEL") --port $PORT --service --force"
  down
  up
}

restore() {
  _file="$1"
  [ -f "$_file" ] || { say "no such file: $_file"; return 1; }
  _name="$(basename "$_file")"
  cp "$_file" "$HOME_DIR/$_name"
  down
  guest "cd /opt/nanomuse && .venv/bin/nanomuse phone restore /root/$_name"
  up
}

usage() {
  say "nanoMuse on this phone"
  say "  nanomuse-phone up        start the model and the app"
  say "  nanomuse-phone down      stop both"
  say "  nanomuse-phone status    what is running"
  say "  nanomuse-phone open      open the app in the browser"
  say "  nanomuse-phone logs      the last lines of both logs"
  say "  nanomuse-phone update    update the runtime and restart"
  say "  nanomuse-phone model NAME  download another Ollama model and use it"
  say "  nanomuse-phone restore FILE  restore a nanoMuse data export"
}

case "${1:-}" in
  up) up ;;
  down) down ;;
  restart) down; up ;;
  status) status ;;
  open) open_app ;;
  logs) logs ;;
  update) update ;;
  model) shift; model_set "${1:-}" ;;
  restore) shift; restore "${1:-}" ;;
  _supervise) supervise_serve ;;
  *) usage ;;
esac
"""


def service_script(
    prefix: Path,
    home: Path,
    *,
    distro: str = "debian",
    model: str = DEFAULT_MODEL,
    port: int = 8787,
    model_port: int = 11434,
) -> str:
    """The ``nanomuse-phone`` command, run from Termux.

    It owns both processes: Ollama serves the model, ``proot-distro`` runs the Python
    runtime in its Debian container, and a small supervisor starts the runtime again if it
    exits.  The wake lock is Termux's; without Termux:API or Termux:Boot the commands are
    simply skipped.
    """
    if not distro or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for ch in distro):
        raise ValueError("the container name may contain only letters, digits, '.', '_' and '-'")
    return (
        SERVICE_TEMPLATE.replace("@MARKER@", MARKER + " (service)")
        .replace("@PREFIX@", _q(prefix))
        .replace("@HOME@", _q(home))
        .replace("@DISTRO@", _q(distro))
        .replace("@MODEL@", _q(model))
        .replace("@PORT@", _q(port))
        .replace("@MODEL_PORT@", _q(model_port))
    )


BOOT_TEMPLATE = """#!/bin/bash
@MARKER@
# Termux:Boot runs this when the phone starts.  Install Termux:Boot, keep Termux out of
# battery optimisation, and the agent comes back without anyone opening the app.
@PREFIX@/bin/nanomuse-phone up >>"@HOME@/.nanomuse/logs/boot.log" 2>&1
"""


def boot_script(prefix: Path, home: Path) -> str:
    """The Termux:Boot script that brings the phone's nanoMuse back after a restart."""
    return (
        BOOT_TEMPLATE.replace("@MARKER@", MARKER + " (boot)")
        .replace("@PREFIX@", _q(prefix))
        .replace("@HOME@", _q(home))
    )


def install_service(
    prefix: Path,
    home: Path,
    *,
    distro: str = "debian",
    model: str = DEFAULT_MODEL,
    port: int = 8787,
    boot: bool = True,
) -> list[Path]:
    """Write the control command and, unless asked not to, the boot script.

    Returns the files written.  Both are chmod 755: Termux and Termux:Boot execute them.
    """
    written: list[Path] = []
    control = prefix / "bin" / "nanomuse-phone"
    control.parent.mkdir(parents=True, exist_ok=True)
    control.write_text(
        service_script(prefix, home, distro=distro, model=model, port=port), encoding="utf-8"
    )
    control.chmod(0o755)
    written.append(control)
    if boot:
        script = home / ".termux" / "boot" / "nanomuse.sh"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(boot_script(prefix, home), encoding="utf-8")
        script.chmod(0o755)
        written.append(script)
    return written


def restore_export(archive: str | Path, data_dir: Path) -> int:
    """Restore a zip written by Settings, Download your agent data; return the file count.

    Only the names the export itself writes are accepted.  That keeps a downloaded archive
    from replacing ``config.toml``, the vault or the server token, and a hand-made zip from
    writing outside the data directory.  The archive is expected to be the one
    ``nanomuse.server.data_export`` makes; other zips are refused member by member.
    """
    from nanomuse.server.data_export import DIRS, FILES

    allowed_files = set(FILES)
    allowed_dirs = set(DIRS)
    root = Path(data_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            name = info.filename.replace("\\", "/").lstrip("/")
            if not name or name.endswith("/"):
                continue
            parts = [p for p in name.split("/") if p not in ("", ".")]
            if not parts or ".." in parts:
                continue
            if parts[0] == "README.txt" and len(parts) == 1:
                continue
            if parts[0] not in allowed_files and parts[0] not in allowed_dirs:
                continue
            if stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF):
                continue
            target = root.joinpath(*parts)
            try:
                target.resolve().relative_to(root)
            except ValueError:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as source, target.open("wb") as sink:
                while chunk := source.read(1024 * 1024):
                    sink.write(chunk)
            count += 1
    return count


#: Directories a package skips: caches, logs and a checkout's own history.
PACKAGE_EXCLUDE_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "__pycache__",
        "node_modules",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        "logs",
        "backups",
    }
)


def _package_tree(tar: tarfile.TarFile, src: Path, arcroot: str, extra: frozenset[str]) -> int:
    count = 0
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = sorted(
            d for d in dirnames if d not in PACKAGE_EXCLUDE_DIRS and d not in extra
        )
        for name in filenames:
            if name.endswith(".pyc"):
                continue
            full = Path(dirpath) / name
            tar.add(full, arcname=f"{arcroot}/{full.relative_to(src).as_posix()}", recursive=False)
            count += 1
    return count


def package_install(root: str | Path, out: str | Path) -> dict[str, Any]:
    """Bundle this install (the checkout, the data and the workspace) into one file.

    ``scripts/phone/install.sh --bundle FILE`` unpacks it on the phone, so the phone gets
    the exact checkout, the chats, memory, goals and the workspace files, not a fresh
    install.  Caches and logs are left out, and the data directory's own ``workspace``
    (an older default) is skipped because the install's workspace lives beside ``data``.
    """
    root = Path(root).resolve()
    repo = root / "repo"
    if not (repo / "pyproject.toml").is_file():
        raise ValueError(f"{repo} is not the nanoMuse checkout (it has no pyproject.toml)")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with tarfile.open(out, "w:gz") as tar:
        for src, arc, extra in (
            (repo, "repo", frozenset()),
            (root / "data", "data", frozenset({"workspace"})),
            (root / "workspace", "workspace", frozenset()),
        ):
            if src.is_dir():
                total += _package_tree(tar, src, arc, extra)
        config = root / "config" / "config.toml"
        if config.is_file():
            tar.add(config, arcname="config.toml", recursive=False)
        blob = json.dumps(
            {
                "created": datetime.now(UTC).isoformat(timespec="seconds"),
                "files": total,
                "note": "Unpack on the phone with `scripts/phone/install.sh --bundle FILE`.",
            },
            indent=1,
        ).encode("utf-8")
        info = tarfile.TarInfo("manifest.json")
        info.size = len(blob)
        tar.addfile(info, io.BytesIO(blob))
    return {"out": str(out), "files": total, "bytes": out.stat().st_size}


__all__ = [
    "DEFAULT_MODEL",
    "MARKER",
    "MODELS",
    "OLLAMA_BASE_URL",
    "TERMUX_HOME",
    "TERMUX_PREFIX",
    "ModelChoice",
    "app_settings",
    "boot_script",
    "choose_model",
    "config_toml",
    "install_service",
    "is_termux",
    "merge_app_settings",
    "package_install",
    "read_ram_gb",
    "restore_export",
    "service_script",
    "termux_home",
    "termux_prefix",
    "write_config",
]
