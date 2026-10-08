# J.A.R.V.I.S — ARVION

**A voice-first personal AI assistant for your computer, by Amen.**

Arvion listens, speaks, sees your screen, and actually *does* things — opens and
closes apps, controls your music, reads what's on screen, sends emails, sets
reminders, and learns your habits. Built on the Gemini Live API for real-time
voice, with OpenRouter free models as auxiliary brains.

![status](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-00d4ff)
![python](https://img.shields.io/badge/python-3.11+-00ff88)

## What it does

- 🎙 **Real-time voice conversation** — talk naturally, interrupt him mid-sentence
  ("shut up" works), he stops and listens. Barge-in with echo gating.
- 🖥 **Computer control** — open any app via Spotlight (fullscreen automatically),
  close a *specific* app by name, switch windows, volume, brightness, wifi,
  screenshots, lock, shutdown.
- 🎵 **Media control that actually works** — "pause the video" controls the *last*
  video/audio you played (YouTube tab, Spotify, VLC...), via Apple's MediaRemote —
  the same engine as the menu-bar widget. Never opens a new video by mistake.
- 👁 **Screen & camera vision** — "what's on my screen?" captures and reads it
  aloud, at a resolution where page text is actually readable.
- 🧠 **Long-term memory** — tell him facts once, he remembers across sessions.
- 🤖 **Learned routines** — "when I say start work, open Chrome, Teams and
  VS Code" → from then on, "start work" runs the whole sequence. Any language.
- 📱 **Telegram bridge** — message your bot: when your computer is on, commands run
  with full tool access; when it's off, a Cloudflare Worker answers 24/7.
  A Flutter mobile app with the same HUD is also part of the project.
- 📺 **HUD desktop interface** — arc reactor, live CPU/MEM/NET/GPU metrics, a
  Now-Playing card with transport buttons, real connection/model status badges,
  and an activity log. `F4` mutes (and *fully releases* the mic so Bluetooth
  headphones keep hi-fi quality), `F11` fullscreen.
- 🛡 **Self-healing** — automatic session resume on disconnects, live-model
  failover when Google breaks one, dynamic OpenRouter model discovery (the free
  list changes weekly), and account-level rate-limit backoff.

## Quick start

```bash
git clone git@github.com:AmenAllahHemdani/Jarvis-Arvion-by-Amen.git
cd Jarvis-Arvion-by-Amen

python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp config/api_keys.example.json config/api_keys.json
# then fill in your keys (see below)

python main.py
```

### Required keys (`config/api_keys.json`)

| Key | Where to get it | Needed for |
|---|---|---|
| `gemini_api_key` | [Google AI Studio](https://aistudio.google.com/apikey) | voice, vision — the core |
| `openrouter_api_key` | [OpenRouter](https://openrouter.ai/keys) | memory extraction, intent detection, offline Telegram replies |
| `telegram_bot_token` | [@BotFather](https://t.me/BotFather) | optional — Telegram bridge |
| `email_sender` / `email_app_password` | Gmail app password | optional — "email me this" |

Everything optional can stay empty — the assistant degrades gracefully.

### macOS permissions (first run)

Grant your terminal app these, one time, when prompted:

- **Microphone** — to hear you
- **Accessibility** — to type, press hotkeys, open apps via Spotlight
- **Automation → browser** — to find/control YouTube tabs for media control
- **Screen Recording** — for "what's on my screen?"

## Talk to him

> "Open Teams" · "Close Chrome" · "What apps are open?"
> "Pause the video" · "Play it back" · "Next track"
> "What's on my screen?" · "Read the error and explain it"
> "Remember that my sister's name is..." · "When I say focus mode, close Teams and open VS Code"
> "Volume 40" · "Dark mode" · "Lock the screen"
> "Email me a summary of..." · "Remind me in 20 minutes to..."

He answers in whatever language you speak to him.

## Architecture

```
main.py              Gemini Live session, tool dispatch, reconnect/failover
ui.py                PyQt6 HUD (reactor, metrics, now-playing, badges, log)
or_client.py         OpenRouter client — dynamic free-model discovery
core/prompt.txt      system prompt & tool-routing rules
actions/             the hands: open_app, computer_settings (media/windows),
                     screen_processor (vision session), browser_control,
                     telegram_bridge, app_bridge, reminders, email, ...
agent/               planner/executor for multi-step tasks
memory/              long-term memory + learned routines
```

Companion pieces (kept out of this repo): a **Cloudflare Worker** (Durable
Object message bridge + 24/7 cloud replies) and a **Flutter mobile app** with
the same HUD, voice input and spoken replies.

## Tips

- Single-tap launch: point [MacTap](https://mactap.app) (or any launcher) at
  `launch_jarvis.command` — it refuses to start a second instance.
- OpenRouter free tier is ~50 requests/day; a one-time $10 credit raises it
  to 1000/day permanently.
- If he ever interrupts himself through loudspeakers, raise `BARGE_IN_RMS`
  in `main.py`.

## Credits

Originally inspired by the open-source MARK/JARVIS projects by FatihMakes.
Rebuilt, extended and maintained as **ARVION** by Amen Allah Hemdani.

*"At your service, sir."*
