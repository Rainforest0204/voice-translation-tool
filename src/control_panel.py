"""
Control panel window — cyberpunk-styled main app window with status,
controls, settings, and log. Uses collapsible panels and neon accents.
"""
import logging
import time
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QSpinBox,
    QSystemTrayIcon, QMenu, QApplication, QCheckBox,
    QTextEdit, QFrame,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon, QFont, QAction, QColor, QPalette, QPixmap, QPainter

from src.theme import (
    DEEP_BG, SURFACE, NEON_CYAN, NEON_GREEN, NEON_PINK, AMBER,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_DIM, TEXT_WHITE, BORDER_SUBTLE,
    FONT_HEADER, FONT_MONO, FONT_BODY, FONT_CJK,
    terminal_log_style, panel_header_style,
)
from src.widgets import (
    CollapsiblePanel, NeonButton, PinButton, StatusIndicator, HudStat,
)

logger = logging.getLogger(__name__)


class ControlPanel(QMainWindow):
    """Control panel window."""

    # Signals to communicate with pipeline controller
    toggle_capture = pyqtSignal()
    clear_subtitles = pyqtSignal()
    toggle_overlay = pyqtSignal()
    font_increase = pyqtSignal()
    font_decrease = pyqtSignal()
    config_changed = pyqtSignal(dict)
    mode_changed = pyqtSignal(str)  # "loopback" or "microphone"
    device_changed = pyqtSignal(str)  # unified device_id "wasapi:N" / "sounddevice:N"
    show_overlay_signal = pyqtSignal()  # re-open closed overlay

    def __init__(self, config: dict, capture_mode: str = "loopback"):
        super().__init__()
        self._config = config
        self._capture_mode = capture_mode
        self._start_time = time.monotonic()
        self._capturing = True  # Auto-start capturing
        self._pinned = True  # always-on-top by default

        mode_display = "扬声器→中文字幕" if capture_mode == "loopback" else "麦克风→英文字幕"
        self.setWindowTitle(mode_display)
        self.setMinimumSize(460, 540)
        self.resize(480, 620)

        # Apply always-on-top
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint
        )

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(6)
        layout.setContentsMargins(12, 10, 12, 10)

        # ---- Row 0: Status indicator + title + pin + runtime ----
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        self._status_indicator = StatusIndicator(state="running")
        top_row.addWidget(self._status_indicator)

        self._status_label = QLabel("ACTIVE")
        self._status_label.setStyleSheet(
            f"color: {NEON_GREEN}; font-family: {FONT_HEADER}; font-size: 13px; "
            f"font-weight: 700; border: none; background: transparent;"
        )
        top_row.addWidget(self._status_label)

        top_row.addStretch()

        self._clock_label = QLabel("[00:00]")
        self._clock_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-family: {FONT_MONO}; font-size: 11px; "
            f"border: none; background: transparent;"
        )
        top_row.addWidget(self._clock_label)

        self._pin_btn = PinButton()
        self._pin_btn.clicked.connect(self._on_pin_clicked)
        self._pin_btn.set_active(self._pinned)
        top_row.addWidget(self._pin_btn)

        layout.addLayout(top_row)

        # ---- Row 1: Stats (HUD cards) ----
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(6)

        self._captured_stat = HudStat("CAPTURED", "0")
        self._asr_stat = HudStat("ASR", "0")
        self._trans_stat = HudStat("TRANS", "0")
        self._latency_stat = HudStat("LATENCY", "0ms")

        for s in [self._captured_stat, self._asr_stat, self._trans_stat, self._latency_stat]:
            stats_layout.addWidget(s)

        layout.addLayout(stats_layout)

        # ---- Panel 1: Translation ----
        self._trans_panel = CollapsiblePanel("TRANSLATION")
        trans_layout = self._trans_panel.content_layout()

        self._en_label = QLabel("Waiting for speech input...")
        self._en_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-family: {FONT_MONO}; font-size: 11px; "
            f"font-style: italic; padding: 4px; border: none; background: transparent;"
        )
        self._en_label.setWordWrap(True)
        trans_layout.addWidget(self._en_label)

        self._zh_label = QLabel("")
        self._zh_label.setStyleSheet(
            f"color: {NEON_GREEN}; font-family: {FONT_CJK}; font-size: 18px; "
            f"font-weight: 700; padding: 4px; border: none; background: transparent;"
        )
        self._zh_label.setWordWrap(True)
        trans_layout.addWidget(self._zh_label)

        layout.addWidget(self._trans_panel)

        # ---- Row 2: Control buttons ----
        ctrl_layout = QHBoxLayout()
        ctrl_layout.setSpacing(8)

        self._start_btn = NeonButton("START", neon_color=NEON_GREEN)
        self._start_btn.clicked.connect(self._on_start_clicked)
        self._start_btn.setEnabled(False)  # Disabled: already running
        ctrl_layout.addWidget(self._start_btn)

        self._pause_btn = NeonButton("PAUSE", neon_color=AMBER)
        self._pause_btn.setEnabled(True)  # Enabled: can pause
        self._pause_btn.clicked.connect(self._on_pause_clicked)
        ctrl_layout.addWidget(self._pause_btn)

        self._clear_btn = NeonButton("CLEAR", neon_color=NEON_PINK)
        self._clear_btn.clicked.connect(lambda: self.clear_subtitles.emit())
        ctrl_layout.addWidget(self._clear_btn)

        layout.addLayout(ctrl_layout)

        # ---- Panel 2: Settings ----
        self._settings_panel = CollapsiblePanel("SETTINGS")
        settings_layout = self._settings_panel.content_layout()

        # Capture mode row
        row0 = QHBoxLayout()
        row0_label = QLabel("CAPTURE:")
        row0_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-family: {FONT_MONO}; font-size: 10px; "
            f"font-weight: 600; border: none; background: transparent;"
        )
        row0.addWidget(row0_label)
        self._mode_combo = QComboBox()
        self._mode_combo.addItems([
            "loopback (扬声器 EN→ZH)",
            "microphone (麦克风 ZH→EN)",
        ])
        # Set current index based on capture_mode
        self._mode_combo.setCurrentIndex(0 if capture_mode == "loopback" else 1)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_combo_changed)
        row0.addWidget(self._mode_combo)
        settings_layout.addLayout(row0)

        # Device selection row
        row_dev = QHBoxLayout()
        row_dev_label = QLabel("DEVICE:")
        row_dev_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-family: {FONT_MONO}; font-size: 10px; "
            f"font-weight: 600; border: none; background: transparent;"
        )
        row_dev.addWidget(row_dev_label)
        self._device_combo = QComboBox()
        self._device_combo.setMinimumWidth(200)
        self._device_combo.currentIndexChanged.connect(self._on_device_changed)
        row_dev.addWidget(self._device_combo)

        self._refresh_devices_btn = QPushButton("R")
        self._refresh_devices_btn.setFixedWidth(28)
        self._refresh_devices_btn.setToolTip("Refresh device list")
        self._refresh_devices_btn.setStyleSheet(
            f"QPushButton {{ background-color: {SURFACE}; color: {NEON_CYAN}; "
            f"border: 1px solid {BORDER_SUBTLE}; border-radius: 2px; "
            f"font-family: {FONT_MONO}; font-size: 10px; font-weight: 700; padding: 4px; }}"
            f"QPushButton:hover {{ border-color: {NEON_CYAN}; }}"
        )
        self._refresh_devices_btn.clicked.connect(self._refresh_device_list)
        row_dev.addWidget(self._refresh_devices_btn)
        settings_layout.addLayout(row_dev)

        self._device_data: list = []  # stores device dicts from list_devices_for_mode

        # ASR model row
        row1 = QHBoxLayout()
        row1_label = QLabel("ASR MODEL:")
        row1_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-family: {FONT_MONO}; font-size: 10px; "
            f"font-weight: 600; border: none; background: transparent;"
        )
        row1.addWidget(row1_label)
        self._model_combo = QComboBox()
        self._model_combo.addItems(["tiny.en  (fast)", "base.en  (balanced)", "small.en (accurate)"])
        self._model_combo.setCurrentIndex(0)
        self._model_combo.currentIndexChanged.connect(self._on_setting_changed)
        row1.addWidget(self._model_combo)
        settings_layout.addLayout(row1)

        # Font size row
        row2 = QHBoxLayout()
        row2_label = QLabel("FONT SIZE:")
        row2_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-family: {FONT_MONO}; font-size: 10px; "
            f"font-weight: 600; border: none; background: transparent;"
        )
        row2.addWidget(row2_label)
        self._font_spin = QSpinBox()
        self._font_spin.setRange(12, 72)
        self._font_spin.setValue(config.get("ui", {}).get("font_size", 24))
        self._font_spin.valueChanged.connect(self._on_setting_changed)
        row2.addWidget(self._font_spin)
        row2.addStretch()
        settings_layout.addLayout(row2)

        # Max lines row
        row3 = QHBoxLayout()
        row3_label = QLabel("MAX LINES:")
        row3_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-family: {FONT_MONO}; font-size: 10px; "
            f"font-weight: 600; border: none; background: transparent;"
        )
        row3.addWidget(row3_label)
        self._lines_spin = QSpinBox()
        self._lines_spin.setRange(1, 10)
        self._lines_spin.setValue(config.get("ui", {}).get("max_lines", 3))
        self._lines_spin.valueChanged.connect(self._on_setting_changed)
        row3.addWidget(self._lines_spin)
        row3.addStretch()
        settings_layout.addLayout(row3)

        # Position row
        row4 = QHBoxLayout()
        row4_label = QLabel("POSITION:")
        row4_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-family: {FONT_MONO}; font-size: 10px; "
            f"font-weight: 600; border: none; background: transparent;"
        )
        row4.addWidget(row4_label)
        self._pos_combo = QComboBox()
        self._pos_combo.addItems(["bottom_center", "top_center", "center"])
        self._pos_combo.setCurrentIndex(0)
        self._pos_combo.currentIndexChanged.connect(self._on_setting_changed)
        row4.addWidget(self._pos_combo)
        settings_layout.addLayout(row4)

        # Overlay control row
        row5 = QHBoxLayout()
        self._overlay_check = QCheckBox("字幕显示")
        self._overlay_check.setChecked(True)
        self._overlay_check.toggled.connect(lambda checked: (
            self.toggle_overlay.emit(),
            self._show_overlay_btn.setVisible(not checked)
        ))
        row5.addWidget(self._overlay_check)

        self._show_overlay_btn = QPushButton("重新打开字幕")
        self._show_overlay_btn.setStyleSheet(
            f"QPushButton {{ background-color: {SURFACE}; color: {NEON_CYAN}; "
            f"border: 1px solid {BORDER_SUBTLE}; border-radius: 2px; "
            f"font-size: 9px; padding: 4px 8px; }}"
            f"QPushButton:hover {{ border-color: {NEON_CYAN}; }}"
        )
        self._show_overlay_btn.clicked.connect(lambda: self.show_overlay_signal.emit())
        self._show_overlay_btn.setVisible(False)  # hidden until overlay is closed
        row5.addWidget(self._show_overlay_btn)
        row5.addStretch()
        settings_layout.addLayout(row5)

        layout.addWidget(self._settings_panel)

        # ---- Panel 3: Log ----
        self._log_panel = CollapsiblePanel("LOG", collapsed=True)
        log_layout = self._log_panel.content_layout()

        self._log_area = QTextEdit()
        self._log_area.setReadOnly(True)
        self._log_area.setMaximumHeight(80)
        self._log_area.setStyleSheet(terminal_log_style())
        log_layout.addWidget(self._log_area)

        layout.addWidget(self._log_panel)

        # ---- System tray ----
        self._setup_tray()

        # Populate device selector
        self.populate_devices(self._capture_mode)

        # Status update timer
        self._status_timer = QTimer()
        self._status_timer.timeout.connect(self._update_runtime)
        self._status_timer.start(1000)

    def _setup_tray(self):
        """Setup system tray icon and menu."""
        self._tray = QSystemTrayIcon(self)
        self._tray.setToolTip("语音翻译")

        # Draw a neon-green icon
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setBrush(QColor(NEON_GREEN))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(4, 4, 24, 24, 6, 6)
        painter.end()
        self._tray.setIcon(QIcon(pixmap))

        tray_menu = QMenu()
        show_action = QAction("Show Window", self)
        show_action.triggered.connect(self.show)
        tray_menu.addAction(show_action)

        toggle_action = QAction("Pause / Resume", self)
        toggle_action.triggered.connect(lambda: self.toggle_capture.emit())
        tray_menu.addAction(toggle_action)

        tray_menu.addSeparator()

        quit_action = QAction("Exit", self)
        quit_action.triggered.connect(self._quit_app)
        tray_menu.addAction(quit_action)

        self._tray.setContextMenu(tray_menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show()
            self.activateWindow()

    def closeEvent(self, event):
        """Minimize to tray instead of closing."""
        event.ignore()
        self.hide()
        self._tray.showMessage(
            "语音翻译",
            "Minimized to system tray",
            QSystemTrayIcon.MessageIcon.Information,
            2000,
        )

    def _quit_app(self):
        self._tray.hide()
        QApplication.quit()

    # ----- Button handlers -----

    def _on_start_clicked(self):
        if self._capturing:
            return  # Already running, do nothing
        self._capturing = True
        self._start_btn.setEnabled(False)
        self._pause_btn.setEnabled(True)
        self._status_indicator.set_state("running")
        self._status_label.setText("ACTIVE")
        self._status_label.setStyleSheet(
            f"color: {NEON_GREEN}; font-family: {FONT_HEADER}; font-size: 13px; "
            f"font-weight: 700; border: none; background: transparent;"
        )
        self.toggle_capture.emit()

    def _on_pause_clicked(self):
        if not self._capturing:
            return  # Already paused, do nothing
        self._capturing = False
        self._start_btn.setEnabled(True)
        self._pause_btn.setEnabled(False)
        self._status_indicator.set_state("paused")
        self._status_label.setText("PAUSED")
        self._status_label.setStyleSheet(
            f"color: {AMBER}; font-family: {FONT_HEADER}; font-size: 13px; "
            f"font-weight: 700; border: none; background: transparent;"
        )
        self.toggle_capture.emit()

    def _on_pin_clicked(self):
        """Toggle always-on-top."""
        self._pinned = not self._pinned
        self._pin_btn.set_active(self._pinned)
        if self._pinned:
            self.setWindowFlags(
                self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint
            )
        else:
            self.setWindowFlags(
                self.windowFlags() & ~Qt.WindowType.WindowStaysOnTopHint
            )
        self.show()  # need to re-show after changing window flags

    def _on_mode_combo_changed(self, index: int):
        """Handle capture mode change — hot restart with new mode and device list."""
        mode = "loopback" if index == 0 else "microphone"
        if mode != self._capture_mode:
            self._capture_mode = mode
            self.populate_devices(mode)  # refresh device list for new mode
            self.mode_changed.emit(mode)
            self.config_changed.emit({"audio.capture_mode": mode})
            mode_display = "扬声器→中文字幕" if mode == "loopback" else "麦克风→英文字幕"
            self.add_log(f"采集模式已切换: {mode_display}")

    def populate_devices(self, mode: str) -> None:
        """Populate the device combo with devices for the given capture mode."""
        from src.audio_capture import list_devices_for_mode
        self._device_combo.blockSignals(True)
        self._device_combo.clear()
        self._device_data = []
        try:
            devices = list_devices_for_mode(mode)
            for dev in devices:
                label = dev["name"]
                if dev["is_default"]:
                    label += "  [DEFAULT]"
                label += f"  ({dev['host_api']})"
                self._device_combo.addItem(label)
                self._device_data.append(dev)
            if not devices:
                self._device_combo.addItem("(no devices found)")
                self._device_data.append(None)
        except Exception as e:
            logger.warning(f"Failed to list devices: {e}")
            self._device_combo.addItem("(enumeration failed)")
            self._device_data.append(None)
        self._device_combo.blockSignals(False)

    def _on_device_changed(self, index: int) -> None:
        """Handle device selection change."""
        if index < 0 or index >= len(self._device_data):
            return
        dev = self._device_data[index]
        if dev is None:
            return
        self.device_changed.emit(dev["device_id"])
        self.add_log(f"音频设备已选择: {dev['name']}")

    def _refresh_device_list(self) -> None:
        """Refresh the device list (e.g., after plugging in a new device)."""
        self.populate_devices(self._capture_mode)
        self.add_log("设备列表已刷新")

    def on_overlay_closed(self) -> None:
        """Called when overlay is closed — show re-open button."""
        self._overlay_check.setChecked(False)
        self._show_overlay_btn.setVisible(True)
        self.add_log("字幕已关闭，点击「重新打开字幕」恢复")

    def _on_setting_changed(self):
        """Emit config changes."""
        pos_map = {0: "bottom_center", 1: "top_center", 2: "center"}
        model_map = {0: "tiny.en", 1: "base.en", 2: "small.en"}
        changes = {
            "asr.model_size": model_map[self._model_combo.currentIndex()],
            "ui.font_size": self._font_spin.value(),
            "ui.max_lines": self._lines_spin.value(),
            "ui.position": pos_map[self._pos_combo.currentIndex()],
        }
        self.config_changed.emit(changes)

    # ----- Public methods (called by PipelineController) -----

    def update_translation(self, source_text: str, translated_text: str):
        """Update the last translation display from main thread."""
        self._en_label.setText(source_text)
        self._zh_label.setText(translated_text)

    def update_stats(self, captured: int, transcribed: int, translated: int, latency_ms: float):
        """Update statistics display."""
        self._captured_stat.set_value(str(captured))
        self._asr_stat.set_value(str(transcribed))
        self._trans_stat.set_value(str(translated))
        self._latency_stat.set_value(f"{latency_ms:.0f}ms")

    def add_log(self, message: str):
        """Add a line to the log area."""
        timestamp = time.strftime("%H:%M:%S")
        self._log_area.append(f"[{timestamp}] {message}")
        # Keep only last 50 lines
        if self._log_area.document().blockCount() > 50:
            cursor = self._log_area.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            cursor.select(cursor.SelectionType.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()

    def _update_runtime(self):
        """Update runtime display."""
        elapsed = time.monotonic() - self._start_time
        mins = int(elapsed // 60)
        secs = int(elapsed % 60)
        mode_label = "EN→ZH" if self._capture_mode == "loopback" else "ZH→EN"
        if self._capturing:
            self.setWindowTitle(f"{mode_label} - [{mins:02d}:{secs:02d}]")
            self._clock_label.setText(f"[{mins:02d}:{secs:02d}]")
        else:
            self._clock_label.setText(f"[{mins:02d}:{secs:02d}]")
