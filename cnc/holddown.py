"""Sujecion de la plancha: donde se puede clavar o atornillar sin tocar piezas ni pasillos de corte, y el programa
corto que lleva la maquina a cada punto para marcarlo.

Sin Qt. Parte del plan de un tablero (ya girado y colocado en mm de maquina) y de su previsualizacion:

* **pasillo de la fresa**: la trayectoria con el radio de la fresa (y los taladros). Ahi nunca se sujeta.
* lo que queda del tablero tras quitar el pasillo se divide en **piezas** (lo que se va a usar: no se clava), **islas**
  (desecho de dentro de un hueco o de una linea cerrada: se suelta al cortar y no sujeta nada) y **marco** (el resto del
  desecho, que es lo unico donde se sujeta).
* la **zona libre** es el marco a una distancia minima del borde de corte y del borde de la plancha; esa distancia
  depende de lo que se use (un tirafondo gordo y duro pide mas margen que un clavo).

Las piezas nunca se clavan: una vez cortado su contorno quedan sueltas dentro del marco.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import polygonize, unary_union

from .post_grbl import _comment, _num
from .i18n import _

CAND_STEP = 8.0          # mm entre candidatos a punto
DEMAND_STEP = 15.0       # mm entre los puntos de la plancha que han de quedar cerca de uno
LARGE_BOARD_REACHES = 4.0   # una plancha es grande si su area pasa de 4 x alcance^2 (600 x 600 mm con alcance 300)
SMALL_BOARD_POINTS = 3      # lo minimo que se propone en una plancha que no es grande (si caben)
MAX_MATRIX = 16_000_000     # candidatos x sitios a cubrir: con mas, la rejilla de candidatos se hace mas gorda
SPREAD_SWEEPS = 40          # vueltas como mucho al repartir los puntos
SPREAD_STRIDE = 3           # al repartir se mira uno de cada 3 sitios de la plancha (con eso basta y va 3 veces mas rapido)
CLEAR_STEPS = (1.0, 0.75, 0.5, 0.25, 0.0)   # la distancia comoda al corte cede poco a poco (x la distancia pedida)
GAP_STEPS = (1.0, 0.75, 0.5, 0.25, 0.0)     # y la separacion minima tambien
CLEAR_WEIGHT = 2.0       # al poner un punto en un nudo de la rejilla, cada mm que le falte de holgura cuenta como 2 mm de ...
CLEAR_COMFORT = 2.0      # ... distancia al nudo, hasta tener 2 x la distancia comoda
GRID_MIN_POINTS = 4      # con menos puntos no hay rejilla de filas y columnas
GRID_MAX = 12            # filas o columnas como mucho
GRID_EXCESS = 0.25       # lo que pesa en la eleccion de la rejilla cada punto que sobra frente al reparto libre
GRID_ASPECT = 1.6        # una celda de la rejilla no es mas de 1,6 veces mas larga que ancha (ni al reves)
GRID_WINDOW = 0.45       # cada nudo busca su sitio a menos de 0,45 x el lado menor de su celda
MIN_FRAGMENT = 10000.0   # mm2: un trozo de marco suelto mayor que esto (100 cm2) debe llevar al menos un punto
MAX_POINTS = 64
ROW_GAP = 30.0           # mm: puntos cuya Y se parece a esto (de uno al siguiente) van en la misma fila al ordenarlos
MANY_PARTS = 2000        # un trazado con mas segmentos sueltos que esto se ensancha por polilineas largas (ver _buffer)
GAP_TOL = DEMAND_STEP * 0.75   # lo que puede pasarse el hueco medido en la rejilla sin que sea de verdad un hueco mayor
CLOSE_TOL = 1e-6
MIN_AREA = 1.0           # mm2: lo mas pequeno que cuenta como trozo


class HoldError(ValueError):
    """Ajuste de sujecion que no vale (el mensaje dice cual)."""


@dataclass(frozen=True)
class Fastener:
    key: str
    label: str
    head: float             # diametro de la cabeza (mm)
    kerf_margin: float      # distancia minima del borde de corte hasta el punto (mm)
    edge_margin: float      # distancia minima del borde de la plancha hasta el punto (mm)


FASTENERS = {
    "clavo": Fastener("clavo", _("Clavo de clavadora (cabeza 2 mm)"), 2.0, 6.0, 12.0),
    "tirafondo": Fastener("tirafondo", _("Tirafondo (hasta 6 mm)"), 6.0, 10.0, 15.0),
}


@dataclass
class HoldSettings:
    kind: str = "clavo"
    reach: float = 300.0                    # ningun sitio de la plancha a mas de esto de un punto (mm)
    min_gap: float = 200.0                  # la propuesta no pone dos puntos a menos de esto (mm)
    corner_clear: float = 100.0             # ni a menos de esto de una esquina de la plancha (mm)
    cut_clear: float = 20.0                 # ni a menos de esto del borde de corte, si hay otro sitio (mm)
    min_points: int = 6                     # lo minimo que se propone en una plancha grande
    grid: bool = True                       # en una plancha grande, los puntos en filas y columnas
    bottom_row: float = 40.0                # la fila de abajo, a esto del borde inferior (0 = sin fila pegada al borde)
    kerf_margin: Optional[float] = None     # None = el del tipo
    edge_margin: Optional[float] = None
    mark_z: float = 5.0                     # altura de la punta al hacer la pausa de marcado (mm sobre la superficie)

    def validate(self) -> None:
        if self.kind not in FASTENERS:
            raise HoldError(_("tipo de sujecion desconocido '{kind}' (hay: {join}).", kind=self.kind, join=', '.join(FASTENERS)))
        if not self.reach > 0:
            raise HoldError(_("el alcance debe ser mayor que 0."))
        for name, v in ((_("la separacion minima"), self.min_gap), (_("la distancia a las esquinas"), self.corner_clear),
                        (_("la distancia a los cortes"), self.cut_clear), (_("la fila del borde inferior"), self.bottom_row)):
            if not v >= 0:
                raise HoldError(_("{name} no puede ser negativa.", name=name))
        if not isinstance(self.grid, bool):
            raise HoldError(_("repartir en filas y columnas es si o no."))
        if not (math.isfinite(self.min_points) and self.min_points >= 0 and self.min_points == int(self.min_points)):
            raise HoldError(_("el minimo de puntos debe ser un numero entero, 0 o mas."))
        for name, v in ((_("margen al corte"), self.kerf_margin), (_("margen al borde"), self.edge_margin)):
            if v is not None and v < 0:
                raise HoldError(_("el {name} no puede ser negativo.", name=name))
        if self.mark_z < 0:
            raise HoldError(_("la altura de marcado no puede ser negativa."))

    @property
    def fastener(self) -> Fastener:
        return FASTENERS[self.kind]

    @property
    def kerf(self) -> float:
        return self.fastener.kerf_margin if self.kerf_margin is None else self.kerf_margin

    @property
    def edge(self) -> float:
        return self.fastener.edge_margin if self.edge_margin is None else self.edge_margin


@dataclass(frozen=True)
class PointCheck:
    ok: bool
    reason: str = ""        # por que no, si no es seguro
    warn: str = ""          # si es seguro pero hay algo que mirar (p. ej. mas cerca del corte de lo comodo)


def _closed(pl, tol: float = CLOSE_TOL) -> bool:
    return len(pl) > 3 and abs(pl[0][0] - pl[-1][0]) < tol and abs(pl[0][1] - pl[-1][1]) < tol


def _loops(pl) -> list:
    """Los poligonos que cierra una polilinea de corte.

    Con varias pasadas seguidas la previsualizacion une todas en una polilinea (la bajada es vertical y no la corta), asi
    que el mismo contorno se recorre dos, tres... veces. `Polygon(pl)` de un contorno recorrido un numero PAR de veces no
    tiene interior (cuenta por paridad): las piezas y los huecos dejaban de reconocerse y se proponia sujetar encima de
    ellos. Uniendo antes el trazado, cada tramo cuenta una sola vez, sean cuantas sean las pasadas."""
    if not _closed(pl):
        return []
    return [g for g in polygonize(unary_union(LineString(pl))) if g.area > MIN_AREA]


def _corners(board) -> list:
    """Las esquinas de la plancha: los vertices de su contorno donde este cambia de direccion."""
    out = []
    for poly in _polys(board):
        pts = list(poly.exterior.coords)[:-1]
        for i, (bx, by) in enumerate(pts):
            ax, ay = pts[i - 1]
            cx, cy = pts[(i + 1) % len(pts)]
            if abs((bx - ax) * (cy - by) - (by - ay) * (cx - bx)) > 1e-6:
                out.append((bx, by))
    return out


def _buffer(geom, dist: float):
    """`geom.buffer(dist)`. Con una coleccion de muchisimos trozos (la trayectoria de un dibujo con curvas son miles de
    segmentos sueltos) GEOS tarda segundos; ensanchando cada trozo y uniendolos sale lo mismo en una fraccion del tiempo.
    Con decenas de miles de trozos (un dibujo grande: 36.000) ni asi: antes se juntan los segmentos que se tocan en polilineas
    largas (una treintena) y luego se ensancha cada una."""
    if geom is None or geom.is_empty:
        return Polygon()
    parts = shapely.get_parts(geom)
    if len(parts) <= 1:
        return geom.buffer(dist)
    if len(parts) > MANY_PARTS:
        lines = shapely.get_type_id(parts) == 1                  # 1 = LineString
        if lines.sum() > 1:
            merged = shapely.line_merge(shapely.multilinestrings(parts[lines]))
            parts = np.concatenate([shapely.get_parts(merged), parts[~lines]])
    return shapely.union_all(shapely.buffer(parts, dist))


def _polys(geom) -> list:
    if geom is None or geom.is_empty:
        return []
    return [g for g in getattr(geom, "geoms", [geom]) if isinstance(g, Polygon) and g.area > MIN_AREA]


@dataclass
class Zones:
    """La plancha dividida segun lo que pasa con cada trozo. No depende del tipo de sujecion."""

    board: object
    radius: float
    axis: object                # ejes de corte (lineas y taladros)
    pieces: list
    islands: list
    frame: list

    @property
    def frame_union(self):
        return unary_union(self.frame) if self.frame else Polygon()


def zones_key(zones: Zones) -> tuple:
    """Identifica la geometria de un tablero: si no cambia, la propuesta de puntos tampoco."""
    return (round(zones.radius, 6), tuple(round(v, 6) for v in zones.board.bounds), hash(zones.axis.wkb))


def build_zones(board, previews, radius: float) -> Zones:
    lines, closed_polys, drills = [], [], []
    for op in previews:
        bridges = [LineString(br) for br in getattr(op, "bridges", []) if len(br) >= 2]
        for pl in op.cuts:
            if len(pl) < 2:
                continue
            lines.append(LineString(pl))
            if op.kind in ("hueco", "pieza", "linea") and not bridges:
                closed_polys.extend((loop, op.kind) for loop in _loops(pl))
        # Los puentes son tramos del contorno que la fresa recorre levantada: ahi la pieza sigue unida al tablero, pero la fresa
        # pasa (a media altura) y un tornillo daria en ella, asi que cuentan como corte; y cierran el contorno: sin ellos, un
        # contorno con puentes eran trozos abiertos y la pieza se tomaba por desecho (se proponia sujetar encima de ella).
        lines.extend(bridges)
        if bridges and op.kind in ("hueco", "pieza", "linea"):
            ring = unary_union([LineString(pl) for pl in op.cuts if len(pl) >= 2] + bridges)
            closed_polys.extend((g, op.kind) for g in polygonize(ring) if g.area > MIN_AREA)
        for x, y, _z in op.drills:
            drills.append(Point(x, y))
    axis = unary_union(lines + drills) if (lines or drills) else Polygon()
    kerf = Polygon()
    if not axis.is_empty:                  # lo corriente: un solo ensanchado; con trazados enormes, por trozos (ver _buffer)
        kerf = _buffer(axis, radius) if len(shapely.get_parts(axis)) > MANY_PARTS else axis.buffer(radius)
    rest = board.difference(kerf) if not kerf.is_empty else board
    pieces, islands, frame = [], [], []
    for comp in _polys(rest):
        rp = comp.representative_point()
        inside = [(poly.area, kind) for poly, kind in closed_polys if poly.contains(rp)]
        if not inside:
            frame.append(comp)
        elif min(inside)[1] == "pieza":
            pieces.append(comp)
        else:
            islands.append(comp)             # hueco o linea cerrada: se suelta al cortar
    return Zones(board, radius, axis, pieces, islands, frame)


def mark_order(points: Sequence[tuple]) -> list:
    """Posiciones de `points` de lejos a cerca: por filas, de la de Y mayor (el fondo de la bancada, lo mas lejos de quien
    maneja la maquina) a la de Y menor, y dentro de cada fila de izquierda a derecha. Dos puntos son de la misma fila
    si su Y se parece (hasta ROW_GAP mm de uno al siguiente): los de una rejilla cuyo nudo se movio unos mm no se
    cruzan de lado a lado."""
    pts = [tuple(p) for p in points]
    rows, row = [], []
    for k in sorted(range(len(pts)), key=lambda k: (-pts[k][1], pts[k][0], k)):
        if row and pts[row[-1]][1] - pts[k][1] > ROW_GAP:
            rows.append(row)
            row = []
        row.append(k)
    if row:
        rows.append(row)
    return [k for row in rows for k in sorted(row, key=lambda k: (pts[k][0], k))]


def order_marks(points: Sequence[tuple]) -> list:
    """Los puntos en el orden en que se marcan (ver `mark_order`)."""
    pts = [tuple(p) for p in points]
    return [pts[k] for k in mark_order(pts)]


def grid_cells(width: float, height: float, nc: int, nr: int, bottom_row: float) -> tuple:
    """(ancho, alto) de la celda de una rejilla de `nc` columnas y `nr` filas. Con `bottom_row` la fila de abajo va pegada
    al borde inferior y las demas se reparten hasta arriba, la ultima a media celda del borde superior; sin ella, las
    filas van en el centro de sus celdas. Una sola fila va en el centro."""
    if nr > 1 and bottom_row > 0:
        return width / nc, (height - bottom_row) / (nr - 0.5)
    return width / nc, height / nr


def grid_nodes(bounds: tuple, nc: int, nr: int, bottom_row: float) -> list:
    """Los nudos de la rejilla: (columna, fila, x, y), fila a fila desde abajo."""
    minx, miny, maxx, maxy = bounds
    cw, ch = grid_cells(maxx - minx, maxy - miny, nc, nr, bottom_row)
    xs = [minx + (i + 0.5) * cw for i in range(nc)]
    if nr > 1 and bottom_row > 0:
        ys = [miny + bottom_row + j * ch for j in range(nr)]
    else:
        ys = [miny + (j + 0.5) * ch for j in range(nr)]
    return [(i, j, xs[i], ys[j]) for j in range(nr) for i in range(nc)]


def grid_extra(n: int) -> int:
    """Cuantos puntos mas que un reparto libre de `n` puede llevar una rejilla: uno, o un cuarto si son muchos."""
    return max(1, n // 4)


def grid_shape(width: float, height: float, n: int, bottom_row: float, reach: float, min_gap: float) -> Optional[tuple]:
    """(columnas, filas) de la rejilla regular que sustituye a un reparto libre de `n` puntos, o None si no hay ninguna que
    valga: la celda ha de caber en el alcance (su media diagonal), ser casi cuadrada (`GRID_ASPECT`), las filas y columnas
    han de estar a `min_gap` o mas, y ha de llevar `n` puntos o algo mas (`grid_extra`). De las que valen, la de celdas
    mas cuadradas y menos sobrante."""
    best = None
    for nc in range(1, GRID_MAX + 1):
        for nr in range(1, GRID_MAX + 1):
            total = nc * nr
            if total < n or total > n + grid_extra(n):
                continue
            cw, ch = grid_cells(width, height, nc, nr, bottom_row)
            if cw <= 0 or ch <= 0 or math.hypot(cw, ch) / 2 > reach or not 1 / GRID_ASPECT <= cw / ch <= GRID_ASPECT:
                continue
            if (nc > 1 and cw < min_gap) or (nr > 1 and ch < min_gap):
                continue
            cost = abs(math.log(cw / ch)) + GRID_EXCESS * (total - n)
            if best is None or cost < best[0] - 1e-12:
                best = (cost, nc, nr)
    return None if best is None else (best[1], best[2])


def _ladder(rank: np.ndarray, min_gap: float) -> list:
    """La escalera de lo que cede para llegar a toda la plancha: (candidatos que valen, separacion que se pide).

    `rank[i]` es 2 x el escalon de holgura al corte del candidato i (0 = toda la holgura pedida) mas 1 si cae cerca de una
    esquina. Con toda la holgura y lejos de las esquinas, primero la separacion cede poco a poco hasta 0; despues valen
    tambien los sitios cerca de las esquinas; y solo entonces se pasa al escalon siguiente de holgura, con la separacion
    otra vez entera. Lo ultimo en ceder es la holgura al corte."""
    steps = []
    for t in range(len(CLEAR_STEPS)):
        steps += [(rank <= 2 * t, min_gap * k) for k in GAP_STEPS]
        steps.append((rank <= 2 * t + 1, 0.0))
    return steps


def _spread(d2: np.ndarray, cand: np.ndarray, idx: list, rank: np.ndarray, region: np.ndarray, min_gap: float) -> list:
    """Reparte los puntos elegidos por la plancha, como lo haria uno a mano.

    `d2[i, k]` es la distancia al cuadrado, en alcances al cuadrado, del candidato i al sitio k de la plancha; `idx`, los
    indices (en `cand`) de los puntos. Cada vuelta mueve cada punto al candidato que deja mas cerca de el la parte de
    plancha que le toca (la distancia a la cuarta, para que pese mas lo lejano); asi los puntos se separan solos y el
    sitio peor cubierto mejora. Un punto solo se mueve si:

    * sigue en su trozo de marco (`region`: un trozo suelto no se queda sin punto) y en un sitio que no es peor que el que
      tiene (`rank`, menos es mejor: lejos de las esquinas y de los cortes);
    * sigue a `min_gap` de los otros;
    * sigue cubriendo todo lo que solo el cubria: repartir nunca deja un sitio a mas del alcance.

    Con el mismo numero de puntos y sin mover ninguno a un sitio peor; si no hay mejora, se queda donde esta."""
    idx = list(idx)
    if len(idx) < 2 or not d2.shape[1]:
        return idx
    seen = d2[:, ::SPREAD_STRIDE]                                # lo que cuenta para repartir; cubrir se mira entero
    d4 = seen * seen
    sites = np.arange(seen.shape[1])
    covers = np.ascontiguousarray((d2 <= 1.0).T)                 # covers[k, i]: el candidato i cubre el sitio k
    for __ in range(SPREAD_SWEEPS):
        moved = False
        owner = np.argmin(seen[idx], axis=0)                     # de que punto es cada sitio de la plancha
        mine = np.zeros((seen.shape[1], len(idx)), np.float32)
        mine[sites, owner] = 1.0
        cost = d4 @ mine                                         # cost[i, j]: lo que costaria que j estuviera en i
        for j, here in enumerate(idx):
            others = idx[:j] + idx[j + 1:]
            only_me = (d2[others].min(axis=0) > 1.0) & (d2[here] <= 1.0)       # lo que solo cubre este punto
            ok = (rank <= rank[here]) & (region == region[here]) & covers[only_me].all(axis=0)
            for o in others:
                ok &= np.hypot(cand[:, 0] - cand[o, 0], cand[:, 1] - cand[o, 1]) >= min_gap - 1e-9
            ok[here] = True                                       # quedarse siempre vale
            best = int(np.argmin(np.where(ok, cost[:, j], np.inf)))
            if best != here and cost[best, j] < cost[here, j] * (1 - 1e-6):
                idx[j] = best
                moved = True
        if not moved:
            break
    return idx


class HoldAnalysis:
    """Zonas de un tablero mas lo que se sujeta (tipo, margenes, alcance). Calcula la zona libre una vez."""

    def __init__(self, zones: Zones, settings: Optional[HoldSettings] = None):
        self.settings = settings or HoldSettings()
        self.settings.validate()
        self.zones = zones
        s = self.settings
        z = zones
        free = z.frame_union
        if not free.is_empty:
            free = free.intersection(z.board.buffer(-s.edge))
        if not free.is_empty and not z.axis.is_empty:
            free = free.difference(_buffer(z.axis, z.radius + s.kerf))
        if not free.is_empty:
            shapely.prepare(free)                    # los clics y el raton la consultan muchas veces
        self.free = free
        self._suggested: Optional[list] = None
        self.key = (zones_key(zones), s.kind, s.reach, s.kerf, s.edge, s.min_gap, s.corner_clear, s.cut_clear, s.min_points,
                    s.grid, s.bottom_row)

    # --- un punto ---------------------------------------------------------------------------------------
    def check(self, x: float, y: float) -> PointCheck:
        """Si se puede sujetar en (x, y) y, si no, por que."""
        s, z = self.settings, self.zones
        p = Point(x, y)
        if not self.free.is_empty and self.free.contains(p):
            d_cut = max(z.axis.distance(p) - z.radius, 0.0) if not z.axis.is_empty else math.inf
            if d_cut < s.cut_clear:
                return PointCheck(True, warn=_("a {d_cut:.1f} mm del corte (lo comodo es {cut_clear:g} o mas)", d_cut=d_cut, cut_clear=s.cut_clear))
            return PointCheck(True)
        if not z.board.contains(p):
            return PointCheck(False, _("fuera de la plancha"))
        d_edge = z.board.boundary.distance(p)
        if d_edge < s.edge:
            return PointCheck(False, _("a {d_edge:.1f} mm del borde de la plancha (minimo {edge:g})", d_edge=d_edge, edge=s.edge))
        d_cut = max(z.axis.distance(p) - z.radius, 0.0) if not z.axis.is_empty else math.inf
        if d_cut <= 0:
            return PointCheck(False, _("encima del recorrido de la fresa"))
        for comp in z.pieces:
            if comp.contains(p):
                return PointCheck(False, _("dentro de una pieza (las piezas no se clavan)"))
        for comp in z.islands:
            if comp.contains(p):
                return PointCheck(False, _("en un desecho que se suelta al cortar: no sujeta nada"))
        if d_cut < s.kerf:
            return PointCheck(False, _("a {d_cut:.1f} mm del corte (minimo {kerf:g})", d_cut=d_cut, kerf=s.kerf))
        return PointCheck(False, _("demasiado cerca del corte"))

    # --- cobertura -------------------------------------------------------------------------------------------
    def _grid(self, step: float):
        minx, miny, maxx, maxy = self.zones.board.bounds
        xs = np.arange(minx + step / 2, maxx, step)
        ys = np.arange(miny + step / 2, maxy, step)
        gx, gy = np.meshgrid(xs, ys)
        return gx.ravel(), gy.ravel()

    def _demand(self) -> np.ndarray:
        gx, gy = self._grid(DEMAND_STEP)
        keep = shapely.contains_xy(self.zones.board, gx, gy)
        return np.column_stack([gx[keep], gy[keep]])

    def gap_ok(self, points: Sequence[tuple]) -> bool:
        """Si ningun sitio de la plancha queda a mas del alcance de un punto (con la tolerancia de la rejilla)."""
        return self.worst_gap(points)[0] <= self.settings.reach + GAP_TOL

    def worst_gap(self, points: Sequence[tuple]) -> tuple:
        """(distancia, (x, y)) del sitio de la plancha mas lejos de un punto. Sin puntos: (inf, None)."""
        demand = self._demand()
        if not len(demand):
            return 0.0, None
        if not len(points):
            return math.inf, None
        pts = np.asarray(points, dtype=float).reshape(-1, 2)
        d = np.min(np.hypot(demand[:, None, 0] - pts[None, :, 0], demand[:, None, 1] - pts[None, :, 1]), axis=1)
        i = int(d.argmax())
        return float(d[i]), (float(demand[i, 0]), float(demand[i, 1]))

    # --- propuesta -----------------------------------------------------------------------------------------
    def is_large(self) -> bool:
        """Si la plancha es grande: su area pasa de 4 x alcance^2 (600 x 600 mm con alcance 300)."""
        return self.zones.board.area >= LARGE_BOARD_REACHES * self.settings.reach ** 2

    def required_points(self) -> int:
        """Cuantos puntos como minimo se proponen: `min_points` en una plancha grande, menos en una pequena."""
        s = self.settings
        return s.min_points if self.is_large() else min(s.min_points, SMALL_BOARD_POINTS)

    def suggest(self) -> list:
        """Puntos propuestos, siempre dentro de la zona libre. En este orden:

        1. uno en cada trozo grande de marco suelto (sujetar un trozo no sujeta el de al lado);
        2. los justos para que ningun sitio de la plancha quede a mas de `reach` de un punto;
        3. si son pocos para una plancha grande, los que faltan hasta `min_points`, siempre el mas alejado de los demas.

        Huye de las esquinas (a menos de `corner_clear` no se propone nada: en la bancada sacrificable seria siempre el
        mismo sitio), se mantiene a `cut_clear` o mas del borde de corte (un error pequeno al clavar no debe dar en el
        corte) y no junta dos puntos a menos de `min_gap`. Eso cede solo si no hay otra manera de llegar a todo, y poco a
        poco: la separacion primero, despues las esquinas, y la holgura al corte la ultima. Cubrir la plancha y soltar
        cada trozo pesa mas.

        En una plancha grande, con `grid`, los mismos puntos van en filas y columnas regulares (la de abajo pegada al
        borde inferior, a `bottom_row`), si la rejilla cabe y no pide muchos mas puntos; si no, repartidos con arte."""
        if self._suggested is None:
            self._suggested = self._compute_suggestion()
        return list(self._suggested)

    def _candidates(self, demand_count: int) -> np.ndarray:
        """Los sitios de la zona libre donde se puede poner un punto, en una rejilla (mas gorda si la plancha es enorme
        y libre, para que la tabla de cobertura quepa en memoria)."""
        step = CAND_STEP
        while True:
            gx, gy = self._grid(step)
            keep = shapely.contains_xy(self.free, gx, gy)
            cand = np.column_stack([gx[keep], gy[keep]])
            if len(cand) * demand_count <= MAX_MATRIX:
                break
            step *= 1.25
        # una zona libre muy estrecha puede caer entre dos puntos de la rejilla: que tenga al menos un candidato
        extra = [(rp.x, rp.y) for rp in (g.representative_point() for g in _polys(self.free))]
        if extra:
            cand = np.vstack([cand, np.array(extra)]) if len(cand) else np.array(extra)
        return cand

    def _clearance(self, cand: np.ndarray) -> np.ndarray:
        """Distancia de cada candidato al borde de corte (al del pasillo de la fresa)."""
        z = self.zones
        if z.axis.is_empty:
            return np.full(len(cand), np.inf)
        tree = shapely.STRtree(shapely.get_parts(z.axis))        # con indice va 5 veces mas rapido que medir contra todo
        _near, dist = tree.query_nearest(shapely.points(cand), return_distance=True, all_matches=False)
        return dist - z.radius

    def _compute_suggestion(self) -> list:
        if self.free.is_empty:
            return []
        s = self.settings
        demand = self._demand()
        cand = self._candidates(len(demand))
        if not len(cand):
            return []
        near_corner = np.zeros(len(cand), bool)
        for cx, cy in _corners(self.zones.board):
            near_corner |= np.hypot(cand[:, 0] - cx, cand[:, 1] - cy) < s.corner_clear
        clear = self._clearance(cand)
        tier = np.full(len(cand), len(CLEAR_STEPS) - 1)               # 0: con toda la holgura pedida ... ultimo: sin ella
        for k in range(len(CLEAR_STEPS) - 1, -1, -1):
            tier[clear >= s.cut_clear * CLEAR_STEPS[k] - 1e-9] = k
        rank = tier * 2 + near_corner                    # menos es mejor: antes la holgura al corte, luego las esquinas
        comfy = rank == 0                                # lejos de las esquinas y con toda la holgura
        d2 = np.empty((len(cand), len(demand)), np.float32)           # distancia candidato -> sitio, en alcances^2
        for lo in range(0, len(cand), 1500):
            part = cand[lo:lo + 1500].astype(np.float32)
            dx = part[:, 0:1] - demand[None, :, 0].astype(np.float32)
            dy = part[:, 1:2] - demand[None, :, 1].astype(np.float32)
            d2[lo:lo + 1500] = (dx * dx + dy * dy) / np.float32(s.reach ** 2)
        within = d2 <= 1.0                               # que sitios cubre cada candidato
        region = np.full(len(cand), -1)                  # en que trozo de marco cae cada candidato
        for n, comp in enumerate(self.zones.frame):
            region[shapely.contains_xy(comp, cand[:, 0], cand[:, 1])] = n

        def select(nodes: list, window: float = 0.0):
            """Los puntos: los de los `nodes` de la rejilla (si hay) y lo que falte para soltar cada trozo, cubrir la
            plancha y llegar al minimo."""
            chosen: list = []                            # indices en cand
            covered = np.zeros(len(demand), bool)
            nearest = np.full(len(cand), np.inf)         # distancia de cada candidato al punto elegido mas cercano

            def take(i: int) -> None:
                chosen.append(i)
                np.logical_or(covered, within[i], out=covered)
                np.minimum(nearest, np.hypot(cand[:, 0] - cand[i, 0], cand[:, 1] - cand[i, 1]), out=nearest)

            def usable(pool: np.ndarray, gap: float) -> np.ndarray:
                ok = pool & (nearest > 1e-6)             # uno ya elegido no se repite
                return ok & (nearest >= gap - 1e-9) if gap > 0 else ok

            def gains() -> np.ndarray:
                return within[:, ~covered].sum(axis=1)

            # 0. la rejilla: cada nudo, el sitio comodo mas cercano (y con mas holgura); los de una fila a la misma altura
            #    y los de una columna a la misma distancia del borde, si se puede
            rows: dict = {}
            cols: dict = {}
            for i, j, nx, ny in nodes:
                tx = float(np.mean(cols[i])) if i in cols else nx
                ty = float(np.mean(rows[j])) if j in rows else ny
                ok = usable(comfy & (np.hypot(cand[:, 0] - nx, cand[:, 1] - ny) <= window), s.min_gap)
                if not ok.any():
                    continue                             # sin sitio comodo cerca: lo que falte lo cubre el reparto
                cost = np.hypot(cand[:, 0] - tx, cand[:, 1] - ty) + CLEAR_WEIGHT * np.maximum(0.0, CLEAR_COMFORT * s.cut_clear - clear)
                k = int(np.argmin(np.where(ok, cost, np.inf)))
                take(k)
                rows.setdefault(j, []).append(cand[k, 1])
                cols.setdefault(i, []).append(cand[k, 0])

            # 1. cada trozo de marco suelto y grande lleva su punto
            for n, comp in enumerate(self.zones.frame):
                if comp.area < MIN_FRAGMENT or len(chosen) >= MAX_POINTS:
                    continue
                inside = region == n
                if not inside.any() or any(region[c] == n for c in chosen):
                    continue                             # no cabe ningun punto (lo dice unreachable_fragments) o ya lo tiene
                pool = next(inside & (rank <= r) for r in range(2 * len(CLEAR_STEPS)) if (inside & (rank <= r)).any())
                gain = np.where(pool, gains(), -1)
                best = np.flatnonzero(gain == gain.max())
                take(int(best[int(np.argmax(np.minimum(nearest[best], 1e9)))]))

            # 2. que ningun sitio de la plancha quede lejos de un punto; la separacion, las esquinas y la holgura al corte
            #    ceden si no hay otra, y en ese orden (la separacion poco a poco: lo mas cerca que haga falta, no pegados)
            gain_now = gains()                           # solo cambia al elegir un punto: no se recalcula en cada escalon
            for pool, gap in _ladder(rank, s.min_gap):
                while len(chosen) < MAX_POINTS and not covered.all():
                    gain = np.where(usable(pool, gap), gain_now, -1)
                    i = int(gain.argmax())
                    if gain[i] <= 0:
                        break
                    take(i)
                    gain_now = gains()

            # 3. una plancha grande no se deja con tres clavos: hasta el minimo, el sitio mas alejado de los demas (solo
            #    donde hay toda la holgura al corte: un clavo peligroso no vale por llegar al minimo)
            while len(chosen) < min(self.required_points(), MAX_POINTS):
                ok = usable(comfy, s.min_gap)
                if not ok.any():
                    break
                take(int(np.argmax(np.where(ok, np.minimum(nearest, 1e9), -1.0))))
            return chosen, covered

        chosen, covered = select([])
        if s.grid and self.is_large() and len(chosen) >= GRID_MIN_POINTS:
            minx, miny, maxx, maxy = self.zones.board.bounds
            shape = grid_shape(maxx - minx, maxy - miny, len(chosen), s.bottom_row, s.reach, s.min_gap)
            if shape:
                nc, nr = shape
                cw, ch = grid_cells(maxx - minx, maxy - miny, nc, nr, s.bottom_row)
                tidy, tidy_covered = select(grid_nodes(self.zones.board.bounds, nc, nr, s.bottom_row),
                                            GRID_WINDOW * min(cw, ch))
                # la rejilla vale si no deja sin cubrir lo que el reparto libre cubria y no pide muchos mas puntos
                if (tidy_covered.all() or not covered.all()) and len(tidy) <= len(chosen) + grid_extra(len(chosen)):
                    return order_marks([(float(cand[i, 0]), float(cand[i, 1])) for i in tidy])
        # 4. repartidos con arte: ni pegados al limite de lo que cubren ni amontonados
        chosen = _spread(d2, cand, chosen, rank, region, s.min_gap)
        return order_marks([(float(cand[i, 0]), float(cand[i, 1])) for i in chosen])

    def unreachable_fragments(self) -> list:
        """Trozos de marco grandes donde no cabe ningun punto con estos margenes (areas en cm2)."""
        out = []
        for comp in self.zones.frame:
            if comp.area >= MIN_FRAGMENT and comp.intersection(self.free).is_empty:
                out.append(round(comp.area / 100.0))
        return out


def analyse_plan(plan, previews, settings: Optional[HoldSettings] = None) -> Optional[HoldAnalysis]:
    """El analisis de sujecion de un tablero ya planificado; None si el plan no vale o no hay tablero (sin
    tablero no se sabe donde esta el borde de la plancha)."""
    if plan is None or not plan.ok or not plan.stock or plan.job is None:
        return None
    board = unary_union([box(*s.shape.bounds) for s in plan.stock])
    return HoldAnalysis(build_zones(board, previews, plan.job.tool.diameter / 2), settings)


def board_points(analysis: HoldAnalysis, edited: Optional[Sequence[tuple]]) -> list:
    """Los puntos de un tablero: los editados a mano si los hay, y si no la propuesta."""
    return [tuple(p) for p in edited] if edited is not None else analysis.suggest()


def marks_gcode(points: Sequence[tuple], *, title: str = "", kind: str = "", safe_z: float = 5.0, mark_z: float = 5.0,
                home_at_end: bool = False, return_to_origin: bool = False, decimals: int = 3) -> str:
    """Programa corto para marcar los puntos: con el husillo parado va a cada uno y hace una pausa (M0); se marca
    bajo la punta y se reanuda en UGS. Mismo cero y mismos ejes que el programa de corte.

    Los visita de lejos a cerca (`mark_order`) sea cual sea el orden en que vengan; si el orden de la ventana es otro,
    el comentario de cada punto dice cual es en la tabla."""
    if not len(points):
        raise HoldError(_("no hay puntos que marcar."))
    travel = max(safe_z, mark_z)
    n = len(points)
    lines = [_comment(_("Marcas de sujecion") + (f": {title}" if title else "")),
             _comment(_("{n} puntos", n=n) + (f", {kind}" if kind else "") + _("; el husillo no arranca")),
             _comment(_("Orden: de lejos a cerca (Y mayor primero), cada fila de izquierda a derecha")),
             _comment(_("En cada pausa (M0) marca el punto bajo la punta y pulsa Reanudar en UGS")),
             "G21 G90 G17 G94 G54",
             f"G0 Z{_num(travel, decimals)}"]
    for i, k in enumerate(mark_order(points), start=1):
        x, y = points[k]
        lines.append(_comment(_("Punto {i} de {n} - es el {v} de la ventana", i=i, n=n, v=k + 1) if k != i - 1
                              else _("Punto {i} de {n}", i=i, n=n)))
        lines.append(f"G0 X{_num(x, decimals)} Y{_num(y, decimals)}")
        lines.append(f"G0 Z{_num(mark_z, decimals)}")
        lines.append("M0")
        lines.append(f"G0 Z{_num(travel, decimals)}")
    if home_at_end:
        lines += ["G4 P0.1", "$H"]
    elif return_to_origin:
        lines.append("G0 X0 Y0")
    lines.append("M30")
    return "\n".join(lines) + "\n"
