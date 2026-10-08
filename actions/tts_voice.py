# actions/tts_voice.py
#
# Generates JARVIS voice notes (OGG/Opus for Telegram) using Gemini TTS
# with the same "Charon" voice the live assistant speaks with.
# Falls back to macOS 'say' if the TTS API is unavailable.

import json
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

_OS = platform.system()

TTS_MODELS  = ["gemini-3.8-flash-lite-tts", "gemini-2.5-flash-preview-tts"]
JARVIS_VOICE = "Charon"   # same prebuilt voice as the live assistant

# fallback macOS voices per language
_SAY_VOICES = {
    "english": ["Samantha", "Daniel", "Albert"],
    "french":  ["Thomas", "Amelie", "Amélie", "Audrey"],
    "arabic":  ["Majed"],
    "german":  ["Anna"],
    "spanish": ["Monica", "Mónica"],
    "italian": ["Alice"],
    "turkish": ["Yelda"],
}


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _api_key() -> str:
    try:
        cfg = json.loads((_get_base_dir() / "config" / "api_keys.json")
                         .read_text(encoding="utf-8"))
        return cfg.get("gemini_api_key", "")
    except Exception:
        return ""


def _pcm_to_ogg(pcm: bytes, sample_rate: int = 24000) -> Path | None:
    """Wrap raw 16-bit mono PCM in a WAV header and convert to OGG/Opus."""
    if not shutil.which("ffmpeg"):
        return None
    tmp = Path(tempfile.mkdtemp(prefix="jarvis_tts_"))
    wav, ogg = tmp / "v.wav", tmp / "v.ogg"
    header = (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " +
              struct.pack("<IHHIIHH", 16, 1, 1, sample_rate,
                          sample_rate * 2, 2, 16) +
              b"data" + struct.pack("<I", len(pcm)))
    wav.write_bytes(header + pcm)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
             "-c:a", "libopus", "-b:a", "32k", str(ogg)],
            timeout=60, check=True,
        )
    except Exception:
        return None
    return ogg if ogg.exists() and ogg.stat().st_size > 0 else None


def _gemini_tts(text: str) -> Path | None:
    key = _api_key()
    if not key:
        return None
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=key)
        for model in TTS_MODELS:
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=text,
                    config=types.GenerateContentConfig(
                        response_modalities=["AUDIO"],
                        speech_config=types.SpeechConfig(
                            voice_config=types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                    voice_name=JARVIS_VOICE
                                )
                            )
                        ),
                    ),
                )
                part = resp.candidates[0].content.parts[0]
                pcm  = part.inline_data.data
                if pcm:
                    return _pcm_to_ogg(pcm)
            except Exception as e:
                print(f"[TTS] {model} failed: {str(e)[:100]}")
        return None
    except Exception as e:
        print(f"[TTS] Gemini TTS unavailable: {str(e)[:100]}")
        return None


def _mac_say_tts(text: str, language: str) -> Path | None:
    if _OS != "Darwin" or not shutil.which("say") or not shutil.which("ffmpeg"):
        return None
    try:
        installed = subprocess.run(["say", "-v", "?"], capture_output=True,
                                   text=True, timeout=10).stdout.lower()
        voice = None
        for cand in _SAY_VOICES.get(language, _SAY_VOICES["english"]):
            if cand.lower() in installed:
                voice = cand
                break
        tmp  = Path(tempfile.mkdtemp(prefix="jarvis_say_"))
        aiff = tmp / "v.aiff"
        ogg  = tmp / "v.ogg"
        cmd = ["say", "-o", str(aiff)]
        if voice:
            cmd += ["-v", voice]
        cmd.append(text)
        subprocess.run(cmd, timeout=60, check=True)
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(aiff),
             "-c:a", "libopus", "-b:a", "32k", str(ogg)],
            timeout=60, check=True,
        )
        return ogg if ogg.exists() and ogg.stat().st_size > 0 else None
    except Exception as e:
        print(f"[TTS] macOS say failed: {str(e)[:80]}")
        return None


def make_voice_ogg(text: str, language: str = "english") -> Path | None:
    """Voice note in JARVIS's Charon voice; falls back to system TTS."""
    text = text.strip()
    if not text:
        return None
    return _gemini_tts(text[:800]) or _mac_say_tts(text[:800], language)
