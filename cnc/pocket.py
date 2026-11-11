"""Vaciado, respetando islas.

Region de trabajo: A = forma erosionada por el radio de la fresa. Mientras el
CENTRO de la fresa este dentro de A, la fresa nunca se sale de la forma ni
entra en las islas (los huecos del poligono).

Dos estrategias:
- offset (por defecto): anillos paralelos al borde de A, separados <= diametro, que se recorren de dentro
  afuera; el ultimo es el propio borde (pasada de acabado ligera). No levanta la fresa entre anillos
  contiguos, asi que sirve igual para formas estrechas, anilladas o con islas (asi se hace habitualmente).
- zigzag: lineas paralelas recortadas contra A y despues el borde de A.

En las dos, si recorrer solo los bordes ya limpia todo (huecos pequenos), el desbaste sobra.
Cobertura: toda la region queda a menos de un radio de algun recorrido, asi que no quedan restos.
"""
from __future__ import annotations

import math
from functools import lru_cache

from shapely import affinity
from shapely.geometry import LinearRing, LineString, Polygon
from shapely.ops import unary_union

from .model import PocketOp, Stock, resolved
from .toolpath import ARC_SEGMENTS, Move, bottom_left_start, pass_depths, total_depth
from .i18n import _

EPS = 1e-9


def pocket_area(shape, tool_radius: float):
    """Region donde puede estar el centro de la fresa."""
    if isinstance(shape, Polygon):
        polys = [shape]
    else:
        polys = list(getattr(shape, "geoms", []))
    if not polys or not all(isinstance(p, Polygon) for p in polys):
        raise ValueError(_("El vaciado requiere un Polygon (o MultiPolygon)."))
    if shape.is_empty or not shape.is_valid:
        raise ValueError(_("La forma esta vacia o se cruza consigo misma."))
    area = shape.buffer(-tool_radius, quad_segs=ARC_SEGMENTS)
    if area.is_empty:
        raise ValueError(_("La fresa es demasiado grande para este vaciado."))
    return area


def _rotate(p, degrees: float):
    if degrees == 0:
        return p
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    return (p[0] * c - p[1] * s, p[0] * s + p[1] * c)


def raster_segments(area, step: float, angle: float = 0.0):
    """Segmentos de desbaste en zigzag: lista de (inicio, fin)."""
    rot = affinity.rotate(area, -angle, origin=(0, 0)) if angle else area
    minx, miny, maxx, maxy = rot.bounds
    height = maxy - miny
    n = max(1, math.ceil(height / step - 1e-9))
    segments = []
    for i in range(n):
        y = miny + (i + 0.5) * height / n
        hit = rot.intersection(LineString([(minx - 1.0, y), (maxx + 1.0, y)]))
        row = []
        for g in getattr(hit, "geoms", [hit]):
            if isinstance(g, LineString) and g.length > 1e-6:
                a, b = sorted([g.coords[0], g.coords[-1]])
                row.append((a, b))
        row.sort()
        if i % 2 == 1:  # zigzag: las lineas impares se recorren al reves
            row = [(b, a) for a, b in reversed(row)]
        segments += [(_rotate(a, angle), _rotate(b, angle)) for a, b in row]
    return segments


def finishing_rings(area, climb: bool = True):
    """Bordes de A en sentido de concordancia: exterior horario, islas antihorario."""
    rings = []
    for poly in getattr(area, "geoms", [area]):
        ext = list(poly.exterior.coords)
        if LinearRing(ext).is_ccw == climb:  # concordancia: exterior horario (climb=True)
            ext.reverse()
        rings.append(bottom_left_start(ext))
        for hole in poly.interiors:
            h = list(hole.coords)
            if LinearRing(h).is_ccw != climb:  # concordancia: isla antihoraria (climb=True)
                h.reverse()
            rings.append(bottom_left_start(h))
    return rings


COVER_TOL = 0.02  # mm: un resto de material mas fino que 2 x esto (rebabas de la aproximacion poligonal) no cuenta


def rings_cover(shape, rings, tool_radius: float, tol: float = COVER_TOL) -> bool:
    """True si recorrer solo los bordes de A ya deja limpia toda la forma (huecos pequenos, ranuras estrechas):
    entonces el desbaste en zigzag sobra."""
    swept = unary_union([LineString(r) for r in rings]).buffer(tool_radius, quad_segs=ARC_SEGMENTS)
    return shape.difference(swept).buffer(-tol).is_empty


def _orient(coords, want_cw: bool):
    """Anillo cerrado con el sentido pedido (horario si want_cw)."""
    coords = list(coords)
    if LinearRing(coords).is_ccw == want_cw:
        coords.reverse()
    return coords


def offset_rings(area, step: float, tool_radius: float, climb: bool = True):
    """Anillos de desbaste de dentro afuera: cada nivel es A erosionada k*step; el ultimo en recorrerse es el borde.

    Cada componente se recorre despues de todos sus hijos (los componentes del nivel siguiente que quedan dentro),
    y dentro de un componente primero su exterior y luego los anillos alrededor de las islas.
    Sentidos como en finishing_rings: exterior horario (concordancia), islas antihorario.
    """
    def polys(g):
        return [p for p in getattr(g, "geoms", [g]) if isinstance(p, Polygon) and p.area > 1e-6]

    levels: list[list[Polygon]] = []
    k = 0
    while True:
        level = polys(area if k == 0 else area.buffer(-k * step, quad_segs=ARC_SEGMENTS))
        if not level:
            break
        levels.append(level)
        k += 1
    # si queda un nucleo mas ancho que la fresa a la que no llega el ultimo anillo, otro anillo a un radio hacia dentro
    core = polys(unary_union(levels[-1]).buffer(-tool_radius, quad_segs=ARC_SEGMENTS))
    if core:
        levels.append(core)

    out: list[list[tuple[float, float]]] = []

    def children(depth: int, poly: Polygon) -> list[Polygon]:
        if depth + 1 >= len(levels):
            return []
        probe = poly.buffer(1e-6)
        return [child for child in levels[depth + 1] if probe.contains(child)]

    def emit(poly: Polygon) -> None:
        out.append(_orient(poly.exterior.coords, want_cw=climb))
        for hole in poly.interiors:
            out.append(_orient(hole.coords, want_cw=not climb))

    # Cada componente va despues de todos sus hijos. Con una pila y no con recursion: un vaciado grande con un paso
    # pequeño tiene miles de niveles y Python no admite mas de ~1000 llamadas anidadas.
    stack: list[tuple[int, Polygon, bool]] = [(0, top, False) for top in reversed(levels[0])]
    while stack:
        depth, poly, done = stack.pop()
        if done:
            emit(poly)
            continue
        stack.append((depth, poly, True))                  # su propio anillo, cuando ya esten todos los hijos
        for child in reversed(children(depth, poly)):      # (al reves, para que salgan en el orden de siempre)
            stack.append((depth + 1, child, False))
    return out


@lru_cache(maxsize=256)
def _planned(shape, tool_radius: float, stepover: float, climb: bool, strategy: str, angle: float) -> tuple:
    """Los recorridos de un vaciado. Con memoria, porque calcularlos es lo caro y cada vaciado los pide dos veces: para
    ordenarlos por donde empiezan (`entry_point`) y para generarlos (`pocket_moves`). Solo dependen de lo que entra."""
    area = pocket_area(shape, tool_radius)
    step = stepover * 2 * tool_radius
    rings = finishing_rings(area, climb)
    if rings_cover(shape, rings, tool_radius):
        return tuple(rings)
    if strategy == "offset":
        paths = offset_rings(area, step, tool_radius, climb)
        return (bottom_left_start(paths[0]), *paths[1:])      # el primero, como es habitual; el resto, al mas cercano
    if strategy != "zigzag":
        raise ValueError(_("strategy desconocida: {strategy!r} (offset o zigzag).", strategy=strategy))
    return (*([a, b] for a, b in raster_segments(area, step, angle)), *rings)


def plan_paths(op: PocketOp, tool_radius: float) -> list[list[tuple[float, float]]]:
    """Recorridos de cada nivel (los cerrados repiten su primer punto al final). Son para leer: no se modifican."""
    op = resolved(op)
    if not 0 < op.stepover <= 1:
        raise ValueError(_("stepover debe estar en (0, 1] (fraccion del diametro)."))
    return list(_planned(op.shape, tool_radius, op.stepover, op.climb, op.strategy, op.angle))


def _start_nearest(ring, pos):
    """Anillo cerrado girado para que empiece en el vertice mas cercano a pos."""
    pts = ring[:-1]
    k = min(range(len(pts)), key=lambda i: (math.dist(pts[i], pos), i))
    pts = pts[k:] + pts[:k]
    return pts + [pts[0]]


def entry_point(op: PocketOp, tool_radius: float) -> tuple[float, float]:
    """Donde empieza a cortar el vaciado (para ordenar las operaciones por cercania)."""
    return plan_paths(op, tool_radius)[0][0]


def pocket_moves(
    op: PocketOp, stock: Stock, tool_radius: float, safe_z: float, clearance_z: float
) -> list[Move]:
    op = resolved(op)
    depths = pass_depths(total_depth(op, stock), op.stepdown)
    area = pocket_area(op.shape, tool_radius)
    paths = plan_paths(op, tool_radius)
    reach = area.buffer(1e-6)  # tolerancia para decidir si un enlace recto queda dentro de A

    def link_ok(a, b) -> bool:
        return math.dist(a, b) < EPS or reach.covers(LineString([a, b]))

    moves: list[Move] = []
    pos = None    # XY actual (None: fresa fuera del material, al inicio)
    cur_z = None  # Z actual de corte
    for z in depths:
        for path in paths:
            if pos is not None and len(path) > 3 and path[0] == path[-1]:
                path = _start_nearest(path, pos)     # anillo cerrado: se entra por el punto mas cercano
            start = path[0]
            if pos is not None and link_ok(pos, start):
                # enlace recto y a profundidad, sin levantar la fresa
                if math.dist(pos, start) >= EPS:
                    moves.append(Move(x=start[0], y=start[1], z=cur_z, feed=op.feed_xy))
                if abs(cur_z - z) > EPS:
                    moves.append(Move(z=z, feed=op.feed_z))
            else:
                moves.append(Move(z=safe_z if pos is None else clearance_z, rapid=True))
                moves += [
                    Move(x=start[0], y=start[1], rapid=True),
                    Move(z=clearance_z, rapid=True),
                    Move(z=z, feed=op.feed_z),
                ]
            moves += [Move(x=x, y=y, z=z, feed=op.feed_xy) for x, y in path[1:]]
            pos, cur_z = path[-1], z
    moves.append(Move(z=safe_z, rapid=True))
    return moves
