"""La herramienta Puente del lienzo: colocar, deslizar y estirar puentes con el ratón.

Es parte de la capa de herramientas (`ToolLayer`, que le pasa los eventos cuando la herramienta activa es Puente). Las cuentas de
cada gesto están en `cnc.bridge_tools` (sin Qt, con sus pruebas); aquí solo se traduce el ratón a ellas, se pinta lo que se ve
mientras se arrastra y se entrega el resultado como una lista de puentes de la forma (`edited`).
"""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QBrush, QPainterPath
from PySide6.QtWidgets import QGraphicsPathItem

from .. import bridge_tools as bt
from ..tabs import Tab, along, locate, perimeter, tab_line
from . import theme
from .canvas import CLICK_PIXELS, _pen, polyline_path
from ..i18n import _

GRAB_PIXELS = 5.0              # a cuantos pixeles de un puente vale un clic para agarrarlo
HANDLE_PIXELS = 9.0            # a cuantos pixeles de un extremo del puente elegido vale un clic para estirarlo
Z_BRIDGE, Z_HANDLE = 41, 42


class BridgeTool(QObject):
    edited = Signal(int, object, float)       # forma, sus puentes (None: vuelven a automaticos) y la posicion que queda elegida (-1: ninguna)
    selected = Signal(object)                 # None, o (forma, puente, es a mano) del puente elegido

    def __init__(self, layer):
        super().__init__(layer)
        self.layer = layer
        self.canvas = layer.canvas
        self.bridges: dict = {}                                    # forma -> (puentes, a mano), tal como los ve el dibujo
        self.problem_of: Callable[[int], str] = lambda i: ""      # por que no se pueden poner puentes en esa forma ('' si se puede)
        self.size_of: Callable[[], tuple] = lambda: (6.0, 2.0)    # ancho y alto de un puente nuevo
        self.cur: Optional[tuple] = None                          # (forma, posicion) del puente elegido
        self._drag: Optional[dict] = None
        self._hover: Optional[tuple] = None                       # (forma, posicion) de lo que hay bajo el cursor
        self._items: dict = {}

    # --- estado --------------------------------------------------------------------------------------------------------
    def shape(self, index: int):
        shapes = self.layer.shapes
        return shapes[index].shape if 0 <= index < len(shapes) else None

    def tabs_of(self, index: int) -> tuple:
        return tuple(self.bridges.get(index, ((), False))[0])

    def current(self) -> Optional[tuple]:
        """(forma, numero de puente, puente, a mano) del puente elegido, o None."""
        if self.cur is None:
            return None
        index, at = self.cur
        tabs = self.tabs_of(index)
        k = bt.pick_tab(tabs, at, perimeter(self.shape(index)) if self.shape(index) is not None else 1.0, slack=0.5)
        if k is None:
            return None
        return index, k, tabs[k], bool(self.bridges.get(index, ((), False))[1])

    def set_shapes(self) -> None:
        """Las formas o los puentes han cambiado: el puente elegido sigue si todavia existe."""
        self._drag, self._hover = None, None
        if self.cur is not None and self.current() is None:
            self.cur = None
            self.selected.emit(None)
        elif self.cur is not None:
            self.selected.emit(self.current())

    def select(self, index: int, at: float) -> None:
        self.cur = (index, at) if at >= 0 else None
        self.selected.emit(self.current())
        self.redraw()

    def reset(self) -> None:
        self._drag = self._hover = None
        if self.cur is not None:
            self.cur = None
            self.selected.emit(None)
        self.hide()

    def scene_cleared(self) -> None:
        self._items = {}

    def cancel(self) -> bool:
        """Esc: suelta lo que se arrastra o, si no hay nada, deja de tener un puente elegido."""
        if self._drag is not None:
            self._drag = None
            self.redraw()
            return True
        if self.cur is not None:
            self.cur = None
            self.selected.emit(None)
            self.redraw()
            return True
        return False

    # --- del raton al contorno -------------------------------------------------------------------------------------------
    def _scene(self, event):
        return self.canvas.mapToScene(event.position().toPoint())

    def _mm(self, pixels: float) -> float:
        return pixels / max(self.canvas.scale_factor, 1e-9)

    def _under(self, event) -> Optional[tuple]:
        """(forma, posicion, puente) bajo el cursor: la forma cuyo contorno esta mas cerca, el sitio del contorno y el puente
        que cubre ese sitio (o None)."""
        p = self._scene(event)
        index = self.canvas.shape_at(p)
        if index is None:
            return None
        shape = self.shape(index)
        found = locate(shape, (p.x(), p.y())) if shape is not None else None
        if found is None:
            return index, None, None                                # una linea abierta: no tiene contorno donde poner puentes
        at = found[0]
        k = bt.pick_tab(self.tabs_of(index), at, perimeter(shape), slack=self._mm(GRAB_PIXELS))
        return index, at, k

    def _handle(self, event) -> Optional[int]:
        """-1 o +1 si el cursor esta sobre un extremo del puente elegido."""
        cur = self.current()
        if cur is None:
            return None
        index, __, tab, __ = cur
        shape = self.shape(index)
        p = self._scene(event)
        found = locate(shape, (p.x(), p.y()))
        if found is None:
            return None
        at, distance = found
        length = perimeter(shape)
        end = bt.near_end(tab, at, length, self._mm(HANDLE_PIXELS))
        if end is None or distance > self._mm(HANDLE_PIXELS):
            return None
        return end

    # --- los gestos ------------------------------------------------------------------------------------------------------
    def press(self, event) -> None:
        end = self._handle(event)
        if end is not None:
            index, k, tab, __ = self.current()
            self._drag = {"kind": "resize", "index": index, "k": k, "end": end, "tabs": self.tabs_of(index),
                          "tab": tab, "from": event.position(), "moved": False}
            return
        under = self._under(event)
        if under is None:
            self._drag = None
            if self.cur is not None:
                self.cur = None
                self.selected.emit(None)
            self.redraw()
            return
        index, at, k = under
        tabs = self.tabs_of(index)
        if at is None:
            self._drag = None
            why = self.problem_of(index) or _("los puentes solo se ponen en formas cerradas.")
            self.layer.status.emit(why[:1].upper() + why[1:])
            return
        if k is not None:
            self.cur = (index, tabs[k].at)
            self.selected.emit(self.current())
            self._drag = {"kind": "slide", "index": index, "k": k, "tabs": tabs, "tab": tabs[k],
                          "from": event.position(), "moved": False}
            self.redraw()
            return
        why = self.problem_of(index)
        if why:
            self._drag = None
            self.layer.status.emit(why[:1].upper() + why[1:])
            return
        width, height = self.size_of()
        self._drag = {"kind": "add", "index": index, "tabs": tabs, "tab": Tab(at=at, width=width, height=height),
                      "from": event.position(), "moved": True}
        self.redraw()

    def move(self, event) -> None:
        d = self._drag
        if d is None:
            under = self._under(event)
            self._hover = None if under is None else (under[0], under[1], under[2])
            self.canvas.viewport().setCursor(Qt.PointingHandCursor if (under is not None and under[2] is not None)
                                             else Qt.CrossCursor)
            self.redraw()
            return
        shape = self.shape(d["index"])
        p = self._scene(event)
        found = locate(shape, (p.x(), p.y())) if shape is not None else None
        if found is None:
            return
        at = found[0]
        length = perimeter(shape)
        if (event.position() - d["from"]).manhattanLength() >= CLICK_PIXELS:
            d["moved"] = True
        base = d["tab"]
        if d["kind"] == "slide":
            d["preview"] = bt.slid(base, at) if d["moved"] else base
        elif d["kind"] == "resize":
            d["preview"] = bt.resized(base, d["end"], at, length) if d["moved"] else base
        else:
            d["preview"] = Tab(at=at, width=base.width, height=base.height)
        self.redraw()

    def release(self, event) -> None:
        d, self._drag = self._drag, None
        if d is None:
            return
        preview = d.get("preview") or d["tab"]
        index = d["index"]
        if d["kind"] == "add":
            tabs, k = bt.added(d["tabs"], preview)
            self.edited.emit(index, tabs, tabs[k].at)
        elif d["moved"] and preview != d["tab"]:
            tabs, k = bt.replaced(d["tabs"], d["k"], preview)
            self.edited.emit(index, tabs, tabs[k].at)
        self.redraw()

    def delete_current(self) -> bool:
        """Supr: quita el puente elegido. False si no hay ninguno."""
        cur = self.current()
        if cur is None:
            return False
        index, k, __, __ = cur
        self.edited.emit(index, bt.removed(self.tabs_of(index), k), -1.0)
        return True

    # --- lo que se ve ------------------------------------------------------------------------------------------------------
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

    def hide(self) -> None:
        for item in self._items.values():
            item.hide()
        self.layer.hud.hide()

    def _line(self, index: int, tab: Tab) -> list:
        shape = self.shape(index)
        return tab_line(shape, tab) if shape is not None else []

    def redraw(self) -> None:
        if self.canvas.mode != "drawing" or self.layer.name != "puente":
            self.hide()
            return
        ghost, current, text = QPainterPath(), QPainterPath(), ""
        d = self._drag
        if d is not None and d.get("preview") is not None:
            ghost = polyline_path([self._line(d["index"], d["preview"])])
            text = self._describe(d["index"], d["preview"])
        elif d is None and self._hover is not None:
            index, at, k = self._hover
            if at is None:
                pass
            elif k is None:
                if not self.problem_of(index):
                    width, height = self.size_of()
                    ghost = polyline_path([self._line(index, Tab(at=at, width=width, height=height))])
                    text = _("Clic para poner un puente aquí") + " · " + self._describe(index, Tab(at=at, width=width, height=height))
            else:
                text = self._describe(index, self.tabs_of(index)[k])
        cur = self.current()
        if cur is not None:
            index, k, tab, __ = cur
            shown = d["preview"] if (d is not None and d.get("preview") is not None and d["index"] == index and d["kind"] != "add") else tab
            current = polyline_path([self._line(index, shown)])
        g = self._item("ghost", Z_BRIDGE)
        g.setPath(ghost)
        g.setPen(_pen(theme.BRIDGE, 4.0, Qt.DashLine, alpha=200))
        g.setVisible(not ghost.isEmpty())
        c = self._item("current", Z_BRIDGE + 0.5)
        c.setPath(current)
        c.setPen(_pen(theme.ACCENT, 5.0))
        c.setVisible(not current.isEmpty())
        self._draw_handles(cur, d)
        if text:
            self.layer.hud.setText(text)
            self.layer.hud.adjustSize()
            self.layer.place_hud()
            self.layer.hud.show()
            self.layer.hud.raise_()
        else:
            self.layer.hud.hide()

    def _draw_handles(self, cur, drag) -> None:
        """Dos cuadritos blancos en los extremos del puente elegido (de tamaño fijo en pantalla)."""
        keys = ("handle-", "handle+")
        if cur is None:
            for key in keys:
                if key in self._items:
                    self._items[key].hide()
            return
        index, __, tab, __ = cur
        shown = drag["preview"] if (drag is not None and drag.get("preview") is not None and drag["index"] == index
                                    and drag["kind"] != "add") else tab
        length = perimeter(self.shape(index))
        for key, end in zip(keys, (-1, 1)):
            point = along(self.shape(index), bt.end_of(shown, end, length))
            item = self._item(key, Z_HANDLE, ignore_zoom=True)
            if point is None:
                item.hide()
                continue
            p = QPainterPath()
            p.addRect(-4, -4, 8, 8)
            item.setPath(p)
            item.setPen(_pen(theme.ACCENT, 1.6))
            item.setBrush(QBrush(Qt.white))
            item.setPos(*point)
            item.show()

    def _describe(self, index: int, tab: Tab) -> str:
        info = bt.describe(self.shape(index), tab)
        return _("Puente: posición {position:g} de {length:g} mm · ancho {width:g} · alto {height:g} mm",
                 position=info["position"], length=info["length"], width=tab.width, height=tab.height)
