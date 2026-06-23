#!/usr/bin/env python3
"""
Voice Translation Tool — Main Entry Point

Dual-mode real-time translation:
  loopback mode:  Speaker output (EN) → ASR → ZH translation → 中文字幕
  microphone mode: Microphone (ZH) → ASR → EN translation → English subtitles

Usage:
    python -m src.main                          # Loopback mode (default)
    python -m src.main --mode microphone        # Microphone mode
    python -m src.main --config config.json     # Custom config
    python -m src.main --list-devices           # List audio devices
    python -m src.main --no-ui                  # Console-only (no overlay)
"""
import sys
import os
import json
import queue
import logging
import threading
import time
import signal
import argparse
import ctypes
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import numpy as np

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer, Qt, QObject, pyqtSignal

from src.audio_capture import AudioCapture, AudioChunk, list_all_devices
from src.asr_engine import AsrEngine, AsrResult
from src.translator import TranslationResult, create_translator
from src.subtitle_overlay import SubtitleOverlay
from src.theme import get_app_stylesheet
from src.companion import CompanionWindow, CompanionStateMachine

logger = logging.getLogger("game-translator")


def _is_likely_noise(text: str, source_lang: str) -> bool:
    """Return True if ASR output looks like noise/garbage/phantom, not real speech.

    Uses multi-layered heuristics tuned from real-world testing:
      1. Too short (< 3 chars)
      2. Single isolated word (Whisper's most common hallucination)
      3. Repeated word patterns (hallucinated loops)
      4. All-same-character gibberish
      5. Common Whisper phantom phrases
      6. Suspicious character composition (too many special chars)
    """
    import re
    t = text.strip()
    if len(t) < 2:
        return True  # only filter single characters (e.g. "a", "的") — "hi", "no", "小心" are valid

    if source_lang == "en":
        words = t.split()

        # Layer 1: Repeated same word (hallucination loop — "the the the")
        if len(words) >= 3:
            word_set = set(w.lower() for w in words)
            if len(word_set) == 1:
                return True
            if len(word_set) <= 2 and len(words) >= 5:
                unique_ratio = len(word_set) / len(words)
                if unique_ratio < 0.3:
                    return True

        # Layer 2: All same character repeated (pure gibberish like "aaaaaa")
        alpha_only = re.sub(r'[^a-zA-Z]', '', t.lower())
        if len(alpha_only) >= 5 and len(set(alpha_only)) <= 2:
            return True

        # Layer 3: Common Whisper hallucination phrases (phantom words from silence)
        hallucination_phrases = [
            "thank you", "thanks for watching", "thank you for watching",
            "please subscribe", "subscribe", "like and subscribe",
            "goodbye", "see you next time", "bye",
        ]
        t_lower_nopunct = t.lower().strip().rstrip('.!?,;: ')
        if t_lower_nopunct in hallucination_phrases:
            return True

    elif source_lang == "zh":
        # Chinese noise detection
        chinese_chars = len(re.findall(r'[一-鿿]', t))
        if chinese_chars < 2:
            return True

        # Single repeated character
        unique_chinese = len(set(re.findall(r'[一-鿿]', t)))
        if chinese_chars >= 3 and unique_chinese == 1:
            return True

        # Common Chinese hallucination phrases
        zh_hallucinations = ["谢谢观看", "谢谢大家", "拜拜", "再见"]
        if t.strip().rstrip('!！。，,') in zh_hallucinations:
            return True

    return False

# ============================================================================
# Configuration
# ============================================================================

def load_config(config_path: Optional[str] = None) -> dict:
    """Load configuration from JSON file. Secrets come from .env."""
    # Load .env file first (python-dotenv)
    try:
        from dotenv import load_dotenv
        env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
        load_dotenv(env_path)
        logger.debug(".env loaded")
    except ImportError:
        pass  # python-dotenv not installed, env vars must be set manually

    if config_path is None:
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    # Strip comments
    config.pop("_comment", None)

    return config


# ============================================================================
# Pipeline Controller
# ============================================================================

class PipelineController(QObject):
    """Manages the audio → ASR → translation → UI pipeline.

    Supports two modes:
    - "loopback": Speaker output (EN) → ZH subtitles
    - "microphone": Microphone input (ZH) → EN subtitles
    """

    translation_ready = pyqtSignal(str, str)  # source_text, translated_text

    def __init__(self, config: dict, capture_mode: str = "loopback"):
        super().__init__()
        self.config = config
        self.capture_mode = capture_mode
        self.running = False
        self.paused = False

        # Current device selection (unified device_id)
        self._current_device_id: Optional[str] = config.get("audio", {}).get("device_id")

        # Determine language direction from capture mode
        if capture_mode == "loopback":
            self._source_lang = "en"
            self._target_lang = "zh"
            self._trans_direction = "en2zh"
            self._asr_model = config.get("asr", {}).get("model_size", "medium.en")
            # English-optimized ASR for maximum accuracy
            asr_cfg = config.setdefault("asr", {})
            asr_cfg["language"] = "en"
            asr_cfg["vad_min_silence_ms"] = 500
            asr_cfg["vad_speech_pad_ms"] = 400
            asr_cfg["no_speech_threshold"] = 0.5
            asr_cfg["beam_size"] = 5
            asr_cfg["best_of"] = 3
        else:  # microphone
            self._source_lang = "zh"
            self._target_lang = "en"
            self._trans_direction = "zh2en"
            # Chinese REQUIRES multilingual model — override config if it's en-only
            cfg_model = config.get("asr", {}).get("model_size", "large-v3")
            if cfg_model.endswith(".en"):
                cfg_model = "large-v3"  # en-only models can't transcribe Chinese
            self._asr_model = cfg_model
            # Chinese-optimized ASR settings
            asr_cfg = config.setdefault("asr", {})
            asr_cfg["language"] = "zh"
            asr_cfg["vad_min_silence_ms"] = 400    # balanced for Chinese
            asr_cfg["vad_speech_pad_ms"] = 400     # keep context
            asr_cfg["beam_size"] = 5
            asr_cfg["best_of"] = 3
            asr_cfg["no_speech_threshold"] = 0.5

        # Audio accumulator — 2.0s buffer for full-sentence context
        self._audio_buffer: list[np.ndarray] = []
        self._overlap_buffer: Optional[np.ndarray] = None
        self._buffer_duration_ms = 0.0
        self._min_asr_chunk_ms = 2000   # 2.0s buffer — full sentence context for maximum ASR accuracy
        self._max_asr_chunk_ms = 5000   # Force ASR after 5.0s max (safety ceiling)
        self._overlap_ms = 200          # 0.2s overlap — continuity between ASR chunks
        self._last_asr_text = ""

        # VAD pre-filter DISABLED: faster-whisper's built-in VAD (vad_filter=True)
        # is more accurate and avoids double-processing overhead.
        # The pre-filter was adding 30-50ms latency per chunk with no benefit.
        self._vad_opts = None

        # Thread-safe queue between capture thread and main thread
        self._capture_queue = queue.Queue(maxsize=128)

        # Thread pool for blocking ASR/translation calls
        # 4 workers: allows concurrent ASR + translation + cache lookup without queueing
        self._executor = ThreadPoolExecutor(max_workers=4)

        # Components (initialized in start())
        self._capture: Optional[AudioCapture] = None
        self._asr: Optional[AsrEngine] = None
        self._translator = None  # DeepSeekTranslator | DeeplTranslator
        self._overlay: Optional[SubtitleOverlay] = None
        self._app: Optional[QApplication] = None
        self._panel = None       # ControlPanel (may be None if companion replaces it)
        self._companion = None   # CompanionWindow (may be None if disabled)
        self._state_machine = None  # CompanionStateMachine

        # Polling timer for Qt integration
        self._poll_timer: Optional[QTimer] = None

        # Stats
        self._stats = {
            "captured": 0,
            "transcribed": 0,
            "translated": 0,
            "displayed": 0,
            "cache_hits": 0,
            "errors": 0,
            "vad_skipped": 0,    # chunks skipped by VAD (silence)
            "vad_passed": 0,     # chunks passed to ASR (speech)
        }

    # ----- Lifecycle -----

    def start(self, show_ui: bool = True) -> None:
        """Start the full pipeline."""
        logger.info("=" * 60)
        logger.info(f"Starting translation pipeline [mode={self.capture_mode}]")
        logger.info(f"Direction: {self._source_lang.upper()} → {self._target_lang.upper()}")
        logger.info("=" * 60)

        # 1. Audio capture
        audio_config = self.config.get("audio", {})
        # Use config capture_mode if set, otherwise use CLI arg
        mode = audio_config.get("capture_mode", self.capture_mode)
        self.capture_mode = mode
        self._capture = AudioCapture(
            output_queue=self._capture_queue,
            capture_mode=mode,
            sample_rate=audio_config.get("sample_rate", 48000),
            channels=audio_config.get("channels", 2),
            chunk_ms=audio_config.get("chunk_ms", 100),
            target_sample_rate=audio_config.get("target_sample_rate", 16000),
            target_channels=audio_config.get("target_channels", 1),
            device_id=self._current_device_id,
        )
        self._capture.start()
        logger.info(f"Audio capture: OK (mode={mode})")

        # 2. VAD: faster-whisper's built-in VAD (vad_filter=True) is used.
        # No external pre-filter needed — avoids double VAD processing overhead.

        # 3. ASR engine (balanced speed + accuracy)
        asr_config = self.config.get("asr", {})
        self._asr = AsrEngine(
            model_size=self._asr_model,
            device=asr_config.get("device", "cuda"),
            compute_type=asr_config.get("compute_type", "int8_float16"),
            beam_size=asr_config.get("beam_size", 8),
            best_of=asr_config.get("best_of", 8),
            language=asr_config.get("language", self._source_lang),
            vad_filter=asr_config.get("vad_filter", True),
            vad_min_silence_ms=asr_config.get("vad_min_silence_ms", 300),
            vad_speech_pad_ms=asr_config.get("vad_speech_pad_ms", 500),
            no_speech_threshold=asr_config.get("no_speech_threshold", 0.3),
            condition_on_previous_text=asr_config.get("condition_on_previous_text", True),
            repetition_penalty=asr_config.get("repetition_penalty", 1.1),
            preprocess=asr_config.get("preprocess", True),
        )
        # Load ASR model asynchronously so UI appears immediately
        self._asr_loaded = False

        def _load_asr_async():
            try:
                self._asr.load()
                self._asr_loaded = True
                logger.info(f"ASR engine: OK (lang={asr_config.get('language', self._source_lang)})")
                target = self._panel or self._companion
                if target:
                    target.add_log("ASR模型加载完成，可以开始翻译")
                if self._panel:
                    self._panel._status_indicator.set_state("ready")
                    self._panel._status_label.setText("READY")
                if self._companion:
                    self._companion.set_state("idle")
            except Exception as e:
                logger.error(f"ASR model load failed: {e}")
                target = self._panel or self._companion
                if target:
                    target.add_log(f"模型加载失败: {e}")

        self._executor.submit(_load_asr_async)

        # 3. Translator (auto-detect from available API keys)
        trans_config = self.config.get("translation", {})
        self._translator = create_translator(trans_config, direction=self._trans_direction)
        logger.info(f"Translator: OK (engine={self._translator.__class__.__name__}, "
                    f"dir={self._trans_direction})")

        # 4. Qt Application (must be created in main thread) — show UI IMMEDIATELY
        if show_ui:
            self._app = QApplication.instance()
            if self._app is None:
                self._app = QApplication(sys.argv)

            # Apply theme globally
            self._app.setStyleSheet(get_app_stylesheet())

            # Determine UI mode from companion config
            companion_cfg = self.config.get("companion", {})
            companion_enabled = companion_cfg.get("enabled", False)
            companion_mode = companion_cfg.get("mode", "off")

            use_companion = companion_enabled and companion_mode in ("replace", "coexist")
            use_panel = not companion_enabled or companion_mode == "coexist"

            self._panel = None
            self._companion = None
            self._state_machine = None

            # --- Create primary UI ---
            if use_companion:
                try:
                    self._companion = CompanionWindow(self.config, capture_mode=self.capture_mode)
                    self._companion.setWindowTitle(
                        "扬声器→中文字幕" if self.capture_mode == "loopback" else "麦克风→英文字幕"
                    )
                    self._companion.show()
                    logger.info("Companion window: OK")

                    # State machine for companion visual states
                    idle_timeout = companion_cfg.get("auto_sleep_sec", 120)
                    self._state_machine = CompanionStateMachine(idle_timeout_sec=idle_timeout)
                    self._state_machine.state_changed.connect(self._companion.set_state)
                except Exception as e:
                    logger.error(f"Companion creation failed: {e}", exc_info=True)
                    logger.info("Falling back to ControlPanel...")
                    self._companion = None
                    self._state_machine = None
                    use_panel = True  # force panel creation

            if use_panel:
                from src.control_panel import ControlPanel
                self._panel = ControlPanel(self.config, capture_mode=self.capture_mode)
                self._panel.show()
                logger.info("Control panel: OK")

            # --- Subtitle overlay (always created) ---
            self._overlay = SubtitleOverlay(self.config)
            self._overlay.show()

            # --- Wire UI signals ---
            if self._companion:
                self._companion.toggle_capture.connect(self._toggle_capture)
                self._companion.clear_subtitles.connect(self._overlay.clear)
                self._companion.toggle_overlay.connect(self._overlay.toggle_visible)
                self._companion.show_overlay_signal.connect(self._overlay.show_overlay)
                self._companion.mode_changed.connect(self._on_mode_changed)
                self._companion.device_changed.connect(self._on_device_changed)
                self._companion.config_changed.connect(self._apply_config_changes)

            if self._panel:
                self._panel.toggle_capture.connect(self._toggle_capture)
                self._panel.clear_subtitles.connect(self._overlay.clear)
                self._panel.toggle_overlay.connect(self._overlay.toggle_visible)
                self._panel.show_overlay_signal.connect(self._overlay.show_overlay)
                self._panel.font_increase.connect(self._overlay.increase_font)
                self._panel.font_decrease.connect(self._overlay.decrease_font)
                self._panel.config_changed.connect(self._apply_config_changes)
                self._panel.mode_changed.connect(self._on_mode_changed)
                self._panel.device_changed.connect(self._on_device_changed)
                self._overlay.overlay_closed.connect(self._panel.on_overlay_closed)

            if self._companion:
                self._overlay.overlay_closed.connect(self._companion.on_overlay_closed)

            self.translation_ready.connect(self._update_ui_with_translation)

            # Register hotkeys (on available windows)
            self._register_hotkeys()

            # Polling timer: check for new audio and process pipeline
            self._poll_timer = QTimer()
            self._poll_timer.timeout.connect(self._poll_pipeline)
            self._poll_timer.start(30)  # ~33 Hz polling — faster response

            # Stats update timer
            self._stats_timer = QTimer()
            self._stats_timer.timeout.connect(self._update_panel_stats)
            self._stats_timer.start(2000)  # every 2s

            # Log startup
            log_target = self._panel or self._companion
            if log_target:
                log_target.add_log(f"程序已启动 [{mode}]")
                log_target.add_log(f"方向: {self._source_lang.upper()}→{self._target_lang.upper()}")
                log_target.add_log(f"模型: {self._asr_model} (后台加载中...)")
                log_target.add_log("字幕窗口已打开")
                log_target.add_log(f"采集设备: {self._capture.device_id or '自动'}")
            logger.info("Subtitle overlay: OK")
        else:
            self._panel = None
            self._stats_timer = None

        self.running = True
        logger.info(f"Pipeline ready. Mode: {mode}")
        logger.info("=" * 60)

        if show_ui:
            # Run Qt event loop (blocks until window closes)
            self._app.exec()

    def stop(self) -> None:
        """Stop the pipeline gracefully."""
        logger.info("Shutting down...")
        self.running = False

        if self._poll_timer:
            self._poll_timer.stop()
        if self._stats_timer:
            self._stats_timer.stop()

        if self._capture:
            self._capture.stop()

        if self._asr:
            self._asr.unload()

        self._executor.shutdown(wait=True, cancel_futures=False)

        if self._overlay:
            self._overlay.close()

        # Print final stats (no sensitive data)
        s = self._stats
        logger.info(f"Final stats: captured={s['captured']}, "
                    f"transcribed={s['transcribed']}, "
                    f"translated={s['translated']}, "
                    f"displayed={s['displayed']}")

    def _register_hotkeys(self) -> None:
        """Register global keyboard shortcuts."""
        try:
            from PyQt6.QtGui import QShortcut, QKeySequence

            hotkeys = self.config.get("hotkeys", {})

            def make_shortcut(key_str: str, callback, name: str):
                if not key_str:
                    return
                try:
                    # Register on overlay + primary UI window
                    QShortcut(QKeySequence(key_str), self._overlay).activated.connect(callback)
                    parent_window = self._panel or self._companion
                    if parent_window:
                        QShortcut(QKeySequence(key_str), parent_window).activated.connect(callback)
                    logger.info(f"Hotkey '{key_str}': {name}")
                except Exception as e:
                    logger.warning(f"Failed to register hotkey '{key_str}': {e}")

            make_shortcut(
                hotkeys.get("toggle_capture", "ctrl+shift+t"),
                self._toggle_capture, "Toggle capture"
            )
            make_shortcut(
                hotkeys.get("clear_subtitles", "ctrl+shift+c"),
                self._overlay.clear, "Clear subtitles"
            )
            make_shortcut(
                hotkeys.get("toggle_ui_visible", "ctrl+shift+h"),
                self._overlay.toggle_visible, "Toggle UI visible"
            )
            make_shortcut(
                hotkeys.get("increase_font", "ctrl+shift+="),
                self._overlay.increase_font, "Increase font"
            )
            make_shortcut(
                hotkeys.get("decrease_font", "ctrl+shift+-"),
                self._overlay.decrease_font, "Decrease font"
            )

        except Exception as e:
            logger.warning(f"Hotkey registration failed: {e}")
            logger.info("Continuing without hotkeys. Use manual control.")

    # ----- Pipeline processing -----

    def _poll_pipeline(self) -> None:
        """Called by QTimer every 50ms to process the pipeline.

        Drains the capture queue, accumulates audio, and triggers ASR
        when enough audio has been collected.
        """
        if not self.running or self.paused:
            return
        if not self._asr_loaded:
            # Model still loading — drain but don't process
            while True:
                try:
                    self._capture_queue.get_nowait()
                except queue.Empty:
                    break
            return

        try:
            self._drain_capture_queue()
        except Exception as e:
            logger.error(f"Pipeline error: {e}", exc_info=True)
            self._stats["errors"] += 1

    def _drain_capture_queue(self) -> None:
        """Drain all available audio chunks from the capture queue.

        Accumulates audio and triggers ASR when enough has been collected.
        """
        drained = 0
        while True:
            try:
                chunk: AudioChunk = self._capture_queue.get_nowait()
                self._audio_buffer.append(chunk.data)
                self._buffer_duration_ms += chunk.duration_ms
                self._stats["captured"] += 1
                drained += 1
            except queue.Empty:
                break

        if drained == 0:
            return

        # Notify state machine that audio is flowing
        if self._state_machine:
            self._state_machine.on_audio_active()

        # Trigger ASR when enough audio accumulated
        if self._buffer_duration_ms >= self._min_asr_chunk_ms:
            self._flush_audio_buffer()
        elif self._buffer_duration_ms >= self._max_asr_chunk_ms:
            # Force flush: buffer has grown too large
            self._flush_audio_buffer()

    def _flush_audio_buffer(self) -> None:
        """Take accumulated audio, apply overlap, and submit to ASR."""
        if not self._audio_buffer:
            return

        audio = np.concatenate(self._audio_buffer)
        self._audio_buffer.clear()

        # Prepend overlap from previous chunk for continuity
        if self._overlap_buffer is not None and len(self._overlap_buffer) > 0:
            audio = np.concatenate([self._overlap_buffer, audio])

        # Save overlap for next chunk (last N ms)
        overlap_samples = int(self._overlap_ms / 1000.0 * 16000)  # at 16kHz
        if len(audio) > overlap_samples:
            self._overlap_buffer = audio[-overlap_samples:].copy()
        else:
            self._overlap_buffer = audio.copy()

        self._buffer_duration_ms = 0.0

        # Notify state machine
        if self._state_machine:
            self._state_machine.on_asr_started()

        # Submit ASR to thread pool (non-blocking)
        future = self._executor.submit(self._run_asr, audio)
        future.add_done_callback(self._on_asr_done)

    def _run_asr(self, audio: np.ndarray) -> AsrResult:
        """Run ASR inference (called in thread pool)."""
        # Create minimal chunk
        from src.audio_capture import AudioChunk
        chunk = AudioChunk(
            data=audio.astype(np.float32),
            sample_rate=16000,
            timestamp=time.monotonic(),
            duration_ms=len(audio) / 16000 * 1000,
            sequence_id=self._stats["transcribed"],
        )
        return self._asr.transcribe(chunk)

    def _on_asr_done(self, future) -> None:
        """Callback when ASR completes — submit translation."""
        try:
            result: AsrResult = future.result()
            self._stats["transcribed"] += 1

            if result.has_text and not result.error:
                text = result.full_text

                # Filter out noise/garbage: single words, all-caps gibberish
                if _is_likely_noise(text, self._source_lang):
                    logger.debug(f"ASR noise filtered: {text!r}")
                    return

                # Deduplicate: only skip exact duplicates of the previous utterance
                if self._last_asr_text and len(text) > 2:
                    if text.strip().lower() == self._last_asr_text.strip().lower():
                        return  # exact repeat — skip

                self._last_asr_text = text
                # Submit translation to thread pool
                trans_future = self._executor.submit(self._run_translation, text)
                trans_future.add_done_callback(self._on_translation_done)
            elif result.error:
                logger.error(f"ASR error: {result.error}")
                self._stats["errors"] += 1

        except Exception as e:
            logger.error(f"ASR future error: {e}", exc_info=True)
            self._stats["errors"] += 1

    def _run_translation(self, text: str) -> TranslationResult:
        """Run translation (called in thread pool)."""
        import asyncio

        # Check cache first
        cache = self._translator.cache
        src_lang = self._source_lang.upper()
        tgt_lang = self._target_lang.upper()
        cached = cache.get(text, src_lang, tgt_lang)
        if cached is not None:
            self._stats["cache_hits"] += 1
            return TranslationResult(
                source_text=text,
                translated_text=cached,
                source_lang=src_lang,
                target_lang=tgt_lang,
                engine="cache",
                latency_ms=0.0,
            )

        # Run async translate in sync context
        async def _translate():
            # Ensure client is started
            if self._translator._client is None:
                await self._translator.start()
            return await self._translator.translate(text)

        try:
            # Try to get or create an event loop for this thread
            try:
                loop = asyncio.get_event_loop()
                if loop.is_closed():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            result = loop.run_until_complete(_translate())
            self._stats["translated"] += 1
            return result
        except Exception as e:
            logger.error(f"Translation error: {e}")
            return TranslationResult(
                source_text=text,
                translated_text=f"[翻译失败] {text}",
                source_lang=src_lang,
                target_lang=tgt_lang,
                error=str(e),
            )

    def _on_translation_done(self, future) -> None:
        """Callback when translation completes - update UI on main thread."""
        try:
            result: TranslationResult = future.result()

            if result.ok:
                # Emit signal to marshal UI update back to main thread.
                # This callback runs in a ThreadPoolExecutor worker thread;
                # pyqtSignal is thread-safe and delivers to the main thread.
                self.translation_ready.emit(result.source_text, result.translated_text)
            else:
                logger.warning(f"Translation failed: {result.error}")

        except Exception as e:
            logger.error(f"Translation future error: {e}", exc_info=True)
            self._stats["errors"] += 1

    def _update_ui_with_translation(self, source: str, translated: str) -> None:
        """Update UI with translation result. Always called on main thread."""
        # Final safety filter: reject output that's clearly wrong
        if not translated or not translated.strip():
            return
        t = translated.strip()
        # Reject if translation equals source (passthrough = API failed)
        if t.lower() == source.strip().lower():
            return
        # Reject pure English in en→zh mode
        if self._source_lang == "en" and self._target_lang == "zh":
            import re
            if not re.search(r'[一-鿿]', t):
                return  # No Chinese characters = garbage
        # Reject pure Chinese in zh→en mode
        if self._source_lang == "zh" and self._target_lang == "en":
            import re
            if re.search(r'[一-鿿]', t):
                return  # Contains Chinese = garbage

        if self._overlay and self._overlay.isVisible():
            self._overlay.add_line(t)
            self._stats["displayed"] += 1
        if self._panel:
            self._panel.update_translation(source, t)
        if self._companion:
            self._companion.update_translation(source, t)

        # Feed state machine
        if self._state_machine:
            self._state_machine.on_translation_complete()

    def _update_panel_stats(self) -> None:
        if self._panel:
            self._panel.update_stats(
                captured=self._stats["captured"],
                transcribed=self._stats["transcribed"],
                translated=self._stats["translated"],
                latency_ms=self._asr.model_info.get("avg_latency_ms", 0) if self._asr else 0,
            )
        if self._companion:
            self._companion.update_stats(
                captured=self._stats["captured"],
                transcribed=self._stats["transcribed"],
                translated=self._stats["translated"],
                latency_ms=self._asr.model_info.get("avg_latency_ms", 0) if self._asr else 0,
            )
        # Log VAD efficiency periodically
        total = self._stats["vad_skipped"] + self._stats["vad_passed"]
        if total > 0 and total % 20 == 0:
            pct = self._stats["vad_skipped"] / total * 100
            logger.debug(f"VAD: {self._stats['vad_skipped']}/{total} chunks filtered ({pct:.0f}% silence)")

    def _apply_config_changes(self, changes: dict) -> None:
        for key_path, value in changes.items():
            keys = key_path.split(".")
            target = self.config
            for k in keys[:-1]:
                target = target.setdefault(k, {})
            target[keys[-1]] = value
        if self._overlay and "ui.font_size" in changes:
            self._overlay.set_font_size(changes["ui.font_size"])

    def _on_mode_changed(self, new_mode: str) -> None:
        """Handle capture mode change — reload ASR, recreate translator, restart capture."""
        logger.info(f"Mode change: {new_mode}")
        old_mode = self.capture_mode
        self.capture_mode = new_mode

        # Update language direction
        if new_mode == "loopback":
            self._source_lang = "en"
            self._target_lang = "zh"
            self._trans_direction = "en2zh"
            self._asr_model = self.config.get("asr", {}).get("model_size", "medium.en")
        else:
            self._source_lang = "zh"
            self._target_lang = "en"
            self._trans_direction = "zh2en"
            cfg_model = self.config.get("asr", {}).get("model_size", "large-v3")
            if cfg_model.endswith(".en"):
                cfg_model = "large-v3"
            self._asr_model = cfg_model

        asr_cfg = self.config.setdefault("asr", {})
        asr_cfg["language"] = self._source_lang
        self.config.setdefault("audio", {})["capture_mode"] = new_mode

        target = self._panel or self._companion
        if target:
            mode_display = "EN→ZH" if new_mode == "loopback" else "ZH→EN"
            target.add_log(f"模式切换: {mode_display}")
        if self._companion:
            self._companion.set_mode(new_mode)

        if self.running and old_mode != new_mode:
            # 1. Recreate translator for new direction
            trans_config = self.config.get("translation", {})
            self._translator = create_translator(trans_config, direction=self._trans_direction)
            logger.info(f"Translator recreated: {self._translator.__class__.__name__} "
                       f"dir={self._trans_direction}")

            # 2. Reload ASR for new language
            self._asr_loaded = False
            self._asr.unload()
            self._asr.language = self._source_lang
            self._asr.model_size = self._asr_model
            self._asr._model = None
            self._asr._loaded = False

            def _reload_asr():
                try:
                    self._asr.load()
                    self._asr_loaded = True
                    logger.info(f"ASR reloaded: {self._asr_model}")
                    target = self._panel or self._companion
                    if target:
                        target.add_log(f"模型 {self._asr_model} 就绪")
                except Exception as e:
                    logger.error(f"ASR reload failed: {e}")

            self._executor.submit(_reload_asr)

            # 3. Restart audio capture
            self._hot_restart_capture()

    def _on_device_changed(self, device_id: str) -> None:
        """Handle device selection change from UI — hot restart capture."""
        if device_id == self._current_device_id:
            return
        logger.info(f"Device change requested: {device_id}")
        self._current_device_id = device_id
        self.config.setdefault("audio", {})["device_id"] = device_id
        if self.running:
            self._hot_restart_capture()

    def _hot_restart_capture(self) -> None:
        """Stop current capture, drain queues, and restart with current settings."""
        was_paused = self.paused
        was_running = self.running

        # 1. Pause pipeline
        self.paused = True

        # 2. Stop current capture
        if self._capture:
            logger.info("Stopping current audio capture for restart...")
            self._capture.stop()
            self._capture = None

        # 3. Drain audio buffer, overlap, and queue
        self._audio_buffer.clear()
        self._overlap_buffer = None
        self._buffer_duration_ms = 0.0
        while True:
            try:
                self._capture_queue.get_nowait()
            except queue.Empty:
                break

        # 4. Build new AudioCapture with current settings
        audio_config = self.config.get("audio", {})
        mode = audio_config.get("capture_mode", self.capture_mode)
        self.capture_mode = mode

        self._capture = AudioCapture(
            output_queue=self._capture_queue,
            capture_mode=mode,
            sample_rate=audio_config.get("sample_rate", 48000),
            channels=audio_config.get("channels", 2),
            chunk_ms=audio_config.get("chunk_ms", 100),
            target_sample_rate=audio_config.get("target_sample_rate", 16000),
            target_channels=audio_config.get("target_channels", 1),
            device_id=self._current_device_id,
        )

        # 5. Start capture
        try:
            self._capture.start()
            logger.info(f"Audio capture restarted: mode={mode}, device={self._current_device_id}")
            target = self._panel or self._companion
            if target:
                target.add_log(f"采集已重启 (device={self._current_device_id})")
        except RuntimeError as e:
            logger.error(f"Failed to restart capture: {e}")
            target = self._panel or self._companion
            if target:
                target.add_log(f"设备切换失败: {e}")
            if self._panel:
                self._panel._status_indicator.set_state("error")
            if self._companion:
                self._companion.set_state("intense")
            self.running = False
            return

        # 6. Restore state
        self.paused = was_paused
        self.running = was_running

    def _toggle_capture(self) -> None:
        """Toggle capture on/off (called by START/PAUSE buttons)."""
        if self.paused:
            self.paused = False
            self._audio_buffer.clear()
            self._overlap_buffer = None
            self._buffer_duration_ms = 0.0
            logger.info("Capture resumed")
            if self._state_machine:
                self._state_machine.on_resume()
            if self._companion:
                self._companion.set_capturing(True)
        else:
            self.paused = True
            self._audio_buffer.clear()
            self._overlap_buffer = None
            self._buffer_duration_ms = 0.0
            logger.info("Capture paused")
            if self._state_machine:
                self._state_machine.on_pause()
            if self._companion:
                self._companion.set_capturing(False)


# ============================================================================
# CLI Entry Point
# ============================================================================

def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Real-time bidirectional EN↔ZH voice translation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.main                              Loopback mode (EN→ZH, default)
  python -m src.main --mode microphone            Microphone mode (ZH→EN)
  python -m src.main --config config.json          Custom config
  python -m src.main --list-devices                List audio devices and exit
  python -m src.main --no-ui                       Console-only mode (no overlay)
  python -m src.main --log-level DEBUG             Verbose logging
        """,
    )
    parser.add_argument("--config", "-c", default=None,
                        help="Path to config.json")
    parser.add_argument("--mode", "-m", default=None,
                        choices=["loopback", "microphone"],
                        help="Capture mode (overrides config.json). loopback=EN→ZH, microphone=ZH→EN")
    parser.add_argument("--no-ui", action="store_true",
                        help="Run without subtitle overlay (console output only)")
    parser.add_argument("--list-devices", action="store_true",
                        help="List all audio devices and exit")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="Logging level")
    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # List devices mode
    if args.list_devices:
        list_all_devices()
        return 0

    # Load config
    try:
        config = load_config(args.config)
    except FileNotFoundError:
        logger.error(f"Config file not found. Create config.json first.")
        return 1
    except json.JSONDecodeError as e:
        logger.error(f"Invalid config JSON: {e}")
        return 1

    # Override config capture_mode with CLI arg
    if args.mode:
        config.setdefault("audio", {})["capture_mode"] = args.mode

    capture_mode = config.get("audio", {}).get("capture_mode", "loopback")

    # Create controller
    controller = PipelineController(config, capture_mode=capture_mode)

    # Handle Ctrl+C gracefully
    def handle_shutdown(sig=None, frame=None):
        logger.info("Received shutdown signal")
        controller.stop()
        # Qt quit
        app = QApplication.instance()
        if app:
            app.quit()

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    # For Windows console
    try:
        # Enable ANSI escape sequences in Windows console
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass

    # Run
    try:
        controller.start(show_ui=not args.no_ui)
        return 0
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 1
    finally:
        if controller.running:
            controller.stop()


if __name__ == "__main__":
    sys.exit(main())
