"""Edicion de las fichas de fresas (herramientas.json) sin Qt: lo que usa el dialogo de la ventana.

Cada ficha se edita como un formulario de textos (lo que se escribe en las casillas) y se valida con el mismo
`parse_tool` que lee el archivo, asi que lo que el dialogo da por bueno es lo que el programa puede volver a leer.
"""
from __future__ import annotations

from typing import Optional, Union

from .model import Tool
from .records import Editor, Entry, require_ident
from .tools import FIELDS, ToolsFileError, parse_tool, save_tools, tool_to_dict
from .i18n import _

KIND_LABEL = {"endmill": _("Fresa plana (end mill)"), "ball": _("De bola (ball)"), "v": _("En V")}
INTEGER_FIELDS = ("flautas",)
TEXT_FIELDS = ("nombre", "tipo", "notas")
NUMBER_FIELDS = tuple(k for k in FIELDS if k not in TEXT_FIELDS)


class ToolEditError(ValueError):
    """Un valor de la ficha que no vale (el mensaje dice cual)."""


def check_ident(ident: str) -> None:
    require_ident(ident, ToolEditError, _("el identificador debe empezar por una letra y usar solo letras, números, "
                                          "'_', '-' o '.' (un número solo se tomaría por un diámetro)."))


def tool_to_form(tool: Tool, ident: str = "") -> dict[str, str]:
    """Los textos de las casillas de una ficha (vacio = sin dato). Un nombre igual al identificador es el que se
    pone solo al leer el archivo: se deja vacio para no escribirlo repetido."""
    data = tool_to_dict(tool)
    if ident and data.get("nombre") == ident:
        data.pop("nombre")
    form = {}
    for key in FIELDS:
        value = data.get(key)
        if value is None:
            form[key] = ""
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            form[key] = f"{value:g}"
        else:
            form[key] = str(value)
    form["tipo"] = form["tipo"] or "endmill"
    return form


def _number(key: str, text: str) -> Union[int, float]:
    try:
        value = float(text.replace(",", "."))
    except ValueError:
        raise ToolEditError(_("'{key}' debe ser un número (hay {text!r}).", key=key, text=text)) from None
    if key in INTEGER_FIELDS:
        if not value.is_integer():
            raise ToolEditError(_("'{key}' debe ser un número entero (hay {text!r}).", key=key, text=text))
        return int(value)
    return int(value) if value.is_integer() else value


def form_to_raw(form: dict[str, str]) -> dict:
    """El diccionario con la forma del JSON (las casillas vacias no se escriben)."""
    raw: dict = {}
    for key in FIELDS:
        text = (form.get(key) or "").strip()
        if not text:
            continue
        raw[key] = _number(key, text) if key in NUMBER_FIELDS else text
    raw.setdefault("tipo", "endmill")
    if raw.get("tipo") != "v":
        raw.pop("angulo", None)             # el angulo solo vale en una V: no se arrastra al cambiar de tipo
    return raw


def build_tool(ident: str, form: dict[str, str]) -> Tool:
    """Ficha valida desde el formulario, o ToolEditError con el motivo en palabras de la ventana."""
    check_ident(ident)
    raw = form_to_raw(form)
    try:
        return parse_tool(ident, raw, "ficha")
    except ToolsFileError as e:
        raise ToolEditError(str(e).split(": ", 1)[-1]) from None


def advice(tool: Tool) -> list[str]:
    """Lo que falta o limita en una ficha valida (informativo, no impide guardar)."""
    notes = []
    if tool.kind != "endmill":
        notes.append(_("De momento solo las fresas planas (end mill) generan trayectorias; esta ficha se guarda como datos."))
    missing = [n for n, v in (("feed", tool.feed_xy), ("entrada", tool.feed_z)) if v is None]
    if missing:
        notes.append(_("Sin {join}: habrá que escribirlo en cada trabajo.", join=' ni '.join(missing)))
    if tool.flute_length is None:
        notes.append(_("Sin longitud útil: la aplicación no puede avisar si un corte es más hondo que el filo."))
    if tool.stepdown is None:
        notes.append(_("Sin profundidad de pasada: corta todo el grosor de una vez, salvo que el trabajo diga otra."))
    return notes


class ToolEntry(Entry):
    """Una ficha en edicion: su formulario y, si es valido, la fresa resultante."""

    @property
    def tool(self) -> Optional[Tool]:
        return self.record

    @property
    def label(self) -> str:
        tool = self.record
        if tool is not None and tool.name and tool.name != self.ident:
            return _("{ident} — {name} (Ø{diameter:g} mm)", ident=self.ident, name=tool.name, diameter=tool.diameter)
        if tool is not None:
            return _("{ident} (Ø{diameter:g} mm)", ident=self.ident, diameter=tool.diameter)
        return self.ident


class ToolEditor(Editor):
    """Lista de fichas de fresa en edicion. Nada se escribe hasta `save`."""

    error_class = ToolEditError
    entry_class = ToolEntry
    blank = {**{k: "" for k in FIELDS}, "tipo": "endmill"}
    new_ident = "fresa"
    to_form = staticmethod(tool_to_form)
    build = staticmethod(build_tool)
    write = staticmethod(save_tools)
    tools = Editor.records
