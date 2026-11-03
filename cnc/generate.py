"""Punto de entrada del nucleo: Job -> G-code + informe (Z minima, avisos)."""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Optional

from .drill import drill_moves
from .model import ContourOp, DrillOp, Job, PocketOp, resolved
from .pocket import pocket_moves
from .post_grbl import PostOptions, _comment, to_gcode
from shapely.geometry import Polygon

from .toolpath import Move, op_moves, total_depth, unreachable
from .i18n import _


@dataclass
class GcodeResult:
    text: str
    min_z: float          # Z mas profunda alcanzada (mm, negativa)
    warnings: list[str]
    bounds: Optional[tuple] = None   # (minx, miny, maxx, maxy) de la trayectoria, en mm
    op_moves: list = field(default_factory=list)   # movimientos de cada operacion, en orden (para previsualizar)
    alarms: list = field(default_factory=list)     # avisos que ademas hay que confirmar a mano (zonas sin cortar, piezas juntas)


def _moves_for(op, job: Job) -> list[Move]:
    radius = job.tool.diameter / 2
    if isinstance(op, ContourOp):
        return op_moves(op, job.stock, radius, job.safe_z, job.clearance_z)
    if isinstance(op, DrillOp):
        return drill_moves(op, job.stock, job.safe_z, job.clearance_z)
    if isinstance(op, PocketOp):
        return pocket_moves(op, job.stock, radius, job.safe_z, job.clearance_z)
    raise TypeError(_("Tipo de operacion desconocido: {name}", name=type(op).__name__))


def _reach_warning(i: int, op, tool_diameter: float) -> Optional[str]:
    """Aviso si la fresa no alcanza zonas gruesas (>2 mm) de la forma de la operacion."""
    radius = tool_diameter / 2
    if isinstance(op, ContourOp) and op.side in ("outside", "inside"):
        shape = op.shape
        poly = Polygon(shape.exterior) if isinstance(shape, Polygon) else Polygon(list(shape.coords))
        mode = op.side
    elif isinstance(op, PocketOp):
        poly, mode = op.shape, "inside"
    else:
        return None
    if poly.is_empty or not poly.is_valid:
        return None
    left = unreachable(poly, mode, radius)
    if left.buffer(-1.0).is_empty:
        return None
    return (
        _("Operacion {i}: unos {area:.0f} mm2 de la forma no se pueden cortar con una fresa de "
          "{tool_diameter:g} mm (zonas mas estrechas que la fresa o esquinas muy agudas).", i=i, area=left.area, tool_diameter=tool_diameter)
    )


def _spacing_warnings(job: Job) -> list[str]:
    """Piezas (contorno 'outside') mas cerca entre si que el diametro de la fresa.

    Si la fresa no cabe entre dos piezas, al cortar una se come parte de la otra.
    Las piezas contenidas dentro de otra (islas de un hueco) no se comparan aqui.
    """
    d_tool = job.tool.diameter
    pieces = [
        (i, op.shape)
        for i, op in enumerate(job.operations, start=1)
        if isinstance(op, ContourOp) and op.side == "outside" and isinstance(op.shape, Polygon)
    ]
    out = []
    for (i, a), (j, b) in combinations(pieces, 2):
        if a.contains(b) or b.contains(a):
            continue
        gap = a.distance(b)
        if gap < d_tool - 1e-6:
            out.append(
                _("Operaciones {i} y {j}: las piezas estan a {gap:.1f} mm y una fresa de {d_tool:g} mm no cabe "
                  "entre ellas (cortaria parte de la otra pieza).", i=i, j=j, gap=gap, d_tool=d_tool)
            )
    return out


_KIND_NAMES = {"ball": _("de bola (ball)"), "vbit": _("en V")}


def tool_label(tool) -> str:
    """Nombre legible; no repite el diametro si ya esta en el nombre ('End mill 4 mm')."""
    if not tool.name:
        return _("de {diameter:g} mm", diameter=tool.diameter)
    if f"{tool.diameter:g}".replace(".", ",") in tool.name or f"{tool.diameter:g}" in tool.name:
        return tool.name
    return _("{name} ({diameter:g} mm)", name=tool.name, diameter=tool.diameter)


def _check_tool(tool) -> None:
    if tool.diameter <= 0:
        raise ValueError(_("El diametro de la fresa debe ser > 0."))
    if tool.kind != "endmill":
        raise ValueError(
            _("La fresa {tool_label} es {get}: de momento solo las end mill generan trayectorias (las demas se "
              "guardan como datos). El diametro de corte de una fresa en V cambia con la profundidad, y eso "
              "aun no esta estudiado.", tool_label=tool_label(tool), get=_KIND_NAMES.get(tool.kind, tool.kind))
        )


def machine_comment(machine) -> str:
    """La linea de la cabecera del .nc que dice para que maquina es (viaja con el archivo, p. ej. en el pendrive). Cabe en
    las 79 columnas que admite GRBL: si el nombre es largo, se recorta."""
    size = " x ".join(f"{v:g}" for v in machine.travel)
    name = machine.name or _("sin nombre")
    while True:
        text = _("Maquina: {name} | recorrido {size} mm", name=name, size=size)
        if len(_comment(text)) <= 79 or len(name) <= 1:
            return text
        name = name[:-1]


def generate_gcode(job: Job, post: Optional[PostOptions] = None) -> GcodeResult:
    post = post or PostOptions()
    _check_tool(job.tool)
    if not job.operations:
        raise ValueError(_("El trabajo no tiene operaciones."))

    machine = job.machine
    warnings: list[str] = []

    for n, ln in enumerate(post.end_code.splitlines(), start=1):
        if len(ln.strip()) > 79:
            warnings.append(
                _("Codigo final, linea {n}: mas de 79 caracteres; GRBL estandar la rechazaria (error:11).", n=n)
            )

    moves: list[Move] = []
    per_op: list[list[Move]] = []
    alarms: list[str] = []
    for i, raw_op in enumerate(job.operations, start=1):
        op = resolved(raw_op, job.tool)  # stepdown, feeds y stepover: de la operacion, si no de la fresa
        flute = job.tool.flute_length
        if flute is not None:
            depth = total_depth(op, job.stock)
            if depth > flute + 1e-9:
                raise ValueError(
                    _("Operacion {i}: la profundidad {depth:g} mm supera la longitud util de la fresa {tool_label} "
                      "({flute:g} mm): el vastago rozaria el material y el corte no llegaria a atravesarlo.", i=i, depth=depth, tool_label=tool_label(job.tool), flute=flute)
                )
        for name, limit in (("feed_xy", machine.max_feed), ("feed_z", machine.max_feed_z)):
            f = getattr(op, name, None)
            if f is not None and limit is not None and f > limit:     # sin dato en la ficha: no se comprueba
                warnings.append(
                    _("Operacion {i}: {name} {f:g} mm/min supera el maximo de la maquina ({limit:g}); el firmware lo "
                      "limitara.", i=i, name=name, f=f, limit=limit)
                )
        op_list = _moves_for(op, job)
        moves += op_list
        per_op.append(op_list)
        reach = _reach_warning(i, op, job.tool.diameter)
        if reach:
            warnings.append(reach)
            alarms.append(reach)

    spacing = _spacing_warnings(job)
    warnings += spacing
    alarms += spacing

    xs = [m.x for m in moves if m.x is not None]
    ys = [m.y for m in moves if m.y is not None]
    zs = [m.z for m in moves if m.z is not None]
    width, height = max(xs) - min(xs), max(ys) - min(ys)
    if width > machine.travel[0] or height > machine.travel[1]:
        warnings.append(
            _("El trabajo ocupa {width:.1f} x {height:.1f} mm y el recorrido es {travel:g} x {travel2:g} mm.", width=width, height=height, travel=machine.travel[0], travel2=machine.travel[1])
        )
    min_z = min(zs)

    comments = [
        _("Generado por cnc_app"),
        machine_comment(machine),
        _("Material {thickness:g} mm | extra sufridor {spoilboard_extra:g} mm | Z min {min_z:.3f} mm", thickness=job.stock.thickness, spoilboard_extra=job.stock.spoilboard_extra, min_z=min_z),
        _("Fresa {tool_label} | {len} operacion(es)", tool_label=tool_label(job.tool), len=len(job.operations)),
    ]
    if job.tool.rpm is not None:
        comments.append(_("RPM aconsejadas (variador): {rpm:g}", rpm=job.tool.rpm))
    comments += [
        _("XY de la trayectoria: X[{min:.3f}, {max:.3f}] Y[{min2:.3f}, {max2:.3f}] mm", min=min(xs), max=max(xs), min2=min(ys), max2=max(ys)),
    ]
    text = to_gcode(moves, comments=comments, safe_z=job.safe_z, opts=post)
    bounds = (min(xs), min(ys), max(xs), max(ys))
    return GcodeResult(text=text, min_z=min_z, warnings=warnings, bounds=bounds, op_moves=per_op, alarms=alarms)
