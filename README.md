# 🎙️ Voice Translation Tool

Real-time **bidirectional** English ↔ Chinese voice translation overlay for Windows.

- **Loopback mode**: Captures system audio (games, videos) → EN speech → ZH subtitles
- **Microphone mode**: Captures your voice → ZH speech → EN subtitles

<p align="center">
  <i>Transparent subtitle overlay + AI Desktop Companion</i>
</p>

---

## ✨ Features

- **Dual mode**: Speaker loopback (EN→ZH) or microphone capture (ZH→EN)
- **AI Desktop Companion**: A sleek floating widget with radial menu, device selection, and state-aware animations
- **Transparent overlay**: Click-through subtitles for fullscreen games
- **GPU-accelerated ASR**: [faster-whisper](https://github.com/SYSTRAN/faster-whisper) on CUDA
- **Smart translation**: Auto-selects DeepSeek or DeepL based on available API keys
- **Noise filtering**: Multi-layer hallucination and silence detection
- **Hotkeys**: Global shortcuts for all controls

## 🚀 Quick Start

### 1. Prerequisites

- Windows 10/11
- Python 3.13+ (3.14 tested)
- NVIDIA GPU with 6+ GB VRAM (for GPU ASR; CPU fallback available)
- API key from [DeepSeek](https://platform.deepseek.com) or [DeepL](https://deepl.com) (free tiers available)

### 2. Install

```bash
git clone https://github.com/YOUR_USERNAME/voice-translation-tool.git
cd voice-translation-tool
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure

```bash
# Copy the example env file
copy .env.example .env

# Edit .env with your API key
# DEEPSEEK_API_KEY=sk-your-key-here
# or
# DEEPL_API_KEY=your-key-here
```

Or edit `config.json` for advanced settings (audio device, ASR model, UI).

### 4. Run

```bash
# Loopback mode (speaker EN→ZH subtitles) — default
python -m src.main

# Microphone mode (your voice ZH→EN subtitles)
python -m src.main --mode microphone

# List audio devices
python -m src.main --list-devices

# Console-only (no UI)
python -m src.main --no-ui
```

Play any English audio/game — translated subtitles appear as a transparent overlay.

### 5. Test

```bash
python scripts/test_companion.py      # Test companion UI
python scripts/test_companion_full.py  # Full interaction test
```

---

## 🖥️ AI Desktop Companion

The Companion is a minimalist floating widget that replaces the traditional control panel:

| Action | Result |
|--------|--------|
| **Click** on companion | Radial menu (Pause / Clear / Devices / Switch Mode / Settings) |
| **Double-click** | Toggle subtitle overlay |
| **Right-click** | Context menu |
| **Drag** | Move companion anywhere |
| **Drag to screen top** | Auto-hide subtitles |
| **Drag to screen bottom** | Auto-show subtitles |

### Configuration

Edit `config.json` → `companion`:

```json
{
  "companion": {
    "enabled": true,
    "mode": "replace",
    "size": 100,
    "initial_position": "center",
    "auto_sleep_sec": 120,
    "voice_commands": {
      "enabled": true,
      "wake_words": ["hey translator", "hey 翻译", "翻译官"]
    }
  }
}
```

To use the classic Control Panel instead: set `"enabled": false`.

---

## ⌨️ Keyboard Shortcuts

| Keys | Action |
|------|--------|
| `Ctrl+Shift+T` | Toggle capture on/off |
| `Ctrl+Shift+H` | Show/hide overlay |
| `Ctrl+Shift+C` | Clear all subtitles |
| `Ctrl+Shift+=` | Increase font size |
| `Ctrl+Shift+-` | Decrease font size |

---

## 🏗️ Architecture

```
┌─ Loopback Mode ──────────────────────────────────────┐
│ Game/System Audio → WASAPI Loopback → 16kHz mono     │
│     → faster-whisper (GPU) → English text            │
│     → DeepSeek/DeepL API → Chinese text              │
│     → PyQt6 Transparent Overlay → Subtitles          │
│     → AI Companion Widget ← State Machine ↕          │
└──────────────────────────────────────────────────────┘

┌─ Microphone Mode ────────────────────────────────────┐
│ Microphone → PortAudio → 16kHz mono                  │
│     → faster-whisper (GPU) → Chinese text            │
│     → DeepSeek/DeepL API → English text              │
│     → PyQt6 Transparent Overlay → Subtitles          │
└──────────────────────────────────────────────────────┘
```

### Key Components

| Module | Description |
|--------|-------------|
| `src/main.py` | Pipeline controller — orchestrates audio → ASR → translation → UI |
| `src/audio_capture.py` | WASAPI loopback + PortAudio microphone capture |
| `src/asr_engine.py` | faster-whisper wrapper with VAD, preprocessing, hallucination filter |
| `src/translator.py` | DeepSeek + DeepL backends with translation cache |
| `src/subtitle_overlay.py` | Transparent frameless overlay with Win32 click-through |
| `src/companion/` | AI Desktop Companion package (see below) |
| `src/theme.py` | Centralized color palette, typography, and QSS stylesheets |
| `src/widgets.py` | Reusable widgets (CollapsiblePanel, NeonButton, StatusIndicator) |

### Companion Package

| File | Purpose |
|------|---------|
| `companion_window.py` | Frameless floating QMainWindow, drag/snap, Win32 layered transparency |
| `companion_widget.py` | Circular glass-morphism character with paintEvent animation layers |
| `state_machine.py` | 5-state FSM: IDLE → LISTENING → TRANSLATING → INTENSE → SLEEP |
| `radial_menu.py` | Semicircle popup menu with expansion animation |
| `device_panel.py` | Audio input/output device selection panel |
| `__init__.py` | Package exports |

---

## ⚙️ Config Reference

Full `config.json`:

```json
{
  "audio": { "capture_mode": "loopback", "sample_rate": 48000, "device_id": null },
  "asr": { "model_size": "base.en", "device": "cuda", "vad_filter": true },
  "translation": { "engine": "auto", "source_lang": "EN", "target_lang": "ZH" },
  "ui": { "max_lines": 5, "font_size": 28, "position": "bottom_center" },
  "companion": { "enabled": true, "mode": "replace", "size": 100 },
  "hotkeys": { "toggle_capture": "ctrl+shift+t", "clear_subtitles": "ctrl+shift+c" }
}
```

---

## 📄 License

MIT — see [LICENSE](LICENSE) for details.

---

## 🙏 Credits

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — GPU-accelerated Whisper inference
- [DeepSeek](https://platform.deepseek.com) / [DeepL](https://deepl.com) — Translation APIs
- [PyQt6](https://www.riverbankcomputing.com/software/pyqt/) — Qt6 Python bindings
