"""Device hardware & local AI runtime capability advisor.

Inspects host RAM, CPU cores, architecture, OS/mobile environment, GPU VRAM (if
available silently), and locally reachable inference servers (Ollama, LM Studio,
vLLM) so onboarding and settings can give honest advice on what local models fit
and whether local AI is worth the tradeoff compared to cheap cloud models.
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

import httpx

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True, slots=True)
class LocalModelOption:
    id: str
    name: str
    params: str
    size_gb: float
    min_ram_gb: int
    tier: str
    summary: str
    tool_quality: str
    recommended_for: str

    def to_dict(self, *, total_ram_gb: float, vram_gb: float, installed: set[str]) -> dict[str, Any]:
        usable_mem = max(total_ram_gb, vram_gb * 1.35)
        if usable_mem >= self.min_ram_gb:
            fit = "fits"
        elif usable_mem >= max(2.5, self.min_ram_gb - 2):
            fit = "tight"
        else:
            fit = "too_heavy"
        base_id = self.id.split(":", 1)[0]
        is_installed = any(m == self.id or m.startswith(self.id + ":") or m.split(":", 1)[0] == base_id for m in installed)
        return {
            "id": self.id,
            "name": self.name,
            "params": self.params,
            "size_gb": self.size_gb,
            "min_ram_gb": self.min_ram_gb,
            "tier": self.tier,
            "summary": self.summary,
            "tool_quality": self.tool_quality,
            "recommended_for": self.recommended_for,
            "fit": fit,
            "installed": is_installed,
            "preset": "ollama",
            "pull_cmd": f"ollama pull {self.id}",
        }


LOCAL_MODEL_CATALOG: tuple[LocalModelOption, ...] = (
    LocalModelOption(
        id="qwen3:8b",
        name="Qwen3 8B",
        params="8B",
        size_gb=5.2,
        min_ram_gb=12,
        tier="sweet_spot",
        summary="Best everyday 8B agent model for tool calling, coding, and multilingual chat.",
        tool_quality="Strong",
        recommended_for="12 GB+ laptops & desktops",
    ),
    LocalModelOption(
        id="qwen3:4b",
        name="Qwen3 4B",
        params="4B",
        size_gb=2.6,
        min_ram_gb=6,
        tier="lightweight",
        summary="Compact reasoning & tool-use model that balances speed and capability on 6-8 GB RAM.",
        tool_quality="Good",
        recommended_for="6-8 GB laptops & high-end phones (offline only)",
    ),
    LocalModelOption(
        id="llama3.2:3b",
        name="Llama 3.2 3B",
        params="3B",
        size_gb=2.0,
        min_ram_gb=6,
        tier="lightweight",
        summary="Fast Meta 3B assistant for quick offline Q&A, summaries, and lightweight tasks.",
        tool_quality="Basic",
        recommended_for="6-8 GB devices & fast CPU inference",
    ),
    LocalModelOption(
        id="gemma3:4b",
        name="Gemma 3 4B",
        params="4B",
        size_gb=3.3,
        min_ram_gb=8,
        tier="lightweight",
        summary="Clean vision-capable small Google model for local screenshots and everyday help.",
        tool_quality="Basic",
        recommended_for="8 GB+ RAM when you want small local vision",
    ),
    LocalModelOption(
        id="deepseek-r1:8b",
        name="DeepSeek R1 8B",
        params="8B",
        size_gb=4.9,
        min_ram_gb=12,
        tier="sweet_spot",
        summary="Distilled reasoning specialist for math, debugging, and careful step-by-step analysis.",
        tool_quality="Moderate",
        recommended_for="12 GB+ computers & coding assistance",
    ),
    LocalModelOption(
        id="llama3.1:8b",
        name="Llama 3.1 8B",
        params="8B",
        size_gb=4.7,
        min_ram_gb=12,
        tier="sweet_spot",
        summary="Reliable general-purpose 8B model with broad compatibility across Ollama and LM Studio.",
        tool_quality="Good",
        recommended_for="12 GB+ laptops & desktops",
    ),
    LocalModelOption(
        id="qwen3:14b",
        name="Qwen3 14B",
        params="14B",
        size_gb=9.3,
        min_ram_gb=20,
        tier="workstation",
        summary="Noticeably smarter multi-step agent planning and complex tool automation.",
        tool_quality="Very Strong",
        recommended_for="20 GB+ RAM or 12 GB+ VRAM GPUs",
    ),
    LocalModelOption(
        id="qwen3:32b",
        name="Qwen3 32B",
        params="32B",
        size_gb=20.0,
        min_ram_gb=32,
        tier="workstation",
        summary="Near-cloud intelligence locally on high-RAM Mac Silicon or 24 GB GPU workstations.",
        tool_quality="Excellent",
        recommended_for="32 GB+ unified RAM / workstation GPUs",
    ),
    LocalModelOption(
        id="phi4-mini",
        name="Phi-4 Mini (3.8B)",
        params="3.8B",
        size_gb=2.5,
        min_ram_gb=6,
        tier="lightweight",
        summary="Efficient Microsoft compact model tuned for logic and concise instructions.",
        tool_quality="Basic",
        recommended_for="6-8 GB RAM devices",
    ),
    LocalModelOption(
        id="qwen3:1.7b",
        name="Qwen3 1.7B",
        params="1.7B",
        size_gb=1.4,
        min_ram_gb=4,
        tier="ultra_tiny",
        summary="Ultra-small offline fallback for weak devices when privacy/offline matters more than quality.",
        tool_quality="Weak",
        recommended_for="4 GB RAM / phones (only if 100% offline required)",
    ),
    LocalModelOption(
        id="llama3.2:1b",
        name="Llama 3.2 1B",
        params="1B",
        size_gb=1.3,
        min_ram_gb=3,
        tier="ultra_tiny",
        summary="Bare-minimum 1B chat model for low-RAM devices; expect limited accuracy and weak tool use.",
        tool_quality="Very Weak",
        recommended_for="3-4 GB weak devices (strictly offline fallback)",
    ),
)


def _read_ram_gb() -> tuple[float, float]:
    """Return ``(total_ram_gb, free_ram_gb)`` using stdlib OS primitives without spawning a window."""
    try:
        import psutil  # type: ignore[import-not-found]

        vm = psutil.virtual_memory()
        gb = 1024**3
        return round(vm.total / gb, 1), round(vm.available / gb, 1)
    except Exception:
        pass

    sys_name = platform.system()
    gb = 1024**3
    if sys_name == "Windows":
        try:
            class _MemStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MemStatusEx()
            stat.dwLength = ctypes.sizeof(_MemStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):  # type: ignore[attr-defined]
                return round(stat.ullTotalPhys / gb, 1), round(stat.ullAvailPhys / gb, 1)
        except Exception:
            pass
    if os.path.exists("/proc/meminfo"):
        try:
            total_kb = 0
            avail_kb = 0
            with open("/proc/meminfo", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        total_kb = int(line.split()[1])
                    elif line.startswith("MemAvailable:"):
                        avail_kb = int(line.split()[1])
                    elif line.startswith("MemFree:") and avail_kb == 0:
                        avail_kb = int(line.split()[1])
            if total_kb > 0:
                return round(total_kb / (1024**2), 1), round(avail_kb / (1024**2), 1)
        except Exception:
            pass
    try:
        if hasattr(os, "sysconf"):
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            total = (pages * page_size) / gb
            return round(total, 1), round(total * 0.5, 1)
    except Exception:
        pass
    return 0.0, 0.0


def _detect_android() -> bool:
    if os.environ.get("ANDROID_ROOT") or os.environ.get("ANDROID_DATA") or os.environ.get("NANOMUSE_ANDROID"):
        return True
    prefix = os.environ.get("PREFIX", "")
    home = os.environ.get("HOME", "")
    if "/data/user/" in prefix or "/data/data/" in home or os.path.exists("/system/build.prop"):
        return True
    return False


def _detect_nvidia_gpu() -> tuple[str, float]:
    """Silently query nvidia-smi if present; never flashes a console window on Windows."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return "", 0.0
    try:
        res = subprocess.run(
            [exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=1.5,
            creationflags=_CREATE_NO_WINDOW if os.name == "nt" else 0,
            check=False,
        )
        if res.returncode != 0:
            return "", 0.0
        lines = [ln.strip() for ln in (res.stdout or "").splitlines() if ln.strip()]
        if not lines:
            return "", 0.0
        parts = [p.strip() for p in lines[0].split(",")]
        name = parts[0]
        vram_mb = float(parts[1]) if len(parts) > 1 and parts[1].replace(".", "", 1).isdigit() else 0.0
        return name, round(vram_mb / 1024.0, 1)
    except Exception:
        return "", 0.0


def collect_host_specs() -> dict[str, Any]:
    """Gather synchronous host hardware specs quickly and safely."""
    total_ram_gb, free_ram_gb = _read_ram_gb()
    sys_name = platform.system() or "Unknown"
    arch = (platform.machine() or "").lower()
    cores = os.cpu_count() or 1
    is_android = _detect_android()
    apple_silicon = sys_name == "Darwin" and arch in ("arm64", "aarch64")
    gpu_name, vram_gb = _detect_nvidia_gpu()
    if apple_silicon and not gpu_name and total_ram_gb > 0:
        gpu_name = f"Apple Silicon Unified Memory ({total_ram_gb:.0f} GB)"
        vram_gb = round(total_ram_gb * 0.7, 1)
    is_mobile = is_android or (sys_name == "Linux" and arch in ("aarch64", "arm64") and cores <= 8 and total_ram_gb <= 12 and not gpu_name)
    return {
        "os": "Android" if is_android else sys_name,
        "arch": arch,
        "cpu_cores": cores,
        "total_ram_gb": total_ram_gb,
        "free_ram_gb": free_ram_gb,
        "is_android": is_android,
        "is_mobile": is_mobile,
        "apple_silicon": apple_silicon,
        "gpu_name": gpu_name,
        "vram_gb": vram_gb,
    }


async def _probe_runtime(
    client: httpx.AsyncClient,
    *,
    runtime_id: str,
    name: str,
    base_url: str,
    probe_url: str,
    install_url: str,
    key_name: str = "data",
) -> dict[str, Any]:
    running = False
    models: list[str] = []
    try:
        r = await client.get(probe_url)
        if r.status_code < 400:
            running = True
            payload = r.json()
            rows = payload.get(key_name) if isinstance(payload, dict) else []
            for row in rows or []:
                if isinstance(row, dict):
                    mid = row.get("name") or row.get("model") or row.get("id")
                    if mid:
                        models.append(str(mid))
                elif row:
                    models.append(str(row))
    except Exception:
        pass
    return {
        "id": runtime_id,
        "name": name,
        "base_url": base_url,
        "running": running,
        "models": models,
        "install_url": install_url,
    }


async def probe_local_runtimes() -> list[dict[str, Any]]:
    """Probe Ollama, LM Studio, and local vLLM on loopback without hitting external proxies."""
    async with httpx.AsyncClient(timeout=0.55, trust_env=False) as client:
        return list(
            await asyncio.gather(
                _probe_runtime(
                    client,
                    runtime_id="ollama",
                    name="Ollama",
                    base_url="http://127.0.0.1:11434/v1",
                    probe_url="http://127.0.0.1:11434/api/tags",
                    install_url="https://ollama.com/download",
                    key_name="models",
                ),
                _probe_runtime(
                    client,
                    runtime_id="lm_studio",
                    name="LM Studio",
                    base_url="http://127.0.0.1:1234/v1",
                    probe_url="http://127.0.0.1:1234/v1/models",
                    install_url="https://lmstudio.ai/",
                    key_name="data",
                ),
                _probe_runtime(
                    client,
                    runtime_id="vllm",
                    name="vLLM / Local Server",
                    base_url="http://127.0.0.1:8000/v1",
                    probe_url="http://127.0.0.1:8000/v1/models",
                    install_url="https://docs.vllm.ai/",
                    key_name="data",
                ),
            )
        )


def assess_local_ai(
    host: dict[str, Any],
    runtimes: list[dict[str, Any]],
    *,
    client_ram_gb: float | None = None,
    client_cores: int | None = None,
    client_mobile: bool | None = None,
) -> dict[str, Any]:
    """Evaluate local AI feasibility and honestly explain whether local models are worth it."""
    total_ram = float(host.get("total_ram_gb") or 0.0)
    vram = float(host.get("vram_gb") or 0.0)
    is_mobile_host = bool(host.get("is_mobile") or host.get("is_android"))
    apple_silicon = bool(host.get("apple_silicon"))

    installed_models: set[str] = set()
    for rt in runtimes:
        for m in rt.get("models") or []:
            installed_models.add(str(m))

    remote_client_mismatch = bool(client_mobile and not is_mobile_host and total_ram >= 8.0)

    if is_mobile_host or (total_ram > 0 and total_ram < 6.0 and vram < 4.0):
        tier = "weak_device"
        worth_it = "cloud_recommended"
        headline = "Local AI is tough on this device: a cheap cloud model is far better here"
        explanation = (
            f"Detected {host.get('os')} with {total_ram or 'limited'} GB RAM"
            f"{' and mobile CPU' if is_mobile_host else ''}. At most, tiny 1B-3B quantized models "
            "can squeeze into memory, which respond slowly, warm up your battery, and often stumble on multi-step tool calls."
        )
        tradeoff = (
            "Recommended unless you strictly need ANY 100% offline local AI over speed and intelligence: "
            "OpenRouter (with DeepSeek Flash, Gemini Flash, or GPT-5 Mini) costs fractions of a cent per chat and even offers free models."
        )
    elif (vram >= 12.0) or (apple_silicon and total_ram >= 24.0) or (total_ram >= 28.0):
        tier = "workstation"
        worth_it = "worth_it"
        headline = "Strong hardware: 8B-32B local models run great on this machine"
        gpu_note = f" and {host['gpu_name']} ({vram:.0f} GB VRAM)" if host.get("gpu_name") else ""
        explanation = (
            f"Detected {total_ram:.0f} GB RAM{gpu_note}. Your system can comfortably run strong 8B, 14B, "
            "and even 32B local models via Ollama or LM Studio for private offline workflows."
        )
        tradeoff = (
            "Local AI is genuinely worthwhile here for private everyday tasks; you can still keep OpenRouter as an optional switchable model for heavy frontier coding."
        )
    elif (vram >= 6.0) or (apple_silicon and total_ram >= 16.0) or (total_ram >= 12.0):
        tier = "good"
        worth_it = "worth_it"
        headline = "Capable PC: 7B-8B local models work well for everyday tasks"
        gpu_note = f" + {host['gpu_name']}" if host.get("gpu_name") else " (CPU inference)"
        explanation = (
            f"Detected {total_ram:.0f} GB RAM{gpu_note}. You can smoothly run solid 4B-8B local models like "
            "Qwen3 8B or Llama 3.1 8B with good privacy and zero per-token cost."
        )
        tradeoff = (
            "Worth trying for privacy and free offline chat. For complex multi-step browser/API automations, a fast cloud backup like DeepSeek Flash or Claude Sonnet via OpenRouter helps too."
        )
    else:
        tier = "limited"
        worth_it = "mixed"
        headline = "Entry specs: only compact 3B-4B local models fit; cloud models perform much better"
        explanation = (
            f"Detected {total_ram or 'unknown'} GB RAM ({host.get('os')}, {host.get('cpu_cores')} cores). "
            "Small 3B-4B models (like Qwen3 4B or Llama 3.2 3B) can run locally on CPU, though tool calling and long contexts will be noticeably slower."
        )
        tradeoff = (
            "For most users on 6-10 GB CPU-only machines, a cheap pay-as-you-go OpenRouter key feels dramatically faster and smarter, "
            "unless running offline locally is your top priority."
        )

    recommended = [
        opt.to_dict(total_ram_gb=total_ram, vram_gb=vram, installed=installed_models)
        for opt in LOCAL_MODEL_CATALOG
    ]
    # Order models so ones that actually fit on this device come first, with sweet spot prioritized
    fit_order = {"fits": 0, "tight": 1, "too_heavy": 2}
    tier_order = (
        {"ultra_tiny": 0, "lightweight": 1, "sweet_spot": 2, "workstation": 3}
        if tier == "weak_device"
        else {"lightweight": 0, "sweet_spot": 1, "workstation": 2, "ultra_tiny": 3}
        if tier == "limited"
        else {"sweet_spot": 0, "workstation": 1, "lightweight": 2, "ultra_tiny": 3}
        if tier == "good"
        else {"workstation": 0, "sweet_spot": 1, "lightweight": 2, "ultra_tiny": 3}
    )
    recommended.sort(
        key=lambda m: (
            0 if m["installed"] else 1,
            fit_order.get(m["fit"], 2),
            tier_order.get(m["tier"], 9),
        )
    )

    client_summary = None
    if client_ram_gb is not None or client_cores is not None or client_mobile is not None:
        client_summary = {
            "ram_gb": client_ram_gb,
            "cores": client_cores,
            "is_mobile": bool(client_mobile),
            "remote_client_mismatch": remote_client_mismatch,
        }

    return {
        "host": host,
        "client": client_summary,
        "runtimes": runtimes,
        "verdict": {
            "tier": tier,
            "worth_it": worth_it,
            "headline": headline,
            "explanation": explanation,
            "tradeoff_summary": tradeoff,
        },
        "recommended_models": recommended,
    }


async def inspect_system_capabilities(
    *,
    client_ram_gb: float | None = None,
    client_cores: int | None = None,
    client_mobile: bool | None = None,
) -> dict[str, Any]:
    """Gather host specs + local model runtime status and return capability advice."""
    host, runtimes = await asyncio.gather(
        asyncio.to_thread(collect_host_specs),
        probe_local_runtimes(),
    )
    return assess_local_ai(
        host,
        runtimes,
        client_ram_gb=client_ram_gb,
        client_cores=client_cores,
        client_mobile=client_mobile,
    )
