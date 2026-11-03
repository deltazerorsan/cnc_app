"""Los topes: lo que el programa no genera nunca, y lo que se genera solo si quien lo maneja lo confirma.

Sin Qt. Estudiar una pieza mal es barato; estropear material y fresas no. Por eso estas reglas son iguales para
todos, expertos o no: un tope no se salta, se corrige el dato. Lo que no se puede saber desde aqui (el grosor real,
la fresa que hay montada, donde esta el cero) lo confirma la persona en la lista de comprobacion (`cnc.checklist`).

Los numeros estan todos aqui, en un solo sitio:

* margen en el sufridor: como mucho 0,5 mm (0,2 basta con el sufridor recien planeado; 0,4 asegura el corte aunque
  haya imperfecciones debajo de la pieza);
* grosor: por encima de 22 mm es una alarma que hay que confirmar; por encima de 30 mm no se genera (haria falta otra
  fresa y otro planteamiento);
* pasada: como mucho 2 veces el diametro de la fresa;
* maquina (su ficha, `cnc.machines`): lo que cabe en Z, que el trabajo no pida Home a una maquina sin homing y que no
  tenga el modo laser con el husillo arrancado por el programa. Lo que la ficha no sabe (None) no se comprueba.
"""
from __future__ import annotations

from typing import Optional, Sequence

from .model import DrillOp, Stock, Tool, resolved
from .toolpath import pass_depths, total_depth
from .verify_nc import analyze
from .i18n import _, number as number_text

MARGIN_MAX = 0.5            # mm: lo que se entra en el sufridor, como mucho
THICKNESS_ALARM = 22.0      # mm: por encima, se pide confirmar el grosor
THICKNESS_MAX = 30.0        # mm: por encima, no se genera
STEP_FACTOR = 2.0           # la pasada maxima es esta cantidad de veces el diametro de la fresa
EPS = 1e-9


def _mm(value: float) -> str:
    """Un numero en mm con el separador decimal del idioma (coma en español)."""
    return number_text(value)


# --- ajustes del trabajo ------------------------------------------------------------------------------------
def margin_problem(margin: float) -> Optional[str]:
    if margin > MARGIN_MAX + EPS:
        return (_("el margen en el sufridor ({mm} mm) pasa del maximo de {mm2} mm: con 0,2 mm basta si el sufridor "
                  "esta recien planeado y 0,4 mm asegura el corte aunque haya imperfecciones debajo de la pieza.", mm=_mm(margin), mm2=_mm(MARGIN_MAX)))
    return None


def thickness_problem(thickness: float) -> Optional[str]:
    if thickness > THICKNESS_MAX + EPS:
        return (_("el grosor del material ({mm} mm) pasa de {mm2} mm, que no se puede cortar con estas fresas. ¿Es "
                  "un error al escribirlo?", mm=_mm(thickness), mm2=_mm(THICKNESS_MAX)))
    return None


def thickness_alarm(thickness: float) -> Optional[str]:
    """Una alarma (no un bloqueo): el grosor esta entre 22 y 30 mm y hay que confirmarlo."""
    if THICKNESS_ALARM + EPS < thickness <= THICKNESS_MAX + EPS:
        return _("Grosor de {mm} mm: pasa de {mm2} mm (el maximo es {mm3} mm).", mm=_mm(thickness), mm2=_mm(THICKNESS_ALARM), mm3=_mm(THICKNESS_MAX))
    return None


def settings_problems(thickness: float, margin: float) -> list[str]:
    return [p for p in (thickness_problem(thickness), margin_problem(margin)) if p]


# --- pasadas -------------------------------------------------------------------------------------------------
def max_step(diameter: float) -> float:
    return STEP_FACTOR * diameter


def operation_steps(operations: Sequence, stock: Stock, tool: Tool) -> list[float]:
    """La profundidad de cada pasada (mm, positiva) de cada operacion, tal como se va a cortar: pasadas iguales con
    los datos de la operacion, o los de la ficha, o los de respaldo. Un taladro sin picoteo es una sola bajada."""
    steps = []
    for raw in operations:
        op = resolved(raw, tool)
        total = total_depth(op, stock)
        if isinstance(op, DrillOp):
            depths = [-total] if op.peck is None else pass_depths(total, op.peck)
        else:
            depths = pass_depths(total, op.stepdown)
        steps.append(total / len(depths))
    return steps


def operation_problems(operations: Sequence, stock: Stock, tool: Tool) -> list[str]:
    """Una frase por cada operacion cuya pasada pasa del maximo (2 x el diametro). Puede lanzar ValueError si una
    operacion no tiene profundidad valida."""
    limit = max_step(tool.diameter)
    out = []
    for i, step in enumerate(operation_steps(operations, stock, tool), start=1):
        if step > limit + EPS:
            out.append(_("Operacion {i}: la pasada seria de {mm} mm y el maximo es {mm2} mm (2 x el diametro de {mm3} "
                         "mm). Pon una pasada menor en la ficha de la fresa, en los ajustes o en la fila de la tabla (sin "
                         "pasada, se corta de una sola vez).", i=i, mm=_mm(step), mm2=_mm(limit), mm3=_mm(tool.diameter)))
    return out


# --- la maquina ---------------------------------------------------------------------------------------------
def home_problem(machine, home_at_end: bool) -> Optional[str]:
    """El trabajo (o el programa de marcas) termina con Home y la ficha dice que la maquina no tiene homing."""
    if home_at_end and machine.homing is False:
        return (_("La maquina «{v}» no tiene homing ($22=0) y se pide terminar con Home ($H): GRBL daria error. "
                  "Quita «Home al terminar», o corrige la ficha si la maquina si lo tiene.", v=machine.name or 'sin nombre'))
    return None


def machine_problems(machine, *, depth: float, safe_z: float, home_at_end: bool, emit_spindle: bool) -> list[str]:
    """Una frase por cada cosa que la ficha de la maquina no permite del trabajo (bloqueos, no alarmas): `depth` es lo mas
    hondo que baja la fresa (mm, positivo) y `safe_z` la altura de desplazamiento sobre la superficie. El recorrido en
    X e Y no esta aqui: ya lo bloquea el plan con la trayectoria real."""
    name = machine.name or _("sin nombre")
    out = []
    span = depth + safe_z
    if span > machine.travel[2] + EPS:
        out.append(_("No cabe en Z: la fresa baja {mm} mm y sube hasta {mm2} mm sobre la superficie ({mm3} mm en "
                     "total) y el recorrido de Z de la maquina «{name}» es de {mm4} mm.", mm=_mm(depth), mm2=_mm(safe_z), mm3=_mm(span), name=name, mm4=_mm(machine.travel[2])))
    home = home_problem(machine, home_at_end)
    if home:
        out.append(home)
    if emit_spindle and machine.laser_mode:
        out.append(_("La maquina «{name}» tiene el modo laser ($32=1): con el, GRBL apaga el husillo en cada "
                     "movimiento rapido (G0) y lo vuelve a encender al cortar. Pon $32=0 en la maquina (y en la "
                     "ficha), o quita el arranque del husillo del G-code.", name=name))
    return out


# --- el programa ya escrito ---------------------------------------------------------------------------------
def gcode_problems(text: str) -> list[str]:
    """Lo que el verificador independiente encuentra mal en el G-code (corte por encima de la superficie, sin avance,
    husillo apagado, lineas de mas de 79 caracteres...). Vacio si pasa. Los codigos que no reconoce no cuentan: pueden
    ser del codigo final que escribe el usuario (M9, por ejemplo), y los del generador ya los vigilan las pruebas. El
    recorrido de la maquina tampoco se mira aqui: ya lo bloquea el plan con su propio mensaje."""
    return list(analyze(text).issues)
