"""Lienzo: el dibujo con sus tableros y la vista de maquina con la trayectoria de cada operacion."""
from __future__ import annotations

import math
from typing import Optional

import shapely
from PySide6.QtCore import QEvent, QLineF, QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QPolygonF, QTransform
from PySide6.QtWidgets import (QGraphicsEllipseItem, QGraphicsPathItem, QGraphicsPolygonItem, QGraphicsRectItem,
                               QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView, QRubberBand)
from shapely.geometry import Point, Polygon

from .. import grid as gr
from ..oprows import shape_key
from ..svg_import import ImportedShape
from ..tabs import tab_line
from . import theme
from ..i18n import _

ITEM_KIND = 0        # QGraphicsItem.data(0): "shape" | "board"
ITEM_ID = 1          # indice de la forma en drawing.shapes, o numero de tablero
FRAME_KINDS = ("frame", "frame-zero")      # lo del contorno de la maquina: no cuenta para ajustar la vista
ZOOM_MIN, ZOOM_MAX = 0.02, 400.0
WHEEL_STEP = 1.25    # lo que acerca o aleja cada paso de la rueda


def wheel_factor(delta: int, inverted: bool = True) -> float:
    """El zoom de un paso de rueda: >1 acerca, <1 aleja, 1 si la rueda no se movio en vertical (una rueda lateral).
    `delta` es el angleDelta().y() de Qt (positivo: la rueda gira hacia delante, alejandose de quien la usa). Con
    `inverted` (lo normal aqui) girarla hacia delante aleja y hacia atras acerca; sin ella, al reves."""
    if delta == 0:
        return 1.0
    zoom_in = delta < 0 if inverted else delta > 0
    return WHEEL_STEP if zoom_in else 1.0 / WHEEL_STEP
HIT_PIXELS = 6.0     # a cuantos pixeles de la linea de un vector vale un clic para elegirlo
CLICK_PIXELS = 4     # un clic que se mueve menos que esto no es un arrastre


def path_from_geom(geom) -> QPainterPath:
    """Camino de Qt de un poligono (con sus huecos) o de una linea de shapely."""
    path = QPainterPath()
    path.setFillRule(Qt.OddEvenFill)
    if isinstance(geom, Polygon):
        rings = [geom.exterior, *geom.interiors]
        for ring in rings:
            pts = list(ring.coords)
            path.moveTo(*pts[0])
            for p in pts[1:]:
                path.lineTo(*p)
            path.closeSubpath()
    else:
        for part in getattr(geom, "geoms", [geom]):
            pts = list(part.coords)
            if len(pts) < 2:
                continue
            path.moveTo(*pts[0])
            for p in pts[1:]:
                path.lineTo(*p)
    return path


def _multi_path(geom) -> QPainterPath:
    """Camino de Qt de un conjunto de poligonos (MultiPolygon) de shapely."""
    path = QPainterPath()
    path.setFillRule(Qt.OddEvenFill)
    for part in getattr(geom, "geoms", [geom]):
        if isinstance(part, Polygon):
            path.addPath(path_from_geom(part))
    return path


def polyline_path(polylines) -> QPainterPath:
    path = QPainterPath()
    for pl in polylines:
        if len(pl) < 2:
            continue
        path.moveTo(*pl[0])
        for p in pl[1:]:
            path.lineTo(*p)
    return path


def _pen(color: str, width: float = 1.0, style=Qt.SolidLine, alpha: int = 255) -> QPen:
    c = QColor(color)
    c.setAlpha(alpha)
    pen = QPen(c, width, style)
    pen.setCosmetic(True)                       # el grosor no cambia con el zoom
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def dotted_pen(color: str, width: float = 2.2) -> QPen:
    """Linea de puntos redondos (no de rayas): la del recorrido de la maquina."""
    pen = _pen(color, width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setDashPattern([0.01, 3.2])             # el largo de los trazos va en anchos de linea: casi cero = un punto
    return pen


class Canvas(QGraphicsView):
    shapeClicked = Signal(int)                  # clic sin teclas sobre un vector: su indice en drawing.shapes
    shapesSelected = Signal(object, str)        # (indices, "replace" | "add" | "toggle"): Ctrl, Mayus, recuadro o clic en vacio
    boardClicked = Signal(int)                  # numero de tablero
    holdAddRequested = Signal(float, float)     # clic en un sitio libre (modo sujecion): x, y de maquina
    holdRemoveRequested = Signal(int)           # clic sobre un punto de sujecion: su indice
    holdHovered = Signal(float, float)          # el raton pasa por (x, y) en modo sujecion
    holdLeft = Signal()                         # el raton sale del lienzo

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor(theme.PANEL))
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.viewport().setCursor(Qt.ArrowCursor)             # Qt pone una mano; aqui solo se ve al arrastrar de verdad
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setViewportUpdateMode(QGraphicsView.BoundingRectViewportUpdate)
        self.setTransform(QTransform.fromScale(1, -1))      # Y hacia arriba, como en la maquina
        self._press = None
        self.wheel_inverted = True                           # girar la rueda hacia delante aleja (ver wheel_factor)
        self._shape_items: dict[int, QGraphicsPathItem] = {}
        self._outlines: dict[int, object] = {}               # linea de cada vector elegible (para saber cual se toca)
        self._hit_tree = None
        self._hit_ids: list[int] = []
        self._band: Optional[QRubberBand] = None             # recuadro de eleccion (Mayus + arrastrar)
        self._band_origin: Optional[QPoint] = None
        self._board_items: dict[int, QGraphicsRectItem] = {}
        self._rapid_items: list = []
        self._travel_items: list = []
        self._hold_items: list = []                          # zona libre y puntos de sujecion
        self._alarm_items: list = []                         # circulos de los avisos de espacio (vista de maquina)
        self._ghost: Optional[QGraphicsEllipseItem] = None
        self.hold_mode = False
        self._frame: tuple = ()                              # esquinas del recorrido de la maquina sobre el dibujo (mm)
        self._frame_text = ""
        self._frame_items: list = []
        self.show_frame = True                               # el contorno de la maquina, en la vista del dibujo
        self.grid_on = True                                  # la rejilla (en la vista del dibujo y en la de la maquina)
        self.mode = "empty"
        self._carry: Optional[tuple] = None                  # (vista a la que va, zoom, centro) al pasar del dibujo a la maquina o al reves
        self._carried = False                                # el zoom que se pone viene de la otra vista: hay que ver que caiga sobre lo dibujado
        self._bridges: dict = {}                             # los puentes de cada forma en la vista del dibujo
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)              # el cursor se sigue sin pulsar nada (vista previa, iman)
        self.setFocusPolicy(Qt.StrongFocus)
        from .draw_layer import ToolLayer                   # aqui y no arriba: la capa usa funciones de este modulo
        self.tools = ToolLayer(self)                         # dibujar y mover con el raton

    # --- contenido -----------------------------------------------------------------------------------------
    def _view_state(self, keep: bool, mode: str):
        """Zoom y centro actuales, para no perderlos al repintar el mismo tipo de vista; o los que trae la otra vista
        (`carry_view_to`) al pasar del dibujo a la maquina o al reves."""
        carry, self._carry = self._carry, None
        if not keep:
            return None
        if self.mode == mode and self._scene.items():
            return self.transform(), self.centre()
        if carry is not None and carry[0] == mode:
            self._carried = True
            return carry[1], carry[2]
        return None

    def _restore_or_fit(self, state) -> None:
        carried, self._carried = self._carried, False
        if state is None:
            self.fit()
            return
        self.setTransform(state[0])
        self.centerOn(state[1])
        if carried and not self._on_the_work(state[1]):
            self.fit()                                  # lo que se miraba no esta en esta vista (otro tablero): se ve todo

    def work_rect(self) -> QRectF:
        """Lo dibujado de verdad (tableros, formas, trayectorias): sin el contorno ni el recorrido de la maquina."""
        rect = QRectF()
        for it in self._scene.items():
            if it.data(ITEM_KIND) not in FRAME_KINDS and it.zValue() > -15:
                rect = rect.united(it.sceneBoundingRect())
        return rect

    def _on_the_work(self, point: QPointF) -> bool:
        rect = self.work_rect()
        if rect.isNull():
            return False
        margin = max(rect.width(), rect.height()) * 0.25
        return rect.adjusted(-margin, -margin, margin, margin).contains(point)

    def _corner_axes(self):
        """Origen y ejes (unitarios) de la maquina vistos sobre el dibujo, de las esquinas de su recorrido; None sin ellas."""
        f = self._frame
        if len(f) != 4:
            return None
        (x0, y0), (x1, y1), (x3, y3) = f[0], f[1], f[3]
        lx, ly = math.hypot(x1 - x0, y1 - y0), math.hypot(x3 - x0, y3 - y0)
        if lx <= 0 or ly <= 0:
            return None
        return (x0, y0), ((x1 - x0) / lx, (y1 - y0) / lx), ((x3 - x0) / ly, (y3 - y0) / ly)

    def carry_view_to(self, mode: str) -> None:
        """Al pasar de la vista del dibujo a la de la maquina (o al reves) se conserva el zoom y la zona que se mira: el
        punto del centro se lleva de unas coordenadas a las otras con el giro y la colocacion del trabajo. Sin ellas
        (o sin trabajo) la vista nueva se ajusta entera. Vale para la proxima vista que se pinte."""
        self._carry = None
        if mode == self.mode or self.mode not in ("drawing", "machine") or not self._scene.items():
            return
        axes = self._corner_axes()
        if axes is None:
            return
        (ox, oy), (ax, ay), (bx, by) = axes
        c = self.centre()
        if self.mode == "drawing":                       # de las coordenadas del dibujo a las de la maquina
            dx, dy = c.x() - ox, c.y() - oy
            point = QPointF(dx * ax + dy * ay, dx * bx + dy * by)
        else:                                            # y al reves
            point = QPointF(ox + c.x() * ax + c.y() * bx, oy + c.x() * ay + c.y() * by)
        self._carry = (mode, QTransform(self.transform()), point)

    def clear(self) -> None:
        self._scene.clear()
        self._shape_items, self._board_items = {}, {}
        self._outlines, self._hit_tree, self._hit_ids = {}, None, []
        self._rapid_items, self._travel_items = [], []
        self._hold_items, self._ghost = [], None
        self._alarm_items = []
        self._frame_items = []
        self.tools.scene_cleared()

    def show_message(self, text: str) -> None:
        self.clear()
        self.mode = "empty"
        self.tools.reset()
        item = self._scene.addSimpleText(text)
        item.setBrush(QColor(theme.MUTED))
        item.setTransform(QTransform.fromScale(1, -1))      # el texto no se ve del reves
        rect = item.sceneBoundingRect()
        self._scene.setSceneRect(rect.adjusted(-200, -200, 200, 200))
        self.centerOn(rect.center())
        self.resetTransform()
        self.scale(1, -1)

    def show_drawing(self, shapes: list[ImportedShape], boards, ignored_keys=(), selected_board: Optional[int] = None,
                     highlight_key=None, keep_view: bool = False, selected=(), titles=None, bridges=None) -> None:
        """El dibujo tal cual viene del SVG, con sus tableros. Las formas de colores ignorados salen atenuadas, las
        elegidas (`selected`: indices) con un halo, y `titles` da a cada fila su nombre (el de un grupo) para el tooltip."""
        state = self._view_state(keep_view, "drawing")
        self.clear()
        self.mode = "drawing"
        ignored = set(ignored_keys)
        chosen = set(selected)
        titles = titles or {}
        board_idx = {b.index: b for b in boards}
        for b in boards:
            r = QGraphicsRectItem(QRectF(b.bounds[0], b.bounds[1], b.width, b.height))
            sel = b.number == selected_board
            r.setPen(_pen(theme.ACCENT if sel else theme.MUTED, 2.0 if sel else 1.0, Qt.SolidLine if sel else Qt.DashLine))
            r.setBrush(QBrush(QColor(theme.BOARD_FILL if sel else "#f0efe9")))
            r.setZValue(-10)
            r.setData(ITEM_KIND, "board")
            r.setData(ITEM_ID, b.number)
            r.setToolTip(_("Tablero {number}: {width:.0f} x {height:.0f} mm", number=b.number, width=b.width, height=b.height))
            self._scene.addItem(r)
            self._board_items[b.number] = r
            lab = QGraphicsSimpleTextItem(_("Tablero {number}", number=b.number))
            lab.setBrush(QColor(theme.ACCENT if sel else theme.MUTED))
            lab.setPos(b.bounds[0], b.bounds[3])
            lab.setFlag(lab.GraphicsItemFlag.ItemIgnoresTransformations, True)
            lab.setZValue(5)
            self._scene.addItem(lab)
        for i, s in enumerate(shapes):
            if i in board_idx:                                 # el rectangulo del tablero ya esta dibujado
                if i in chosen:
                    self._add_halo(s)
                continue
            key = shape_key(s)
            color = s.stroke or s.fill or theme.SHAPE_NO_COLOR
            item = QGraphicsPathItem(path_from_geom(s.shape))
            dim = key in ignored
            alpha = 70 if dim else 255
            hl = highlight_key is not None and key == highlight_key
            item.setPen(_pen(color if (s.stroke or not s.fill) else color, 3.0 if hl else 1.2, alpha=alpha))
            if s.fill and s.closed:
                c = QColor(s.fill)
                c.setAlpha(60 if dim else (230 if not s.stroke else 70))
                item.setBrush(QBrush(c))
            item.setZValue(2 if hl else 0)
            item.setData(ITEM_KIND, "shape")
            item.setData(ITEM_ID, i)
            item.setToolTip(_("{v} (forma {i})", v=titles.get(key) or key[0] or key[1] or _('sin color'), i=i))
            self._scene.addItem(item)
            self._shape_items[i] = item
            self._outlines[i] = s.shape.boundary if isinstance(s.shape, Polygon) else s.shape
            if i in chosen:
                self._add_halo(s)
        self._bridges = dict(bridges or {})
        self._add_bridges(shapes)
        if not shapes:
            self._empty_drawing_marks()
        self._add_selection_box([shapes[i].shape.bounds for i in sorted(chosen) if 0 <= i < len(shapes)])
        self._draw_frame()
        self._set_scene_rect(self._scene.itemsBoundingRect(), state)
        self.tools.set_shapes(shapes, chosen)
        self.tools.set_bridges(self._bridges)

    def bridges_shown(self) -> dict:
        """Los puentes que la vista del dibujo tiene pintados (forma -> (puentes, a mano))."""
        return self._bridges if self.mode == "drawing" else {}

    def _add_bridges(self, shapes: list) -> None:
        """Los puentes de cada forma sobre su contorno: ambar fuerte los puestos a mano, mas claro los que propone el programa."""
        for i, (tabs, manual) in self._bridges.items():
            if not tabs or not 0 <= i < len(shapes):
                continue
            lines = [tab_line(shapes[i].shape, t) for t in tabs]
            item = QGraphicsPathItem(polyline_path(lines))
            item.setPen(_pen(theme.BRIDGE, 4.4, alpha=255 if manual else 160))
            item.setZValue(3)
            item.setAcceptedMouseButtons(Qt.NoButton)
            item.setData(ITEM_KIND, "bridge")
            item.setToolTip(_("{count} puente(s), {how}", count=len(tabs), how=_("puestos a mano") if manual else _("automáticos")))
            self._scene.addItem(item)

    def _add_selection_box(self, bounds: list) -> None:
        """Varias figuras elegidas son una sola: su rectangulo circunscrito, con las cuatro esquinas marcadas. Es el que miden la
        tira de medidas y «Colocar en…», y el que agarra la herramienta Mover."""
        if len(bounds) < 2:
            return
        x0, y0 = min(b[0] for b in bounds), min(b[1] for b in bounds)
        x1, y1 = max(b[2] for b in bounds), max(b[3] for b in bounds)
        box = QGraphicsRectItem(QRectF(x0, y0, x1 - x0, y1 - y0))
        box.setPen(_pen(theme.ACCENT, 1.5, Qt.DashLine))
        box.setBrush(Qt.NoBrush)
        box.setZValue(3)
        box.setAcceptedMouseButtons(Qt.NoButton)
        box.setData(ITEM_KIND, "selbox")
        self._scene.addItem(box)
        for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
            corner = QGraphicsRectItem(QRectF(-3.5, -3.5, 7, 7))
            corner.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
            corner.setPos(x, y)
            corner.setPen(_pen(theme.ACCENT, 1.5))
            corner.setBrush(QBrush(QColor("white")))
            corner.setZValue(3)
            corner.setAcceptedMouseButtons(Qt.NoButton)
            corner.setData(ITEM_KIND, "selbox")
            self._scene.addItem(corner)

    def _add_halo(self, s: ImportedShape) -> None:
        halo = QGraphicsPathItem(path_from_geom(s.shape))
        halo.setPen(_pen(theme.ACCENT, 7.0, alpha=120))
        halo.setZValue(1)
        halo.setAcceptedMouseButtons(Qt.NoButton)
        halo.setData(ITEM_KIND, "halo")
        self._scene.addItem(halo)

    def _empty_drawing_marks(self) -> None:
        """Un dibujo sin formas: el cero y un trozo de pagina a la vista para saber donde se dibuja."""
        page = QGraphicsRectItem(QRectF(0, 0, 240, 160))
        page.setPen(_pen(theme.MUTED, 1.0, Qt.DashLine))
        page.setBrush(Qt.NoBrush)
        page.setAcceptedMouseButtons(Qt.NoButton)
        self._scene.addItem(page)
        z = QPainterPath()
        z.moveTo(-8, 0), z.lineTo(8, 0), z.moveTo(0, -8), z.lineTo(0, 8)
        zi = QGraphicsPathItem(z)
        zi.setPen(_pen(theme.ERROR, 1.5))
        zi.setFlag(zi.GraphicsItemFlag.ItemIgnoresTransformations, True)
        zi.setToolTip(_("Cero del dibujo (X0 Y0)"))
        self._scene.addItem(zi)
        text = QGraphicsSimpleTextItem(_("Dibujo vacío: usa el menú Dibujo para añadir formas"))
        text.setBrush(QColor(theme.MUTED))
        text.setFlag(text.GraphicsItemFlag.ItemIgnoresTransformations, True)     # no se voltea con la vista (Y hacia arriba)
        text.setPos(12, 90)
        self._scene.addItem(text)

    def show_machine(self, plan, previews, machine, tool_diameter: float, show_rapids: bool = False,
                     show_travel: bool = True, keep_view: bool = False) -> None:
        """El trabajo ya colocado en coordenadas de maquina, con la trayectoria de cada operacion."""
        state = self._view_state(keep_view, "machine")
        self.clear()
        self.mode = "machine"
        self.tools.reset()
        radius = tool_diameter / 2
        if show_travel:
            t = QGraphicsRectItem(QRectF(0, 0, machine.travel[0], machine.travel[1]))
            t.setPen(dotted_pen(theme.MACHINE))
            t.setBrush(Qt.NoBrush)
            t.setZValue(-20)
            t.setAcceptedMouseButtons(Qt.NoButton)
            t.setToolTip(_("Recorrido de la máquina {travel:g} x {travel2:g} mm desde el cero", travel=machine.travel[0], travel2=machine.travel[1]))
            self._scene.addItem(t)
            self._travel_items.append(t)
        virtual = getattr(plan, "virtual_stock", False)
        for s in plan.stock:
            b = s.shape.bounds
            r = QGraphicsRectItem(QRectF(b[0], b[1], b[2] - b[0], b[3] - b[1]))
            r.setPen(_pen("#9a8a62", 1.5, Qt.DashLine if virtual else Qt.SolidLine))
            r.setBrush(QBrush(QColor(theme.BOARD_FILL)))
            r.setZValue(-10)
            if virtual:
                r.setToolTip(_("Tablero supuesto: del cero a lo que se corta más el mismo margen (el dibujo no trae recuadro)"))
            self._scene.addItem(r)
        for s in plan.shapes:
            item = QGraphicsPathItem(path_from_geom(s.shape))
            item.setPen(_pen("#8f8c82", 0.8, alpha=150))
            item.setZValue(-5)
            self._scene.addItem(item)
        if previews:
            rapid_path = QPainterPath()
            for op in previews:
                for seg in op.rapids:
                    rapid_path.moveTo(*seg[0])
                    rapid_path.lineTo(*seg[1])
            if show_rapids:
                it = QGraphicsPathItem(rapid_path)
                it.setPen(_pen(theme.RAPID, 0.8, Qt.DotLine))
                it.setZValue(-1)
                self._scene.addItem(it)
                self._rapid_items.append(it)
            for op in previews:
                color = theme.KIND_COLORS[op.kind]
                if op.cuts:
                    it = QGraphicsPathItem(polyline_path(op.cuts))
                    it.setPen(_pen(color, 1.3))
                    it.setZValue(3)
                    it.setToolTip(_("Operación {index}: {lower}, hasta Z {z_min:.2f} mm", index=op.index, lower=theme.KIND_LABELS[op.kind].lower(), z_min=op.z_min))
                    self._scene.addItem(it)
                if op.bridges:
                    it = QGraphicsPathItem(polyline_path(op.bridges))
                    pen = _pen(theme.BRIDGE, 3.4)
                    pen.setCapStyle(Qt.FlatCap)
                    it.setPen(pen)
                    it.setZValue(4)
                    it.setToolTip(_("Operación {index}: {count} puente(s). La fresa pasa por encima y deja ese tramo sin cortar.",
                                    index=op.index, count=len(op.bridges)))
                    self._scene.addItem(it)
                for x, y, _z in op.drills:
                    e = QGraphicsEllipseItem(QRectF(x - radius, y - radius, 2 * radius, 2 * radius))
                    e.setPen(_pen(color, 1.3))
                    e.setBrush(QBrush(QColor(color).lighter(190)))
                    e.setZValue(3)
                    e.setToolTip(_("Operación {index}: taladro hasta Z {z_min:.2f} mm", index=op.index, z_min=op.z_min))
                    self._scene.addItem(e)
        # el cero
        z = QPainterPath()
        z.moveTo(-8, 0), z.lineTo(8, 0), z.moveTo(0, -8), z.lineTo(0, 8)
        zi = QGraphicsPathItem(z)
        zi.setPen(_pen(theme.ERROR, 1.5))
        zi.setFlag(zi.GraphicsItemFlag.ItemIgnoresTransformations, True)
        zi.setZValue(10)
        zi.setToolTip(_("Cero de la pieza (X0 Y0)"))
        self._scene.addItem(zi)
        self.set_alarm_layer(getattr(getattr(plan, "result", None), "marks", None) or ())
        self._set_scene_rect(self._scene.itemsBoundingRect(), state)

    # --- avisos de espacio ------------------------------------------------------------------------------------------
    def clear_alarm_layer(self) -> None:
        for it in self._alarm_items:
            if it.scene() is self._scene:
                self._scene.removeItem(it)
        self._alarm_items = []

    def set_alarm_layer(self, marks) -> None:
        """Un circulo rojo sobre cada sitio donde la fresa no llega (punto agudo, zona estrecha) o no cabe entre dos piezas,
        con el sitio al pasar el raton; entre piezas, ademas, la raya de los dos puntos mas cercanos. Solo en la vista de
        maquina. Son solo una señal: no se pulsan ni mueven la vista (el raton pasa de largo y sigue arrastrando)."""
        self.clear_alarm_layer()
        if self.mode != "machine":
            return
        for m in marks:
            if m.line:
                (x1, y1), (x2, y2) = m.line
                path = QPainterPath()
                path.moveTo(x1, y1)
                path.lineTo(x2, y2)
                seg = QGraphicsPathItem(path)
                pen = _pen(theme.ERROR, 2.0)
                pen.setCosmetic(True)
                seg.setPen(pen)
                seg.setZValue(14)
                seg.setAcceptedMouseButtons(Qt.NoButton)
                seg.setData(ITEM_KIND, "alarm-line")
                self._scene.addItem(seg)
                self._alarm_items.append(seg)
            ring = QGraphicsEllipseItem(QRectF(-12, -12, 24, 24))
            ring.setFlag(ring.GraphicsItemFlag.ItemIgnoresTransformations, True)    # del mismo tamaño con cualquier zoom
            ring.setPos(m.x, m.y)
            fill = QColor(theme.ERROR)
            fill.setAlpha(45)
            ring.setBrush(QBrush(fill))
            ring.setPen(QPen(QColor(theme.ERROR), 2.5))
            ring.setZValue(15)
            ring.setAcceptedMouseButtons(Qt.NoButton)
            ring.setToolTip(m.text)
            ring.setData(ITEM_KIND, "alarm")
            self._scene.addItem(ring)
            self._alarm_items.append(ring)

    def alarm_item_count(self) -> int:
        return sum(1 for it in self._alarm_items if it.data(ITEM_KIND) == "alarm")

    # --- contorno de la maquina ---------------------------------------------------------------------------------------
    @staticmethod
    def _frame_caption(machine) -> str:
        name = (getattr(machine, "name", "") or "").strip()
        size = _("{travel:g} x {travel2:g} mm", travel=machine.travel[0], travel2=machine.travel[1])
        return _("Máquina {name} · {size}", name=name, size=size) if name else _("Máquina · {size}", size=size)

    def set_machine_frame(self, corners, caption: str = "") -> None:
        """El recorrido de la maquina sobre el dibujo (cuatro esquinas, la primera es el cero de la maquina) y su texto
        (para el aviso al pasar el raton y la leyenda). Vacio lo quita. Solo se ve en la vista del dibujo."""
        self._frame, self._frame_text = tuple(corners), caption
        self._draw_frame()

    def set_frame_visible(self, on: bool) -> None:
        self.show_frame = bool(on)
        self._draw_frame()

    def frame_text(self) -> str:
        """El texto del contorno que se ve ahora (vacio si no hay o esta apagado): para la leyenda de la ventana."""
        return self._frame_text if (self.show_frame and len(self._frame) == 4) else ""

    def _draw_frame(self) -> None:
        if self.mode != "drawing":                  # en la vista de la maquina el contorno es el recorrido, que ya esta
            return
        for it in self._frame_items:
            if it.scene() is self._scene:
                self._scene.removeItem(it)
        self._frame_items = []
        if not self.show_frame or len(self._frame) != 4:
            return
        poly = QGraphicsPolygonItem(QPolygonF([QPointF(x, y) for x, y in self._frame]))
        poly.setPen(dotted_pen(theme.MACHINE))
        poly.setBrush(Qt.NoBrush)
        poly.setZValue(-20)
        poly.setAcceptedMouseButtons(Qt.NoButton)
        poly.setData(ITEM_KIND, "frame")
        poly.setToolTip(_("{v}: hasta donde llega la fresa desde el cero (cruz roja) con el giro y la colocación que "
                          "tendrá el G-code", v=self._frame_text or _('Recorrido de la máquina')))
        self._scene.addItem(poly)
        self._frame_items.append(poly)
        zero = QPainterPath()
        zero.moveTo(-8, 0), zero.lineTo(8, 0), zero.moveTo(0, -8), zero.lineTo(0, 8)
        mark = QGraphicsPathItem(zero)
        mark.setPen(_pen(theme.ERROR, 1.5))
        mark.setFlag(mark.GraphicsItemFlag.ItemIgnoresTransformations, True)
        mark.setPos(*self._frame[0])
        mark.setZValue(10)
        mark.setAcceptedMouseButtons(Qt.NoButton)
        mark.setData(ITEM_KIND, "frame-zero")
        mark.setToolTip(_("Cero de la máquina (X0 Y0) para este tablero"))
        self._scene.addItem(mark)
        self._frame_items.append(mark)
        rect = poly.sceneBoundingRect()
        margin = max(rect.width(), rect.height()) * 0.5
        self._scene.setSceneRect(self._scene.sceneRect().united(rect.adjusted(-margin, -margin, margin, margin)))

    # --- rejilla -------------------------------------------------------------------------------------------------
    def set_grid_visible(self, on: bool) -> None:
        self.grid_on = bool(on)
        self.viewport().update()

    def grid_step(self) -> float:
        """El paso de la rejilla con el zoom de ahora: el de la cuadricula del raton (o un multiplo si no cabe) o, sin
        cuadricula, uno redondo que se adapta al zoom."""
        return gr.step_for(max(self.scale_factor, 1e-9), self.tools.options.grid)

    def grid_lines(self, view: QRectF) -> gr.Grid:
        return gr.grid_for((view.left(), view.top(), view.right(), view.bottom()), max(self.scale_factor, 1e-9),
                           self.tools.options.grid)

    def _grid_visible(self) -> bool:
        return self.grid_on and self.mode in ("drawing", "machine")

    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        if not self._grid_visible():
            return
        g = self.grid_lines(rect)
        if not g.count:
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, False)
        top, bottom, left, right = rect.top(), rect.bottom(), rect.left(), rect.right()
        painter.setPen(_pen(theme.LINE, 1.0, alpha=120))
        painter.drawLines([QLineF(x, top, x, bottom) for x in g.x_fine] + [QLineF(left, y, right, y) for y in g.y_fine])
        painter.setPen(_pen(theme.MUTED, 1.0, alpha=95))
        painter.drawLines([QLineF(x, top, x, bottom) for x in g.x_label] + [QLineF(left, y, right, y) for y in g.y_label])
        painter.restore()

    def drawForeground(self, painter, rect):
        super().drawForeground(painter, rect)
        if not self._grid_visible():
            return
        view = self.mapToScene(self.viewport().rect()).boundingRect()
        g = self.grid_lines(view)
        painter.save()
        painter.resetTransform()                                    # lo que sigue va en pixeles de la ventana
        font = painter.font()
        font.setPointSizeF(7.5)
        painter.setFont(font)
        painter.setPen(QColor(theme.MUTED))
        height = self.viewport().height()
        for x in g.x_label:
            painter.drawText(QPointF(self.mapFromScene(QPointF(x, 0)).x() + 3, height - 4), gr.label(x, g.step))
        for y in g.y_label:
            painter.drawText(QPointF(3, self.mapFromScene(QPointF(0, y)).y() - 3), gr.label(y, g.step))
        note = _("rejilla {label} mm", label=gr.label(g.step, g.step))
        width = painter.fontMetrics().horizontalAdvance(note)
        painter.drawText(QPointF(self.viewport().width() - width - 8, 14), note)
        painter.restore()

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self.viewport().update()                                    # los numeros del borde no se desplazan con el dibujo

    # --- sujecion ------------------------------------------------------------------------------------------------
    def set_hold_mode(self, on: bool) -> None:
        """Con el modo activo, en la vista de maquina un clic anade o quita un punto de sujecion."""
        self.hold_mode = on
        if not on:
            self.clear_hold_layer()

    def clear_hold_layer(self) -> None:
        for it in self._hold_items:
            if it.scene() is self._scene:
                self._scene.removeItem(it)
        self._hold_items, self._ghost = [], None

    def set_hold_layer(self, free, points, selected: Optional[int] = None) -> None:
        """La zona libre (verde) y los puntos de sujecion numerados sobre la vista de maquina: `points` es una lista
        de (x, y, ok); los que no valen salen en rojo."""
        self.clear_hold_layer()
        if self.mode != "machine":
            return
        if free is not None and not free.is_empty:
            it = QGraphicsPathItem(path_from_geom(free) if isinstance(free, Polygon) else _multi_path(free))
            it.setPen(_pen(theme.OK, 1.0, alpha=140))
            c = QColor(theme.OK)
            c.setAlpha(55)
            it.setBrush(QBrush(c))
            it.setZValue(-3)
            it.setToolTip(_("Zona donde se puede sujetar"))
            self._scene.addItem(it)
            self._hold_items.append(it)
        for i, (x, y, ok) in enumerate(points):
            color = QColor(theme.OK if ok else theme.ERROR)
            dot = QGraphicsEllipseItem(QRectF(-7, -7, 14, 14))
            dot.setFlag(dot.GraphicsItemFlag.ItemIgnoresTransformations, True)
            dot.setPos(x, y)
            dot.setBrush(QBrush(color))
            dot.setPen(QPen(QColor("white") if i != selected else QColor(theme.TEXT), 2.0 if i != selected else 2.5))
            dot.setZValue(20)
            dot.setData(ITEM_KIND, "hold")
            dot.setData(ITEM_ID, i)
            dot.setToolTip(_("Punto {v}: X{x:.1f} Y{y:.1f}", v=i + 1, x=x, y=y) + ("" if ok else _(" (no es seguro)")))
            label = QGraphicsSimpleTextItem(str(i + 1), dot)
            label.setBrush(QColor("white"))
            label.setPos(-3.5 * len(str(i + 1)), -7)
            self._scene.addItem(dot)
            self._hold_items.append(dot)

    def set_hold_ghost(self, x: float, y: float, ok: bool) -> None:
        """Un circulo que sigue al raton: verde si ahi se puede sujetar, rojo si no."""
        color = QColor(theme.OK if ok else theme.ERROR)
        if self._ghost is None or self._ghost.scene() is not self._scene:
            g = QGraphicsEllipseItem(QRectF(-9, -9, 18, 18))
            g.setFlag(g.GraphicsItemFlag.ItemIgnoresTransformations, True)
            g.setZValue(19)
            g.setAcceptedMouseButtons(Qt.NoButton)
            self._scene.addItem(g)
            self._ghost = g
            self._hold_items.append(g)
        color.setAlpha(70)
        self._ghost.setBrush(QBrush(color))
        self._ghost.setPen(QPen(QColor(theme.OK if ok else theme.ERROR), 2.0))
        self._ghost.setPos(x, y)

    def clear_hold_ghost(self) -> None:
        if self._ghost is not None and self._ghost.scene() is self._scene:
            self._scene.removeItem(self._ghost)
            if self._ghost in self._hold_items:
                self._hold_items.remove(self._ghost)
        self._ghost = None

    def hold_item_count(self) -> int:
        return sum(1 for it in self._hold_items if it.data(ITEM_KIND) == "hold")

    # --- vista ---------------------------------------------------------------------------------------------
    def _set_scene_rect(self, rect: QRectF, state=None) -> None:
        big = max(rect.width(), rect.height(), 100.0)
        full = rect.adjusted(-big, -big, big, big)
        if state is not None:                      # que lo que se ve ahora siga al alcance: si no, la vista salta al achicarse lo dibujado
            transform, centre = state
            half_w = self.viewport().width() / abs(transform.m11()) / 2
            half_h = self.viewport().height() / abs(transform.m22()) / 2
            full = full.united(QRectF(centre.x() - half_w, centre.y() - half_h, 2 * half_w, 2 * half_h))
        self._scene.setSceneRect(full)
        self._restore_or_fit(state)

    def content_rect(self) -> QRectF:
        """Lo que se ve al ajustar la vista: todo menos el contorno de la maquina (que puede ser mucho mayor que la pieza)."""
        rect = QRectF()
        for it in self._scene.items():
            if it.data(ITEM_KIND) not in FRAME_KINDS:
                rect = rect.united(it.sceneBoundingRect())
        return rect

    def fit(self) -> None:
        rect = self.content_rect()
        if rect.isNull() or rect.width() <= 0 or rect.height() <= 0:
            return
        vw, vh = max(self.viewport().width(), 1), max(self.viewport().height(), 1)
        s = min(vw / rect.width(), vh / rect.height()) * 0.94
        self.setTransform(QTransform.fromScale(s, -s))
        self.centerOn(rect.center())

    def zoom_by(self, factor: float) -> None:
        s = abs(self.transform().m11()) * factor
        if ZOOM_MIN <= s <= ZOOM_MAX:
            self.scale(factor, factor)

    def wheelEvent(self, event):
        factor = wheel_factor(event.angleDelta().y(), self.wheel_inverted)
        if factor != 1.0:
            self.zoom_by(factor)
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if not self.tools.double_click(event):
            self.fit()

    def event(self, event):
        # Supr con la herramienta Puente y un puente elegido es de la herramienta: la ventana no debe usarlo para borrar formas
        if (event.type() == QEvent.ShortcutOverride and self.tools.name == "puente" and self.tools.bridge.current() is not None
                and event.key() in (Qt.Key_Delete, Qt.Key_Backspace)):
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        if not self.tools.key(event):
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if not self.tools.key_release(event):
            super().keyReleaseEvent(event)

    def mousePressEvent(self, event):
        if self.tools.press(event):
            return
        self._press = event.position()
        if (self.mode == "drawing" and not self.hold_mode and event.button() == Qt.LeftButton
                and event.modifiers() & Qt.ShiftModifier):
            self._band_origin = event.position().toPoint()          # Mayus: un recuadro, no se desplaza la vista
            if self._band is None:
                self._band = QRubberBand(QRubberBand.Rectangle, self.viewport())
            self._band.setGeometry(QRect(self._band_origin, QSize()))
            self._band.show()
            return
        super().mousePressEvent(event)
        self._calm_cursor()

    def mouseReleaseEvent(self, event):
        if self.tools.release(event):
            return
        if self._band_origin is not None:
            origin, self._band_origin = self._band_origin, None
            self._band.hide()
            pos = event.position().toPoint()
            if (event.position() - self._press).manhattanLength() < CLICK_PIXELS:
                hit = self.shape_at(self.mapToScene(pos))
                if hit is not None:
                    self.shapesSelected.emit([hit], "add")
            else:
                self.shapesSelected.emit(self.shapes_in(self.mapToScene(QRect(origin, pos).normalized()).boundingRect()),
                                         "add")
            self._press = None
            return
        super().mouseReleaseEvent(event)
        self._calm_cursor()
        if self._press is not None and (event.position() - self._press).manhattanLength() < CLICK_PIXELS:
            self._click(event.position().toPoint(), event.modifiers())
        self._press = None

    def _calm_cursor(self) -> None:
        """Con ScrollHandDrag Qt pone una mano al pulsar y al soltar; aqui vuelve la flecha (la mano solo sale al arrastrar)."""
        if self.dragMode() == QGraphicsView.ScrollHandDrag:
            self.viewport().setCursor(Qt.ArrowCursor)

    def mouseMoveEvent(self, event):
        if self.tools.move(event):
            return
        if self._band_origin is not None:
            self._band.setGeometry(QRect(self._band_origin, event.position().toPoint()).normalized())
            return
        super().mouseMoveEvent(event)
        if (self.dragMode() == QGraphicsView.ScrollHandDrag and event.buttons() & Qt.LeftButton
                and self._press is not None and (event.position() - self._press).manhattanLength() >= CLICK_PIXELS):
            self.viewport().setCursor(Qt.ClosedHandCursor)         # ahora si se esta moviendo la vista
        if self.hold_mode and self.mode == "machine" and not event.buttons():
            p = self.mapToScene(event.position().toPoint())
            self.holdHovered.emit(p.x(), p.y())

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.tools.leave()
        if self.hold_mode:
            self.clear_hold_ghost()
            self.holdLeft.emit()

    def shape_at(self, scene_pos: QPointF) -> Optional[int]:
        """El vector cuya linea pasa a menos de HIT_PIXELS del punto (el mas cercano), o None."""
        if not self._outlines:
            return None
        if self._hit_tree is None:
            self._hit_ids = list(self._outlines)
            self._hit_tree = shapely.STRtree([self._outlines[i] for i in self._hit_ids])
        tol = HIT_PIXELS / max(self.scale_factor, 1e-9)
        point = Point(scene_pos.x(), scene_pos.y())
        best, best_d = None, tol
        for k in self._hit_tree.query(point.buffer(tol)):
            i = self._hit_ids[int(k)]
            d = self._outlines[i].distance(point)
            if d <= best_d:
                best, best_d = i, d
        return best

    def shapes_in(self, rect: QRectF) -> list[int]:
        """Los vectores que caben enteros en el rectangulo (en coordenadas del dibujo)."""
        x0, y0, x1, y1 = rect.left(), rect.top(), rect.right(), rect.bottom()
        out = []
        for i, line in self._outlines.items():
            a, b, c, d = line.bounds
            if a >= x0 and b >= y0 and c <= x1 and d <= y1:
                out.append(i)
        return sorted(out)

    def _click(self, pos, modifiers=Qt.NoModifier) -> None:
        if self.hold_mode and self.mode == "machine":
            for it in self.items(pos):
                if it.data(ITEM_KIND) == "hold":
                    self.holdRemoveRequested.emit(int(it.data(ITEM_ID)))
                    return
            p = self.mapToScene(pos)
            self.holdAddRequested.emit(p.x(), p.y())
            return
        if self.mode != "drawing":
            return
        hit = self.shape_at(self.mapToScene(pos))
        if hit is not None:
            if modifiers & Qt.ControlModifier:
                self.shapesSelected.emit([hit], "toggle")
            else:
                self.shapeClicked.emit(hit)
            return
        if not modifiers & Qt.ControlModifier:
            self.shapesSelected.emit([], "replace")                 # clic en vacio: se quita la eleccion
        for it in self.items(pos):
            if it.data(ITEM_KIND) == "board":
                self.boardClicked.emit(int(it.data(ITEM_ID)))
                return

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.mode == "empty":
            self.centerOn(self._scene.sceneRect().center())

    @property
    def scale_factor(self) -> float:
        return abs(self.transform().m11())

    def centre(self) -> QPointF:
        vp = self.viewport()
        inverse, ok = self.viewportTransform().inverted()
        middle = QPointF(vp.width() / 2.0, vp.height() / 2.0)                # el medio exacto: con el entero la vista se corria un pixel cada vez
        return inverse.map(middle) if ok else self.mapToScene(vp.rect().center())
