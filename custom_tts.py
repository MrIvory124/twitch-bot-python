from __future__ import annotations
from enum import Enum
from pathlib import Path
import threading
import queue
import logging
import numpy as np
import sounddevice as sd
from piper import PiperVoice

LOG = logging.getLogger("TTS")

### OPTIONS ####
MAX_TTS_QUEUE = int(1e6) #pratically infinite

class Voice(Enum):
    HFC_MALE = "en_US-hfc_male-medium.onnx"
    NE_MALE = "en_GB-northern_english_male-medium.onnx"
    NORMAN_MALE = "en_US-norman-medium.onnx"
    RYAN_MALE = "en_US-ryan-high.onnx"
    SEMAINE_FEMALE = "en_US-semaine_female.onnx"

default_voice = Voice.NORMAN_MALE

# ---- Worker ---------------------------------------------------------------

class TTSWorker:
    """
    Single-threaded TTS/audio executor.
    - Submit text with .speak(text, voice)
    - Optional .clear_pending() to drop queued items
    - Call .stop() on shutdown
    """
    _STOP = object()

    def __init__(self, models_dir: str | Path = "tts_voice_files", *, max_queue: int = MAX_TTS_QUEUE) -> None:
        self.models_dir = Path(models_dir)
        self.q: "queue.Queue[tuple[Voice, str] | object]" = queue.Queue(maxsize=max_queue)
        self.thread = threading.Thread(target=self._run, name="TTSWorker", daemon=True)
        self._voices: dict[Voice, PiperVoice] = {}   # cache models in-memory
        self._started = False
        self._lock = threading.Lock()                # protects _started only

    # --- lifecycle ---

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self.thread.start()
            self._started = True

    def stop(self, *, wait: bool = True) -> None:
        # signal and optionally join
        try:
            self.q.put_nowait(self._STOP)
        except queue.Full:
            # if full, clear and push stop
            self.clear_pending()
            self.q.put_nowait(self._STOP)
        if wait:
            self.thread.join(timeout=5)

    # --- API used from the event loop thread ---

    def speak(self, text: str, voice: Voice = default_voice, *, drop_if_full: bool = True) -> None:
        """
        Enqueue a TTS job. Non-blocking by default (drops oldest if queue is full).
        """
        job = (voice, text)
        try:
            self.q.put_nowait(job)
        except queue.Full:
            if not drop_if_full:
                # block briefly instead of dropping
                self.q.put(job)  # may block the caller; your call
                return
            # drop the oldest and enqueue the newest to keep things fresh
            try:
                _ = self.q.get_nowait()
            except queue.Empty:
                pass
            self.q.put_nowait(job)

    def clear_pending(self) -> None:
        """Drop any not-yet-played items (does not interrupt the current playback)."""
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass

    # --- internals, run only on the worker thread ---

    def _get_voice(self, v: Voice) -> PiperVoice:
        mdl = self._voices.get(v)
        if mdl is None:
            path = str(self.models_dir / v.value)
            LOG.info("Loading Piper voice: %s", path)
            mdl = PiperVoice.load(path)
            self._voices[v] = mdl
        return mdl

    def _run(self) -> None:
        LOG.info("TTS worker started")
        stream = None
        try:
            while True:
                item = self.q.get()
                if item is self._STOP:
                    break

                voice, text = item  # type: ignore[assignment]
                try:
                    model = self._get_voice(voice)
                    # Create/refresh stream per utterance using the first chunk’s format.
                    stream = None
                    for chunk in model.synthesize(text):
                        if stream is None:
                            dtype = 'int16' if chunk.sample_width == 2 else 'int32'
                            stream = sd.OutputStream(
                                samplerate=chunk.sample_rate,
                                channels=chunk.sample_channels,
                                dtype=dtype,
                            )
                            stream.start()

                        # Piper gives 16-bit bytes in audio_int16_bytes when sample_width==2
                        if chunk.sample_width == 2:
                            data = np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16)
                        else:
                            data = np.frombuffer(chunk.audio_bytes, dtype=np.int32)
                        stream.write(data)
                except Exception as e:
                    LOG.exception("TTS job failed: %r", e)
                finally:
                    if stream is not None:
                        try:
                            stream.stop()
                            stream.close()
                        except Exception:
                            LOG.debug("Stream close failed", exc_info=True)
                        stream = None
        finally:
            LOG.info("TTS worker stopping")
