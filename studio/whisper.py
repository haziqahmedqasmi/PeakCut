"""Local multilingual speech recognition. Hindi and English stay as spoken, with word timings."""
import threading

import numpy as np

from . import config

_lock = threading.Lock()
_model = None


def transcribe(audio: np.ndarray, on_progress=None) -> list:
    """One shared model, rather than a large copy for every video being processed."""
    global _model
    if not len(audio):
        if on_progress:
            on_progress(1.0)
        return []
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel
            from . import transcribe as speech
            model = WhisperModel(config.WHISPER_MODEL, device=speech.PROVIDER,
                                 compute_type="int8" if speech.PROVIDER == "cpu" else "float16",
                                 cpu_threads=speech.THREADS, num_workers=2,
                                 download_root=str(config.MODELS_DIR / "whisper"))
            if not model.model.is_multilingual:
                raise ValueError("Choose a multilingual Whisper model, such as medium or large-v3, not an English-only model.")
            _model = model
    # Faster-whisper supports concurrent calls; lock only loading so live audio need not wait for a whole video.
    segments, _ = _model.transcribe(
        audio.astype(np.float32) / 32768.0, task="transcribe", language=None,
        multilingual=True, word_timestamps=True, beam_size=5,
        condition_on_previous_text=False, vad_filter=True,
    )
    words = []
    duration = len(audio) / 16000
    for segment in segments:
        for word in segment.words or []:
            text = word.word.strip()
            if text:
                words.append({"w": text, "s": round(word.start, 2), "e": round(word.end, 2)})
        if on_progress:
            on_progress(min(1.0, segment.end / duration))
    if on_progress:
        on_progress(1.0)
    return words
