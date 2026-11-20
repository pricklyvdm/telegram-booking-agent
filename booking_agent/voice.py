"""Voice: manager's voice notes -> text (faster-whisper, local);
agent's text -> British voice note (edge-tts en-GB -> ogg/opus via ffmpeg)."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path

_model = None


def _load_model(name: str, device: str = os.getenv("WHISPER_DEVICE", "cpu")):
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        try:
            _model = WhisperModel(name, device=device, compute_type="float16" if device == "cuda" else "int8")
        except Exception:
            _model = WhisperModel(name, device="cpu", compute_type="int8")
    return _model


def _transcribe_sync(path: Path, model_name: str) -> str:
    global _model
    try:
        segments, _ = _load_model(model_name).transcribe(str(path), vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()
    except RuntimeError:  # CUDA libs missing at inference time -> fall back to CPU
        _model = None
        segments, _ = _load_model(model_name, "cpu").transcribe(str(path), vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()


async def transcribe(path: Path, model_name: str = "small") -> str:
    return await asyncio.to_thread(_transcribe_sync, path, model_name)


def _duration(path: Path) -> int:
    out = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
                         capture_output=True, text=True)
    try:
        return max(1, round(float(json.loads(out.stdout)["format"]["duration"])))
    except Exception:
        return 1


def pick_voice(text: str, default: str) -> str:
    """British voice for English; if the manager switched language, a matching voice."""
    if any("\u0400" <= ch <= "\u04ff" for ch in text):
        return "uk-UA-OstapNeural" if any(ch in "іїєґІЇЄҐ" for ch in text) else "ru-RU-DmitryNeural"
    return default


async def synthesize(text: str, out_dir: Path, voice: str = "en-GB-RyanNeural") -> tuple[Path, int]:
    """Returns (path to .ogg opus voice note, duration in seconds)."""
    import edge_tts
    voice = pick_voice(text, voice)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"tts_{abs(hash(text)) % 10**8}"
    mp3, ogg = stem.with_suffix(".mp3"), stem.with_suffix(".ogg")
    await edge_tts.Communicate(text, voice).save(str(mp3))
    await asyncio.to_thread(subprocess.run, [
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3),
        "-c:a", "libopus", "-b:a", "32k", "-ac", "1", "-ar", "48000", str(ogg)], check=True)
    mp3.unlink(missing_ok=True)
    return ogg, _duration(ogg)
