"""Design tokens and the application stylesheet.

Qt Style Sheets are CSS-like, so the interface gets a real design system
rather than colours scattered through the widget code: one neutral family,
one accent, three semantic states, a typographic scale, and explicit hover /
pressed / focus / disabled states.

Two rules the rest of the UI follows:
- no widget sets its own colours inline; it sets an object name or a dynamic
  property and this stylesheet decides how that looks;
- anything numeric (counters, sizes, throughput, hashes, paths) is rendered in
  a monospaced face so digits stop shifting while a transfer runs.
"""

from __future__ import annotations

from PySide6.QtGui import QFont, QFontDatabase

# --- Neutrals -------------------------------------------------------------
# One warm-tinted family, never mixed with cool greys.
BG = "#f6f5f3"
SURFACE = "#ffffff"
SURFACE_SUNK = "#faf9f7"
BORDER = "#e3e1dc"
BORDER_STRONG = "#d2cfc8"
TEXT = "#1c1b19"
TEXT_MUTED = "#6b6862"
TEXT_FAINT = "#918d86"

# --- One accent, plus three semantic states (all under 80% saturation) ----
ACCENT = "#1d6b4f"
ACCENT_HOVER = "#195c44"
ACCENT_PRESSED = "#144a37"
ACCENT_SOFT = "#eaf2ee"

WARN = "#9a6a12"
WARN_SOFT = "#fbf3e4"
DANGER = "#a33a32"
DANGER_SOFT = "#fbeeec"
NEUTRAL_DOT = "#9b978f"

# --- Type scale -----------------------------------------------------------
SIZE_DISPLAY = 15
SIZE_TITLE = 11
SIZE_BODY = 10
SIZE_SMALL = 9

# --- Spacing --------------------------------------------------------------
GAP = 11
PAD = 12


def ui_font_family() -> str:
    """Best available UI face, in order of character. System fonts only."""
    for name in ("Segoe UI Variable Text", "Segoe UI", "Inter", "Noto Sans"):
        if name in QFontDatabase.families():
            return name
    return QFont().defaultFamily()


def mono_font_family() -> str:
    """Monospaced face for figures, paths and hashes."""
    for name in ("Cascadia Mono", "Consolas", "JetBrains Mono",
                 "DejaVu Sans Mono", "Menlo"):
        if name in QFontDatabase.families():
            return name
    return "monospace"


def mono_font(size: int = SIZE_BODY, bold: bool = False) -> QFont:
    f = QFont(mono_font_family(), size)
    f.setStyleHint(QFont.Monospace)
    if bold:
        f.setWeight(QFont.DemiBold)
    return f


def stylesheet() -> str:
    """The whole application stylesheet."""
    ui, mono = ui_font_family(), mono_font_family()
    return f"""
/* ---------- base ---------- */
QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: "{ui}";
    font-size: {SIZE_BODY}pt;
}}
QMainWindow, QDialog {{ background: {BG}; }}

/* Labels and plain containers must never paint the window colour on top of a
   panel — otherwise every caption looks like a sunken input field. */
QLabel, QCheckBox {{ background: transparent; }}
QWidget#Cell, QWidget#Plain {{ background: transparent; }}

QToolTip {{
    background: {TEXT};
    color: {SURFACE};
    border: none;
    padding: 6px 8px;
}}

/* ---------- panels ---------- */
QGroupBox {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 10px;
    margin-top: 16px;
    padding: {PAD}px;
    font-size: {SIZE_SMALL}pt;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 2px;
    padding: 0 2px 6px 0;
    color: {TEXT_FAINT};
    font-weight: 600;
    letter-spacing: 1px;
}}

/* ---------- status card ---------- */
QFrame#StatusCard {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 2px;
}}
QFrame#StatusCard[state="ready"]  {{ background: {ACCENT_SOFT}; border-color: #cfe2d8; }}
QFrame#StatusCard[state="warn"]   {{ background: {WARN_SOFT};   border-color: #eedfc0; }}
QFrame#StatusCard[state="danger"] {{ background: {DANGER_SOFT}; border-color: #f0d4d0; }}

QLabel#StatusDot {{
    min-width: 10px; max-width: 10px;
    min-height: 10px; max-height: 10px;
    border-radius: 5px;
    background: {NEUTRAL_DOT};
}}
QFrame#StatusCard[state="ready"]  QLabel#StatusDot {{ background: {ACCENT}; }}
QFrame#StatusCard[state="warn"]   QLabel#StatusDot {{ background: {WARN}; }}
QFrame#StatusCard[state="danger"] QLabel#StatusDot {{ background: {DANGER}; }}

QLabel#StatusTitle {{
    font-size: {SIZE_DISPLAY}pt;
    font-weight: 600;
    letter-spacing: -0.3px;
}}
QLabel#StatusHint {{ color: {TEXT_MUTED}; }}
QLabel#StatusUdid {{
    color: {TEXT_FAINT};
    font-family: "{mono}";
    font-size: {SIZE_SMALL}pt;
}}

/* ---------- notice strips (update, error) ---------- */
QFrame#Notice {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 2px;
    background: {SURFACE};
}}
QFrame#Notice[tone="accent"] {{ background: {ACCENT_SOFT}; border-color: #cfe2d8; }}
QFrame#Notice[tone="danger"] {{ background: {DANGER_SOFT}; border-color: #f0d4d0; }}
QLabel#NoticeText {{ color: {TEXT}; }}

/* ---------- buttons ---------- */
QPushButton {{
    background: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    border-radius: 7px;
    padding: 7px 13px;
    font-weight: 500;
}}
QPushButton:hover  {{ background: {SURFACE_SUNK}; border-color: {TEXT_FAINT}; }}
QPushButton:pressed {{ background: #f0eee9; padding-top: 8px; padding-bottom: 6px; }}
QPushButton:focus  {{ border: 1px solid {ACCENT}; outline: none; }}
QPushButton:disabled {{
    background: {SURFACE};
    color: #b9b5ae;
    border-color: {BORDER};
}}

QPushButton#Primary {{
    background: {ACCENT};
    color: #ffffff;
    border: 1px solid {ACCENT};
    font-weight: 600;
}}
QPushButton#Primary:hover   {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton#Primary:pressed {{ background: {ACCENT_PRESSED}; }}
QPushButton#Primary:disabled {{
    background: #e8e6e1;
    color: #b0aca5;
    border-color: {BORDER};
}}

QPushButton#Quiet {{
    background: transparent;
    border-color: transparent;
    color: {TEXT_MUTED};
}}
QPushButton#Quiet:hover {{ background: {SURFACE_SUNK}; border-color: {BORDER}; color: {TEXT}; }}
QPushButton#Quiet:disabled {{ color: #c2beb7; background: transparent; border-color: transparent; }}

QPushButton#Stop {{ color: {DANGER}; border-color: #e8cdc9; background: {SURFACE}; }}
QPushButton#Stop:hover {{ background: {DANGER_SOFT}; border-color: {DANGER}; }}
QPushButton#Stop:disabled {{ color: #cbc7c1; border-color: {BORDER}; }}

/* ---------- inputs ---------- */
QComboBox {{
    background: {SURFACE};
    border: 1px solid {BORDER_STRONG};
    border-radius: 7px;
    padding: 6px 10px;
    min-width: 260px;
}}
QComboBox:hover {{ border-color: {TEXT_FAINT}; }}
QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox:disabled {{ color: {TEXT_FAINT}; background: {SURFACE_SUNK}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE};
    border: 1px solid {BORDER_STRONG};
    selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT};
    padding: 4px;
}}
QCheckBox {{ color: {TEXT}; spacing: 7px; }}
QCheckBox:disabled {{ color: {TEXT_FAINT}; }}
QCheckBox::indicator {{
    width: 15px; height: 15px;
    border: 1px solid {BORDER_STRONG};
    border-radius: 4px;
    background: {SURFACE};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QCheckBox::indicator:disabled {{ background: {SURFACE_SUNK}; }}

/* ---------- progress ---------- */
QProgressBar {{
    background: #eceae5;
    border: none;
    border-radius: 5px;
    height: 10px;
    text-align: center;
    color: transparent;      /* the numbers live in the labels below */
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}
QProgressBar[outcome="failed"]::chunk {{ background: {DANGER}; }}

/* ---------- labels ---------- */
QLabel#FieldLabel {{
    color: {TEXT_FAINT};
    font-size: {SIZE_SMALL}pt;
    font-weight: 600;
    letter-spacing: 0.6px;
    min-height: 14px;
    padding-bottom: 2px;
}}
QLabel#Value {{ color: {TEXT}; font-family: "{mono}"; }}
QLabel#ValueMuted {{ color: {TEXT_MUTED}; font-family: "{mono}"; font-size: {SIZE_SMALL}pt; }}
QLabel#Phase {{ color: {TEXT}; font-weight: 500; }}
QLabel#Hint {{ color: {TEXT_MUTED}; }}
QLabel#LockNote {{ color: {TEXT_FAINT}; font-size: {SIZE_SMALL}pt; }}
QLabel#EmptyTitle {{ color: {TEXT}; font-size: {SIZE_TITLE}pt; font-weight: 600; }}
QLabel#EmptyBody {{ color: {TEXT_MUTED}; }}

/* ---------- report ---------- */
QTextBrowser {{
    background: {SURFACE_SUNK};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 10px;
    selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT};
}}

/* ---------- scrollbars ---------- */
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: #d6d3cc; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: #bdb9b1; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle:horizontal {{
    background: #d6d3cc; border-radius: 5px; min-width: 30px;
}}

/* ---------- status bar ---------- */
QStatusBar {{ background: {BG}; color: {TEXT_MUTED}; border-top: 1px solid {BORDER}; }}
QStatusBar::item {{ border: none; }}

/* ---------- separators ---------- */
QFrame#VSep {{ background: {BORDER}; max-width: 1px; min-width: 1px; border: none; }}
"""


def report_document_css() -> str:
    """Stylesheet applied to the Markdown rendered in the report panel."""
    ui, mono = ui_font_family(), mono_font_family()
    return f"""
    body {{ font-family: "{ui}"; color: {TEXT}; font-size: {SIZE_BODY}pt; }}
    h1 {{ font-size: {SIZE_TITLE}pt; font-weight: 600; color: {TEXT};
          margin: 2px 0 8px 0; }}
    h2 {{ font-size: {SIZE_BODY}pt; font-weight: 600; color: {TEXT_MUTED};
          margin: 12px 0 4px 0; }}
    p, li {{ color: {TEXT}; line-height: 145%; }}
    code, pre {{ font-family: "{mono}"; color: {TEXT_MUTED}; }}
    a {{ color: {ACCENT}; }}
    """
