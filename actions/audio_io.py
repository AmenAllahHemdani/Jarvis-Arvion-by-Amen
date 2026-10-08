# actions/audio_io.py
# JARVIS ARVION — PortAudio recovery helper
#
# When the output/input device changes (Bluetooth headphones connecting,
# switching A2DP<->HFP, unplugging), PortAudio's cached device list goes
# stale and stream opens fail with -9986 / AUHAL errors until it is
# re-initialized.

import time


def reset_portaudio() -> bool:
    """Tear down and re-init PortAudio so it re-enumerates audio devices.
    Only call when no stream is usable anyway — existing streams die."""
    try:
        import sounddevice as sd
        sd._terminate()
        time.sleep(0.4)
        sd._initialize()
        print("[Audio] 🔁 PortAudio reset — devices re-enumerated")
        return True
    except Exception as e:
        print(f"[Audio] ⚠️ PortAudio reset failed: {e}")
        return False
