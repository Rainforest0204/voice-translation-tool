"""
Audio capture with dual-mode support: WASAPI loopback (speaker output)
and microphone input.

- loopback mode: Captures system/game audio output (EN → ZH translation)
- microphone mode: Captures microphone input (ZH → EN translation)

Uses sounddevice (PortAudio backend) for cross-platform audio I/O.
"""
import threading
import time
import logging
from dataclasses import dataclass
from typing import Optional, Callable
import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AudioChunk:
    """Single buffer of audio data traveling the pipeline."""
    data: np.ndarray       # float32 mono, shape (n_samples,)
    sample_rate: int       # 16000
    timestamp: float       # monotonic capture time (time.monotonic)
    duration_ms: float     # duration in milliseconds
    sequence_id: int       # monotonic sequence number


def find_loopback_device() -> Optional[int]:
    """Find a WASAPI loopback device for capturing system audio output.

    Only returns true loopback devices — NEVER falls back to microphones.
    Prefers WASAPI devices (best compatibility), falls back to WDM-KS if needed.
    Returns the device index, or None if no loopback device found.
    """
    devices = sd.query_devices()
    logger.info("Searching for loopback devices...")

    # Strategy 1: WASAPI devices with "Loopback" in the name (Windows 10/11)
    for idx, dev in enumerate(devices):
        name_lower = dev["name"].lower()
        hostapi_name = sd.query_hostapis(dev["hostapi"])["name"]
        if dev["max_input_channels"] >= 2:
            if "loopback" in name_lower and "wasapi" in hostapi_name.lower():
                logger.info(f"Found WASAPI loopback [{idx}]: {dev['name']}")
                return idx

    # Strategy 2: WASAPI "Stereo Mix" (prefer over WDM-KS for compatibility)
    for idx, dev in enumerate(devices):
        name_lower = dev["name"].lower()
        hostapi_name = sd.query_hostapis(dev["hostapi"])["name"]
        if dev["max_input_channels"] >= 2 and "wasapi" in hostapi_name.lower():
            if "stereo mix" in name_lower or "立体声混音" in name_lower:
                logger.info(f"Found WASAPI Stereo Mix [{idx}]: {dev['name']}")
                return idx

    # Strategy 3: WASAPI speaker/render devices (not microphones)
    for idx, dev in enumerate(devices):
        name = dev["name"]
        hostapi_name = sd.query_hostapis(dev["hostapi"])["name"]
        if dev["max_input_channels"] >= 2 and "WASAPI" in hostapi_name:
            name_lower = name.lower()
            mic_keywords = ["mic", "microphone", "麦克风", "话筒", "headset", "headphone",
                          "webcam", "camera", "front", "array"]
            is_mic = any(kw in name_lower for kw in mic_keywords)
            if not is_mic:
                speaker_keywords = ["speaker", "扬声器", "output", "render", "混音",
                                  "realtek", "nvidia", "amd", "intel", "display",
                                  "monitor", "显示器", "声音"]
                is_speaker = any(kw in name_lower for kw in speaker_keywords)
                if is_speaker:
                    logger.info(f"Found WASAPI speaker loopback [{idx}]: {dev['name']}")
                    return idx

    # Strategy 4: Last WASAPI resort — any 2+ ch input not clearly a mic
    for idx, dev in enumerate(devices):
        hostapi_name = sd.query_hostapis(dev["hostapi"])["name"]
        if dev["max_input_channels"] >= 2 and "WASAPI" in hostapi_name:
            name_lower = dev["name"].lower()
            mic_keywords = ["mic", "microphone", "麦克风", "话筒"]
            if not any(kw in name_lower for kw in mic_keywords):
                logger.warning(f"Fallback WASAPI loopback [{idx}]: {dev['name']}")
                return idx

    # Strategy 5: WDM-KS Stereo Mix (may require callback mode, handled by caller)
    for idx, dev in enumerate(devices):
        name_lower = dev["name"].lower()
        if dev["max_input_channels"] >= 2:
            if "stereo mix" in name_lower or "立体声混音" in name_lower:
                logger.info(f"Found WDM-KS Stereo Mix [{idx}]: {dev['name']} "
                           "(will use callback mode)")
                return idx

    logger.error("No loopback device found! "
                 "Enable 'Stereo Mix' in Windows Sound settings or install a virtual audio cable.")
    return None


def find_microphone_device() -> Optional[int]:
    """Find a suitable microphone device for capturing user voice.

    Prefers WASAPI devices, then falls back to MME.
    Returns the device index, or None if no microphone found.
    """
    devices = sd.query_devices()
    logger.info("Searching for microphone devices...")

    # Strategy 1: WASAPI microphone with "Microphone" or "Mic" in name
    for idx, dev in enumerate(devices):
        name_lower = dev["name"].lower()
        hostapi_name = sd.query_hostapis(dev["hostapi"])["name"]
        if dev["max_input_channels"] >= 1:
            if "wasapi" in hostapi_name.lower():
                mic_keywords = ["mic", "microphone", "麦克风", "话筒", "headset"]
                if any(kw in name_lower for kw in mic_keywords):
                    logger.info(f"Found WASAPI microphone [{idx}]: {dev['name']} "
                               f"(ch={dev['max_input_channels']})")
                    return idx

    # Strategy 2: Any WASAPI input with 1+ channel
    for idx, dev in enumerate(devices):
        hostapi_name = sd.query_hostapis(dev["hostapi"])["name"]
        if dev["max_input_channels"] >= 1 and "WASAPI" in hostapi_name:
            name_lower = dev["name"].lower()
            # Exclude loopback/speaker devices
            if "loopback" not in name_lower and "speaker" not in name_lower:
                logger.info(f"Using WASAPI input [{idx}]: {dev['name']} "
                           f"(ch={dev['max_input_channels']})")
                return idx

    # Strategy 3: MME microphone
    for idx, dev in enumerate(devices):
        hostapi_name = sd.query_hostapis(dev["hostapi"])["name"]
        if dev["max_input_channels"] >= 1 and "MME" in hostapi_name:
            name_lower = dev["name"].lower()
            if "mapper" not in name_lower:
                logger.info(f"Using MME input [{idx}]: {dev['name']}")
                return idx

    logger.error("No microphone device found!")
    return None


def resolve_device(device_id: Optional[str]) -> tuple[Optional[str], Optional[int]]:
    """Parse a unified device_id into (backend, backend_index).

    Args:
        device_id: "wasapi:0", "sounddevice:5", or None for auto-detect.

    Returns:
        (backend_str, backend_index) — backend_str is "wasapi" or "sounddevice".
        Returns (None, None) if device_id is None.
    """
    if device_id is None:
        return (None, None)
    if ":" not in str(device_id):
        try:
            return ("sounddevice", int(device_id))
        except (ValueError, TypeError):
            return (None, None)
    backend, idx_str = str(device_id).split(":", 1)
    try:
        return (backend, int(idx_str))
    except (ValueError, TypeError):
        return (None, None)


def list_devices_for_mode(capture_mode: str) -> list[dict]:
    """Return all available audio devices for a given capture mode.

    Args:
        capture_mode: "loopback" or "microphone"

    Returns:
        List of dicts with keys: device_id, name, channels, sample_rate,
        backend, backend_index, is_default, host_api.
    """
    results = []

    if capture_mode == "loopback":
        # --- WASAPI render endpoints (native loopback, preferred) ---
        try:
            from src.wasapi_loopback_capture import list_wasapi_render_devices
            wasapi_devs = list_wasapi_render_devices()
            for d in wasapi_devs:
                results.append({
                    "device_id": f"wasapi:{d['index']}",
                    "name": d["name"],
                    "channels": d["channels"],
                    "sample_rate": d["sample_rate"],
                    "backend": "wasapi",
                    "backend_index": d["index"],
                    "is_default": d.get("is_default", d["index"] == 0),
                    "host_api": "WASAPI (loopback)",
                })
        except Exception as e:
            logger.warning(f"Failed to list WASAPI render devices: {e}")

        # --- sounddevice loopback devices (Stereo Mix / WDM-KS fallback) ---
        for idx, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] < 2:
                continue
            hostapi_name = sd.query_hostapis(dev["hostapi"])["name"]
            name_lower = dev["name"].lower()
            is_loopback = (
                "loopback" in name_lower
                or "stereo mix" in name_lower
                or "立体声混音" in name_lower
            )
            if is_loopback:
                results.append({
                    "device_id": f"sounddevice:{idx}",
                    "name": dev["name"],
                    "channels": dev["max_input_channels"],
                    "sample_rate": int(dev["default_samplerate"]),
                    "backend": "sounddevice",
                    "backend_index": idx,
                    "is_default": False,
                    "host_api": hostapi_name,
                })

    else:  # microphone mode
        for idx, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] < 1:
                continue
            hostapi_name = sd.query_hostapis(dev["hostapi"])["name"]
            name_lower = dev["name"].lower()
            # Exclude loopback/speaker/output-only devices
            if "loopback" in name_lower or "speaker" in name_lower or "扬声器" in name_lower:
                continue
            if "mapper" in name_lower and "input" not in name_lower:
                continue
            is_mic = any(kw in name_lower for kw in [
                "mic", "microphone", "麦克风", "话筒", "headset", "耳机"
            ])
            results.append({
                "device_id": f"sounddevice:{idx}",
                "name": dev["name"],
                "channels": dev["max_input_channels"],
                "sample_rate": int(dev["default_samplerate"]),
                "backend": "sounddevice",
                "backend_index": idx,
                "is_default": is_mic,
                "host_api": hostapi_name,
            })

    return results


class AudioCapture:
    """Captures audio in a background thread with dual-mode support.

    Supports two capture modes:
    - "loopback": Captures system speaker output (for EN→ZH translation)
    - "microphone": Captures microphone input (for ZH→EN translation)

    Usage:
        import queue
        q = queue.Queue()
        cap = AudioCapture(output_queue=q, capture_mode="loopback")
        cap.start()
        # ... audio chunks appear in q ...
        cap.stop()
    """

    # Mapping from capture mode to (target_sample_rate, target_channels, source_language)
    MODE_CONFIG = {
        "loopback": {
            "label": "扬声器回采",
            "target_sample_rate": 16000,
            "target_channels": 1,
            "source_lang": "en",
        },
        "microphone": {
            "label": "麦克风",
            "target_sample_rate": 16000,
            "target_channels": 1,
            "source_lang": "zh",
        },
    }

    def __init__(
        self,
        output_queue,
        capture_mode: str = "loopback",
        sample_rate: int = 48000,
        channels: int = 2,
        chunk_ms: int = 100,
        target_sample_rate: int = 16000,
        target_channels: int = 1,
        device_id: Optional[str] = None,
    ):
        if capture_mode not in self.MODE_CONFIG:
            raise ValueError(f"Invalid capture_mode: {capture_mode}. "
                           f"Must be one of: {list(self.MODE_CONFIG.keys())}")

        self.capture_mode = capture_mode
        self.output_queue = output_queue
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_ms = chunk_ms
        self.device_id = device_id  # "wasapi:N", "sounddevice:N", or None

        # Mode-specific defaults (can be overridden by caller)
        mode_defaults = self.MODE_CONFIG[capture_mode]
        self.target_sample_rate = target_sample_rate or mode_defaults["target_sample_rate"]
        self.target_channels = target_channels or mode_defaults["target_channels"]

        # Resolved at start() time
        self.device_index: Optional[int] = None  # native backend index

        self._stream: Optional[sd.InputStream] = None
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._seq = 0
        self._buffer_chunks: list[np.ndarray] = []  # accumulate for resampling

    def start(self) -> None:
        """Start audio capture in a background thread.

        Loopback mode: tries native WASAPI loopback first (no Stereo Mix needed),
        falls back to sounddevice with Stereo Mix / WDM-KS.
        Microphone mode: uses sounddevice with WASAPI/MME microphone.

        Resolves device_id ("wasapi:N" / "sounddevice:N") into the
        correct backend and native device index.
        """
        if self._running.is_set():
            logger.warning("Capture already running")
            return

        # Resolve unified device_id → (backend, backend_index)
        backend, backend_idx = resolve_device(self.device_id)
        self._use_native_wasapi = False

        if self.capture_mode == "loopback":
            # Try native WASAPI loopback first (unless user explicitly chose sounddevice)
            if backend is None or backend == "wasapi":
                try:
                    from src.wasapi_loopback_capture import (
                        WasapiLoopbackCapture, list_wasapi_render_devices,
                    )

                    # Show available devices
                    render_devs = list_wasapi_render_devices()
                    if render_devs:
                        logger.info(f"Found {len(render_devs)} WASAPI render device(s):")
                        for d in render_devs:
                            tag = " [DEFAULT]" if d["is_default"] else ""
                            logger.info(f"  [{d['index']}] {d['name']}: "
                                       f"{d['channels']}ch {d['sample_rate']}Hz{tag}")

                    # Use specified backend_idx (wasapi:N) or auto-detect (None)
                    wasapi_dev_idx = backend_idx  # may be None = default
                    self._wasapi_capture = WasapiLoopbackCapture(
                        output_queue=self.output_queue,
                        sample_rate=self.sample_rate,
                        channels=self.channels,
                        chunk_ms=self.chunk_ms,
                        target_sample_rate=self.target_sample_rate,
                        target_channels=self.target_channels,
                        device_index=wasapi_dev_idx,
                    )
                    self._use_native_wasapi = True
                    if wasapi_dev_idx is not None:
                        logger.info(f"Using native WASAPI loopback (device [{wasapi_dev_idx}])")
                    else:
                        logger.info("Using native WASAPI loopback (default audio output)")
                except Exception as e:
                    logger.warning(f"Native WASAPI loopback unavailable: {e}")
                    logger.info("Falling back to sounddevice loopback devices...")

        # Fallback to sounddevice for microphone mode, explicit sounddevice backend,
        # or if WASAPI failed
        if not self._use_native_wasapi:
            if backend_idx is None:
                # Auto-detect
                if self.capture_mode == "loopback":
                    self.device_index = find_loopback_device()
                elif self.capture_mode == "microphone":
                    self.device_index = find_microphone_device()
            else:
                self.device_index = backend_idx

            if self.device_index is None:
                mode_label = self.MODE_CONFIG[self.capture_mode]["label"]
                if self.capture_mode == "loopback":
                    raise RuntimeError(
                        f"未找到扬声器回采设备（{mode_label}）。\n"
                        "请启用 Windows 的「立体声混音」或安装虚拟声卡。"
                    )
                else:
                    raise RuntimeError(
                        f"未找到麦克风设备（{mode_label}）。\n"
                        "请检查麦克风是否已连接并在系统设置中启用。"
                    )

            device_info = sd.query_devices(self.device_index)
            actual_channels = min(self.channels, device_info["max_input_channels"])
            if actual_channels != self.channels:
                logger.info(f"Device has {device_info['max_input_channels']} input channels, "
                           f"using {actual_channels}")
                self.channels = actual_channels

            mode_label = self.MODE_CONFIG[self.capture_mode]["label"]
            logger.info(f"Audio capture mode: {mode_label}")
            logger.info(f"Device [{self.device_index}]: {device_info['name']}")
            logger.info(f"Format: {self.sample_rate}Hz, {self.channels}ch, "
                        f"{self.chunk_ms}ms chunks → {self.target_sample_rate}Hz, "
                        f"{self.target_channels}ch")

            self._frames_per_chunk = int(self.sample_rate * self.chunk_ms / 1000)

        self._running.set()
        self._thread = threading.Thread(
            target=self._capture_loop,
            name=f"AudioCapture-{self.capture_mode}",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"Audio capture thread started (mode={self.capture_mode})")

    def stop(self) -> None:
        """Stop audio capture and wait for thread to finish."""
        self._running.clear()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        logger.info("Audio capture stopped")

    @property
    def is_capturing(self) -> bool:
        return self._running.is_set()

    def _capture_loop(self) -> None:
        """Main capture loop — delegates to the appropriate backend."""
        if self._use_native_wasapi:
            # Delegate to native WASAPI loopback, sharing our running event
            self._wasapi_capture._running = self._running
            self._wasapi_capture._capture_loop()
            return

        try:
            device_info = sd.query_devices(self.device_index)
            hostapi_name = sd.query_hostapis(device_info["hostapi"])["name"]
            use_callback = "wdm-ks" in hostapi_name.lower()

            if use_callback:
                logger.info("WDM-KS device detected, using callback mode")
                self._capture_loop_callback()
            else:
                self._capture_loop_blocking()

        except Exception as e:
            if self._running.is_set():
                logger.error(f"Capture error: {e}", exc_info=True)
        finally:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None

    def _capture_loop_blocking(self) -> None:
        """Blocking read loop — works with WASAPI, MME, DirectSound."""
        self._stream = sd.InputStream(
            device=self.device_index,
            channels=self.channels,
            samplerate=self.sample_rate,
            blocksize=self._frames_per_chunk,
            dtype=np.int16,
            latency="low",
            callback=None,            # blocking mode
        )
        self._stream.start()

        while self._running.is_set():
            data, overflowed = self._stream.read(self._frames_per_chunk)
            if data is None or len(data) == 0:
                continue

            if overflowed:
                logger.debug("Audio buffer overflow — dropped samples")

            chunk = self._process_chunk(data)
            if chunk is not None:
                self.output_queue.put(chunk)

    def _capture_loop_callback(self) -> None:
        """Callback-based capture — required for WDM-KS devices.

        The callback runs in a PortAudio-internal thread. We push processed
        chunks into the queue from the callback. A threading.Event keeps
        the main capture thread alive until stop() is called.
        """
        def audio_callback(indata: np.ndarray, frames: int,
                          time_info, status) -> None:
            """PortAudio callback — called when new audio is available."""
            if status:
                logger.debug(f"Audio callback status: {status}")
            if not self._running.is_set():
                return

            try:
                chunk = self._process_chunk(indata.copy())
                if chunk is not None:
                    self.output_queue.put(chunk)
            except Exception as e:
                logger.error(f"Callback processing error: {e}")

        self._stream = sd.InputStream(
            device=self.device_index,
            channels=self.channels,
            samplerate=self.sample_rate,
            blocksize=self._frames_per_chunk,
            dtype=np.int16,
            latency="low",
            callback=audio_callback,   # callback mode
        )
        self._stream.start()

        # Just wait until stopped — audio is handled in the callback
        while self._running.is_set():
            time.sleep(0.1)

    def _process_chunk(self, data: np.ndarray) -> Optional[AudioChunk]:
        """Resample and convert captured audio to standard format.

        Input: int16 at self.sample_rate (e.g., 48000 Hz)
        Output: float32 mono at target_sample_rate (16000 Hz)
        """
        try:
            # Convert int16 → float32 [-1.0, 1.0]
            audio = data.astype(np.float32) / 32768.0

            # Multi-channel → mono (average channels)
            if audio.ndim == 2 and audio.shape[1] >= 2:
                audio = audio.mean(axis=1)
            elif audio.ndim == 2 and audio.shape[1] == 1:
                audio = audio.squeeze(1)  # (N,1) → (N,)

            # Resample to target rate
            if self.sample_rate != self.target_sample_rate:
                audio = self._resample(audio, self.sample_rate, self.target_sample_rate)

            # Duration calculation
            duration_ms = len(audio) / self.target_sample_rate * 1000

            self._seq += 1
            return AudioChunk(
                data=audio.astype(np.float32),
                sample_rate=self.target_sample_rate,
                timestamp=time.monotonic(),
                duration_ms=duration_ms,
                sequence_id=self._seq,
            )
        except Exception as e:
            logger.error(f"Error processing chunk: {e}", exc_info=True)
            return None

    @staticmethod
    def _resample(data: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
        """High-quality resampling: scipy FFT > scipy polyphase > numpy linear."""
        # Ensure 1D
        if data.ndim == 2 and data.shape[1] == 1:
            data = data.squeeze(1)
        if data.ndim != 1:
            logger.warning(f"Unexpected audio shape {data.shape}, forcing to 1D")
            data = data.ravel()

        duration = len(data) / orig_sr
        target_len = int(duration * target_sr)
        if target_len < 1:
            return data.astype(np.float32)

        # Try scipy FFT-based resampling (best quality)
        try:
            from scipy.signal import resample
            return resample(data, target_len).astype(np.float32)
        except ImportError:
            pass

        # Try scipy polyphase (efficient rational resampling)
        try:
            from scipy.signal import resample_poly
            from math import gcd
            g = gcd(orig_sr, target_sr)
            up = target_sr // g
            down = orig_sr // g
            if up <= 1000 and down <= 1000:
                return resample_poly(data, up, down).astype(np.float32)
        except (ImportError, ValueError):
            pass

        # Numpy linear interpolation (fallback)
        x_orig = np.linspace(0, duration, len(data))
        x_new = np.linspace(0, duration, target_len)
        return np.interp(x_new, x_orig, data).astype(np.float32)


def list_all_devices() -> None:
    """Print all audio devices for debugging."""
    print("\n" + "=" * 80)
    print("AUDIO DEVICES (sounddevice/PortAudio)")
    print("=" * 80)
    devices = sd.query_devices()
    for idx, dev in enumerate(devices):
        hostapi = sd.query_hostapis(dev["hostapi"])
        print(f"\n[{idx}] {dev['name']}")
        print(f"    Inputs: {dev['max_input_channels']}, Outputs: {dev['max_output_channels']}")
        print(f"    Default SR: {dev['default_samplerate']:.0f} Hz")
        print(f"    Host API: {hostapi['name']}")
        # Tag loopback and microphone devices
        name_lower = dev["name"].lower()
        tags = []
        if "loopback" in name_lower or "stereo mix" in name_lower or "立体声混音" in name_lower:
            tags.append("LOOPBACK")
        if any(kw in name_lower for kw in ["mic", "microphone", "麦克风", "话筒"]):
            tags.append("MICROPHONE")
        if tags:
            print(f"    >>> {', '.join(tags)} <<<")

    # Also show WASAPI render endpoints for loopback
    print("\n" + "=" * 80)
    print("WASAPI RENDER ENDPOINTS (for loopback capture)")
    print("=" * 80)
    try:
        from src.wasapi_loopback_capture import list_wasapi_render_devices
        render_devs = list_wasapi_render_devices()
        if render_devs:
            for d in render_devs:
                tag = " [DEFAULT]" if d["is_default"] else ""
                print(f"\n[{d['index']}] {d['name']}{tag}")
                print(f"    Channels: {d['channels']}, Sample Rate: {d['sample_rate']}Hz")
                print(f"    ID: {d['device_id']}")
        else:
            print("  No WASAPI render endpoints found")
    except Exception as e:
        print(f"  Error listing WASAPI endpoints: {e}")


if __name__ == "__main__":
    # Quick test: enumerate devices
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    list_all_devices()
