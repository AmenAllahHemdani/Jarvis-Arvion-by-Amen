# actions/gesture_picker.py
# JARVIS ARVION — hand-gesture control for the radial option picker
#
# Runs on the webcam (cvzone/mediapipe) only while the picker is on screen:
#   ☝  flick the index up and back down (like scrolling a phone) → next option
#   🤏  touch thumb + index together and HOLD 1 second           → select
#   ✋  open palm (all 5 up), hold                                → cancel
# Camera is released the moment the picker closes.

import json
import time
from pathlib import Path

PINCH_PX       = 45     # thumb-index tip distance that counts as touching
APART_PX       = 85     # thumb-index seen at least this far apart = armed
ARM_WINDOW_S   = 4.0    # pinch must start within this time after being apart
PINCH_HOLD_S   = 1.0    # thumb+index must stay touching this long to select
START_GRACE_S  = 1.2    # ignore selects right after the camera opens
PALM_HOLD_S    = 1.0    # open palm must be held this long to cancel
MAX_SESSION_S  = 60     # safety: stop the camera after a minute regardless


def _camera_index() -> int:
    try:
        cfg = json.loads((Path(__file__).resolve().parent.parent /
                          "config" / "api_keys.json").read_text())
        return int(cfg.get("camera_index", 0))
    except Exception:
        return 0


def run(ui) -> None:
    """Blocking gesture loop — call in a daemon thread while picker shows."""
    try:
        import cv2
        from cvzone.HandTrackingModule import HandDetector
    except Exception as e:
        print(f"[Gestures] ⚠️ unavailable ({e}) — mouse/keys still work")
        return

    cap = cv2.VideoCapture(_camera_index())
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    if not cap.isOpened():
        print("[Gestures] ⚠️ camera busy/denied — mouse/keys still work")
        return
    try:
        detector = HandDetector(maxHands=1, detectionCon=0.75, minTrackCon=0.6)
    except Exception as e:
        cap.release()
        print(f"[Gestures] ⚠️ detector failed: {e}")
        return

    print("[Gestures] 👋 Hand control — ☝ flick: next · 🤏 thumb+index 1s: select · ✋ palm: cancel")
    started     = time.time()
    was_up      = False      # edge trigger: each flick = one step
    palm_since  = 0.0
    last_apart  = 0.0        # when thumb+index were last seen clearly apart
    pinch_since = 0.0        # when the current touch started

    try:
        while ui.picker_visible() and time.time() - started < MAX_SESSION_S:
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.1)
                continue
            frame = cv2.flip(frame, 1)
            hands, _ = detector.findHands(frame, draw=False, flipType=False)
            now = time.time()

            if not hands:
                was_up, palm_since, pinch_since = False, 0.0, 0.0
                continue

            hand    = hands[0]
            fingers = detector.fingersUp(hand)
            lm      = hand["lmList"]

            # ✋ open palm (held) → cancel
            if sum(fingers) == 5:
                if palm_since == 0.0:
                    palm_since = now
                elif now - palm_since >= PALM_HOLD_S:
                    print("[Gestures] ✋ cancel")
                    ui.picker_cancel()
                    break
                continue
            palm_since = 0.0

            # 🤏 thumb+index touching, HELD 1s → select.
            # Must have been seen apart recently (deliberate pinch) — a hand
            # arriving already closed can't trigger it.
            if len(lm) > 8:
                d_ti, *_ = detector.findDistance(lm[4][:2], lm[8][:2])
                if d_ti > APART_PX:
                    last_apart  = now
                    pinch_since = 0.0
                elif (d_ti < PINCH_PX
                      and now - started > START_GRACE_S
                      and now - last_apart < ARM_WINDOW_S):
                    if pinch_since == 0.0:
                        pinch_since = now
                    elif now - pinch_since >= PINCH_HOLD_S:
                        print("[Gestures] 🤏 select (thumb+index held)")
                        ui.picker_select()
                        break
                else:
                    pinch_since = 0.0

            # ☝ index flick (up then down) → exactly one step per flick,
            # like scrolling on a phone
            if fingers == [0, 1, 0, 0, 0]:
                if not was_up:
                    ui.picker_move(1)
                    print("[Gestures] ☝ next")
                was_up = True
            else:
                was_up = False
    except Exception as e:
        print(f"[Gestures] ⚠️ loop error: {e}")
    finally:
        cap.release()
        print("[Gestures] 📷 Camera released")
