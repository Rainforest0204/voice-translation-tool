"""
Companion state machine — pure logic, no UI dependency.

Defines the companion's finite states and valid transitions.
Emits state_changed signal so visual components can react without coupling.
"""
import enum
from PyQt6.QtCore import QObject, QTimer, pyqtSignal


class CompanionState(enum.Enum):
    """States the companion can be in."""
    IDLE = "idle"
    LISTENING = "listening"
    TRANSLATING = "translating"
    INTENSE = "intense"
    SLEEP = "sleep"


class CompanionStateMachine(QObject):
    """Manages state transitions based on pipeline events.

    Signals:
        state_changed(old_state, new_state) — emitted on every valid transition.
    """

    state_changed = pyqtSignal(str, str)  # old_state, new_state

    def __init__(self, idle_timeout_sec: float = 120.0, parent=None):
        super().__init__(parent)
        self._current = CompanionState.IDLE
        self._idle_timeout_ms = int(idle_timeout_sec * 1000) if idle_timeout_sec > 0 else 0
        self._last_activity = 0.0

        # Idle -> Sleep auto-transition timer
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self._on_idle_timeout)

        # Intense cooldown timer
        self._intense_cooldown = QTimer(self)
        self._intense_cooldown.setSingleShot(True)
        self._intense_cooldown.timeout.connect(self._on_intense_cooldown)

        self._pre_intense_state = CompanionState.IDLE

        if self._idle_timeout_ms > 0:
            self._idle_timer.start(self._idle_timeout_ms)

    # ---- Public API ----

    @property
    def current_state(self) -> CompanionState:
        return self._current

    @property
    def state_name(self) -> str:
        return self._current.value

    def transition(self, new_state: CompanionState) -> bool:
        """Attempt to transition to *new_state*. Returns True if valid."""
        if new_state == self._current:
            return False

        old_name = self._current.value
        self._current = new_state
        self._reset_activity_timer()

        self.state_changed.emit(old_name, new_state.value)
        return True

    def on_audio_active(self) -> None:
        """Called when audio chunks arrive (pipeline polling)."""
        import time
        self._last_activity = time.monotonic()
        if self._current in (CompanionState.IDLE, CompanionState.SLEEP):
            self.transition(CompanionState.LISTENING)
        self._reset_idle_timer()

    def on_asr_started(self) -> None:
        """Called when audio buffer is flushed to ASR."""
        if self._current not in (CompanionState.INTENSE, CompanionState.SLEEP):
            self.transition(CompanionState.TRANSLATING)

    def on_translation_complete(self) -> None:
        """Called when translation result is ready."""
        if self._current == CompanionState.TRANSLATING:
            self.transition(CompanionState.IDLE)
        self._reset_idle_timer()

    def on_intense_audio(self, rms_value: float) -> None:
        """Called when high-energy audio is detected."""
        if self._current != CompanionState.INTENSE:
            self._pre_intense_state = self._current
            self.transition(CompanionState.INTENSE)
        # Restart cooldown
        self._intense_cooldown.stop()
        self._intense_cooldown.start(2000)  # 2s cooldown after last intense burst

    def on_pause(self) -> None:
        """User paused translation."""
        self.transition(CompanionState.SLEEP)
        self._idle_timer.stop()

    def on_resume(self) -> None:
        """User resumed translation."""
        if self._current == CompanionState.SLEEP:
            self.transition(CompanionState.IDLE)
        self._reset_idle_timer()

    # ---- Internal ----

    def _on_idle_timeout(self) -> None:
        """Auto-transition to SLEEP after prolonged idle."""
        if self._current == CompanionState.IDLE:
            self.transition(CompanionState.SLEEP)

    def _on_intense_cooldown(self) -> None:
        """Return to previous state after intense cooldown."""
        if self._current == CompanionState.INTENSE:
            self.transition(self._pre_intense_state)

    def _reset_activity_timer(self) -> None:
        import time
        self._last_activity = time.monotonic()

    def _reset_idle_timer(self) -> None:
        if self._idle_timeout_ms > 0:
            self._idle_timer.stop()
            self._idle_timer.start(self._idle_timeout_ms)
