"""Orden de corte de las formas, el habitual en taller (comprobado con G-code de referencia).

Dentro de cada grupo (huecos, sobre la linea, piezas exteriores; siempre en ese orden):

- **Huecos** (y vaciados, y taladros): se empieza por el mas alto (a igualdad, el de mas a la izquierda)
  y desde ahi siempre al mas cercano, midiendo entre los puntos de entrada de cada uno.
- **Piezas y lineas**: de arriba abajo por su borde superior (a igualdad, de izquierda a derecha).

Con pocas formas las dos reglas dan lo mismo que ordenar por Y; con muchos huecos repartidos
(una placa con 20 agujeros) la segunda obliga a recorrer toda la placa fila a fila y la primera no.
"""
from __future__ import annotations

import math
from typing import Callable, Sequence

from .toolpath import tool_path_rings
from .i18n import _

TIE_TOL = 0.5   # mm: bordes superiores a menos de esto cuentan como la misma fila
GROUP_ORDER = {"inside": 0, "on": 1, "outside": 2}


def _top(shape) -> float:
    return shape.bounds[3]


def _left(shape) -> float:
    return shape.bounds[0]


def rows(indices: list[int], shapes: Sequence) -> list[int]:
    """Arriba abajo por borde superior; las formas con el borde a menos de TIE_TOL van de izquierda a derecha."""
    pending = sorted(indices, key=lambda i: (-_top(shapes[i]), i))
    out: list[int] = []
    while pending:
        top = _top(shapes[pending[0]])
        row = [i for i in pending if top - _top(shapes[i]) <= TIE_TOL]
        out += sorted(row, key=lambda i: (_left(shapes[i]), i))
        pending = [i for i in pending if i not in row]
    return out


def nearest_chain(indices: list[int], shapes: Sequence, entry: Callable[[int], tuple[float, float]]) -> list[int]:
    """Empieza por la forma mas alta (a igualdad, la de mas a la izquierda) y sigue siempre a la mas cercana,
    midiendo entre los puntos de entrada (`entry(i)`) de cada forma."""
    if len(indices) < 3:
        return rows(indices, shapes)
    points = {i: entry(i) for i in indices}
    order = rows(indices, shapes)[:1]
    rest = [i for i in indices if i != order[0]]
    while rest:
        here = points[order[-1]]
        nxt = min(rest, key=lambda j: (round(math.dist(here, points[j]), 6), j))
        order.append(nxt)
        rest.remove(nxt)
    return order


def order_cuts(shapes: Sequence, sides: Sequence[str], tool_radius: float, mode: str = "far-to-near") -> list[int]:
    """Posiciones de `shapes` (contornos) en el orden de corte. mode 'file' solo agrupa (huecos, lineas, piezas)."""
    if len(shapes) != len(sides):
        raise ValueError(_("shapes y sides deben tener la misma longitud."))
    out: list[int] = []
    for side in sorted(set(sides), key=lambda s: GROUP_ORDER[s]):
        group = [i for i, s in enumerate(sides) if s == side]
        if mode == "file":
            out += group
        elif side == "inside":
            out += nearest_chain(group, shapes, lambda i: tool_path_rings(shapes[i], side, tool_radius)[0][0][0])
        else:
            out += rows(group, shapes)
    return out
