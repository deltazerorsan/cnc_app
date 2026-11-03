"""Tableros: convencion para SVG con varios materiales en la misma pagina.

- Un **rectangulo que contiene otras formas** es un tablero (el material). Cada tablero es un
  trabajo, un .nc. El rectangulo no se corta y el cero queda en su esquina inferior izquierda.
- Una **elipse (o circulo) que contiene algun tablero** es una agrupacion visual y se descarta.
- Las formas que no estan dentro de ningun tablero no se cortan (no tendrian cero propio). Una forma
  cuenta como del tablero si al menos la mitad cae dentro: una pieza que sobresale un poco sigue siendo
  suya (el programa avisa luego de que la trayectoria se sale del material).

Un rectangulo con huecos dentro es indistinguible de un tablero con piezas: si ese es el caso,
hay que desactivar la convencion (--no-boards).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from shapely.geometry import Polygon, box

from .svg_import import ImportedShape, suggest_sides
from .i18n import _

RECT_TOL = 1e-6       # diferencia relativa de area con su caja para considerarlo rectangulo
CONTAIN_TOL = 1e-4    # mm de holgura al comprobar que una forma esta dentro de otra
COLUMN_TOL = 50.0     # mm: tableros cuyo borde izquierdo difiere menos que esto van en la misma columna
MEMBER_SHARE = 0.5    # una forma es del tablero si al menos esta parte de ella cae dentro (aunque sobresalga algo)
GROUP_KINDS = ("ellipse", "circle")


@dataclass
class Board:
    number: int                      # 1, 2, 3... en orden de lectura (ver _reading_order)
    index: int                       # posicion del rectangulo en la lista de formas
    contents: list[int]              # posiciones de las formas que contiene (en orden de dibujo)
    bounds: tuple[float, float, float, float]

    @property
    def width(self) -> float:
        return self.bounds[2] - self.bounds[0]

    @property
    def height(self) -> float:
        return self.bounds[3] - self.bounds[1]


@dataclass
class BoardLayout:
    boards: list[Board] = field(default_factory=list)
    groupings: list[int] = field(default_factory=list)   # elipses de agrupacion (se descartan)
    loose: list[int] = field(default_factory=list)       # formas fuera de todo tablero (no se cortan)


def is_rectangle(shape: ImportedShape) -> bool:
    """Poligono cerrado, sin huecos, que ocupa toda su caja (rectangulo con los lados en los ejes)."""
    g = shape.shape
    if not shape.closed or not isinstance(g, Polygon) or g.interiors or g.area <= 0:
        return False
    return g.symmetric_difference(box(*g.bounds)).area <= RECT_TOL * g.area


def _inside(outer: ImportedShape, inner: ImportedShape) -> bool:
    return outer.shape.buffer(CONTAIN_TOL).contains(inner.shape)


def _share(outer: ImportedShape, inner: ImportedShape) -> float:
    """Parte de la forma (por area; por longitud si es una linea) que cae dentro del rectangulo."""
    g = inner.shape
    by_area = g.area > 0
    whole = g.area if by_area else g.length
    if whole <= 0:
        return 0.0
    part = g.intersection(outer.shape)
    return (part.area if by_area else part.length) / whole


def _reading_order(boards: list[Board]) -> list[Board]:
    """De izquierda a derecha por columnas (bordes izquierdos a menos de COLUMN_TOL) y, dentro de una
    columna, de arriba a abajo."""
    if not boards:
        return []
    by_left = sorted(boards, key=lambda b: b.bounds[0])
    columns: list[list[Board]] = [[by_left[0]]]
    for b in by_left[1:]:
        if b.bounds[0] - columns[-1][-1].bounds[0] < COLUMN_TOL:
            columns[-1].append(b)
        else:
            columns.append([b])
    return [b for col in columns for b in sorted(col, key=lambda b: -b.bounds[3])]


def detect_boards(shapes: Sequence[ImportedShape]) -> BoardLayout:
    shapes = list(shapes)
    rects = [i for i, s in enumerate(shapes) if is_rectangle(s)]
    contained = {
        i: [j for j, t in enumerate(shapes) if j != i and _inside(shapes[i], t)] for i in rects
    }
    candidates = [i for i in rects if contained[i]]
    # miembros de un tablero: lo que cae dentro por completo y lo que solo sobresale un poco
    members = {
        i: [j for j, t in enumerate(shapes) if j != i and (j in contained[i] or _share(shapes[i], t) >= MEMBER_SHARE)]
        for i in candidates
    }
    # un candidato dentro de otro candidato es una pieza rectangular, no un tablero
    top = [
        i for i in candidates
        if not any(k != i and _inside(shapes[k], shapes[i]) for k in candidates)
    ]
    groupings = [
        i for i, s in enumerate(shapes)
        if s.kind in GROUP_KINDS and s.closed and i not in top
        and any(_inside(s, shapes[b]) for b in top)
    ]
    ignored = set(top) | set(groupings)
    boards = [
        Board(0, i, [j for j in members[i] if j not in ignored], shapes[i].shape.bounds) for i in top
    ]
    boards = _reading_order(boards)
    for n, b in enumerate(boards, start=1):
        b.number = n
    placed = {j for b in boards for j in b.contents}
    loose = [i for i in range(len(shapes)) if i not in ignored and i not in placed]
    return BoardLayout(boards=boards, groupings=groupings, loose=loose)


def describe(layout: BoardLayout, shapes: Sequence[ImportedShape], recipe=None) -> str:
    """Tabla de tableros para --list-boards. Con receta, las columnas son sus operaciones (formas de cada una);
    sin ella, piezas y huecos por anidamiento."""
    if not layout.boards:
        return _("No se ha encontrado ningun tablero (rectangulo que contenga formas).")
    if recipe is None:
        head = ("n", _("tamano (mm)"), _("posicion X,Y (mm)"), "piezas", "huecos")
    else:
        from .recipe import count_by_operation

        head = ("n", _("tamano (mm)"), _("posicion X,Y (mm)"), *(op.name for op in recipe.operations))
    rows = []
    for b in layout.boards:
        inner = [shapes[j] for j in b.contents]
        base = (str(b.number), _("{width:.0f} x {height:.0f}", width=b.width, height=b.height), f"{b.bounds[0]:.0f}, {b.bounds[1]:.0f}")
        if recipe is None:
            sides = suggest_sides(inner)
            rows.append((*base, str(sides.count("outside")), str(sides.count("inside"))))
        else:
            rows.append((*base, *(str(n) for n in count_by_operation(recipe, inner).values())))
    widths = [max(len(r[i]) for r in [head, *rows]) for i in range(len(head))]
    fmt = lambda r: "  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip()
    lines = [fmt(head), fmt(tuple("-" * w for w in widths))] + [fmt(r) for r in rows]
    if layout.groupings:
        lines.append(_("\n{len} elipse(s) de agrupacion descartada(s).", len=len(layout.groupings)))
    if layout.loose:
        lines.append(_("{len} forma(s) fuera de todo tablero (no se cortan).", len=len(layout.loose)))
    return "\n".join(lines)
