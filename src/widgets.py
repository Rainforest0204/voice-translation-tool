"""
Custom reusable widgets for the Cyberpunk/Neon Dark UI.

- CollapsiblePanel : accordion-style expand/collapse container
- NeonButton       : neon-outlined push button with hover glow
- PinButton        : always-on-top toggle button
- StatusIndicator  : pulsing neon dot with state-driven animation
- HudStat          : HUD-style metric display (label + value)
"""
import logging
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QSizePolicy,
)
from PyQt6.QtCore import (
    Qt, QPropertyAnimation, QEasingCurve, QTimer, pyqtProperty, pyqtSignal,
)
from PyQt6.QtGui import QPainter, QColor, QFont, QPen, QBrush, QFontDatabase

from src.theme import (
    SURFACE, SURFACE_RAISED, NEON_CYAN, NEON_GREEN, NEON_PINK, AMBER, RED,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_DIM, BORDER_SUBTLE,
    FONT_HEADER, FONT_MONO, FONT_CJK,
    neon_button_style, pin_button_style, panel_header_style,
)

logger = logging.getLogger(__name__)


# ============================================================================
# CollapsiblePanel — accordion container
# ============================================================================

class CollapsiblePanel(QWidget):
    """A panel with a clickable header that expands/collapses content.

    Usage:
        panel = CollapsiblePanel("SETTINGS")
        layout = panel.content_layout()
        layout.addWidget(...)
    """

    toggled = pyqtSignal(bool)  # emitted when collapsed state changes

    def __init__(self, title: str = "", parent=None, collapsed: bool = False):
        super().__init__(parent)
        self._title = title
        self._collapsed = collapsed
        self._stored_height = 0
        self._anim_duration = 250  # ms

        # Outer layout
        self._outer_layout = QVBoxLayout(self)
        self._outer_layout.setContentsMargins(0, 0, 0, 0)
        self._outer_layout.setSpacing(0)

        # Header button
        arrow = "▶" if self._collapsed else "▼"  # ▶ or ▼
        self._header_btn = QPushButton(f"  {arrow}  {self._title}")
        self._header_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header_btn.setStyleSheet(panel_header_style())
        self._header_btn.clicked.connect(self.toggle)
        self._outer_layout.addWidget(self._header_btn)

        # Content area
        self._content = QWidget()
        self._content.setStyleSheet(
            f"background-color: {SURFACE}; border: none; padding: 6px;"
        )
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(10, 6, 10, 8)
        self._content_layout.setSpacing(6)

        if self._collapsed:
            self._content.setVisible(False)
            self._content.setMaximumHeight(0)

        self._outer_layout.addWidget(self._content)

        # Build animation
        self._anim = QPropertyAnimation(self._content, b"maximumHeight")
        self._anim.setDuration(self._anim_duration)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

    def content_layout(self) -> QVBoxLayout:
        """Return the content area's layout for callers to populate."""
        return self._content_layout

    def toggle(self) -> None:
        """Toggle collapsed state with animation."""
        self._collapsed = not self._collapsed
        self._update_header()
        self._animate()
        self.toggled.emit(self._collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        """Programmatically set collapsed state."""
        if self._collapsed == collapsed:
            return
        self._collapsed = collapsed
        self._update_header()
        self._animate()
        self.toggled.emit(self._collapsed)

    @property
    def is_collapsed(self) -> bool:
        return self._collapsed

    def _update_header(self) -> None:
        arrow = "▶" if self._collapsed else "▼"
        self._header_btn.setText(f"  {arrow}  {self._title}")

    def _animate(self) -> None:
        if self._collapsed:
            self._stored_height = max(
                self._content.sizeHint().height(), self._content.minimumHeight()
            )
            self._anim.setStartValue(self._stored_height)
            self._anim.setEndValue(0)
        else:
            self._content.setVisible(True)
            target = max(self._stored_height, self._content.sizeHint().height(), 60)
            self._anim.setStartValue(0)
            self._anim.setEndValue(target)

        self._anim.start()


# ============================================================================
# NeonButton — neon-outlined push button
# ============================================================================

class NeonButton(QPushButton):
    """A button with a neon-colored border that brightens on hover.

    Usage:
        btn = NeonButton("START", neon_color=NEON_GREEN)
    """

    def __init__(self, text: str = "", neon_color: str = NEON_CYAN, parent=None):
        super().__init__(text, parent)
        self._neon_color = neon_color
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(neon_button_style(neon_color))

    def set_neon_color(self, color: str) -> None:
        """Change the neon accent color at runtime."""
        self._neon_color = color
        self.setStyleSheet(neon_button_style(color))


# ============================================================================
# PinButton — always-on-top toggle button
# ============================================================================

class PinButton(QPushButton):
    """Toggle button for always-on-top behavior.

    Usage:
        btn = PinButton("PIN")
        btn.clicked.connect(lambda: ...)
    """

    def __init__(self, text: str = "◇  PIN", parent=None):  # ◇ PIN
        super().__init__(text, parent)
        self._active = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(pin_button_style())
        self.setCheckable(True)

    def set_active(self, active: bool) -> None:
        """Update the active/pinned visual state."""
        self._active = active
        self.setProperty("active", "true" if active else "false")
        self.setStyleSheet(pin_button_style())
        # Force style refresh
        self.style().unpolish(self)
        self.style().polish(self)
        if active:
            self.setText("◆  PINNED")  # ◆ PINNED
        else:
            self.setText("◇  PIN")     # ◇ PIN


# ============================================================================
# StatusIndicator — pulsing neon dot
# ============================================================================

class StatusIndicator(QWidget):
    """A small pulsing neon status dot with glow.

    States: "ready", "running", "paused", "error"
    """

    STATE_COLORS = {
        "ready": NEON_CYAN,
        "running": NEON_GREEN,
        "paused": AMBER,
        "error": RED,
    }

    def __init__(self, parent=None, state: str = "ready"):
        super().__init__(parent)
        self._state = state
        self._pulse = 0.3  # 0.0 .. 1.0

        self.setFixedSize(24, 24)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # Pulse animation
        self._anim = QPropertyAnimation(self, b"pulse")
        self._anim.setDuration(2000)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutSine)

        self._apply_state()

    # Qt property for animation
    def get_pulse(self) -> float:
        return self._pulse

    def set_pulse(self, value: float) -> None:
        self._pulse = value
        self.update()  # trigger repaint

    pulse = pyqtProperty(float, get_pulse, set_pulse)

    def set_state(self, state: str) -> None:
        """Change the indicator state."""
        if state not in self.STATE_COLORS:
            return
        self._state = state
        self._apply_state()

    @property
    def state(self) -> str:
        return self._state

    def _apply_state(self) -> None:
        """Start/stop animation based on state."""
        self._anim.stop()
        if self._state == "running":
            self._anim.setDuration(2000)
            self._anim.setLoopCount(-1)  # infinite
            self._anim.start()
        elif self._state == "paused":
            self._anim.setDuration(3000)
            self._anim.setLoopCount(-1)
            self._anim.start()
        elif self._state == "error":
            self._anim.setDuration(600)
            self._anim.setLoopCount(-1)
            self._anim.start()
        else:
            # ready: static
            self._pulse = 0.3
            self.update()

    def paintEvent(self, event) -> None:
        from PyQt6 import QtCore

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        base = QColor(self.STATE_COLORS.get(self._state, NEON_CYAN))
        cx = self.width() / 2
        cy = self.height() / 2

        pulse_val = self._pulse if self._state != "ready" else 0.3

        # Outer glow layers (3 concentric circles, larger = softer)
        for layer in range(3, 0, -1):
            alpha = int(max(0, min(255, base.alpha() * 0.3 / layer * (0.6 + 0.4 * pulse_val))))
            radius = 4 + layer * 3 + pulse_val * 2
            glow = QColor(base.red(), base.green(), base.blue(), alpha)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(glow))
            painter.drawEllipse(QtCore.QPointF(cx, cy), radius, radius)

        # Solid core
        core_alpha = int(200 + 55 * pulse_val)
        core = QColor(base.red(), base.green(), base.blue(), core_alpha)
        painter.setBrush(QBrush(core))
        painter.drawEllipse(QtCore.QPointF(cx, cy), 3.5, 3.5)

        painter.end()


# ============================================================================
# HudStat — HUD-style metric display
# ============================================================================

class HudStat(QWidget):
    """A single HUD-style stat card showing a label and value.

    Usage:
        stat = HudStat("CAPTURED", "0")
        stat.set_value("238")
    """

    def __init__(self, label: str = "", value: str = "0", parent=None):
        super().__init__(parent)
        self._label_text = label
        self._value_text = value

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)

        # Label
        self._label = QLabel(label.upper())
        self._label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-family: {FONT_MONO}; font-size: 8px; "
            f"font-weight: 600; border: none; background: transparent;"
        )
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._label)

        # Value
        self._value = QLabel(str(value))
        self._value.setStyleSheet(
            f"color: {NEON_CYAN}; font-family: {FONT_MONO}; font-size: 18px; "
            f"font-weight: 700; border: none; background: transparent;"
        )
        self._value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._value)

        # Card appearance
        self.setStyleSheet(
            f"background-color: #0D0D14; border: 1px solid {BORDER_SUBTLE}; border-radius: 2px;"
        )
        self.setMinimumWidth(80)

    def set_value(self, value: str) -> None:
        """Update the displayed value."""
        if value != self._value_text:
            self._value_text = value
            self._value.setText(str(value))

    def set_value_color(self, color: str) -> None:
        """Change the value text color."""
        self._value.setStyleSheet(
            f"color: {color}; font-family: {FONT_MONO}; font-size: 18px; "
            f"font-weight: 700; border: none; background: transparent;"
        )
