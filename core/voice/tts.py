"""
Text-to-Speech module using Piper TTS.
Async, non-blocking, with audio queue and playback control.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import tempfile
import threading
from pathlib import Path
from queue import Queue, Empty
from typing import Optional

logger = logging.getLogger("aios.voice.tts")


class TTSEngine:
    """
    Piper TTS engine wrapper.
    Maintains an audio playback queue so long responses don't block.
    """

    def __init__(self, config: dict):
        self.engine = config.get("engine", "piper")
        self.voice = config.get("voice", "en_US-ryan-high")
        self.rate = float(config.get("rate", 1.0))
        self.volume = float(config.get("volume", 0.9))
        self.models_dir = Path.home() / ".local" / "share" / "piper" / "voices"

        self._queue: Queue = Queue()
        self._worker_thread: Optional[threading.Thread] = None
        self._running = False
        self._current_proc: Optional[subprocess.Popen] = None

    def start(self):
        self._running = True
        self._worker_thread = threading.Thread(target=self._worker, daemon=True)
        self._worker_thread.start()
        logger.info(f"TTS engine started: {self.engine}/{self.voice}")

    def stop(self):
        self._running = False
        self.interrupt()
        if self._worker_thread:
            self._worker_thread.join(timeout=3)

    def speak(self, text: str, priority: bool = False):
        """Queue text for speech. priority=True clears queue first."""
        if priority:
            self.interrupt()
        self._queue.put(text)

    def interrupt(self):
        """Stop current speech and clear queue."""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except Empty:
                break
        if self._current_proc and self._current_proc.poll() is None:
            self._current_proc.terminate()

    def _worker(self):
        while self._running:
            try:
                text = self._queue.get(timeout=0.5)
                self._synthesize_and_play(text)
            except Empty:
                continue
            except Exception as exc:
                logger.error(f"TTS worker error: {exc}", exc_info=True)

    def _synthesize_and_play(self, text: str):
        if self.engine == "piper":
            self._piper_speak(text)
        elif self.engine == "espeak":
            self._espeak_speak(text)
        else:
            logger.warning(f"Unknown TTS engine: {self.engine}")

    def _piper_speak(self, text: str):
        voice_model = self.models_dir / f"{self.voice}.onnx"
        if not voice_model.exists():
            logger.warning(f"Voice model not found: {voice_model}, using espeak fallback")
            self._espeak_speak(text)
            return

        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                wav_path = f.name

            piper_cmd = [
                "piper",
                "--model", str(voice_model),
                "--output_file", wav_path,
            ]
            proc = subprocess.Popen(
                piper_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            proc.communicate(input=text.encode("utf-8"))
            self._play_wav(wav_path)
            Path(wav_path).unlink(missing_ok=True)

        except FileNotFoundError:
            logger.warning("piper not found, falling back to espeak")
            self._espeak_speak(text)
        except Exception as exc:
            logger.error(f"Piper TTS error: {exc}")

    def _espeak_speak(self, text: str):
        try:
            speed = int(175 * self.rate)
            self._current_proc = subprocess.Popen(
                ["espeak-ng", "-s", str(speed), "-a", str(int(self.volume * 200)), text],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._current_proc.wait()
        except FileNotFoundError:
            logger.error("No TTS engine available. Install piper or espeak-ng.")
        except Exception as exc:
            logger.error(f"espeak error: {exc}")

    def _play_wav(self, path: str):
        try:
            self._current_proc = subprocess.Popen(
                ["aplay", "-q", path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._current_proc.wait()
        except FileNotFoundError:
            try:
                self._current_proc = subprocess.Popen(
                    ["paplay", path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._current_proc.wait()
            except FileNotFoundError:
                logger.error("No audio player found (tried aplay, paplay)")

    async def speak_async(self, text: str, priority: bool = False):
        """Async wrapper — schedules speech and returns immediately."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.speak, text, priority)
