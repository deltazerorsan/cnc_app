"""Edicion de las fichas de maquina (maquinas.json) sin Qt: lo que usa el dialogo de la ventana.

Como con las fresas, cada ficha se edita como un formulario de textos (lo que se escribe en las casillas) y se valida con
el mismo `parse_machine` que lee el archivo: lo que el dialogo da por bueno es lo que el programa puede volver a leer.
Ademas se puede crear una ficha desde el log de arranque (`import_log`), que es la forma normal de hacerlo.
"""
from __future__ import annotations

from dataclasses import replace
from functools import partial
from typing import Optional

from .machines import (MachinesFileError, ParsedLog, differences, machine_from_log, machine_to_dict, n, parse_log,
                       parse_machine, save_machines)
from .model import MachineProfile
from .records import Editor, Entry, edit_error, form_number, require_ident
from .i18n import _

# Las casillas del formulario, en el orden en que se ensenan. Los tres numeros de un recorrido o una aceleracion son
# casillas aparte; los si/no se escriben "", "si" o "no" (vacio = sin dato).
FORM_FIELDS = ("nombre", "x", "y", "z", "avance_max", "avance_max_z", "acel_x", "acel_y", "acel_z", "limites_blandos",
               "limites_duros", "homing", "husillo_max", "laser", "firmware", "origen", "notas")
TEXT_FIELDS = ("nombre", "firmware", "origen", "notas")
FLAG_FIELDS = ("limites_blandos", "limites_duros", "homing", "laser")
NUMBER_FIELDS = tuple(k for k in FORM_FIELDS if k not in TEXT_FIELDS and k not in FLAG_FIELDS)
FLAG_CHOICES = (("", _("sin dato")), ("si", _("sí")), ("no", _("no")))


class MachineEditError(ValueError):
    """Un valor de la ficha que no vale (el mensaje dice cual)."""


def check_ident(ident: str) -> None:
    require_ident(ident, MachineEditError,
                  _("el identificador debe empezar por una letra y usar solo letras, números, '_', '-' o '.'."))


def _text(v: float) -> str:
    return f"{v:g}"


def machine_to_form(machine: MachineProfile, ident: str = "") -> dict[str, str]:
    """Los textos de las casillas de una ficha (vacio = sin dato). Un nombre igual al identificador se deja vacio."""
    form = {k: "" for k in FORM_FIELDS}
    form["nombre"] = "" if machine.name in ("", ident) else machine.name
    form["x"], form["y"], form["z"] = (_text(v) for v in machine.travel)
    if machine.max_feed is not None:
        form["avance_max"] = _text(machine.max_feed)
    if machine.max_feed_z is not None:
        form["avance_max_z"] = _text(machine.max_feed_z)
    if machine.accel is not None:
        form["acel_x"], form["acel_y"], form["acel_z"] = (_text(v) for v in machine.accel)
    for key, value in (("limites_blandos", machine.soft_limits), ("limites_duros", machine.hard_limits),
                       ("homing", machine.homing), ("laser", machine.laser_mode)):
        form[key] = "" if value is None else ("si" if value else "no")
    if machine.spindle_max is not None:
        form["husillo_max"] = _text(machine.spindle_max)
    form["firmware"], form["origen"], form["notas"] = machine.firmware, machine.source, machine.notes
    return form


_number = partial(form_number, error=MachineEditError)


def _flag(key: str, text: str) -> bool:
    word = text.strip().lower().replace("í", "i")
    if word in ("si", "true", "1"):
        return True
    if word in ("no", "false", "0"):
        return False
    raise MachineEditError(_("'{key}' debe ser sí, no o sin dato (hay {text!r}).", key=key, text=text))


def _triple(form: dict[str, str], keys: tuple, label: str, required: bool, article: str = "el"):
    texts = [(form.get(k) or "").strip() for k in keys]
    if not any(texts):
        if required:
            raise MachineEditError(_("falta {article} {label} (X, Y y Z: $130, $131, $132). Sin él no se sabe lo que cabe.", article=article, label=label))
        return None
    if not all(texts):
        missing = ", ".join(k[-1].upper() for k, t in zip(keys, texts) if not t)
        raise MachineEditError(_("{article} {label} tiene que tener las tres casillas (X, Y y Z); falta {missing}.", article=article, label=label, missing=missing))
    return [_number(f"{label} {k[-1].upper()}", t) for k, t in zip(keys, texts)]


def form_to_raw(form: dict[str, str]) -> dict:
    """El diccionario con la forma del JSON (las casillas vacias no se escriben)."""
    raw: dict = {}
    for key in TEXT_FIELDS:
        text = (form.get(key) or "").strip()
        if text:
            raw[key] = text
    raw["recorrido"] = _triple(form, ("x", "y", "z"), _("recorrido"), True)
    accel = _triple(form, ("acel_x", "acel_y", "acel_z"), _("aceleración"), False, "la")
    if accel is not None:
        raw["aceleracion"] = accel
    for key in ("avance_max", "avance_max_z", "husillo_max"):
        text = (form.get(key) or "").strip()
        if text:
            raw[key] = _number(key, text)
    for key in FLAG_FIELDS:
        text = (form.get(key) or "").strip()
        if text:
            raw[key] = _flag(key, text)
    return raw


def build_machine(ident: str, form: dict[str, str]) -> MachineProfile:
    """Ficha valida desde el formulario, o MachineEditError con el motivo en palabras de la ventana."""
    check_ident(ident)
    raw = form_to_raw(form)
    try:
        return parse_machine(ident, raw, "ficha")
    except MachinesFileError as e:
        raise edit_error(e, MachineEditError) from None


def advice(machine: MachineProfile) -> list[str]:
    """Lo que falta o limita en una ficha valida (informativo, no impide guardar)."""
    notes = []
    if machine.max_feed is None or machine.max_feed_z is None:
        notes.append(_("Sin avance máximo (XY o Z): la aplicación no avisa si un trabajo pide más de lo que la máquina da."))
    if machine.homing is None:
        notes.append(_("Sin dato de homing: no se puede avisar de que «Home» fallaría en una máquina sin él."))
    elif not machine.homing:
        notes.append(_("Sin homing: la lista de comprobación no manda hacer Home y el cero se marca a mano."))
    if machine.laser_mode is None:
        notes.append(_("Sin dato del modo láser ($32): no se puede avisar de que M3 no arrancaría el husillo."))
    return notes


class MachineEntry(Entry):
    """Una ficha en edicion: su formulario y, si es valido, la maquina resultante."""

    @property
    def machine(self) -> Optional[MachineProfile]:
        return self.record

    @property
    def label(self) -> str:
        if self.record is None:
            return self.ident
        size = " × ".join(n(v) for v in self.record.travel)
        if self.record.name and self.record.name != self.ident:
            return _("{ident} — {name} ({size} mm)", ident=self.ident, name=self.record.name, size=size)
        return _("{ident} ({size} mm)", ident=self.ident, size=size)


class MachineEditor(Editor):
    """Lista de fichas de maquina en edicion. Nada se escribe hasta `save`."""

    error_class = MachineEditError
    entry_class = MachineEntry
    blank = {k: "" for k in FORM_FIELDS}
    new_ident = "maquina"
    to_form = staticmethod(machine_to_form)
    build = staticmethod(build_machine)
    write = staticmethod(save_machines)
    machines = Editor.records

    def import_log(self, text: str, ident: str = "", name: str = "", source: str = _("log pegado")) -> int:
        """Anade una ficha leida de un log (ver `machines.parse_log`). Si el log no se puede usar lanza
        MachineEditError con el motivo, sin tocar la lista. Devuelve el indice de la ficha nueva."""
        machine = machine_from_log_checked(text, name, source)
        ident = (ident or "").strip() or self.unique("maquina")
        check_ident(ident)
        if any(e.ident == ident for e in self.entries):
            raise MachineEditError(_("ya hay una ficha con el identificador '{ident}'.", ident=ident))
        self.entries.append(MachineEntry(ident, machine_to_form(machine, ident)))
        self.revalidate()
        return len(self.entries) - 1

    def _refreshed(self, index: int, text: str, source: str) -> tuple[dict, list[str]]:
        entry = self.entries[index]
        if not entry.valid:
            raise MachineEditError(_("la ficha tiene errores: corrígelos antes de actualizarla desde un log."))
        new_form = machine_to_form(machine_from_log_checked(text, "", source), entry.ident)
        form = dict(entry.form)
        for key in FORM_FIELDS:
            if key not in ("nombre", "notas") and new_form[key]:
                form[key] = new_form[key]
        return form, differences(entry.machine, build_machine(entry.ident, form))

    def preview_refresh(self, index: int, text: str, source: str = _("log pegado")) -> list[str]:
        """Lo que cambiaria `refresh_from_log` (una frase por dato), sin tocar nada. Lanza MachineEditError si el log no
        sirve."""
        return self._refreshed(index, text, source)[1]

    def refresh_from_log(self, index: int, text: str, source: str = _("log pegado")) -> list[str]:
        """Actualiza una ficha existente con los datos de un log y devuelve lo que cambia respecto a la anterior (una frase
        por dato). Solo cambia lo que el log trae: lo que no aparezca en el log se queda como estaba. Mantiene el
        identificador, el nombre y las notas. Si el log no sirve, lanza MachineEditError sin tocar nada."""
        form, changes = self._refreshed(index, text, source)
        self.entries[index].form = form
        self.revalidate()
        return changes


def explain_log(parsed: ParsedLog) -> str:
    """Por que un log no sirve, en una frase para quien lo pego."""
    if parsed.problems:
        return " ".join(parsed.problems)
    if not parsed.settings:
        return (_("No se ha encontrado ningún ajuste ($130=651…). Pega lo que UGS muestra al conectar o al escribir $$ "
                "en su consola, o escribe los valores uno por línea."))
    return " ".join(parsed.notes[:1]) or _("El log no tiene los datos mínimos.")


def machine_from_log_checked(text: str, name: str = "", source: str = _("log pegado")) -> MachineProfile:
    """La ficha de un log o MachineEditError con el motivo. Pasa por `parse_machine` para que lo que sale cumpla las mismas
    reglas que lo leido de un archivo."""
    parsed = parse_log(text)
    machine = machine_from_log(text, name, source) if parsed.usable else None
    if machine is None:
        raise MachineEditError(explain_log(parsed))
    try:
        return replace(parse_machine("log", machine_to_dict(machine), "log"), name=machine.name)
    except MachinesFileError as e:
        raise edit_error(e, MachineEditError) from None
