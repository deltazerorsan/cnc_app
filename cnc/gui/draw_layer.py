"""Capa del lienzo para dibujar y mover con el raton.

Traduce los eventos del raton a puntos del dibujo (pegandolos al iman), pinta la vista previa, el marcador del iman y las
medidas junto al cursor, y entrega la forma o el movimiento terminados. Toda la logica (iman, restricciones, que forma
sale de cada herramienta, medidas) esta en `cnc.draw_tools`, que no usa Qt y tiene sus propias pruebas.
"""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QObject, QPoint, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainterPath
from PySide6.QtWidgets import QGraphicsPathItem, QGraphicsView, QLabel

from .. import draw_tools as dt
from .. import drawing_edit as de
from . import theme
from .bridge_layer import BridgeTool
from .canvas import CLICK_PIXELS, _pen, path_from_geom, polyline_path
from ..i18n import _

HUD_OFFSET = QPoint(16, 18)           # donde queda la etiqueta de medidas respecto al cursor
Z_GHOST, Z_PREVIEW, Z_MARKER = 39, 40, 50


def marker_path(kind: str) -> QPainterPath:
    """La forma del marcador de iman: un cuadrado en las esquinas, un circulo en centros y un rombo en puntos medios."""
    p = QPainterPath()
    if kind in ("esquina", "inicio"):
        p.addRect(-6, -6, 12, 12)
    elif kind in ("centro", "caja", "cero"):
        p.addEllipse(-6, -6, 12, 12)
    else:
        p.moveTo(0, -7), p.lineTo(7, 0), p.lineTo(0, 7), p.lineTo(-7, 0), p.closeSubpath()
    return p


class ToolLayer(QObject):
    finished = Signal(object)         # draw_tools.Done: hay una forma nueva o un movimiento que hacer
    status = Signal(str)              # una ayuda o un motivo para la barra de estado
    changed = Signal(str)             # la herramienta activa ahora

    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvas = canvas
        self.name = "elegir"
        self.options = dt.Options()
        self.stroke_of: Callable[[], Optional[str]] = lambda: "#000000"    # color del trazo de lo que se dibuja
        self.snaps = dt.SnapMap()
        self.session: Optional[dt.Session] = None
        self.shapes: list = []
        self.selected: tuple = ()
        self.spot: Optional[dt.Spot] = None        # donde cae el cursor ahora
        self.problem = ""                          # por que no se pudo dar el ultimo punto
        self.space = False                         # Espacio pulsado: arrastrar desplaza la vista
        self._stale = True                         # las formas han cambiado y falta poner al dia el mapa de iman
        self._pos = QPoint(0, 0)
        self._press = None                         # (posicion en pixeles, punto) del boton izquierdo pulsado
        self._pan = None
        self._box: Optional[tuple] = None
        self._items: dict = {}
        self.bridge = BridgeTool(self)                           # la herramienta Puente (sus gestos y lo que pinta)
        self.hud = QLabel(canvas.viewport())
        self.hud.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hud.setStyleSheet(f"QLabel {{ background: {theme.PANEL}; color: {theme.TEXT}; border: 1px solid {theme.LINE};"
                               " border-radius: 3px; padding: 2px 6px; }")
        self.hud.hide()

    # --- estado ----------------------------------------------------------------------------------------------------
    @property
    def active(self) -> bool:
        return self.session is not None and self.canvas.mode == "drawing"

    @property
    def busy(self) -> bool:
        return self.session is not None and self.session.busy

    def set_tool(self, name: str) -> bool:
        """Cambia de herramienta; False si no se puede (Mover sin nada elegido, o no se ve el dibujo)."""
        if name not in dt.TOOLS:
            raise ValueError(_("herramienta desconocida: {name!r}", name=name))
        if name != "elegir":
            if self.canvas.mode != "drawing":
                return False
            if name == "mover" and not self.selected:
                self.status.emit(_("Mover: elige primero lo que quieres mover (clic, Ctrl+clic o recuadro con Mayús)."))
                return False
        if self.name == "puente" and name != "puente":
            self.bridge.reset()
        self.name = name
        self.problem = ""
        self.spot = None
        if name == "elegir":
            self.session = None
            self.canvas.setDragMode(QGraphicsView.ScrollHandDrag)
            self.canvas.viewport().setCursor(Qt.ArrowCursor)
        else:
            self.session = dt.Session(name, self.options, self.stroke_of(), self.selected)
            self.canvas.setDragMode(QGraphicsView.NoDrag)
            self.canvas.viewport().setCursor(Qt.CrossCursor)
            self.status.emit(self.session.hint())
        self._press = self._pan = None
        self._redraw()
        self.changed.emit(name)
        return True

    def reset(self) -> None:
        """Vuelve a Elegir (al dejar de verse el dibujo)."""
        if self.name != "elegir":
            self.set_tool("elegir")

    def configure(self, options: dt.Options) -> None:
        self.options = options
        self.canvas.viewport().update()                  # la rejilla de la vista sigue la cuadricula del raton
        if self.session is not None:
            self.session.options = options
            self._redraw()

    def escape(self) -> bool:
        """Esc: cancela lo que se esta dibujando y, si no hay nada, deja la herramienta. False si no hacia falta nada."""
        if self.cancel():
            return True
        if self.name != "elegir":
            self.set_tool("elegir")
            return True
        return False

    def cancel(self) -> bool:
        if self.name == "puente":
            return self.bridge.cancel()
        s = self.session
        if s is None or not s.busy:
            return False
        s.cancel()
        self.problem = ""
        self._redraw()
        self.status.emit(s.hint())
        return True

    # --- el dibujo ---------------------------------------------------------------------------------------------------
    def scene_cleared(self) -> None:
        """La escena se ha vaciado: los items de la vista previa ya no existen."""
        self._items = {}
        self.bridge.scene_cleared()

    def set_bridges(self, bridges) -> None:
        """Los puentes que tiene cada forma ahora (forma -> (puentes, a mano))."""
        self.bridge.bridges = dict(bridges or {})
        self.bridge.set_shapes()
        if self.name == "puente":
            self.bridge.redraw()

    def set_shapes(self, shapes, selected) -> None:
        """Lo que el lienzo muestra ahora: las formas y los indices elegidos."""
        self.shapes = shapes
        self.selected = tuple(sorted(i for i in selected if 0 <= i < len(shapes)))
        self._stale, self._box = True, None
        s = self.session
        if s is not None and s.tool == "mover":
            if not self.selected:
                self.set_tool("elegir")
                self.status.emit(_("Mover: ya no hay nada elegido."))
                return
            if s.busy and s.selection != self.selected:
                s.cancel()
                self.status.emit(_("Mover: lo elegido ha cambiado; empieza otra vez."))
            s.selection = self.selected
        self._redraw()

    def _selection_box(self) -> tuple:
        if self._box is None:
            bs = [self.shapes[i].shape.bounds for i in self.selected]
            self._box = (min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs))
        return self._box

    # --- del raton al dibujo --------------------------------------------------------------------------------------------
    def _spot(self, event) -> dt.Spot:
        s = self.session
        if self._stale:
            self.snaps.update(self.shapes)
            self._stale = False
        p = self.canvas.mapToScene(event.position().toPoint())
        radius = dt.SNAP_PIXELS / max(self.canvas.scale_factor, 1e-9)
        mods = event.modifiers()
        extra = s.extras()
        moving = s.tool == "mover"
        if moving and not s.busy:
            extra += dt.box_points(self._selection_box())              # de donde agarrar lo que se mueve
        # Varias formas elegidas se agarran por su rectangulo circunscrito: sus esquinas y mitades, no los puntos de cada forma
        # (la esquina de la mas a la izquierda no es la de la caja si esa no es tambien la mas baja). Al soltar, lo que se
        # mueve no cuenta como destino.
        own = moving and (s.busy or len(s.selection) > 1)
        return dt.resolve(s.tool, (p.x(), p.y()), s.origin, self.options, self.snaps, radius,
                          shift=bool(mods & Qt.ShiftModifier), ctrl=bool(mods & Qt.ControlModifier),
                          exclude=s.selection if own else (), extra=extra)

    def press(self, event) -> bool:
        if not self.active:
            return False
        self.canvas.setFocus()
        button = event.button()
        if button == Qt.MiddleButton or (button == Qt.LeftButton and self.space):
            self._pan = event.position()
            return True
        if button == Qt.RightButton:
            self._right_click()
            return True
        if button == Qt.LeftButton and self.name == "puente":
            self._pos = event.position().toPoint()
            self.bridge.press(event)
            return True
        if button == Qt.LeftButton:
            self.problem = ""
            self._pos = event.position().toPoint()
            self._press = (event.position(), not self.session.busy)       # (donde, si es el primer punto: puede ser el inicio de un arrastre)
            self.spot = self._spot(event)
            self._give(self.spot)
            return True
        return False

    def move(self, event) -> bool:
        if not self.active:
            return False
        if self._pan is not None:
            d = event.position() - self._pan
            self._pan = event.position()
            hbar, vbar = self.canvas.horizontalScrollBar(), self.canvas.verticalScrollBar()
            hbar.setValue(hbar.value() - round(d.x()))
            vbar.setValue(vbar.value() - round(d.y()))
            return True
        self._pos = event.position().toPoint()
        if self.name == "puente":
            self.bridge.move(event)
            return True
        self.spot = self._spot(event)
        self._redraw()
        return True

    def release(self, event) -> bool:
        if not self.active:
            return False
        if self._pan is not None:
            self._pan = None
            return True
        if self.name == "puente":
            if event.button() == Qt.LeftButton:
                self.bridge.release(event)
            return True
        if event.button() != Qt.LeftButton or self._press is None:
            return True
        (start_pos, first), self._press = self._press, None
        if first and (event.position() - start_pos).manhattanLength() >= CLICK_PIXELS:
            self._pos = event.position().toPoint()                     # se arrastro desde el primer punto: soltar da el siguiente
            self.spot = self._spot(event)
            self._give(self.spot)
        return True

    def double_click(self, event) -> bool:
        if not self.active:
            return False
        self._press = None                                             # el soltar que viene detras no es un clic
        if self.name == "puente":
            return True
        if self.session.tool == "linea" and self.session.busy:
            self._finish()
        return True

    def _right_click(self) -> None:
        s = self.session
        if self.name == "puente":
            if not self.bridge.cancel():
                self.set_tool("elegir")
        elif s.tool == "linea" and len(s.points) >= 2:
            self._finish()
        elif not self.cancel():
            self.set_tool("elegir")

    def leave(self) -> None:
        self.spot = None
        if self.name == "puente":
            self.bridge._hover = None
            self.bridge.redraw()
            return
        self._redraw()

    # --- teclado ---------------------------------------------------------------------------------------------------------
    def key(self, event) -> bool:
        if self.canvas.mode != "drawing":
            return False
        k = event.key()
        if k == Qt.Key_Escape:
            return self.escape()
        s = self.session
        if self.name == "puente" and k in (Qt.Key_Delete, Qt.Key_Backspace):
            self.bridge.delete_current()
            return True
        if k == Qt.Key_Space and s is not None:
            self.space = True
            self.canvas.viewport().setCursor(Qt.OpenHandCursor)
            return True
        if s is not None and k in (Qt.Key_Return, Qt.Key_Enter):
            if s.tool == "linea" and s.busy:
                self._finish()
            return True
        if s is not None and k == Qt.Key_Backspace:
            if s.back():
                self.problem = ""
                self._redraw()
                self.status.emit(s.hint())
            return True
        if event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            return False
        name = dt.KEYS.get(event.text().lower())
        if name is None:
            return False
        if name != self.name:
            self.set_tool(name)
        return True

    def key_release(self, event) -> bool:
        if event.key() == Qt.Key_Space and self.space:
            self.space, self._pan = False, None
            self.canvas.viewport().setCursor(Qt.CrossCursor if self.session is not None else Qt.ArrowCursor)
            return True
        return False

    # --- dar puntos ------------------------------------------------------------------------------------------------------
    def _give(self, spot: dt.Spot) -> None:
        s = self.session
        s.stroke = self.stroke_of()
        try:
            done = s.add(spot.xy)
        except de.EditError as e:
            self._refuse(str(e))
            return
        self.problem = ""
        self._after(done)

    def _finish(self) -> None:
        try:
            done = self.session.finish()
        except de.EditError as e:
            self._refuse(str(e))
            return
        self._after(done)

    def _refuse(self, reason: str) -> None:
        self.problem = reason[:1].upper() + reason[1:]
        self.status.emit(self.problem)
        self._redraw()

    def _after(self, done: Optional[dt.Done]) -> None:
        if done is not None:
            self._redraw()
            self.finished.emit(done)
            return
        self.status.emit(self.session.hint())
        self._redraw()

    # --- lo que se ve ----------------------------------------------------------------------------------------------------
    def _item(self, key: str, z: float, ignore_zoom: bool = False) -> QGraphicsPathItem:
        item = self._items.get(key)
        if item is None:
            item = QGraphicsPathItem()
            item.setZValue(z)
            item.setAcceptedMouseButtons(Qt.NoButton)
            if ignore_zoom:
                item.setFlag(QGraphicsPathItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
            self.canvas.scene().addItem(item)
            self._items[key] = item
        return item

    def _hide_all(self) -> None:
        for item in self._items.values():
            item.hide()
        self.bridge.hide()
        self.hud.hide()

    def _redraw(self) -> None:
        if self.name == "puente":
            self.bridge.redraw()
            return
        if not self.active or self.spot is None:
            self._hide_all()
            return
        draft = self.session.draft(self.spot.xy)
        self._draw_preview(draft)
        self._draw_ghost(draft)
        self._draw_marker(self.spot, draft)
        self._draw_hud(self.spot, draft)

    def _draw_preview(self, draft: dt.Draft) -> None:
        item = self._item("preview", Z_PREVIEW)
        if draft.shape is not None:
            item.setPath(path_from_geom(draft.shape.shape))
            tint = QColor(theme.ACCENT)
            tint.setAlpha(40)
            item.setBrush(QBrush(tint) if draft.shape.closed else QBrush())
        elif draft.path:
            item.setPath(polyline_path([list(draft.path)]))
            item.setBrush(QBrush())
        else:
            item.hide()
            return
        item.setPen(_pen(theme.ACCENT, 1.6, Qt.DashLine))
        item.show()

    def _draw_ghost(self, draft: dt.Draft) -> None:
        if draft.shift is None:
            if "ghost" in self._items:
                self._items["ghost"].hide()
            return
        item = self._item("ghost", Z_GHOST)
        if item.path().isEmpty():                                      # lo que se mueve, de una vez y sin repetir en cada paso
            path = QPainterPath()
            for i in self.selected:
                path.addPath(path_from_geom(self.shapes[i].shape))
            item.setPath(path)
            item.setPen(_pen(theme.ACCENT, 1.4, alpha=170))
        item.setPos(*draft.shift)
        item.show()

    def _draw_marker(self, spot: dt.Spot, draft: dt.Draft) -> None:
        if not spot.kind:
            if "marker" in self._items:
                self._items["marker"].hide()
            return
        item = self._item("marker", Z_MARKER, ignore_zoom=True)
        item.setPath(marker_path(spot.kind))
        item.setPen(_pen(theme.WARN if draft.closes else theme.OK, 2.0))
        item.setPos(spot.x, spot.y)
        item.show()

    def _draw_hud(self, spot: dt.Spot, draft: dt.Draft) -> None:
        text = dt.coord_text(spot)
        if draft.text:
            text += "\n" + draft.text
        if self.problem:
            text += "\n⚠ " + self.problem
        self.hud.setText(text)
        self.hud.adjustSize()
        self.place_hud()
        self.hud.show()
        self.hud.raise_()

    def place_hud(self) -> None:
        """Pone la etiqueta de medidas junto al cursor, sin que se salga de la vista."""
        vp = self.canvas.viewport()
        w, h = self.hud.width(), self.hud.height()
        x, y = self._pos.x() + HUD_OFFSET.x(), self._pos.y() + HUD_OFFSET.y()
        if x + w > vp.width():
            x = self._pos.x() - HUD_OFFSET.x() - w
        if y + h > vp.height():
            y = self._pos.y() - HUD_OFFSET.y() - h
        self.hud.move(max(x, 0), max(y, 0))
