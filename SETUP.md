# JARVIS ARVION — Setup Guide

Everything you need to get JARVIS running after cloning, including the
permissions and API keys that are easy to miss. Primary platform is **macOS**;
Windows/Linux notes are at the bottom.

---

## 1. Requirements

- **Python 3.11+**
- **macOS** (recommended — all features), Windows or Linux (core features)
- A microphone
- A free **Google Gemini API key** (this powers the voice brain)

---

## 2. Install

```bash
git clone git@github.com:AmenAllahHemdani/Jarvis-Arvion-by-Amen.git
cd Jarvis-Arvion-by-Amen

python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# macOS: pyaudio needs portaudio first
brew install portaudio

pip install -r requirements.txt
python -m playwright install      # browser engines for web automation
```

Or run everything in one go: `python setup.py`

### Optional external tools

| Tool | Why | Install |
|------|-----|---------|
| **Tesseract** | OCR fallback for scanned PDFs/images (macOS already uses Apple Vision first) | `brew install tesseract` |
| **Ollama** | Offline "local brain" — JARVIS still answers typed chat with no internet | [ollama.com](https://ollama.com), then set `ollama_model` in the config |
| **VLC** | Watched/next-episode detection for downloaded films & series reads VLC's resume positions | [videolan.org](https://www.videolan.org) |
| **WhatsApp Desktop** | Sending/reading WhatsApp messages | Mac App Store, then **log in** once |

---

## 3. API keys — `config/api_keys.json`

```bash
cp config/api_keys.example.json config/api_keys.json
```

Then fill in:

| Key | Required? | Where to get it |
|-----|-----------|-----------------|
| `gemini_api_key` | **YES** | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) — free |
| `openrouter_api_key` | Recommended | [openrouter.ai/keys](https://openrouter.ai/keys) — free models used as auxiliary brains (agents, summaries) |
| `os_system` | auto | `Darwin`, `Windows` or `Linux` |
| `email_sender` / `email_app_password` | For "email me…" | Gmail → enable 2FA → [App Passwords](https://myaccount.google.com/apppasswords) (NOT your normal password) |
| `email_default_to` | For "email me…" | Where "email me X" lands |
| `telegram_bot_token` | For the Telegram bridge | Talk to [@BotFather](https://t.me/BotFather) → `/newbot` |
| `telegram_chat_id` | For the Telegram bridge | Your numeric chat id (message [@userinfobot](https://t.me/userinfobot)) |
| `telegram_webhook_*` | Optional 24/7 mode | Cloudflare Worker — see `deploy/` |
| `wake_word` | Optional | `true` to enable on-device "Hey Jarvis" standby mode |
| `ollama_model` | Optional | e.g. `"llama3.2"` — offline brain model |

> If `gemini_api_key` is empty, the HUD shows a setup overlay on first launch
> where you can paste the keys instead.

---

## 4. macOS permissions (IMPORTANT — most "it doesn't work" cases)

JARVIS automates your Mac with keystrokes, reads the screen, and inspects app
windows. macOS requires you to allow this **for the app that launches JARVIS**
(normally **Terminal**; if you use iTerm2, grant iTerm2 instead).

Open **System Settings → Privacy & Security** and enable Terminal under:

| Permission | Needed for | Symptom if missing |
|------------|-----------|--------------------|
| **Microphone** | Voice input | He never hears you (macOS prompts automatically on first run) |
| **Accessibility** | ALL keyboard automation: opening apps via Spotlight, typing, WhatsApp sending | Keystrokes silently vanish; "message was NOT sent"; Spotlight never opens |
| **Screen Recording** | "What's on my screen?", reading documents on screen | Vision sees only the wallpaper |
| **Camera** | Gesture picker (hand tracking), camera vision | Radial picker can't see your hand |
| **Automation** (prompts appear on first use) | Controlling System Events, WhatsApp, Spotify, browsers via AppleScript | "Not authorized to send Apple events" errors |

After changing Accessibility or Screen Recording, **restart JARVIS**.

### One-time app settings

- **WhatsApp**: install from the App Store and log in — JARVIS reads the chat
  list and messages through the Accessibility tree; nothing works logged-out.
- **Brave/Chrome silent YouTube control** (optional): View → Developer →
  **Allow JavaScript from Apple Events** — lets JARVIS replay/control a YouTube
  tab without stealing focus.

---

## 5. First run

```bash
./launch_jarvis.command        # opens in Terminal with the right permissions
# or manually:
venv/bin/python main.py
```

> `launch_jarvis.command` contains an absolute `JARVIS_DIR` path — edit the
> first lines to point at YOUR clone location.

What you should see: the HUD window, `[JARVIS] 🔌 Connecting…` in the
terminal, then the CORE badge goes green. Say something — or type in the
COMMAND INPUT box.

---

## 6. Feature → requirement matrix

| Feature | Needs |
|---------|-------|
| Voice conversation | Gemini key + Microphone |
| Open/close apps, volume, windows | Accessibility |
| Screen vision | Screen Recording |
| WhatsApp send/read | WhatsApp Desktop (logged in) + Accessibility |
| Web browsing/automation | Playwright browsers (`python -m playwright install`) |
| Scanned-PDF / image OCR | Nothing on macOS (Apple Vision); `brew install tesseract` as fallback |
| Downloaded media library ("what have I watched") | VLC used for watch history |
| Email | Gmail app password in config |
| Telegram remote control | Bot token + chat id |
| Offline mode | Ollama installed + `ollama_model` set |
| "Hey Jarvis" wake word | `"wake_word": true` |
| Gesture picker | Camera permission (`hand_landmarker.task` ships with the repo) |

---

## 7. Windows / Linux notes

- **Windows**: app launching uses the Start menu; WhatsApp automation uses the
  keyboard flow (the macOS chat-list reading is Mac-only). `pip install pyaudio`
  may need [pipwin](https://pypi.org/project/pipwin/) if the wheel fails.
- **Linux**: install `xdg-utils`, `gnome-screenshot` (or equivalent),
  `portaudio19-dev` and `tesseract-ocr` via your package manager. Wayland may
  block global keystroke automation — X11 session recommended.

---

## 8. Troubleshooting

| Problem | Fix |
|---------|-----|
| Keystrokes do nothing / messages "NOT sent" | Accessibility permission for Terminal, then restart JARVIS |
| `No API key was provided` | Fill `config/api_keys.json` (`gemini_api_key`) |
| `pyaudio` fails to build | `brew install portaudio` first (macOS) |
| Browser automation fails | `python -m playwright install` |
| WhatsApp "could not read the chat list" | WhatsApp app must be installed, logged in, and its window open at least once; wait a few seconds — the list loads lazily |
| Jarvis speaks but can't hear | Microphone permission; check the F4 mute state in the HUD |
| Screen reading sees nothing | Screen Recording permission, then restart JARVIS |
| Already running error | A previous instance is alive: `pkill -f "venv/bin/python.*main.py"` |
