"""Puentes (tabs): trozos de material que se dejan sin cortar para que la pieza no se mueva al soltarse del tablero. Sin Qt.

Un puente pertenece a un contorno y se sitúa por su posición a lo largo de él (una fracción de su perímetro contada desde
el primer vértice de la forma), no por X e Y: así lo siguen los giros, los movimientos y los cambios de escala de la pieza, y
si cambia la fresa se recoloca solo sobre la nueva trayectoria. Su anchura es la del puente que queda de verdad: la fresa se
levanta esa anchura más su propio diámetro. Su altura es el material que queda sobre el fondo del tablero.

Este módulo propone los puentes de una pieza (`propose_tabs`), los pasa a tramos de la trayectoria de la fresa
(`tab_spans`) y trocea el recorrido por ellos (`raised_path`); `cnc.toolpath` pone la Z.
"""
from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from itertools import accumulate
from typing import Optional, Sequence

from shapely.geometry import LinearRing, Point, Polygon

from .i18n import _

EPS = 1e-9
STRAIGHT_DEGREES = 30.0     # un tramo que gira menos que esto (sumando todos sus vértices) se considera recto
STEP = 1.0                  # mm: lo fino que se busca el mejor sitio de un puente
SPAN_FACTOR = 3.0           # un contorno aloja un puente por cada esta cantidad de veces lo que se levanta la fresa


class TabError(ValueError):
    """Un ajuste de puentes que no vale (el mensaje dice cuál)."""


@dataclass(frozen=True)
class Tab:
    """Un puente. `at` es la fracción (0 a 1) del perímetro del contorno, contada desde el primer vértice de la forma, donde
    está su centro."""

    at: float
    width: float            # mm del puente que queda (a lo largo del contorno)
    height: float           # mm de material que queda sobre el fondo del tablero


@dataclass(frozen=True)
class TabSettings:
    """Cómo se reparten los puentes automáticos."""

    width: float = 6.0
    height: float = 2.0
    spacing: float = 150.0      # mm de perímetro por puente
    minimum: int = 2            # puentes mínimos por pieza (si cabe)
    clear: float = 5.0          # mm libres de esquina a cada lado del tramo que levanta la fresa

    def __post_init__(self):
        for name, value in ((_("la anchura del puente"), self.width), (_("la altura del puente"), self.height),
                            (_("los mm de perímetro por puente"), self.spacing)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise TabError(_("{name} debe ser un número mayor que 0 (hay {value!r}).", name=name, value=value))
        if isinstance(self.minimum, bool) or not isinstance(self.minimum, int) or self.minimum < 1:
            raise TabError(_("los puentes mínimos por pieza deben ser un número entero, 1 o más."))
        if not (isinstance(self.clear, (int, float)) and math.isfinite(self.clear) and self.clear >= 0):
            raise TabError(_("la distancia libre de esquina no puede ser negativa."))


# --- el contorno --------------------------------------------------------------------------------------------------
def contour_points(shape) -> Optional[list]:
    """Los vértices del contorno cerrado de una forma (el último repite el primero), o None si no hay contorno cerrado."""
    if isinstance(shape, Polygon):
        return [(float(x), float(y)) for x, y in shape.exterior.coords]
    coords = getattr(shape, "coords", None)
    if coords is not None and getattr(shape, "is_closed", False):
        return [(float(x), float(y)) for x, y in coords]
    return None


def lengths(points: Sequence) -> list:
    """Longitud acumulada hasta cada punto (el primero es 0)."""
    steps = (math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:]))
    return [0.0, *accumulate(steps)]


def point_at(points: Sequence, cum: Sequence, s: float) -> tuple:
    """El punto que está a `s` mm del primero a lo largo de la línea (se queda en los extremos si se sale)."""
    if s <= 0:
        return tuple(points[0])
    if s >= cum[-1]:
        return tuple(points[-1])
    i = bisect_right(cum, s) - 1
    span = cum[i + 1] - cum[i]
    f = (s - cum[i]) / span if span > EPS else 0.0
    return (points[i][0] + f * (points[i + 1][0] - points[i][0]), points[i][1] + f * (points[i + 1][1] - points[i][1]))


def _turns(points: Sequence, cum: Sequence) -> tuple:
    """(posición, giro en grados) de cada vértice de un contorno cerrado; el giro del primero cuenta con el último tramo."""
    pts = list(points[:-1])
    n = len(pts)
    out_pos, out_turn = [], []
    for i in range(n):
        a, b, c = pts[i - 1], pts[i], pts[(i + 1) % n]
        v1, v2 = (b[0] - a[0], b[1] - a[1]), (c[0] - b[0], c[1] - b[1])
        if math.hypot(*v1) < EPS or math.hypot(*v2) < EPS:
            continue
        turn = abs(math.degrees(math.atan2(v1[0] * v2[1] - v1[1] * v2[0], v1[0] * v2[0] + v1[1] * v2[1])))
        out_pos.append(cum[i])
        out_turn.append(turn)
    return out_pos, out_turn


# --- puentes puestos a mano ---------------------------------------------------------------------------------------
MAX_TABS = 200              # puentes a mano en una sola forma


def tabs_to_list(tabs: Sequence[Tab]) -> list:
    """Los puentes como listas [posicion, ancho, alto] (como se guardan en el trabajo)."""
    return [[round(t.at, 6), round(t.width, 6), round(t.height, 6)] for t in tabs]


def tabs_from_list(raw, what: str = "") -> tuple:
    """Los puentes de una lista [[posicion, ancho, alto], ...]. TabError (con un mensaje para quien usa el programa) si no valen."""
    prefix = f"{what}: " if what else ""
    if not isinstance(raw, (list, tuple)) or len(raw) > MAX_TABS:
        raise TabError(prefix + _("los puentes deben ser una lista de a lo sumo {MAX_TABS}.", MAX_TABS=MAX_TABS))
    out = []
    for item in raw:
        try:
            at, width, height = item
            ok = all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (at, width, height))
        except (TypeError, ValueError):
            ok = False
        if not ok or not (0 <= at <= 1) or width <= 0 or height <= 0:
            raise TabError(prefix + _("un puente necesita posición (0 a 1), ancho y alto mayores que 0 (hay {item!r}).", item=item))
        out.append(Tab(at=float(at) % 1.0, width=float(width), height=float(height)))
    return tuple(sorted(out, key=lambda t: t.at))


def reverse_tabs(tabs: Sequence[Tab]) -> tuple:
    """Los mismos puentes cuando el contorno se recorre al revés (tras reflejar una forma): misma posición en la pieza."""
    return tuple(sorted((Tab(at=(1.0 - t.at) % 1.0, width=t.width, height=t.height) for t in tabs), key=lambda t: t.at))


def remap_tabs(old_shape, new_shape, tabs: Sequence[Tab]) -> tuple:
    """Los puentes de una forma que se ha rehecho con otras medidas (un rectángulo, un círculo...): cada uno va al punto
    del contorno nuevo que ocupa el mismo sitio relativo dentro de la caja de la forma."""
    old, new = contour_points(old_shape), contour_points(new_shape)
    if old is None or new is None:
        return ()
    cum_old, cum_new = lengths(old), lengths(new)
    ox0, oy0, ox1, oy1 = old_shape.bounds
    nx0, ny0, nx1, ny1 = new_shape.bounds
    ring = LinearRing(new)
    out = []
    for t in tabs:
        x, y = point_at(old, cum_old, (t.at % 1.0) * cum_old[-1])
        u = (x - ox0) / (ox1 - ox0) if ox1 - ox0 > EPS else 0.5
        v = (y - oy0) / (oy1 - oy0) if oy1 - oy0 > EPS else 0.5
        along = ring.project(Point(nx0 + u * (nx1 - nx0), ny0 + v * (ny1 - ny0)))
        out.append(Tab(at=(along / cum_new[-1]) % 1.0, width=t.width, height=t.height))
    return tuple(sorted(out, key=lambda t: t.at))


def tab_line(shape, tab: Tab) -> list:
    """Los puntos de la polilínea que ocupa un puente sobre el contorno de la forma (su ancho, centrado en su posición)."""
    pts = contour_points(shape)
    if pts is None:
        return []
    cum = lengths(pts)
    total = cum[-1]
    if total <= EPS:
        return []
    half = min(tab.width, total * 0.99) / 2
    centre = (tab.at % 1.0) * total
    a, b = centre - half, centre + half
    out = [point_at(pts, cum, a % total)]
    for s in sorted({round(c + k * total, 9) for c in cum for k in (-1, 0, 1)}):
        if a + EPS < s < b - EPS:
            out.append(point_at(pts, cum, s % total))
    out.append(point_at(pts, cum, b % total))
    return out


def locate(shape, point: Sequence[float]) -> Optional[tuple]:
    """(posición 0 a 1, distancia en mm) del punto del contorno de la forma que está más cerca de `point`; None si no hay contorno."""
    pts = contour_points(shape)
    if pts is None:
        return None
    total = lengths(pts)[-1]
    if total <= EPS:
        return None
    ring = LinearRing(pts)
    spot = Point(point[0], point[1])
    return (ring.project(spot) / total) % 1.0, ring.distance(spot)


def along(shape, at: float) -> Optional[tuple]:
    """El punto del contorno que está en esa posición (0 a 1)."""
    pts = contour_points(shape)
    if pts is None:
        return None
    cum = lengths(pts)
    return point_at(pts, cum, (at % 1.0) * cum[-1])


def perimeter(shape) -> float:
    pts = contour_points(shape)
    return lengths(pts)[-1] if pts else 0.0


# --- la propuesta -------------------------------------------------------------------------------------------------
def tab_count(perimeter: float, span: float, settings: TabSettings, room_perimeter: Optional[float] = None) -> int:
    """Cuántos puentes lleva un contorno: uno por `spacing` mm de perímetro, al menos `minimum`, y los que quepan
    (`span` es lo que levanta la fresa por puente) sin que se pisen ni dejen la pieza hecha de puentes. `room_perimeter`:
    lo que mide el recorrido de la fresa si es menos que el contorno (en un hueco va por dentro)."""
    wanted = max(settings.minimum, int(perimeter / settings.spacing + 0.5))
    room = int((perimeter if room_perimeter is None else room_perimeter) / (SPAN_FACTOR * span))
    return max(0, min(wanted, room))


def propose_tabs(shape, settings: TabSettings, tool_diameter: float, inside: bool = False) -> tuple:
    """Los puentes automáticos de un contorno cerrado: repartidos a partes iguales por el perímetro, con el reparto girado
    hasta que caigan en tramos rectos (lejos de las esquinas) y, si no hay forma, cada uno en el sitio más recto que
    haya cerca. Vacío si el contorno es demasiado pequeño. `inside`: es un hueco (la fresa lo recorre por dentro, por un
    camino más corto que el contorno)."""
    pts = contour_points(shape)
    if pts is None:
        return ()
    cum = lengths(pts)
    perimeter = cum[-1]
    span = settings.width + tool_diameter
    ring = max(perimeter - math.pi * tool_diameter, 0.0) if inside else None      # un camino por dentro pierde 2*pi*radio
    n = tab_count(perimeter, span, settings, ring)
    if n == 0:
        return ()
    half = span / 2 + settings.clear
    pos, turn = _turns(pts, cum)
    # tres vueltas seguidas: así una ventana que pasa por el principio del contorno se mira igual que cualquier otra
    big_pos = [p + k * perimeter for k in (-1, 0, 1) for p in pos]
    prefix = [0.0, *accumulate(turn * 3)]

    def bend(s: float, reach: float = half) -> float:
        """Lo que gira el contorno por encima de lo recto en la ventana de +-reach mm centrada en s."""
        lo, hi = bisect_right(big_pos, s - reach), bisect_left(big_pos, s + reach)
        return max(0.0, prefix[hi] - prefix[lo] - STRAIGHT_DEGREES) if hi > lo else 0.0

    gap = perimeter / n
    steps = max(1, int(gap / STEP))

    def cost(k: int) -> tuple:                           # primero que caiga recto; despues, lejos de las esquinas
        spots = [k * gap / steps + j * gap for j in range(n)]
        return (round(sum(bend(s) for s in spots), 6), round(sum(bend(s, 3 * half) for s in spots), 6))

    costs = [cost(k) for k in range(steps)]
    ties = [k for k, c in enumerate(costs) if c == min(costs)]
    best = ties[len(ties) // 2]                          # el medio de los repartos igual de buenos: centrado en el tramo
    spots = [(best * gap / steps + j * gap) % perimeter for j in range(n)]
    for j, s in enumerate(spots):                        # lo que no cayo en un tramo recto busca uno cerca
        if bend(s) > EPS:
            reach = int(gap / 4 / STEP)
            options = [s + d * STEP for d in range(-reach, reach + 1)]
            spots[j] = min(options, key=lambda t: (round(bend(t), 6), abs(t - s))) % perimeter
    return tuple(Tab(at=s / perimeter, width=settings.width, height=settings.height) for s in sorted(spots))


# --- sobre la trayectoria de la fresa -----------------------------------------------------------------------------
def tab_spans(shape, tabs: Sequence[Tab], rings: Sequence, tool_radius: float, side: str, thickness: float) -> list:
    """Los tramos de cada anillo de la trayectoria que la fresa recorre levantada: para cada anillo, una lista de
    (desde, hasta, Z de la cima del puente), con `desde` y `hasta` en mm contados desde el primer punto del anillo (`hasta`
    pasa de la longitud si el tramo da la vuelta). Un puente cae en el anillo que tiene más cerca de su sitio en el
    contorno; si no hay ninguno a la distancia que toca (la forma cambió), se salta."""
    out = [[] for __ in rings]
    pts = contour_points(shape)
    if pts is None or not rings:
        return out
    cum = lengths(pts)
    perimeter = cum[-1]
    lines = [LinearRing(r) for r in rings]
    expected = 0.0 if side == "on" else tool_radius
    for tab in tabs:
        spot = Point(point_at(pts, cum, (tab.at % 1.0) * perimeter))
        dists = [line.distance(spot) for line in lines]
        k = min(range(len(lines)), key=dists.__getitem__)
        if abs(dists[k] - expected) > max(0.5, 0.5 * tool_radius):
            continue
        half = tab.width / 2 + tool_radius
        centre = lines[k].project(spot)
        out[k].append((centre - half, centre + half, -(thickness - tab.height)))
    return out


def raised_path(points: Sequence, spans: Sequence) -> tuple:
    """Parte el recorrido de un anillo cerrado por sus tramos levantados. Devuelve (primer punto, pasos): cada paso es
    (x, y, cima) y va desde el punto anterior hasta (x, y); `cima` es la Z del puente que se pisa, o None. El recorrido
    arranca fuera de los puentes (la fresa baja ahi), asi que el primer punto puede no ser el que venia."""
    cum = lengths(points)
    total = cum[-1]
    pieces = []
    for a, b, top in spans:
        if b - a >= total - EPS:
            raise ValueError(_("un puente ocupa todo el contorno: es demasiado ancho para esta pieza."))
        a0 = a % total
        b0 = a0 + (b - a)
        pieces += [(a0, total, top), (0.0, b0 - total, top)] if b0 > total else [(a0, b0, top)]
    start = 0.0                                          # la bajada no puede caer dentro de un puente
    for a, b, __ in sorted(pieces):
        if a <= start + EPS and b > start:
            start = b
    if start >= total - EPS:
        raise ValueError(_("los puentes ocupan todo el contorno: son demasiado anchos para esta pieza."))
    if start > EPS:                                      # el recorrido empieza donde acaba el puente que tapaba la bajada
        moved = [((a - start) % total, b - a, top) for a, b, top in pieces]
        pieces = [(a, a + width, top) for a, width, top in moved if width > EPS]
    ring = _rotated(points, cum, start)
    along = lengths(ring)
    marks = sorted({round(s, 9) for a, b, __ in pieces for s in (a, b) if EPS < s < total - EPS}
                   | {round(c, 9) for c in along[1:]})
    steps, last = [], 0.0
    for s in marks:
        if s - last < EPS:
            continue
        mid = (last + s) / 2
        tops = [top for a, b, top in pieces if a - EPS <= mid <= b + EPS]
        x, y = point_at(ring, along, s)
        steps.append((x, y, max(tops) if tops else None))
        last = s
    return tuple(ring[0]), steps


def _rotated(points: Sequence, cum: Sequence, start: float) -> list:
    """El mismo anillo cerrado, empezando a `start` mm del primer punto (con un vertice nuevo ahi si hace falta)."""
    if start <= EPS:
        return [tuple(p) for p in points]
    first = point_at(points, cum, start)
    after = [tuple(p) for p, c in zip(points[:-1], cum) if c > start + EPS]
    before = [tuple(p) for p, c in zip(points[:-1], cum) if c < start - EPS]      # empieza por el primer vertice
    return [first, *after, *before, first]
