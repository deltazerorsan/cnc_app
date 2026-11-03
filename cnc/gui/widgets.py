"""Controles pequenos que usan varios paneles."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRegularExpression, QSize, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QRegularExpressionValidator
from PySide6.QtWidgets import QLineEdit, QTabWidget


class OptionalNumber(QLineEdit):
    """Numero que puede estar vacio (vacio = "el de la ficha" o "el general"). Admite coma o punto decimal.
    El texto gris (placeholder) dice que valor se usara si se deja vacio."""

    valueChanged = Signal()

    def __init__(self, placeholder: str = "", parent=None, negative: bool = False):
        super().__init__(parent)
        self.setAcceptDrops(False)           # un archivo soltado aqui se escribiria como texto: que lo coja la ventana
        sign = "-?" if negative else ""
        self.setValidator(QRegularExpressionValidator(QRegularExpression(rf"^{sign}\d{{0,6}}([.,]\d{{0,4}})?$"), self))
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(False)
        self.textEdited.connect(lambda _t: self.valueChanged.emit())

    def value(self) -> Optional[float]:
        text = self.text().strip().replace(",", ".")
        if text in ("", "-", ".", "-."):
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def setValue(self, value: Optional[float]) -> None:
        self.blockSignals(True)
        self.setText("" if value is None else f"{value:g}")
        self.blockSignals(False)

    def setHint(self, value: Optional[float], suffix: str = "") -> None:
        """Pone en gris el valor que se usara si el campo queda vacio."""
        self.setPlaceholderText("" if value is None else f"{value:g}{suffix}")


def color_icon(color: Optional[str], size: int = 14) -> QIcon:
    """Cuadradito del color de un trazo o relleno (gris con raya si no hay color)."""
    pm = QPixmap(size, size)
    pm.fill(QColor(color) if color else QColor("#ffffff"))
    p = QPainter(pm)
    p.setPen(QPen(QColor("#9a988f"), 1))
    p.drawRect(0, 0, size - 1, size - 1)
    if not color:
        p.drawLine(0, size - 1, size - 1, 0)
    p.end()
    return QIcon(pm)


class CurrentPageTabs(QTabWidget):
    """Pestañas cuya altura minima es la de la pestaña que se ve, no la de la mas alta de todas: con las otras (Sujecion
    pide unos 275 px) el panel de abajo no podria ser mas bajo ni mostrando la tabla de operaciones."""

    def minimumSizeHint(self) -> QSize:
        page = self.currentWidget()
        if page is None:
            return super().minimumSizeHint()
        bar = self.tabBar().sizeHint()
        need = page.minimumSizeHint().expandedTo(page.minimumSize())
        return QSize(max(bar.width(), need.width()), bar.height() + need.height())
