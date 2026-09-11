from __future__ import annotations

# Compact production-like dark theme. No external font or icon assets required.
COLORS = {
    "bg": "#0B0F14",
    "sidebar": "#0D1219",
    "panel": "#111720",
    "panel2": "#151D27",
    "panel3": "#0F151D",
    "border": "#263140",
    "text": "#F3F6FA",
    "muted": "#8D9AAA",
    "accent": "#3B82F6",
    "accent_hover": "#2563EB",
    "accent_soft": "#162A47",
    "green": "#22C55E",
    "green_soft": "#12301F",
    "amber": "#F59E0B",
    "amber_soft": "#35240C",
    "red": "#EF4444",
    "red_soft": "#361718",
}

APP_QSS = f"""
QMainWindow, QWidget {{
    background: {COLORS['bg']};
    color: {COLORS['text']};
    font-family: "Leelawadee UI", "Segoe UI";
    font-size: 10pt;
}}
QToolTip {{
    color: {COLORS['text']};
    background: {COLORS['panel2']};
    border: 1px solid {COLORS['border']};
    padding: 5px 7px;
}}
QFrame#sidebar {{ background: {COLORS['sidebar']}; border-right: 1px solid {COLORS['border']}; }}
QFrame#topbar {{ background: {COLORS['bg']}; border-bottom: 1px solid {COLORS['border']}; }}
QFrame#card, QFrame#section, QFrame#console {{
    background: {COLORS['panel']};
    border: 1px solid {COLORS['border']};
    border-radius: 10px;
}}
QFrame#metricCard {{
    background: {COLORS['panel2']};
    border: 1px solid {COLORS['border']};
    border-radius: 9px;
}}
QLabel#brand {{ color: {COLORS['text']}; font-size: 16pt; font-weight: 700; }}
QLabel#brandSub {{ color: {COLORS['accent']}; font-size: 8pt; font-weight: 700; }}
QLabel#pageTitle {{ color: {COLORS['text']}; font-size: 17pt; font-weight: 700; }}
QLabel#pageSub {{ color: {COLORS['muted']}; font-size: 9pt; }}
QLabel#sectionTitle {{ color: {COLORS['text']}; font-size: 11pt; font-weight: 650; }}
QLabel#fieldLabel {{ color: {COLORS['muted']}; font-size: 8.5pt; font-weight: 550; }}
QLabel#metricValue {{ color: {COLORS['text']}; font-size: 18pt; font-weight: 750; }}
QLabel#metricLabel {{ color: {COLORS['muted']}; font-size: 8.5pt; }}
QLabel#hint {{ color: {COLORS['muted']}; font-size: 8.5pt; }}
QLabel#successText {{ color: {COLORS['green']}; font-weight: 650; }}
QLabel#warningText {{ color: {COLORS['amber']}; font-weight: 650; }}
QLabel#errorText {{ color: {COLORS['red']}; font-weight: 650; }}
QPushButton {{
    background: {COLORS['panel2']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    border-radius: 7px;
    padding: 7px 12px;
    min-height: 18px;
}}
QPushButton:hover {{ background: #1A2430; border-color: #344255; }}
QPushButton:pressed {{ background: #0F151D; }}
QPushButton:disabled {{ color: #596574; background: #11161D; border-color: #1B2531; }}
QPushButton#primary {{ background: {COLORS['accent']}; border-color: {COLORS['accent']}; font-weight: 700; }}
QPushButton#primary:hover {{ background: {COLORS['accent_hover']}; border-color: {COLORS['accent_hover']}; }}
QPushButton#danger {{ background: {COLORS['red_soft']}; color: #FFB4B4; border-color: #5D2628; }}
QPushButton#navButton {{
    background: transparent;
    border: 0;
    border-radius: 7px;
    color: {COLORS['muted']};
    text-align: left;
    padding: 9px 12px;
    font-weight: 550;
}}
QPushButton#navButton:hover {{ background: #141C26; color: {COLORS['text']}; }}
QPushButton#navButton:checked {{ background: {COLORS['accent_soft']}; color: #CFE3FF; font-weight: 700; }}
QLineEdit, QSpinBox, QComboBox {{
    background: {COLORS['panel3']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    border-radius: 7px;
    padding: 7px 9px;
    selection-background-color: {COLORS['accent']};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border: 1px solid {COLORS['accent']}; }}
QComboBox::drop-down {{ border: 0; width: 24px; }}
QComboBox QAbstractItemView {{ background: {COLORS['panel2']}; color: {COLORS['text']}; border: 1px solid {COLORS['border']}; selection-background-color: {COLORS['accent_soft']}; }}
QCheckBox {{ spacing: 7px; color: {COLORS['text']}; }}
QTableView {{
    background: {COLORS['panel']};
    alternate-background-color: #0F151D;
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    gridline-color: transparent;
    selection-background-color: {COLORS['accent_soft']};
    selection-color: {COLORS['text']};
}}
QHeaderView::section {{
    background: {COLORS['panel2']};
    color: {COLORS['muted']};
    border: 0;
    border-bottom: 1px solid {COLORS['border']};
    padding: 7px 8px;
    font-size: 8.5pt;
    font-weight: 650;
}}
QTableCornerButton::section {{ background: {COLORS['panel2']}; border: 0; }}
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #344153; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #344153; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QProgressBar {{
    background: {COLORS['panel3']};
    border: 0;
    border-radius: 4px;
    height: 8px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background: {COLORS['accent']}; border-radius: 4px; }}
QPlainTextEdit {{
    background: #080C11;
    color: #B8C4D1;
    border: 0;
    border-radius: 7px;
    padding: 7px;
    font-family: "Cascadia Mono", "Consolas";
    font-size: 8.5pt;
}}
QSplitter::handle {{ background: transparent; height: 5px; }}
QDialog {{ background: {COLORS['bg']}; }}
"""
