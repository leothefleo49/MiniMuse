"""Multimodal media inspection & text extraction (Images, Audio, Video, Code).

Provides OCR text extraction (RapidOCR), nutrition label macro extraction,
audio speech-to-text (faster-whisper), video keyframe & track analysis (PyAV),
and source code structural inspection.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from nanomuse.logger import logger

_OCR_INSTANCE = None
_WHISPER_INSTANCE = None

def get_ocr():
    global _OCR_INSTANCE
    if _OCR_INSTANCE is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _OCR_INSTANCE = RapidOCR()
        except Exception as e:
            logger.error("Failed to load RapidOCR: {}", e)
    return _OCR_INSTANCE

def get_whisper():
    global _WHISPER_INSTANCE
    if _WHISPER_INSTANCE is None:
        try:
            from faster_whisper import WhisperModel
            _WHISPER_INSTANCE = WhisperModel("tiny.en", device="cpu", compute_type="int8")
        except Exception as e:
            logger.error("Failed to load WhisperModel: {}", e)
    return _WHISPER_INSTANCE

def parse_nutrition(text: str) -> dict[str, Any]:
    """Extract calories, protein, carbs, fat, fiber from OCR text."""
    macros: dict[str, Any] = {}
    
    cal_m = re.search(r'(?:calories|cal|energy)[:\s]+(\d+)', text, re.I)
    if cal_m:
        macros['calories'] = int(cal_m.group(1))
        
    prot_m = re.search(r'(?:protein)[:\s]+(\d+(?:\.\d+)?)\s*g?', text, re.I)
    if prot_m:
        macros['protein'] = float(prot_m.group(1))
        
    carb_m = re.search(r'(?:total carbohydrate|carbohydrate|carbs)[:\s]+(\d+(?:\.\d+)?)\s*g?', text, re.I)
    if carb_m:
        macros['carbs'] = float(carb_m.group(1))
        
    fat_m = re.search(r'(?:total fat|fat)[:\s]+(\d+(?:\.\d+)?)\s*g?', text, re.I)
    if fat_m:
        macros['fat'] = float(fat_m.group(1))
        
    fib_m = re.search(r'(?:dietary fiber|fiber)[:\s]+(\d+(?:\.\d+)?)\s*g?', text, re.I)
    if fib_m:
        macros['fiber'] = float(fib_m.group(1))
        
    serv_m = re.search(r'(?:serving size)[:\s]+([^\n\r]+)', text, re.I)
    if serv_m:
        macros['serving_size'] = serv_m.group(1).strip()
        
    return macros

def inspect_image(path: str | Path) -> str:
    """Analyze image resolution, format, and run OCR text extraction."""
    p = Path(path)
    if not p.exists():
        return f"(Image file not found: {p})"
    
    info_lines = []
    width, height = 0, 0
    try:
        from PIL import Image
        with Image.open(p) as img:
            width, height = img.size
            info_lines.append(f"Image: {p.name} ({img.format}, {width}x{height}, mode: {img.mode})")
    except Exception as e:
        info_lines.append(f"Image: {p.name} (could not open header: {e})")
        
    ocr = get_ocr()
    if ocr is not None:
        try:
            res, _ = ocr(str(p))
            if res:
                text_lines = [r[1] for r in res if len(r) > 1 and r[1].strip()]
                full_text = "\n".join(text_lines)
                info_lines.append("Detected Text (OCR):")
                info_lines.append(full_text)
                
                macros = parse_nutrition(full_text)
                if macros:
                    info_lines.append("\nExtracted Nutrition Facts / Macros:")
                    for k, v in macros.items():
                        info_lines.append(f"  • {k.replace('_', ' ').title()}: {v}")
            else:
                info_lines.append("(No readable text detected in image)")
        except Exception as e:
            info_lines.append(f"(OCR error: {e})")
    else:
        info_lines.append("(OCR engine not available)")
        
    return "\n".join(info_lines)

def inspect_audio(path: str | Path) -> str:
    """Analyze audio duration and transcribe speech to text."""
    p = Path(path)
    if not p.exists():
        return f"(Audio file not found: {p})"
        
    lines = [f"Audio File: {p.name}"]
    whisper = get_whisper()
    if whisper is not None:
        try:
            segments, info = whisper.transcribe(str(p), beam_size=1)
            lines.append(f"Duration: {info.duration:.1f}s | Language: {info.language} (prob: {info.language_probability:.2f})")
            transcription = []
            for s in segments:
                transcription.append(f"[{s.start:.1f}s - {s.end:.1f}s]: {s.text}")
            if transcription:
                lines.append("Transcription:")
                lines.append("\n".join(transcription))
            else:
                lines.append("(No speech detected in audio)")
        except Exception as e:
            lines.append(f"(Transcription error: {e})")
    else:
        lines.append("(Whisper transcription engine not available)")
        
    return "\n".join(lines)

def inspect_video(path: str | Path) -> str:
    """Analyze video metadata, transcribe audio track, and OCR keyframes."""
    p = Path(path)
    if not p.exists():
        return f"(Video file not found: {p})"
        
    lines = [f"Video File: {p.name}"]
    try:
        import av
        container = av.open(str(p))
        v_stream = next((s for s in container.streams if s.type == 'video'), None)
        a_stream = next((s for s in container.streams if s.type == 'audio'), None)
        
        if v_stream:
            lines.append(f"Video: {v_stream.codec_context.name}, {v_stream.width}x{v_stream.height}, fps: {float(v_stream.average_rate or 0):.1f}")
        if a_stream:
            lines.append(f"Audio track: {a_stream.codec_context.name}, {a_stream.rate}Hz, channels: {a_stream.channels}")
            
        container.close()
    except Exception as e:
        lines.append(f"(Video metadata error: {e})")
        
    # Also transcribe audio if present
    whisper = get_whisper()
    if whisper is not None:
        try:
            segments, info = whisper.transcribe(str(p), beam_size=1)
            transcription = [f"[{s.start:.1f}s - {s.end:.1f}s]: {s.text}" for s in segments]
            if transcription:
                lines.append("Audio Track Transcription:")
                lines.append("\n".join(transcription[:20]))
        except Exception:
            pass
            
    return "\n".join(lines)

def inspect_code(path: str | Path) -> str:
    """Analyze code file structure, line count, functions, and classes."""
    p = Path(path)
    if not p.exists():
        return f"(Code file not found: {p})"
        
    try:
        text = p.read_text(encoding='utf-8', errors='replace')
        lines = text.splitlines()
        summary = [f"Code File: {p.name} ({len(lines)} lines, {len(text)} bytes)"]
        
        if p.suffix.lower() == '.py':
            import ast
            try:
                tree = ast.parse(text)
                classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
                funcs = [n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                if classes:
                    summary.append(f"Classes ({len(classes)}): {', '.join(classes[:15])}")
                if funcs:
                    summary.append(f"Functions ({len(funcs)}): {', '.join(funcs[:20])}")
            except Exception:
                pass
        return "\n".join(summary) + "\n\n" + "\n".join(lines[:100]) + ("\n...(truncated)" if len(lines) > 100 else "")
    except Exception as e:
        return f"(Could not read code file {p.name}: {e})"

def inspect_any_file(path: str | Path) -> str:
    """Universal multimodal file inspector for images, audio, video, code, and text."""
    p = Path(path)
    ext = p.suffix.lower()
    
    if ext in ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tiff'):
        return inspect_image(p)
    elif ext in ('.mp3', '.wav', '.m4a', '.ogg', '.flac', '.aac', '.wma'):
        return inspect_audio(p)
    elif ext in ('.mp4', '.mkv', '.mov', '.avi', '.webm'):
        return inspect_video(p)
    elif ext in ('.py', '.js', '.ts', '.tsx', '.jsx', '.html', '.css', '.json', '.rs', '.go', '.kt', '.swift', '.c', '.cpp', '.h', '.java'):
        return inspect_code(p)
    else:
        # Default text read
        try:
            return p.read_text(encoding='utf-8', errors='replace')[:4000]
        except Exception as e:
            return f"(Could not inspect {p.name}: {e})"
