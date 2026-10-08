# actions/wake_word.py
# JARVIS ARVION — local wake-word detection ("hey Jarvis")
#
# Runs fully on-device via openwakeword: in standby, mic audio is analysed
# locally and NOTHING is streamed to the cloud until the wake word is heard.

import numpy as np

THRESHOLD   = 0.5     # hey_jarvis score needed to wake (test: real=0.78, other speech=0.00)
_FRAME      = 1280    # openwakeword wants 80 ms frames @ 16 kHz


class WakeWordDetector:

    def __init__(self):
        self._model  = None
        self._buffer = np.zeros(0, dtype=np.int16)
        try:
            from openwakeword.model import Model
            try:
                self._model = Model(wakeword_models=["hey_jarvis"],
                                    inference_framework="onnx")
            except Exception:
                # first run: fetch the pretrained model, then retry
                import openwakeword.utils as u
                u.download_models(["hey_jarvis"])
                self._model = Model(wakeword_models=["hey_jarvis"],
                                    inference_framework="onnx")
            print("[WakeWord] ✅ 'hey Jarvis' detector ready (on-device)")
        except Exception as e:
            print(f"[WakeWord] ⚠️ unavailable ({e}) — pip install openwakeword")

    @property
    def available(self) -> bool:
        return self._model is not None

    def reset(self):
        if self._model:
            self._model.reset()
        self._buffer = np.zeros(0, dtype=np.int16)

    def feed(self, frame: np.ndarray) -> bool:
        """Feed int16 mono 16 kHz samples; True when the wake word is heard."""
        if self._model is None:
            return False
        self._buffer = np.concatenate([self._buffer, frame.reshape(-1)])
        woke = False
        while len(self._buffer) >= _FRAME:
            chunk, self._buffer = self._buffer[:_FRAME], self._buffer[_FRAME:]
            try:
                if self._model.predict(chunk)["hey_jarvis"] >= THRESHOLD:
                    woke = True
            except Exception:
                return False
        if woke:
            self.reset()   # don't re-trigger on the tail of the same utterance
        return woke
