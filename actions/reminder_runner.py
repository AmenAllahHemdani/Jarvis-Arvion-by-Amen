# actions/reminder_runner.py
#
# Detached reminder process (macOS / Linux): spawned by reminder.py, sleeps
# until the target time, then rings the owner's phone via the Telegram bot
# and shows a local desktop notification. Survives the main app closing.
#
# Usage: python reminder_runner.py <ISO datetime> <message>

import platform
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_OS = platform.system()


def _local_notify(message: str) -> None:
    try:
        if _OS == "Darwin":
            subprocess.run(
                ["osascript", "-e",
                 f'display notification "{message}" with title "JARVIS Reminder" sound name "Glass"'],
                timeout=10,
            )
            for snd in ("/System/Library/Sounds/Glass.aiff",):
                subprocess.run(["afplay", snd], timeout=10)
        elif _OS == "Linux":
            subprocess.run(["notify-send", "JARVIS Reminder", message], timeout=10)
    except Exception:
        pass


def main() -> None:
    if len(sys.argv) < 3:
        return
    target  = datetime.fromisoformat(sys.argv[1])
    message = sys.argv[2]

    wait = (target - datetime.now()).total_seconds()
    if wait > 0:
        time.sleep(wait)

    _local_notify(message)

    try:
        from actions.call_me import call_me
        call_me({"message": f"Reminder: {message}", "repeat": 3})
    except Exception as e:
        print(f"[ReminderRunner] phone alert failed: {e}")


if __name__ == "__main__":
    main()
