"""
Theme engine — centralized colors, typography, and QSS stylesheet for the
Cyberpunk/Neon Dark aesthetic.

All visual constants live here so tweaking the theme means editing one file.
"""

# ============================================================================
# Color Palette
# ============================================================================

# Backgrounds
DEEP_BG = "#0A0A0F"
SURFACE = "#12121A"
SURFACE_RAISED = "#1A1A24"
SURFACE_OVERLAY = "#0D0D14"
GLASS_BG = "rgba(10,10,15,0.85)"      # Semi-transparent for glass effects

# Neon accents
NEON_CYAN = "#00E5FF"
NEON_GREEN = "#00FF41"
NEON_PINK = "#FF007F"
NEON_PURPLE = "#B400FF"

# Semantic
AMBER = "#FFB74D"
RED = "#FF1744"

# Text
TEXT_PRIMARY = "#E0E0E0"
TEXT_SECONDARY = "#787899"
TEXT_DIM = "#4A4A5E"
TEXT_WHITE = "#FFFFFF"

# Borders
BORDER_SUBTLE = "#1E1E2E"
BORDER_NEON = "#00E5FF"

# ============================================================================
# Typography
# ============================================================================

FONT_HEADER = '"Bahnschrift", "Segoe UI", sans-serif'
FONT_MONO = '"Cascadia Code", "Consolas", "Courier New", monospace'
FONT_BODY = '"Segoe UI", sans-serif'
FONT_CJK = '"Microsoft YaHei", "微软雅黑", "SimHei", "黑体", sans-serif'

# ============================================================================
# Full Application Stylesheet
# ============================================================================


def get_app_stylesheet() -> str:
    """Return the complete QSS stylesheet for the entire application.

    Apply once via QApplication.setStyleSheet() in main.py.
    """
    return f"""
        /* ---- Global ---- */
        QMainWindow {{
            background-color: {DEEP_BG};
            color: {TEXT_PRIMARY};
        }}

        QWidget {{
            font-family: {FONT_BODY};
            font-size: 12px;
            color: {TEXT_PRIMARY};
        }}

        /* ---- Labels ---- */
        QLabel {{
            color: {TEXT_PRIMARY};
            background: transparent;
            border: none;
        }}

        /* ---- Buttons (base, overridden by NeonButton QSS in widgets.py) ---- */
        QPushButton {{
            background-color: {SURFACE};
            color: {TEXT_PRIMARY};
            border: 1px solid {BORDER_SUBTLE};
            border-radius: 2px;
            padding: 6px 16px;
            font-family: {FONT_HEADER};
            font-size: 11px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background-color: {SURFACE_RAISED};
            border-color: {NEON_CYAN};
        }}
        QPushButton:pressed {{
            background-color: {SURFACE};
        }}
        QPushButton:disabled {{
            color: {TEXT_DIM};
            border-color: #2A2A3E;
            background-color: transparent;
        }}

        /* ---- Inputs ---- */
        QComboBox, QSpinBox {{
            background-color: {SURFACE};
            color: {TEXT_PRIMARY};
            border: 1px solid {BORDER_SUBTLE};
            border-radius: 2px;
            padding: 4px 10px;
            font-family: {FONT_MONO};
            font-size: 11px;
        }}
        QComboBox:focus, QSpinBox:focus {{
            border: 1px solid {NEON_CYAN};
        }}
        QComboBox::drop-down {{
            border: none;
            width: 20px;
        }}
        QComboBox::down-arrow {{
            width: 0;
            height: 0;
        }}
        QComboBox QAbstractItemView {{
            background-color: {SURFACE};
            color: {TEXT_PRIMARY};
            selection-background-color: {SURFACE_RAISED};
            selection-color: {NEON_CYAN};
            border: 1px solid {BORDER_SUBTLE};
            outline: none;
        }}
        QSpinBox::up-button, QSpinBox::down-button {{
            background-color: {SURFACE_RAISED};
            border: none;
            width: 18px;
        }}
        QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
            background-color: #252535;
        }}

        /* ---- Checkboxes ---- */
        QCheckBox {{
            color: {TEXT_PRIMARY};
            font-family: {FONT_HEADER};
            font-size: 11px;
            spacing: 8px;
        }}
        QCheckBox::indicator {{
            width: 16px;
            height: 16px;
            border: 1px solid {BORDER_SUBTLE};
            border-radius: 2px;
            background-color: {SURFACE};
        }}
        QCheckBox::indicator:checked {{
            background-color: {NEON_CYAN};
            border-color: {NEON_CYAN};
        }}
        QCheckBox::indicator:hover {{
            border-color: {NEON_CYAN};
        }}

        /* ---- Text Edit / Log Area ---- */
        QTextEdit {{
            background-color: #050508;
            color: {NEON_GREEN};
            border: 1px solid {BORDER_SUBTLE};
            border-radius: 2px;
            font-family: {FONT_MONO};
            font-size: 10px;
            selection-background-color: {NEON_GREEN};
            selection-color: #050508;
        }}

        /* ---- Scrollbars ---- */
        QScrollBar:vertical {{
            background: {DEEP_BG};
            width: 6px;
            margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: {TEXT_DIM};
            min-height: 20px;
            border-radius: 3px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {NEON_CYAN};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
        }}
        QScrollBar:horizontal {{
            background: {DEEP_BG};
            height: 6px;
            margin: 0;
        }}
        QScrollBar::handle:horizontal {{
            background: {TEXT_DIM};
            min-width: 20px;
            border-radius: 3px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background: {NEON_CYAN};
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0;
        }}

        /* ---- Tooltips ---- */
        QToolTip {{
            background-color: {SURFACE_RAISED};
            color: {TEXT_PRIMARY};
            border: 1px solid {NEON_CYAN};
            padding: 4px 8px;
            font-family: {FONT_MONO};
            font-size: 10px;
        }}
    """


# ============================================================================
# Per-Widget QSS Helpers
# ============================================================================


def neon_button_style(color: str, hover_color: str = "") -> str:
    """QSS for a NeonButton with the given neon accent color."""
    if not hover_color:
        hover_color = _brighten_hex(color, 0.4)

    r, g, b = _hex_to_rgb(color)
    bg_10 = f"rgba({r},{g},{b},0.10)"
    bg_20 = f"rgba({r},{g},{b},0.20)"

    return f"""
        NeonButton {{
            background-color: transparent;
            color: {color};
            border: 1px solid {color};
            border-radius: 2px;
            padding: 7px 20px;
            font-family: {FONT_HEADER};
            font-size: 11px;
            font-weight: 700;
        }}
        NeonButton:hover {{
            background-color: {bg_10};
            border: 1px solid {hover_color};
            color: {hover_color};
        }}
        NeonButton:pressed {{
            background-color: {bg_20};
        }}
        NeonButton:disabled {{
            color: {TEXT_DIM};
            border-color: #2A2A3E;
            background-color: transparent;
        }}
    """


def pin_button_style() -> str:
    """QSS for a pin/always-on-top toggle button."""
    return f"""
        PinButton {{
            background-color: transparent;
            color: {TEXT_SECONDARY};
            border: 1px solid {BORDER_SUBTLE};
            border-radius: 2px;
            padding: 4px 10px;
            font-family: {FONT_HEADER};
            font-size: 10px;
            font-weight: 600;
        }}
        PinButton:hover {{
            border-color: {NEON_CYAN};
            color: {NEON_CYAN};
        }}
        PinButton[active="true"] {{
            color: {NEON_CYAN};
            border-color: {NEON_CYAN};
            background-color: {_hex_to_rgba(NEON_CYAN, 0.10)};
        }}
        PinButton[active="true"]:hover {{
            color: {NEON_PINK};
            border-color: {NEON_PINK};
            background-color: {_hex_to_rgba(NEON_PINK, 0.10)};
        }}
    """


def panel_header_style() -> str:
    """QSS for CollapsiblePanel header buttons."""
    return f"""
        QPushButton {{
            background-color: {SURFACE};
            color: {NEON_CYAN};
            border: none;
            border-left: 2px solid {NEON_CYAN};
            border-radius: 0;
            padding: 8px 12px;
            text-align: left;
            font-family: {FONT_HEADER};
            font-size: 11px;
            font-weight: 700;
        }}
        QPushButton:hover {{
            background-color: {SURFACE_RAISED};
            color: {_brighten_hex(NEON_CYAN, 0.3)};
        }}
    """


def transparent_overlay_style() -> str:
    """QSS for transparent subtitle overlay window."""
    return f"""
        QMainWindow {{
            background: transparent;
        }}
        QWidget {{
            background: transparent;
            color: {TEXT_WHITE};
        }}
        QLabel {{
            background: transparent;
            color: {TEXT_WHITE};
            border: none;
        }}
    """


def terminal_log_style() -> str:
    """QSS for the retro terminal log area (green phosphor)."""
    return f"""
        QTextEdit {{
            background-color: #050508;
            color: {NEON_GREEN};
            border: 1px solid #0A2A0A;
            border-radius: 0;
            font-family: {FONT_MONO};
            font-size: 10px;
            selection-background-color: {NEON_GREEN};
            selection-color: #050508;
        }}
    """


# ============================================================================
# Companion Theme — Professional Tool Style
# ============================================================================

# Backgrounds & Surfaces
COMPANION_GLASS = "rgba(255,255,255,0.75)"
COMPANION_GLASS_DARK = "rgba(30,31,43,0.82)"
COMPANION_CARD = "rgba(255,255,255,0.92)"
COMPANION_PANEL = "rgba(255,255,255,0.88)"

# Accent colors
COMPANION_ACCENT = "#4F6EF7"
COMPANION_SUCCESS = "#34C759"
COMPANION_WARNING = "#FF9500"
COMPANION_DANGER = "#FF3B30"

# State colors
COMPANION_IDLE = "#4F6EF7"
COMPANION_LISTENING = "#4F6EF7"
COMPANION_TRANSLATING = "#34C759"
COMPANION_INTENSE = "#FF3B30"
COMPANION_SLEEP = "#9496A8"

# Text
COMPANION_TEXT_PRIMARY = "#1A1B2E"
COMPANION_TEXT_SECONDARY = "#5A5C72"
COMPANION_TEXT_TERTIARY = "#9496A8"
COMPANION_TEXT_ON_DARK = "#E8E9F0"

# Borders & Shadows
COMPANION_BORDER = "rgba(0,0,0,0.07)"
COMPANION_SHADOW_SM = "0 2px 8px rgba(0,0,0,0.05)"
COMPANION_SHADOW_MD = "0 8px 24px rgba(0,0,0,0.09)"


def companion_window_style() -> str:
    """QSS for the CompanionWindow."""
    return """
        QMainWindow {
            background: transparent;
        }
        QWidget {
            background: transparent;
            color: """ + COMPANION_TEXT_PRIMARY + """;
        }
        QLabel {
            background: transparent;
            border: none;
            color: """ + COMPANION_TEXT_PRIMARY + """;
        }
    """


def companion_glass_panel_style() -> str:
    """QSS for glass panel widgets."""
    return f"""
        QWidget {{
            background: {COMPANION_GLASS};
            border: 1px solid {COMPANION_BORDER};
            border-radius: 9px;
        }}
        QLabel {{
            color: {COMPANION_TEXT_PRIMARY};
            background: transparent;
            border: none;
        }}
    """


def companion_dropdown_style() -> str:
    """QSS for companion combo boxes."""
    return f"""
        QComboBox {{
            background: rgba(0,0,0,0.03);
            color: {COMPANION_TEXT_PRIMARY};
            border: 1px solid {COMPANION_BORDER};
            border-radius: 5px;
            padding: 6px 10px;
            font-family: {FONT_MONO};
            font-size: 10px;
        }}
        QComboBox:hover {{
            border-color: {COMPANION_ACCENT};
        }}
        QComboBox:focus {{
            border-color: {COMPANION_ACCENT};
            outline: none;
        }}
        QComboBox::drop-down {{
            border: none;
            width: 20px;
        }}
        QComboBox QAbstractItemView {{
            background: rgba(255,255,255,0.95);
            color: {COMPANION_TEXT_PRIMARY};
            selection-background-color: rgba(79,110,247,0.10);
            selection-color: {COMPANION_ACCENT};
            border: 1px solid {COMPANION_BORDER};
            border-radius: 5px;
            outline: none;
        }}
    """


# ============================================================================
# Color Utilities
# ============================================================================


def _hex_to_rgb(hex_color: str) -> tuple:
    """Parse #RRGGBB to (r, g, b) ints."""
    hex_color = hex_color.lstrip("#")
    return int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    """Convert #RRGGBB to rgba(r,g,b,a) string."""
    r, g, b = _hex_to_rgb(hex_color)
    return f"rgba({r},{g},{b},{alpha:.2f})"


def _brighten_hex(hex_color: str, factor: float) -> str:
    """Brighten a hex color by blending toward white."""
    r, g, b = _hex_to_rgb(hex_color)
    r = int(r + (255 - r) * factor)
    g = int(g + (255 - g) * factor)
    b = int(b + (255 - b) * factor)
    return f"#{r:02X}{g:02X}{b:02X}"
