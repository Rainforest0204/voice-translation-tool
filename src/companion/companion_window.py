"""
Companion window — frameless floating QMainWindow.

Mouse interaction: companion_widget and all child widgets are set
WA_TransparentForMouseEvents so ALL clicks pass through to the window's
own mousePressEvent/mouseReleaseEvent/mouseMoveEvent handlers.
"""
import logging
import ctypes

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QLabel, QApplication, QMenu,
)
from PyQt6.QtCore import (
    Qt, QTimer, QPoint, pyqtSignal, QRect, QEvent,
)
from PyQt6.QtGui import (
    QMouseEvent, QAction,
)

from src.companion.companion_widget import CompanionWidget, STATE_COLORS
from src.companion.radial_menu import RadialMenu
from src.companion.device_panel import DevicePanel
from src.theme import (
    COMPANION_BORDER, COMPANION_CARD,
    COMPANION_TEXT_PRIMARY, COMPANION_TEXT_SECONDARY,
    COMPANION_ACCENT, COMPANION_SUCCESS, COMPANION_WARNING, COMPANION_DANGER,
    FONT_MONO, FONT_CJK,
)

logger = logging.getLogger(__name__)

# Win32
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
HWND_TOPMOST = -1
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040


def _force_topmost(hwnd: int) -> None:
    ctypes.windll.user32.SetWindowPos(
        hwnd, HWND_TOPMOST, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
    )


class CompanionWindow(QMainWindow):
    """Frameless floating companion. All interactions via window-level mouse handlers."""

    toggle_capture = pyqtSignal()
    clear_subtitles = pyqtSignal()
    toggle_overlay = pyqtSignal()
    mode_changed = pyqtSignal(str)
    device_changed = pyqtSignal(str)
    config_changed = pyqtSignal(dict)
    show_overlay_signal = pyqtSignal()
    quit_requested = pyqtSignal()

    def __init__(self, config: dict, capture_mode: str = "loopback"):
        super().__init__()
        self._config = config
        self._capture_mode = capture_mode
        self._capturing = True
        self._overlay_visible = True

        companion_cfg = config.get("companion", {})
        self._size_px = companion_cfg.get("size", 100)
        self._edge_snap = companion_cfg.get("edge_snap", {})

        self._setup_window()
        self._setup_ui()
        self._setup_menus()
        self._setup_timers()
        self._position_window()

        # Drag state
        self._drag_active = False
        self._drag_origin = QPoint()

    # ================================================================
    # Window Setup
    # ================================================================

    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMinimumSize(120, 140)
        self.resize(140, 160)
        self.setMouseTracking(True)

        central = QWidget(self)
        central.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        central.setStyleSheet("background: transparent;")
        # Make central widget mouse-transparent so events reach the window
        central.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setCentralWidget(central)

        self._root_layout = QVBoxLayout(central)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(0)
        self._root_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def _setup_ui(self):
        container = QWidget(self)
        container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        container.setStyleSheet("background: transparent;")
        # Make container mouse-transparent
        container.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._companion_widget = CompanionWidget(size_px=self._size_px)
        # Make companion widget mouse-transparent — window handles all clicks
        self._companion_widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self._companion_widget, alignment=Qt.AlignmentFlag.AlignCenter)

        self._mode_badge = QLabel(self._mode_badge_text())
        self._mode_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._mode_badge.setFixedHeight(18)
        # Also transparent
        self._mode_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._mode_badge.setStyleSheet(f"""
            QLabel {{
                background: {COMPANION_CARD};
                border: 1px solid {COMPANION_BORDER};
                border-radius: 5px;
                padding: 2px 8px;
                font-family: {FONT_MONO};
                font-size: 8px; font-weight: 700;
                color: {COMPANION_TEXT_SECONDARY};
            }}
        """)
        layout.addWidget(self._mode_badge, alignment=Qt.AlignmentFlag.AlignCenter)

        self._root_layout.addWidget(container)

    def _setup_menus(self):
        self._radial_menu = RadialMenu(parent=None)
        self._radial_menu.item_activated.connect(self._on_radial_action)

        self._device_panel = DevicePanel(parent=None)
        self._device_panel.devices_changed.connect(self._on_device_config_changed)

        self._context_menu = QMenu(self)
        self._context_menu.setStyleSheet(f"""
            QMenu {{
                background: rgba(255,255,255,0.94);
                border: 1px solid {COMPANION_BORDER};
                border-radius: 8px; padding: 4px 0;
                font-family: {FONT_CJK}; font-size: 11px;
                color: {COMPANION_TEXT_PRIMARY};
            }}
            QMenu::item {{ padding: 6px 18px; margin: 0 4px; border-radius: 4px; }}
            QMenu::item:selected {{ background: rgba(79,110,247,0.10); color: {COMPANION_ACCENT}; }}
            QMenu::separator {{ height: 1px; background: {COMPANION_BORDER}; margin: 4px 6px; }}
        """)
        self._ctx_toggle = self._context_menu.addAction("⏯  暂停 / 恢复")
        self._ctx_clear = self._context_menu.addAction("✖  清除字幕")
        self._ctx_devices = self._context_menu.addAction("🎧  音频设备…")
        self._ctx_mode = self._context_menu.addAction("⇄  切换采集模式")
        self._context_menu.addSeparator()
        self._ctx_settings = self._context_menu.addAction("⚙  设置…")
        self._ctx_quit = self._context_menu.addAction("⏻  退出")
        self._ctx_toggle.triggered.connect(lambda: self.toggle_capture.emit())
        self._ctx_clear.triggered.connect(lambda: self.clear_subtitles.emit())
        self._ctx_devices.triggered.connect(self._show_device_panel)
        self._ctx_mode.triggered.connect(self._switch_mode)
        self._ctx_settings.triggered.connect(lambda: self.config_changed.emit({}))
        self._ctx_quit.triggered.connect(self.quit_requested.emit)

    def _setup_timers(self):
        self._topmost_timer = QTimer(self)
        self._topmost_timer.timeout.connect(self._refresh_topmost)
        self._topmost_timer.start(5000)

    def _position_window(self):
        screen = QApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        companion_cfg = self._config.get("companion", {})
        pos = companion_cfg.get("initial_position", "center")
        cx = companion_cfg.get("custom_x")
        cy = companion_cfg.get("custom_y")
        w, h = self.width(), self.height()
        if cx is not None and cy is not None:
            x, y = cx, cy
        elif pos == "center":
            x, y = geo.center().x() - w // 2, geo.center().y() - h // 2
        elif pos == "center_right":
            x, y = geo.right() - w - 40, geo.center().y() - h // 2
        elif pos == "top_right":
            x, y = geo.right() - w - 40, 60
        else:
            x, y = geo.right() - w - 40, geo.bottom() - h - 80
        self.move(x, y)

    # ================================================================
    # Window-level Mouse Handlers
    # ================================================================

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = False
            self._drag_origin = event.globalPosition().toPoint()
            self._window_origin = self.pos()
        elif event.button() == Qt.MouseButton.RightButton:
            from PyQt6.QtGui import QCursor
            self._context_menu.popup(QCursor.pos())

    def mouseMoveEvent(self, event: QMouseEvent):
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        delta = event.globalPosition().toPoint() - self._drag_origin
        dist = (delta.x()**2 + delta.y()**2) ** 0.5
        if dist > 3:
            self._drag_active = True
        if self._drag_active:
            new_x = self._window_origin.x() + delta.x()
            new_y = self._window_origin.y() + delta.y()
            self.move(new_x, new_y)
            self._check_edge_snap_hint(new_x, new_y)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        delta = event.globalPosition().toPoint() - self._drag_origin
        dist = (delta.x()**2 + delta.y()**2) ** 0.5

        if self._drag_active:
            self._drag_active = False
            self._handle_edge_snap()
            self.setWindowOpacity(1.0)
        elif dist < 3:
            # Click (not drag) — show radial menu
            self._show_radial_menu()

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._overlay_visible = not self._overlay_visible
            self.toggle_overlay.emit()

    # ================================================================
    # Menus
    # ================================================================

    def _show_radial_menu(self):
        center = self._companion_widget.mapToGlobal(
            QPoint(self._companion_widget.width() // 2,
                   self._companion_widget.height() // 2))
        self._radial_menu.show_at(center)

    def _on_radial_action(self, action_id: str):
        if action_id == "toggle_capture":
            self.toggle_capture.emit()
        elif action_id == "clear":
            self.clear_subtitles.emit()
        elif action_id == "devices":
            self._show_device_panel()
        elif action_id == "mode_switch":
            self._switch_mode()
        elif action_id == "settings":
            self.config_changed.emit({})

    def _show_device_panel(self):
        pos = self.mapToGlobal(QPoint(self.width() // 2 - 125, self.height() + 4))
        try:
            from src.audio_capture import list_all_devices
            all_devs = list_all_devices()
            loopback = [d for d in all_devs if d.get("is_loopback")]
            mic = [d for d in all_devs if d.get("is_input") and not d.get("is_loopback")]
            self._device_panel.populate_devices(loopback or all_devs, mic or all_devs)
        except Exception:
            self._device_panel.populate_devices([], [])
        self._device_panel.show_at(pos)

    def _on_device_config_changed(self, changes: dict):
        if changes.get("output"):
            self.device_changed.emit(changes["output"])
        if changes.get("input"):
            self.device_changed.emit(changes["input"])

    def _switch_mode(self):
        new_mode = "microphone" if self._capture_mode == "loopback" else "loopback"
        self._capture_mode = new_mode
        self._mode_badge.setText(self._mode_badge_text())
        self.mode_changed.emit(new_mode)

    # ================================================================
    # Edge Snap
    # ================================================================

    def _check_edge_snap_hint(self, x, y):
        threshold = self._edge_snap.get("snap_threshold_px", 50)
        screen = QApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        if y < threshold or y > geo.bottom() - threshold - self.height():
            self.setWindowOpacity(0.55)
        else:
            self.setWindowOpacity(1.0)

    def _handle_edge_snap(self):
        self.setWindowOpacity(1.0)
        threshold = self._edge_snap.get("snap_threshold_px", 50)
        screen = QApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        y = self.y()
        if self._edge_snap.get("enabled", True):
            if y < threshold and self._edge_snap.get("top_action") == "hide_subtitles":
                self._overlay_visible = False
                self.toggle_overlay.emit()
            elif (y > geo.bottom() - threshold - self.height()
                  and self._edge_snap.get("bottom_action") == "show_subtitles"):
                self._overlay_visible = True
                self.show_overlay_signal.emit()

    # ================================================================
    # Win32
    # ================================================================

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_win32_style()

    def _apply_win32_style(self):
        try:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                                  ex | WS_EX_LAYERED | WS_EX_TOOLWINDOW)
            _force_topmost(hwnd)
        except Exception as e:
            logger.debug(f"Win32 style: {e}")

    def _refresh_topmost(self):
        if self.isVisible():
            try:
                _force_topmost(int(self.winId()))
            except Exception:
                pass

    # ================================================================
    # Public API
    # ================================================================

    def set_state(self, state: str):
        self._companion_widget.set_state(state)

    def update_translation(self, source_text: str, translated_text: str):
        pass

    def update_stats(self, captured: int, transcribed: int, translated: int,
                     latency_ms: float):
        pass

    def add_log(self, message: str):
        pass

    def set_mode(self, mode: str):
        self._capture_mode = mode
        self._mode_badge.setText(self._mode_badge_text())

    def set_capturing(self, capturing: bool):
        self._capturing = capturing

    def on_overlay_closed(self):
        self._overlay_visible = False

    def _mode_badge_text(self) -> str:
        return "🔊 EN→ZH" if self._capture_mode == "loopback" else "🎤 ZH→EN"
