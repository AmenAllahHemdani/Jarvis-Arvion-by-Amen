"""Layout-aware keyboard helpers.

pyautogui assumes a US QWERTY layout: on AZERTY (French/Belgian) or QWERTZ
(German/Swiss) keyboards it types the wrong characters and letter shortcuts
land on the wrong keys (on an AZERTY Mac, Cmd+A becomes Cmd+Q — quit!).

This module detects the active OS keyboard layout and provides drop-in
replacements:
    smart_write(text)     — types text correctly on any layout (clipboard paste)
    smart_press(key)      — presses a key, remapping single letters per layout
    smart_hotkey(*keys)   — hotkey with per-layout letter remapping
"""

import platform
import subprocess
import time

import pyautogui
import pyperclip

_OS = platform.system()

# For each wanted character, the US-QWERTY character sitting on the physical
# key that produces it under the given layout (only keys that moved).
_AZERTY_KEYMAP = {"a": "q", "q": "a", "z": "w", "w": "z", "m": ";"}
_QWERTZ_KEYMAP = {"z": "y", "y": "z"}

_cached_layout: str | None = None


def detect_layout(refresh: bool = False) -> str:
    """Return the active keyboard layout: 'azerty', 'qwertz' or 'qwerty'."""
    global _cached_layout
    if _cached_layout is not None and not refresh:
        return _cached_layout

    layout = "qwerty"
    try:
        if _OS == "Darwin":
            out = ""
            for key in ("AppleCurrentKeyboardLayoutInputSourceID",
                        "AppleSelectedInputSources"):
                r = subprocess.run(
                    ["defaults", "read", "com.apple.HIToolbox", key],
                    capture_output=True, text=True, timeout=5,
                )
                out += r.stdout.lower()
            if any(k in out for k in ("french", "belgian", "azerty")):
                layout = "azerty"
            elif any(k in out for k in ("german", "swiss", "austrian", "qwertz")):
                layout = "qwertz"

        elif _OS == "Windows":
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            thread_id = user32.GetWindowThreadProcessId(hwnd, None)
            lang = user32.GetKeyboardLayout(thread_id) & 0xFFFF
            if lang in (0x040C, 0x080C, 0x140C):        # French, Belgian, Luxembourg
                layout = "azerty"
            elif lang in (0x0407, 0x0807, 0x0C07):      # German, Swiss, Austrian
                layout = "qwertz"

        else:  # Linux
            r = subprocess.run(["setxkbmap", "-query"],
                               capture_output=True, text=True, timeout=5)
            for line in r.stdout.lower().splitlines():
                if line.startswith("layout"):
                    codes = line.split(":", 1)[1].strip()
                    first = codes.split(",")[0].strip()
                    if first in ("fr", "be"):
                        layout = "azerty"
                    elif first in ("de", "ch", "at"):
                        layout = "qwertz"
    except Exception:
        pass

    _cached_layout = layout
    print(f"[Keyboard] Detected layout: {layout}")
    return layout


def _keymap() -> dict:
    layout = detect_layout()
    if layout == "azerty":
        return _AZERTY_KEYMAP
    if layout == "qwertz":
        return _QWERTZ_KEYMAP
    return {}


def smart_write(text, interval: float = 0.03) -> None:
    """Type text correctly whatever the active keyboard layout.

    QWERTY + plain ASCII uses pyautogui.write as before; anything else is
    pasted through the clipboard, which is layout-independent and also
    handles accents and non-Latin characters.
    """
    text = str(text)
    if not text:
        return

    if detect_layout() == "qwerty" and text.isascii():
        pyautogui.write(text, interval=interval)
        return

    try:
        old_clip = None
        try:
            old_clip = pyperclip.paste()
        except Exception:
            pass
        pyperclip.copy(text)
        time.sleep(0.15)
        pyautogui.hotkey("command" if _OS == "Darwin" else "ctrl", "v")
        time.sleep(0.25)
        if old_clip is not None:
            try:
                pyperclip.copy(old_clip)
            except Exception:
                pass
    except Exception as e:
        print(f"[Keyboard] Clipboard paste failed ({e}), falling back to raw typing")
        keymap = _keymap()
        remapped = "".join(keymap.get(ch, ch) for ch in text)
        pyautogui.write(remapped, interval=interval)


def smart_press(key: str, presses: int = 1) -> None:
    """pyautogui.press with per-layout remapping of single letters."""
    if isinstance(key, str) and len(key) == 1:
        key = _keymap().get(key.lower(), key)
    pyautogui.press(key, presses=presses)


def smart_hotkey(*keys) -> None:
    """pyautogui.hotkey with per-layout remapping of single letters."""
    keymap = _keymap()
    mapped = [keymap.get(k.lower(), k) if isinstance(k, str) and len(k) == 1 else k
              for k in keys]
    pyautogui.hotkey(*mapped)
