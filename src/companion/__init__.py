"""
Companion package — AI Desktop Companion UI.

Replaces or coexists with the traditional ControlPanel.
"""
from src.companion.state_machine import CompanionState, CompanionStateMachine
from src.companion.companion_widget import CompanionWidget
from src.companion.companion_window import CompanionWindow
from src.companion.radial_menu import RadialMenu, RadialItem
from src.companion.device_panel import DevicePanel

__all__ = [
    "CompanionState",
    "CompanionStateMachine",
    "CompanionWidget",
    "CompanionWindow",
    "RadialMenu",
    "RadialItem",
    "DevicePanel",
]
