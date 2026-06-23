"""
Radial (pie) menu — a circular popup menu that fans out items
around the companion when clicked.

Items are arranged in a semicircle arc and rendered with paintEvent.
"""
import math
from dataclasses import dataclass
from typing import Optional

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import (
    Qt, QPoint, QTimer, QPropertyAnimation, QEasingCurve, pyqtProperty, pyqtSignal,
)
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QPainterPath, QMouseEvent,
)

from src.theme import (
    COMPANION_CARD, COMPANION_BORDER, COMPANION_ACCENT,
    COMPANION_TEXT_PRIMARY, COMPANION_TEXT_TERTIARY,
    COMPANION_SUCCESS, COMPANION_DANGER, COMPANION_WARNING,
    FONT_MONO,
)


@dataclass
class RadialItem:
    """A single item in the radial menu."""
    icon: str        # Unicode character
    label: str       # Short label
    action_id: str   # Emitted when clicked
    accent_color: str = COMPANION_ACCENT


DEFAULT_ITEMS = [
    RadialItem("⏯", "暂停", "toggle_capture", COMPANION_SUCCESS),
    RadialItem("✖", "清除", "clear", COMPANION_DANGER),
    RadialItem("🎧", "设备", "devices", COMPANION_ACCENT),
    RadialItem("⇄", "切换", "mode_switch", COMPANION_WARNING),
    RadialItem("⚙", "设置", "settings", COMPANION_TEXT_TERTIARY),
]


class RadialMenu(QWidget):
    """Circular popup menu rendered with QPainter.

    Shows items arranged in a semicircle. Animates open/close via a
    custom `expansion` Qt property (0.0 → 1.0).
    """

    item_activated = pyqtSignal(str)  # action_id
    closed = pyqtSignal()

    def __init__(self, items=None, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._items = items or DEFAULT_ITEMS
        self._hovered: Optional[int] = None
        self._expansion = 0.0
        self._diameter = 210
        self._item_r = 22  # item button radius

        self.setFixedSize(self._diameter, self._diameter)

        # Expansion animation
        self._anim = QPropertyAnimation(self, b"expansion")
        self._anim.setDuration(280)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.setMouseTracking(True)

    # ---- Qt Property ----

    def get_expansion(self) -> float: return self._expansion
    def set_expansion(self, v: float): self._expansion = v; self.update()
    expansion = pyqtProperty(float, get_expansion, set_expansion)

    # ---- Public ----

    def show_at(self, center_global: QPoint) -> None:
        """Position the menu centered on the given global point, then show."""
        x = center_global.x() - self._diameter // 2
        y = center_global.y() - self._diameter // 2
        self.move(x, y)
        self._expansion = 0.0
        self._hovered = None
        self.show()
        self._anim.start()

        # Auto-close timer
        self._close_timer = QTimer(self)
        self._close_timer.setSingleShot(True)
        self._close_timer.timeout.connect(self.close_menu)
        self._close_timer.start(5000)  # auto-close after 5s

    def close_menu(self) -> None:
        self.hide()
        self.closed.emit()

    # ---- Mouse ----

    def mouseMoveEvent(self, event: QMouseEvent):
        self._hovered = self._hit_test(event.position().toPoint())
        self.update()

    def mousePressEvent(self, event: QMouseEvent):
        idx = self._hit_test(event.position().toPoint())
        if idx is not None and 0 <= idx < len(self._items):
            self.item_activated.emit(self._items[idx].action_id)
            self.close_menu()

    def leaveEvent(self, event):
        self._hovered = None
        self.update()

    def _hit_test(self, pt: QPoint) -> Optional[int]:
        """Return index of item under point, or None."""
        positions = self._item_positions()
        for i, (ix, iy) in enumerate(positions):
            dx = pt.x() - ix
            dy = pt.y() - iy
            if dx * dx + dy * dy <= (self._item_r + 6) ** 2:
                return i
        return None

    # ---- Layout ----

    def _item_positions(self):
        """Calculate semicircle positions for items. Returns list of (x, y)."""
        n = len(self._items)
        if n == 0:
            return []
        cx, cy = self._diameter / 2, self._diameter / 2
        # Arc radius (center of companion to center of each item)
        arc_r = self._diameter * 0.36 * self._expansion

        # Arrange items in a semicircle (top half, -π to 0)
        angles = []
        if n == 1:
            angles = [-math.pi / 2]
        else:
            total_angle = math.pi * 0.75  # 135 degrees arc
            start_angle = -math.pi - total_angle / 2 + math.pi  # centered top
            for i in range(n):
                t = i / (n - 1)
                angles.append(start_angle + total_angle * t)

        positions = []
        for angle in angles:
            x = cx + arc_r * math.cos(angle)
            y = cy + arc_r * math.sin(angle)
            positions.append((x, y))
        return positions

    # ---- Paint ----

    def paintEvent(self, event):
        if self._expansion < 0.01:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx, cy = self._diameter / 2.0, self._diameter / 2.0

        # Backdrop
        backdrop_r = self._diameter * 0.45 * self._expansion
        backdrop_color = QColor(255, 255, 255, int(235 * self._expansion))
        p.setPen(QPen(QColor(0, 0, 0, int(18 * self._expansion)), 1))
        p.setBrush(QBrush(backdrop_color))
        p.drawEllipse(int(cx - backdrop_r), int(cy - backdrop_r),
                      int(backdrop_r * 2), int(backdrop_r * 2))

        # Items
        positions = self._item_positions()
        font = QFont("Segoe UI", 9)
        label_font = QFont(FONT_MONO.split(",")[0].strip('"'), 6)
        label_font.setBold(True)

        for i, (item, (ix, iy)) in enumerate(zip(self._items, positions)):
            is_hovered = self._hovered == i
            item_r = self._item_r

            # Item highlight
            if is_hovered:
                highlight = QColor(79, 110, 247, 20)
                p.setPen(QPen(QColor(79, 110, 247, 140), 1.5))
                p.setBrush(QBrush(highlight))
                p.drawEllipse(int(ix - item_r - 3), int(iy - item_r - 3),
                            int((item_r + 3) * 2), int((item_r + 3) * 2))

            # Item background
            bg = QColor(255, 255, 255, int(220 * self._expansion))
            p.setPen(QPen(QColor(0, 0, 0, int(14 * self._expansion)), 1))
            p.setBrush(QBrush(bg))
            p.drawEllipse(int(ix - item_r), int(iy - item_r),
                         int(item_r * 2), int(item_r * 2))

            # Icon
            p.setFont(font)
            icon_color = QColor(item.accent_color)
            icon_color.setAlpha(int(255 * self._expansion))
            p.setPen(QPen(icon_color))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawText(int(ix - 10), int(iy - 2), 20, 20,
                      Qt.AlignmentFlag.AlignCenter, item.icon)

            # Label below item
            label_y = iy + item_r + 6
            p.setFont(label_font)
            label_c = QColor(COMPANION_TEXT_TERTIARY)
            label_c.setAlpha(int(220 * self._expansion))
            p.setPen(QPen(label_c))
            p.drawText(int(ix - 18), int(label_y), 36, 10,
                      Qt.AlignmentFlag.AlignCenter, item.label)

        p.end()
