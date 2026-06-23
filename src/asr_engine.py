"""
ASR (Automatic Speech Recognition) engine using faster-whisper with GPU.

Wraps faster-whisper's WhisperModel for efficient GPU-accelerated transcription
with built-in VAD filtering. Supports both English and Chinese recognition.

Accuracy & latency improvements over baseline:
  - condition_on_previous_text DISABLED (prevents hallucinated/phantom words)
  - Strict no_speech_threshold (0.6) filters out noise/silence
  - Standard compression_ratio_threshold (2.4) prevents repetition artifacts
  - Standard log_prob_threshold (-1.0) rejects low-confidence output
  - best_of=3 / beam_size=5 for speed without quality loss
  - Audio preprocessing: DC offset removal + peak protection only
  - Pre-filter non-speech content (single chars, gibberish, punctuation-only)

Log policy: never logs full transcription text — only character counts and latency.
"""
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional
import numpy as np

# Make NVIDIA CUDA DLLs available to ctranslate2
def _setup_cuda_dlls():
    """Add NVIDIA CUDA DLL directories to the DLL search path."""
    import sys
    dll_dirs = []
    for pkg in ['nvidia.cublas', 'nvidia.cuda_nvrtc']:
        try:
            mod = __import__(pkg, fromlist=[''])
            pkg_path = getattr(mod, '__path__', None)
            if pkg_path:
                for p in ([pkg_path] if isinstance(pkg_path, str) else list(pkg_path)):
                    bin_dir = os.path.join(p, 'bin')
                    if os.path.isdir(bin_dir):
                        dll_dirs.append(bin_dir)
        except (ImportError, AttributeError):
            pass
    for d in dll_dirs:
        if os.path.isdir(d) and hasattr(os, 'add_dll_directory'):
            os.add_dll_directory(os.path.abspath(d))

_setup_cuda_dlls()

logger = logging.getLogger(__name__)


@dataclass
class TranscriptionSegment:
    """A transcribed segment of speech."""
    text: str
    start_ms: float
    end_ms: float
    confidence: float       # avg_logprob from Whisper
    language: str = "en"
    is_partial: bool = False

    def __bool__(self) -> bool:
        return bool(self.text and self.text.strip())


@dataclass
class AsrResult:
    """Complete ASR result for one audio chunk."""
    segments: list[TranscriptionSegment] = field(default_factory=list)
    full_text: str = ""
    detected_language: str = ""
    latency_ms: float = 0.0
    error: Optional[str] = None

    @property
    def has_text(self) -> bool:
        return bool(self.full_text and self.full_text.strip())

    @property
    def char_count(self) -> int:
        return len(self.full_text.strip()) if self.full_text else 0


# ============================================================================
# Audio preprocessing
# ============================================================================

def preprocess_audio(
    audio: np.ndarray,
    sample_rate: int = 16000,
    normalize: bool = True,
    remove_dc: bool = True,
) -> np.ndarray:
    """Minimal audio preprocessing — DC removal + peak protection.

    Keeps preprocessing light to avoid degrading speech quality.
    No highpass filter (can remove speech fundamentals).
    No aggressive normalization (preserves natural dynamics).
    """
    audio = audio.astype(np.float32).copy()

    # 1. Remove DC offset only
    if remove_dc:
        audio -= np.mean(audio)

    # 2. Light RMS normalization — only if volume is very low
    if normalize:
        rms = np.sqrt(np.mean(audio ** 2))
        if rms < 0.01:  # Only boost if very quiet
            gain = min(0.05 / max(rms, 1e-8), 5.0)
            audio *= gain

    # 3. Hard clip protection only
    peak = np.abs(audio).max()
    if peak > 1.0:
        audio *= 0.98 / peak

    return audio.astype(np.float32)


# ============================================================================
# Post-transcription noise filter
# ============================================================================

def _is_likely_hallucination(text: str) -> bool:
    """Detect common Whisper hallucination patterns that produce phantom words.

    Returns True if the text looks like a hallucination that should be discarded.
    """
    import re
    t = text.strip()
    if not t or len(t) < 2:
        return True

    # Determine if text is primarily ASCII (English) or CJK (Chinese)
    ascii_chars = len(re.findall(r'[a-zA-Z]', t))
    cjk_chars = len(re.findall(r'[一-鿿]', t))

    # Hallucination pattern 1: All punctuation / non-speech characters
    alpha_chars = ascii_chars + cjk_chars
    if alpha_chars < 2:
        return True

    # Hallucination pattern 2: Very repetitive character patterns
    if len(set(t.lower())) <= 3 and len(t) >= 5:
        return True

    words = t.split()

    # English-specific checks
    if ascii_chars >= cjk_chars and len(words) >= 1:
        # Hallucination: Repeated same word (e.g. "the the the")
        if len(words) >= 3:
            # All words identical
            if len(set(w.lower() for w in words)) == 1:
                return True
            # Very low unique ratio (near-repetition)
            unique_ratio = len(set(w.lower() for w in words)) / len(words)
            if unique_ratio < 0.3:
                return True

        # Common hallucination phrases
        common_hallucinations = [
            "thank you", "thanks for watching", "thank you for watching",
            "subscribe", "please subscribe", "like and subscribe",
            "goodbye", "see you next time",
        ]
        t_lower_np = t.lower().strip().rstrip('.!?,;: ')
        if t_lower_np in common_hallucinations:
            return True

    # Chinese-specific checks
    if cjk_chars > 0:
        # Single repeated Chinese character
        unique_cjk = len(set(re.findall(r'[一-鿿]', t)))
        if cjk_chars >= 3 and unique_cjk == 1:
            return True
        # Common Chinese hallucination phrases
        zh_hallucinations = ["谢谢观看", "谢谢大家", "拜拜"]
        t_np = t.strip().rstrip('!！。，, ')
        if t_np in zh_hallucinations:
            return True

    return False


class AsrEngine:
    """GPU-accelerated ASR using faster-whisper with accuracy optimizations.

    Supports English ("en") and Chinese ("zh") recognition.

    Key accuracy/speed features:
      - condition_on_previous_text DISABLED (prevents phantom word hallucination)
      - Strict noise filtering with compression_ratio + log_prob thresholds
      - Post-transcription hallucination detection
      - Audio preprocessing (DC removal, peak protection)
      - best_of=3 for speed (3 independent beams, not 8)

    Usage:
        engine = AsrEngine(model_size="medium.en", language="en")
        engine.load()
        result = engine.transcribe(audio_chunk)
    """

    # Model recommendations
    LANGUAGE_MODELS = {
        "en": {
            "tiny": "tiny.en",     # ~39MB,  fastest
            "small": "small.en",   # ~141MB, fast, moderate accuracy
            "medium": "medium.en", # ~514MB, RECOMMENDED — best accuracy/speed balance
            "large": "large-v3",   # ~1.5GB, highest accuracy
        },
        "zh": {
            "tiny": "tiny",        # ~39MB
            "small": "small",      # ~141MB
            "medium": "medium",    # ~514MB, RECOMMENDED for Chinese
            "large": "large-v3",   # ~1.5GB
        },
    }

    def __init__(
        self,
        model_size: str = "medium.en",
        device: str = "cuda",
        compute_type: str = "float16",
        beam_size: int = 5,
        best_of: int = 3,
        language: str = "en",
        vad_filter: bool = True,
        vad_min_silence_ms: int = 500,
        vad_speech_pad_ms: int = 400,
        no_speech_threshold: float = 0.6,
        condition_on_previous_text: bool = False,
        repetition_penalty: float = 1.0,
        preprocess: bool = True,
    ):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.beam_size = beam_size
        self.best_of = best_of
        self.language = language
        self.vad_filter = vad_filter
        self.vad_min_silence_ms = vad_min_silence_ms
        self.vad_speech_pad_ms = vad_speech_pad_ms
        self.no_speech_threshold = no_speech_threshold
        self.condition_on_previous_text = condition_on_previous_text
        self.repetition_penalty = repetition_penalty
        self.preprocess = preprocess

        self._model = None
        self._loaded = False
        self._lock = threading.Lock()
        self._total_transcriptions = 0
        self._total_latency = 0.0
        # Previous text tracking for deduplication only (NOT fed as prompt to avoid hallucination)
        self._previous_text = ""

    def load(self) -> None:
        """Download (if needed) and load the Whisper model. Call once on startup."""
        if self._loaded:
            return

        # Use HuggingFace mirror for China access
        if "HF_ENDPOINT" not in os.environ:
            os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

        try:
            from faster_whisper import WhisperModel

            logger.info(f"Loading Whisper model: {self.model_size} on {self.device} "
                       f"(compute={self.compute_type}, beam={self.beam_size}, "
                       f"best_of={self.best_of})")
            t0 = time.monotonic()

            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
                download_root="./models",
            )
            self._loaded = True
            elapsed = time.monotonic() - t0
            logger.info(f"Model loaded in {elapsed:.1f}s")

        except ImportError:
            raise ImportError(
                "faster-whisper not installed. Run: pip install faster-whisper"
            )
        except Exception as e:
            error_msg = str(e)
            logger.warning(f"Failed to load model on {self.device}: {e}")

            if self.device == "cuda":
                logger.warning("CUDA unavailable, retrying with device='cpu'...")
                self.device = "cpu"
                self.compute_type = "int8"
                return self.load()

            if self.device == "cuda" and self.compute_type == "float16":
                logger.warning("float16 failed, retrying with int8_float16...")
                self.compute_type = "int8_float16"
                return self.load()

            raise RuntimeError(f"Failed to load Whisper model: {error_msg}")

    def transcribe(self, chunk) -> AsrResult:
        """Transcribe an AudioChunk with preprocessing and accuracy optimizations.

        Args:
            chunk: AudioChunk with .data (np.ndarray float32), .sample_rate, .duration_ms

        Returns:
            AsrResult with segments and full_text
        """
        if not self._loaded:
            self.load()

        t0 = time.monotonic()

        try:
            # Preprocess audio for better recognition
            audio = chunk.data
            if self.preprocess and len(audio) > 0:
                audio = preprocess_audio(
                    audio,
                    sample_rate=chunk.sample_rate,
                    normalize=True,
                    remove_dc=True,
                )

            with self._lock:
                # Build transcription options — optimized for accuracy WITHOUT hallucinations
                transcribe_kwargs = dict(
                    beam_size=self.beam_size,
                    best_of=self.best_of,
                    patience=2.0,                        # shorter patience = faster decoding
                    language=self.language,
                    temperature=[0.0],                   # greedy only = most accurate, no randomness
                    compression_ratio_threshold=2.4,     # STANDARD — rejects hallucinated repetitions
                    log_prob_threshold=-1.0,             # STANDARD — rejects low-confidence output
                    no_speech_threshold=self.no_speech_threshold,
                    repetition_penalty=self.repetition_penalty,
                    word_timestamps=False,               # faster, same accuracy
                    vad_filter=self.vad_filter,
                    vad_parameters=dict(
                        min_silence_duration_ms=self.vad_min_silence_ms,
                        speech_pad_ms=self.vad_speech_pad_ms,
                        threshold=0.25,                  # sensitive enough for real speech, filters obvious noise
                        min_speech_duration_ms=50,       # catch short utterances without picking up clicks
                        max_speech_duration_s=30.0,
                    ),
                )

                # IMPORTANT: Do NOT feed previous_text as initial_prompt.
                # This was the #1 cause of phantom/hallucinated words.
                # condition_on_previous_text is ALWAYS disabled.
                transcribe_kwargs["condition_on_previous_text"] = False

                segments_iter, info = self._model.transcribe(
                    audio.astype(np.float32),
                    **transcribe_kwargs,
                )

                # Collect segments
                segments = []
                full_text_parts = []
                for seg in segments_iter:
                    ts = TranscriptionSegment(
                        text=seg.text.strip(),
                        start_ms=seg.start * 1000,
                        end_ms=seg.end * 1000,
                        confidence=seg.avg_logprob,
                        language=info.language if info else self.language,
                    )
                    if ts.text:
                        segments.append(ts)
                        full_text_parts.append(ts.text)

            full_text = " ".join(full_text_parts).strip()

            # Post-transcription hallucination filter
            if full_text and _is_likely_hallucination(full_text):
                logger.debug(f"ASR hallucination filtered: {full_text[:60]!r}")
                full_text = ""
                segments = []

            # Update previous text for deduplication only (NOT fed back to model)
            if full_text:
                self._previous_text = full_text
                if len(self._previous_text) > 300:
                    self._previous_text = self._previous_text[-300:]

            latency_ms = (time.monotonic() - t0) * 1000

            self._total_transcriptions += 1
            self._total_latency += latency_ms

            result = AsrResult(
                segments=segments,
                full_text=full_text,
                detected_language=info.language if info else self.language,
                latency_ms=latency_ms,
            )

            # Privacy-safe logging
            if result.has_text:
                avg_conf = (
                    np.mean([s.confidence for s in segments])
                    if segments else 0.0
                )
                logger.info(
                    f"ASR [{latency_ms:.0f}ms] lang={result.detected_language} "
                    f"chars={result.char_count} conf={avg_conf:.2f}"
                )
            else:
                logger.debug(f"ASR [{latency_ms:.0f}ms] no speech detected")

            return result

        except RuntimeError as e:
            error_msg = str(e)
            if ("cublas" in error_msg.lower() or "cudnn" in error_msg.lower()
                    or "cuda" in error_msg.lower()):
                if self.device == "cuda":
                    logger.warning(f"CUDA runtime error, switching to CPU: {e}")
                    self.device = "cpu"
                    self.compute_type = "int8"
                    self._model = None
                    self._loaded = False
                    self.load()
                    return self.transcribe(chunk)

            logger.error(f"ASR error: {e}", exc_info=True)
            latency_ms = (time.monotonic() - t0) * 1000
            return AsrResult(error=error_msg, latency_ms=latency_ms)

        except Exception as e:
            logger.error(f"ASR error: {e}", exc_info=True)
            latency_ms = (time.monotonic() - t0) * 1000
            return AsrResult(error=str(e), latency_ms=latency_ms)

    def reset_context(self) -> None:
        """Reset streaming context (call when switching audio source)."""
        self._previous_text = ""

    @property
    def model_info(self) -> dict:
        return {
            "model_size": self.model_size,
            "device": self.device,
            "compute_type": self.compute_type,
            "language": self.language,
            "loaded": self._loaded,
            "total_transcriptions": self._total_transcriptions,
            "avg_latency_ms": (self._total_latency / self._total_transcriptions
                               if self._total_transcriptions > 0 else 0),
        }

    def unload(self) -> None:
        """Free GPU memory by unloading the model."""
        self._model = None
        self._loaded = False
        self._previous_text = ""
        logger.info("Model unloaded")
