"""Dibujar y mover con el raton, sin Qt: el iman (los puntos a los que se pega el cursor), las restricciones con Mayus,
el estado de cada herramienta (cuantos puntos lleva y que forma sale) y los textos de medida en vivo.

La capa de Qt (`cnc.gui.draw_layer`) solo traduce el raton a puntos de dibujo, dibuja la vista previa y entrega lo que
sale de aqui; todo lo que hay que acertar esta en este modulo y tiene sus pruebas sin pantalla.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np
from shapely.geometry import LineString, Polygon

from . import drawing_edit as de
from .svg_import import ImportedShape
from .i18n import _

SNAP_PIXELS = 10.0        # a cuantos pixeles del cursor se pega el iman
CORNER_DEG = 12.0         # un vertice que gira menos que esto es parte de una curva, no una esquina
MAX_MIDS = 400            # una forma con mas esquinas que estas no ofrece puntos medios (seria ruido)
DECIMALS = 2              # un punto libre se redondea a la centesima de mm

TOOLS = ("elegir", "rectangulo", "circulo", "elipse", "poligono", "ranura", "linea", "mover", "puente")
LABELS = {"elegir": _("Elegir"), "rectangulo": _("Rectángulo"), "circulo": _("Círculo"), "elipse": _("Elipse"),
          "poligono": _("Polígono"), "ranura": _("Ranura"), "linea": _("Línea"), "mover": _("Mover"), "puente": _("Puente")}
KEYS = {"v": "elegir", "r": "rectangulo", "c": "circulo", "e": "elipse", "p": "poligono", "s": "ranura",
        "l": "linea", "m": "mover", "b": "puente"}
NEEDS = {"rectangulo": 2, "circulo": 2, "elipse": 2, "poligono": 2, "ranura": 2, "mover": 2, "linea": 0, "puente": 0}
CONSTRAINT = {"rectangulo": "cuadrado", "elipse": "cuadrado", "linea": 45.0, "ranura": 45.0, "mover": 45.0,
              "poligono": 15.0}       # lo que hace Mayus en cada herramienta: un cuadrado o girar de N en N grados

KINDS = ("esquina", "inicio", "cuadrante", "centro", "medio", "caja", "cero")     # de mas a menos importante
_RANK = {"esquina": 0, "inicio": 0, "cuadrante": 1, "centro": 2, "medio": 3, "caja": 4, "cero": 5}
_RANKS = np.array([_RANK[k] for k in KINDS], dtype=np.int64)

HINTS = {
    "rectangulo": (_("Rectángulo: clic en una esquina (o arrastra hasta la opuesta)."),
                   _("Rectángulo: clic en la esquina opuesta · Mayús: cuadrado.")),
    "circulo": (_("Círculo: clic en el centro (o arrastra hasta el borde)."), _("Círculo: clic donde acaba el radio.")),
    "elipse": (_("Elipse: clic en una esquina de su caja (o arrastra hasta la opuesta)."),
               _("Elipse: clic en la esquina opuesta · Mayús: círculo.")),
    "poligono": (_("Polígono: clic en el centro (o arrastra hasta un vértice)."),
                 _("Polígono: clic en un vértice (da el tamaño y el giro) · Mayús: giro de 15 en 15°.")),
    "ranura": (_("Ranura: clic en el centro de un extremo (o arrastra hasta el otro)."),
               _("Ranura: clic en el centro del otro extremo · Mayús: 0°, 45°, 90°…")),
    "linea": (_("Línea: clic en el primer punto."),
              _("Línea: clic en cada punto; doble clic o Intro acaba, clic en el inicio cierra el contorno · "
              "Retroceso quita el último · Mayús: 0°, 45°, 90°…")),
    "puente": (_("Puente: clic en un contorno para poner un puente; arrastra uno para deslizarlo o tira de sus extremos para "
                 "cambiar su ancho · Supr lo quita."),
               _("Puente: suelta donde quieras el puente.")),
    "mover": (_("Mover: clic en el punto de partida (una esquina o el centro de lo elegido; con varias formas, de su rectángulo "
                "circunscrito), o arrastra desde él."),
              _("Mover: clic en el punto de llegada (se pega a esquinas y centros de otras formas) · Mayús: 0°, 45°, 90°…")),
}


@dataclass(frozen=True)
class Options:
    """Lo que no se puede dar con el raton: lo piden los botones de opciones."""
    radius: float = 0.0          # esquinas del rectangulo (mm)
    sides: int = 6               # lados del poligono
    slot_width: float = 8.0      # ancho de la ranura (mm)
    grid: float = 0.0            # paso de la cuadricula (mm); 0 = sin cuadricula
    snap: bool = True            # iman a esquinas, centros y puntos medios

    def __post_init__(self):
        if not (self.radius >= 0 and self.grid >= 0 and self.slot_width > 0):
            raise ValueError(_("el radio y la cuadrícula no pueden ser negativos y el ancho de la ranura tiene que ser positivo."))
        if not (isinstance(self.sides, int) and self.sides >= 3):
            raise ValueError(_("un polígono necesita al menos 3 lados."))


@dataclass(frozen=True)
class SnapPoint:
    x: float
    y: float
    kind: str
    shape: int = -1              # indice de la forma de la que viene (-1: ninguna)


@dataclass(frozen=True)
class Spot:
    """Donde cae el cursor una vez aplicados el iman y las restricciones."""
    x: float
    y: float
    kind: str = ""               # clase del punto al que se pego ("" si es un punto libre)

    @property
    def xy(self) -> tuple:
        return self.x, self.y


@dataclass(frozen=True)
class Done:
    kind: str                    # "forma" (hay una forma nueva) o "mover"
    shape: Optional[ImportedShape] = None
    dx: float = 0.0
    dy: float = 0.0
    indices: tuple = ()


@dataclass(frozen=True)
class Draft:
    """Lo que se ve mientras se mueve el cursor."""
    shape: Optional[ImportedShape] = None     # la forma que saldria
    path: tuple = ()                          # linea de guia (polilinea en curso, o de la salida a la llegada)
    shift: Optional[tuple] = None             # (dx, dy) de lo que se mueve
    text: str = ""                            # las medidas
    closes: bool = False                      # el cursor esta en el inicio de un contorno que se cerraria


# --- numeros y textos --------------------------------------------------------------------------------------------
def fmt(v: float) -> str:
    """Un numero para leer: hasta dos decimales, sin ceros de mas."""
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def angle_of(a: Sequence[float], b: Sequence[float]) -> float:
    """Grados (0 a 360, antihorario, 0 hacia la derecha) de la direccion de a hacia b."""
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 360.0


def coord_text(spot: Spot) -> str:
    return _("X {fmt}  Y {fmt2}", fmt=fmt(spot.x), fmt2=fmt(spot.y)) + (f" · {spot.kind}" if spot.kind else "")


# --- puntos de iman ------------------------------------------------------------------------------------------------
def _parts(geom) -> list:
    """(puntos, cerrado) de cada anillo o linea de una geometria."""
    if isinstance(geom, Polygon):
        if geom.is_empty:
            return []
        return [(np.asarray(r.coords)[:-1], True) for r in (geom.exterior, *geom.interiors)]
    if isinstance(geom, LineString):
        return [] if geom.is_empty else [(np.asarray(geom.coords), False)]
    return [p for g in getattr(geom, "geoms", ()) for p in _parts(g)]


def _turning(pts: np.ndarray, closed: bool) -> np.ndarray:
    """Grados que gira el contorno en cada vertice; los extremos de una linea abierta cuentan como esquina."""
    a = pts - np.roll(pts, 1, axis=0)
    b = np.roll(pts, -1, axis=0) - pts
    turn = np.degrees(np.abs(np.arctan2(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0], (a * b).sum(axis=1))))
    if not closed:
        turn[0] = turn[-1] = 180.0
    return turn


def shape_snaps(s: ImportedShape) -> list:
    """Los puntos de iman de una forma: (x, y, clase). Esquinas de verdad (no los vertices de una curva), puntos medios
    de los lados rectos, el centro de la caja de lo cerrado y, en circulos y elipses, los cuatro cuadrantes."""
    g = s.shape
    smooth = s.kind in ("circle", "ellipse")
    out: list = []
    for pts, closed in _parts(g):
        if len(pts) < 2 or smooth:
            continue
        sharp = _turning(pts, closed) >= CORNER_DEG
        out += [(float(x), float(y), "esquina") for x, y in pts[sharp]]
        if int(sharp.sum()) <= MAX_MIDS:
            both = sharp & np.roll(sharp, -1)
            if not closed:
                both[-1] = False
            mids = (pts + np.roll(pts, -1, axis=0)) / 2
            out += [(float(x), float(y), "medio") for x, y in mids[both]]
    if isinstance(g, Polygon) and not g.is_empty:
        x0, y0, x1, y1 = g.bounds
        out.append(((x0 + x1) / 2, (y0 + y1) / 2, "centro"))
        ring = np.asarray(g.exterior.coords)
        if smooth:
            for k in (ring[:, 0].argmin(), ring[:, 0].argmax(), ring[:, 1].argmin(), ring[:, 1].argmax()):
                out.append((float(ring[k, 0]), float(ring[k, 1]), "cuadrante"))
        if s.kind == "rect":                                # un rectangulo con esquinas redondas sigue teniendo las suyas
            out += [(x, y, "esquina") for x in (x0, x1) for y in (y0, y1)]
            out += [((x0 + x1) / 2, y0, "medio"), ((x0 + x1) / 2, y1, "medio"),
                    (x0, (y0 + y1) / 2, "medio"), (x1, (y0 + y1) / 2, "medio")]
    return list(dict.fromkeys((round(x, 6), round(y, 6), k) for x, y, k in out))


def box_points(bounds: Sequence[float]) -> list:
    """Las nueve asas de una caja (esquinas, mitades de los lados y centro): de donde agarrar lo que se mueve."""
    x0, y0, x1, y1 = bounds
    return [SnapPoint(x, y, "caja") for x in (x0, (x0 + x1) / 2, x1) for y in (y0, (y0 + y1) / 2, y1)]


class SnapMap:
    """Todos los puntos de iman de un dibujo, listos para preguntar cual cae mas cerca de un punto."""

    def __init__(self):
        self._cache: dict = {}
        self._key = None
        self._x = self._y = np.empty(0)
        self._code = self._shape = np.empty(0, dtype=np.int64)

    def __len__(self) -> int:
        return int(self._x.size)

    def update(self, shapes: Sequence[ImportedShape]) -> None:
        """Pone al dia los puntos con las formas de ahora; de las que no han cambiado se aprovecha lo ya calculado."""
        key = tuple((id(s.shape), s.kind) for s in shapes)
        if key == self._key:
            return
        fresh, xy, codes, owners = {}, [], [], []
        for i, s in enumerate(shapes):
            k = (id(s.shape), s.kind)
            entry = self._cache.get(k)
            if entry is None or entry[0] is not s.shape:
                pts = shape_snaps(s)
                entry = (s.shape, np.array([(x, y) for x, y, __ in pts], dtype=float).reshape(-1, 2),
                         np.array([KINDS.index(c) for __, __, c in pts], dtype=np.int64))
            fresh[k] = entry
            xy.append(entry[1])
            codes.append(entry[2])
            owners.append(np.full(len(entry[2]), i, dtype=np.int64))
        xy.append(np.zeros((1, 2)))                                 # el cero del dibujo
        codes.append(np.array([KINDS.index("cero")], dtype=np.int64))
        owners.append(np.array([-1], dtype=np.int64))
        flat = np.concatenate(xy)
        self._x, self._y = flat[:, 0].copy(), flat[:, 1].copy()
        self._code, self._shape = np.concatenate(codes), np.concatenate(owners)
        self._cache, self._key = fresh, key

    def _point(self, j: int) -> SnapPoint:
        return SnapPoint(float(self._x[j]), float(self._y[j]), KINDS[int(self._code[j])], int(self._shape[j]))

    def nearest(self, x: float, y: float, radius: float, exclude=(), extra: Sequence[SnapPoint] = (),
                main: bool = True) -> Optional[SnapPoint]:
        """El punto de iman mas cercano a (x, y) a `radius` mm o menos, o None. `exclude`: formas que no cuentan;
        `extra`: puntos que no son de ninguna forma (el inicio de una linea, las asas de lo que se mueve); `main`:
        False para mirar solo `extra`. A igual distancia gana la clase mas importante."""
        if not radius > 0:
            return None
        r2 = radius * radius
        found: list = []
        if main and self._x.size:
            d2 = (self._x - x) ** 2 + (self._y - y) ** 2
            ok = d2 <= r2
            if len(exclude):
                ok &= ~np.isin(self._shape, np.fromiter(exclude, dtype=np.int64))
            hits = np.flatnonzero(ok)
            if hits.size:
                best = float(d2[hits].min())
                tied = hits[d2[hits] <= best + best * 1e-9 + 1e-18]
                j = int(tied[np.argmin(_RANKS[self._code[tied]])])
                found.append((float(d2[j]), int(_RANKS[self._code[j]]), self._point(j)))
        for p in extra:
            d = (p.x - x) ** 2 + (p.y - y) ** 2
            if d <= r2:
                found.append((d, _RANK[p.kind], p))
        if not found:
            return None
        m = min(f[0] for f in found)
        return min((f for f in found if f[0] <= m + m * 1e-9 + 1e-18), key=lambda f: (f[1], f[0]))[2]


# --- restricciones y redondeo -------------------------------------------------------------------------------------
def _unit(a: float) -> tuple:
    return round(math.cos(a), 12), round(math.sin(a), 12)


def constrain(tool: str, origin: Sequence[float], point: Sequence[float]) -> tuple:
    """El punto con la restriccion de Mayus de la herramienta: un cuadrado (rectangulo, elipse) o la direccion
    redondeada a 45 grados (15 en el poligono), con la longitud redondeada a la centesima."""
    how = CONSTRAINT[tool]
    ox, oy = origin
    dx, dy = point[0] - ox, point[1] - oy
    if how == "cuadrado":
        s = round(max(abs(dx), abs(dy)), DECIMALS)
        return ox + (-s if dx < 0 else s), oy + (-s if dy < 0 else s)
    d = round(math.hypot(dx, dy), DECIMALS)
    step = math.radians(how)
    c, s = _unit(round(math.atan2(dy, dx) / step) * step)
    return ox + d * c, oy + d * s


def free_value(v: float, grid: float = 0.0) -> float:
    """Una coordenada libre: a la cuadricula si la hay, y si no a la centesima de mm."""
    if grid > 0:
        return round(round(v / grid) * grid, 6)
    return round(v, DECIMALS)


def resolve(tool: str, raw: Sequence[float], origin: Optional[Sequence[float]], options: Options, snaps: SnapMap,
            radius: float, *, shift: bool = False, ctrl: bool = False, exclude=(), extra: Sequence[SnapPoint] = ()) -> Spot:
    """Donde cae de verdad un punto del raton. Mayus (con un punto de partida): la restriccion de la herramienta, sin
    iman. Ctrl: sin iman. Si no, el punto de iman mas cercano (`extra`, como el inicio de una linea que se cierra, vale
    aunque el iman este apagado) y, si no hay, el punto libre redondeado."""
    if shift and origin is not None and tool in CONSTRAINT:
        x, y = constrain(tool, origin, raw)
        return Spot(x, y)
    if not ctrl:
        hit = snaps.nearest(raw[0], raw[1], radius, exclude, extra, main=options.snap)
        if hit is not None:
            return Spot(hit.x, hit.y, hit.kind)
    return Spot(free_value(raw[0], options.grid), free_value(raw[1], options.grid))


# --- una herramienta en marcha ------------------------------------------------------------------------------------
class Session:
    """Los puntos que lleva una herramienta y lo que sale de ellos. `add` da el siguiente punto y devuelve `Done` cuando
    ya hay forma (o movimiento); si no se puede hacer lanza `EditError` y todo queda como estaba."""

    def __init__(self, tool: str, options: Options = Options(), stroke: Optional[str] = "#000000", selection=()):
        if tool not in NEEDS:
            raise ValueError(_("herramienta desconocida: {tool!r}", tool=tool))
        self.tool, self.options, self.stroke = tool, options, stroke
        self.selection = tuple(sorted(selection))
        self.points: list = []

    @property
    def busy(self) -> bool:
        return bool(self.points)

    @property
    def origin(self) -> Optional[tuple]:
        return self.points[-1] if self.points else None

    def extras(self) -> list:
        """Puntos de iman que solo existen mientras se dibuja: el inicio de una linea con tres puntos o mas (para cerrarla)."""
        if self.tool == "linea" and len(self.points) >= 3:
            return [SnapPoint(*self.points[0], "inicio")]
        return []

    def hint(self) -> str:
        first, second = HINTS[self.tool]
        if self.points:
            return second + _(" · Esc: cancelar")
        return first + _(" · Ctrl: sin imán · Esc: salir")

    def cancel(self) -> None:
        self.points = []

    def back(self) -> bool:
        """Quita el ultimo punto; False si no habia."""
        if not self.points:
            return False
        self.points.pop()
        return True

    # --- dar puntos ---
    def add(self, p: Sequence[float]) -> Optional[Done]:
        p = (float(p[0]), float(p[1]))
        if self.tool == "linea":
            return self._add_line(p)
        if not self.points:
            self.points.append(p)
            return None
        a = self.points[0]
        if math.dist(a, p) <= de.MIN_VERTEX:
            raise de.EditError(_("los dos puntos coinciden: el segundo clic tiene que estar en otro sitio."))
        if self.tool == "mover":
            done = Done("mover", dx=p[0] - a[0], dy=p[1] - a[1], indices=self.selection)
        else:
            done = Done("forma", shape=self._shape(a, p))
        self.points = []
        return done

    def _add_line(self, p: tuple) -> Optional[Done]:
        pts = self.points
        if pts and math.dist(pts[-1], p) <= de.MIN_VERTEX:
            return None                                              # el mismo sitio dos veces (un doble clic)
        if len(pts) >= 3 and math.dist(pts[0], p) <= de.MIN_VERTEX:
            return self._end_line(True)
        pts.append(p)
        return None

    def finish(self) -> Optional[Done]:
        """Acaba la linea que se dibuja (abierta). Solo la herramienta Linea; None si no hay nada que acabar."""
        if self.tool != "linea" or not self.points:
            return None
        if len(self.points) < 2:
            raise de.EditError(_("una línea necesita al menos dos puntos."))
        return self._end_line(False)

    def _end_line(self, closed: bool) -> Done:
        done = Done("forma", shape=de.make_polyline(self.points, closed, stroke=self.stroke))
        self.points = []
        return done

    # --- lo que sale ---
    def _shape(self, a: tuple, c: tuple) -> ImportedShape:
        t, o = self.tool, self.options
        w, h = abs(c[0] - a[0]), abs(c[1] - a[1])
        if t == "rectangulo":
            return de.make_rectangle(min(a[0], c[0]), min(a[1], c[1]), w, h, min(o.radius, w / 2, h / 2),
                                     stroke=self.stroke)
        if t == "circulo":
            return de.make_circle(a[0], a[1], math.dist(a, c), stroke=self.stroke)
        if t == "elipse":
            return de.make_ellipse((a[0] + c[0]) / 2, (a[1] + c[1]) / 2, w / 2, h / 2, 0.0, stroke=self.stroke)
        if t == "poligono":
            return de.make_polygon(a[0], a[1], o.sides, math.dist(a, c), angle_of(a, c), "radio", stroke=self.stroke)
        return de.make_slot(a[0], a[1], c[0], c[1], o.slot_width, stroke=self.stroke)

    def draft(self, cursor: Sequence[float]) -> Draft:
        """Lo que se veria si se diera ahora el siguiente punto en `cursor`."""
        c = (float(cursor[0]), float(cursor[1]))
        if not self.points:
            return Draft()
        a = self.points[0]
        if self.tool == "linea":
            return self._line_draft(c)
        if math.dist(a, c) <= de.MIN_VERTEX:
            return Draft()
        if self.tool == "mover":
            dx, dy = c[0] - a[0], c[1] - a[1]
            return Draft(path=(a, c), shift=(dx, dy), text=_("ΔX {fmt} · ΔY {fmt2} mm", fmt=fmt(dx), fmt2=fmt(dy)))
        try:
            shape = self._shape(a, c)
        except de.EditError:
            shape = None                                             # p. ej. de ancho cero: se ve la guia y no la forma
        return Draft(shape=shape, text=self._text(a, c))

    def _line_draft(self, c: tuple) -> Draft:
        pts = list(self.points)
        closes = len(pts) >= 3 and math.dist(pts[0], c) <= de.MIN_VERTEX
        if math.dist(pts[-1], c) > de.MIN_VERTEX:
            pts.append(c)
        total = sum(math.dist(p, q) for p, q in zip(pts, pts[1:]))
        text = _("{fmt} mm · {fmt2}°", fmt=fmt(math.dist(pts[-2], pts[-1])), fmt2=fmt(angle_of(pts[-2], pts[-1]))) if len(pts) >= 2 else ""
        if len(pts) > 2:
            text += _(" · total {fmt} mm", fmt=fmt(total))
        return Draft(path=tuple(pts), text=text + (_(" · cerrar") if closes else ""), closes=closes)

    def _text(self, a: tuple, c: tuple) -> str:
        t, o = self.tool, self.options
        w, h = abs(c[0] - a[0]), abs(c[1] - a[1])
        if t in ("rectangulo", "elipse"):
            return _("{fmt} × {fmt2} mm", fmt=fmt(w), fmt2=fmt(h)) + (_(" · esquinas R{fmt}", fmt=fmt(o.radius)) if t == "rectangulo" and o.radius > 0 else "")
        r = math.dist(a, c)
        if t == "circulo":
            return _("Ø {fmt} mm · radio {fmt2}", fmt=fmt(2 * r), fmt2=fmt(r))
        if t == "poligono":
            return (_("{sides} lados · radio {fmt} mm · entre caras {fmt2} mm · giro {fmt3}°", sides=o.sides, fmt=fmt(r), fmt2=fmt(2 * r * math.cos(math.pi / o.sides)), fmt3=fmt(angle_of(a, c))))
        return _("largo total {fmt} mm · ancho {fmt2} · {fmt3}°", fmt=fmt(r + o.slot_width), fmt2=fmt(o.slot_width), fmt3=fmt(angle_of(a, c)))
