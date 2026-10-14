# actions/send_message.py
# Universal messaging — WhatsApp, Telegram, Instagram
# Keyboard-driven (pyautogui) and cross-platform: opens the app natively per
# OS, verifies it actually has focus before typing, and reports honestly.

import platform
import subprocess
import time
import pyautogui
try:
    from actions.typing_utils import smart_write, smart_hotkey, smart_press
except ImportError:
    from typing_utils import smart_write, smart_hotkey, smart_press
from pathlib import Path

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.08

_OS  = platform.system()
_MOD = "command" if _OS == "Darwin" else "ctrl"   # search/select shortcuts

CONTACTS_PATH = Path(__file__).resolve().parent.parent / "config" / "contacts.json"


def _ax_trusted() -> bool:
    """Can this process send keystrokes? (macOS Accessibility permission)."""
    if _OS != "Darwin":
        return True
    try:
        from ApplicationServices import AXIsProcessTrusted
        return bool(AXIsProcessTrusted())
    except Exception:
        return True          # can't check — assume yes

_NO_AX_MSG = (
    "FAILED — the message was NOT sent: Jarvis has no permission to press "
    "keys. Tell the user to enable it once: System Settings → Privacy & "
    "Security → Accessibility → enable Terminal (then restart Jarvis).")


def _load_contacts() -> dict:
    try:
        import json
        return json.loads(CONTACTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_contact(name: str, phone: str) -> None:
    import json
    d = _load_contacts()
    d[name.lower().strip()] = phone
    CONTACTS_PATH.write_text(json.dumps(d, indent=2, ensure_ascii=False),
                             encoding="utf-8")


def _normalize_phone(raw: str) -> str:
    """'+216 12 345 678' → '21612345678'; '' if not a plausible number."""
    digits = "".join(c for c in (raw or "") if c.isdigit())
    return digits if len(digits) >= 8 else ""


def _resolve_phone(receiver: str, phone_param: str) -> str:
    """Phone for a receiver: explicit param > saved contact > receiver-is-number."""
    p = _normalize_phone(phone_param)
    if p:
        _save_contact(receiver, p)       # remember for next time
        return p
    saved = _load_contacts().get(receiver.lower().strip(), "")
    if saved:
        return _normalize_phone(saved)
    return _normalize_phone(receiver)    # user said the number itself


def _frontmost() -> str:
    """Name of the app that currently has focus (macOS)."""
    if _OS != "Darwin":
        return ""
    try:
        r = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get name of first '
             'application process whose frontmost is true'],
            capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except Exception:
        return ""


def _has_focus(app_name: str) -> bool:
    """True if app_name (fuzzy) is the frontmost app. Non-mac: assume yes."""
    if _OS != "Darwin":
        return True
    front = _frontmost().lower().replace(" ", "")
    want  = app_name.lower().replace(" ", "")
    return bool(front) and (want in front or front in want)


def _focused_role(app_name: str) -> str:
    """Accessibility role of the focused UI element ('' if unknown).
    AXTextArea = WhatsApp's message box; AXTextField = the search box."""
    if _OS != "Darwin":
        return ""
    try:
        r = subprocess.run(
            ["osascript", "-e",
             f'tell application "System Events" to tell process "{app_name}" '
             f'to get role of (value of attribute "AXFocusedUIElement")'],
            capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except Exception:
        return ""


def _open_app(app_name: str) -> bool:
    """Open/focus an app and make sure it actually took focus."""
    try:
        if _OS == "Darwin":
            subprocess.run(["open", "-a", app_name],
                           capture_output=True, timeout=8)
            for _ in range(10):                   # wait up to ~5 s for focus
                time.sleep(0.5)
                if _has_focus(app_name):
                    return True
            print(f"[SendMessage] {app_name} did not take focus "
                  f"(frontmost: {_frontmost() or 'unknown'})")
            return False

        # Windows: start-menu search
        smart_press("win")
        time.sleep(0.4)
        smart_write(app_name, interval=0.04)
        time.sleep(0.5)
        smart_press("enter")
        time.sleep(2.0)
        return True
    except Exception as e:
        print(f"[SendMessage] Could not open {app_name}: {e}")
        return False


def _search_contact(contact: str, platform: str):
    """
    Searches for a contact inside the messaging app.
    Uses Ctrl+F (universal search shortcut) then types contact name.
    """
    time.sleep(0.5)
    smart_hotkey("ctrl", "f")
    time.sleep(0.4)
    smart_hotkey("ctrl", "a")
    smart_write(contact, interval=0.04)
    time.sleep(0.8)
    smart_press("enter")
    time.sleep(0.6)


def _type_and_send(message: str):
    """Types message and sends it."""
    smart_press("tab")
    time.sleep(0.2)
    smart_hotkey("ctrl", "a")
    smart_write(message, interval=0.03)
    time.sleep(0.2)
    smart_press("enter")
    time.sleep(0.3)


def _send_whatsapp(receiver: str, message: str, phone_param: str = "") -> str:
    """
    Sends a WhatsApp message.

    Preferred (deterministic): whatsapp://send?phone=N&text=M — opens the
    EXACT chat with the message pre-filled; one Enter sends it. Needs the
    contact's number (asked once, then saved in config/contacts.json).

    Fallback: keyboard search flow inside the app.
    """
    try:
        phone = _resolve_phone(receiver, phone_param)

        # ── 1st choice (macOS): click the chat in the sidebar directly ──
        # Reads the chat list via Accessibility — no phone number needed.
        if _OS == "Darwin" and not phone:
            if not _open_app("WhatsApp"):
                return ("FAILED — could not bring WhatsApp to the front. "
                        "Tell the user the message was NOT sent.")
            time.sleep(0.8)
            try:
                from actions.whatsapp_ui import send_via_chat_list
            except ImportError:
                from whatsapp_ui import send_via_chat_list
            r = send_via_chat_list(receiver, message)
            print(f"[SendMessage] chat-list: {r[:80]}")

            if r.startswith("SENT"):
                return r
            if r.startswith("AMBIGUOUS:"):
                names = r.split(":", 1)[1].replace("|", " | ")
                return (f"MULTIPLE MATCHES for '{receiver}': {names}. "
                        f"Call show_option_picker with these names "
                        f"(on_select 'say'); when the user picks one, call "
                        f"send_message again with that exact receiver.")
            if r.startswith("UNSURE"):
                return r
            if r.startswith("NOTFOUND"):
                return (f"'{receiver}' is not in the recent WhatsApp chats "
                        f"({r.split('—', 1)[-1].strip()}). Ask the user "
                        f"which chat it is, or for the phone number (with "
                        f"country code) to message them directly.")
            # FAILED → fall through to the other strategies
            print(f"[SendMessage] falling back after: {r[:60]}")

        if phone:
            import urllib.parse
            try:
                from actions.whatsapp_ui import split_message
            except ImportError:
                from whatsapp_ui import split_message
            parts = split_message(message)    # one bubble per thought
            url = (f"whatsapp://send?phone={phone}"
                   f"&text={urllib.parse.quote(parts[0])}")
            subprocess.run(["open", url] if _OS == "Darwin"
                           else ["cmd", "/c", "start", "", url.replace("&", "^&")],
                           capture_output=True, timeout=8)
            for _ in range(12):               # wait for WhatsApp to come up
                time.sleep(0.5)
                if _has_focus("WhatsApp"):
                    break
            else:
                return ("FAILED — WhatsApp did not open. Tell the user the "
                        "message was NOT sent.")
            time.sleep(1.2)                   # let the pre-filled chat render
            if not _ax_trusted():
                return (f"PARTIAL — opened {receiver}'s chat with the message "
                        f"pre-filled, but Jarvis can't press Enter (no "
                        f"Accessibility permission: System Settings → Privacy "
                        f"& Security → Accessibility → enable Terminal). Tell "
                        f"the user to just press Enter to send it.")
            smart_press("enter")
            time.sleep(0.5)
            for part in parts[1:]:
                smart_write(part, interval=0.03)
                time.sleep(0.3)
                smart_press("enter")
                time.sleep(0.5)
            n = f" ({len(parts)} messages)" if len(parts) > 1 else ""
            return (f"Opened {receiver}'s chat directly ({phone}) and sent "
                    f"the message{n}.")

        # ── no number known: keyboard search flow ──
        if not _ax_trusted():
            return (_NO_AX_MSG + f" ALTERNATIVELY: ask the user for "
                    f"{receiver}'s phone number (with country code) and call "
                    f"send_message again with it in 'phone' — that path needs "
                    f"no typing permission to open the chat.")

        if not _open_app("WhatsApp"):
            return ("FAILED — could not bring WhatsApp to the front. "
                    "Tell the user the message was NOT sent.")

        time.sleep(1.5)

        smart_hotkey(_MOD, "f")
        time.sleep(0.4)
        smart_hotkey(_MOD, "a")
        smart_write(receiver, interval=0.04)
        time.sleep(1.2)

        # Enter in the search box does NOT open a chat on macOS — go DOWN
        # into the results list first, then Enter opens the chat.
        smart_press("down")
        time.sleep(0.4)
        smart_press("enter")
        time.sleep(1.0)

        if not _has_focus("WhatsApp"):
            return ("FAILED — WhatsApp lost focus before the message was "
                    "typed. Tell the user the message was NOT sent.")

        # Only type if focus really moved into the MESSAGE box — otherwise
        # we would be typing into the search field (seen in the wild).
        role = _focused_role("WhatsApp")
        if role and role != "AXTextArea":
            smart_press("escape")            # clear the stuck search
            return (f"FAILED — the chat for '{receiver}' did not open (focus "
                    f"stayed on {role}). The message was NOT sent. Ask the "
                    f"user for {receiver}'s phone number (with country code) "
                    f"and retry with it in 'phone' — that path is reliable.")

        smart_write(message, interval=0.03)
        time.sleep(0.3)
        smart_press("enter")
        time.sleep(0.3)

        return (f"Typed the message into the chat matching '{receiver}' and "
                f"pressed send in WhatsApp (top search result). TIP: for "
                f"reliable sending, ask the user for {receiver}'s number once "
                f"and pass it as 'phone' next time.")

    except Exception as e:
        return f"WhatsApp error: {e}"


def _send_instagram(receiver: str, message: str) -> str:
    """
    Sends an Instagram DM via browser (instagram.com).
    Steps: Open Chrome → Go to instagram.com/direct → Search contact → Send
    """
    try:
        try:
            from actions.web_open import open_url
        except ImportError:
            from web_open import open_url

        open_url("https://www.instagram.com/direct/new/")
        time.sleep(3.5)

        smart_write(receiver, interval=0.05)
        time.sleep(1.5)

        smart_press("down")
        time.sleep(0.3)
        smart_press("enter")
        time.sleep(0.5)

        for _ in range(3):
            smart_press("tab")
            time.sleep(0.1)
        smart_press("enter")
        time.sleep(1.5)

        smart_write(message, interval=0.04)
        time.sleep(0.2)
        smart_press("enter")

        return f"Message sent to {receiver} via Instagram."

    except Exception as e:
        return f"Instagram error: {e}"

def _send_telegram(receiver: str, message: str) -> str:
    """Sends a Telegram message via the desktop app."""
    try:
        if not _open_app("Telegram"):
            return ("FAILED — could not bring Telegram to the front. "
                    "Tell the user the message was NOT sent.")

        time.sleep(1.5)

        smart_hotkey(_MOD, "f")
        time.sleep(0.4)
        smart_write(receiver, interval=0.04)
        time.sleep(1.0)
        smart_press("enter")
        time.sleep(0.8)

        if not _has_focus("Telegram"):
            return ("FAILED — Telegram lost focus before the message was "
                    "typed. Tell the user the message was NOT sent.")

        smart_write(message, interval=0.03)
        time.sleep(0.2)
        smart_press("enter")

        return f"Typed and sent the message to '{receiver}' in Telegram."

    except Exception as e:
        return f"Telegram error: {e}"



def _send_generic(platform: str, receiver: str, message: str) -> str:
    """
    For any other platform not explicitly supported.
    Opens the app, searches for contact, types and sends.
    Works for: Messenger, Discord, Signal, etc.
    """
    try:
        if not _open_app(platform):
            return (f"FAILED — could not bring {platform} to the front. "
                    f"Tell the user the message was NOT sent.")

        time.sleep(1.5)
        smart_hotkey(_MOD, "f")
        time.sleep(0.4)
        smart_write(receiver, interval=0.04)
        time.sleep(1.0)
        smart_press("enter")
        time.sleep(0.8)
        if not _has_focus(platform):
            return (f"FAILED — {platform} lost focus before the message was "
                    f"typed. Tell the user the message was NOT sent.")
        smart_write(message, interval=0.03)
        time.sleep(0.2)
        smart_press("enter")

        return f"Typed and sent the message to '{receiver}' in {platform}."

    except Exception as e:
        return f"{platform} error: {e}"

def send_message(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None
) -> str:
    """
    Called from main.py.

    parameters:
        receiver     : Contact name to send to
        message_text : The message content
        platform     : whatsapp | instagram | telegram | <any app name>
                       Default: whatsapp
    """
    params       = parameters or {}
    receiver     = params.get("receiver", "").strip()
    message_text = params.get("message_text", "").strip()
    phone        = str(params.get("phone", "") or "").strip()
    platform     = params.get("platform", "whatsapp").strip().lower()

    if not receiver:
        return "Please specify who to send the message to, sir."
    if not message_text:
        return "Please specify what message to send, sir."

    print(f"[SendMessage] 📨 {platform} → {receiver}: {message_text[:40]}")
    if player:
        player.write_log(f"[msg] Sending to {receiver} via {platform}...")

    if "whatsapp" in platform or "wp" in platform or "wapp" in platform:
        result = _send_whatsapp(receiver, message_text, phone)

    elif "instagram" in platform or "ig" in platform or "insta" in platform:
        result = _send_instagram(receiver, message_text)

    elif "telegram" in platform or "tg" in platform:
        result = _send_telegram(receiver, message_text)

    else:
        result = _send_generic(platform, receiver, message_text)

    print(f"[SendMessage] ✅ {result}")
    if player:
        player.write_log(f"[msg] {result}")

    return result