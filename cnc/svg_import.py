"""Importador de SVG (de cualquier aplicacion de dibujo vectorial) a formas shapely en mm.

Salida en coordenadas de pagina: mm, Y hacia arriba y origen en la esquina
inferior izquierda de la pagina (el SVG tiene Y hacia abajo). Con place() se
puede mover el origen despues de quitar notas o cotas.

Cada trazado se importa como una forma independiente (los trazados compuestos
se separan en sus subtrazados), con su color de trazo y relleno, su capa y su
id, para poder filtrar por color y asignar una operacion a cada vector.
"""
from __future__ import annotations

import io
import math
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional, Sequence, Union

import shapely
import svgelements as se
from shapely import affinity
from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry
from .i18n import _

MM_PER_INCH = 25.4
PPI = 96.0
LAYER_LABEL = "{http://www.inkscape.org/namespaces/inkscape}label"   # etiqueta de capa: espacio de nombres usado por varias aplicaciones
DEDUPE_TOL = 1e-3     # mm: puntos mas cercanos que esto son el mismo (ruido de algunos exportadores al cerrar trazados)
MIN_SUBDIVISIONS = 3   # profundidad minima de subdivision (evita curvas en S que parecen rectas)
MAX_SUBDIVISIONS = 14


@dataclass
class ImportedShape:
    shape: BaseGeometry              # Polygon (cerrado) o LineString (abierto), en mm
    closed: bool
    stroke: Optional[str] = None     # "#rrggbb" o None
    fill: Optional[str] = None
    layer: Optional[str] = None      # etiqueta o id del grupo/capa
    id: Optional[str] = None         # id del elemento (con sufijo -N en trazados compuestos)
    kind: str = "path"               # rect, circle, ellipse, path, polygon, polyline, line
    tabs: Optional[tuple] = None     # puentes puestos a mano (cnc.tabs.Tab; vacio: sin puentes); None: los propone el programa


@dataclass
class SvgImport:
    shapes: list[ImportedShape]
    page_size: tuple[float, float]               # ancho, alto de la pagina (mm)
    ignored: dict[str, int] = field(default_factory=dict)  # tipo -> cantidad (texto, imagen...)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        closed = sum(1 for s in self.shapes if s.closed)
        lines = [
            _("Pagina: {page_size:.1f} x {page_size2:.1f} mm", page_size=self.page_size[0], page_size2=self.page_size[1]),
            _("Formas: {len} ({closed} cerradas, {v} abiertas)", len=len(self.shapes), closed=closed, v=len(self.shapes) - closed),
        ]
        strokes = Counter(s.stroke or _("(sin trazo)") for s in self.shapes)
        lines.append(_("Colores de trazo: ") + ", ".join(f"{c} ({n})" for c, n in strokes.most_common()))
        layers = Counter(s.layer or _("(sin capa)") for s in self.shapes)
        lines.append(_("Capas: ") + ", ".join(f"{c} ({n})" for c, n in layers.most_common()))
        if self.ignored:
            lines.append(_("Ignorados: ") + ", ".join(f"{k} x{n}" for k, n in self.ignored.items()))
        lines += [_("AVISO: {w}", w=w) for w in self.warnings]
        return "\n".join(lines)


def _hex(color) -> Optional[str]:
    if color is None or getattr(color, "value", None) is None:
        return None
    return color.hex


def _flatten_segment(seg, tol_px: float, out: list) -> None:
    """Anade los puntos del segmento (sin repetir el inicial)."""
    if isinstance(seg, (se.Line, se.Close)):
        out.append((seg.end.x, seg.end.y))
        return

    def sag(p0, pm, p1) -> float:  # distancia de pm a la cuerda p0-p1
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        length = math.hypot(dx, dy)
        if length < 1e-12:
            return math.hypot(pm[0] - p0[0], pm[1] - p0[1])
        return abs(dx * (p0[1] - pm[1]) - dy * (p0[0] - pm[0])) / length

    def rec(t0, p0, t1, p1, depth):
        tm = (t0 + t1) / 2
        q = seg.point(tm)
        pm = (q.x, q.y)
        if depth >= MAX_SUBDIVISIONS or (depth >= MIN_SUBDIVISIONS and sag(p0, pm, p1) <= tol_px):
            out.append(p1)
            return
        rec(t0, p0, tm, pm, depth + 1)
        rec(tm, pm, t1, p1, depth + 1)

    a, b = seg.point(0.0), seg.point(1.0)
    rec(0.0, (a.x, a.y), 1.0, (b.x, b.y), 0)


def _subpaths(shape, tol_px: float):
    """Genera (puntos en px, cerrado) por cada subtrazado de la forma."""
    path = se.Path(shape)
    path.reify()
    for sub in path.as_subpaths():
        pts: list = []
        closed = False
        for seg in sub:
            if isinstance(seg, se.Move):
                pts = [(seg.end.x, seg.end.y)]
                continue
            if isinstance(seg, se.Close):
                closed = True
            if not pts:
                pts = [(seg.start.x, seg.start.y)]
            _flatten_segment(seg, tol_px, pts)
        if len(pts) >= 2:
            if not closed and math.dist(pts[0], pts[-1]) < 1e-6:
                closed = True
            yield pts, closed


def _dedupe(points: list, tol: float = DEDUPE_TOL) -> list:
    """Quita puntos consecutivos casi coincidentes (a menos de tol mm)."""
    out = [points[0]]
    for p in points[1:]:
        if math.dist(p, out[-1]) > tol:
            out.append(p)
    return out


def _walk(node, layer, shapes, ignored):
    for child in node:
        vals = getattr(child, "values", {}) or {}
        if vals.get("display") == "none" or vals.get("visibility") == "hidden":
            continue
        if isinstance(child, se.Group):
            _walk(child, vals.get(LAYER_LABEL) or child.id or layer, shapes, ignored)
        elif isinstance(child, se.Shape):
            shapes.append((child, layer))
        else:
            tag = vals.get("tag") or type(child).__name__.lower()
            if tag in ("metadata", "title", "desc", "defs", "style"):
                continue
            ignored[{"text": "texto", "image": "imagen"}.get(tag, tag)] += 1


def import_svg(
    source: Union[str, bytes, Path], tolerance: float = 0.02, ppi: float = PPI
) -> SvgImport:
    """Lee un SVG (ruta de archivo, texto o bytes) y devuelve sus formas en mm.

    tolerance: desviacion maxima (mm) al convertir curvas en segmentos.
    ppi: pixeles por pulgada para tamanos SIN unidades (la mayoria de las aplicaciones usan 96;
         algunas, 72). Si el SVG declara mm o cm, no influye.
    """
    mm_per_px = MM_PER_INCH / ppi
    if isinstance(source, (bytes, bytearray)):
        stream = io.BytesIO(bytes(source))
    elif isinstance(source, Path) or (isinstance(source, str) and not source.lstrip().startswith("<")):
        stream = str(source)
    else:
        stream = io.StringIO(source)
    doc = se.SVG.parse(stream, reify=True, ppi=ppi)

    warnings: list[str] = []
    ignored: Counter = Counter()
    found: list = []
    _walk(doc, None, found, ignored)

    try:
        page_w_px, page_h_px = float(doc.width), float(doc.height)
    except (TypeError, ValueError):
        page_w_px = page_h_px = None
    if not page_h_px or not math.isfinite(page_h_px):
        vb = doc.viewbox
        page_w_px, page_h_px = (vb.width, vb.height) if vb else (0.0, 0.0)
        warnings.append(_("El SVG no declara tamano de pagina valido; se usa el viewBox."))

    tol_px = tolerance / mm_per_px
    shapes: list[ImportedShape] = []
    for element, layer in found:
        kind = type(element).__name__.lower()
        kind = "line" if kind == "simpleline" else kind
        subs = list(_subpaths(element, tol_px))
        for n, (pts, closed) in enumerate(subs, start=1):
            mm = _dedupe([(x * mm_per_px, (page_h_px - y) * mm_per_px) for x, y in pts])
            ident = element.id if len(subs) == 1 or element.id is None else f"{element.id}-{n}"
            name = ident or kind
            if closed:
                while len(mm) > 1 and math.dist(mm[0], mm[-1]) < DEDUPE_TOL:
                    mm.pop()  # el punto final repite (casi) el inicial
                if len(mm) < 3:
                    continue
                geom = Polygon(mm)
                minx, miny, maxx, maxy = geom.bounds
                if maxx - minx < 1e-9 or maxy - miny < 1e-9:
                    warnings.append(_("Forma sin area ignorada ({name}).", name=name))
                    continue
                if not geom.is_valid:
                    # un cruce minimo se repara solo si el area no cambia; un lazo (area 0) no
                    c = geom.representative_point()
                    name = _("{name} (cerca de X {x:.0f}, Y {y:.0f} mm de la pagina)", name=name, x=c.x, y=c.y)
                    fixed = geom.buffer(0)
                    if (
                        isinstance(fixed, Polygon)
                        and not fixed.is_empty
                        and abs(fixed.area - geom.area) <= 1e-3 * fixed.area
                    ):
                        geom = fixed
                        warnings.append(_("La forma {name} tenia un cruce minimo y se ha reparado.", name=name))
                    else:
                        warnings.append(_("La forma {name} se cruza consigo misma.", name=name))
            else:
                if len(mm) < 2:
                    continue
                geom = LineString(mm)
            shapes.append(
                ImportedShape(
                    shape=geom,
                    closed=closed,
                    stroke=_hex(element.stroke),
                    fill=_hex(element.fill),
                    layer=layer,
                    id=ident,
                    kind=kind,
                )
            )

    keys = Counter(
        shapely.to_wkb(shapely.normalize(shapely.set_precision(s.shape, 1e-3, mode="pointwise")))
        for s in shapes
    )
    duplicates = sum(n - 1 for n in keys.values() if n > 1)
    if duplicates:
        warnings.append(_("{duplicates} forma(s) duplicada(s) (mismo contorno encima de otro).", duplicates=duplicates))
    if not shapes:
        warnings.append(_("No se ha encontrado ninguna forma."))

    return SvgImport(
        shapes=shapes,
        page_size=(page_w_px * mm_per_px, page_h_px * mm_per_px),
        ignored=dict(ignored),
        warnings=warnings,
    )


def place(
    shapes: Sequence[ImportedShape],
    origin: str = "bbox_bottom_left",
    reference: Optional[Sequence[ImportedShape]] = None,
) -> list[ImportedShape]:
    """Mueve las formas para fijar el origen del diseno (mm).

    - page_bottom_left: sin cambios (esquina inferior izquierda de la pagina).
    - bbox_bottom_left: esquina inferior izquierda del conjunto de formas.
    - center: centro del conjunto de formas.
    Con reference (p. ej. el contorno del tablero), el origen se calcula sobre esas formas
    en vez de sobre las que se mueven: asi el cero queda en la esquina del tablero.
    """
    shapes = list(shapes)
    if origin == "page_bottom_left" or not shapes:
        return shapes
    bounds = [s.shape.bounds for s in (list(reference) if reference else shapes)]
    minx, miny = min(b[0] for b in bounds), min(b[1] for b in bounds)
    maxx, maxy = max(b[2] for b in bounds), max(b[3] for b in bounds)
    if origin == "bbox_bottom_left":
        dx, dy = -minx, -miny
    elif origin == "center":
        dx, dy = -(minx + maxx) / 2, -(miny + maxy) / 2
    else:
        raise ValueError(_("origin desconocido: {origin!r}", origin=origin))
    return [replace(s, shape=affinity.translate(s.shape, dx, dy)) for s in shapes]


def rotate(shapes: Sequence[ImportedShape], degrees: float) -> list[ImportedShape]:
    """Gira las formas (grados, antihorario) alrededor del centro del conjunto.

    Util cuando el lado largo del diseno no cabe en el eje corto de la maquina.
    Despues conviene volver a llamar a place() para fijar el origen.
    """
    shapes = list(shapes)
    if not shapes or degrees % 360 == 0:
        return shapes
    bounds = [s.shape.bounds for s in shapes]
    cx = (min(b[0] for b in bounds) + max(b[2] for b in bounds)) / 2
    cy = (min(b[1] for b in bounds) + max(b[3] for b in bounds)) / 2
    return [replace(s, shape=affinity.rotate(s.shape, degrees, origin=(cx, cy))) for s in shapes]


def shift(shapes: Sequence[ImportedShape], dx: float, dy: float) -> list[ImportedShape]:
    """Mueve todas las formas (mm); sirve para correr el dibujo respecto al cero."""
    return [replace(s, shape=affinity.translate(s.shape, dx, dy)) for s in shapes]


def is_degenerate(shape: ImportedShape, min_length: float = 0.5, min_area: float = 0.01) -> bool:
    """Restos sin sentido que dejan algunos exportadores: lineas de menos de 0,5 mm o poligonos de menos de 0,01 mm2."""
    g = shape.shape
    if g.is_empty:
        return True
    if shape.closed and isinstance(g, Polygon):
        return g.area < min_area
    return g.length < min_length


def nesting_levels(shapes: Sequence[ImportedShape]) -> list[Optional[int]]:
    """Nivel de anidamiento de cada forma cerrada: 0 exterior, 1 dentro de otra, 2 isla dentro de un hueco...

    None para las formas abiertas.
    """
    polys = [
        (i, s.shape) for i, s in enumerate(shapes) if s.closed and isinstance(s.shape, Polygon)
    ]
    levels: list[Optional[int]] = [None] * len(shapes)
    for i, pi in polys:
        levels[i] = sum(
            1 for j, pj in polys if j != i and pj.area > pi.area * (1 + 1e-9) and pj.contains(pi)
        )
    return levels


def suggest_sides(shapes: Sequence[ImportedShape]) -> list[str]:
    """Sugerencia de lado de corte por forma (para revisar, no para aplicar a ciegas).

    Nivel par -> 'outside' (es una pieza), nivel impar -> 'inside' (es un hueco),
    abierta -> 'on' (seguir la linea).
    """
    return [
        "on" if lvl is None else ("outside" if lvl % 2 == 0 else "inside")
        for lvl in nesting_levels(shapes)
    ]
