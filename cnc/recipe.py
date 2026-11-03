"""Receta de trabajo: que operacion corresponde a cada color del dibujo.

Asigna operaciones a las formas del dibujo: aqui el disenador marca la operacion con el color
en su aplicacion de dibujo vectorial y la receta (un TOML con comentarios) dice, para cada color,
que operacion es y con que profundidad y avances. Las operaciones se cortan en el orden de la receta.

    ignorar = ["#ffcc00"]               # cotas, guias...: no se cortan

    [[operacion]]
    nombre = "Pozos"
    tipo = "vaciado"                    # corte | vaciado | taladro
    trazo = "#e63c24"                   # color de trazo y/o de relleno de las formas
    profundidad = 3

Una operacion de corte decide por anidamiento si cada forma es pieza o hueco (lado = "auto"); en un
vaciado, las formas del mismo color que quedan dentro de otra son islas que no se tocan.
"""
from __future__ import annotations

import difflib
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

import svgelements as se
import svgelements.svgelements as sv
from shapely.geometry import Polygon

from .config import ConfigError, read_config
from .model import ContourOp, DrillOp, PocketOp, Stock, Tool
from .ordering import nearest_chain, order_cuts, rows
from .pocket import entry_point
from .svg_import import ImportedShape, nesting_levels, suggest_sides
from .tabs import TabSettings, propose_tabs
from .toolpath import total_depth
from .i18n import _

KINDS = ("corte", "vaciado", "taladro")
SIDES = {"auto": None, "fuera": "outside", "dentro": "inside", "linea": "on"}
TOP_KEYS = {"titulo", "ignorar", "operacion"}
OP_KEYS = {"nombre", "tipo", "trazo", "relleno", "lado", "profundidad", "pasante", "pasada", "avance", "entrada",
           "stepover", "puentes"}
DIAMETER_TOL = 0.05   # mm: un taladro cuyo circulo difiere mas que esto de la fresa avisa


class RecipeError(ValueError):
    """Error en la receta o al aplicarla (el mensaje dice cual y por que)."""


@dataclass
class RecipeOp:
    name: str
    kind: str                            # corte | vaciado | taladro
    stroke: Optional[str] = None         # "#rrggbb" normalizado
    fill: Optional[str] = None
    side: str = "auto"                   # solo corte
    depth: Optional[float] = None
    through: bool = False
    stepdown: Optional[float] = None     # pasada (en un taladro, el picoteo)
    feed_xy: Optional[float] = None
    feed_z: Optional[float] = None
    stepover: Optional[float] = None     # fraccion del diametro (0-1]
    tabs: bool = False                   # deja puentes en las piezas y los huecos que corta (solo corte pasante)

    def matches(self, shape: ImportedShape) -> bool:
        return (self.stroke is None or _color(shape.stroke) == self.stroke) and (
            self.fill is None or _color(shape.fill) == self.fill
        )


@dataclass
class Recipe:
    operations: list[RecipeOp]
    ignore: list[str] = field(default_factory=list)
    title: str = ""
    source: str = ""


@dataclass
class Built:
    operations: list                                   # ContourOp / PocketOp / DrillOp, en orden de corte
    counts: dict[str, int]                             # formas de cada operacion de la receta (por nombre)
    ignored: int = 0
    unmatched: list[ImportedShape] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


_HEX_RE = re.compile(r"^#(?:[0-9a-f]{3}|[0-9a-f]{6})$")
_FUNCTIONAL = (sv.REGEX_COLOR_RGB, sv.REGEX_COLOR_RGB_PERCENT, sv.REGEX_COLOR_HSL)
_BLACK = se.Color.parse("black")


def _color(value) -> Optional[str]:
    """'#rrggbb' en minusculas, o None si no hay color (none, vacio...) o no se entiende.

    svgelements convierte en negro cualquier texto que no entiende ("rojo", "#12345"): aqui no, porque una
    receta con un color mal escrito cortaria todo lo negro del dibujo.
    """
    if value is None:
        return None
    text = str(value).strip().lower().replace(" ", "")
    if text in ("", "none", "transparent", "currentcolor", "inherit"):
        return None
    if text.startswith("#"):
        if not _HEX_RE.match(text):
            return None
    elif text.startswith(("rgb", "hsl")):
        if not any(r.match(text) for r in _FUNCTIONAL):
            return None
    elif text != "black" and (not text.isalpha() or se.Color.parse_color_lookup(text) == _BLACK):
        return None                               # un nombre que svgelements no conoce
    try:
        c = se.Color(text)
    except Exception:
        return None
    return c.hex.lower() if c.value is not None and getattr(c, "alpha", 255) != 0 else None


def _parse_color(value, where: str) -> str:
    if not isinstance(value, str):
        raise RecipeError(_("{where} debe ser un color como \"#e63c24\" (hay {value!r}).", where=where, value=value))
    c = _color(value)
    if c is None:
        raise RecipeError(_("{where}: no entiendo el color {value!r}; usa \"#rrggbb\".", where=where, value=value))
    return c


def _number(raw: dict, key: str, where: str, *, positive=True) -> Optional[float]:
    if key not in raw:
        return None
    v = raw[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise RecipeError(_("{where}: '{key}' debe ser un numero (hay {v!r}).", where=where, key=key, v=v))
    if positive and not v > 0:
        raise RecipeError(_("{where}: '{key}' debe ser mayor que 0 (hay {v!r}).", where=where, key=key, v=v))
    return float(v)


def _check_keys(raw: dict, allowed: set, where: str) -> None:
    for key in raw:
        if key not in allowed:
            close = difflib.get_close_matches(str(key), sorted(allowed), n=1)
            hint = _(" Quisiste decir '{close}'?", close=close[0]) if close else ""
            raise RecipeError(_("{where}: clave desconocida '{key}'.{hint} Claves validas: {join}.", where=where, key=key, hint=hint, join=', '.join(sorted(allowed))))


def parse_recipe(data: dict, source: str = "receta") -> Recipe:
    _check_keys(data, TOP_KEYS, source)
    title = data.get("titulo", "")
    if not isinstance(title, str):
        raise RecipeError(_("{source}: 'titulo' debe ser texto.", source=source))
    ignore_raw = data.get("ignorar", [])
    if isinstance(ignore_raw, str):
        ignore_raw = [ignore_raw]
    if not isinstance(ignore_raw, list):
        raise RecipeError(_("{source}: 'ignorar' debe ser una lista de colores.", source=source))
    ignore = [_parse_color(c, _("{source}: 'ignorar'", source=source)) for c in ignore_raw]

    raw_ops = data.get("operacion")
    if not isinstance(raw_ops, list) or not raw_ops or not all(isinstance(o, dict) for o in raw_ops):
        raise RecipeError(_("{source}: hace falta al menos una operacion ([[operacion]]).", source=source))
    ops: list[RecipeOp] = []
    for n, raw in enumerate(raw_ops, start=1):
        name = raw.get("nombre", _("operacion {n}", n=n))
        where = _("{source}: operacion {n}", source=source, n=n) + (f" ('{name}')" if "nombre" in raw else "")
        _check_keys(raw, OP_KEYS, where)
        if not isinstance(name, str):
            raise RecipeError(_("{where}: 'nombre' debe ser texto.", where=where))
        kind = raw.get("tipo")
        if kind not in KINDS:
            raise RecipeError(_("{where}: 'tipo' debe ser uno de {join} (hay {kind!r}).", where=where, join=', '.join(KINDS), kind=kind))
        stroke = _parse_color(raw["trazo"], _("{where}: 'trazo'", where=where)) if "trazo" in raw else None
        fill = _parse_color(raw["relleno"], _("{where}: 'relleno'", where=where)) if "relleno" in raw else None
        if stroke is None and fill is None:
            raise RecipeError(_("{where}: indica que formas son con 'trazo' (color de trazo) y/o 'relleno'.", where=where))
        for c in (stroke, fill):
            if c is not None and c in ignore:
                raise RecipeError(_("{where}: el color {c} esta en 'ignorar' y en esta operacion.", where=where, c=c))
        side = raw.get("lado", "auto")
        if side not in SIDES:
            raise RecipeError(_("{where}: 'lado' debe ser uno de {join} (hay {side!r}).", where=where, join=', '.join(SIDES), side=side))
        if "lado" in raw and kind != "corte":
            raise RecipeError(_("{where}: 'lado' solo vale en operaciones de corte.", where=where))
        if "stepover" in raw and kind != "vaciado":
            raise RecipeError(_("{where}: 'stepover' solo vale en vaciados.", where=where))
        through = raw.get("pasante", False)
        if not isinstance(through, bool):
            raise RecipeError(_("{where}: 'pasante' debe ser true o false (hay {through!r}).", where=where, through=through))
        depth = _number(raw, "profundidad", where)
        if through and depth is not None:
            raise RecipeError(_("{where}: 'pasante' y 'profundidad' se excluyen.", where=where))
        if not through and depth is None:
            if kind == "corte":
                through = True            # un corte sin profundidad es pasante
            else:
                raise RecipeError(_("{where}: falta 'profundidad' (mm desde la superficie) o 'pasante = true'.", where=where))
        tabs = raw.get("puentes", False)
        if not isinstance(tabs, bool):
            raise RecipeError(_("{where}: 'puentes' debe ser true o false (hay {tabs!r}).", where=where, tabs=tabs))
        if tabs and kind != "corte":
            raise RecipeError(_("{where}: 'puentes' solo vale en operaciones de corte.", where=where))
        if tabs and not through:
            raise RecipeError(_("{where}: los puentes solo valen en cortes pasantes.", where=where))
        stepover = _number(raw, "stepover", where)
        if stepover is not None:
            if stepover > 100:
                raise RecipeError(_("{where}: 'stepover' va en % del diametro, hasta 100 (hay {stepover:g}).", where=where, stepover=stepover))
            stepover /= 100.0
        ops.append(RecipeOp(
            name=name, kind=kind, stroke=stroke, fill=fill, side=side, depth=depth, through=through,
            stepdown=_number(raw, "pasada", where), feed_xy=_number(raw, "avance", where),
            feed_z=_number(raw, "entrada", where), stepover=stepover, tabs=tabs,
        ))
    names = [o.name for o in ops]
    for name in set(names):
        if names.count(name) > 1:
            raise RecipeError(_("{source}: hay dos operaciones con el nombre '{name}'.", source=source, name=name))
    return Recipe(operations=ops, ignore=ignore, title=title, source=source)


def load_recipe(path) -> Recipe:
    path = Path(path)
    try:
        data = read_config(path, what=_("archivo de receta"))
    except ConfigError as e:
        raise RecipeError(str(e)) from None
    return parse_recipe(data, path.name)


# --- aplicar la receta a las formas de un tablero ---------------------------------------------------
def _is_ignored(recipe: Recipe, shape: ImportedShape) -> bool:
    return bool({_color(shape.stroke), _color(shape.fill)} & set(recipe.ignore))


def classify(recipe: Recipe, shapes: Sequence[ImportedShape]):
    """(formas por operacion, formas ignoradas, formas sin operacion). Gana la primera operacion que encaje."""
    per_op: list[list[ImportedShape]] = [[] for __ in recipe.operations]
    ignored: list[ImportedShape] = []
    unmatched: list[ImportedShape] = []
    for s in shapes:
        if _is_ignored(recipe, s):
            ignored.append(s)
            continue
        for k, op in enumerate(recipe.operations):
            if op.matches(s):
                per_op[k].append(s)
                break
        else:
            unmatched.append(s)
    return per_op, ignored, unmatched


def cut_shapes(recipe: Recipe, shapes: Sequence[ImportedShape]) -> list[ImportedShape]:
    """Las formas que la receta corta (sin las ignoradas ni las que no encajan en ninguna operacion)."""
    per_op, __, __ = classify(recipe, shapes)
    return [s for group in per_op for s in group]


def count_by_operation(recipe: Recipe, shapes: Sequence[ImportedShape]) -> dict[str, int]:
    per_op, __, __ = classify(recipe, shapes)
    return {op.name: len(group) for op, group in zip(recipe.operations, per_op)}


def _where(shape: ImportedShape) -> str:
    c = shape.shape.centroid
    return _("cerca de X {x:.0f}, Y {y:.0f} mm", x=c.x, y=c.y)


def _islands(shapes: list[ImportedShape]) -> list[Polygon]:
    """Vaciados con sus islas: cada forma de nivel par es un vaciado y las de nivel impar que quedan dentro
    de ella (la menor que las contiene) son sus islas."""
    levels = nesting_levels(shapes)
    polys = [s.shape for s in shapes]
    out = []
    for i, lvl in enumerate(levels):
        if lvl is None or lvl % 2:
            continue
        holes = []
        for j, other in enumerate(levels):
            if other is None or other != lvl + 1 or not polys[i].contains(polys[j]):
                continue
            parents = [k for k, lk in enumerate(levels) if lk == lvl and polys[k].contains(polys[j])]
            if min(parents, key=lambda k: polys[k].area) == i:
                holes.append(polys[j].exterior)
        out.append(Polygon(polys[i].exterior, list(polys[i].interiors) + holes))
    return out


def shape_bridges(shape: ImportedShape, side: str, wanted: bool, settings: TabSettings, tool_diameter: float,
                  enabled: bool = True) -> tuple:
    """Los puentes de una forma que se corta con esa fila: (puentes, de dónde salen). Los puestos a mano (`manual`, aunque
    la lista esté vacía) mandan en cualquier lado; si no, los propone el programa (`auto`) cuando la fila los pide y la
    forma es una pieza o un hueco (se corta por fuera o por dentro). `none`: ninguno (también con el interruptor general
    de los puentes apagado)."""
    if not enabled:
        return (), "none"
    if shape.tabs is not None:
        return tuple(shape.tabs), "manual"
    if wanted and side in ("outside", "inside"):
        return propose_tabs(shape.shape, settings, tool_diameter, inside=side == "inside"), "auto"
    return (), "none"


def build_operations(
    shapes: Sequence[ImportedShape],
    recipe: Recipe,
    tool: Tool,
    stock: Stock,
    *,
    feed: Optional[float] = None,
    plunge: Optional[float] = None,
    stepdown: Optional[float] = None,
    order: str = "far-to-near",
    tabs: Optional[TabSettings] = None,
    bridges_on: bool = True,
) -> Built:
    """Operaciones listas para el Job, en el orden de la receta. Los valores que una operacion no indica salen
    de feed / plunge / stepdown (linea de comandos) y despues de la ficha de la fresa."""
    radius = tool.diameter / 2
    per_op, ignored, unmatched = classify(recipe, shapes)
    built = Built(operations=[], counts={}, ignored=len(ignored), unmatched=unmatched)

    def pick(spec: RecipeOp, own, cli, from_tool, what, option, field_name):
        for v in (own, cli, from_tool):
            if v is not None:
                return v
        raise RecipeError(
            _("La operacion '{name}' no tiene {what}: ponlo en la receta ('{field_name}'), en el trabajo o en "
              "la ficha de la fresa.", name=spec.name, what=what, field_name=field_name)
        )

    for spec, group in zip(recipe.operations, per_op):
        built.counts[spec.name] = len(group)
        if not group:
            continue
        feed_xy = pick(spec, spec.feed_xy, feed, tool.feed_xy, _("avance de corte"), "--feed", "avance")
        feed_z = pick(spec, spec.feed_z, plunge, tool.feed_z, _("avance de bajada"), "--plunge", "entrada")
        common = dict(through=spec.through, depth=spec.depth)
        try:
            total = total_depth(ContourOp(group[0].shape, **common), stock)   # profundidad total (valida el grosor)
        except ValueError as e:
            raise RecipeError(_("Operacion '{name}': {e}", name=spec.name, e=e)) from None
        step = spec.stepdown if spec.stepdown is not None else (stepdown if stepdown is not None else tool.stepdown)
        step = step if step is not None else total                         # sin dato: una sola pasada

        if spec.kind == "corte":
            for s in group:
                if spec.side in ("fuera", "dentro") and not s.closed:
                    raise RecipeError(
                        _("La operacion '{name}' corta 'por {side}' una forma abierta ({where}); usa lado = \"linea\".", name=spec.name, side=spec.side, where=_where(s))
                    )
            sides = suggest_sides(group) if spec.side == "auto" else [SIDES[spec.side]] * len(group)
            geoms = [s.shape for s in group]
            small = []                                      # piezas con puentes pedidos que no tienen sitio para ellos
            by_hand = []                                    # formas con puentes a mano en un corte que no es pasante
            for i in order_cuts(geoms, sides, radius, order):
                bridges, source = shape_bridges(group[i], sides[i], spec.tabs, tabs or TabSettings(), tool.diameter, bridges_on)
                if source == "auto" and not bridges and sides[i] == "outside":       # un hueco pequeño no avisa: no los necesita
                    small.append(group[i])
                if source == "manual" and bridges and not spec.through:
                    by_hand.append(group[i])
                    bridges = ()
                built.operations.append(ContourOp(
                    geoms[i], side=sides[i], stepdown=step, feed_xy=feed_xy, feed_z=feed_z, tabs=bridges, **common))
            if by_hand:
                built.notes.append(
                    _("'{name}': {count} forma(s) tienen puentes puestos a mano pero esta operación no corta de lado a lado: "
                      "se cortan sin ellos (la primera, {where}).", name=spec.name, count=len(by_hand), where=_where(by_hand[0])))
            if small:
                built.notes.append(
                    _("'{name}': {count} pieza(s) son demasiado pequeñas para llevar puentes de {width:g} mm y se cortan sin ellos "
                      "(la primera, {where}).", name=spec.name, count=len(small), width=(tabs or TabSettings()).width,
                      where=_where(small[0])))
        elif spec.kind == "vaciado":
            for s in group:
                if not s.closed:
                    raise RecipeError(_("La operacion '{name}' (vaciado) tiene una forma abierta ({where}).", name=spec.name, where=_where(s)))
            stepover = spec.stepover if spec.stepover is not None else tool.stepover
            ops = [PocketOp(p, stepdown=step, stepover=stepover, feed_xy=feed_xy, feed_z=feed_z, **common)
                   for p in _islands(group)]
            polys = [o.shape for o in ops]
            idx = list(range(len(ops)))
            seq = idx if order == "file" else nearest_chain(idx, polys, lambda i: entry_point(ops[i], radius))
            built.operations += [ops[i] for i in seq]
        else:  # taladro
            for s in group:
                if not s.closed:
                    raise RecipeError(_("La operacion '{name}' (taladro) tiene una forma abierta ({where}).", name=spec.name, where=_where(s)))
                b = s.shape.bounds
                size = max(b[2] - b[0], b[3] - b[1])
                if abs(size - tool.diameter) > DIAMETER_TOL:
                    built.notes.append(
                        _("'{name}': un circulo de {size:.2f} mm ({where}) se taladra con la fresa de {diameter:g} mm, que "
                          "hace un agujero de {diameter:g} mm; para otro diametro usa tipo = \"vaciado\".", name=spec.name, size=size, where=_where(s), diameter=tool.diameter)
                    )
            geoms = [s.shape for s in group]
            idx = list(range(len(geoms)))
            seq = idx if order == "file" else rows(idx, geoms)       # el primero es el mas alto; el resto, al mas cercano
            pts = [(geoms[i].centroid.x, geoms[i].centroid.y) for i in seq]
            built.operations.append(DrillOp(
                pts, peck=spec.stepdown if spec.stepdown is not None else None, feed_z=feed_z,
                optimize_order=order != "file", **common))
    if not built.operations:
        raise RecipeError(_("La receta no encuentra ninguna forma que cortar en este dibujo."))
    return built
