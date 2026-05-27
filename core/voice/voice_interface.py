"""
AIOS Voice Interface — orchestrates STT, wake word detection, TTS, and agent dispatch.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from .stt import STTEngine, TranscriptResult, WakeWordDetector
from .tts import TTSEngine

logger = logging.getLogger("aios.voice")


class VoiceInterface:
    """
    Main voice interface for AIOS.

    Flow:
    1. STT engine continuously listens
    2. Wake word detected → enter command mode
    3. Next utterance is the command
    4. Dispatch to AI router / agent
    5. TTS speaks the response
    """

    WAKE_RESPONSES = [
        "Áno?",
        "Počúvam.",
        "Tu som.",
        "Čo môžem urobiť?",
    ]

    def __init__(self, config: dict, on_command: Callable[[str], None]):
        self._stt = STTEngine(config.get("stt", {}))
        self._tts = TTSEngine(config.get("tts", {}))
        self._wake = WakeWordDetector(
            wake_word=config.get("wake_word", "aios"),
            sensitivity=float(config.get("wake_word_sensitivity", 0.7)),
        )
        self._on_command = on_command
        self._wake_mode = False
        self._command_timeout = 8.0  # seconds to wait for command after wake
        self._wake_task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._wake_index = 0

    def start(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        self._tts.start()
        self._stt.add_callback(self._on_transcript)
        self._stt.start()
        logger.info("Voice interface started")

    def stop(self):
        self._stt.stop()
        self._tts.stop()
        logger.info("Voice interface stopped")

    def speak(self, text: str, priority: bool = False):
        self._tts.speak(text, priority=priority)

    def _on_transcript(self, result: TranscriptResult):
        """Called by STT engine in a background thread."""
        if not self._loop:
            return
        asyncio.run_coroutine_threadsafe(
            self._handle_transcript(result),
            self._loop,
        )

    async def _handle_transcript(self, result: TranscriptResult):
        text = result.text.strip()
        if not text:
            return

        logger.debug(f"Transcript: {text!r} (lang={result.language})")

        if self._wake_mode:
            await self._dispatch_command(text)
            return

        if self._wake.detect(b"", text_fallback=text):
            await self._enter_wake_mode()

    async def _enter_wake_mode(self):
        if self._wake_mode:
            return
        self._wake_mode = True
        ack = self.WAKE_RESPONSES[self._wake_index % len(self.WAKE_RESPONSES)]
        self._wake_index += 1
        self._tts.speak(ack, priority=True)
        logger.info("Wake word detected, entering command mode")

        if self._wake_task and not self._wake_task.done():
            self._wake_task.cancel()
        self._wake_task = asyncio.create_task(self._wake_timeout())

    async def _wake_timeout(self):
        await asyncio.sleep(self._command_timeout)
        if self._wake_mode:
            self._wake_mode = False
            logger.debug("Wake mode timeout, returning to passive listening")

    async def _dispatch_command(self, text: str):
        self._wake_mode = False
        if self._wake_task and not self._wake_task.done():
            self._wake_task.cancel()

        logger.info(f"Voice command: {text!r}")

        try:
            if asyncio.iscoroutinefunction(self._on_command):
                await self._on_command(text)
            else:
                self._on_command(text)
        except Exception as exc:
            logger.error(f"Command dispatch failed: {exc}", exc_info=True)
            self._tts.speak("Nastala chyba pri spracovaní príkazu.")

    def set_command_handler(self, fn: Callable[[str], None]):
        self._on_command = fn
