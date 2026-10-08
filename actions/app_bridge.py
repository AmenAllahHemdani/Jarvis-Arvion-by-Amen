# actions/app_bridge.py
#
# Bridge between the JARVIS mobile app (Flutter) and the local JARVIS.
#
# The mobile app talks to the Cloudflare worker; the worker queues messages
# in KV. While JARVIS runs on this computer, this bridge polls the queue
# (which doubles as the "desktop online" heartbeat), injects each message
# into the live session — full tool access — and posts the answer back.
# When the computer is off, the worker answers the app directly (cloud AI),
# exactly like the Telegram bridge.
#
# Reuses telegram_* config keys: telegram_webhook_url (worker base URL)
# and telegram_webhook_secret (shared secret).

import threading
import time

import requests

try:
    from actions.telegram_bridge import _config, _generate_reply
except ImportError:
    from telegram_bridge import _config, _generate_reply

# The worker long-polls: /desktop/poll is held open up to 25s and returns
# the instant a phone message arrives, so we reconnect immediately.
POLL_TIMEOUT  = 35      # request timeout — must exceed the worker's 25s hold
REPLY_WINDOW  = 120     # seconds to wait for a live-session answer

_app            = None   # JarvisLive instance, set by main.py
_awaiting_id    = None   # worker message id awaiting a live-session reply
_awaiting_since = 0.0
_awaiting_lock  = threading.Lock()
_started        = False


def set_app(jarvis) -> None:
    """Called by main.py so app commands run through the live session."""
    global _app
    _app = jarvis


def _base_url() -> str:
    return str(_config().get("telegram_webhook_url", "")).strip().rstrip("/")


def _secret() -> str:
    return str(_config().get("telegram_webhook_secret", "")).strip()


def _headers() -> dict:
    return {"X-App-Secret": _secret()}


def _post_reply(msg_id: str, reply: str) -> None:
    base = _base_url()
    if not base or not reply:
        return
    try:
        requests.post(f"{base}/desktop/reply",
                      json={"id": msg_id, "reply": reply[:4000]},
                      headers=_headers(), timeout=15)
        print(f"[AppBridge] 📱 Replied to app ({msg_id[:8]}…)")
    except Exception as e:
        print(f"[AppBridge] ⚠️ reply failed: {e}")


def on_turn_output(text: str) -> None:
    """Called by main.py after each assistant turn; relays the answer to the
    mobile app when the turn was triggered from the app."""
    global _awaiting_id, _awaiting_since
    with _awaiting_lock:
        if _awaiting_id is None or time.time() - _awaiting_since > REPLY_WINDOW:
            _awaiting_id = None
            return
        msg_id, _awaiting_id = _awaiting_id, None
    if text:
        threading.Thread(target=_post_reply, args=(msg_id, text),
                         daemon=True).start()


def _handle_message(msg: dict) -> None:
    global _awaiting_id, _awaiting_since
    msg_id = str(msg.get("id", ""))
    text   = str(msg.get("text", "")).strip()
    if not msg_id or not text:
        return
    print(f"[AppBridge] 💬 App: {text[:60]}")

    if _app is not None and getattr(_app, "session", None) is not None:
        with _awaiting_lock:
            _awaiting_id    = msg_id
            _awaiting_since = time.time()
        _app._on_text_command(text)
        print("[AppBridge] ⚙️ Routed to live JARVIS (tools enabled)")
    else:
        reply = _generate_reply(text)
        _post_reply(msg_id, reply)


def _loop() -> None:
    print("[AppBridge] 📱 Mobile app bridge started — polling worker queue.")
    while True:
        base = _base_url()
        if not base or not _secret():
            time.sleep(30)
            continue
        try:
            r = requests.get(f"{base}/desktop/poll",
                             headers=_headers(), timeout=POLL_TIMEOUT).json()
            for msg in r.get("messages", []):
                _handle_message(msg)
            time.sleep(0.1)   # long-poll: the server does the waiting
        except Exception as e:
            print(f"[AppBridge] ⚠️ {str(e)[:100]} — retrying in 10s")
            time.sleep(10)


def start_app_bridge() -> None:
    """Start the bridge once, as a daemon thread (no-op without worker config)."""
    global _started
    if _started:
        return
    cfg = _config()
    if not str(cfg.get("telegram_webhook_url", "")).strip():
        print("[AppBridge] Worker URL not configured — app bridge not started.")
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name="app-bridge").start()
