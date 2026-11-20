"""Log & Diagnostics Exporter for nanoMuse.

Bundles all server logs (nanomuse.log, server_stdout.log, watchdog.log, ollama_serve.log)
along with an intelligent ERRORS_SUMMARY.txt containing recent traces and system telemetry.
"""

from __future__ import annotations

import io
import os
import platform
import re
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from nanomuse.instrumentation import get_hardware_stats

DATA_DIR = Path(os.environ.get("NANOMUSE_DATA_DIR", str(Path.home() / ".nanomuse")))
LOGS_DIR = DATA_DIR / "logs"


def _linux_proc_ram() -> tuple[float, float, int]:
    """Read RAM from /proc/meminfo when psutil/telemetry returns 0 (e.g. Android PRoot)."""
    try:
        meminfo = Path("/proc/meminfo")
        if not meminfo.is_file():
            return 0.0, 0.0, 0
        vals: dict[str, int] = {}
        for line in meminfo.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = line.split(":")
            if len(parts) == 2:
                num = parts[1].strip().split()[0]
                if num.isdigit():
                    vals[parts[0].strip()] = int(num)
        total_kb = vals.get("MemTotal", 0)
        avail_kb = vals.get("MemAvailable", vals.get("MemFree", 0))
        if total_kb > 0:
            used_kb = max(0, total_kb - avail_kb)
            total_gb = total_kb / (1024 * 1024)
            used_gb = used_kb / (1024 * 1024)
            pct = int(round((used_kb / total_kb) * 100))
            return used_gb, total_gb, pct
    except OSError:
        pass
    return 0.0, 0.0, 0


def _resolve_logs_dir(data_dir: Path | None = None) -> Path:
    if data_dir is not None:
        return Path(data_dir) / "logs"
    env_dir = os.environ.get("NANOMUSE_DATA_DIR", "").strip()
    if env_dir:
        return Path(env_dir) / "logs"
    return LOGS_DIR


def get_error_summary(limit: int = 50, data_dir: Path | None = None) -> str:
    """Extracts recent errors and stack traces from nanomuse.log."""
    log_file = _resolve_logs_dir(data_dir) / "nanomuse.log"
    out = [
        "=================================================================",
        "                    NANOMUSE ERROR & DIAGNOSTIC REPORT            ",
        f"  Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}                  ",
        f"  Platform:  {platform.platform()} ({platform.machine()})   ",
        f"  Python:    {sys.version.split()[0]}                                     ",
        "=================================================================\n",
    ]

    # Telemetry
    try:
        hw = get_hardware_stats()
        ram_used = float(hw.get("ram_used_gb") or 0)
        ram_total = float(hw.get("ram_total_gb") or 0)
        ram_pct = int(hw.get("ram_percent") or 0)
        if ram_total <= 0.0:
            ram_used, ram_total, ram_pct = _linux_proc_ram()
        gpu_name = str(hw.get("gpu_name") or "").strip() or "None / Integrated"
        out.append("--- LIVE SYSTEM TELEMETRY ---")
        out.append(f"RAM Usage:     {ram_used:.2f} GB / {ram_total:.2f} GB ({ram_pct}%)")
        out.append(f"VRAM Usage:    {hw.get('gpu_vram_used_gb', 0):.2f} GB / {hw.get('gpu_vram_total_gb', 0):.2f} GB")
        out.append(f"GPU Util:      {hw.get('gpu_util_percent', 0)}%")
        out.append(f"GPU Name:      {gpu_name}")
        out.append("-----------------------------\n")
    except Exception as exc:
        out.append(f"Telemetry unavailable: {exc}\n")

    out.append("--- RECENT CRITICAL & ERROR LOGS (LAST 50) ---")
    if not log_file.is_file():
        out.append("No nanomuse.log found.")
        return "\n".join(out)

    try:
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as exc:
        out.append(f"Error reading log file: {exc}")
        return "\n".join(out)

    error_blocks: list[str] = []
    current_block: list[str] = []
    in_traceback = False

    for line in lines:
        is_error = " | ERROR    |" in line or " | CRITICAL |" in line
        if is_error or "Traceback (most recent call last):" in line:
            in_traceback = True
            if current_block:
                error_blocks.append("\n".join(current_block))
                current_block = []
            current_block.append(line)
        elif in_traceback:
            # Continue gathering traceback or end if new timestamped non-error log line
            if re.match(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}", line) and not is_error:
                in_traceback = False
                error_blocks.append("\n".join(current_block))
                current_block = []
            else:
                current_block.append(line)

    if current_block:
        error_blocks.append("\n".join(current_block))

    recent = error_blocks[-limit:] if len(error_blocks) > limit else error_blocks
    if not recent:
        out.append("No errors found in nanomuse.log! System is healthy.")
    else:
        for i, block in enumerate(recent, 1):
            out.append(f"[{i}/{len(recent)}]")
            out.append(block)
            out.append("-" * 65)

    return "\n".join(out)


def build_logs_zip(data_dir: Path | None = None) -> bytes:
    """Builds an in-memory zip archive containing all logs and the error summary."""
    logs_dir = _resolve_logs_dir(data_dir)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # 1. Summary
        summary = get_error_summary(data_dir=data_dir)
        zf.writestr("ERRORS_SUMMARY.txt", summary.encode("utf-8"))

        # 2. Add log files from logs_dir
        if logs_dir.is_dir():
            for f in logs_dir.glob("*.log"):
                try:
                    # Take up to last 10 MB per log file
                    raw = f.read_bytes()
                    if len(raw) > 10 * 1024 * 1024:
                        raw = raw[-(10 * 1024 * 1024):]
                    zf.writestr(f"logs/{f.name}", raw)
                except Exception as exc:
                    zf.writestr(f"logs/{f.name}.err.txt", str(exc).encode("utf-8"))

    return buf.getvalue()


def logs_filename() -> str:
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    return f"nanomuse-logs-{ts}.zip"
