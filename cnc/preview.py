"""Del plan de un trabajo a lineas que dibujar: lo que corta cada operacion, sin Qt.

`preview_ops(plan)` recorre los movimientos de cada operacion y separa lo que es corte (G1 en XY bajo la
superficie), los desplazamientos en rapido y las bajadas de taladro, para que la interfaz los pinte por separado.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import ContourOp, DrillOp, PocketOp
from .i18n import _

EPS = 1e-9


@dataclass
class OpPreview:
    index: int                                     # numero de operacion (empieza en 1, como en los avisos)
    kind: str                                      # hueco | pieza | linea | vaciado | taladro
    cuts: list = field(default_factory=list)       # polilineas [(x, y), ...] cortadas a avance
    outline: list = field(default_factory=list)    # lo mismo que `cuts` visto en planta: cada vuelta una sola vez, aunque se
                                                   # repita en cada pasada (para pintar y para la sujecion; ver `outline_of`)
    bridges: list = field(default_factory=list)    # polilineas [(x, y), ...] que la fresa recorre levantada sobre un puente
    rapids: list = field(default_factory=list)     # tramos [(x0, y0), (x1, y1)] en G0 con desplazamiento XY
    drills: list = field(default_factory=list)     # (x, y, z) de cada bajada sin desplazamiento (taladros)
    z_min: float = 0.0
    end: tuple = (None, None, None)                # donde queda la fresa (x, y, z) al acabar la operacion

    @property
    def points(self) -> int:
        return sum(len(c) for c in self.cuts)


def op_kind(op) -> str:
    if isinstance(op, ContourOp):
        return {"inside": "hueco", "outside": "pieza", "on": "linea"}[op.side]
    if isinstance(op, PocketOp):
        return "vaciado"
    if isinstance(op, DrillOp):
        return "taladro"
    raise TypeError(_("Tipo de operacion desconocido: {name}", name=type(op).__name__))


def outline_of(op) -> list:
    """Las lineas de corte de una operacion en planta, sin repetir las pasadas (`cuts` si la vista previa no las trae)."""
    return getattr(op, "outline", None) or op.cuts


def _unique(lines: list, key=tuple) -> list:
    """Las mismas lineas sin las repetidas (la primera de cada una, en su orden)."""
    seen, out = set(), []
    for line in lines:
        k = key(line)
        if k not in seen:
            seen.add(k)
            out.append(line)
    return out


def preview_moves(moves, index: int = 1, kind: str = "pieza", start: tuple = (None, None, None)) -> OpPreview:
    """`start` es donde estaba la fresa antes de la operacion (la anterior acaba ahi): sin ello, el primer
    desplazamiento en rapido de la operacion no se podria dibujar."""
    out = OpPreview(index, kind)
    x, y, z = start
    current: list = []
    bridge: list = []
    lap: list = []                                     # la vuelta en curso: como `current`, pero acaba en cada bajada
    laps: list = []
    for m in moves:
        nx = m.x if m.x is not None else x
        ny = m.y if m.y is not None else y
        nz = m.z if m.z is not None else z
        moved_xy = x is not None and nx is not None and (abs(nx - x) > EPS or abs(ny - y) > EPS)
        if nz is not None:
            out.z_min = min(out.z_min, nz)
        if m.rapid:
            if moved_xy:
                out.rapids.append([(x, y), (nx, ny)])
            current, bridge, lap = [], [], []
        elif m.tab and moved_xy:                       # sobre un puente: cada pasada repite el mismo tramo, se dibuja una vez
            if not bridge:
                bridge = [(x, y)]
                out.bridges.append(bridge)
            bridge.append((nx, ny))
            current, lap = [], []
        elif moved_xy and nz is not None and nz <= EPS:
            bridge = []
            if not current:
                current = [(x, y)]
                out.cuts.append(current)
            current.append((nx, ny))
            if not lap:
                lap = [(x, y)]
                laps.append(lap)
            lap.append((nx, ny))
        elif not moved_xy:
            lap = []                                   # cambia de Z sin moverse (baja a otra pasada): empieza otra vuelta
            if kind == "taladro" and nz is not None and z is not None and nz < z - EPS and nz < 0:
                out.drills.append((nx, ny, nz))        # bajada en vertical a avance: un taladro (o un picoteo)
        x, y, z = nx, ny, nz
    out.end = (x, y, z)
    # cada pasada profunda repite los mismos puentes, y cada pasada repite la misma vuelta: en planta, una vez
    out.bridges = _unique(out.bridges, lambda line: tuple((round(px, 3), round(py, 3)) for px, py in line))
    out.outline = _unique(laps)
    return out


def preview_ops(plan) -> list[OpPreview]:
    """Una OpPreview por operacion del plan (vacio si el plan no se pudo generar)."""
    if not plan.ok or plan.result is None:
        return []
    out, pos = [], (None, None, None)
    for i, (op, moves) in enumerate(zip(plan.operations, plan.result.op_moves), start=1):
        p = preview_moves(moves, i, op_kind(op), pos)
        out.append(p)
        pos = p.end
    return out
