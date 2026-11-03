"""Del dibujo (shapely) a una lista de movimientos.

Cada Move lleva su propia Z: los tramos de una pasada comparten profundidad, salvo los puentes (tabs), donde las pasadas
que bajarian mas que la cima del puente van a esa altura (ver `cnc.tabs`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from shapely.geometry import LinearRing, LineString, Polygon

from .model import ContourOp, Stock, resolved
from .tabs import raised_path, tab_spans
from .i18n import _

ARC_SEGMENTS = 16  # segmentos por cuarto de circunferencia en los offsets


@dataclass(frozen=True)
class Move:
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None
    rapid: bool = False
    feed: Optional[float] = None  # mm/min, solo para movimientos de corte
    tab: bool = False             # tramo que va levantado por un puente (la vista previa lo pinta aparte)


def unreachable(poly, mode: str, tool_radius: float):
    """Parte de la forma que la fresa no puede cortar por su radio.

    mode='inside': material que queda dentro de un hueco o vaciado (zonas mas estrechas
    que la fresa y esquinas interiores). mode='outside': muescas de la pieza que quedan
    rellenas. Las esquinas interiores siempre quedan algo redondeadas; lo relevante son
    las zonas gruesas.
    """
    if mode == "inside":
        return poly.difference(poly.buffer(-tool_radius, quad_segs=ARC_SEGMENTS).buffer(tool_radius, quad_segs=ARC_SEGMENTS))
    return poly.buffer(tool_radius, quad_segs=ARC_SEGMENTS).buffer(-tool_radius, quad_segs=ARC_SEGMENTS).difference(poly)


def total_depth(op, stock: Stock) -> float:
    """Profundidad total (positiva) desde la superficie del material.

    Vale para cualquier operacion con los campos through y depth.
    """
    if stock.thickness <= 0:
        raise ValueError(_("El grosor del material debe ser > 0."))
    if op.through:
        extra = stock.spoilboard_extra
        if extra < 0:
            raise ValueError(_("La profundidad extra en el sufridor no puede ser negativa."))
        if extra > stock.max_spoilboard_extra:
            raise ValueError(
                _("Extra en el sufridor ({extra:g} mm) por encima del limite ({max_spoilboard_extra:g} mm).", extra=extra, max_spoilboard_extra=stock.max_spoilboard_extra)
            )
        return stock.thickness + extra
    if op.depth is None or op.depth <= 0:
        raise ValueError(_("Una operacion no pasante necesita depth > 0."))
    if op.depth > stock.thickness:
        raise ValueError(
            _("Profundidad {depth:g} mm mayor que el grosor del material ({thickness:g} mm); si quieres cortar "
              "del todo usa through=True.", depth=op.depth, thickness=stock.thickness)
        )
    return op.depth


def pass_depths(total: float, stepdown: float) -> list[float]:
    """Pasadas iguales (Z negativas); la ultima cae exactamente en -total."""
    if stepdown <= 0:
        raise ValueError(_("stepdown debe ser > 0."))
    n = max(1, math.ceil(total / stepdown - 1e-9))
    step = total / n
    depths = [-(step * i) for i in range(1, n + 1)]
    depths[-1] = -total
    return depths


def bottom_left_start(coords: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Gira un anillo cerrado para que empiece en el vertice mas cercano a la esquina inferior izquierda
    de su caja (asi se hace habitualmente). El sentido de giro no cambia."""
    pts = list(coords[:-1]) if len(coords) > 1 and coords[0] == coords[-1] else list(coords)
    if len(pts) < 2:
        return list(coords)
    x0 = min(p[0] for p in pts)
    y0 = min(p[1] for p in pts)
    k = min(range(len(pts)), key=lambda i: (round(math.hypot(pts[i][0] - x0, pts[i][1] - y0), 6), i))
    pts = pts[k:] + pts[:k]
    return pts + [pts[0]]


def tool_path_rings(
    shape, side: str, tool_radius: float, climb: bool = True
) -> list[tuple[list[tuple[float, float]], bool]]:
    """Trayectoria del CENTRO de la fresa: lista de (puntos, cerrado).

    - outside: la forma es la pieza; se offsetea hacia fuera.
    - inside:  la forma es un hueco; se offsetea hacia dentro.
    - on:      la fresa sigue la linea tal cual.
    En outside/inside el sentido se elige para que la pared acabada quede a la
    izquierda del avance (concordancia con husillo horario): antihorario por
    fuera, horario por dentro. Con climb=False se invierte. Cada anillo empieza en el
    vertice mas cercano a la esquina inferior izquierda de su caja, como es habitual.
    """
    if side == "on":
        if isinstance(shape, Polygon):
            return [(list(shape.exterior.coords), True)]
        if isinstance(shape, LineString):
            return [(list(shape.coords), bool(shape.is_closed))]
        raise ValueError(_("Con side='on' la forma debe ser Polygon o LineString."))

    if side not in ("outside", "inside"):
        raise ValueError(_("side desconocido: {side!r}", side=side))

    if isinstance(shape, Polygon):
        poly = Polygon(shape.exterior)  # los huecos se ignoran: haz una operacion 'inside' aparte
    elif isinstance(shape, LineString) and shape.is_closed:
        poly = Polygon(list(shape.coords))
    else:
        raise ValueError(_("outside/inside requieren una forma cerrada."))
    if poly.is_empty or not poly.is_valid:
        raise ValueError(_("La forma esta vacia o se cruza consigo misma."))

    dist = tool_radius if side == "outside" else -tool_radius
    off = poly.buffer(dist, quad_segs=ARC_SEGMENTS)
    if off.is_empty:
        raise ValueError(_("La fresa es demasiado grande para este contorno."))

    want_ccw = (side == "outside") == climb
    rings = []
    for piece in getattr(off, "geoms", [off]):
        coords = list(piece.exterior.coords)
        if LinearRing(coords).is_ccw != want_ccw:
            coords.reverse()
        rings.append((bottom_left_start(coords), True))
    return rings


def op_moves(
    op: ContourOp, stock: Stock, tool_radius: float, safe_z: float, clearance_z: float
) -> list[Move]:
    op = resolved(op)
    depths = pass_depths(total_depth(op, stock), op.stepdown)
    rings = tool_path_rings(op.shape, op.side, tool_radius, op.climb)
    spans = _bridge_spans(op, stock, rings, tool_radius)
    moves: list[Move] = []
    for (points, closed), lifted in zip(rings, spans):
        if lifted:
            start, steps = raised_path(points, lifted)       # arranca fuera de los puentes: ahi baja la fresa
        else:
            start, steps = points[0], [(x, y, None) for x, y in points[1:]]
        x0, y0 = start
        moves += [
            Move(z=safe_z, rapid=True),
            Move(x=x0, y=y0, rapid=True),
            Move(z=clearance_z, rapid=True),
        ]
        for i, z in enumerate(depths):
            if i > 0 and not closed:
                # trayecto abierto: subir y volver al inicio para la siguiente pasada
                moves += [Move(z=clearance_z, rapid=True), Move(x=x0, y=y0, rapid=True)]
            moves.append(Move(z=z, feed=op.feed_z))
            level = z
            for x, y, top in steps:
                target = top if top is not None and z < top - 1e-9 else z       # sobre un puente, hasta su cima y no mas
                if abs(target - level) > 1e-9:
                    moves.append(Move(z=target, feed=op.feed_z))
                    level = target
                moves.append(Move(x=x, y=y, z=target, feed=op.feed_xy, tab=target != z))
            if abs(level - z) > 1e-9:
                moves.append(Move(z=z, feed=op.feed_z))
        moves.append(Move(z=safe_z, rapid=True))
    return moves


def _bridge_spans(op: ContourOp, stock: Stock, rings, tool_radius: float) -> list:
    """Los tramos levantados de cada anillo de la trayectoria (vacios si la operacion no lleva puentes)."""
    none = [[] for __ in rings]
    if not op.tabs:
        return none
    if not op.through:
        raise ValueError(_("los puentes solo valen en cortes pasantes."))
    for tab in op.tabs:
        if not 0 < tab.height < stock.thickness:
            raise ValueError(_("un puente de {height:g} mm de alto no cabe en un material de {thickness:g} mm.",
                               height=tab.height, thickness=stock.thickness))
    if not all(closed for __, closed in rings):
        return none
    return tab_spans(op.shape, op.tabs, [points for points, __ in rings], tool_radius, op.side, stock.thickness)
