import atexit
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

import requests

# live-app integration: when the JARVIS app is running, Telegram messages
# are injected into the live session (full tool access) instead of being
# answered by the plain chat model.
_app             = None     # JarvisLive instance, set by main.py
_awaiting_since  = 0.0      # time of last injected Telegram command
_awaiting_lock   = threading.Lock()
_we_took_over    = False    # True when we removed the cloud webhook


def set_app(jarvis) -> None:
    """Called by main.py so Telegram commands run through the live session."""
    global _app
    _app = jarvis


def on_turn_output(text: str) -> None:
    """Called by main.py after each assistant turn; relays the answer to
    Telegram when the turn was triggered from Telegram."""
    global _awaiting_since
    with _awaiting_lock:
        if time.time() - _awaiting_since > 120:
            return
        _awaiting_since = 0.0
    if not text:
        return
    cfg = _config()
    token, chat_id = cfg.get("telegram_bot_token", ""), cfg.get("telegram_chat_id", "")
    if token and chat_id:
        threading.Thread(target=_send_reply, args=(token, chat_id, text),
                         daemon=True).start()


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


CONFIG_PATH = _get_base_dir() / "config" / "api_keys.json"

_started = False


def _config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_chat_id(chat_id: str) -> None:
    try:
        data = _config()
        data["telegram_chat_id"] = chat_id
        CONFIG_PATH.write_text(json.dumps(data, indent=4), encoding="utf-8")
    except Exception:
        pass


def _api(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def _generate_reply(user_text: str) -> str:
    try:
        from or_client import client
    except Exception as e:
        return f"Sir, my reasoning module is unavailable right now: {e}"

    memory_ctx = ""
    try:
        from memory.memory_manager import load_memory, format_memory_for_prompt
        memory_ctx = format_memory_for_prompt(load_memory()) or ""
    except Exception:
        pass

    system = (
        "You are JARVIS, the user's personal AI assistant, replying on Telegram "
        "while they are away from their computer. Be helpful, concise and "
        "personable — a few sentences unless more detail is asked. Reply in the "
        "same language the user wrote in.\n"
        + (f"\nWhat you remember about the user:\n{memory_ctx}" if memory_ctx else "")
    )
    try:
        reply = client.chat(user_text, system=system, max_tokens=1024)
        return (reply or "").strip() or "Sir, I have no answer for that right now."
    except Exception as e:
        return f"Sir, I couldn't think that through: {str(e)[:120]}"


def _send_reply(token: str, chat_id: str, reply: str) -> None:
    try:
        requests.post(_api(token, "sendMessage"),
                      data={"chat_id": chat_id, "text": reply[:4000]},
                      timeout=30)
    except Exception as e:
        print(f"[Bridge] ⚠️ sendMessage failed: {e}")
        return

    # voice notes only when telegram_voice_replies is enabled in config
    if not _config().get("telegram_voice_replies", False):
        return
    if len(reply) <= 500:
        try:
            from actions.tts_voice import make_voice_ogg
            ogg = make_voice_ogg(reply)
            if ogg:
                with open(ogg, "rb") as f:
                    requests.post(_api(token, "sendVoice"),
                                  data={"chat_id": chat_id, "caption": "🎙 JARVIS"},
                                  files={"voice": ("jarvis.ogg", f, "audio/ogg")},
                                  timeout=60)
        except Exception as e:
            print(f"[Bridge] ⚠️ voice reply failed: {e}")


def _take_over_from_cloud(token: str) -> None:
    """Remove the cloud webhook so the local app (with full tool access)
    handles Telegram while it runs; the webhook is restored on exit."""
    global _we_took_over
    try:
        requests.post(_api(token, "deleteWebhook"),
                      data={"drop_pending_updates": "false"}, timeout=15)
        _we_took_over = True
        print("[Bridge] 💻 Local JARVIS took over Telegram (full computer control).")
    except Exception as e:
        print(f"[Bridge] ⚠️ Could not remove cloud webhook: {e}")


def _restore_cloud_webhook() -> None:
    """On app exit, hand Telegram back to the cloud worker."""
    if not _we_took_over:
        return
    cfg    = _config()
    token  = str(cfg.get("telegram_bot_token", "")).strip()
    url    = str(cfg.get("telegram_webhook_url", "")).strip()
    secret = str(cfg.get("telegram_webhook_secret", "")).strip()
    if not token or not url:
        return
    try:
        requests.post(_api(token, "setWebhook"),
                      data={"url": url, "secret_token": secret}, timeout=15)
        print("[Bridge] ☁️ Handed Telegram back to the cloud bridge.")
    except Exception as e:
        print(f"[Bridge] ⚠️ Could not restore cloud webhook: {e}")


atexit.register(_restore_cloud_webhook)


def _install_signal_handlers() -> None:
    """atexit never runs on SIGTERM/SIGHUP (force-quit, kill, logout) —
    restore the cloud webhook before dying, or Telegram goes dark."""
    def _handler(signum, frame):
        _restore_cloud_webhook()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    for name in ("SIGTERM", "SIGHUP", "SIGINT"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            pass  # not in the main thread / unsupported platform


def _loop() -> None:
    global _awaiting_since
    offset = None
    token = str(_config().get("telegram_bot_token", "")).strip()
    if token:
        _take_over_from_cloud(token)
    print("[Bridge] 📨 Telegram bridge started — messages to the bot get auto-replies.")
    while True:
        cfg    = _config()
        token  = str(cfg.get("telegram_bot_token", "")).strip()
        owner  = str(cfg.get("telegram_chat_id", "")).strip()
        if not token:
            time.sleep(30)
            continue
        try:
            params = {"timeout": 25}
            if offset is not None:
                params["offset"] = offset
            r = requests.get(_api(token, "getUpdates"), params=params, timeout=35).json()
            for upd in r.get("result", []):
                offset = upd["update_id"] + 1
                m    = upd.get("message") or {}
                chat = m.get("chat", {})
                if chat.get("type") != "private":
                    continue
                chat_id = str(chat.get("id", ""))
                if not owner:
                    owner = chat_id
                    _save_chat_id(owner)
                if chat_id != owner:
                    continue                      # ignore strangers
                text = (m.get("text") or "").strip()
                if not text:
                    _send_reply_text_only = (
                        "Sir, I can only read text messages here for now."
                    )
                    requests.post(_api(token, "sendMessage"),
                                  data={"chat_id": chat_id,
                                        "text": _send_reply_text_only},
                                  timeout=30)
                    continue
                if text.startswith("/start"):
                    requests.post(_api(token, "sendMessage"),
                                  data={"chat_id": chat_id,
                                        "text": "At your service, sir. Message me "
                                                "anytime and I'll answer."},
                                  timeout=30)
                    continue
                print(f"[Bridge] 💬 Owner: {text[:60]}")
                if _app is not None and getattr(_app, "session", None) is not None:
                    # full JARVIS: inject into the live session — tools included
                    global _awaiting_since
                    with _awaiting_lock:
                        _awaiting_since = time.time()
                    _app._on_text_command(text)
                    print("[Bridge] ⚙️ Routed to live JARVIS (tools enabled)")
                else:
                    reply = _generate_reply(text)
                    print(f"[Bridge] 🤖 JARVIS: {reply[:60]}")
                    _send_reply(token, chat_id, reply)
        except Exception as e:
            print(f"[Bridge] ⚠️ {str(e)[:100]} — retrying in 10s")
            time.sleep(10)


def start_telegram_bridge() -> None:
    """Start the bridge once, as a daemon thread (no-op without a token)."""
    global _started
    if _started:
        return
    if not str(_config().get("telegram_bot_token", "")).strip():
        print("[Bridge] Telegram bot token not configured — bridge not started.")
        return
    _started = True
    _install_signal_handlers()
    threading.Thread(target=_loop, daemon=True, name="telegram-bridge").start()
