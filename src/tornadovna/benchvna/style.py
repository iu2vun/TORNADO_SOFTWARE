"""Stylesheet QSS stile Rohde & Schwarz ZNB."""

QSS = """
/* base */
QMainWindow, QWidget {
    background: #1a1c20;
    color: #e0e2e6;
    font-family: 'Segoe UI', 'DejaVu Sans', sans-serif;
    font-size: 13px;
}

/* header sottile */
QFrame#header {
    background: #20232a;
    border-bottom: 1px solid #2e323b;
    min-height: 32px;
}
QLabel#brand {
    font-size: 14px; font-weight: 700;
    color: #c8cdd6; letter-spacing: 1px;
}
QLabel#idn { color: #7a8090; font-size: 12px; }

/* pannello softkey */
QFrame#softpanel {
    background: #20232a;
    border-left: 1px solid #2e323b;
}
QLabel#softtitle {
    color: #7a8090; font-size: 10px; letter-spacing: 2px;
    padding: 3px 0; border-bottom: 1px solid #2e323b;
}
QPushButton#softkey {
    background: #2c3040;
    border: 1px solid #3a3f50;
    border-radius: 3px;
    color: #cdd0d8;
    text-align: left;
    padding: 0 10px;
    font-size: 13px;
}
QPushButton#softkey:hover   { background: #363b4e; }
QPushButton#softkey:pressed { background: #424869; }
QPushButton#softkey[nav="true"] {
    background: #22252e;
    color: #6a7080;
    font-style: italic;
}

/* barra entry numerica */
QFrame#entry {
    background: #22252e;
    border-top: 1px solid #3a3f50;
    min-height: 38px;
}
QLineEdit {
    background: #111318;
    border: 1px solid #3a3f50;
    border-radius: 3px;
    color: #ffffff;
    font-size: 15px;
    font-family: 'Consolas', 'DejaVu Sans Mono', monospace;
    padding: 4px 8px;
    selection-background-color: #3a5080;
}
QPushButton#unitbtn {
    background: #2c3040;
    border: 1px solid #3a3f50;
    border-radius: 3px;
    padding: 5px 4px;
    min-width: 52px;
}
QPushButton#unitbtn:hover { background: #363b4e; }

/* overlay trace-info / marker */
QLabel#overlay {
    background: transparent;
    font-family: 'Consolas', 'DejaVu Sans Mono', monospace;
    font-size: 12px;
}

/* channel bar */
QLabel#chbar {
    background: #20232a;
    border-top: 1px solid #2e323b;
    font-family: 'Consolas', 'DejaVu Sans Mono', monospace;
    font-size: 12px;
    padding: 4px 12px;
    color: #9aa0b0;
}

/* status bar */
QStatusBar {
    background: #1a1c20;
    color: #6a7080;
    font-size: 11px;
}

/* dialogs / extra widgets */
QDialog, QFrame#calframe {
    background: #20232a;
}
QTableWidget {
    background: #1a1c20;
    gridline-color: #2e323b;
    color: #c8cdd6;
}
QHeaderView::section {
    background: #22252e;
    border: 0;
    padding: 4px;
    color: #8090a0;
}
QComboBox {
    background: #2c3040;
    border: 1px solid #3a3f50;
    border-radius: 3px;
    padding: 4px 8px;
}
QComboBox::drop-down { border: 0; }
QComboBox QAbstractItemView {
    background: #22252e;
    selection-background-color: #3a5080;
}
QPushButton {
    background: #2c3040;
    border: 1px solid #3a3f50;
    border-radius: 4px;
    padding: 6px 14px;
}
QPushButton:hover   { background: #363b4e; }
QPushButton:pressed { background: #424868; }

/* Diagram widget (finestra-diagramma) */
#diagram {
    border: 1px solid #2a2f3a;
    background: #0a0b0d;
}
#diagram[active="true"] {
    border: 1px solid #3a6ea5;
}
#traceheader {
    padding: 1px 4px;
    font-size: 11px;
}
"""
