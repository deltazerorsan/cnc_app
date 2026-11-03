"""El selector de punto de referencia: una caja con nueve puntos (esquinas, mitades de lado y centro) para elegir a que punto de la
caja que rodea a la figura se refieren las medidas X e Y. Como el cuadro de posicion de los programas de dibujo."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from .theme import ACCENT, LINE, MUTED, PANEL, TEXT
from ..i18n import _

SIZE = 40                         # px: lado del selector
FRACTIONS = (0.0, 0.5, 1.0)       # donde caen las columnas (de izquierda a derecha) y las filas (de abajo a arriba)
POINT_NAMES = {
    (0.0, 0.0): _("la esquina inferior izquierda"), (0.5, 0.0): _("la mitad del lado inferior"),
    (1.0, 0.0): _("la esquina inferior derecha"), (0.0, 0.5): _("la mitad del lado izquierdo"), (0.5, 0.5): _("el centro"),
    (1.0, 0.5): _("la mitad del lado derecho"), (0.0, 1.0): _("la esquina superior izquierda"),
    (0.5, 1.0): _("la mitad del lado superior"), (1.0, 1.0): _("la esquina superior derecha"),
}


def point_name(fx: float, fy: float) -> str:
    return POINT_NAMES.get((fx, fy), _("un punto de la caja"))


class AnchorPicker(QWidget):
    """Nueve puntos sobre un cuadro redondeado; el elegido va relleno. `anchor()` da (fx, fy): (0, 0) es la esquina inferior izquierda
    de la caja de la figura, (1, 1) la superior derecha. Se elige con el raton o, con el foco, con las flechas."""

    anchorChanged = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._col, self._row = 0, 0                      # columna 0..2 (izquierda a derecha) y fila 0..2 (abajo a arriba)
        self._hover = None
        self.setFixedSize(SIZE, SIZE)
        self.setFocusPolicy(Qt.StrongFocus)              # asi, al pulsarlo, una casilla a medio escribir se entrega antes
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setAccessibleName(_("Punto de referencia de las medidas"))
        self._retip()

    # --- el punto elegido -------------------------------------------------------------------------------------------
    def anchor(self) -> tuple:
        return FRACTIONS[self._col], FRACTIONS[self._row]

    def set_anchor(self, fx: float, fy: float) -> None:
        """Pone el punto (sin avisar: lo usa quien ya lo sabe)."""
        col, row = FRACTIONS.index(fx), FRACTIONS.index(fy)
        if (col, row) != (self._col, self._row):
            self._col, self._row = col, row
            self._retip()
            self.update()

    def _choose(self, col: int, row: int) -> None:
        col, row = max(0, min(2, col)), max(0, min(2, row))
        if (col, row) == (self._col, self._row):
            return
        self._col, self._row = col, row
        self._retip()
        self.update()
        self.anchorChanged.emit(*self.anchor())

    def _retip(self) -> None:
        self.setToolTip(_("Punto de la forma al que se refieren X e Y: {point_name}. Pulsa otro punto para cambiarlo (la "
                          "forma se mide y se coloca por ese punto).", point_name=point_name(*self.anchor())))

    # --- geometria --------------------------------------------------------------------------------------------------
    def _cell_at(self, pos: QPointF):
        col = max(0, min(2, int(pos.x() * 3 / self.width())))
        row = max(0, min(2, 2 - int(pos.y() * 3 / self.height())))          # arriba del selector = arriba de la figura
        return col, row

    def _center_of(self, col: int, row: int) -> QPointF:
        pad = 9.0
        span = SIZE - 2 * pad
        return QPointF(pad + col * span / 2, pad + (2 - row) * span / 2)

    # --- raton y teclado --------------------------------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:            # (apagado, ni raton ni teclado llegan hasta aqui)
            self._choose(*self._cell_at(event.position()))
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        cell = self._cell_at(event.position())
        if cell != self._hover:
            self._hover = cell
            self.update()

    def leaveEvent(self, event) -> None:
        self._hover = None
        self.update()

    def keyPressEvent(self, event) -> None:
        step = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0), Qt.Key_Up: (0, 1), Qt.Key_Down: (0, -1)}.get(event.key())
        if step is None:
            super().keyPressEvent(event)
            return
        self._choose(self._col + step[0], self._row + step[1])

    # --- dibujo -----------------------------------------------------------------------------------------------------
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        on = self.isEnabled()
        edge = QColor(ACCENT if on else LINE)
        p.setPen(QPen(edge, 2.0))
        p.setBrush(QColor(PANEL))
        p.drawRoundedRect(QRectF(2, 2, SIZE - 4, SIZE - 4), 8, 8)
        if self.hasFocus():
            p.setPen(QPen(QColor(ACCENT), 1, Qt.DotLine))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(0.5, 0.5, SIZE - 1, SIZE - 1), 9, 9)
        for row in range(3):
            for col in range(3):
                c = self._center_of(col, row)
                chosen = (col, row) == (self._col, self._row)
                if not on:
                    p.setPen(QPen(QColor(LINE), 1))
                    p.setBrush(Qt.NoBrush)
                    p.drawEllipse(c, 3.0, 3.0)
                    continue
                if chosen:
                    p.setPen(QPen(QColor(TEXT), 1))
                    p.setBrush(QColor(ACCENT))
                    p.drawEllipse(c, 4.4, 4.4)
                else:
                    hovered = (col, row) == self._hover
                    p.setPen(QPen(QColor(TEXT if hovered else MUTED), 1))
                    p.setBrush(QColor("#dbe6fa") if hovered else Qt.NoBrush)
                    p.drawEllipse(c, 3.2, 3.2)
        p.end()
