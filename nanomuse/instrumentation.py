"""Performance instrumentation and hardware diagnostics for nanoMuse.

Tracks every task with a unique trace ID, measuring:
- Time to first token (TTFT)
- Tokens per second
- Total response time
- Prompt and completion tokens
- Context size
- Model calls & tool calls per task
- Retry counts, errors, timeouts
- CPU, RAM and GPU VRAM consumption
"""

from __future__ import annotations

import ctypes
import json
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nanomuse.logger import logger
from nanomuse.win_proc import hidden_subprocess_kwargs

DATA_DIR = Path.home() / ".nanomuse"
TRACES_PATH = DATA_DIR / "traces.jsonl"


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


# Hardware stats are decoration on a trace: they must never be able to hold up a turn.
# `nvidia-smi` is a real process launch, and Windows `subprocess` can block on the pipe
# join far past its own `timeout` when several launches overlap (a turn starts and
# finishes traces while other turns do the same), which froze whole turns on machines
# with a working GPU. So the GPU probe runs at most once every few seconds, in a
# background thread, and a slow or missing `nvidia-smi` only ever costs a stale number.
_GPU_TTL_S = 5.0
_GPU_PROBE_TIMEOUT_S = 2.0
_gpu_lock = threading.Lock()
_gpu_cache: dict[str, int] = {"vram_used_mb": 0, "vram_total_mb": 0, "gpu_util_pct": 0}
_gpu_probed_at: float = 0.0
_gpu_probe_running = False
_NVIDIA_SMI: str | None = None
_NVIDIA_SMI_CHECKED = False


def _nvidia_smi_bin() -> str | None:
    global _NVIDIA_SMI, _NVIDIA_SMI_CHECKED
    if not _NVIDIA_SMI_CHECKED:
        _NVIDIA_SMI = shutil.which("nvidia-smi")
        _NVIDIA_SMI_CHECKED = True
    return _NVIDIA_SMI


def _probe_gpu() -> None:
    """One `nvidia-smi` read into the cache. Runs in its own thread, never raises."""
    global _gpu_probed_at, _gpu_probe_running
    try:
        smi_bin = _nvidia_smi_bin()
        if not smi_bin:
            return
        smi = subprocess.check_output(
            [
                smi_bin,
                "--query-gpu=memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=_GPU_PROBE_TIMEOUT_S,
            stderr=subprocess.DEVNULL,
            **hidden_subprocess_kwargs(),
        ).strip()
        parts = [p.strip() for p in smi.split(",")]
        if len(parts) >= 3:
            with _gpu_lock:
                _gpu_cache["vram_used_mb"] = int(parts[0])
                _gpu_cache["vram_total_mb"] = int(parts[1])
                _gpu_cache["gpu_util_pct"] = int(parts[2])
    except Exception as e:
        logger.debug(f"Hardware stats GPU error: {e}")
    finally:
        with _gpu_lock:
            _gpu_probed_at = time.monotonic()
            _gpu_probe_running = False


def get_hardware_stats() -> dict[str, Any]:
    """RAM now, GPU from the cache. Never blocks: a stale GPU number beats a stuck turn."""
    global _gpu_probe_running
    stats: dict[str, Any] = {
        "ram_used_gb": 0.0,
        "ram_total_gb": 0.0,
        "ram_load_pct": 0,
        "vram_used_mb": 0,
        "vram_total_mb": 0,
        "gpu_util_pct": 0,
    }
    # RAM: a cheap in-process call, always fresh.
    try:
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            stats["ram_used_gb"] = round((stat.ullTotalPhys - stat.ullAvailPhys) / (1024**3), 2)
            stats["ram_total_gb"] = round(stat.ullTotalPhys / (1024**3), 2)
            stats["ram_load_pct"] = int(stat.dwMemoryLoad)
    except Exception as e:
        logger.debug(f"Hardware stats RAM error: {e}")

    # GPU: kick off at most one probe at a time, and only when the cache is old.
    with _gpu_lock:
        stale = time.monotonic() - _gpu_probed_at > _GPU_TTL_S
        start = stale and not _gpu_probe_running
        if start:
            _gpu_probe_running = True
        cached = dict(_gpu_cache)
    if start:
        threading.Thread(target=_probe_gpu, name="gpu-probe", daemon=True).start()
    stats.update(cached)
    return stats


class TaskTrace:
    def __init__(self, trace_id: str, prompt: str, conversation_id: str = ""):
        self.trace_id = trace_id
        self.prompt = prompt
        self.conversation_id = conversation_id
        self.start_time = time.perf_counter()
        self.start_iso = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        self.end_time: float | None = None
        self.end_iso: str | None = None
        self.duration_s: float = 0.0

        self.ttft_s: float | None = None
        self.tokens_per_sec: float = 0.0
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.total_tokens: int = 0
        self.context_tokens: int = 0
        self.cache_tokens: int = 0
        self.reasoning_tokens: int = 0

        self.model_calls: int = 0
        self.tool_calls: int = 0
        self.tool_names: list[str] = []
        self.retry_count: int = 0
        self.errors: list[str] = []
        self.status: str = "running"
        self.hardware = get_hardware_stats()

    def record_first_token(self) -> None:
        if self.ttft_s is None:
            self.ttft_s = round(time.perf_counter() - self.start_time, 3)

    def record_llm_step(self, usage: dict[str, Any]) -> None:
        self.model_calls += 1
        p_tok = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        c_tok = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        self.prompt_tokens += p_tok
        self.completion_tokens += c_tok
        self.total_tokens += p_tok + c_tok
        self.context_tokens = max(self.context_tokens, p_tok)
        # The served prefix the endpoint billed at the cache rate: DeepSeek reports it
        # flat, OpenAI nests it under prompt_tokens_details.
        details = usage.get("prompt_tokens_details") or {}
        self.cache_tokens += int(
            usage.get("prompt_cache_hit_tokens") or details.get("cached_tokens") or 0
        )
        # The thinking a reasoning model billed inside the output (OpenAI-style detail;
        # absent on endpoints that fold reasoning into the plain completion count).
        out_details = usage.get("completion_tokens_details") or {}
        self.reasoning_tokens += int(out_details.get("reasoning_tokens") or 0)

    def record_tool_call(self, tool_name: str) -> None:
        self.tool_calls += 1
        self.tool_names.append(tool_name)

    def complete(self, status: str = "success", error: str | None = None) -> dict[str, Any]:
        self.end_time = time.perf_counter()
        self.end_iso = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        self.duration_s = round(self.end_time - self.start_time, 3)
        self.status = status
        if error:
            self.errors.append(error)

        gen_time = self.duration_s - (self.ttft_s or 0.0)
        if gen_time > 0 and self.completion_tokens > 0:
            self.tokens_per_sec = round(self.completion_tokens / gen_time, 2)
        elif self.duration_s > 0 and self.completion_tokens > 0:
            self.tokens_per_sec = round(self.completion_tokens / self.duration_s, 2)

        self.hardware = get_hardware_stats()
        return self.to_dict()

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "conversation_id": self.conversation_id,
            "prompt": self.prompt[:200] if len(self.prompt) > 200 else self.prompt,
            "status": self.status,
            "start_iso": self.start_iso,
            "end_iso": self.end_iso,
            "duration_s": self.duration_s,
            "ttft_s": self.ttft_s,
            "tokens_per_sec": self.tokens_per_sec,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "context_tokens": self.context_tokens,
            "cache_tokens": self.cache_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "tool_names": self.tool_names,
            "retry_count": self.retry_count,
            "errors": self.errors,
            "hardware": self.hardware,
        }


class InstrumentationRegistry:
    _instance: InstrumentationRegistry | None = None

    def __init__(self, max_history: int = 100):
        self.max_history = max_history
        self._traces: deque[dict[str, Any]] = deque(maxlen=max_history)
        self._active: dict[str, TaskTrace] = {}
        self._lock = threading.Lock()

    @classmethod
    def get(cls) -> InstrumentationRegistry:
        if cls._instance is None:
            cls._instance = InstrumentationRegistry()
        return cls._instance

    @staticmethod
    def configure(data_dir: Path | str) -> None:
        """Point trace storage at the configured data directory (the module default
        points at the standard install location)."""
        global TRACES_PATH  # noqa: PLW0603
        TRACES_PATH = Path(data_dir) / "traces.jsonl"

    def start_trace(self, prompt: str, conversation_id: str = "") -> TaskTrace:
        trace_id = f"trace-{uuid.uuid4().hex[:12]}"
        trace = TaskTrace(trace_id, prompt, conversation_id)
        with self._lock:
            self._active[trace_id] = trace
        return trace

    def finish_trace(
        self, trace: TaskTrace, status: str = "success", error: str | None = None
    ) -> dict[str, Any]:
        summary = trace.complete(status=status, error=error)
        with self._lock:
            self._active.pop(trace.trace_id, None)
            self._traces.append(summary)

        # Append to traces.jsonl
        try:
            TRACES_PATH.parent.mkdir(parents=True, exist_ok=True)
            with TRACES_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(summary, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"Failed to append to traces.jsonl: {e}")

        return summary

    def list_traces(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._traces)[-limit:]

    def get_summary(self) -> dict[str, Any]:
        with self._lock:
            recent = list(self._traces)[-30:]
        if not recent:
            return {"count": 0, "hardware": get_hardware_stats()}
        durations = [t["duration_s"] for t in recent if t.get("duration_s")]
        ttfts = [t["ttft_s"] for t in recent if t.get("ttft_s")]
        tps = [t["tokens_per_sec"] for t in recent if t.get("tokens_per_sec")]
        return {
            "count": len(recent),
            "avg_duration_s": round(sum(durations) / len(durations), 2) if durations else 0,
            "min_duration_s": min(durations) if durations else 0,
            "max_duration_s": max(durations) if durations else 0,
            "avg_ttft_s": round(sum(ttfts) / len(ttfts), 2) if ttfts else 0,
            "avg_tokens_per_sec": round(sum(tps) / len(tps), 2) if tps else 0,
            "hardware": get_hardware_stats(),
        }
