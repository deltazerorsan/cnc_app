"""Herramientas de dibujo: formas basicas, mover/girar/copiar, desfase (offset) y unir/restar formas.

Todo son funciones puras sobre la lista de formas del dibujo (`ImportedShape`: mm, Y hacia arriba, origen en la
esquina inferior izquierda de la pagina). Una **edicion** es un diccionario sencillo (asi se guarda dentro del
.cncjob y se vuelve a aplicar al abrirlo): `apply_edit(formas, edicion)` devuelve la lista nueva, de que forma antigua
viene cada forma (para conservar grupos, tableros y ajustes) y cuales quedan elegidas.

Las formas nuevas son siempre un poligono sin huecos (cerrado) o una linea: cuando una operacion deja un hueco dentro de
una pieza (restar, desfase), el hueco sale como una forma aparte dentro de la otra, que es como el programa entiende
piezas y huecos (por anidamiento).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Callable, Optional, Sequence

import shapely
from shapely import affinity
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from .svg_import import ImportedShape, is_degenerate
from .i18n import _
from .tabs import TabError, remap_tabs, reverse_tabs, tabs_from_list, tabs_to_list

ARC_TOL = 0.005        # mm: lo que se aparta un arco dibujado de la curva de verdad
MAX_NEW = 5000         # formas nuevas por operacion (una matriz de copias enorme es un error de tecleo)
LIMIT = 1.0e6          # mm: coordenadas y medidas mayores que esto son un error de tecleo
TIDY = 1.0e-4          # mm: vertices que sobran en lo que sale de unir, restar o desfasar
MIN_VERTEX = 1.0e-6    # mm: dos puntos mas cerca que esto son el mismo
MEASURE_TOL = 5.0e-4   # mm: una medida que difiere menos que esto de la actual es la actual (los cuadros de medidas redondean a 0,001)
ALIGN_SIDES = ("izquierda", "centro_x", "derecha", "abajo", "centro_y", "arriba")     # por que cara o centro se alinean las formas
ALIGN_LABELS = {"izquierda": _("Alinear a la izquierda"), "centro_x": _("Centrar en horizontal"), "derecha": _("Alinear a la derecha"),
                "arriba": _("Alinear arriba"), "centro_y": _("Centrar en vertical"), "abajo": _("Alinear abajo")}


class EditError(ValueError):
    """Una edicion que no se puede hacer (el mensaje esta escrito para quien usa el programa)."""


@dataclass
class EditResult:
    shapes: list[ImportedShape]
    origin: list[Optional[int]]                  # de cada forma nueva: indice de la antigua de la que viene (None: es nueva)
    selected: list[int]                          # indices (en `shapes`) que quedan elegidos
    notes: list[str] = field(default_factory=list)

    def index_map(self) -> dict[int, int]:
        """Antiguo -> nuevo para las formas que siguen existiendo (si una antigua tiene varias, la primera)."""
        out: dict[int, int] = {}
        for new, old in enumerate(self.origin):
            if old is not None and old not in out:
                out[old] = new
        return out


# --- comprobaciones -----------------------------------------------------------------------------------------
def _number(value, what: str, *, positive: bool = False, nonzero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > LIMIT:
        raise EditError(_("{what}: no es un numero valido (hay {value!r}).", what=what, value=value))
    value = float(value)
    if positive and value <= 0:
        raise EditError(_("{what} tiene que ser mayor que cero (hay {value:g}).", what=what, value=value))
    if nonzero and value == 0:
        raise EditError(_("{what} no puede ser cero.", what=what))
    return value


def _count(value, what: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or value > MAX_NEW:
        raise EditError(_("{what} tiene que ser un numero entero de {minimum} a {MAX_NEW} (hay {value!r}).", what=what, minimum=minimum, MAX_NEW=MAX_NEW, value=value))
    return value


def _indices(edit: dict, count: int, key: str = "formas") -> list[int]:
    raw = edit.get(key)
    if not isinstance(raw, (list, tuple)) or not raw:
        raise EditError(_("no hay ninguna forma elegida."))
    out: list[int] = []
    for i in raw:
        if isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < count:
            raise EditError(_("la forma {i!r} no existe.", i=i))
        if i not in out:
            out.append(i)
    return sorted(out)


def _color(value, what: str) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower()
    if len(text) != 7 or text[0] != "#" or any(c not in "0123456789abcdef" for c in text[1:]):
        raise EditError(_("{what}: el color tiene que ser #rrggbb (hay {value!r}).", what=what, value=value))
    return text


def arc_segments(radius: float, sweep: float = math.pi / 2, tol: float = ARC_TOL) -> int:
    """Tramos rectos con los que se dibuja un arco de `sweep` radianes para que no se aparte mas de `tol` mm de la curva."""
    if radius <= tol:
        return 1
    step = 2.0 * math.acos(1.0 - tol / radius)
    return max(1, math.ceil(sweep / step))


def _quadrant(radius: float) -> int:
    return min(256, max(8, arc_segments(radius)))


# --- formas basicas -----------------------------------------------------------------------------------------
def _dedupe(points: Sequence[tuple], ring: bool = True) -> list[tuple]:
    """Quita los puntos repetidos seguidos; en un contorno cerrado (`ring`) tambien el ultimo si vuelve al primero."""
    out: list[tuple] = []
    for p in points:
        if not out or math.dist(out[-1], p) > MIN_VERTEX:
            out.append((float(p[0]), float(p[1])))
    if ring and len(out) > 1 and math.dist(out[0], out[-1]) <= MIN_VERTEX:
        out.pop()
    return out


def _polygon(points: Sequence[tuple], what: str) -> Polygon:
    pts = _dedupe(points)
    if len(pts) < 3:
        raise EditError(_("{what}: hacen falta al menos tres puntos distintos.", what=what))
    poly = Polygon(pts)
    if not poly.is_valid:
        fixed = shapely.make_valid(poly)                       # un lazo o puntos en linea: que se vea cual de las dos
        if fixed.area < 0.01:
            raise EditError(_("{what}: el contorno no encierra nada (area de menos de 0,01 mm2).", what=what))
        raise EditError(_("{what}: el contorno se cruza consigo mismo.", what=what))
    if poly.area < 0.01:
        raise EditError(_("{what}: el contorno no encierra nada (area de menos de 0,01 mm2).", what=what))
    return orient(poly, 1.0)


def make_rectangle(x: float, y: float, width: float, height: float, radius: float = 0.0, *,
                   stroke: Optional[str], fill: Optional[str] = None) -> ImportedShape:
    """Rectangulo con la esquina inferior izquierda en (x, y) y las esquinas redondeadas con `radius` mm."""
    x, y = _number(x, "X"), _number(y, "Y")
    width, height = _number(width, _("El ancho"), positive=True), _number(height, _("El alto"), positive=True)
    radius = _number(radius, _("El radio de las esquinas"))
    if radius < 0:
        raise EditError(_("el radio de las esquinas no puede ser negativo."))
    if radius > min(width, height) / 2 + 1e-9:
        raise EditError(_("el radio de las esquinas ({radius:g} mm) no puede pasar de la mitad del lado menor ({v:g} mm).", radius=radius, v=min(width, height) / 2))
    if radius <= 0:
        pts = [(x, y), (x + width, y), (x + width, y + height), (x, y + height)]
    else:
        n = _quadrant(radius)
        pts = []
        for cx, cy, a0 in ((x + width - radius, y + radius, -90.0), (x + width - radius, y + height - radius, 0.0),
                           (x + radius, y + height - radius, 90.0), (x + radius, y + radius, 180.0)):
            for k in range(n + 1):
                a = math.radians(a0 + 90.0 * k / n)
                pts.append((cx + radius * math.cos(a), cy + radius * math.sin(a)))
    return ImportedShape(_polygon(pts, _("El rectangulo")), True, _color(stroke, _("Rectangulo")), _color(fill, _("Rectangulo")),
                         kind="rect")


def make_circle(cx: float, cy: float, radius: float, *, stroke: Optional[str], fill: Optional[str] = None) -> ImportedShape:
    cx, cy = _number(cx, _("X del centro")), _number(cy, _("Y del centro"))
    radius = _number(radius, _("El radio"), positive=True)
    n = 4 * _quadrant(radius)
    pts = [(cx + radius * math.cos(2 * math.pi * k / n), cy + radius * math.sin(2 * math.pi * k / n)) for k in range(n)]
    return ImportedShape(_polygon(pts, _("El circulo")), True, _color(stroke, _("Circulo")), _color(fill, _("Circulo")), kind="circle")


def make_ellipse(cx: float, cy: float, rx: float, ry: float, angle: float = 0.0, *, stroke: Optional[str],
                 fill: Optional[str] = None) -> ImportedShape:
    """Elipse de semiejes rx (horizontal) y ry (vertical), girada `angle` grados alrededor de su centro."""
    cx, cy = _number(cx, _("X del centro")), _number(cy, _("Y del centro"))
    rx, ry = _number(rx, _("El semieje horizontal"), positive=True), _number(ry, _("El semieje vertical"), positive=True)
    angle = _number(angle, _("El giro"))
    n = 4 * _quadrant(max(rx, ry))
    pts = [(cx + rx * math.cos(2 * math.pi * k / n), cy + ry * math.sin(2 * math.pi * k / n)) for k in range(n)]
    poly = _polygon(pts, _("La elipse"))
    if angle % 360:
        poly = affinity.rotate(poly, angle, origin=(cx, cy))
    return ImportedShape(poly, True, _color(stroke, _("Elipse")), _color(fill, _("Elipse")), kind="ellipse")


def make_polygon(cx: float, cy: float, sides: int, size: float, angle: float = 0.0, measure: str = "radio", *,
                 stroke: Optional[str], fill: Optional[str] = None) -> ImportedShape:
    """Poligono regular. `size` es el radio hasta los vertices (measure="radio") o la distancia del centro a cada lado
    (measure="apotema": para una tuerca hexagonal, la mitad de la medida entre caras). El primer vertice queda a `angle`
    grados (0 = hacia la derecha)."""
    cx, cy = _number(cx, _("X del centro")), _number(cy, _("Y del centro"))
    sides = _count(sides, _("El numero de lados"), 3)
    size = _number(size, _("El tamano"), positive=True)
    angle = _number(angle, _("El giro"))
    if measure not in ("radio", "apotema"):
        raise EditError(_("el tamano se mide como radio o como apotema."))
    radius = size if measure == "radio" else size / math.cos(math.pi / sides)
    pts = [(cx + radius * math.cos(math.radians(angle) + 2 * math.pi * k / sides),
            cy + radius * math.sin(math.radians(angle) + 2 * math.pi * k / sides)) for k in range(sides)]
    return ImportedShape(_polygon(pts, _("El poligono")), True, _color(stroke, _("Poligono")), _color(fill, _("Poligono")),
                         kind="polygon")


def make_slot(x0: float, y0: float, x1: float, y1: float, width: float, *, stroke: Optional[str],
              fill: Optional[str] = None) -> ImportedShape:
    """Ranura (un rectangulo con los extremos redondos) entre dos centros; `width` es su ancho total."""
    x0, y0, x1, y1 = (_number(v, n) for v, n in ((x0, "X1"), (y0, "Y1"), (x1, "X2"), (y1, "Y2")))
    width = _number(width, _("El ancho"), positive=True)
    if math.dist((x0, y0), (x1, y1)) <= MIN_VERTEX:
        raise EditError(_("los dos extremos de la ranura coinciden: para eso hace falta un circulo."))
    poly = LineString([(x0, y0), (x1, y1)]).buffer(width / 2, quad_segs=_quadrant(width / 2))
    return ImportedShape(_polygon(list(poly.exterior.coords), _("La ranura")), True, _color(stroke, _("Ranura")),
                         _color(fill, _("Ranura")), kind="path")


def make_polyline(points: Sequence[tuple], closed: bool = False, *, stroke: Optional[str],
                  fill: Optional[str] = None) -> ImportedShape:
    """Linea o polilinea por los puntos dados; cerrada es un poligono. Dos puntos son una linea."""
    pts = []
    for p in points:
        try:
            pts.append((_number(p[0], "X"), _number(p[1], "Y")))
        except (TypeError, IndexError):
            raise EditError(_("punto mal escrito: {p!r}.", p=p)) from None
    if not closed and len(pts) > 3 and math.dist(pts[0], pts[-1]) <= MIN_VERTEX:
        closed = True
    stroke, fill = _color(stroke, _("Linea")), _color(fill, _("Linea"))
    if closed:
        return ImportedShape(_polygon(pts, _("El contorno")), True, stroke, fill, kind="polygon")
    pts = _dedupe(pts, ring=False) if len(pts) > 2 else pts          # abierta: ir y volver por el mismo sitio es valido
    if len(pts) < 2 or all(math.dist(pts[0], p) <= MIN_VERTEX for p in pts):
        raise EditError(_("la linea necesita dos puntos distintos."))
    return ImportedShape(LineString(pts), False, stroke, fill, kind="line" if len(pts) == 2 else "polyline")


# --- guardar y leer formas ------------------------------------------------------------------------------------
def shape_to_dict(s: ImportedShape) -> dict:
    return {"geom": shapely.to_wkt(s.shape, rounding_precision=6, trim=True), "cerrada": bool(s.closed),
            "trazo": s.stroke, "relleno": s.fill, "tipo": s.kind}


def shape_from_dict(d) -> ImportedShape:
    try:
        geom = shapely.from_wkt(d["geom"])
        closed = bool(d["cerrada"])
        kind = str(d.get("tipo") or "path")
        stroke, fill = _color(d.get("trazo"), _("Forma")), _color(d.get("relleno"), _("Forma"))
    except EditError:
        raise
    except Exception:
        raise EditError(_("una forma guardada no se puede leer.")) from None
    if closed and (not isinstance(geom, Polygon) or geom.interiors or geom.is_empty or not geom.is_valid):
        raise EditError(_("una forma cerrada guardada no es un poligono valido."))
    if not closed and (not isinstance(geom, LineString) or geom.is_empty):
        raise EditError(_("una forma abierta guardada no es una linea."))
    return ImportedShape(geom, closed, stroke, fill, kind=kind)


# --- construir ediciones ----------------------------------------------------------------------------------------
def add_edit(shapes: Sequence[ImportedShape]) -> dict:
    return {"op": "anadir", "formas": [shape_to_dict(s) for s in shapes]}


def delete_edit(indices) -> dict:
    return {"op": "borrar", "formas": sorted(indices)}


def move_edit(indices, dx: float, dy: float) -> dict:
    return {"op": "mover", "formas": sorted(indices), "dx": dx, "dy": dy}


def rotate_edit(indices, degrees: float, cx: float, cy: float) -> dict:
    return {"op": "girar", "formas": sorted(indices), "grados": degrees, "cx": cx, "cy": cy}


def scale_edit(indices, sx: float, sy: float, cx: float, cy: float) -> dict:
    return {"op": "escalar", "formas": sorted(indices), "sx": sx, "sy": sy, "cx": cx, "cy": cy}


def mirror_edit(indices, axis: str, position: float) -> dict:
    return {"op": "reflejar", "formas": sorted(indices), "eje": axis, "pos": position}


def array_edit(indices, nx: int, ny: int, dx: float, dy: float) -> dict:
    return {"op": "copiar", "formas": sorted(indices), "nx": nx, "ny": ny, "dx": dx, "dy": dy}


def polar_edit(indices, count: int, degrees: float, cx: float, cy: float) -> dict:
    return {"op": "copiar_circular", "formas": sorted(indices), "n": count, "grados": degrees, "cx": cx, "cy": cy}


def offset_edit(indices, distance: float, keep: bool = False, corners: str = "redondas") -> dict:
    return {"op": "desfase", "formas": sorted(indices), "distancia": distance, "conservar": keep, "esquinas": corners}


def union_edit(indices, keep: bool = False) -> dict:
    return {"op": "unir", "formas": sorted(indices), "conservar": keep}


def subtract_edit(indices, base: str = "primera", keep: bool = False) -> dict:
    return {"op": "restar", "formas": sorted(indices), "base": base, "conservar": keep}


def intersect_edit(indices, keep: bool = False) -> dict:
    return {"op": "intersecar", "formas": sorted(indices), "conservar": keep}


def close_edit(indices) -> dict:
    return {"op": "cerrar", "formas": sorted(indices)}


def measures_edit(indices, x: float, y: float, width: float, height: float) -> dict:
    """Pone la caja de lo elegido (todo junto, si son varias formas) con su esquina inferior izquierda en (x, y) y con ese ancho y alto."""
    return {"op": "medidas", "formas": sorted(indices), "x": x, "y": y, "ancho": width, "alto": height}


def align_edit(indices, side: str) -> dict:
    """Alinea cada forma elegida con la caja de todas ellas: por su cara `izquierda`, `derecha`, `abajo` o `arriba`, o por su
    centro (`centro_x`: todos los centros en la misma X; `centro_y`: en la misma Y). Cada forma se mueve entera."""
    return {"op": "alinear", "formas": sorted(indices), "lado": side}


def tabs_edit(indices, tabs) -> dict:
    """Pone los puentes de esas formas a mano (`tabs`: lista de Tab; vacía: sin puentes) o los devuelve a automático (None)."""
    return {"op": "puentes", "formas": sorted(indices), "puentes": None if tabs is None else tabs_to_list(tabs)}


# --- medidas de lo elegido ----------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Measures:
    """La caja de lo elegido (mm, Y hacia arriba): esquina inferior izquierda y tamano. `kind` es "circle" si lo elegido es un
    solo circulo (entonces se habla de centro y diametro), el tipo de la forma si es una sola, o "varias"."""
    count: int
    kind: str
    x: float
    y: float
    width: float
    height: float

    @property
    def cx(self) -> float:
        return self.x + self.width / 2

    @property
    def cy(self) -> float:
        return self.y + self.height / 2

    def point(self, fx: float, fy: float) -> tuple:
        """Un punto de la caja: (0, 0) su esquina inferior izquierda, (1, 1) la superior derecha, (0.5, 0.5) su centro."""
        _check_fraction(fx, fy)
        return self.x + fx * self.width, self.y + fy * self.height


def _check_fraction(fx: float, fy: float) -> None:
    for f in (fx, fy):
        if isinstance(f, bool) or not isinstance(f, (int, float)) or not 0.0 <= f <= 1.0:
            raise EditError(_("el punto de la caja tiene que estar entre 0 y 1 (hay {f!r}).", f=f))


def box_corner(px: float, py: float, fx: float, fy: float, width: float, height: float) -> tuple:
    """La esquina inferior izquierda de una caja de ese tamano cuyo punto (`fx`, `fy`, como en `Measures.point`) esta en (px, py):
    el punto elegido es el que no se mueve cuando cambia el tamano."""
    _check_fraction(fx, fy)
    return px - fx * width, py - fy * height


def _bounds(shapes: Sequence[ImportedShape], picked: Sequence[int]) -> tuple:
    bs = [shapes[i].shape.bounds for i in picked]
    return min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)


def _is_circle(s: ImportedShape, w: float, h: float) -> bool:
    return s.kind == "circle" and s.closed and w > 0 and abs(w - h) <= 0.02 * max(w, h)


def measures_of(shapes: Sequence[ImportedShape], picked: Sequence[int]) -> Optional[Measures]:
    """Las medidas de lo elegido, o None si no hay nada elegido (o lo elegido ya no existe)."""
    picked = sorted({i for i in picked if isinstance(i, int) and 0 <= i < len(shapes)})
    if not picked:
        return None
    x0, y0, x1, y1 = _bounds(shapes, picked)
    w, h = x1 - x0, y1 - y0
    if len(picked) > 1:
        kind = "varias"
    else:
        s = shapes[picked[0]]
        kind = "circle" if _is_circle(s, w, h) else ("ellipse" if s.kind == "circle" else s.kind)
    return Measures(len(picked), kind, x0, y0, w, h)


# --- aplicar --------------------------------------------------------------------------------------------------------
def _transform(shapes: list[ImportedShape], picked: list[int], fn: Callable) -> EditResult:
    out = list(shapes)
    for i in picked:
        out[i] = fn(shapes[i])
    return EditResult(out, list(range(len(out))), picked)


def _op_add(shapes, edit) -> EditResult:
    raw = edit.get("formas")
    if not isinstance(raw, list) or not raw:
        raise EditError(_("no hay ninguna forma que anadir."))
    if len(raw) > MAX_NEW:
        raise EditError(_("demasiadas formas de una vez (mas de {MAX_NEW}).", MAX_NEW=MAX_NEW))
    new = [shape_from_dict(d) for d in raw]
    out = list(shapes) + new
    return EditResult(out, list(range(len(shapes))) + [None] * len(new), list(range(len(shapes), len(out))))


def _op_delete(shapes, edit) -> EditResult:
    picked = set(_indices(edit, len(shapes)))
    keep = [i for i in range(len(shapes)) if i not in picked]
    return EditResult([shapes[i] for i in keep], keep, [])


def _op_move(shapes, edit) -> EditResult:
    picked = _indices(edit, len(shapes))
    dx, dy = _number(edit.get("dx"), _("El desplazamiento en X")), _number(edit.get("dy"), _("El desplazamiento en Y"))
    if dx == 0 and dy == 0:
        raise EditError(_("el desplazamiento es cero: no se mueve nada."))
    return _transform(shapes, picked, lambda s: replace(s, shape=affinity.translate(s.shape, dx, dy)))


def _op_rotate(shapes, edit) -> EditResult:
    picked = _indices(edit, len(shapes))
    deg = _number(edit.get("grados"), _("El giro"))
    c = (_number(edit.get("cx"), _("X del centro de giro")), _number(edit.get("cy"), _("Y del centro de giro")))
    if deg % 360 == 0:
        raise EditError(_("un giro de 0 grados (o de una vuelta entera) no cambia nada."))
    return _transform(shapes, picked, lambda s: replace(s, shape=affinity.rotate(s.shape, deg, origin=c)))


def _op_scale(shapes, edit) -> EditResult:
    picked = _indices(edit, len(shapes))
    sx, sy = _number(edit.get("sx"), _("El factor en X"), positive=True), _number(edit.get("sy"), _("El factor en Y"), positive=True)
    c = (_number(edit.get("cx"), _("X del centro")), _number(edit.get("cy"), _("Y del centro")))
    if sx == 1 and sy == 1:
        raise EditError(_("un factor de 1 no cambia nada."))

    def fn(s: ImportedShape) -> ImportedShape:
        kind = "ellipse" if s.kind == "circle" and abs(sx - sy) > 1e-12 else s.kind
        return replace(s, shape=affinity.scale(s.shape, xfact=sx, yfact=sy, origin=c), kind=kind)

    return _transform(shapes, picked, fn)


def _op_mirror(shapes, edit) -> EditResult:
    picked = _indices(edit, len(shapes))
    axis, pos = edit.get("eje"), _number(edit.get("pos"), _("La posicion del eje"))
    if axis not in ("vertical", "horizontal"):
        raise EditError(_("el eje del reflejo es vertical u horizontal."))

    def fn(s: ImportedShape) -> ImportedShape:
        g = affinity.scale(s.shape, xfact=-1, yfact=1, origin=(pos, 0)) if axis == "vertical" else \
            affinity.scale(s.shape, xfact=1, yfact=-1, origin=(0, pos))
        flipped = s.tabs
        if s.tabs and isinstance(s.shape, Polygon) and s.shape.exterior.is_ccw:       # orient() le da la vuelta al contorno
            flipped = reverse_tabs(s.tabs)
        return replace(s, shape=orient(g, 1.0) if isinstance(g, Polygon) else g, tabs=flipped)

    return _transform(shapes, picked, fn)


def _copies(shapes, picked: list[int], moves: list[Callable]) -> EditResult:
    if len(moves) * len(picked) > MAX_NEW:
        raise EditError(_("serian {v} copias; el maximo de una vez es {MAX_NEW}.", v=len(moves) * len(picked), MAX_NEW=MAX_NEW))
    out, origin = list(shapes), list(range(len(shapes)))
    for fn in moves:
        for i in picked:
            out.append(replace(shapes[i], shape=fn(shapes[i].shape), id=None))
            origin.append(i)
    return EditResult(out, origin, list(range(len(shapes), len(out))))


def _op_array(shapes, edit) -> EditResult:
    picked = _indices(edit, len(shapes))
    nx, ny = _count(edit.get("nx"), _("El numero de columnas")), _count(edit.get("ny"), _("El numero de filas"))
    dx, dy = _number(edit.get("dx"), _("La separacion en X")), _number(edit.get("dy"), _("La separacion en Y"))
    if nx * ny < 2:
        raise EditError(_("con una columna y una fila no hay ninguna copia que hacer."))
    offsets = [(i * dx, j * dy) for j in range(ny) for i in range(nx)]
    if len({(round(a, 9), round(b, 9)) for a, b in offsets}) < len(offsets):
        raise EditError(_("con esa separacion las copias caerian unas encima de otras."))
    moves = [(lambda g, a=a, b=b: affinity.translate(g, a, b)) for a, b in offsets[1:]]
    return _copies(shapes, picked, moves)


def _op_polar(shapes, edit) -> EditResult:
    picked = _indices(edit, len(shapes))
    n = _count(edit.get("n"), _("El numero de formas"), 2)
    total = _number(edit.get("grados"), _("El angulo total"), nonzero=True)
    c = (_number(edit.get("cx"), _("X del centro")), _number(edit.get("cy"), _("Y del centro")))
    if abs(total) > 360:
        raise EditError(_("el angulo total no puede pasar de 360 grados."))
    step = total / n if abs(total) == 360 else total / (n - 1)
    moves = [(lambda g, a=k * step: affinity.rotate(g, a, origin=c)) for k in range(1, n)]
    return _copies(shapes, picked, moves)


def _polygons(geom) -> list[Polygon]:
    """Los poligonos de un resultado de shapely (puede ser uno, varios o una coleccion con restos de lineas)."""
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon) or hasattr(geom, "geoms"):
        return [p for part in geom.geoms for p in _polygons(part)]
    return []


def _explode(geom, like: ImportedShape) -> list[ImportedShape]:
    """Un poligono con huecos sale como su contorno exterior y cada hueco como forma aparte (con el estilo de `like`)."""
    out: list[ImportedShape] = []
    for p in _polygons(shapely.simplify(geom, TIDY) if not geom.is_empty else geom):
        for ring in (p.exterior, *p.interiors):
            poly = Polygon(ring)
            if poly.is_valid and not poly.is_empty:
                shape = ImportedShape(orient(poly, 1.0), True, like.stroke, like.fill, like.layer, None, "path")
                if not is_degenerate(shape):
                    out.append(shape)
    return out


def _results(shapes, picked: list[int], new: list[tuple[int, list[ImportedShape]]], keep: bool) -> EditResult:
    """Lista nueva con los resultados al final: [(forma de la que vienen, formas nuevas)...]."""
    drop = set() if keep else set(picked)
    survivors = [i for i in range(len(shapes)) if i not in drop]
    out = [shapes[i] for i in survivors]
    origin: list[Optional[int]] = list(survivors)
    first = len(out)
    for src, made in new:
        out.extend(made)
        origin.extend([src] * len(made))
    return EditResult(out, origin, list(range(first, len(out))))


def _op_offset(shapes, edit) -> EditResult:
    picked = _indices(edit, len(shapes))
    d = _number(edit.get("distancia"), _("La distancia"), nonzero=True)
    corners = edit.get("esquinas", "redondas")
    if corners not in ("redondas", "vivas"):
        raise EditError(_("las esquinas del desfase son redondas o vivas."))
    keep = bool(edit.get("conservar", False))
    new: list[tuple[int, list[ImportedShape]]] = []
    empty = 0
    for i in picked:
        s = shapes[i]
        if not s.closed and d < 0:
            raise EditError(_("una linea abierta solo se puede desfasar hacia fuera (positivo): queda un contorno alrededor."))
        kwargs = dict(quad_segs=_quadrant(abs(d)), join_style="round" if corners == "redondas" else "mitre")
        if corners == "vivas":
            kwargs["mitre_limit"] = 5.0
        made = _explode(s.shape.buffer(d, **kwargs), s)
        if made:
            new.append((i, made))
        else:
            empty += 1
    if not new:
        raise EditError(_("el desfase de {d:g} mm no deja nada: las formas son mas pequenas que eso.", d=d))
    res = _results(shapes, picked, new, keep)
    if empty:
        res.notes.append(_("{empty} forma(s) desaparecen con un desfase de {d:g} mm (son mas pequenas) y no salen.", empty=empty, d=d))
    return res


def _closed_polys(shapes, picked: list[int], what: str) -> list[Polygon]:
    out = []
    for i in picked:
        s = shapes[i]
        if not s.closed or not isinstance(s.shape, Polygon):
            raise EditError(_("{what} solo vale con formas cerradas: entre las elegidas hay una linea abierta (se puede cerrar "
                              "con Dibujo > Cerrar contorno).", what=what))
        g = s.shape if s.shape.is_valid else shapely.make_valid(s.shape)
        out.append(g)
    return out


def _boolean(shapes, edit, what: str, combine: Callable) -> EditResult:
    picked = _indices(edit, len(shapes))
    if len(picked) < 2:
        raise EditError(_("para {what} hacen falta al menos dos formas elegidas.", what=what))
    geoms = _closed_polys(shapes, picked, what)
    base = combine(picked, geoms, edit)
    like = shapes[base[0]]
    made = _explode(base[1], like)
    if not made:
        raise EditError(_("el resultado esta vacio: no queda nada de las formas elegidas."))
    return _results(shapes, picked, [(base[0], made)], bool(edit.get("conservar", False)))


def _op_union(shapes, edit) -> EditResult:
    return _boolean(shapes, edit, "unir", lambda picked, geoms, e: (picked[0], unary_union(geoms)))


def _op_intersect(shapes, edit) -> EditResult:
    def combine(picked, geoms, e):
        g = geoms[0]
        for other in geoms[1:]:
            g = g.intersection(other)
        return picked[0], g

    return _boolean(shapes, edit, "intersecar", combine)


def _op_subtract(shapes, edit) -> EditResult:
    if edit.get("base", "primera") not in ("primera", "ultima"):
        raise EditError(_("la forma de la que se resta es la primera o la ultima de las elegidas."))

    def combine(picked, geoms, e):
        k = 0 if e.get("base", "primera") == "primera" else len(picked) - 1
        rest = [g for n, g in enumerate(geoms) if n != k]
        return picked[k], geoms[k].difference(unary_union(rest))

    return _boolean(shapes, edit, "restar", combine)


def _op_close(shapes, edit) -> EditResult:
    picked = _indices(edit, len(shapes))
    out, done, bad = list(shapes), [], 0
    for i in picked:
        s = shapes[i]
        if s.closed or not isinstance(s.shape, LineString):
            continue
        try:
            poly = _polygon(list(s.shape.coords), _("El contorno"))
        except EditError:
            bad += 1
            continue
        out[i] = replace(s, shape=poly, closed=True, kind="polygon")
        done.append(i)
    if not done:
        raise EditError(_("ninguna de las formas elegidas es una linea abierta que se pueda cerrar")
                        + (_(" (las que hay se cruzan o no encierran nada).") if bad else "."))
    res = EditResult(out, list(range(len(out))), done)
    if bad:
        res.notes.append(_("{bad} linea(s) no se pueden cerrar (se cruzan o no encierran nada) y se quedan abiertas.", bad=bad))
    return res


def _rect_corner(poly) -> Optional[float]:
    """El radio de las esquinas de un rectangulo con los lados paralelos a los ejes (0 si son vivas); None si la forma no es
    un rectangulo asi (girado, o con otra forma)."""
    if not isinstance(poly, Polygon) or poly.is_empty:
        return None
    x0, y0, x1, y1 = poly.bounds
    w, h = x1 - x0, y1 - y0
    tol = 1e-6 * max(1.0, w, h)
    pts = list(poly.exterior.coords)[:-1]
    bottom = [p[0] for p in pts if p[1] <= y0 + tol]
    top = [p[0] for p in pts if p[1] >= y1 - tol]
    left = [p[1] for p in pts if p[0] <= x0 + tol]
    right = [p[1] for p in pts if p[0] >= x1 - tol]
    if min(len(bottom), len(top), len(left), len(right)) < 1:
        return None
    radii = (min(bottom) - x0, x1 - max(bottom), min(top) - x0, x1 - max(top),
             min(left) - y0, y1 - max(left), min(right) - y0, y1 - max(right))
    r = radii[0]
    if any(abs(v - r) > 10 * tol for v in radii):                 # (con las ocho iguales, r nunca pasa de la mitad del lado menor)
        return None
    if abs(poly.area - (w * h - (4 - math.pi) * r * r)) > 0.02 * w * h:        # un octogono tambien tiene dos puntos por lado
        return None
    return round(max(r, 0.0), 9)


def _axis_ellipse(poly, cx: float, cy: float, rx: float, ry: float) -> bool:
    """True si todos los vertices estan sobre la elipse de esos semiejes con los ejes paralelos a los de la pagina."""
    if not isinstance(poly, Polygon) or rx <= 0 or ry <= 0:
        return False
    return all(abs(((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 - 1.0) <= 1e-3 for x, y in poly.exterior.coords)


def _op_measures(shapes, edit) -> EditResult:
    picked = _indices(edit, len(shapes))
    x, y = _number(edit.get("x"), "X"), _number(edit.get("y"), "Y")
    w, h = _number(edit.get("ancho"), _("El ancho")), _number(edit.get("alto"), _("El alto"))
    if w < 0 or h < 0:
        raise EditError(_("el ancho y el alto no pueden ser negativos."))
    x0, y0, x1, y1 = _bounds(shapes, picked)
    w0, h0 = x1 - x0, y1 - y0
    same_w, same_h = abs(w - w0) <= MEASURE_TOL, abs(h - h0) <= MEASURE_TOL
    if not same_w and w0 <= 0:
        raise EditError(_("lo elegido no tiene ancho (es una linea vertical): no se le puede dar un ancho."))
    if not same_h and h0 <= 0:
        raise EditError(_("lo elegido no tiene alto (es una linea horizontal): no se le puede dar un alto."))
    if (not same_w and w <= 0) or (not same_h and h <= 0):
        raise EditError(_("el ancho y el alto tienen que ser mayores que cero."))
    sx, sy = (1.0 if same_w else w / w0), (1.0 if same_h else h / h0)
    dx, dy = x - x0, y - y0
    if sx == 1.0 and sy == 1.0 and abs(dx) <= MEASURE_TOL and abs(dy) <= MEASURE_TOL:
        raise EditError(_("esas medidas son las que ya tiene: no cambia nada."))
    new_w, new_h = (w0 if same_w else w), (h0 if same_h else h)
    notes: list[str] = []
    out = list(shapes)
    if len(picked) == 1 and not (same_w and same_h):          # solo mover no rehace la forma: se queda tal cual
        s = shapes[picked[0]]
        rebuilt = None
        if _is_circle(s, w0, h0) and abs(new_w - new_h) <= MEASURE_TOL:
            d = new_w if not same_w else new_h
            rebuilt = make_circle(x + d / 2, y + d / 2, d / 2, stroke=None).shape
        elif s.kind == "rect":
            r = _rect_corner(s.shape)
            if r is not None:
                kept = min(r, min(new_w, new_h) / 2)
                if kept < r - 1e-9:
                    notes.append(_("El radio de las esquinas baja de {r:g} a {kept:g} mm: con ese tamano no cabe.", r=r, kept=kept))
                rebuilt = make_rectangle(x, y, new_w, new_h, kept, stroke=None).shape
        elif s.kind == "ellipse" and _axis_ellipse(s.shape, x0 + w0 / 2, y0 + h0 / 2, w0 / 2, h0 / 2):
            rebuilt = make_ellipse(x + new_w / 2, y + new_h / 2, new_w / 2, new_h / 2, stroke=None).shape
        if rebuilt is not None:
            out[picked[0]] = replace(s, shape=rebuilt, tabs=remap_tabs(s.shape, rebuilt, s.tabs) if s.tabs else s.tabs)
            res = EditResult(out, list(range(len(out))), picked)
            res.notes.extend(notes)
            return res

    def fn(s: ImportedShape) -> ImportedShape:
        g = affinity.scale(s.shape, xfact=sx, yfact=sy, origin=(x0, y0)) if (sx != 1.0 or sy != 1.0) else s.shape
        g = affinity.translate(g, dx, dy)
        kind = "ellipse" if s.kind == "circle" and abs(sx - sy) > 1e-12 else s.kind
        return replace(s, shape=g, kind=kind)

    return _transform(shapes, picked, fn)


def _op_tabs(shapes, edit) -> EditResult:
    picked = _indices(edit, len(shapes))
    raw = edit.get("puentes")
    try:
        tabs = None if raw is None else tabs_from_list(raw)
    except TabError as e:
        raise EditError(str(e)) from None
    out = list(shapes)
    changed = 0
    for i in picked:
        s = shapes[i]
        if tabs is not None and not (s.closed and isinstance(s.shape, Polygon)):
            raise EditError(_("los puentes solo se ponen en formas cerradas."))
        if s.tabs != tabs:
            out[i] = replace(s, tabs=tabs)
            changed += 1
    if not changed:
        raise EditError(_("esos puentes son los que ya tiene: no cambia nada."))
    return EditResult(out, list(range(len(out))), picked)


def _op_align(shapes, edit) -> EditResult:
    picked = _indices(edit, len(shapes))
    side = edit.get("lado")
    if side not in ALIGN_SIDES:
        raise EditError(_("no se puede alinear por {side!r}.", side=side))
    if len(picked) < 2:
        raise EditError(_("para alinear hacen falta al menos dos formas elegidas."))
    x0, y0, x1, y1 = _bounds(shapes, picked)
    moves: dict[int, tuple] = {}
    for i in picked:
        b = shapes[i].shape.bounds
        dx = dy = 0.0
        if side == "izquierda":
            dx = x0 - b[0]
        elif side == "derecha":
            dx = x1 - b[2]
        elif side == "centro_x":
            dx = (x0 + x1) / 2 - (b[0] + b[2]) / 2
        elif side == "abajo":
            dy = y0 - b[1]
        elif side == "arriba":
            dy = y1 - b[3]
        else:
            dy = (y0 + y1) / 2 - (b[1] + b[3]) / 2
        if abs(dx) > MEASURE_TOL or abs(dy) > MEASURE_TOL:           # las que ya estan alineadas no se tocan
            moves[i] = (dx, dy)
    if not moves:
        raise EditError(_("esas formas ya estan alineadas: no cambia nada."))
    out = list(shapes)
    for i, (dx, dy) in moves.items():
        out[i] = replace(shapes[i], shape=affinity.translate(shapes[i].shape, dx, dy))
    return EditResult(out, list(range(len(out))), picked)


OPS: dict[str, Callable] = {
    "anadir": _op_add, "borrar": _op_delete, "mover": _op_move, "girar": _op_rotate, "escalar": _op_scale,
    "reflejar": _op_mirror, "copiar": _op_array, "copiar_circular": _op_polar, "desfase": _op_offset,
    "unir": _op_union, "restar": _op_subtract, "intersecar": _op_intersect, "cerrar": _op_close,
    "medidas": _op_measures, "alinear": _op_align, "puentes": _op_tabs,
}

LABELS: dict[str, str] = {
    "anadir": _("Dibujar forma"), "borrar": _("Borrar"), "mover": _("Mover"), "girar": _("Girar"), "escalar": _("Cambiar tamano"),
    "reflejar": _("Reflejar"), "copiar": _("Copiar en matriz"), "copiar_circular": _("Copiar en circulo"), "desfase": _("Desfase"),
    "unir": _("Unir formas"), "restar": _("Restar formas"), "intersecar": _("Interseccion"), "cerrar": _("Cerrar contorno"),
    "medidas": _("Cambiar medidas"), "alinear": _("Alinear"), "puentes": _("Puentes"),
}


def apply_edit(shapes: Sequence[ImportedShape], edit: dict) -> EditResult:
    """La lista de formas tras la edicion. No toca `shapes`. Lanza EditError (con un mensaje para el usuario) si no se puede."""
    if not isinstance(edit, dict) or edit.get("op") not in OPS:
        raise EditError(_("operacion de dibujo desconocida: {alt!r}.", alt=edit.get('op') if isinstance(edit, dict) else edit))
    return OPS[edit["op"]](list(shapes), edit)


def edit_label(edit: dict) -> str:
    if not isinstance(edit, dict):
        return _("Edicion")
    if edit.get("op") == "alinear" and edit.get("lado") in ALIGN_LABELS:             # el deshacer dice por donde se alineo
        return ALIGN_LABELS[edit["lado"]]
    return LABELS.get(edit.get("op", ""), "Edicion")
