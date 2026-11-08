"""Las fichas de maquina (maquinas.json) y el lector del log de arranque de GRBL. Sin Qt.

Una ficha dice como es una maquina: el recorrido (lo que cabe), los avances maximos, si tiene homing y limites... Es la
llave de todo lo que dependa de la maquina: cada trabajo lleva la suya, y el programa solo da por bueno lo que esa ficha
permite. Se rellena pegando lo que UGS muestra al conectar (la linea `Grbl 1.1h ['$' for help]`, el `$$` y el `$I`) o
escribiendo los valores a mano: el ordenador que manda a la maquina suele ser otro, sin el programa.

Formato de `maquinas.json`: una ficha por maquina, con claves en espanol. Solo `recorrido` es obligatorio.

    {
      "maquinas": {
        "real": {"nombre": "Maquina del taller", "recorrido": [651, 1011, 100], "avance_max": 3600,
                 "homing": true}
      }
    }

El cargador es estricto a proposito, como el de las fresas: una clave mal escrita, un duplicado o un valor absurdo dan un
error que dice la ficha y el campo, en vez de cortar con un valor que no era.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Optional, Union

from .model import MachineProfile
from .i18n import _, number as number_text
from .records import check_fields, clean, parse_records, positive, suggest

DEFAULT_MACHINES_FILE = "maquinas.json"
DEFAULT_MACHINE_ID = "real"          # la ficha que usa la biblioteca sin maquinas.json (el que se entrega solo trae el emulador)

# clave del JSON -> descripcion (el orden es el de escritura)
FIELDS = {
    "nombre": _("texto libre"),
    "recorrido": _("[X, Y, Z] en mm ($130, $131, $132): obligatorio"),
    "avance_max": _("mm/min en XY ($110 y $111; el menor de los dos)"),
    "avance_max_z": _("mm/min en Z ($112)"),
    "aceleracion": _("[X, Y, Z] en mm/s2 ($120, $121, $122): informativo"),
    "limites_blandos": _("true o false ($20)"),
    "limites_duros": _("true o false ($21)"),
    "homing": _("true o false ($22): sin homing, $H da error"),
    "husillo_max": _("RPM maximas ($30): informativo, el husillo de aqui es on/off"),
    "laser": _("true o false ($32): con el modo laser, M3 no arranca un husillo normal"),
    "firmware": _("texto, p. ej. Grbl 1.1h"),
    "origen": _("texto: de donde salieron los datos (log del dia, a mano...)"),
    "notas": _("texto libre"),
}

HELP_TEXT = (
    _("Una ficha por maquina. Obligatorio: recorrido [X, Y, Z] en mm ($130-$132). Resto opcional: nombre, avance_max "
    "(mm/min en XY), avance_max_z, aceleracion [X, Y, Z], limites_blandos, limites_duros, homing, laser (true o "
    "false), husillo_max, firmware, origen, notas. Lo que falte no se comprueba. Se rellena con "
    "Maquinas... > Importar de un log (lo que UGS muestra al conectar).")
)


class MachinesFileError(ValueError):
    """Error en el archivo de maquinas (el mensaje dice donde)."""


_positive = partial(positive, error=MachinesFileError)


def _triple(v, where: str) -> tuple:
    if not isinstance(v, list) or len(v) != 3:
        raise MachinesFileError(_("{where} debe ser una lista de tres números [X, Y, Z] (hay {v!r}).", where=where, v=v))
    return tuple(_positive(x, _("{where}, cada valor", where=where)) for x in v)


def _bool(v, where: str) -> bool:
    if not isinstance(v, bool):
        raise MachinesFileError(_("{where} debe ser true o false (hay {v!r}).", where=where, v=v))
    return v


def parse_machine(ident: str, raw: dict, source: str = "maquinas.json") -> MachineProfile:
    """Valida una ficha y la convierte en MachineProfile."""
    where = _("{source}, ficha '{ident}'", source=source, ident=ident)
    if not isinstance(raw, dict):
        raise MachinesFileError(_("{where}: debe ser un objeto {{...}} con los campos de la máquina.", where=where))
    check_fields(raw, FIELDS, where, MachinesFileError)
    if "recorrido" not in raw:
        raise MachinesFileError(_("{where}: falta el campo obligatorio 'recorrido' ([X, Y, Z] en mm: $130-$132). Sin él no se sabe "
                                  "lo que cabe.", where=where))
    kw: dict = {"travel": _triple(raw["recorrido"], _("{where}: 'recorrido'", where=where))}
    for key in ("nombre", "firmware", "origen", "notas"):
        if key in raw and not isinstance(raw[key], str):
            raise MachinesFileError(_("{where}: '{key}' debe ser texto.", where=where, key=key))
    kw["name"] = raw.get("nombre") or ident
    kw["firmware"] = raw.get("firmware", "")
    kw["source"] = raw.get("origen", "")
    kw["notes"] = raw.get("notas", "")
    for key, attr in (("avance_max", "max_feed"), ("avance_max_z", "max_feed_z"), ("husillo_max", "spindle_max")):
        kw[attr] = _positive(raw[key], f"{where}: '{key}'") if key in raw else None
    kw["accel"] = _triple(raw["aceleracion"], _("{where}: 'aceleracion'", where=where)) if "aceleracion" in raw else None
    for key, attr in (("limites_blandos", "soft_limits"), ("limites_duros", "hard_limits"), ("homing", "homing"),
                      ("laser", "laser_mode")):
        kw[attr] = _bool(raw[key], f"{where}: '{key}'") if key in raw else None
    return MachineProfile(**kw)


def parse_machines(text: str, source: str = DEFAULT_MACHINES_FILE) -> dict[str, MachineProfile]:
    return parse_records(text, source, "maquinas", _("máquina"), MachinesFileError, parse_machine)


def load_machines(path: Union[str, Path]) -> dict[str, MachineProfile]:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise MachinesFileError(_("No existe el archivo de máquinas {path}.", path=path)) from None
    except OSError as e:
        raise MachinesFileError(_("No se puede leer {path}: {e}", path=path, e=e.strerror or e)) from None
    return parse_machines(text, path.name)


def machine_to_dict(machine: MachineProfile, ident: str = "") -> dict:
    """La ficha en el formato del JSON (sin los campos vacios). Un nombre igual al identificador no se escribe."""
    out: dict = {}
    if machine.name and machine.name != ident:
        out["nombre"] = machine.name
    out["recorrido"] = [clean(v) for v in machine.travel]
    if machine.max_feed is not None:
        out["avance_max"] = clean(machine.max_feed)
    if machine.max_feed_z is not None:
        out["avance_max_z"] = clean(machine.max_feed_z)
    if machine.accel is not None:
        out["aceleracion"] = [clean(v) for v in machine.accel]
    for key, value in (("limites_blandos", machine.soft_limits), ("limites_duros", machine.hard_limits),
                       ("homing", machine.homing)):
        if value is not None:
            out[key] = value
    if machine.spindle_max is not None:
        out["husillo_max"] = clean(machine.spindle_max)
    if machine.laser_mode is not None:
        out["laser"] = machine.laser_mode
    for key, value in (("firmware", machine.firmware), ("origen", machine.source), ("notas", machine.notes)):
        if value:
            out[key] = value
    return out


def _json_line(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def save_machines(machines: dict[str, MachineProfile], path: Union[str, Path]) -> None:
    """Escribe el JSON (con la ayuda estandar). Una ficha por maquina, un campo por linea: legible y editable a mano."""
    lines = ["{", f'  "_ayuda": {_json_line(HELP_TEXT)},', '  "maquinas": {']
    items = list(machines.items())
    for n_, (ident, machine) in enumerate(items):
        fields = list(machine_to_dict(machine, ident).items())
        lines.append(f"    {_json_line(ident)}: {{")
        for m_, (key, value) in enumerate(fields):
            lines.append(f"      {_json_line(key)}: {_json_line(value)}" + ("," if m_ < len(fields) - 1 else ""))
        lines.append("    }" + ("," if n_ < len(items) - 1 else ""))
    lines += ["  }", "}", ""]
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def get_machine(machines: dict[str, MachineProfile], ident: str) -> MachineProfile:
    try:
        return machines[ident]
    except KeyError:
        known = ", ".join(machines) or "(ninguna)"
        raise MachinesFileError(_("No hay ninguna máquina '{ident}'. Máquinas: {known}.{suggest}", ident=ident, known=known, suggest=suggest(ident, machines))) from None


def choose_machine(machines: dict[str, MachineProfile], wanted: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """(identificador, motivo): la maquina que toca. Si se pide una, esa (o el motivo de que no esta); si no, la unica que
    haya. Con varias y ninguna elegida, o ninguna ficha, no se elige por nadie: el motivo dice como elegir."""
    if wanted:
        if wanted in machines:
            return wanted, None
        try:
            get_machine(machines, wanted)
        except MachinesFileError as e:
            return None, str(e)
    if len(machines) == 1:
        return next(iter(machines)), None
    if not machines:
        return None, (_("No hay ninguna ficha de máquina: impórtala de un log (Máquinas… en la ventana)."))
    return None, _("Hay varias máquinas ({join}) y no se ha elegido ninguna.", join=', '.join(machines))


# --- numeros y textos -------------------------------------------------------------------------------------------
def n(value: float) -> str:
    """Un numero con el separador decimal del idioma (coma en español) y sin ceros de mas."""
    return number_text(value)


def travel_text(machine: MachineProfile) -> str:
    return " × ".join(n(v) for v in machine.travel) + _(" mm")


def _yes(v: Optional[bool]) -> str:
    return _("sin dato") if v is None else (_("sí") if v else _("no"))


def _fmt3(v) -> str:
    return _("sin dato") if v is None else " × ".join(n(x) for x in v)


def _fmt1(v) -> str:
    return _("sin dato") if v is None else n(v)


# (etiqueta, funcion que saca el valor de la ficha, como se escribe)
_COMPARE = (
    (_("recorrido (mm)"), lambda m: m.travel, _fmt3),
    (_("avance máx. XY (mm/min)"), lambda m: m.max_feed, _fmt1),
    (_("avance máx. Z (mm/min)"), lambda m: m.max_feed_z, _fmt1),
    (_("aceleración (mm/s²)"), lambda m: m.accel, _fmt3),
    (_("límites blandos"), lambda m: m.soft_limits, _yes),
    (_("límites duros"), lambda m: m.hard_limits, _yes),
    ("homing", lambda m: m.homing, _yes),
    (_("husillo máx. (RPM)"), lambda m: m.spindle_max, _fmt1),
    (_("modo láser"), lambda m: m.laser_mode, _yes),
)


def differences(old: MachineProfile, new: MachineProfile) -> list[str]:
    """Una frase por cada dato que cambia de una ficha a otra: `recorrido (mm): 650 × 1017 × 100 → 651 × 1011 × 100`.
    El nombre, el firmware, el origen y las notas no cuentan: no cambian lo que la maquina permite."""
    out = []
    for label, get, fmt in _COMPARE:
        a, b = get(old), get(new)
        if a != b:
            out.append(f"{label}: {fmt(a)} → {fmt(b)}")
    return out


# --- el log de arranque ------------------------------------------------------------------------------------------
_NUM = r"[-+]?\d+(?:[.,]\d+)?"
_SETTING_EQ = re.compile(rf"\$(\d{{1,3}})\s*=\s*({_NUM})")
_SETTING_TYPED = re.compile(rf"^\s*\$(\d{{1,3}})\s+({_NUM})\s*(?:[(\[;].*)?$")
_BANNER = re.compile(r"\bGrbl\s+(\d+\.\d+[A-Za-z0-9.]*)", re.IGNORECASE)
_VER = re.compile(r"\[VER:([^\]]*)\]", re.IGNORECASE)

# lo que se lee de cada ajuste: ($N, que es)
SETTING_NAMES = {110: _("avance máx. X"), 111: _("avance máx. Y"), 112: _("avance máx. Z"), 120: _("aceleración X"),
                 121: _("aceleración Y"), 122: _("aceleración Z"), 130: _("recorrido X"), 131: _("recorrido Y"),
                 132: _("recorrido Z"), 20: _("límites blandos"), 21: _("límites duros"), 22: "homing", 30: _("husillo máx."),
                 32: _("modo láser")}
_BOOLEAN = (20, 21, 22, 32)


@dataclass
class ParsedLog:
    """Lo que se entendio de un log. Es una lectura, no una ficha: `machine_from_log` la convierte en ficha."""

    settings: dict = field(default_factory=dict)       # numero de ajuste ($N) -> valor
    firmware: str = ""
    problems: list = field(default_factory=list)       # lo que impide usar el log (hay que corregirlo)
    notes: list = field(default_factory=list)          # lo que se leyo con reservas, o no estaba

    @property
    def has_travel(self) -> bool:
        return all(k in self.settings for k in (130, 131, 132))

    @property
    def usable(self) -> bool:
        return self.has_travel and not self.problems


def _value(text: str) -> float:
    """Un numero escrito con punto o con coma decimal."""
    return float(text.replace(",", "."))


def parse_log(text: str) -> ParsedLog:
    """Lee los `$N=valor` y el firmware de lo que se pegue: el `$$` y el `$I` que UGS muestra al conectar, o los valores
    escritos a mano (uno por linea: `$130=651` o `$130 651`). Lo que no reconoce se ignora; lo contradictorio (el mismo
    ajuste con dos valores) es un problema que hay que corregir."""
    out = ParsedLog()
    for line in text.splitlines():
        found = _SETTING_EQ.findall(line) or ([m.groups()] if (m := _SETTING_TYPED.match(line)) else [])
        for number, raw in found:
            key, value = int(number), _value(raw)
            if key in out.settings and out.settings[key] != value:
                name = SETTING_NAMES.get(key, f"${key}")
                out.problems.append(_("${key} ({name}) sale dos veces con valores distintos ({n} y {n2}): ¿hay dos logs mezclados?", key=key, name=name, n=n(out.settings[key]), n2=n(value)))
            out.settings[key] = value
        if not out.firmware:
            banner = _BANNER.search(line)
            if banner:
                out.firmware = _("Grbl {group}", group=banner.group(1))
    if not out.firmware:
        ver = _VER.search(text)
        number = re.match(r"\s*(\d+\.\d+[A-Za-z]?)", ver.group(1)) if ver else None
        if number:
            out.firmware = _("Grbl {group}", group=number.group(1))
    s = out.settings
    if not out.has_travel:
        missing = ", ".join(f"${k} ({SETTING_NAMES[k]})" for k in (130, 131, 132) if k not in s)
        out.notes.append(_("Falta {missing}: sin el recorrido no se sabe lo que cabe en la máquina.", missing=missing))
    for key in _BOOLEAN:
        if key in s and s[key] not in (0, 1):
            out.problems.append(_("${key} ({SETTING_NAMES}) debe ser 0 o 1 (hay {n}).", key=key, SETTING_NAMES=SETTING_NAMES[key], n=n(s[key])))
    for key in (110, 111, 112, 120, 121, 122, 130, 131, 132, 30):
        if key in s and not s[key] > 0:
            if key == 30 and s[key] == 0:
                continue
            out.problems.append(_("${key} ({SETTING_NAMES}) debe ser mayor que 0 (hay {n}).", key=key, SETTING_NAMES=SETTING_NAMES[key], n=n(s[key])))
    if 110 in s and 111 in s and s[110] != s[111]:
        out.notes.append(_("El avance máximo de X ({n}) y el de Y ({n2}) difieren: la ficha usa el menor.", n=n(s[110]), n2=n(s[111])))
    absent = [f"${k} ({SETTING_NAMES[k]})" for k in (110, 111, 112, 120, 121, 122, 20, 21, 22, 30, 32) if k not in s]
    if absent and out.has_travel:
        out.notes.append(_("No aparece: ") + ", ".join(absent) + _(". Lo que falte queda «sin dato» y no se comprueba."))
    return out


def machine_from_log(text: str, name: str = "", source: str = _("log pegado")) -> Optional[MachineProfile]:
    """La ficha que sale de un log, o None si no se puede (falta el recorrido o hay algo contradictorio: `parse_log` dice
    el motivo)."""
    parsed = parse_log(text)
    if not parsed.usable:
        return None
    s = parsed.settings
    feeds_xy = [s[k] for k in (110, 111) if k in s]
    accel = tuple(s[k] for k in (120, 121, 122)) if all(k in s for k in (120, 121, 122)) else None
    flag = lambda key: None if key not in s else bool(s[key])
    return MachineProfile(
        name=name, max_feed=min(feeds_xy) if feeds_xy else None, travel=(s[130], s[131], s[132]),
        max_feed_z=s.get(112), accel=accel, soft_limits=flag(20), hard_limits=flag(21), homing=flag(22),
        spindle_max=s.get(30) or None, laser_mode=flag(32), firmware=parsed.firmware, source=source)


def understood(parsed: ParsedLog) -> list[str]:
    """Lo que se entendio, en frases para ensenarselo a quien lo pega (que compruebe que es lo que ve)."""
    s = parsed.settings
    out = []
    if parsed.firmware:
        out.append(_("Firmware: {firmware}", firmware=parsed.firmware))
    if parsed.has_travel:
        out.append(_("Recorrido: {n} × {n2} × {n3} mm", n=n(s[130]), n2=n(s[131]), n3=n(s[132])))
    feeds = [f"{lab} {n(s[k])}" for k, lab in ((110, "X"), (111, "Y"), (112, "Z")) if k in s]
    if feeds:
        out.append(_("Avances máximos (mm/min): ") + ", ".join(feeds))
    accel = [f"{lab} {n(s[k])}" for k, lab in ((120, "X"), (121, "Y"), (122, "Z")) if k in s]
    if accel:
        out.append(_("Aceleración (mm/s²): ") + ", ".join(accel))
    for key in _BOOLEAN:
        if key in s and s[key] in (0, 1):
            out.append(f"{SETTING_NAMES[key].capitalize()}: {'sí' if s[key] else 'no'}")
    if 30 in s:
        out.append(_("Husillo máx.: {n} RPM", n=n(s[30])))
    return out
