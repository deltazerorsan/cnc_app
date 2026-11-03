"""Filas de la tabla "color -> operacion" de la interfaz, y su paso de ida y vuelta a recetas TOML.

Cada fila es una combinacion de colores (trazo, relleno) que hay en el dibujo y lo que se hace con las formas
que la tienen: cortarlas, vaciarlas, taladrarlas o ignorarlas. Las filas activas, en orden, son una receta
(`cnc.recipe`); la tabla es la forma de editarla sin escribir el TOML.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Optional, Sequence

from .recipe import Recipe, RecipeError, _color, parse_recipe
from .svg_import import ImportedShape
from .i18n import _

ACTIONS = ("corte", "vaciado", "taladro", "ignorar")
ACTION_LABEL = {"corte": _("Corte"), "vaciado": _("Vaciado"), "taladro": _("Taladro"), "ignorar": _("Ignorar")}
SIDES = ("auto", "fuera", "dentro", "linea")


@dataclass
class OpRow:
    stroke: Optional[str] = None
    fill: Optional[str] = None
    count: int = 0
    action: str = "corte"
    name: str = ""
    side: str = "auto"                   # solo corte
    through: bool = True                 # pasante (grosor + margen) o hasta `depth`
    depth: Optional[float] = None
    stepdown: Optional[float] = None     # pasada (en un taladro, el picoteo); vacio = la del trabajo o la ficha
    feed: Optional[float] = None         # mm/min de corte; vacio = el del trabajo o la ficha
    plunge: Optional[float] = None       # mm/min de bajada
    stepover: Optional[float] = None     # % del diametro, solo vaciado
    tabs: bool = False                   # puentes en las piezas y los huecos que corta (solo corte pasante)
    group: bool = False                  # grupo de vectores elegidos a mano: su "color" es solo una etiqueta del programa

    @property
    def key(self) -> tuple:
        return (self.stroke, self.fill)

    @property
    def active(self) -> bool:
        return self.action != "ignorar"

    @property
    def color_label(self) -> str:
        if self.stroke and self.fill:
            return _("trazo {stroke} + relleno {fill}", stroke=self.stroke, fill=self.fill)
        if self.stroke:
            return _("trazo {stroke}", stroke=self.stroke)
        if self.fill:
            return _("relleno {fill}", fill=self.fill)
        return _("sin color")

    @property
    def display_color(self) -> Optional[str]:
        return self.stroke or self.fill

    @property
    def title(self) -> str:
        """Lo que la tabla pone en la primera columna: el nombre del grupo, o los colores de la fila."""
        return self.name if self.group and self.name else self.color_label

    def operation_name(self) -> str:
        return self.name or f"{ACTION_LABEL[self.action]} {self.display_color or 'sin color'}"

    def matches(self, shape: ImportedShape) -> bool:
        return shape_key(shape) == self.key

    def is_plain_cut(self) -> bool:
        """Corte pasante normal, sin nada propio: lo que hace el programa sin receta."""
        return (self.action == "corte" and self.side == "auto" and self.through and self.depth is None
                and self.stepdown is None and self.feed is None and self.plunge is None and not self.tabs)


def shape_key(shape: ImportedShape) -> tuple:
    return (_color(shape.stroke), _color(shape.fill))


def rows_from_shapes(shapes: Sequence[ImportedShape]) -> list[OpRow]:
    """Una fila por combinacion de colores, en el orden en que aparece en el dibujo."""
    rows: dict[tuple, OpRow] = {}
    for s in shapes:
        key = shape_key(s)
        if key not in rows:
            rows[key] = OpRow(stroke=key[0], fill=key[1])
        rows[key].count += 1
    return list(rows.values())


def plain(rows: Sequence[OpRow]) -> bool:
    """True si el trabajo no necesita receta: todo lo activo es corte pasante normal (y se corta todo junto,
    como sin receta, para que el anidamiento valga entre colores)."""
    active = [r for r in rows if r.active]
    return bool(active) and all(r.is_plain_cut() for r in active)


# --- filas -> receta ----------------------------------------------------------------------------------
def _num(v):
    return int(v) if float(v).is_integer() else float(v)


def row_to_dict(row: OpRow, name: str, index: int = 0) -> dict:
    d: dict = {"nombre": name, "tipo": row.action}
    if row.stroke:
        d["trazo"] = row.stroke
    if row.fill:
        d["relleno"] = row.fill
    if row.action == "corte" and row.side != "auto":
        d["lado"] = row.side
    if row.through:
        d["pasante"] = True
    elif row.depth is not None:
        d["profundidad"] = _num(row.depth)
    elif row.action == "corte":
        # la receta daria por pasante un corte sin profundidad: en la tabla, desmarcar Pasante sin dar profundidad es
        # un olvido, no una orden (vaciado y taladro ya lo rechaza la receta)
        raise RecipeError(_("operacion {index} ('{name}'): falta la profundidad (escribela en Prof. o marca Pasante).", index=index, name=name))
    for key, value in (("pasada", row.stepdown), ("avance", row.feed), ("entrada", row.plunge)):
        if value is not None:
            d[key] = _num(value)
    if row.action == "vaciado" and row.stepover is not None:
        d["stepover"] = _num(row.stepover)
    if row.action == "corte" and row.tabs and row.through:
        d["puentes"] = True
    return d


def recipe_dict(rows: Sequence[OpRow], title: str = "", ignore: Sequence[str] = ()) -> dict:
    """Diccionario con la forma del TOML de la receta, de las filas activas (en orden)."""
    names: dict[str, int] = {}
    ops = []
    for r in rows:
        if not r.active:
            continue
        base = r.operation_name()
        names[base] = names.get(base, 0) + 1
        ops.append(row_to_dict(r, base if names[base] == 1 else f"{base} ({names[base]})", len(ops) + 1))
    data: dict = {}
    if title:
        data["titulo"] = title
    if ignore:
        data["ignorar"] = list(ignore)
    data["operacion"] = ops
    return data


def ignored_colors(rows: Sequence[OpRow]) -> list[str]:
    out: list[str] = []
    for r in rows:
        if not r.active:
            for c in (r.stroke, r.fill):
                if c and c not in out:
                    out.append(c)
    return out


def build_recipe(rows: Sequence[OpRow], title: str = "", source: str = _("tabla de operaciones")) -> Recipe:
    """Receta de las filas activas. RecipeError (con el nombre de la operacion) si algun valor no vale."""
    data = recipe_dict(rows, title)
    if not data["operacion"]:
        raise RecipeError(_("no hay ninguna operacion activa en la tabla (todo esta en Ignorar)."))
    return parse_recipe(data, source)


# --- receta -> filas ------------------------------------------------------------------------------------
def rows_from_recipe(recipe: Recipe, rows: Sequence[OpRow]) -> list[OpRow]:
    """Aplica una receta a las filas del dibujo. Cada fila toma la primera operacion que encaja con sus colores
    (como al cortar); las filas sin operacion pasan a Ignorar. Las operaciones fijan el orden."""
    assigned: list[tuple[int, int, OpRow]] = []
    for pos, row in enumerate(rows):
        for k, spec in enumerate(recipe.operations):
            if (spec.stroke is None or spec.stroke == row.stroke) and (spec.fill is None or spec.fill == row.fill):
                assigned.append((k, pos, replace(
                    row, action=spec.kind, name=spec.name, side=spec.side, through=spec.through, depth=spec.depth,
                    stepdown=spec.stepdown, feed=spec.feed_xy, plunge=spec.feed_z, tabs=spec.tabs,
                    stepover=None if spec.stepover is None else spec.stepover * 100)))
                break
        else:
            assigned.append((len(recipe.operations), pos, replace(row, action="ignorar")))
    assigned.sort(key=lambda t: (t[0], t[1]))
    return [r for __, __, r in assigned]


# --- receta TOML ------------------------------------------------------------------------------------------
def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    raise TypeError(_("no se puede escribir {v!r} en TOML", v=v))


def recipe_to_toml(data: dict) -> str:
    """Texto TOML de un diccionario de receta (`recipe_dict`), en el orden de claves de `receta_ejemplo.toml`."""
    lines = [_("# Receta de trabajo generada por cnc_app: que operacion es cada color del dibujo."),
             _("# Se puede editar a mano y abrir otra vez con Receta > Abrir receta (ver receta_ejemplo.toml)."), ""]
    for key in ("titulo", "ignorar"):
        if key in data:
            lines.append(f"{key} = {_toml_value(data[key])}")
    order = ("nombre", "tipo", "trazo", "relleno", "lado", "pasante", "profundidad", "pasada", "avance", "entrada",
             "stepover", "puentes")
    for op in data["operacion"]:
        lines += ["", "[[operacion]]"]
        lines += [f"{k} = {_toml_value(op[k])}" for k in order if k in op]
    return "\n".join(lines) + "\n"
