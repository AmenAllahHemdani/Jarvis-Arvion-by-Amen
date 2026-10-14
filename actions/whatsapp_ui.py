# actions/whatsapp_ui.py
# JARVIS ARVION — drive the WhatsApp macOS app through the Accessibility API
#
# The chat sidebar is exposed as an AXGroup "List of chats" whose children
# are AXButtons named by contact. So instead of fragile search-box typing we:
#   read the list → match the name → AXPress the right chat → focus the
#   "Compose message" box → paste the text → Enter.
#
# Verified against WhatsApp 2.26.x (Electron) on macOS.

import re
import subprocess
import time

try:
    from actions.typing_utils import smart_write, smart_press
except ImportError:
    from typing_utils import smart_write, smart_press

_UNREAD_RE = re.compile(r",\s*(\d+)\s+unread.*$", re.I)


def split_message(text: str) -> list[str]:
    """One WhatsApp bubble per thought, the way people actually chat:
    'Waa Khalil! Fi dar positive, kol chay mriguel' →
    ['Waa Khalil!', 'Fi dar positive', 'kol chay mriguel'].
    Splits on sentence ends (keeping !?.) and commas (dropping them)."""
    parts = []
    for sentence in re.split(r"(?<=[.!?؟…])\s+|\n+", (text or "").strip()):
        for piece in re.split(r"\s*[,،;]\s+", sentence):
            piece = piece.strip(" \t,،;")
            if piece:
                parts.append(piece)
    return parts[:12] or [text.strip()]


def _ax():
    from ApplicationServices import (
        AXUIElementCreateApplication,
        AXUIElementCopyAttributeValue,
        AXUIElementPerformAction,
        AXUIElementSetAttributeValue,
    )
    return (AXUIElementCreateApplication, AXUIElementCopyAttributeValue,
            AXUIElementPerformAction, AXUIElementSetAttributeValue)


def _attr(copy_fn, el, name):
    try:
        err, val = copy_fn(el, name, None)
        return val if err == 0 else None
    except Exception:
        return None


def _clean(s: str) -> str:
    return (s or "").replace("‎", "").replace("‏", "").strip()


def _wa_pid() -> int | None:
    try:
        out = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get unix id of process "WhatsApp"'],
            capture_output=True, text=True, timeout=5).stdout.strip()
        return int(out)
    except Exception:
        return None


class _WaApp:
    def __init__(self):
        (self._create, self._copy,
         self._perform, self._set) = _ax()
        pid = _wa_pid()
        self.root = self._create(pid) if pid else None

    def a(self, el, name):
        return _attr(self._copy, el, name)

    def find(self, pred, el=None, depth=0, out=None, max_hits=30):
        if out is None:
            out = []
        if el is None:
            el = self.root
        if depth > 45 or len(out) >= max_hits:
            return out
        try:
            if pred(el):
                out.append(el)
        except Exception:
            pass
        for k in (self.a(el, "AXChildren") or []):
            self.find(pred, k, depth + 1, out, max_hits)
        return out

    # ── chat list ──

    def chat_rows(self) -> list:
        """[(clean name, raw description, element, selected)] per sidebar
        chat. Raw description carries the unread badge ('Mama, 2 unread
        messages'); the currently OPEN chat is an AXStaticText with
        AXSelected=True instead of an AXButton."""
        lists = self.find(
            lambda e: self.a(e, "AXRole") == "AXGroup"
            and "list of chats" in _clean(self.a(e, "AXDescription")).lower(),
            max_hits=1)
        if not lists:
            return []
        rows = []
        for c in (self.a(lists[0], "AXChildren") or []):
            role = self.a(c, "AXRole")
            if role not in ("AXButton", "AXStaticText"):
                continue
            raw = _clean(self.a(c, "AXDescription"))
            name = _UNREAD_RE.sub("", raw).strip()
            low = name.lower()
            if not name or low.startswith("archived") or "encrypted" in low:
                continue
            rows.append((name, raw, c, bool(self.a(c, "AXSelected"))))
        return rows

    def chat_buttons(self) -> list:
        return [(n, el) for n, _r, el, _s in self.chat_rows()]

    def messages_group(self):
        groups = self.find(
            lambda e: self.a(e, "AXRole") == "AXGroup"
            and _clean(self.a(e, "AXDescription")).lower()
                .startswith("messages in chat with"),
            max_hits=1)
        return groups[0] if groups else None

    def compose_box(self):
        boxes = self.find(
            lambda e: self.a(e, "AXRole") == "AXTextArea"
            and "compose" in _clean(self.a(e, "AXDescription")).lower(),
            max_hits=3)
        if boxes:
            return boxes[-1]
        boxes = self.find(lambda e: self.a(e, "AXRole") == "AXTextArea",
                          max_hits=3)
        return boxes[-1] if boxes else None

    def press(self, el) -> bool:
        try:
            return self._perform(el, "AXPress") == 0
        except Exception:
            return False

    def focus(self, el) -> bool:
        try:
            self._set(el, "AXFocused", True)
            time.sleep(0.3)
            return bool(self.a(el, "AXFocused"))
        except Exception:
            return False


def _match(chats: list, receiver: str) -> list:
    """Chats matching the spoken name: exact first, then substring/word."""
    q = _clean(receiver).casefold()
    exact = [(n, el) for n, el in chats if n.casefold() == q]
    if exact:
        return exact
    sub = [(n, el) for n, el in chats if q in n.casefold()]
    if sub:
        return sub
    words = [w for w in q.split() if len(w) >= 3]
    return [(n, el) for n, el in chats
            if words and all(w in n.casefold() for w in words)]


def _parse_message(raw: str, partner: str) -> dict | None:
    """
    'Your message, <text>, <time>, Sent to X, Read'   → You
    'message, <text>, <time>, Received from X'        → partner
    Reply bubbles prefix a 'Replying to …' line — the core line wins.
    """
    core = ""
    for line in raw.split("\n"):
        line = line.strip()
        if line.lower().startswith(("your message,", "message,")):
            core = line
            break
    if not core:
        return None

    if core.lower().startswith("your message,"):
        sender, rest = "You", core[len("your message,"):].strip()
    else:
        sender, rest = partner, core[len("message,"):].strip()

    toks = rest.split(", ")
    # from the right: [... text ..., <time>, Sent to X / Received from X, Read]
    anchor = next((i for i in range(len(toks) - 1, -1, -1)
                   if toks[i].startswith(("Sent to", "Received from"))), -1)
    if anchor >= 2:
        text = ", ".join(toks[:anchor - 1])
        when = toks[anchor - 1]
    elif anchor == 1:
        text, when = toks[0], ""
    else:
        text, when = rest, ""
    # times come as '4:33 PM' or '12Octoberat6:26 AM' — keep just the clock
    m = re.search(r"\d{1,2}:\d{2}\s*\S*$", when)
    when = m.group(0).replace(" ", " ") if m else when
    return {"sender": sender, "text": text.strip(), "time": when}


def _ready_app(timeout_tries: int = 18):
    """Foreground WhatsApp and wait for its lazy AX tree. → (app, rows)."""
    subprocess.run(["open", "-a", "WhatsApp"], capture_output=True, timeout=8)
    app, rows, restarted = None, [], False
    for i in range(timeout_tries):
        app = _WaApp()
        if app.root is not None:
            rows = app.chat_rows()
            if rows:
                return app, rows
        if i == 6 and not restarted:
            # running but windowless/stuck Electron → one clean relaunch
            wins = app.a(app.root, "AXWindows") if app.root is not None else None
            if not wins:
                print("[WhatsApp] no window — relaunching the app")
                subprocess.run(["osascript", "-e",
                                'tell application "WhatsApp" to quit'],
                               capture_output=True, timeout=8)
                time.sleep(2.0)
                subprocess.run(["open", "-a", "WhatsApp"],
                               capture_output=True, timeout=8)
                restarted = True
        time.sleep(0.7)
    return app, rows


def list_unread() -> str:
    """Names + counts of chats with unread messages."""
    _, rows = _ready_app()
    if not rows:
        return "FAILED — could not read the WhatsApp chat list."
    unread = []
    for name, raw, _el, _sel in rows:
        m = _UNREAD_RE.search(raw)
        if m:
            unread.append(f"{name} ({m.group(1)} unread)")
    if not unread:
        return "No unread WhatsApp messages."
    return "UNREAD CHATS: " + " | ".join(unread)


def read_chat(chat: str, limit: int = 8) -> str:
    """Open a chat by name and return its last messages."""
    app, rows = _ready_app()
    if not rows:
        return "FAILED — could not read the WhatsApp chat list."

    hits = _match([(n, (el, sel)) for n, _r, el, sel in rows], chat)
    if not hits:
        names = ", ".join(n for n, _r, _e, _s in rows[:10])
        return f"NOTFOUND — '{chat}' not in recent chats: {names}"
    if len(hits) > 1:
        return "AMBIGUOUS:" + "|".join(n for n, _ in hits[:6])

    name, (btn, selected) = hits[0]
    if not selected:                     # already-open chat needs no click
        app.press(btn)
        time.sleep(1.5)

    group = None
    for _ in range(8):
        app = _WaApp()
        group = app.messages_group()
        if group is not None:
            break
        time.sleep(0.7)
    if group is None:
        return f"FAILED — opened '{name}' but could not read the messages."

    msgs = []
    for el in (app.a(group, "AXChildren") or []):
        if app.a(el, "AXRole") != "AXStaticText":
            continue
        raw = _clean(str(app.a(el, "AXValue") or "")) \
            or _clean(app.a(el, "AXDescription") or "")
        parsed = _parse_message(raw, name)
        if parsed and parsed["text"]:
            msgs.append(parsed)
    if not msgs:
        return f"Opened '{name}' but found no readable messages."

    lines = [f"[{m['sender']}{' ' + m['time'] if m['time'] else ''}] {m['text']}"
             for m in msgs[-limit:]]
    return f"LAST MESSAGES with {name}:\n" + "\n".join(lines)


def whatsapp_chats(parameters=None, response=None, player=None,
                   session_memory=None) -> str:
    """Tool: read WhatsApp — unread overview or a chat's last messages."""
    p      = parameters or {}
    action = (p.get("action") or "unread").lower().strip()
    chat   = (p.get("chat") or "").strip()
    try:
        limit = max(1, min(20, int(p.get("limit") or 8)))
    except (TypeError, ValueError):
        limit = 8

    if player:
        player.write_log(f"[WhatsApp] {action}{' ' + chat if chat else ''}")

    if action in ("unread", "check", "list_unread", "new"):
        r = list_unread()
        if r.startswith("UNREAD"):
            return (r + "\n\n(Report briefly; offer to read one — "
                    "whatsapp_chats action 'read' with the chat name.)")
        return r

    if action in ("read", "last", "last_messages", "messages"):
        if not chat:
            return "Which chat should I read, sir? Provide 'chat'."
        r = read_chat(chat, limit)
        if r.startswith("AMBIGUOUS:"):
            names = r.split(":", 1)[1].replace("|", " | ")
            return (f"MULTIPLE MATCHES for '{chat}': {names}. "
                    f"Call show_option_picker with these names "
                    f"(on_select 'say'); then call whatsapp_chats again "
                    f"with the exact chat name.")
        if r.startswith("LAST MESSAGES"):
            return (r + "\n\n(Answer the user's question about these "
                    "naturally — don't read every line unless asked. To "
                    "reply, call send_message platform 'WhatsApp'.)")
        return r

    return f"Unknown action '{action}'. Use: unread | read"


def send_via_chat_list(receiver: str, message: str) -> str:
    """
    Click the receiver's chat in the sidebar and send the message.
    Returns a status string for the model; 'AMBIGUOUS:<names>' when several
    chats match; 'NOTFOUND' when the contact isn't in the visible list.
    """
    try:
        # Electron builds its accessibility tree lazily: the first query
        # triggers it and the tree appears a few seconds later — so poll.
        app, rows = _ready_app()
        if app is None or app.root is None:
            return "FAILED — WhatsApp is not running."
        if not rows:
            return "FAILED — could not read the chat list."

        hits = _match([(n, (el, sel)) for n, _r, el, sel in rows], receiver)
        if not hits:
            names = ", ".join(n for n, _r, _e, _s in rows[:10])
            return f"NOTFOUND — visible chats: {names}"
        if len(hits) > 1:
            return "AMBIGUOUS:" + "|".join(n for n, _ in hits[:6])

        name, (btn, selected) = hits[0]
        if not selected and not app.press(btn):
            return f"FAILED — could not open the chat '{name}'."
        time.sleep(1.2)

        box = app.compose_box()
        if box is None or not app.focus(box):
            return (f"FAILED — opened '{name}' but could not focus the "
                    f"message box. Message NOT sent.")

        parts = split_message(message)
        for part in parts:               # one bubble per thought
            smart_write(part, interval=0.03)
            time.sleep(0.3)
            smart_press("enter")
            time.sleep(0.5)

        # after sending, the compose box empties — a cheap confirmation
        leftover = _clean(str(app.a(box, "AXValue") or ""))
        if leftover:
            return (f"UNSURE — typed the message in '{name}' and pressed "
                    f"Enter, but text is still in the box. Ask the user to "
                    f"check WhatsApp.")
        n = f" ({len(parts)} messages)" if len(parts) > 1 else ""
        return f"SENT to '{name}' via WhatsApp{n}."

    except Exception as e:
        return f"FAILED — WhatsApp UI error: {e}"
