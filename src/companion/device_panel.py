"""
Device panel — a floating popup for selecting audio input/output devices.

Shows two combo boxes (output for loopback, input for microphone)
and a refresh button. Emits devices_changed when selection changes.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont

from src.theme import (
    COMPANION_PANEL, COMPANION_BORDER, COMPANION_ACCENT,
    COMPANION_TEXT_PRIMARY, COMPANION_TEXT_SECONDARY, COMPANION_TEXT_TERTIARY,
    COMPANION_DANGER,
    FONT_MONO,
)
from src.audio_capture import list_all_devices


class DevicePanel(QWidget):
    """Floating device selection panel.

    Signals:
        devices_changed(dict): emitted with {"output": device_id, "input": device_id}
        device_refresh_requested: emitted when refresh button clicked
    """

    devices_changed = pyqtSignal(dict)
    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self.setFixedSize(250, 190)

        self._build_ui()
        self._apply_style()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        # Title bar
        title_row = QHBoxLayout()
        title = QLabel("音频设备")
        title_font = QFont()
        title_font.setPointSize(11)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet(f"color: {COMPANION_TEXT_PRIMARY}; background: transparent;")
        title_row.addWidget(title)
        title_row.addStretch()

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(22, 22)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.close)
        close_btn.clicked.connect(self.closed.emit)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(0,0,0,0.04); border: none; border-radius: 11px;
                color: {COMPANION_TEXT_TERTIARY}; font-size: 11px; font-weight: bold;
            }}
            QPushButton:hover {{ background: rgba(255,59,48,0.12); color: {COMPANION_DANGER}; }}
        """)
        title_row.addWidget(close_btn)
        layout.addLayout(title_row)

        # Output device
        out_label = QLabel("🔊 输出设备（扬声器捕获源）")
        out_label.setStyleSheet(
            f"color: {COMPANION_TEXT_TERTIARY}; font-family: {FONT_MONO}; "
            f"font-size: 8px; font-weight: 700; background: transparent;"
        )
        layout.addWidget(out_label)

        self._output_combo = QComboBox()
        self._output_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._output_combo.currentTextChanged.connect(self._emit_changes)
        layout.addWidget(self._output_combo)

        out_hint = QLabel("回环模式：捕获系统播放的音频")
        out_hint.setStyleSheet(
            f"color: {COMPANION_TEXT_TERTIARY}; font-size: 8px; background: transparent;"
        )
        layout.addWidget(out_hint)

        # Input device
        in_label = QLabel("🎤 输入设备（麦克风）")
        in_label.setStyleSheet(
            f"color: {COMPANION_TEXT_TERTIARY}; font-family: {FONT_MONO}; "
            f"font-size: 8px; font-weight: 700; background: transparent;"
        )
        layout.addWidget(in_label)

        self._input_combo = QComboBox()
        self._input_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._input_combo.currentTextChanged.connect(self._emit_changes)
        layout.addWidget(self._input_combo)

        in_hint = QLabel("麦克风模式：捕获你的语音")
        in_hint.setStyleSheet(
            f"color: {COMPANION_TEXT_TERTIARY}; font-size: 8px; background: transparent;"
        )
        layout.addWidget(in_hint)

        # Refresh button
        refresh_btn = QPushButton("⟳ 刷新设备列表")
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.clicked.connect(self._refresh_devices)
        refresh_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(0,0,0,0.03); border: 1px solid {COMPANION_BORDER};
                border-radius: 5px; padding: 5px 12px;
                font-family: {FONT_MONO}; font-size: 9px; font-weight: 600;
                color: {COMPANION_TEXT_SECONDARY};
            }}
            QPushButton:hover {{
                background: rgba(79,110,247,0.08); border-color: {COMPANION_ACCENT};
                color: {COMPANION_ACCENT};
            }}
        """)
        layout.addWidget(refresh_btn)

    def _apply_style(self):
        self.setStyleSheet(f"""
            QWidget {{
                background: {COMPANION_PANEL};
                border: 1px solid {COMPANION_BORDER};
                border-radius: 12px;
            }}
            QComboBox {{
                background: rgba(0,0,0,0.03);
                color: {COMPANION_TEXT_PRIMARY};
                border: 1px solid {COMPANION_BORDER};
                border-radius: 5px;
                padding: 6px 10px;
                font-family: {FONT_MONO}; font-size: 10px;
            }}
            QComboBox:hover {{ border-color: {COMPANION_ACCENT}; }}
            QComboBox:focus {{ border-color: {COMPANION_ACCENT}; }}
            QComboBox::drop-down {{ border: none; width: 18px; }}
            QComboBox QAbstractItemView {{
                background: rgba(255,255,255,0.95);
                color: {COMPANION_TEXT_PRIMARY};
                selection-background-color: rgba(79,110,247,0.10);
                selection-color: {COMPANION_ACCENT};
                border: 1px solid {COMPANION_BORDER};
                border-radius: 5px;
                font-family: {FONT_MONO}; font-size: 10px;
            }}
        """)

    # ---- Public ----

    def show_at(self, pos) -> None:
        """Show the panel at the given global position."""
        self.move(pos)
        self.show()
        self.raise_()

    def populate_devices(self, loopback_devices: list[dict],
                         microphone_devices: list[dict]) -> None:
        """Fill combo boxes with device entries."""
        self._output_combo.blockSignals(True)
        self._input_combo.blockSignals(True)

        self._output_combo.clear()
        self._output_devices = []
        for dev in loopback_devices:
            label = dev.get("name", dev.get("device_id", "Unknown"))
            if dev.get("is_default"):
                label += "  [默认]"
            self._output_combo.addItem(label)
            self._output_devices.append(dev)

        self._input_combo.clear()
        self._input_devices = []
        for dev in microphone_devices:
            label = dev.get("name", dev.get("device_id", "Unknown"))
            if dev.get("is_default"):
                label += "  [默认]"
            self._input_combo.addItem(label)
            self._input_devices.append(dev)

        self._output_combo.blockSignals(False)
        self._input_combo.blockSignals(False)

    def get_selected_output(self) -> str:
        idx = self._output_combo.currentIndex()
        if 0 <= idx < len(self._output_devices):
            return self._output_devices[idx].get("device_id", "")
        return ""

    def get_selected_input(self) -> str:
        idx = self._input_combo.currentIndex()
        if 0 <= idx < len(self._input_devices):
            return self._input_devices[idx].get("device_id", "")
        return ""

    # ---- Internal ----

    def _emit_changes(self) -> None:
        self.devices_changed.emit({
            "output": self.get_selected_output(),
            "input": self.get_selected_input(),
        })

    def _refresh_devices(self) -> None:
        """Re-enumerate audio devices."""
        try:
            all_devs = list_all_devices()
            loopback = [d for d in all_devs if d.get("is_loopback")]
            microphone = [d for d in all_devs if d.get("is_input") and not d.get("is_loopback")]
            self.populate_devices(loopback or all_devs, microphone or all_devs)
        except Exception:
            pass  # Keep existing device list on failure
