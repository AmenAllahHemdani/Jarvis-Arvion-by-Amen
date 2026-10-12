# Live end-to-end test for the gesture radial picker (standalone, no Gemini).
import sys, time, threading, subprocess
sys.path.insert(0, ".")
from PyQt6.QtCore import QTimer
from ui import JarvisUI
from memory import media_history
from actions import gesture_picker

ui = JarvisUI("face.png")
done = {"flag": False, "choice": "~none~"}

titles = [e["title"][:40] for e in media_history.recent(6)] or \
         ["Surah Al-Kahf", "RITMO MIRA", "2 MILLONES", "Test Option D"]

def on_done(choice):
    done["choice"] = choice
    done["flag"]   = True
    msg = f"You selected: {choice}" if choice else "Cancelled."
    print(f"[TEST] ✅ RESULT -> {choice!r}", flush=True)
    subprocess.Popen(["say", msg])

print("[TEST] opening picker with:", titles, flush=True)
ui.show_option_picker("Gesture Test — pick one", titles, on_done)
threading.Thread(target=lambda: (time.sleep(0.5), gesture_picker.run(ui)),
                 daemon=True).start()

t0 = time.time()
def tick():
    if done["flag"] or time.time() - t0 > 60:
        if not done["flag"]:
            print("[TEST] ⏱ timeout — no selection", flush=True)
        ui.root._app.quit()
timer = QTimer()
timer.timeout.connect(tick)
timer.start(300)
ui.root._app.exec()
print("[TEST] window closed", flush=True)
