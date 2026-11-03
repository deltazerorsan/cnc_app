"""Lo que comparten las fichas de fresas (herramientas.json) y las de maquina (maquinas.json), sin Qt.

Los dos archivos son el mismo tipo de cosa: un JSON con una ficha por identificador, leido de forma estricta, y un
editor que trabaja con formularios de textos y no escribe nada hasta `save`. Aqui esta lo comun de ambos (leer el
contenedor, validar campos, sugerir la clave bien escrita) y la lista de fichas en edicion; cada tipo pone solo sus
campos, su validacion y su forma de escribirse.
"""
from __future__ import annotations

import difflib
import json
import math
import re
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Union

from .i18n import _

BACKUP_SUFFIX = ".bak"

ID = re.compile(r"[A-Za-z][A-Za-z0-9_.-]*")


# --- el archivo ---------------------------------------------------------------------------------------------------
def is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def clean(x: float):
    """Un numero sin el `.0` de mas (el JSON queda como lo escribiria una persona)."""
    return int(x) if float(x).is_integer() else x


def positive(v, where: str, error: type) -> float:
    if not is_number(v) or v <= 0:
        raise error(_("{where} debe ser un número mayor que 0 (hay {v!r}).", where=where, v=v))
    return float(v)


def suggest(word: str, options) -> str:
    close = difflib.get_close_matches(word, list(options), n=1)
    return _(" ¿Quisiste decir '{close}'?", close=close[0]) if close else ""


def check_fields(raw: dict, fields, where: str, error: type) -> None:
    """Falla si la ficha trae un campo que no existe, con la clave bien escrita si se parece a alguna."""
    unknown = [k for k in raw if k not in fields]
    if unknown:
        hints = "".join(suggest(k, fields) for k in unknown)
        raise error(_("{where}: campo(s) desconocido(s) {join}.{hints} Campos válidos: {join2}.", where=where,
                      join=", ".join(repr(k) for k in unknown), hints=hints, join2=", ".join(fields)))


def _unique_pairs(pairs, error: type):
    out = {}
    for key, value in pairs:
        if key in out:
            raise error(_("la clave '{key}' está repetida (se pisarían los datos de una ficha).", key=key))
        out[key] = value
    return out


def parse_records(text: str, source: str, key: str, noun: str, error: type, parse_one: Callable[[str, Any, str], Any],
                  id_hint: str = "") -> dict:
    """El contenedor `{"<key>": {"<id>": {...ficha...}}}`: JSON valido sin claves repetidas, sin claves de mas y con
    identificadores que valgan; cada ficha pasa por `parse_one(ident, raw, source)`. `noun` ("fresa", "maquina") es lo
    que lleva una ficha, para el mensaje."""
    try:
        data = json.loads(text, object_pairs_hook=lambda pairs: _unique_pairs(pairs, error))
    except json.JSONDecodeError as e:
        raise error(_("{source}: JSON inválido en la línea {lineno}, columna {colno}: {msg}.", source=source,
                      lineno=e.lineno, colno=e.colno, msg=e.msg)) from None
    except error as e:
        raise error(f"{source}: {e}") from None
    if not isinstance(data, dict):
        raise error(_("{source}: debe contener un objeto con la clave '{key}'.", source=source, key=key))
    extra = [k for k in data if k != key and not k.startswith("_")]
    if extra:
        raise error(_("{source}: clave(s) desconocida(s) {join} (solo '{key}').", source=source,
                      join=", ".join(repr(k) for k in extra), key=key))
    entries = data.get(key)
    if not isinstance(entries, dict):
        raise error(_("{source}: falta la clave '{key}' con una ficha por {noun}.", source=source, key=key, noun=noun))
    out = {}
    for ident, raw in entries.items():
        if not ID.fullmatch(ident):
            raise error(_("{source}: el identificador '{ident}' no vale: empieza por una letra y usa solo letras, "
                          "números, '_', '-' o '.'{hint}.", source=source, ident=ident, hint=id_hint))
        out[ident] = parse_one(ident, raw, source)
    return out


# --- la edicion ---------------------------------------------------------------------------------------------------
@dataclass
class Entry:
    """Una ficha en edicion: su formulario y, si es valida, el dato resultante (`record`: la fresa o la maquina)."""

    ident: str
    form: dict[str, str]
    original: Optional[str] = None          # identificador que tenia en el archivo (None: ficha nueva)
    record: Any = None
    error: str = ""

    @property
    def valid(self) -> bool:
        return self.record is not None and not self.error


class Editor:
    """Lista de fichas en edicion. Nada se escribe hasta `save`.

    Cada tipo de ficha dice: la clase de error (`error_class`), la de entrada (`entry_class`), el formulario vacio de
    una ficha nueva (`blank`) y su identificador (`new_ident`), y como se pasa de ficha a formulario (`to_form`), de
    formulario a ficha (`build`, que lanza `error_class`) y de fichas a archivo (`write`)."""

    error_class: type = ValueError
    entry_class: type = Entry
    blank: dict[str, str] = {}
    new_ident = "ficha"
    to_form: Callable[[Any, str], dict[str, str]]
    build: Callable[[str, dict[str, str]], Any]
    write: Callable[[dict, Union[str, Path]], None]

    def __init__(self, records: Optional[dict] = None):
        self.entries: list = []
        self.initial_ids = list(records or {})
        for ident, record in (records or {}).items():
            self.entries.append(self.entry_class(ident, self.to_form(record, ident), original=ident))
        self.revalidate()

    # --- validacion ------------------------------------------------------------------------------------------
    def revalidate(self) -> None:
        counts = Counter(e.ident for e in self.entries)
        for e in self.entries:
            e.record, e.error = None, ""
            try:
                if counts[e.ident] > 1:
                    raise self.error_class(_("hay otra ficha con el identificador '{ident}'.", ident=e.ident))
                e.record = self.build(e.ident, e.form)
            except self.error_class as err:
                e.error = str(err)

    @property
    def valid(self) -> bool:
        return all(e.valid for e in self.entries)

    def errors(self) -> list[str]:
        return [f"{e.ident or _('(sin identificador)')}: {e.error}" for e in self.entries if not e.valid]

    # --- cambios ---------------------------------------------------------------------------------------------------
    def update(self, index: int, ident: Optional[str] = None, form: Optional[dict[str, str]] = None):
        e = self.entries[index]
        if ident is not None:
            e.ident = ident.strip()
        if form is not None:
            e.form = dict(form)
        self.revalidate()
        return e

    def unique(self, base: str) -> str:
        used = {e.ident for e in self.entries}
        if base not in used:
            return base
        k = 2
        while f"{base}_{k}" in used:
            k += 1
        return f"{base}_{k}"

    def new(self) -> int:
        """Ficha vacia: no vale hasta que tenga lo obligatorio."""
        self.entries.append(self.entry_class(self.unique(self.new_ident), dict(self.blank)))
        self.revalidate()
        return len(self.entries) - 1

    def duplicate(self, index: int) -> int:
        """Copia de una ficha (para el mismo caso con otro material, otro ajuste...)."""
        src = self.entries[index]
        self.entries.insert(index + 1, self.entry_class(self.unique(src.ident + _("_copia")), dict(src.form)))
        self.revalidate()
        return index + 1

    def remove(self, index: int) -> None:
        del self.entries[index]
        self.revalidate()

    # --- resultado ---------------------------------------------------------------------------------------------------
    def records(self) -> dict:
        if not self.valid:
            raise self.error_class("; ".join(self.errors()))
        return {e.ident: e.record for e in self.entries}

    def renamed(self) -> dict[str, str]:
        """{identificador antiguo: nuevo} de las fichas que cambiaron de nombre."""
        return {e.original: e.ident for e in self.entries if e.original is not None and e.original != e.ident}

    def removed(self) -> list[str]:
        """Identificadores del archivo que ya no estan (borrados; un cambio de nombre no cuenta)."""
        kept = {e.original for e in self.entries if e.original is not None}
        return [i for i in self.initial_ids if i not in kept]

    def save(self, path: Union[str, Path]) -> Path:
        """Escribe el archivo; si ya existia deja el anterior con el sufijo `.bak`."""
        records = self.records()
        path = Path(path)
        if path.exists():
            shutil.copyfile(path, path.with_name(path.name + BACKUP_SUFFIX))
        self.write(records, path)
        return path


def require_ident(ident: str, error: type, rule: str) -> None:
    """Falla si el identificador esta vacio o no sigue `ID`; `rule` es la explicacion de que se admite."""
    if not ident:
        raise error(_("falta el identificador de la ficha."))
    if not ID.fullmatch(ident):
        raise error(rule)
