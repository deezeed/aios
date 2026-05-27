"""
Speech-to-Text module using faster-whisper.
Supports continuous listening, wake word detection, and command extraction.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger("aios.voice.stt")


@dataclass
class TranscriptResult:
    text: str
    language: str
    confidence: float
    is_wake_word: bool
    segments: list[dict]
    duration_s: float


class WakeWordDetector:
    """Lightweight wake word detector using keyword matching."""

    def __init__(self, wake_word: str = "aios", sensitivity: float = 0.7):
        self.wake_word = wake_word.lower()
        self.sensitivity = sensitivity
        self._openwakeword_available = False
        self._model = None
        self._try_load_openwakeword()

    def _try_load_openwakeword(self):
        try:
            import openwakeword
            from openwakeword.model import Model
            self._model = Model(inference_framework="onnx")
            self._openwakeword_available = True
            logger.info("openWakeWord loaded successfully")
        except ImportError:
            logger.info("openWakeWord not available, using keyword matching fallback")

    def detect(self, audio_chunk: bytes, text_fallback: Optional[str] = None) -> bool:
        if self._openwakeword_available and self._model:
            try:
                import numpy as np
                audio_array = np.frombuffer(audio_chunk, dtype=np.int16)
                scores = self._model.predict(audio_array)
                for score in scores.values():
                    if score >= self.sensitivity:
                        return True
                return False
            except Exception as exc:
                logger.debug(f"openWakeWord predict failed: {exc}")

        if text_fallback:
            return self.wake_word in text_fallback.lower()
        return False


class STTEngine:
    """
    Continuous speech-to-text engine using faster-whisper.
    Runs in a background thread, calls callback with transcripts.
    """

    def __init__(self, config: dict):
        self.model_size = config.get("model", "medium")
        self.device = config.get("device", "auto")
        self.compute_type = config.get("compute_type", "int8")
        self.beam_size = config.get("beam_size", 5)
        self.language = config.get("language", None)  # None = auto-detect
        self.sample_rate = config.get("sample_rate", 16000)
        self.chunk_size = config.get("chunk_size", 1024)

        self._model = None
        self._running = False
        self._audio_queue: queue.Queue = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._callbacks: list[Callable[[TranscriptResult], None]] = []

    def add_callback(self, fn: Callable[[TranscriptResult], None]):
        self._callbacks.append(fn)

    def _load_model(self):
        try:
            from faster_whisper import WhisperModel
            device = self.device
            if device == "auto":
                try:
                    import torch
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                except ImportError:
                    device = "cpu"
            self._model = WhisperModel(
                self.model_size,
                device=device,
                compute_type=self.compute_type,
            )
            logger.info(f"Whisper model loaded: {self.model_size} on {device}")
        except ImportError:
            logger.error("faster-whisper not installed. Run: pip install faster-whisper")
            raise

    def _transcribe(self, audio_data: bytes) -> Optional[TranscriptResult]:
        if not self._model:
            return None
        try:
            import io
            import numpy as np
            import soundfile as sf

            audio_array = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0

            kwargs = dict(
                beam_size=self.beam_size,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=500),
            )
            if self.language:
                kwargs["language"] = self.language

            start = time.monotonic()
            segments, info = self._model.transcribe(audio_array, **kwargs)
            segment_list = list(segments)
            duration = time.monotonic() - start

            text = " ".join(s.text.strip() for s in segment_list).strip()
            if not text:
                return None

            return TranscriptResult(
                text=text,
                language=info.language,
                confidence=info.language_probability,
                is_wake_word=False,
                segments=[{"start": s.start, "end": s.end, "text": s.text} for s in segment_list],
                duration_s=duration,
            )
        except Exception as exc:
            logger.error(f"Transcription failed: {exc}", exc_info=True)
            return None

    def _listen_loop(self):
        try:
            import pyaudio
        except ImportError:
            logger.error("pyaudio not installed. Run: pip install pyaudio")
            return

        pa = pyaudio.PyAudio()
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self.sample_rate,
            input=True,
            frames_per_buffer=self.chunk_size,
        )

        logger.info("Microphone stream opened, listening...")
        buffer = bytearray()
        silence_threshold = 500
        silence_chunks = 0
        max_silence_chunks = int(self.sample_rate / self.chunk_size * 1.5)

        try:
            while self._running:
                try:
                    chunk = stream.read(self.chunk_size, exception_on_overflow=False)
                    buffer.extend(chunk)

                    import audioop
                    rms = audioop.rms(chunk, 2)
                    if rms < silence_threshold:
                        silence_chunks += 1
                    else:
                        silence_chunks = 0

                    min_buffer = self.sample_rate * 2 * 0.5  # 0.5s minimum
                    if len(buffer) >= min_buffer and silence_chunks >= max_silence_chunks:
                        audio_data = bytes(buffer)
                        buffer.clear()
                        silence_chunks = 0
                        self._audio_queue.put(audio_data)

                except OSError as exc:
                    logger.warning(f"Audio read error: {exc}")
        finally:
            stream.stop_stream()
            stream.close()
            pa.terminate()

    def _process_loop(self):
        while self._running or not self._audio_queue.empty():
            try:
                audio_data = self._audio_queue.get(timeout=0.5)
                result = self._transcribe(audio_data)
                if result:
                    for cb in self._callbacks:
                        try:
                            cb(result)
                        except Exception as exc:
                            logger.error(f"STT callback error: {exc}")
            except queue.Empty:
                continue

    def start(self):
        if self._running:
            return
        self._load_model()
        self._running = True

        self._listen_thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._process_thread = threading.Thread(target=self._process_loop, daemon=True)
        self._listen_thread.start()
        self._process_thread.start()
        logger.info("STT engine started")

    def stop(self):
        self._running = False
        if self._listen_thread:
            self._listen_thread.join(timeout=3)
        if self._process_thread:
            self._process_thread.join(timeout=3)
        logger.info("STT engine stopped")

    async def transcribe_file(self, path: str) -> Optional[TranscriptResult]:
        """Transcribe an audio file asynchronously."""
        loop = asyncio.get_event_loop()
        with open(path, "rb") as f:
            audio_data = f.read()
        return await loop.run_in_executor(None, self._transcribe, audio_data)
