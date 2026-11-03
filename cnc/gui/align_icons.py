"""Los iconos de alinear (dibujados aqui, sin archivos): dos barras que se alinean contra una raya.

Se pintan como vectores al tamano que se pida (no son imagenes), y el boton de la tira de medidas se pinta el mismo, sin pasar por
el estilo de Windows: asi salen del tamano que deben en cualquier pantalla y apagados salen grises sin que nadie los reescale.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QIconEngine, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QToolButton

from ..drawing_edit import ALIGN_LABELS as LABELS, ALIGN_SIDES
from .theme import ACCENT, LINE, MUTED, PANEL, TEXT
from ..i18n import _

GRID = 20          # el dibujo esta pensado en un cuadro de 20 x 20 y se estira al tamano que se pida
BUTTON = 30        # px: lado del boton de la tira
GLYPH = 22         # px: lado del dibujo dentro del boton
OFF_LINE, OFF_FILL = "#a9a79f", "#d6d4cd"      # los colores de un icono apagado
ORDER = ("izquierda", "centro_x", "derecha", "arriba", "centro_y", "abajo")          # como salen en la tira y en el menu
TIPS = {
    "izquierda": _("Alinear a la izquierda: las caras izquierdas de las formas elegidas pasan a la misma X (la de la que está más a la izquierda)."),
    "centro_x": _("Centrar en horizontal: los centros de las formas elegidas pasan a la misma X (el centro de todas juntas)."),
    "derecha": _("Alinear a la derecha: las caras derechas de las formas elegidas pasan a la misma X (la de la que está más a la derecha)."),
    "arriba": _("Alinear arriba: las caras de arriba de las formas elegidas pasan a la misma Y (la de la que está más arriba)."),
    "centro_y": _("Centrar en vertical: los centros de las formas elegidas pasan a la misma Y (el centro de todas juntas)."),
    "abajo": _("Alinear abajo: las caras de abajo de las formas elegidas pasan a la misma Y (la de la que está más abajo)."),
}
assert set(LABELS) == set(TIPS) == set(ORDER) == set(ALIGN_SIDES)


def _bars(side: str) -> tuple:
    """(raya, barra grande, barra pequena) en un cuadro de 20 x 20; cada una es (x, y, ancho, alto)."""
    if side == "izquierda":
        return (2, 1, 1.5, 18), (4.5, 3, 13, 5), (4.5, 12, 8, 5)
    if side == "derecha":
        return (16.5, 1, 1.5, 18), (2.5, 3, 13, 5), (7.5, 12, 8, 5)
    if side == "centro_x":
        return (9.25, 1, 1.5, 18), (3.5, 3, 13, 5), (6, 12, 8, 5)
    if side == "arriba":
        return (1, 2, 18, 1.5), (3, 4.5, 5, 13), (12, 4.5, 5, 8)
    if side == "abajo":
        return (1, 16.5, 18, 1.5), (3, 2.5, 5, 13), (12, 7.5, 5, 8)
    return (1, 9.25, 18, 1.5), (3, 3.5, 5, 13), (12, 6, 5, 8)           # centro_y


def paint_glyph(p: QPainter, rect: QRectF, side: str, enabled: bool = True) -> None:
    """Pinta el icono de `side` en el cuadrado mas grande y centrado que cabe en `rect` (apagado: en gris)."""
    edge = min(rect.width(), rect.height())
    box = QRectF(rect.center().x() - edge / 2, rect.center().y() - edge / 2, edge, edge)
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    p.translate(box.topLeft())
    p.scale(edge / GRID, edge / GRID)
    line, big, small = _bars(side)
    dark, fill = (QColor(TEXT), QColor(ACCENT)) if enabled else (QColor(OFF_LINE), QColor(OFF_FILL))
    p.setPen(Qt.NoPen)
    p.setBrush(dark)
    p.drawRect(QRectF(*line))
    p.setPen(QPen(dark, 1))
    p.setBrush(fill)
    for r in (big, small):
        p.drawRect(QRectF(r[0] + 0.5, r[1] + 0.5, r[2] - 1, r[3] - 1))
    p.restore()


class AlignIconEngine(QIconEngine):
    """El icono como vector: se pinta del tamano que se pide (con cualquier escala de pantalla) y apagado sale gris."""

    def __init__(self, side: str):
        super().__init__()
        self.side = side

    def paint(self, painter, rect, mode, state) -> None:
        paint_glyph(painter, QRectF(rect), self.side, enabled=mode != QIcon.Disabled)

    def pixmap(self, size, mode, state) -> QPixmap:
        pm = QPixmap(size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        self.paint(p, QRect(QPoint(0, 0), size), mode, state)
        p.end()
        return pm

    def clone(self) -> "AlignIconEngine":
        return AlignIconEngine(self.side)


def align_icon(side: str) -> QIcon:
    return QIcon(AlignIconEngine(side))


class AlignButton(QToolButton):
    """El boton de alinear de la tira: lleva la accion (la misma del menu) y se pinta el solo, a 30 x 30 px con el dibujo de 22."""

    def __init__(self, action, parent=None):
        super().__init__(parent)
        self.side = action.data()
        self.setDefaultAction(action)
        self.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.setFixedSize(BUTTON, BUTTON)

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        on = self.isEnabled()
        if on and self.isDown():
            back, edge = QColor("#c5d6f5"), QColor(ACCENT)
        elif on and self.underMouse():
            back, edge = QColor("#dbe6fa"), QColor(ACCENT)
        else:
            back, edge = QColor(PANEL), QColor(LINE if not on else MUTED)
        p.setPen(QPen(edge, 1))
        p.setBrush(back)
        p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 5, 5)
        if self.hasFocus():
            p.setPen(QPen(QColor(ACCENT), 1, Qt.DotLine))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(2.5, 2.5, self.width() - 5, self.height() - 5), 4, 4)
        paint_glyph(p, QRectF((self.width() - GLYPH) / 2, (self.height() - GLYPH) / 2, GLYPH, GLYPH), self.side, on)
        p.end()
