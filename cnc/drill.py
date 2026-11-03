"""Taladrado con G0/G1 explicitos (GRBL no tiene ciclos fijos como G81-G83)."""
from __future__ import annotations

from .model import DrillOp, Stock, resolved
from .toolpath import Move, pass_depths, total_depth
from .i18n import _

PECK_CLEARANCE = 0.5  # mm: tras subir a despejar virutas, se baja en rapido hasta 0,5 mm sobre el fondo anterior


def _nearest_order(points):
    """Orden por vecino mas cercano, empezando por el primer punto dado."""
    remaining = list(points)
    ordered = [remaining.pop(0)]
    while remaining:
        lx, ly = ordered[-1]
        i = min(
            range(len(remaining)),
            key=lambda k: (remaining[k][0] - lx) ** 2 + (remaining[k][1] - ly) ** 2,
        )
        ordered.append(remaining.pop(i))
    return ordered


def drill_moves(op: DrillOp, stock: Stock, safe_z: float, clearance_z: float) -> list[Move]:
    op = resolved(op)
    if not op.points:
        raise ValueError(_("La operacion de taladrado no tiene puntos."))
    total = total_depth(op, stock)
    depths = [-total] if op.peck is None else pass_depths(total, op.peck)
    points = _nearest_order(op.points) if op.optimize_order else list(op.points)

    moves = [Move(z=safe_z, rapid=True)]
    for x, y in points:
        moves += [Move(x=x, y=y, rapid=True), Move(z=clearance_z, rapid=True)]
        prev = None
        for z in depths:
            if prev is not None:
                moves.append(Move(z=min(prev + PECK_CLEARANCE, clearance_z), rapid=True))
            moves.append(Move(z=z, feed=op.feed_z))
            moves.append(Move(z=clearance_z, rapid=True))  # sube: despeja virutas / termina el taladro
            prev = z
    moves.append(Move(z=safe_z, rapid=True))
    return moves
