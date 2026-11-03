"""La lista de comprobacion antes de generar: lo que el programa sabe, a la vista, y lo que solo sabe la persona, a confirmar.

Sin Qt. Como las listas de los pilotos: no se pide saber, se pide mirar y confirmar, y es la misma para todos. Tiene dos
partes:

* **Datos** (los calcula el programa y no se confirman): la fresa, el grosor y la profundidad, las pasadas, los avances,
  lo que ocupa la trayectoria frente al recorrido de la maquina, donde queda el cero...
* **Confirmaciones** (cada una con su casilla): lo que el programa no puede comprobar, como la fresa montada, el grosor
  real, el cero puesto, la sujecion y el husillo; y las alarmas del trabajo (grosor grande, piezas muy juntas, zonas que
  la fresa no alcanza, trayectoria fuera del tablero), en rojo.

La ventana (`cnc/gui/checklist_dialog.py`) solo pinta esto; no deja generar hasta que todas las casillas estan marcadas.

**Desconectada de momento (v0.23)**: resulto exagerada para el uso diario. `ENABLED` es el unico interruptor: apagado, generar
escribe sin abrir la lista y la linea de comandos no la imprime. Todo lo demas sigue igual: los topes (no cabe, margen, grosor,
pasada...) siguen bloqueando y las alarmas siguen saliendo en rojo en el resumen y en *Mensajes*, aunque ya no hay que
confirmarlas. El contenido y la ventana se mantienen con sus pruebas para volver a conectarla; la idea es que mas adelante se
active y desactive desde un archivo externo, para que no sea facil de cambiar por descuido.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from . import safety
from .generate import tool_label
from .model import ContourOp, DrillOp, PocketOp, resolved
from .timing import describe, format_duration
from .toolpath import total_depth
from .i18n import _, number as number_text


ENABLED = False                    # el interruptor de la lista de comprobacion (ver arriba)

ALARM_CONFIRM = _("Lo he mirado y sigo adelante.")


def _n(value: float) -> str:
    """Un numero con el separador decimal del idioma (coma en español) y sin ceros de mas."""
    return number_text(value)


def _n1(value: float) -> str:
    """Una medida de la trayectoria: un decimal."""
    return number_text(value, 1, strip=False)


@dataclass(frozen=True)
class Check:
    text: str                  # la frase que se confirma
    alarm: bool = False        # una alarma del trabajo: se pinta en rojo


@dataclass
class Section:
    title: str                 # "" si solo hay un trabajo; si no, "Tablero 3"
    rows: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Checklist:
    sections: list[Section] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)


def _label(number: int) -> str:
    return _("Tablero {number}", number=number)


def _origin_text(plan) -> str:
    s = plan.settings
    if plan.stock and not plan.virtual_stock:
        return _("la esquina inferior izquierda del tablero")
    if s is not None and s.origin == "center":
        return _("el centro de lo que se corta")
    if s is not None and s.origin == "page_bottom_left":
        return _("la esquina inferior izquierda de la pagina del dibujo")
    margin = s.origin_margin if s is not None else 0.0
    return (_("la esquina inferior izquierda de lo que se corta") if not margin
            else _("la esquina inferior izquierda de lo que se corta, {n} mm por fuera", n=_n(margin)))


def _ending_text(settings) -> str:
    if settings.home_at_end:
        return _("hace Home ($H, hace falta $22=1)")
    if settings.return_to_origin:
        return _("vuelve al cero (X0 Y0) a la Z segura")
    return _("sube a la Z segura y se queda donde acaba")


def _cuts_text(operations: Sequence) -> str:
    sides = {"outside": "exteriores", "inside": "interiores", "on": _("sobre la linea")}
    counts: dict[str, int] = {}
    pockets = drills = 0
    for op in operations:
        if isinstance(op, ContourOp):
            counts[sides[op.side]] = counts.get(sides[op.side], 0) + 1
        elif isinstance(op, PocketOp):
            pockets += 1
        elif isinstance(op, DrillOp):
            drills += len(op.points)
    parts = [f"{v} {k}" for k, v in counts.items()]
    if pockets:
        parts.append(_("{pockets} vaciado(s)", pockets=pockets))
    if drills:
        parts.append(_("{drills} taladro(s)", drills=drills))
    return ", ".join(parts) or "ninguno"


def _range(values: set) -> str:
    low, high = min(values), max(values)
    return _n(low) if low == high else _("{n} a {n2}", n=_n(low), n2=_n(high))


def _feeds_text(operations: Sequence, tool) -> str:
    cut: set = set()
    plunge: set = set()
    for raw in operations:
        op = resolved(raw, tool)
        if getattr(op, "feed_xy", None) is not None:
            cut.add(op.feed_xy)
        if getattr(op, "feed_z", None) is not None:
            plunge.add(op.feed_z)
    parts = []
    if cut:
        parts.append(_("corte {range}", range=_range(cut)))
    if plunge:
        parts.append(_("bajada {range}", range=_range(plunge)))
    return (", ".join(parts) + _(" mm/min")) if parts else _("sin dato")


def _machine_text(machine) -> str:
    return _("{v}, recorrido {n} x {n2} x {n3} mm", v=machine.name or 'sin nombre', n=_n(machine.travel[0]), n2=_n(machine.travel[1]), n3=_n(machine.travel[2]))


def _rows(plan) -> list[tuple[str, str]]:
    job, settings, result = plan.job, plan.settings, plan.result
    tool = job.tool
    stock = job.stock
    steps = safety.operation_steps(plan.operations, stock, tool)
    depth = max((total_depth(resolved(op, tool), stock) for op in plan.operations), default=0.0)
    minx, miny, maxx, maxy = result.bounds
    tx, ty = job.machine.travel[0], job.machine.travel[1]
    rpm = _(" · {n} RPM aconsejadas", n=_n(tool.rpm)) if tool.rpm is not None else ""
    spindle = (_("lo arranca la aplicación (M3 S{n})", n=_n(settings.post_options().spindle_s)) if settings.emit_spindle
               else _("NO lo arranca la aplicación: se arranca a mano"))
    rows = [
        (_("Maquina"), _machine_text(job.machine)),
        (_("Fresa"), _("{tool_label}, diametro {n} mm{rpm}", tool_label=tool_label(tool), n=_n(tool.diameter), rpm=rpm)),
        (_("Material"), _("{n} mm de grosor + {n2} mm en el sufridor = {n3} mm de profundidad", n=_n(stock.thickness), n2=_n(stock.spoilboard_extra), n3=_n(depth))),
        (_("Pasadas"), _("hasta {n} mm cada una (maximo {n2} mm)", n=_n(max(steps)), n2=_n(safety.max_step(tool.diameter)))
                    if steps else "ninguna"),
        (_("Avances"), _feeds_text(plan.operations, tool)),
        (_("Cortes"), _cuts_text(plan.operations)),
        (_("Trayectoria"), _("{n1} x {n12} mm, de {n} x {n2} mm de recorrido", n1=_n1(maxx - minx), n12=_n1(maxy - miny), n=_n(tx), n2=_n(ty))),
        (_("Tiempo"), describe(plan.time)),
        (_("Giro"), _("sin giro") if plan.degrees % 360 == 0 else _("{n} grados antihorario", n=_n(plan.degrees))),
        (_("Cero"), _origin_text(plan)),
        (_("Z segura"), _("{n} mm sobre la superficie", n=_n(job.safe_z))),
        (_("Husillo"), spindle),
        (_("Al terminar"), _ending_text(settings)),
    ]
    return rows


def _thickness_check(plans: Sequence[tuple]) -> Check:
    values = {}
    for number, plan in plans:
        values.setdefault(plan.job.stock.thickness, []).append(number)
    if len(values) == 1:
        return Check(_("El material mide {n} mm de grosor.", n=_n(next(iter(values)))))
    parts = [_("{join}: {n} mm", join=', '.join(_label(n) for n in numbers), n=_n(t)) for t, numbers in values.items()]
    return Check(_("El grosor del material es el indicado (") + "; ".join(parts) + ").")


def build_checklist(plans: Sequence[tuple]) -> Checklist:
    """La lista de los trabajos que se van a escribir: `plans` son pares (numero de tablero o None, JobPlan) ya planificados
    y sin errores."""
    out = Checklist()
    many = len(plans) > 1
    for number, plan in plans:
        out.sections.append(Section(_label(number) if many else "", _rows(plan)))
    if many:
        total = sum(plan.time.seconds for __, plan in plans)
        out.sections.append(Section(_("Todos los tableros"),
                                    [(_("Tiempo"), _("unos {format_duration} de maquina en total", format_duration=format_duration(total)))]))

    first = plans[0][1]
    tool, settings = first.job.tool, first.settings
    out.checks.append(Check(_("La maquina conectada a UGS es «{v}» (recorrido {n} x {n2} x {n3} mm): este G-code es para ella.", v=first.job.machine.name or 'sin nombre', n=_n(first.job.machine.travel[0]), n2=_n(first.job.machine.travel[1]), n3=_n(first.job.machine.travel[2]))))
    out.checks.append(Check(_("La fresa montada es {tool_label} (diametro {n} mm) y esta bien apretada.", tool_label=tool_label(tool), n=_n(tool.diameter))))
    out.checks.append(_thickness_check(plans))
    out.checks.append(Check(_("El cero (X0 Y0) esta en {origin_text}; Z0, en la cara superior del material.", origin_text=_origin_text(first))))
    out.checks.append(Check(
        _("El material esta sujeto (con los puntos de Sujecion, si los usas), y nada sobresale por encima "
          "de la Z segura ({n} mm) ni queda en la trayectoria.", n=_n(first.job.safe_z))))
    adjust = _("el variador esta ajustado para esta fresa y este material")
    if settings.emit_spindle:
        out.checks.append(Check(_("El husillo esta parado (lo arranca la aplicación) y {adjust}", adjust=adjust)
                                + (_(" ({n} RPM aconsejadas).", n=_n(tool.rpm)) if tool.rpm is not None else ".")))
    else:
        out.checks.append(Check(_("El husillo NO lo arranca la aplicación: lo arranco yo antes de empezar, y {adjust}.", adjust=adjust)))

    seen = set()
    for number, plan in plans:
        for text in plan.alarms:
            full = (f"{_label(number)}: " if many else "") + text
            if full not in seen:
                seen.add(full)
                out.checks.append(Check(f"{full} {ALARM_CONFIRM}", alarm=True))
    return out
