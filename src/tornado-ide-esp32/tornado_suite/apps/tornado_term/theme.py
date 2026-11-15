"""Tema scuro dell'applicazione, coordinato coi colori del terminale.

Un solo foglio di stile QSS applicato a livello di QApplication. I colori
richiamano lo sfondo del terminale (#101010) per continuita' visiva tra
cornice e area di rendering.
"""

BG = "#141414"
BG_RAISED = "#1e1e1e"
BG_INPUT = "#0d0d0d"
BORDER = "#2a2a2a"
FG = "#d8d8d8"
FG_DIM = "#8a8a8a"
ACCENT = "#19cb00"
ACCENT_DIM = "#127e00"

APP_STYLESHEET = f"""
QMainWindow, QWidget {{
    background: {BG};
    color: {FG};
    font-family: "Segoe UI", "Cantarell", sans-serif;
    font-size: 13px;
}}

QToolBar {{
    background: {BG_RAISED};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 6px 8px;
    spacing: 8px;
}}

QLabel#tbLabel {{
    color: {FG_DIM};
    padding-left: 4px;
}}

QComboBox {{
    background: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px 8px;
    min-height: 20px;
    color: {FG};
}}
QComboBox:hover {{ border-color: {ACCENT_DIM}; }}
QComboBox:disabled {{ color: {FG_DIM}; background: {BG}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background: {BG_RAISED};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT_DIM};
    color: {FG};
    outline: none;
}}

QPushButton {{
    background: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 5px 16px;
    color: {FG};
    font-weight: 600;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {BG}; }}
QPushButton#connectBtn[connected="true"] {{
    background: {ACCENT_DIM};
    border-color: {ACCENT};
    color: #ffffff;
}}

QStatusBar {{
    background: {BG_RAISED};
    border-top: 1px solid {BORDER};
    color: {FG_DIM};
}}
QStatusBar::item {{ border: none; }}

QCheckBox {{
    color: {FG};
    spacing: 6px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {BORDER};
    border-radius: 3px;
    background: {BG_INPUT};
}}
QCheckBox::indicator:hover {{ border-color: {ACCENT_DIM}; }}
QCheckBox::indicator:checked {{
    background: {ACCENT_DIM};
    border-color: {ACCENT};
}}

QScrollBar:vertical {{
    background: {BG};
    width: 12px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 5px;
    min-height: 28px;
    margin: 2px;
}}
QScrollBar::handle:vertical:hover {{ background: {FG_DIM}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
"""
