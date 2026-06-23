"""
Companion widget — the core circular visual component.

Renders the companion's body with glass-morphism layers,
state-driven glow ring, core circle, and pupil.
All visual rendering is done in paintEvent (no image assets).
"""
import math
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, pyqtProperty, pyqtSignal,
)
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QPainterPath, QFont,
)

from src.theme import (
    COMPANION_IDLE, COMPANION_LISTENING, COMPANION_TRANSLATING,
    COMPANION_INTENSE, COMPANION_SLEEP, COMPANION_GLASS,
    COMPANION_TEXT_PRIMARY, COMPANION_TEXT_TERTIARY,
)


# State → color mapping
STATE_COLORS = {
    "idle":        COMPANION_IDLE,
    "listening":   COMPANION_LISTENING,
    "translating": COMPANION_TRANSLATING,
    "intense":     COMPANION_INTENSE,
    "sleep":       COMPANION_SLEEP,
}


class CompanionWidget(QWidget):
    """Circular companion character with state-driven visuals.

    Qt properties (for QPropertyAnimation):
        breath: 0.0→1.0  — sinusoidal breathing (IDLE)
        pulse:  0.0→1.0  — attentive pulse (LISTENING)
        ripple: 0.0→6.28 — orbital ripple (TRANSLATING)
        flash:  0.0→1.0  — intensity flash (INTENSE)
    """

    clicked = pyqtSignal()
    double_clicked = pyqtSignal()
    right_clicked = pyqtSignal()

    def __init__(self, size_px: int = 80, parent=None):
        super().__init__(parent)
        self._size = size_px
        self._state = "idle"
        self._glow_color = QColor(COMPANION_IDLE)

        # Animation values
        self._breath = 0.0
        self._pulse = 0.0
        self._ripple = 0.0
        self._flash = 0.0

        self.setFixedSize(size_px + 22, size_px + 22)  # extra for glow ring + margin
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)

        # ---- Animations ----
        # Breath (IDLE)
        self._breath_anim = QPropertyAnimation(self, b"breath")
        self._breath_anim.setDuration(4200)
        self._breath_anim.setStartValue(0.0)
        self._breath_anim.setEndValue(1.0)
        self._breath_anim.setEasingCurve(QEasingCurve.Type.InOutSine)

        # Pulse (LISTENING)
        self._pulse_anim = QPropertyAnimation(self, b"pulse")
        self._pulse_anim.setDuration(1400)
        self._pulse_anim.setStartValue(0.0)
        self._pulse_anim.setEndValue(1.0)
        self._pulse_anim.setEasingCurve(QEasingCurve.Type.InOutSine)

        # Ripple (TRANSLATING) — continuous
        self._ripple_anim = QPropertyAnimation(self, b"ripple")
        self._ripple_anim.setDuration(3000)
        self._ripple_anim.setStartValue(0.0)
        self._ripple_anim.setEndValue(6.28)
        self._ripple_anim.setEasingCurve(QEasingCurve.Type.Linear)

        # Flash (INTENSE)
        self._flash_anim = QPropertyAnimation(self, b"flash")
        self._flash_anim.setDuration(400)
        self._flash_anim.setStartValue(0.0)
        self._flash_anim.setEndValue(1.0)
        self._flash_anim.setEasingCurve(QEasingCurve.Type.InOutSine)

        # Auto-repeat
        self._breath_anim.setLoopCount(-1)
        self._pulse_anim.setLoopCount(-1)
        self._ripple_anim.setLoopCount(-1)

        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._end_flash)

        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # ---- Qt Properties ----

    def get_breath(self) -> float: return self._breath
    def set_breath(self, v: float): self._breath = v; self.update()
    breath = pyqtProperty(float, get_breath, set_breath)

    def get_pulse(self) -> float: return self._pulse
    def set_pulse(self, v: float): self._pulse = v; self.update()
    pulse = pyqtProperty(float, get_pulse, set_pulse)

    def get_ripple(self) -> float: return self._ripple
    def set_ripple(self, v: float): self._ripple = v; self.update()
    ripple = pyqtProperty(float, get_ripple, set_ripple)

    def get_flash(self) -> float: return self._flash
    def set_flash(self, v: float): self._flash = v; self.update()
    flash = pyqtProperty(float, get_flash, set_flash)

    # ---- Public API ----

    def set_state(self, state: str) -> None:
        """Transition to a new visual state."""
        if state == self._state:
            return
        self._state = state
        color = STATE_COLORS.get(state, COMPANION_IDLE)
        self._glow_color = QColor(color)

        # Stop all animations
        self._breath_anim.stop()
        self._pulse_anim.stop()
        self._ripple_anim.stop()
        self._flash_anim.stop()
        self._flash_timer.stop()

        # Reset values
        self._breath = 0.0
        self._pulse = 0.0
        self._ripple = 0.0
        self._flash = 0.0

        # Start state-appropriate animation
        if state == "idle":
            self._breath_anim.start()
        elif state == "listening":
            self._pulse_anim.start()
        elif state == "translating":
            self._ripple_anim.start()
        elif state == "intense":
            self._flash_anim.start()
            self._flash_timer.start(600)
        # sleep: no animation (static)

        self.update()

    def _end_flash(self) -> None:
        self._flash_anim.stop()
        self._flash = 0.0
        self.update()

    def set_size(self, px: int) -> None:
        self._size = max(60, min(200, px))
        self.setFixedSize(self._size + 22, self._size + 22)
        self.update()

    # ---- Mouse ----

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
        elif event.button() == Qt.MouseButton.RightButton:
            self.right_clicked.emit()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            dx = pos.x() - getattr(self, '_press_pos', pos).x()
            dy = pos.y() - getattr(self, '_press_pos', pos).y()
            if abs(dx) < 5 and abs(dy) < 5:
                self.clicked.emit()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()

    # ---- Paint ----

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        radius = self._size / 2.0

        # Helper: draw a circle with float center + radius
        def _draw_circle(cx_f, cy_f, r_f, pen, brush):
            x = int(cx_f - r_f)
            y = int(cy_f - r_f)
            d = int(r_f * 2)
            p.setPen(pen)
            p.setBrush(brush)
            p.drawEllipse(x, y, d, d)

        # 1. Glass outer ring
        outer_r = radius + 10.0
        _draw_circle(cx, cy, outer_r,
                     QPen(QColor(0, 0, 0, 50), 1.5),
                     QBrush(QColor(255, 255, 255, 230)))

        # 2. Status ring
        if self._state == "intense":
            intense_alpha = int(80 + 60 * self._flash)
            ring_color = QColor(255, 59, 48, intense_alpha)
        elif self._state == "translating":
            ring_alpha = int(65 + 25 * abs(math.sin(self._ripple)))
            ring_color = QColor(52, 199, 89, ring_alpha)
        elif self._state == "listening":
            ring_alpha = int(50 + 40 * self._pulse)
            ring_color = QColor(79, 110, 247, ring_alpha)
        elif self._state == "idle":
            ring_alpha = int(20 + 12 * abs(math.sin(self._breath * math.pi)))
            ring_color = QColor(79, 110, 247, ring_alpha)
        elif self._state == "sleep":
            ring_alpha = int(15 + 5 * abs(math.sin(self._breath * math.pi * 0.3)))
            ring_color = QColor(148, 150, 168, ring_alpha)
        else:
            ring_color = QColor(79, 110, 247, 20)

        _draw_circle(cx, cy, radius + 4.0,
                     QPen(ring_color, 2),
                     Qt.BrushStyle.NoBrush)

        # 3. Inner circle
        _draw_circle(cx, cy, radius - 2.0,
                     QPen(QColor(0, 0, 0, 10), 1),
                     QBrush(QColor(255, 255, 255, 200)))

        # 4. Pupil ring
        pupil_ring_r = self._pupil_ring_size()
        _draw_circle(cx, cy, pupil_ring_r,
                     QPen(QColor(self._glow_color.red(), self._glow_color.green(),
                                 self._glow_color.blue(), 45), 1.5),
                     Qt.BrushStyle.NoBrush)

        # 5. Core circle
        core_r = self._core_size()
        _draw_circle(cx, cy, core_r,
                     Qt.PenStyle.NoPen,
                     QBrush(self._glow_color))

        # 6. Core dot (pupil)
        dot_r = self._pupil_size()
        dot_offset = 0.0
        if self._state == "translating":
            dot_offset = core_r * 0.3
        dot_cx = cx + math.cos(self._ripple) * dot_offset if dot_offset else cx
        dot_cy = cy + math.sin(self._ripple) * dot_offset if dot_offset else cy

        _draw_circle(dot_cx, dot_cy, dot_r,
                     Qt.PenStyle.NoPen,
                     QBrush(QColor(255, 255, 255)))

        p.end()

    def _core_size(self) -> float:
        base = self._size * 0.13
        if self._state == "sleep":
            return base * 0.7
        if self._state == "intense":
            return base * 1.2
        return base

    def _pupil_size(self) -> float:
        base = self._size * 0.032
        if self._state == "listening":
            return base * 1.4
        if self._state == "intense":
            return base * 0.55
        if self._state == "sleep":
            return base * 0.75
        return base

    def _pupil_ring_size(self) -> float:
        base = self._size * 0.19
        if self._state == "listening":
            return base * 1.25
        if self._state == "translating":
            return base * 1.40
        if self._state == "intense":
            return base * 0.85
        if self._state == "sleep":
            return base * 0.90
        return base
