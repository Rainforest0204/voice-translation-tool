"""
Subtitles overlay with draggable title bar, minimize, close, and resize.

Features:
  - Drag: grab the title bar to move
  - Minimize: minimize button hides to tray
  - Close: close button hides overlay (re-open from control panel)
  - Resize: drag any edge or corner to resize
  - Content area remains click-through (game receives input)
  - Title bar auto-hides after 3s, reappears on mouse hover

Uses PyQt6 with Win32 API for game click-through compatibility.
"""
import logging
import time
import ctypes
from ctypes import wintypes
from typing import Optional

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QApplication,
    QGraphicsOpacityEffect, QPushButton, QSizeGrip,
)
from PyQt6.QtCore import (
    Qt, QTimer, QPoint, pyqtSignal, QRect, QSize,
    QPropertyAnimation, QEasingCurve,
)
from PyQt6.QtGui import (
    QFont, QColor, QPainter, QPen, QFontDatabase,
    QMouseEvent, QEnterEvent,
)

from src.theme import (
    NEON_CYAN, NEON_GREEN, TEXT_WHITE, DEEP_BG,
    transparent_overlay_style,
)

logger = logging.getLogger(__name__)

# Win32 constants
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
HWND_TOPMOST = -1
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
HTTRANSPARENT = -1
HTBOTTOMRIGHT = 17
HTBOTTOM = 15
HTRIGHT = 11
HTTOP = 12
HTLEFT = 10
HTTOPLEFT = 13
HTTOPRIGHT = 14
HTBOTTOMLEFT = 16
HTCAPTION = 2

TITLE_BAR_HEIGHT = 28
RESIZE_MARGIN = 5


def _force_topmost(hwnd: int) -> None:
    ctypes.windll.user32.SetWindowPos(
        hwnd, HWND_TOPMOST, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
    )


class TitleBar(QWidget):
    """Custom title bar with drag handle, minimize, and close buttons."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dragging = False
        self._drag_start = None

        self.setFixedHeight(TITLE_BAR_HEIGHT)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 4, 0)
        layout.setSpacing(4)

        # Title label
        title = QLabel("")
        title.setStyleSheet(
            f"color: {NEON_CYAN}; font-size: 11px; font-weight: 700; "
            "background: transparent; border: none;"
        )
        layout.addWidget(title)

        layout.addStretch()

        # Minimize button
        self._min_btn = QPushButton("—")
        self._min_btn.setFixedSize(24, 20)
        self._min_btn.setToolTip("最小化到托盘")
        self._min_btn.setStyleSheet(self._btn_style())
        self._min_btn.clicked.connect(self._on_minimize)
        layout.addWidget(self._min_btn)

        # Close button
        self._close_btn = QPushButton("✕")
        self._close_btn.setFixedSize(24, 20)
        self._close_btn.setToolTip("关闭字幕（可从控制面板重新打开）")
        self._close_btn.setStyleSheet(self._btn_style(hover_color="#FF1744"))
        self._close_btn.clicked.connect(self._on_close)
        layout.addWidget(self._close_btn)

        self.setStyleSheet(
            f"TitleBar {{ background-color: {DEEP_BG}; border-top-left-radius: 4px; "
            f"border-top-right-radius: 4px; }}"
        )

        # Auto-hide timer
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._fade_out)

    def _btn_style(self, hover_color=NEON_CYAN):
        return (
            f"QPushButton {{ color: #888; background: transparent; border: none; "
            f"font-size: 12px; font-weight: bold; padding: 0; }}"
            f"QPushButton:hover {{ color: {hover_color}; background: #222; }}"
        )

    def _on_minimize(self):
        w = self.window()
        if hasattr(w, 'minimize_to_tray'):
            w.minimize_to_tray()

    def _on_close(self):
        w = self.window()
        if hasattr(w, 'close_overlay'):
            w.close_overlay()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_start = event.globalPosition().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging and self._drag_start is not None:
            delta = event.globalPosition().toPoint() - self._drag_start
            w = self.window()
            w.move(w.x() + delta.x(), w.y() + delta.y())
            self._drag_start = event.globalPosition().toPoint()
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._dragging = False
        self._drag_start = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        event.accept()

    def enterEvent(self, event: QEnterEvent):
        self._hide_timer.stop()
        self._fade_in()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hide_timer.start(3000)
        super().leaveEvent(event)

    def _fade_in(self):
        effect = self.graphicsEffect()
        if effect is None:
            effect = QGraphicsOpacityEffect(self)
            self.setGraphicsEffect(effect)
        effect.setOpacity(1.0)

    def _fade_out(self):
        effect = self.graphicsEffect()
        if effect is None:
            effect = QGraphicsOpacityEffect(self)
            self.setGraphicsEffect(effect)
        effect.setOpacity(0.15)


class SubtitleLine(QLabel):
    """Subtitle text with neon glow via QPainter."""

    def __init__(self, text="", font_size=28, font_color=TEXT_WHITE,
                 outline_color="#000000", outline_width=2,
                 neon_enabled=True, neon_color=NEON_CYAN):
        super().__init__()
        self._font_size = font_size
        self._font_color = font_color
        self._outline_color = outline_color
        self._outline_width = outline_width
        self._neon_enabled = neon_enabled
        self._neon_color = neon_color
        self._text = text

        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.setMinimumHeight(font_size + 30)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent; border: none;")
        self._choose_font()

    def _choose_font(self):
        available = QFontDatabase.families()
        candidates = [
            "Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑",
            "Noto Sans CJK SC", "SimHei", "黑体", "DengXian",
            "Arial Unicode MS", "Segoe UI", "Arial",
        ]
        chosen = next((f for f in candidates if f in available),
                      QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family())
        self._font = QFont(chosen, self._font_size)
        self._font.setWeight(QFont.Weight.Bold)
        self._font.setStyleHint(QFont.StyleHint.SansSerif)
        self._font.setStyleStrategy(
            QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality
        )
        self.setFont(self._font)

    def set_font_size(self, s):
        self._font_size = s
        self._choose_font()
        self.setMinimumHeight(s + 30)
        self.update()

    def set_text(self, t):
        if t != self._text:
            self._text = t
            self.update()

    def paintEvent(self, event):
        if not self._text:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setFont(self._font)
        r = self.rect()

        if self._neon_enabled:
            g = QColor(self._neon_color)
            for layer in range(3, 0, -1):
                pen = QPen(QColor(g.red(), g.green(), g.blue(), int(50 / layer)),
                           self._outline_width + layer * 3)
                p.setPen(pen)
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, self._text)

        p.setPen(QPen(QColor(self._outline_color), self._outline_width))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self._text)
        p.setPen(QPen(QColor(self._font_color)))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self._text)
        p.end()


class SubtitleOverlay(QMainWindow):
    """Transparent subtitle overlay with draggable title bar, resize, min/close.

    Title bar: drag to move, buttons to minimize/close, auto-hides.
    Content area: click-through for games.
    Edges: 5px resize margins.
    """

    # Signal to notify control panel when overlay is closed
    overlay_closed = pyqtSignal()

    def __init__(self, config: dict):
        super().__init__()
        self._config = config
        ui = config.get("ui", {})

        self._max_lines = ui.get("max_lines", 5)
        self._font_size = ui.get("font_size", 28)
        self._font_color = ui.get("font_color", "#FFFFFF")
        self._outline_color = ui.get("outline_color", "#000000")
        self._outline_width = ui.get("outline_width", 2)
        self._fade_sec = ui.get("fade_sec", 8.0)
        self._position = ui.get("position", "bottom_center")
        self._neon_enabled = ui.get("neon_glow", True)

        self._setup_window()
        self._setup_ui()

        # Topmost refresh
        self._topmost_timer = QTimer(self)
        self._topmost_timer.timeout.connect(self._refresh_topmost)
        self._topmost_timer.start(5000)

        # Fade timer
        self._fade_timer = QTimer(self)
        self._fade_timer.timeout.connect(self._check_fade)
        self._fade_timer.start(1000)

        self._line_timestamps: list[float] = []
        self._all_lines: list[str] = []

    def _setup_window(self):
        """Frameless, transparent, on-top. Resizable via nativeEvent."""
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMinimumSize(400, 100)
        self.setStyleSheet(transparent_overlay_style())

        # Central widget holds everything
        central = QWidget(self)
        central.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        central.setStyleSheet("background: transparent;")
        central.setMouseTracking(True)
        self.setCentralWidget(central)

        self._root_layout = QVBoxLayout(central)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(0)

    def _setup_ui(self):
        """Build: title bar + subtitle content area."""
        # ---- Title bar ----
        self._title_bar = TitleBar(self)
        self._root_layout.addWidget(self._title_bar)

        # ---- Subtitle content area ----
        self._content = QWidget(self)
        self._content.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._content.setStyleSheet("background: transparent;")
        # Content is click-through at Win32 level
        self._content.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        content_layout = QVBoxLayout(self._content)
        content_layout.setContentsMargins(20, 8, 20, 10)
        content_layout.setSpacing(6)
        content_layout.setAlignment(Qt.AlignmentFlag.AlignBottom)

        self._line_widgets: list[SubtitleLine] = []
        self._fade_anims: list[Optional[QPropertyAnimation]] = []

        for i in range(self._max_lines):
            line = SubtitleLine(
                font_size=self._font_size,
                font_color=self._font_color,
                outline_color=self._outline_color,
                outline_width=self._outline_width,
                neon_enabled=self._neon_enabled,
            )
            line.setVisible(False)
            effect = QGraphicsOpacityEffect(line)
            effect.setOpacity(1.0)
            line.setGraphicsEffect(effect)
            content_layout.addWidget(line)
            self._line_widgets.append(line)
            self._fade_anims.append(None)

        self._root_layout.addWidget(self._content)

        # ---- Resize grip (bottom-right corner) ----
        grip_layout = QHBoxLayout()
        grip_layout.setContentsMargins(0, 0, 0, 0)
        grip_layout.addStretch()
        size_grip = QSizeGrip(self)
        size_grip.setFixedSize(16, 16)
        size_grip.setStyleSheet("background: transparent;")
        grip_layout.addWidget(size_grip)

        grip_widget = QWidget(self)
        grip_widget.setLayout(grip_layout)
        grip_widget.setFixedHeight(16)
        grip_widget.setStyleSheet("background: transparent;")
        self._root_layout.addWidget(grip_widget)

        self._position_window()

    def _position_window(self):
        screen = QApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        ui = self._config.get("ui", {})
        w = ui.get("width", 1200)
        h = ui.get("height", 250)
        pos = self._position

        if pos == "bottom_center":
            x, y = geo.center().x() - w // 2, geo.bottom() - h - 60
        elif pos == "top_center":
            x, y = geo.center().x() - w // 2, 20
        elif pos == "center":
            x, y = geo.center().x() - w // 2, geo.center().y() - h // 2
        else:
            x = ui.get("custom_x", geo.center().x() - w // 2)
            y = ui.get("custom_y", geo.bottom() - h - 60)

        self.setGeometry(x, y, w, h)

    # ---- Resize support via nativeEvent ----

    def nativeEvent(self, eventType, message):
        """Handle edge resizing for frameless window."""
        try:
            msg_ptr = ctypes.c_void_p(int(message))
        except (TypeError, ValueError):
            return False, 0

        MSG = ctypes.wintypes.MSG
        msg = ctypes.cast(msg_ptr, ctypes.POINTER(MSG)).contents

        if msg.message == 0x0084:  # WM_NCHITTEST
            # Get mouse position in window coordinates
            x = msg.lParam & 0xFFFF
            y = (msg.lParam >> 16) & 0xFFFF
            # Convert screen coords to window coords
            pt = QPoint(x, y)
            wp = self.mapFromGlobal(pt)

            margin = RESIZE_MARGIN
            w, h = self.width(), self.height()

            left = wp.x() < margin
            right = wp.x() > w - margin
            top = wp.y() < margin
            bottom = wp.y() > h - margin

            # Don't interfere with title bar area
            if wp.y() < TITLE_BAR_HEIGHT and not (left or right):
                return False, 0  # Let title bar handle it

            if top and left:
                return True, HTTOPLEFT
            if top and right:
                return True, HTTOPRIGHT
            if bottom and left:
                return True, HTBOTTOMLEFT
            if bottom and right:
                return True, HTBOTTOMRIGHT
            if top:
                return True, HTTOP
            if bottom:
                return True, HTBOTTOM
            if left:
                return True, HTLEFT
            if right:
                return True, HTRIGHT

        return False, 0

    # ---- Subtitle display ----

    def add_line(self, text: str):
        if not text or not text.strip():
            return
        text = text.strip()
        self._all_lines.append(text)
        if len(self._all_lines) > 100:
            self._all_lines = self._all_lines[-50:]
        self._line_timestamps.append(time.monotonic())

        visible = self._all_lines[-self._max_lines:]
        for i, w in enumerate(self._line_widgets):
            if i < len(visible):
                age = (len(visible) - 1 - i) / max(1, len(visible))
                self._animate_opacity(i, 1.0 - age * 0.5)
                w.set_text(visible[i])
                w.setVisible(True)
            else:
                w.setVisible(False)

    def _animate_opacity(self, idx, target):
        w = self._line_widgets[idx]
        eff = w.graphicsEffect()
        if eff is None:
            return
        cur = eff.opacity()
        if abs(cur - target) < 0.02:
            return
        existing = self._fade_anims[idx]
        if existing and existing.state() == QPropertyAnimation.State.Running:
            existing.stop()
        a = QPropertyAnimation(eff, b"opacity")
        a.setDuration(400)
        a.setStartValue(cur)
        a.setEndValue(target)
        a.setEasingCurve(QEasingCurve.Type.InOutCubic)
        a.start()
        self._fade_anims[idx] = a

    def clear(self):
        self._all_lines.clear()
        self._line_timestamps.clear()
        for w in self._line_widgets:
            w.setVisible(False)

    def set_font_size(self, s):
        self._font_size = max(12, min(72, s))
        for w in self._line_widgets:
            w.set_font_size(self._font_size)

    def increase_font(self):
        self.set_font_size(self._font_size + 2)

    def decrease_font(self):
        self.set_font_size(self._font_size - 2)

    # ---- Minimize / Close / Show ----

    def minimize_to_tray(self):
        """Hide to system tray."""
        self.hide()
        logger.info("Overlay minimized to tray")

    def close_overlay(self):
        """Close overlay (can be re-opened)."""
        self.hide()
        self.overlay_closed.emit()
        logger.info("Overlay closed")

    def show_overlay(self):
        """Re-open overlay."""
        self.show()
        self._apply_win32_style()
        logger.info("Overlay shown")

    def toggle_visible(self):
        if self.isVisible():
            self.hide()
        else:
            self.show_overlay()

    # ---- Win32 integration ----

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

    def _check_fade(self):
        if not self._line_timestamps:
            return
        now = time.monotonic()
        while self._line_timestamps and now - self._line_timestamps[0] > self._fade_sec:
            self._line_timestamps.pop(0)
            if self._all_lines:
                self._all_lines.pop(0)
        if self._all_lines:
            v = self._all_lines[-self._max_lines:]
            for i, w in enumerate(self._line_widgets):
                if i < len(v):
                    w.set_text(v[i])
                    w.setVisible(True)
                else:
                    w.setVisible(False)
        else:
            for w in self._line_widgets:
                w.setVisible(False)
