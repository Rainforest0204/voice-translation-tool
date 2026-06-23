"""
Native WASAPI loopback capture for Windows 10/11.

Captures system audio output directly via Windows Core Audio API
(AUDCLNT_STREAMFLAGS_LOOPBACK). No need for Stereo Mix or virtual cables.

Uses ctypes to call WASAPI COM interfaces — zero additional dependencies.

COM vtable layout (0-indexed, includes IUnknown methods):

IMMDeviceEnumerator:
  [3] EnumAudioEndpoints  [4] GetDefaultAudioEndpoint  [5] GetDevice

IMMDevice:
  [3] Activate  [4] OpenPropertyStore  [5] GetId  [6] GetState

IAudioClient:
  [3] Initialize  [4] GetBufferSize  [8] GetMixFormat
  [10] Start  [11] Stop  [13] SetEventHandle  [14] GetService

IAudioCaptureClient:
  [3] GetBuffer  [4] ReleaseBuffer  [5] GetNextPacketSize

IMMDeviceCollection:
  [3] GetCount  [4] Item
"""
import ctypes
from ctypes import wintypes, byref, POINTER, cast, c_void_p
from ctypes.wintypes import DWORD, UINT, WORD, INT, HANDLE, LPVOID
import threading
import time
import logging
import queue
import numpy as np
from typing import Optional

logger = logging.getLogger(__name__)

# ============================================================================
# COM vtable index constants (indices into the vtable array, 0-based)
# ============================================================================
# IUnknown
VTBL_QUERY_INTERFACE = 0
VTBL_ADD_REF = 1
VTBL_RELEASE = 2

# IMMDeviceEnumerator
VTBL_ENUM_AUDIO_ENDPOINTS = 3
VTBL_GET_DEFAULT_AUDIO_ENDPOINT = 4
VTBL_GET_DEVICE = 5

# IMMDevice
VTBL_DEV_ACTIVATE = 3
VTBL_DEV_OPEN_PROPERTY_STORE = 4
VTBL_DEV_GET_ID = 5
VTBL_DEV_GET_STATE = 6

# IAudioClient
VTBL_AC_INITIALIZE = 3
VTBL_AC_GET_BUFFER_SIZE = 4
VTBL_AC_GET_STREAM_LATENCY = 5
VTBL_AC_GET_CURRENT_PADDING = 6
VTBL_AC_IS_FORMAT_SUPPORTED = 7
VTBL_AC_GET_MIX_FORMAT = 8
VTBL_AC_GET_DEVICE_PERIOD = 9
VTBL_AC_START = 10
VTBL_AC_STOP = 11
VTBL_AC_RESET = 12
VTBL_AC_SET_EVENT_HANDLE = 13
VTBL_AC_GET_SERVICE = 14

# IAudioCaptureClient
VTBL_CC_GET_BUFFER = 3
VTBL_CC_RELEASE_BUFFER = 4
VTBL_CC_GET_NEXT_PACKET_SIZE = 5

# IMMDeviceCollection
VTBL_COLL_GET_COUNT = 3
VTBL_COLL_ITEM = 4

# ============================================================================
# Windows types & constants
# ============================================================================

HRESULT = ctypes.c_ulong
REFERENCE_TIME = ctypes.c_longlong
LPCGUID = ctypes.c_void_p

class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", DWORD),
        ("Data2", WORD),
        ("Data3", WORD),
        ("Data4", ctypes.c_byte * 8),
    ]

# CLSID_MMDeviceEnumerator: {BCDE0395-E52F-467C-8E3D-C4579291692E}
CLSID_MMDeviceEnumerator = GUID(
    0xBCDE0395, 0xE52F, 0x467C,
    (0x8E, 0x3D, 0xC4, 0x57, 0x92, 0x91, 0x69, 0x2E)
)
IID_IMMDeviceEnumerator = GUID(
    0xA95664D2, 0x9614, 0x4F35,
    (0xA7, 0x46, 0xDE, 0x8D, 0xB6, 0x36, 0x17, 0xE6)
)
IID_IAudioClient = GUID(
    0x1CB9AD4C, 0xDBFA, 0x4C32,
    (0xB1, 0x78, 0xC2, 0xF5, 0x68, 0xA7, 0x03, 0xB2)
)
IID_IAudioCaptureClient = GUID(
    0xC8ADBD64, 0xE71E, 0x48A0,
    (0xA4, 0xDE, 0x18, 0x5C, 0x39, 0x5C, 0xD3, 0x17)
)

AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000
WAVE_FORMAT_IEEE_FLOAT = 3
S_OK = 0
AUDCLNT_E_DEVICE_INVALIDATED = 0x88890004
AUDCLNT_S_BUFFER_EMPTY = 0x08890001

eRender = 0
eConsole = 0


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", WORD),
        ("nChannels", WORD),
        ("nSamplesPerSec", DWORD),
        ("nAvgBytesPerSec", DWORD),
        ("nBlockAlign", WORD),
        ("wBitsPerSample", WORD),
        ("cbSize", WORD),
    ]


def _com_init():
    """Initialize COM for current thread (apartment-threaded)."""
    hr = ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
    return hr == S_OK or hr == 0x00000001  # S_OK or S_FALSE (already initialized)


def _com_uninit():
    ctypes.windll.ole32.CoUninitialize()


# ============================================================================
# Helper: call a COM method by vtable index
# ============================================================================

def _vtbl_call(ptr, index, restype, argtypes, *args):
    """Call a COM interface method by vtable index."""
    vtbl = cast(ptr, POINTER(c_void_p)).contents
    vtbl = cast(vtbl, POINTER(c_void_p))
    func = ctypes.WINFUNCTYPE(restype, *argtypes)
    fn = cast(vtbl[index], func)
    return fn(ptr, *args)


def _vtbl_release(ptr):
    """Release a COM interface."""
    try:
        return _vtbl_call(ptr, VTBL_RELEASE, DWORD, [c_void_p])
    except Exception:
        return 0


# ============================================================================
# WasapiLoopbackCapture
# ============================================================================

class WasapiLoopbackCapture:
    """Captures system audio output via WASAPI loopback on Windows.

    Works on any Windows 10/11 system — no Stereo Mix or virtual cables needed.
    Captures whatever is playing through the default system audio output.

    Usage:
        q = queue.Queue()
        cap = WasapiLoopbackCapture(output_queue=q)
        cap.start()
        # audio chunks appear in q (float32, 16kHz mono)
        cap.stop()
    """

    def __init__(
        self,
        output_queue,
        sample_rate: int = 48000,
        channels: int = 2,
        chunk_ms: int = 100,
        target_sample_rate: int = 16000,
        target_channels: int = 1,
        device_index: Optional[int] = None,
    ):
        self.output_queue = output_queue
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_ms = chunk_ms
        self.target_sample_rate = target_sample_rate
        self.target_channels = target_channels
        self.device_index = device_index  # None = default render endpoint

        self._running = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._seq = 0

    def start(self) -> None:
        """Start WASAPI loopback capture in a background thread."""
        if self._running.is_set():
            logger.warning("Loopback capture already running")
            return

        logger.info("Starting native WASAPI loopback capture "
                    f"({self.sample_rate}Hz → {self.target_sample_rate}Hz mono)")

        self._running.set()
        self._thread = threading.Thread(
            target=self._capture_loop,
            name="WasapiLoopback",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop loopback capture."""
        self._running.clear()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        logger.info("WASAPI loopback capture stopped")

    @property
    def is_capturing(self) -> bool:
        return self._running.is_set()

    def _capture_loop(self) -> None:
        """Main capture loop — pure WASAPI loopback via COM."""
        ole32 = ctypes.windll.ole32
        kernel32 = ctypes.windll.kernel32

        pEnumerator = None
        pDevice = None
        pAudioClient = None
        pCaptureClient = None
        hEvent = None

        try:
            _com_init()

            # ── 1. Create MMDeviceEnumerator ──
            pEnumerator = c_void_p()
            hr = ole32.CoCreateInstance(
                byref(CLSID_MMDeviceEnumerator), None, 0x1,  # CLSCTX_INPROC_SERVER
                byref(IID_IMMDeviceEnumerator), byref(pEnumerator)
            )
            if hr != S_OK:
                raise RuntimeError(f"CoCreateInstance(MMDeviceEnumerator) failed: 0x{hr:08X}")

            # ── 2. Get audio render endpoint (default or specific device) ──
            pDevice = c_void_p()
            if self.device_index is not None:
                # Use specific device by enumerating and selecting by index
                pCollection = c_void_p()
                hr = _vtbl_call(pEnumerator, VTBL_ENUM_AUDIO_ENDPOINTS,
                              HRESULT, [c_void_p, DWORD, DWORD, POINTER(c_void_p)],
                              eRender, 0x1, byref(pCollection))
                if hr != S_OK:
                    raise RuntimeError(f"EnumAudioEndpoints failed: 0x{hr:08X}")

                count = UINT(0)
                _vtbl_call(pCollection, VTBL_COLL_GET_COUNT,
                         HRESULT, [c_void_p, POINTER(UINT)], byref(count))

                if self.device_index >= count.value:
                    _vtbl_release(pCollection)
                    raise RuntimeError(
                        f"Device index {self.device_index} out of range "
                        f"(found {count.value} render device(s))"
                    )

                hr = _vtbl_call(pCollection, VTBL_COLL_ITEM,
                              HRESULT, [c_void_p, UINT, POINTER(c_void_p)],
                              UINT(self.device_index), byref(pDevice))
                _vtbl_release(pCollection)
                if hr != S_OK:
                    raise RuntimeError(f"Item({self.device_index}) failed: 0x{hr:08X}")

                logger.info(f"Selected render device [{self.device_index}]")
            else:
                # Use default render endpoint
                hr = _vtbl_call(pEnumerator, VTBL_GET_DEFAULT_AUDIO_ENDPOINT,
                              HRESULT, [c_void_p, DWORD, DWORD, POINTER(c_void_p)],
                              eRender, eConsole, byref(pDevice))
                if hr != S_OK:
                    raise RuntimeError(f"GetDefaultAudioEndpoint failed: 0x{hr:08X}")

            # ── 3. Activate IAudioClient ──
            pAudioClient = c_void_p()
            hr = _vtbl_call(pDevice, VTBL_DEV_ACTIVATE,
                          HRESULT, [c_void_p, LPCGUID, DWORD, c_void_p, POINTER(c_void_p)],
                          byref(IID_IAudioClient), 0x1, None, byref(pAudioClient))
            if hr != S_OK:
                raise RuntimeError(f"Activate(IAudioClient) failed: 0x{hr:08X}")

            # ── 4. GetMixFormat ──
            ppMixFormat = c_void_p()
            hr = _vtbl_call(pAudioClient, VTBL_AC_GET_MIX_FORMAT,
                          HRESULT, [c_void_p, POINTER(c_void_p)],
                          byref(ppMixFormat))
            if hr != S_OK or not ppMixFormat:
                raise RuntimeError(f"GetMixFormat failed: 0x{hr:08X}")

            wfx = cast(ppMixFormat, POINTER(WAVEFORMATEX))
            actual_channels = wfx.contents.nChannels
            actual_sample_rate = wfx.contents.nSamplesPerSec
            ole32.CoTaskMemFree(ppMixFormat)
            logger.info(f"Default render endpoint: {actual_channels}ch {actual_sample_rate}Hz")

            self.sample_rate = actual_sample_rate
            self.channels = actual_channels

            # ── 5. Create event handle ──
            hEvent = kernel32.CreateEventW(None, 0, 0, None)
            if not hEvent:
                raise RuntimeError("CreateEventW failed")

            # ── 6. Initialize with LOOPBACK flag ──
            # For loopback: must use shared mode, format MUST be provided
            # (mix format for loopback = float32)
            REFTIMES_PER_SEC = 10_000_000
            buffer_duration = REFTIMES_PER_SEC // 10  # 100ms buffer (smaller = lower latency)
            flags = AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK

            # Build WAVEFORMATEX for the mix format
            wf = WAVEFORMATEX()
            wf.wFormatTag = WAVE_FORMAT_IEEE_FLOAT
            wf.nChannels = actual_channels
            wf.nSamplesPerSec = actual_sample_rate
            wf.wBitsPerSample = 32
            wf.nBlockAlign = (wf.wBitsPerSample // 8) * wf.nChannels
            wf.nAvgBytesPerSec = wf.nSamplesPerSec * wf.nBlockAlign
            wf.cbSize = 0

            hr = _vtbl_call(pAudioClient, VTBL_AC_INITIALIZE,
                          HRESULT, [c_void_p, DWORD, DWORD, ctypes.c_longlong, ctypes.c_longlong,
                                    c_void_p, LPCGUID],
                          AUDCLNT_SHAREMODE_SHARED, flags,
                          buffer_duration, 0, byref(wf), None)
            if hr != S_OK:
                # Try with NULL format (older Windows behavior)
                hr = _vtbl_call(pAudioClient, VTBL_AC_INITIALIZE,
                              HRESULT, [c_void_p, DWORD, DWORD, ctypes.c_longlong, ctypes.c_longlong,
                                        c_void_p, LPCGUID],
                              AUDCLNT_SHAREMODE_SHARED, flags,
                              buffer_duration, 0, None, None)
            if hr != S_OK:
                raise RuntimeError(f"Initialize(LOOPBACK) failed: 0x{hr:08X}")

            # ── 7. GetBufferSize ──
            buffer_frame_count = UINT(0)
            hr = _vtbl_call(pAudioClient, VTBL_AC_GET_BUFFER_SIZE,
                          HRESULT, [c_void_p, POINTER(UINT)],
                          byref(buffer_frame_count))
            if hr != S_OK:
                raise RuntimeError(f"GetBufferSize failed: 0x{hr:08X}")
            logger.info(f"Loopback buffer: {buffer_frame_count.value} frames")

            # ── 8. SetEventHandle ──
            hr = _vtbl_call(pAudioClient, VTBL_AC_SET_EVENT_HANDLE,
                          HRESULT, [c_void_p, HANDLE],
                          hEvent)
            if hr != S_OK:
                raise RuntimeError(f"SetEventHandle failed: 0x{hr:08X}")

            # ── 9. Get IAudioCaptureClient ──
            pCaptureClient = c_void_p()
            hr = _vtbl_call(pAudioClient, VTBL_AC_GET_SERVICE,
                          HRESULT, [c_void_p, LPCGUID, POINTER(c_void_p)],
                          byref(IID_IAudioCaptureClient), byref(pCaptureClient))
            if hr != S_OK:
                raise RuntimeError(f"GetService(IAudioCaptureClient) failed: 0x{hr:08X}")

            # ── 10. Start audio client ──
            hr = _vtbl_call(pAudioClient, VTBL_AC_START,
                          HRESULT, [c_void_p])
            if hr != S_OK:
                raise RuntimeError(f"Start failed: 0x{hr:08X}")

            logger.info("WASAPI loopback active — capturing system audio")

            # ── 11. Capture loop ──
            while self._running.is_set():
                result = kernel32.WaitForSingleObject(hEvent, 200)
                if result == 0xFFFFFFFF:
                    logger.error("WaitForSingleObject failed")
                    break

                # Drain all available packets
                while self._running.is_set():
                    pData = c_void_p()
                    numFramesAvailable = UINT(0)
                    dwFlags = DWORD(0)
                    u64DevPos = ctypes.c_ulonglong(0)
                    u64QPCPos = ctypes.c_ulonglong(0)

                    hr = _vtbl_call(pCaptureClient, VTBL_CC_GET_BUFFER,
                                  HRESULT, [c_void_p, POINTER(c_void_p), POINTER(UINT),
                                            POINTER(DWORD), POINTER(ctypes.c_ulonglong),
                                            POINTER(ctypes.c_ulonglong)],
                                  byref(pData), byref(numFramesAvailable),
                                  byref(dwFlags), byref(u64DevPos), byref(u64QPCPos))

                    if hr == AUDCLNT_S_BUFFER_EMPTY or hr != S_OK:
                        break

                    frames = numFramesAvailable.value
                    if frames > 0 and pData:
                        # WASAPI loopback always delivers IEEE float
                        frame_size = actual_channels * 4
                        data_size = frames * frame_size
                        buffer = (ctypes.c_byte * data_size).from_address(pData.value)
                        audio = np.frombuffer(buffer, dtype=np.float32)
                        audio = audio.reshape(frames, actual_channels)

                        chunk = self._process_chunk(audio)
                        if chunk is not None:
                            self.output_queue.put(chunk)

                    # Release buffer
                    _vtbl_call(pCaptureClient, VTBL_CC_RELEASE_BUFFER,
                              HRESULT, [c_void_p, UINT],
                              frames)

        except Exception as e:
            if self._running.is_set():
                logger.error(f"WASAPI loopback error: {e}", exc_info=True)
        finally:
            # Stop playback
            if pAudioClient:
                try:
                    _vtbl_call(pAudioClient, VTBL_AC_STOP, HRESULT, [c_void_p])
                except Exception:
                    pass

            # Release resources (reverse order)
            if hEvent:
                kernel32.CloseHandle(hEvent)
            if pCaptureClient:
                _vtbl_release(pCaptureClient)
            if pAudioClient:
                _vtbl_release(pAudioClient)
            if pDevice:
                _vtbl_release(pDevice)
            if pEnumerator:
                _vtbl_release(pEnumerator)

            _com_uninit()

    def _process_chunk(self, data: np.ndarray):
        """Process float32 audio to 16kHz mono AudioChunk."""
        try:
            from src.audio_capture import AudioChunk

            # Stereo → mono
            if data.ndim == 2 and data.shape[1] >= 2:
                audio = data.mean(axis=1)
            elif data.ndim == 2 and data.shape[1] == 1:
                audio = data.squeeze(1)
            else:
                audio = data

            # Resample
            if self.sample_rate != self.target_sample_rate:
                audio = self._resample(audio, self.sample_rate, self.target_sample_rate)

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
            logger.error(f"Error processing WASAPI chunk: {e}", exc_info=True)
            return None

    @staticmethod
    def _resample(data: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
        """High-quality resampling: scipy FFT > scipy polyphase > numpy linear."""
        if data.ndim != 1:
            data = data.ravel()
        duration = len(data) / orig_sr
        target_len = int(duration * target_sr)
        if target_len < 1:
            return data.astype(np.float32)
        try:
            from scipy.signal import resample
            return resample(data, target_len).astype(np.float32)
        except ImportError:
            pass
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
        x_orig = np.linspace(0, duration, len(data))
        x_new = np.linspace(0, duration, target_len)
        return np.interp(x_new, x_orig, data).astype(np.float32)


def list_wasapi_render_devices() -> list[dict]:
    """List all WASAPI render (output) endpoints available for loopback.

    Returns a list of dicts with keys: index, channels, sample_rate, is_default, device_id.
    """
    devices = []
    ole32 = ctypes.windll.ole32

    _com_init()
    try:
        pEnumerator = c_void_p()
        hr = ole32.CoCreateInstance(
            byref(CLSID_MMDeviceEnumerator), None, 0x1,
            byref(IID_IMMDeviceEnumerator), byref(pEnumerator))
        if hr != S_OK:
            return devices

        pCollection = c_void_p()
        hr = _vtbl_call(pEnumerator, VTBL_ENUM_AUDIO_ENDPOINTS,
                      HRESULT, [c_void_p, DWORD, DWORD, POINTER(c_void_p)],
                      eRender, 0x1, byref(pCollection))
        if hr != S_OK:
            _vtbl_release(pEnumerator)
            return devices

        count = UINT(0)
        _vtbl_call(pCollection, VTBL_COLL_GET_COUNT,
                 HRESULT, [c_void_p, POINTER(UINT)], byref(count))

        for i in range(count.value):
            pDevice = c_void_p()
            hr = _vtbl_call(pCollection, VTBL_COLL_ITEM,
                          HRESULT, [c_void_p, UINT, POINTER(c_void_p)],
                          UINT(i), byref(pDevice))
            if hr != S_OK:
                continue

            pAudioClient = c_void_p()
            hr2 = _vtbl_call(pDevice, VTBL_DEV_ACTIVATE,
                           HRESULT, [c_void_p, LPCGUID, DWORD, c_void_p, POINTER(c_void_p)],
                           byref(IID_IAudioClient), 0x1, None, byref(pAudioClient))
            ch, sr = 2, 48000
            if hr2 == S_OK and pAudioClient:
                ppMixFormat = c_void_p()
                hr3 = _vtbl_call(pAudioClient, VTBL_AC_GET_MIX_FORMAT,
                               HRESULT, [c_void_p, POINTER(c_void_p)],
                               byref(ppMixFormat))
                if hr3 == S_OK and ppMixFormat:
                    wfx = cast(ppMixFormat, POINTER(WAVEFORMATEX))
                    ch = wfx.contents.nChannels
                    sr = wfx.contents.nSamplesPerSec
                    ole32.CoTaskMemFree(ppMixFormat)
                _vtbl_release(pAudioClient)

            # Get device name via sounddevice for display
            # Match by index: WASAPI endpoint order = WASAPI output device order in sounddevice
            try:
                import sounddevice as sd
                sd_devs = sd.query_devices()
                wasapi_outputs = []
                for sd_idx, sd_dev in enumerate(sd_devs):
                    hostapi = sd.query_hostapis(sd_dev['hostapi'])['name']
                    if 'wasapi' in hostapi.lower() and sd_dev['max_output_channels'] >= 2:
                        wasapi_outputs.append(sd_dev['name'])
                if i < len(wasapi_outputs):
                    name = wasapi_outputs[i]
                else:
                    name = f"Render Device {i}"
            except Exception:
                name = f"Render Device {i}"

            # Try to get friendly name from device ID
            pwszId = ctypes.c_wchar_p()
            _vtbl_call(pDevice, VTBL_DEV_GET_ID,
                     HRESULT, [c_void_p, POINTER(ctypes.c_wchar_p)],
                     byref(pwszId))
            device_id = pwszId.value if pwszId else ""

            devices.append({
                "index": i,
                "name": name,
                "channels": ch,
                "sample_rate": sr,
                "is_default": (i == 0),
                "device_id": device_id[:80] if device_id else "",
            })
            _vtbl_release(pDevice)

        _vtbl_release(pCollection)
        _vtbl_release(pEnumerator)
    finally:
        _com_uninit()

    return devices

