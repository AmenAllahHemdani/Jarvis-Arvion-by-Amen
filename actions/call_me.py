# actions/call_me.py
#
# Urgent phone alerts through the owner's own private Telegram bot.
# The bot sends loud repeated notification pings plus a spoken voice
# message (TTS), so the owner's phone "rings" and JARVIS talks.
#
# One-time setup:
#   1. Create a bot with @BotFather (/newbot) and put its token in
#      config/api_keys.json under "telegram_bot_token".
#   2. Open the bot in Telegram and press START once.
#      (telegram_chat_id is then detected and saved automatically)
# Tip: give the bot's chat a loud, repeating notification sound in
# Telegram so alerts really ring like a call.

import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

_OS = platform.system()


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


CONFIG_PATH = _get_base_dir() / "config" / "api_keys.json"

SETUP_HELP = (
    "Calling is not configured yet. Create a Telegram bot with @BotFather and "
    "put its token in config/api_keys.json under telegram_bot_token, then open "
    "the bot in Telegram and press Start once."
)

# preferred macOS 'say' voices per language (first installed one wins)
_VOICES = {
    "english": ["Samantha", "Daniel", "Albert"],
    "french":  ["Thomas", "Amelie", "Amélie", "Audrey"],
    "arabic":  ["Majed"],
    "german":  ["Anna"],
    "spanish": ["Monica", "Mónica"],
    "italian": ["Alice"],
    "turkish": ["Yelda"],
}


def _speak_and_log(message: str, player=None):
    if player:
        try:
            player.write_log(f"JARVIS: {message}")
        except Exception:
            pass
    print(f"[CallMe] {message}")


def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_config(data: dict) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(data, indent=4), encoding="utf-8")
    except Exception as e:
        print(f"[CallMe] ⚠️ Could not save config: {e}")


def _api(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def _autodetect_chat_id(token: str) -> str:
    """Find the owner's chat id from the bot's pending updates."""
    try:
        r = requests.get(_api(token, "getUpdates"), timeout=30).json()
        for upd in reversed(r.get("result", [])):
            m = upd.get("message") or {}
            chat = m.get("chat", {})
            if chat.get("type") == "private" and chat.get("id"):
                return str(chat["id"])
    except Exception as e:
        print(f"[CallMe] ⚠️ getUpdates failed: {e}")
    return ""


def _make_voice_note(text: str, language: str) -> Path | None:
    """Voice note in JARVIS's own (Charon) voice, with system TTS fallback."""
    try:
        from actions.tts_voice import make_voice_ogg
    except ImportError:
        from tts_voice import make_voice_ogg
    return make_voice_ogg(text, language)


def call_me(parameters: dict, player=None):
    """
    Rings the owner's phone via their private Telegram bot: repeated loud
    alert messages plus a spoken voice note.

    parameters:
        message  (required) — what to tell the owner
        language (optional) — english, french, arabic, ... (default english)
        repeat   (optional) — number of alert pings, 1-5 (default 3)
    """
    message = str(parameters.get("message", "")).strip()
    if not message:
        msg = "Sir, I need a message for the call."
        _speak_and_log(msg, player)
        return msg

    cfg   = _load_config()
    token = str(cfg.get("telegram_bot_token", "")).strip()
    if not token:
        _speak_and_log(SETUP_HELP, player)
        return SETUP_HELP

    chat_id = str(cfg.get("telegram_chat_id", "")).strip()
    if not chat_id:
        chat_id = _autodetect_chat_id(token)
        if chat_id:
            cfg["telegram_chat_id"] = chat_id
            _save_config(cfg)
        else:
            msg = ("Sir, I can't reach you yet — open your bot in Telegram "
                   "and press Start once, then ask me to call again.")
            _speak_and_log(msg, player)
            return msg

    language = str(parameters.get("language", "english")).lower().strip()
    try:
        repeat = max(1, min(5, int(parameters.get("repeat", 3))))
    except Exception:
        repeat = 3

    sent_any = False
    try:
        # loud alert pings (each one triggers a notification sound)
        for i in range(repeat):
            text = (f"🚨 JARVIS IS CALLING YOU 🚨\n\n{message}"
                    if i == 0 else "🔔 🔔 🔔")
            r = requests.post(_api(token, "sendMessage"),
                              data={"chat_id": chat_id, "text": text},
                              timeout=30)
            sent_any = sent_any or r.ok
            if i < repeat - 1:
                time.sleep(2)

        # spoken voice note
        ogg = _make_voice_note(message, language)
        if ogg:
            with open(ogg, "rb") as f:
                requests.post(_api(token, "sendVoice"),
                              data={"chat_id": chat_id, "caption": "🎙 JARVIS"},
                              files={"voice": ("jarvis.ogg", f, "audio/ogg")},
                              timeout=60)
    except Exception as e:
        if not sent_any:
            msg = f"Sir, I couldn't reach your phone: {e}"
            _speak_and_log(msg, player)
            return msg

    msg = f"I'm ringing your phone now, sir — alert sent: {message[:80]}"
    _speak_and_log(msg, player)
    return msg
