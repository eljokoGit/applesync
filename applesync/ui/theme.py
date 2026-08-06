"""Design tokens and the application stylesheet, light and dark.

Qt Style Sheets are CSS-like, so the interface gets a real design system
rather than colours scattered through the widget code: one neutral family,
one accent, three semantic states, a typographic scale, and explicit hover /
pressed / focus / disabled states.

Two rules the rest of the UI follows:
- no widget sets its own colours inline; it sets an object name or a dynamic
  property and this stylesheet decides how that looks;
- anything numeric (counters, sizes, throughput, hashes, paths) is rendered in
  a monospaced face so digits stop shifting while a transfer runs.

The palette follows the operating system by default; the user can force one
from the header.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str            # window
    surface: str       # cards
    sunk: str          # inputs, read-only areas
    border: str
    border_strong: str
    text: str
    muted: str
    faint: str
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_soft: str
    accent_on: str     # text over the accent
    warn: str
    warn_soft: str
    danger: str
    danger_soft: str
    dot_idle: str
    track: str
    scroll: str
    scroll_hover: str


LIGHT = Palette(
    name="light",
    bg="#f4f3f1", surface="#ffffff", sunk="#faf9f7",
    border="#e4e2dd", border_strong="#d2cfc8",
    text="#1b1a18", muted="#67645e", faint="#918d86",
    accent="#1d6b4f", accent_hover="#195c44", accent_pressed="#144a37",
    accent_soft="#e9f2ed", accent_on="#ffffff",
    warn="#8f6412", warn_soft="#fbf3e3",
    danger="#a03830", danger_soft="#fbeeec",
    dot_idle="#a5a19a", track="#e9e7e2",
    scroll="#d6d3cc", scroll_hover="#bab6ae",
)

DARK = Palette(
    name="dark",
    bg="#17181a", surface="#1f2124", sunk="#1a1c1e",
    border="#2c2f33", border_strong="#3a3e43",
    text="#e8e8e6", muted="#a7a9a6", faint="#7d807e",
    accent="#3f9d78", accent_hover="#48ac85", accent_pressed="#358a68",
    accent_soft="#1c2b25", accent_on="#0d1512",
    warn="#c9963f", warn_soft="#2b2519",
    danger="#d4756a", danger_soft="#2d1e1c",
    dot_idle="#6a6d6b", track="#26292c",
    scroll="#3a3e43", scroll_hover="#4c5157",
)

# --- Type scale -----------------------------------------------------------
SIZE_HERO = 17
SIZE_DISPLAY = 13
SIZE_TITLE = 11
SIZE_BODY = 10
SIZE_SMALL = 9

# --- Spacing --------------------------------------------------------------
GAP = 12
PAD = 14


def system_prefers_dark() -> bool:
    """Follow the operating system when it tells us (Qt 6.5+)."""
    try:
        hints = QApplication.styleHints()
        return hints.colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        return False


def palette_for(mode: str) -> Palette:
    """`mode` is "system", "light" or "dark"."""
    if mode == "dark":
        return DARK
    if mode == "light":
        return LIGHT
    return DARK if system_prefers_dark() else LIGHT


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


def mono_font(size: int = SIZE_BODY) -> QFont:
    f = QFont(mono_font_family(), size)
    f.setStyleHint(QFont.Monospace)
    return f


def stylesheet(p: Palette) -> str:
    """The whole application stylesheet for one palette."""
    ui, mono = ui_font_family(), mono_font_family()
    return f"""
/* ---------- base ---------- */
QWidget {{
    background: {p.bg};
    color: {p.text};
    font-family: "{ui}";
    font-size: {SIZE_BODY}pt;
}}
QMainWindow, QDialog {{ background: {p.bg}; }}
/* Labels must never paint the window colour on top of a card. */
QLabel, QCheckBox {{ background: transparent; }}
QWidget#Cell, QWidget#Plain {{ background: transparent; }}

QToolTip {{
    background: {p.surface};
    color: {p.text};
    border: 1px solid {p.border_strong};
    padding: 6px 9px;
}}

/* ---------- header ---------- */
QWidget#Header {{ background: {p.bg}; }}
QLabel#AppName {{
    font-size: {SIZE_HERO}pt;
    font-weight: 600;
    letter-spacing: -0.4px;
}}
QLabel#AppVersion {{
    color: {p.faint};
    font-family: "{mono}";
    font-size: {SIZE_SMALL}pt;
}}

/* ---------- device chip ---------- */
QFrame#Chip {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 16px;
    padding: 3px;
}}
QFrame#Chip[state="ready"]  {{ background: {p.accent_soft}; border-color: {p.accent}; }}
QFrame#Chip[state="warn"]   {{ background: {p.warn_soft};   border-color: {p.warn}; }}
QFrame#Chip[state="danger"] {{ background: {p.danger_soft}; border-color: {p.danger}; }}
QLabel#ChipDot {{
    min-width: 8px; max-width: 8px; min-height: 8px; max-height: 8px;
    border-radius: 4px; background: {p.dot_idle};
}}
QFrame#Chip[state="ready"]  QLabel#ChipDot {{ background: {p.accent}; }}
QFrame#Chip[state="warn"]   QLabel#ChipDot {{ background: {p.warn}; }}
QFrame#Chip[state="danger"] QLabel#ChipDot {{ background: {p.danger}; }}
QLabel#ChipText {{ font-weight: 600; }}
QLabel#ChipHint {{ color: {p.muted}; }}

/* ---------- cards ---------- */
QFrame#Card {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 12px;
}}
QFrame#Card[tone="accent"] {{ background: {p.accent_soft}; border-color: {p.accent}; }}
QFrame#Card[tone="danger"] {{ background: {p.danger_soft}; border-color: {p.danger}; }}

QLabel#CardTitle {{
    font-size: {SIZE_TITLE}pt;
    font-weight: 600;
    letter-spacing: -0.2px;
}}
QLabel#SectionLabel {{
    color: {p.faint};
    font-size: {SIZE_SMALL}pt;
    font-weight: 600;
    letter-spacing: 1.1px;
}}
QLabel#StepNumber {{
    color: {p.faint};
    font-family: "{mono}";
    font-size: {SIZE_SMALL}pt;
    font-weight: 600;
}}

/* ---------- buttons ---------- */
QPushButton, QToolButton {{
    background: {p.surface};
    color: {p.text};
    border: 1px solid {p.border_strong};
    border-radius: 8px;
    padding: 8px 14px;
    font-weight: 500;
}}
QPushButton:hover, QToolButton:hover {{
    background: {p.sunk}; border-color: {p.faint};
}}
QPushButton:pressed, QToolButton:pressed {{
    padding-top: 9px; padding-bottom: 7px;
}}
QPushButton:focus, QToolButton:focus {{ border: 1px solid {p.accent}; outline: none; }}
QPushButton:disabled, QToolButton:disabled {{
    color: {p.faint}; border-color: {p.border}; background: {p.surface};
}}
QToolButton::menu-indicator {{ image: none; width: 0; }}

QPushButton#Primary {{
    background: {p.accent}; color: {p.accent_on};
    border: 1px solid {p.accent}; font-weight: 600;
    padding: 9px 18px;
}}
QPushButton#Primary:hover   {{ background: {p.accent_hover}; border-color: {p.accent_hover}; }}
QPushButton#Primary:pressed {{ background: {p.accent_pressed}; }}
QPushButton#Primary:disabled {{
    background: {p.track}; color: {p.faint}; border-color: {p.border};
}}

QPushButton#Quiet, QToolButton#Quiet {{
    background: transparent; border-color: transparent; color: {p.muted};
}}
QPushButton#Quiet:hover, QToolButton#Quiet:hover {{
    background: {p.sunk}; border-color: {p.border}; color: {p.text};
}}
QPushButton#Quiet:disabled {{ color: {p.faint}; background: transparent;
                              border-color: transparent; }}

QPushButton#Stop {{ color: {p.danger}; border-color: {p.danger}; background: transparent; }}
QPushButton#Stop:hover {{ background: {p.danger_soft}; }}
QPushButton#Stop:disabled {{ color: {p.faint}; border-color: {p.border}; }}

QMenu {{
    background: {p.surface};
    border: 1px solid {p.border_strong};
    border-radius: 8px;
    padding: 6px;
}}
QMenu::item {{ padding: 7px 16px; border-radius: 5px; }}
QMenu::item:selected {{ background: {p.accent_soft}; color: {p.text}; }}
QMenu::item:disabled {{ color: {p.faint}; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: 5px 8px; }}

/* ---------- inputs ---------- */
QComboBox {{
    background: {p.sunk};
    border: 1px solid {p.border_strong};
    border-radius: 8px;
    padding: 7px 11px;
    min-width: 250px;
}}
QComboBox:hover {{ border-color: {p.faint}; }}
QComboBox:focus {{ border-color: {p.accent}; }}
QComboBox:disabled {{ color: {p.faint}; background: {p.bg}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {p.surface};
    border: 1px solid {p.border_strong};
    selection-background-color: {p.accent_soft};
    selection-color: {p.text};
    padding: 4px;
}}
QCheckBox {{ color: {p.text}; spacing: 8px; }}
QCheckBox:disabled {{ color: {p.faint}; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {p.border_strong};
    border-radius: 5px;
    background: {p.sunk};
}}
QCheckBox::indicator:checked {{ background: {p.accent}; border-color: {p.accent}; }}
QCheckBox::indicator:disabled {{ background: {p.bg}; }}

/* ---------- progress ---------- */
QProgressBar {{
    background: {p.track};
    border: none;
    border-radius: 4px;
    height: 8px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background: {p.accent}; border-radius: 4px; }}
QProgressBar[outcome="failed"]::chunk {{ background: {p.danger}; }}

/* ---------- labels ---------- */
QLabel#FieldLabel {{
    color: {p.faint};
    font-size: {SIZE_SMALL}pt;
    font-weight: 600;
    letter-spacing: 0.7px;
    min-height: 14px;
    padding-bottom: 2px;
}}
QLabel#Value {{ color: {p.text}; font-family: "{mono}"; }}
/* The stylesheet wins over setFont(), so anything that must be monospaced
   says so here rather than in the widget code. */
QLabel#PlanText {{ color: {p.text}; font-family: "{mono}"; }}
QLabel#ValueBig {{
    color: {p.text}; font-family: "{mono}";
    font-size: {SIZE_DISPLAY}pt; font-weight: 600;
}}
QLabel#ValueMuted {{ color: {p.muted}; font-family: "{mono}"; font-size: {SIZE_SMALL}pt; }}
QLabel#Phase {{ color: {p.text}; font-weight: 500; }}
QLabel#Hint {{ color: {p.muted}; }}
QLabel#LockNote {{ color: {p.faint}; font-size: {SIZE_SMALL}pt; }}
QLabel#EmptyTitle {{ color: {p.text}; font-size: {SIZE_TITLE}pt; font-weight: 600; }}
QLabel#EmptyBody {{ color: {p.muted}; }}
QLabel#NoticeText {{ color: {p.text}; }}

/* ---------- report ---------- */
QTextBrowser {{
    background: {p.sunk};
    border: 1px solid {p.border};
    border-radius: 10px;
    padding: 12px;
    selection-background-color: {p.accent_soft};
    selection-color: {p.text};
}}

/* ---------- scrollbars ---------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p.scroll}; border-radius: 5px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {p.scroll_hover}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p.scroll}; border-radius: 5px; min-width: 28px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---------- status bar ---------- */
QStatusBar {{ background: {p.bg}; color: {p.muted}; border-top: 1px solid {p.border}; }}
QStatusBar::item {{ border: none; }}

/* ---------- separators ---------- */
QFrame#VSep {{ background: {p.border}; max-width: 1px; min-width: 1px; border: none; }}
QFrame#HSep {{ background: {p.border}; max-height: 1px; min-height: 1px; border: none; }}
"""


def report_document_css(p: Palette) -> str:
    """Stylesheet applied to the Markdown rendered in the report panel."""
    ui, mono = ui_font_family(), mono_font_family()
    return f"""
    body {{ font-family: "{ui}"; color: {p.text}; font-size: {SIZE_BODY}pt; }}
    h1 {{ font-size: {SIZE_TITLE}pt; font-weight: 600; color: {p.text};
          margin: 2px 0 8px 0; }}
    h2 {{ font-size: {SIZE_BODY}pt; font-weight: 600; color: {p.muted};
          margin: 12px 0 4px 0; }}
    p, li {{ color: {p.text}; line-height: 145%; }}
    code, pre {{ font-family: "{mono}"; color: {p.muted}; }}
    a {{ color: {p.accent}; }}
    """
