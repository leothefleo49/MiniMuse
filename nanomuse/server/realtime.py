"""Live Call Mode: Native Realtime voice streaming + universal hands-free call loop.

Supports two ways to stay in an uninterrupted voice call with MiniMuse:

1. **Native Realtime audio-to-audio** when the active chat/call model is a native realtime
   model (OpenAI ``gpt-realtime`` / ``gpt-4o-realtime*`` over ``wss://api.openai.com/v1/realtime``
   or Google Gemini Live ``gemini-*-native-audio*`` / ``gemini-*-live*`` over
   ``BidiGenerateContent``), tuned with generous turn-end silence patience so the assistant
   does not cut the user off mid-thought, plus a bridge tool to delegate workspace/device tasks.
2. **Universal Hands-Free Live Call** for every other model (DeepSeek, Qwen, Claude, Ollama,
   OpenRouter, etc.): runs normal MiniMuse agent turns in the chosen thread, streams text
   deltas in real time, and lets the app/watch speak continuously and re-arm the microphone
   after each reply without sleeping or turning off mid-prompt.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import re
import time
from typing import TYPE_CHECKING, Any

from nanomuse.logger import logger

if TYPE_CHECKING:
    from fastapi import WebSocket

    from nanomuse.server.service import MuseService

_OPENAI_REALTIME_RE = re.compile(r"(?:^|/)gpt-(?:realtime|4o-(?:mini-)?realtime)", re.I)
_GEMINI_LIVE_RE = re.compile(r"(?:^|models/)(?:gemini-.*(?:native-audio|live)|.*live-preview)", re.I)

OPENAI_REALTIME_PRESETS = (
    "gpt-realtime",
    "gpt-4o-realtime-preview",
    "gpt-4o-mini-realtime-preview",
)
GEMINI_LIVE_PRESETS = (
    "gemini-2.5-flash-native-audio-preview-12-2025",
    "gemini-2.5-flash-live-preview",
)


def detect_realtime_kind(provider: str, model: str, base_url: str | None = None) -> str:
    """Return ``'openai_realtime'``, ``'gemini_live'``, or ``'voice_loop'``."""
    m = (model or "").strip()
    p = (provider or "").strip().lower()
    host = (base_url or "").strip().lower()
    if _OPENAI_REALTIME_RE.search(m) or (
        "realtime" in m.lower() and (p == "openai" or "api.openai.com" in host)
    ):
        return "openai_realtime"
    if _GEMINI_LIVE_RE.search(m) or (
        ("native-audio" in m.lower() or m.lower().endswith("-live"))
        and ("gemini" in p or "generativelanguage.googleapis.com" in host)
    ):
        return "gemini_live"
    return "voice_loop"


def _resolve_secret(svc: MuseService, raw: str) -> str:
    if not raw:
        return ""
    try:
        return str(svc.app.vault.resolve(raw, strict=False) or "")
    except Exception:  # noqa: BLE001
        return ""


def _resolve_call_target(svc: MuseService) -> dict[str, Any]:
    """Resolve provider, model, base_url, and API key for Live Call mode."""
    cfg = svc.connections.data.get("call") if hasattr(svc.connections, "data") else None
    override = cfg if isinstance(cfg, dict) else {}
    pref_model = str(override.get("model") or "").strip()
    pref_provider = str(override.get("provider") or "").strip()

    s_llm = svc.settings.llm
    provider = pref_provider or str(s_llm.provider or "openai")
    model = pref_model or str(s_llm.model or "")
    base_url = str(s_llm.base_url or "")
    api_key = _resolve_secret(svc, str(s_llm.api_key or ""))

    if pref_provider and pref_provider != s_llm.provider:
        # Check saved endpoints first for a key + base_url matching the chosen call provider
        try:
            endpoints = svc.connections.endpoints.rows() if hasattr(svc.connections, "endpoints") else []
            for ep in endpoints:
                ep_id = str(ep.get("id") or "")
                ep_url = str(ep.get("base_url") or "")
                ep_key = _resolve_secret(svc, str(ep.get("api_key") or ""))
                if pref_provider == ep_id or (
                    pref_provider == "gemini" and "generativelanguage.googleapis.com" in ep_url
                ) or (pref_provider == "openai" and "api.openai.com" in ep_url):
                    base_url = ep_url or base_url
                    if ep_key:
                        api_key = ep_key
                    break
        except Exception as exc:  # noqa: BLE001
            logger.debug("resolve call endpoint: {}", exc)

        if pref_provider == "gemini" and not base_url:
            base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
        elif pref_provider == "openai" and not base_url:
            base_url = "https://api.openai.com/v1"

    mode = detect_realtime_kind(provider, model, base_url)
    return {
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
        "mode": mode,
        "native_realtime": mode in ("openai_realtime", "gemini_live"),
        "override_active": bool(pref_model or pref_provider),
    }


def _discover_realtime_presets(svc: MuseService) -> list[dict[str, Any]]:
    """List native realtime options that match keys the user already configured."""
    options: list[dict[str, Any]] = []
    have_openai = False
    have_gemini = False
    try:
        for ep in svc.connections.endpoints.rows():
            url = str(ep.get("base_url") or "").lower()
            key = _resolve_secret(svc, str(ep.get("api_key") or ""))
            if "api.openai.com" in url and key:
                have_openai = True
            if "generativelanguage.googleapis.com" in url and key:
                have_gemini = True
    except Exception:  # noqa: BLE001
        pass
    s_url = str(svc.settings.llm.base_url or "").lower()
    if svc.settings.llm.api_key:
        if "api.openai.com" in s_url or svc.settings.llm.provider == "openai":
            have_openai = True
        if "generativelanguage.googleapis.com" in s_url or svc.settings.llm.provider == "gemini":
            have_gemini = True
    if have_openai:
        for m in OPENAI_REALTIME_PRESETS:
            options.append({"provider": "openai", "model": m, "kind": "openai_realtime"})
    if have_gemini:
        for m in GEMINI_LIVE_PRESETS:
            options.append({"provider": "gemini", "model": m, "kind": "gemini_live"})
    return options


def call_capabilities(svc: MuseService) -> dict[str, Any]:
    """Return Live Call capabilities and current model configuration."""
    target = _resolve_call_target(svc)
    return {
        "supported": True,
        "voice_loop": True,
        "vision": True,
        "camera": True,
        "screen_share": True,
        "mode": target["mode"],
        "native_realtime": target["native_realtime"],
        "provider": target["provider"],
        "model": target["model"],
        "override_active": target["override_active"],
        "has_api_key": bool(target["api_key"]),
        "default_patience_ms": 2600,
        "available_realtime_options": _discover_realtime_presets(svc),
    }


def set_call_config(svc: MuseService, provider: str = "", model: str = "") -> dict[str, Any]:
    """Persist or clear an explicit Call Model override without disturbing the main chat slot."""
    p = provider.strip()[:80]
    m = model.strip()[:160]
    if not p and not m:
        svc.connections.data.pop("call", None)
    else:
        svc.connections.data["call"] = {"provider": p, "model": m}
    if hasattr(svc.connections, "_save"):
        svc.connections._save()
    return call_capabilities(svc)


async def handle_call_websocket(
    svc: MuseService,
    ws: WebSocket,
    *,
    thread_id: str = "main",
    patience_ms: int = 2600,
) -> None:
    """Serve ``/ws/call`` using either Native Realtime WS proxy or Universal Voice Loop."""
    target = _resolve_call_target(svc)
    await ws.send_json(
        {
            "kind": "call_ready",
            "thread": thread_id,
            "mode": target["mode"],
            "native_realtime": target["native_realtime"],
            "model": target["model"],
            "provider": target["provider"],
            "patience_ms": patience_ms,
        }
    )
    if target["mode"] == "openai_realtime" and target["api_key"]:
        await _run_openai_realtime(svc, ws, target, thread_id=thread_id, patience_ms=patience_ms)
        return
    if target["mode"] == "gemini_live" and target["api_key"]:
        await _run_gemini_live(svc, ws, target, thread_id=thread_id, patience_ms=patience_ms)
        return
    await _run_universal_voice_loop(svc, ws, thread_id=thread_id)


def _clean_jpeg_b64(raw: Any) -> str:
    """Strip data URL prefix and validate a base64 JPEG payload (cap at ~2 MB)."""
    s = str(raw or "").strip()
    if not s:
        return ""
    if s.startswith("data:"):
        idx = s.find(",")
        if idx >= 0:
            s = s[idx + 1 :].strip()
    if len(s) > 2_800_000:
        return ""
    return s


def _save_call_frame(svc: MuseService, jpeg_b64: str, source: str = "camera") -> str | None:
    """Persist a live call camera/screen-share JPEG into workspace/attachments and prune old ones."""
    clean = _clean_jpeg_b64(jpeg_b64)
    if not clean:
        return None
    try:
        raw = base64.b64decode(clean)
    except Exception:  # noqa: BLE001
        return None
    if not raw or len(raw) > 2_200_000:
        return None
    src_tag = "screen" if str(source or "").lower().startswith("screen") else "camera"
    att_dir = svc.workspace() / "attachments"
    try:
        att_dir.mkdir(parents=True, exist_ok=True)
        fname = f"live-{src_tag}-{int(time.time() * 1000)}.jpg"
        dst = att_dir / fname
        dst.write_bytes(raw)
        # Keep only the 16 newest live call snapshots so workspace stays clean
        existing = sorted(
            att_dir.glob("live-*.jpg"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in existing[16:]:
            with contextlib.suppress(OSError):
                stale.unlink()
        return f"attachments/{fname}"
    except Exception as exc:  # noqa: BLE001
        logger.debug("save live call frame failed: {}", exc)
        return None


async def _run_universal_voice_loop(
    svc: MuseService, ws: WebSocket, *, thread_id: str
) -> None:
    """Universal hands-free turn loop: works with any configured MiniMuse model,
    including live camera and screen-share frames."""
    queue = svc.bus.subscribe()
    send_lock = asyncio.Lock()
    latest_frame: dict[str, Any] | None = None

    async def safe_send(payload: dict[str, Any]) -> None:
        async with send_lock:
            await ws.send_json(payload)

    async def forward_bus() -> None:
        active_sid: str | None = None
        while True:
            ev = await queue.get()
            inner_ev = ev.get("event") if isinstance(ev.get("event"), dict) else {}
            ev_thread = ev.get("thread") or inner_ev.get("thread")
            if ev_thread != thread_id:
                continue
            kind = ev.get("kind")
            if kind == "stream_start":
                active_sid = ev.get("id")
                await safe_send({"kind": "assistant_start", "id": active_sid})
            elif kind in ("delta", "stream_delta"):
                delta = str(ev.get("text") or ev.get("delta") or "")
                if delta:
                    await safe_send({"kind": "assistant_delta", "delta": delta})
            elif kind == "stream_end":
                active_sid = None
            elif kind == "event":
                if inner_ev.get("type") == "assistant" and not ev.get("discard"):
                    await safe_send(
                        {
                            "kind": "assistant_done",
                            "id": inner_ev.get("id"),
                            "text": str(inner_ev.get("text") or ""),
                        }
                    )
            elif kind == "hold" or kind == "approval":
                await safe_send({"kind": "call_notice", "event": ev})

    pump = asyncio.create_task(forward_bus())
    try:
        while True:
            msg = await ws.receive_json()
            mtype = str(msg.get("kind") or msg.get("type") or "")
            if mtype == "ping":
                await safe_send({"kind": "pong"})
                continue
            if mtype == "interrupt":
                svc.stop_thread(thread_id)
                await safe_send({"kind": "interrupted"})
                continue
            if mtype == "video_frame":
                jpeg = _clean_jpeg_b64(msg.get("jpeg") or msg.get("data"))
                src = "screen" if str(msg.get("source") or "").lower().startswith("screen") else "camera"
                if jpeg:
                    latest_frame = {"jpeg": jpeg, "source": src, "ts": time.monotonic()}
                    if msg.get("ack"):
                        await safe_send({"kind": "frame_ack", "source": src})
                continue
            if mtype == "video_stop":
                latest_frame = None
                continue
            if mtype == "user_text":
                text = str(msg.get("text") or "").strip()
                language = str(msg.get("language") or "").strip()
                inline_jpeg = _clean_jpeg_b64(msg.get("jpeg") or msg.get("image"))
                src = str(msg.get("source") or "").strip().lower()
                files: list[str] = []
                frame_b64 = inline_jpeg
                frame_src = src
                if not frame_b64 and latest_frame and (time.monotonic() - float(latest_frame["ts"]) < 25.0):
                    frame_b64 = str(latest_frame["jpeg"])
                    frame_src = str(latest_frame["source"])
                if frame_b64:
                    saved = _save_call_frame(svc, frame_b64, frame_src or "camera")
                    if saved:
                        files.append(saved)
                if not text and not files:
                    continue
                if not text and files:
                    label = "shared screen" if frame_src.startswith("screen") else "live camera"
                    text = f"Here is what I'm showing you on my {label} right now. What do you see?"
                try:
                    ev = svc.send(thread_id, text, files=files or None, language=language)
                    await safe_send({"kind": "user_accepted", "id": ev.get("id"), "text": text, "has_frame": bool(files)})
                except Exception as exc:  # noqa: BLE001
                    await safe_send({"kind": "call_error", "message": str(exc)})
            elif mtype == "end_call":
                break
    finally:
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump
        svc.bus.unsubscribe(queue)


def _agent_instructions(svc: MuseService) -> str:
    name = svc.profile.name if hasattr(svc, "profile") else "MiniMuse"
    user_part = (
        f" The user's name is {svc.profile.user_name}."
        if getattr(svc.profile, "user_name", "")
        else ""
    )
    return (
        f"You are {name}, a helpful, natural-sounding personal voice assistant inside MiniMuse in a live call.{user_part} "
        "Keep spoken replies clear and concise unless asked for detail. Wait patiently if the user pauses briefly. "
        "The user may also share their live camera or screen during the call; when visual frames are shared, "
        "respond naturally and honestly to what you actually see (never guess). "
        "You know MiniMuse's full UI: Chat (with collapsible Chats sidebar on desktop, bottom bar on phone), "
        "Feed, Ideas, Goals, Calendar, Location, Library (synced Apps, Artifacts, Media), Devices (PC/phone pairing "
        "from anywhere with Personal Cloud Free Tunnel, Wear OS Smartwatch, Meta Quest VR, Hands), Avatar Studio "
        "(tap the avatar then the edit pencil), and Settings (Name & personality, Account & Personal Cloud, Usage, "
        "Appearance, Proactivity, Agent behavior, Model, nanoMuse Cloud, Devices, Notifications, Safety·Sentinel, "
        "App lock, Passwords & logins, Permissions, Data controls, Import & export, Developer, About). "
        "Use `run_agent_task` whenever the user asks to check files, search the web, control apps, inspect calendar/email, "
        "create reminders, inspect or change ANY MiniMuse setting (only when explicitly asked!), or run any MiniMuse tool. "
        "Always be honest about what you can and cannot do (e.g., you cannot grant OS permissions or unlock App Lock PIN/biometrics without the user)."
    )


async def _delegate_to_thread(
    svc: MuseService, thread_id: str, instruction: str, files: list[str] | None = None
) -> str:
    """Run a delegated MiniMuse turn from inside a native realtime voice call and wait for its answer."""
    q = svc.bus.subscribe()
    try:
        svc.send(thread_id, instruction, files=files)
        deadline = asyncio.get_running_loop().time() + 90.0
        while asyncio.get_running_loop().time() < deadline:
            rem = deadline - asyncio.get_running_loop().time()
            ev = await asyncio.wait_for(q.get(), timeout=max(0.5, rem))
            if ev.get("thread") != thread_id:
                continue
            if ev.get("kind") == "event":
                inner = ev.get("event") or {}
                if inner.get("type") == "assistant":
                    return str(inner.get("text") or "Done.")
        return "Still working in the background chat thread."
    finally:
        svc.bus.unsubscribe(q)


async def _run_openai_realtime(
    svc: MuseService,
    ws: WebSocket,
    target: dict[str, Any],
    *,
    thread_id: str,
    patience_ms: int,
) -> None:
    """Proxy live PCM16 audio/text to OpenAI Realtime API with generous VAD patience."""
    try:
        import websockets
    except ImportError:
        await ws.send_json(
            {
                "kind": "call_notice",
                "message": "websockets package unavailable; falling back to universal voice loop",
            }
        )
        await _run_universal_voice_loop(svc, ws, thread_id=thread_id)
        return

    model = target["model"] or "gpt-realtime"
    url = f"wss://api.openai.com/v1/realtime?model={model}"
    headers = {
        "Authorization": f"Bearer {target['api_key']}",
        "OpenAI-Beta": "realtime=v1",
    }
    try:
        async with websockets.connect(url, additional_headers=headers, max_size=None) as upstream:
            await upstream.send(
                json.dumps(
                    {
                        "type": "session.update",
                        "session": {
                            "instructions": _agent_instructions(svc),
                            "modalities": ["audio", "text"],
                            "input_audio_format": "pcm16",
                            "output_audio_format": "pcm16",
                            "input_audio_transcription": {"model": "whisper-1"},
                            "turn_detection": {
                                "type": "server_vad",
                                "threshold": 0.55,
                                "prefix_padding_ms": 400,
                                "silence_duration_ms": max(1200, min(5000, int(patience_ms))),
                                "create_response": True,
                            },
                            "tools": [
                                {
                                    "type": "function",
                                    "name": "run_agent_task",
                                    "description": "Execute a task using MiniMuse tools (files, web search, browser, calendar, reminders, devices).",
                                    "parameters": {
                                        "type": "object",
                                        "properties": {
                                            "request": {"type": "string"},
                                        },
                                        "required": ["request"],
                                    },
                                }
                            ],
                        },
                    }
                )
            )

            latest_frame: dict[str, Any] | None = None

            async def upstream_to_client() -> None:
                async for raw in upstream:
                    ev = json.loads(raw)
                    etype = ev.get("type")
                    if etype == "response.audio.delta" and ev.get("delta"):
                        await ws.send_json({"kind": "audio_delta", "pcm16": ev["delta"]})
                    elif etype == "response.audio_transcript.delta" and ev.get("delta"):
                        await ws.send_json({"kind": "assistant_delta", "delta": ev["delta"]})
                    elif etype == "response.audio_transcript.done":
                        text = str(ev.get("transcript") or "").strip()
                        await ws.send_json({"kind": "assistant_done", "text": text})
                    elif etype == "conversation.item.input_audio_transcription.completed":
                        t = str(ev.get("transcript") or "").strip()
                        if t:
                            await ws.send_json({"kind": "user_transcript", "text": t})
                    elif etype == "input_audio_buffer.speech_started":
                        await ws.send_json({"kind": "speech_started"})
                    elif etype == "response.function_call_arguments.done":
                        if ev.get("name") == "run_agent_task":
                            call_id = ev.get("call_id")
                            try:
                                args = json.loads(ev.get("arguments") or "{}")
                                extra_files: list[str] | None = None
                                if latest_frame and (time.monotonic() - float(latest_frame["ts"]) < 25.0):
                                    saved = _save_call_frame(svc, str(latest_frame["jpeg"]), str(latest_frame["source"]))
                                    if saved:
                                        extra_files = [saved]
                                out = await _delegate_to_thread(
                                    svc, thread_id, str(args.get("request") or ""), files=extra_files
                                )
                            except Exception as exc:  # noqa: BLE001
                                out = f"Task failed: {exc}"
                            await upstream.send(
                                json.dumps(
                                    {
                                        "type": "conversation.item.create",
                                        "item": {
                                            "type": "function_call_output",
                                            "call_id": call_id,
                                            "output": out[:4000],
                                        },
                                    }
                                )
                            )
                            await upstream.send(json.dumps({"type": "response.create"}))
                    elif etype == "error":
                        msg = str((ev.get("error") or {}).get("message") or "Realtime error")
                        await ws.send_json({"kind": "call_error", "message": msg})

            async def client_to_upstream() -> None:
                nonlocal latest_frame
                while True:
                    msg = await ws.receive_json()
                    kind = str(msg.get("kind") or msg.get("type") or "")
                    if kind == "audio_chunk" and msg.get("pcm16"):
                        await upstream.send(
                            json.dumps(
                                {
                                    "type": "input_audio_buffer.append",
                                    "audio": msg["pcm16"],
                                }
                            )
                        )
                    elif kind == "video_frame":
                        jpeg = _clean_jpeg_b64(msg.get("jpeg") or msg.get("data"))
                        src = "screen" if str(msg.get("source") or "").lower().startswith("screen") else "camera"
                        if jpeg:
                            latest_frame = {"jpeg": jpeg, "source": src, "ts": time.monotonic()}
                            if msg.get("ack"):
                                await ws.send_json({"kind": "frame_ack", "source": src})
                    elif kind == "video_stop":
                        latest_frame = None
                    elif kind == "commit_turn":
                        await upstream.send(json.dumps({"type": "input_audio_buffer.commit"}))
                        await upstream.send(json.dumps({"type": "response.create"}))
                    elif kind == "user_text" and (msg.get("text") or msg.get("jpeg")):
                        content_parts: list[dict[str, Any]] = []
                        if msg.get("text"):
                            content_parts.append({"type": "input_text", "text": str(msg["text"])})
                        jpeg = _clean_jpeg_b64(msg.get("jpeg") or msg.get("image"))
                        if not jpeg and latest_frame and (time.monotonic() - float(latest_frame["ts"]) < 25.0):
                            jpeg = str(latest_frame["jpeg"])
                        if jpeg:
                            content_parts.append(
                                {
                                    "type": "input_image",
                                    "image_url": f"data:image/jpeg;base64,{jpeg}",
                                }
                            )
                        if content_parts:
                            await upstream.send(
                                json.dumps(
                                    {
                                        "type": "conversation.item.create",
                                        "item": {
                                            "type": "message",
                                            "role": "user",
                                            "content": content_parts,
                                        },
                                    }
                                )
                            )
                            await upstream.send(json.dumps({"type": "response.create"}))
                    elif kind == "interrupt":
                        await upstream.send(json.dumps({"type": "response.cancel"}))
                    elif kind == "end_call":
                        break

            t1 = asyncio.create_task(upstream_to_client())
            t2 = asyncio.create_task(client_to_upstream())
            done, pending = await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            for t in done:
                exc = t.exception()
                if exc and not isinstance(exc, asyncio.CancelledError):
                    logger.debug("openai realtime end: {}", exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("openai realtime failed ({}); falling back to voice loop", exc)
        await ws.send_json(
            {
                "kind": "call_notice",
                "message": f"Realtime socket unavailable ({exc}); switched to hands-free mode",
            }
        )
        await _run_universal_voice_loop(svc, ws, thread_id=thread_id)


async def _run_gemini_live(
    svc: MuseService,
    ws: WebSocket,
    target: dict[str, Any],
    *,
    thread_id: str,
    patience_ms: int,
) -> None:
    """Proxy live PCM16 audio/text to Google Gemini Multimodal Live (BidiGenerateContent)."""
    try:
        import websockets
    except ImportError:
        await _run_universal_voice_loop(svc, ws, thread_id=thread_id)
        return

    model = target["model"]
    if not model.startswith("models/"):
        model = f"models/{model}"
    url = (
        "wss://generativelanguage.googleapis.com/ws/"
        "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
        f"?key={target['api_key']}"
    )
    try:
        async with websockets.connect(url, max_size=None) as upstream:
            await upstream.send(
                json.dumps(
                    {
                        "setup": {
                            "model": model,
                            "generationConfig": {
                                "responseModalities": ["AUDIO"],
                            },
                            "systemInstruction": {
                                "parts": [{"text": _agent_instructions(svc)}],
                            },
                            "realtimeInputConfig": {
                                "automaticActivityDetection": {
                                    "silenceDurationMs": max(1200, min(5000, int(patience_ms))),
                                }
                            },
                        }
                    }
                )
            )

            async def upstream_to_client() -> None:
                buf_text: list[str] = []
                async for raw in upstream:
                    ev = json.loads(raw)
                    sc = ev.get("serverContent") or {}
                    model_turn = sc.get("modelTurn") or {}
                    for part in model_turn.get("parts") or []:
                        inline = part.get("inlineData") or {}
                        if inline.get("data") and "audio" in str(inline.get("mimeType") or ""):
                            await ws.send_json(
                                {
                                    "kind": "audio_delta",
                                    "pcm16": inline["data"],
                                    "rate": 24000,
                                }
                            )
                        if part.get("text"):
                            buf_text.append(part["text"])
                            await ws.send_json({"kind": "assistant_delta", "delta": part["text"]})
                    out_tx = (sc.get("outputTranscription") or {}).get("text")
                    if out_tx:
                        buf_text.append(out_tx)
                        await ws.send_json({"kind": "assistant_delta", "delta": out_tx})
                    in_tx = (sc.get("inputTranscription") or {}).get("text")
                    if in_tx:
                        await ws.send_json({"kind": "user_transcript", "text": in_tx})
                    if sc.get("turnComplete"):
                        await ws.send_json(
                            {"kind": "assistant_done", "text": "".join(buf_text).strip()}
                        )
                        buf_text.clear()

            async def client_to_upstream() -> None:
                while True:
                    msg = await ws.receive_json()
                    kind = str(msg.get("kind") or msg.get("type") or "")
                    if kind == "audio_chunk" and msg.get("pcm16"):
                        # Gemini expects 16kHz PCM16 base64
                        await upstream.send(
                            json.dumps(
                                {
                                    "realtimeInput": {
                                        "mediaChunks": [
                                            {
                                                "mimeType": "audio/pcm;rate=16000",
                                                "data": msg["pcm16"],
                                            }
                                        ]
                                    }
                                }
                            )
                        )
                    elif kind == "video_frame":
                        jpeg = _clean_jpeg_b64(msg.get("jpeg") or msg.get("data"))
                        src = "screen" if str(msg.get("source") or "").lower().startswith("screen") else "camera"
                        if jpeg:
                            await upstream.send(
                                json.dumps(
                                    {
                                        "realtimeInput": {
                                            "mediaChunks": [
                                                {
                                                    "mimeType": "image/jpeg",
                                                    "data": jpeg,
                                                }
                                            ]
                                        }
                                    }
                                )
                            )
                            if msg.get("ack"):
                                await ws.send_json({"kind": "frame_ack", "source": src})
                    elif kind == "video_stop":
                        pass
                    elif kind == "commit_turn":
                        await upstream.send(
                            json.dumps({"realtimeInput": {"audioStreamEnd": True}})
                        )
                    elif kind == "user_text" and (msg.get("text") or msg.get("jpeg")):
                        parts: list[dict[str, Any]] = []
                        if msg.get("text"):
                            parts.append({"text": str(msg["text"])})
                        jpeg = _clean_jpeg_b64(msg.get("jpeg") or msg.get("image"))
                        if jpeg:
                            parts.append(
                                {
                                    "inlineData": {
                                        "mimeType": "image/jpeg",
                                        "data": jpeg,
                                    }
                                }
                            )
                        if parts:
                            await upstream.send(
                                json.dumps(
                                    {
                                        "clientContent": {
                                            "turns": [
                                                {
                                                    "role": "user",
                                                    "parts": parts,
                                                }
                                            ],
                                            "turnComplete": True,
                                        }
                                    }
                                )
                            )
                    elif kind == "end_call":
                        break

            t1 = asyncio.create_task(upstream_to_client())
            t2 = asyncio.create_task(client_to_upstream())
            done, pending = await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            for t in done:
                exc = t.exception()
                if exc and not isinstance(exc, asyncio.CancelledError):
                    logger.debug("gemini live end: {}", exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("gemini live failed ({}); falling back to voice loop", exc)
        await ws.send_json(
            {
                "kind": "call_notice",
                "message": f"Gemini Live socket unavailable ({exc}); switched to hands-free mode",
            }
        )
        await _run_universal_voice_loop(svc, ws, thread_id=thread_id)


def _pcm16_b64_to_bytes(b64: str) -> bytes:
    return base64.b64decode(b64) if b64 else b""


__all__ = [
    "GEMINI_LIVE_PRESETS",
    "OPENAI_REALTIME_PRESETS",
    "call_capabilities",
    "detect_realtime_kind",
    "handle_call_websocket",
    "set_call_config",
]
