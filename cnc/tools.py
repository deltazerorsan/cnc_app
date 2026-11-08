"""Base de datos de fresas en un JSON editable a mano (herramientas.json).

Formato: una ficha por fresa, con claves en espanol. Solo diametro es obligatorio.

    {
      "herramientas": {
        "em4": {"nombre": "End mill 4 mm", "tipo": "endmill", "diametro": 4,
                "feed": 300, "entrada": 200}
      }
    }

El cargador es estricto a proposito: una clave mal escrita, un duplicado o un valor absurdo
dan un error que dice la ficha y el campo, en vez de cortar con un valor que no era.
"""
from __future__ import annotations

import json
from functools import partial
from pathlib import Path
from typing import Union

from .model import Tool
from .i18n import _
from .records import check_fields, clean, is_number, parse_records, positive, suggest

DEFAULT_TOOLS_FILE = "herramientas.json"

# tipo en el JSON -> tipo en el modelo
KINDS = {"endmill": "endmill", "ball": "ball", "v": "vbit"}
_KINDS_BACK = {v: k for k, v in KINDS.items()}

# clave del JSON -> descripcion (el orden es el de escritura)
FIELDS = {
    "nombre": _("texto libre"),
    "tipo": _("endmill | ball | v"),
    "diametro": _("mm (en una V, el diametro maximo de corte)"),
    "flautas": _("numero de filos"),
    "longitud_util": _("mm de filo util"),
    "angulo": _("grados incluidos de la V (solo tipo v)"),
    "rpm": _("RPM aconsejadas (informativo: el variador se ajusta a mano)"),
    "profundidad_pasada": _("mm por pasada"),
    "feed": _("mm/min de corte"),
    "entrada": _("mm/min de bajada"),
    "stepover": _("% del diametro entre pasadas (p. ej. 40)"),
    "notas": _("texto libre"),
}

HELP_TEXT = (
    _("Una ficha por fresa. Obligatorio: diametro (mm). Resto opcional: nombre, tipo (endmill | ball | v; "
    "la v necesita angulo en grados), flautas, longitud_util (mm), rpm (aconsejadas; solo informativo, el "
    "variador se ajusta a mano), profundidad_pasada (mm), feed (mm/min de corte), entrada (mm/min de "
    "bajada), stepover (% del diametro, p. ej. 40), notas. Lo que falte se pide por linea de comandos. "
    "Para distintos materiales, duplica la ficha con otro nombre (em4_mdf, em4_contra...).")
)


class ToolsFileError(ValueError):
    """Error en el archivo de fresas (el mensaje dice donde)."""


_positive = partial(positive, error=ToolsFileError)


def parse_tool(ident: str, raw: dict, source: str = "herramientas") -> Tool:
    """Valida una ficha y la convierte en Tool."""
    where = _("{source}, ficha '{ident}'", source=source, ident=ident)
    if not isinstance(raw, dict):
        raise ToolsFileError(_("{where}: debe ser un objeto {{...}} con los campos de la fresa.", where=where))
    check_fields(raw, FIELDS, where, ToolsFileError)
    if "diametro" not in raw:
        raise ToolsFileError(_("{where}: falta el campo obligatorio 'diametro' (mm).", where=where))

    tipo = raw.get("tipo", "endmill")
    kind_raw = tipo.strip().lower() if isinstance(tipo, str) else None
    if kind_raw not in KINDS:
        raise ToolsFileError(_("{where}: 'tipo' debe ser uno de {join} (hay {get!r}).", where=where, join=', '.join(KINDS), get=raw.get('tipo')))
    kind = KINDS[kind_raw]

    kw: dict = {"diameter": _positive(raw["diametro"], _("{where}: 'diametro'", where=where)), "kind": kind}

    for key in ("nombre", "notas"):
        if key in raw and not isinstance(raw[key], str):
            raise ToolsFileError(_("{where}: '{key}' debe ser texto.", where=where, key=key))
    kw["name"] = raw.get("nombre") or ident
    kw["notes"] = raw.get("notas", "")

    if "flautas" in raw:
        n = raw["flautas"]
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise ToolsFileError(_("{where}: 'flautas' debe ser un numero entero >= 1 (hay {n!r}).", where=where, n=n))
        kw["flutes"] = n
    for key, attr in (("longitud_util", "flute_length"), ("rpm", "rpm"), ("profundidad_pasada", "stepdown"),
                      ("feed", "feed_xy"), ("entrada", "feed_z")):
        if key in raw:
            kw[attr] = _positive(raw[key], f"{where}: '{key}'")

    if "angulo" in raw:
        if kind != "vbit":
            raise ToolsFileError(_("{where}: 'angulo' solo tiene sentido en fresas de tipo v.", where=where))
        a = raw["angulo"]
        if not is_number(a) or not 0 < a < 180:
            raise ToolsFileError(_("{where}: 'angulo' debe estar entre 0 y 180 grados (hay {a!r}).", where=where, a=a))
        kw["v_angle"] = float(a)
    elif kind == "vbit":
        raise ToolsFileError(_("{where}: una fresa de tipo v necesita 'angulo' (grados incluidos, p. ej. 90).", where=where))

    if "stepover" in raw:
        s = raw["stepover"]
        if not is_number(s) or not 0 < s <= 100:
            raise ToolsFileError(_("{where}: 'stepover' debe estar entre 0 y 100 (% del diametro) (hay {s!r}).", where=where, s=s))
        if s <= 1:
            raise ToolsFileError(
                _("{where}: 'stepover' va en porcentaje del diametro (p. ej. 40 para el 40 %), no como fraccion "
                  "(hay {s!r}).", where=where, s=s)
            )
        kw["stepover"] = s / 100.0

    return Tool(**kw)


def parse_tools(text: str, source: str = "herramientas.json") -> dict[str, Tool]:
    return parse_records(text, source, "herramientas", _("fresa"), ToolsFileError, parse_tool,
                         _(" (un número solo se interpreta como un diámetro)"))


def load_tools(path: Union[str, Path]) -> dict[str, Tool]:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")  # admite el BOM que anaden algunos editores de Windows
    except FileNotFoundError:
        raise ToolsFileError(_("No existe el archivo de fresas {path}.", path=path)) from None
    except OSError as e:
        raise ToolsFileError(_("No se puede leer {path}: {e}", path=path, e=e.strerror or e)) from None
    return parse_tools(text, path.name)


def tool_to_dict(tool: Tool) -> dict:
    """Ficha en el formato del JSON (sin los campos vacios)."""
    out: dict = {}
    if tool.name:
        out["nombre"] = tool.name
    out["tipo"] = _KINDS_BACK[tool.kind]
    out["diametro"] = clean(tool.diameter)
    pairs = (("flautas", tool.flutes), ("longitud_util", tool.flute_length), ("angulo", tool.v_angle),
             ("rpm", tool.rpm), ("profundidad_pasada", tool.stepdown), ("feed", tool.feed_xy),
             ("entrada", tool.feed_z),
             ("stepover", None if tool.stepover is None else round(tool.stepover * 100, 6)))
    for key, value in pairs:
        if value is not None:
            out[key] = value if isinstance(value, int) and not isinstance(value, bool) else clean(value)
    if tool.notes:
        out["notas"] = tool.notes
    return out


def save_tools(tools: dict[str, Tool], path: Union[str, Path]) -> None:
    """Escribe el JSON (con la ayuda estandar). Una ficha por fresa, legible y editable a mano."""
    lines = ['{', f'  "_ayuda": {json.dumps(HELP_TEXT, ensure_ascii=False)},', '  "herramientas": {']
    items = list(tools.items())
    for n, (ident, tool) in enumerate(items):
        body = json.dumps(tool_to_dict(tool), ensure_ascii=False, indent=6)
        body = body.replace("\n}", "\n    }")
        lines.append(f'    {json.dumps(ident)}: {body}' + ("," if n < len(items) - 1 else ""))
    lines += ["  }", "}", ""]
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def get_tool(tools: dict[str, Tool], ident: str) -> Tool:
    try:
        return tools[ident]
    except KeyError:
        known = ", ".join(tools) or "(ninguna)"
        raise ToolsFileError(_("No hay ninguna ficha '{ident}'. Fichas: {known}.{suggest}", ident=ident, known=known, suggest=suggest(ident, tools))) from None
