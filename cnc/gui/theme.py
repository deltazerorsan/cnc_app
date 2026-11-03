"""Colores, fuentes y estilo de la ventana."""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication
from ..i18n import _

ACCENT = "#2f6fdb"
BG = "#f4f4f1"
PANEL = "#fbfbf9"
LINE = "#d9d8d2"
TEXT = "#23221f"
MUTED = "#74726b"
OK = "#2b8a57"
WARN = "#b7791f"
ERROR = "#c0392b"

# lo que se pinta en la vista de maquina, por tipo de operacion (los mismos nombres que cnc.preview)
KIND_COLORS = {
    "hueco": "#d6432f",
    "pieza": "#2f6fdb",
    "linea": "#7a4fc4",
    "vaciado": "#e08a1e",
    "taladro": "#2b9a62",
}
KIND_LABELS = {"hueco": _("Hueco"), "pieza": _("Pieza"), "linea": _("Línea"), "vaciado": _("Vaciado"), "taladro": _("Taladro")}
RAPID = "#b5b3ab"
BRIDGE = "#c27a00"            # los puentes: tramos que la fresa recorre levantada
BOARD_FILL = "#ecdfc4"
MACHINE = "#3f6fa8"           # el contorno punteado del recorrido de la maquina
SHAPE_NO_COLOR = "#8a8880"

STYLE = f"""
QWidget {{ color: {TEXT}; }}
QMainWindow, QDockWidget, QTabWidget::pane {{ background: {BG}; }}
QGroupBox {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 6px; margin-top: 14px; padding: 8px 8px 6px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {MUTED}; font-weight: 600; }}
QTableWidget {{ background: {PANEL}; gridline-color: {LINE}; border: 1px solid {LINE}; selection-background-color: #dce7fb; selection-color: {TEXT}; }}
QHeaderView::section {{ background: {BG}; border: none; border-bottom: 1px solid {LINE}; padding: 4px 6px; color: {MUTED}; font-weight: 600; }}
QListWidget {{ background: {PANEL}; border: 1px solid {LINE}; }}
QListWidget::item {{ padding: 4px 6px; }}
QListWidget::item:selected {{ background: #dce7fb; color: {TEXT}; }}
QPlainTextEdit, QLineEdit, QDoubleSpinBox, QComboBox {{ background: white; border: 1px solid {LINE}; border-radius: 4px; padding: 2px 4px; }}
QPlainTextEdit:focus, QLineEdit:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QToolBar {{ background: {BG}; border: none; spacing: 6px; padding: 4px; }}
QStatusBar {{ background: {BG}; color: {MUTED}; }}
QPushButton, QToolButton {{ background: white; border: 1px solid {LINE}; border-radius: 4px; padding: 4px 10px; }}
QPushButton:hover, QToolButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {MUTED}; background: {BG}; }}
QPushButton#primary {{ background: {ACCENT}; color: white; border-color: {ACCENT}; font-weight: 600; }}
QPushButton#primary:disabled {{ background: #a9bce0; border-color: #a9bce0; color: white; }}
QToolButton:checked {{ background: #dce7fb; border-color: {ACCENT}; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#problem {{ color: {ERROR}; }}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(BG))
    pal.setColor(QPalette.Base, QColor("white"))
    pal.setColor(QPalette.AlternateBase, QColor(PANEL))
    pal.setColor(QPalette.Text, QColor(TEXT))
    pal.setColor(QPalette.WindowText, QColor(TEXT))
    pal.setColor(QPalette.Button, QColor("white"))
    pal.setColor(QPalette.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.HighlightedText, QColor("white"))
    app.setPalette(pal)
    font = app.font()
    font.setPointSizeF(max(font.pointSizeF(), 9.5))
    app.setFont(font)
    app.setStyleSheet(STYLE)


def mono_font() -> QFont:
    f = QFont("Consolas")
    f.setStyleHint(QFont.Monospace)
    f.setPointSizeF(9.0)
    return f
