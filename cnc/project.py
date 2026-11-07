"""Un trabajo de la interfaz: el dibujo abierto, la tabla de operaciones y los ajustes, sin Qt.

La ventana solo pinta y recoge datos; todo lo que decide (que formas se cortan, que ajustes vale cada tablero,
cuando hace falta receta, como se llaman los archivos) esta aqui para poder probarlo sin pantalla.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Optional, Sequence, Union

from shapely.geometry import Polygon

from .boards import Board, BoardLayout, detect_boards
from .config import ConfigError, find_config, read_config
from .drawing_edit import EditError, EditResult, apply_edit, edit_label, tabs_edit
from .holddown import HoldError, HoldSettings, marks_gcode
from .machines import (DEFAULT_MACHINE_ID, MachinesFileError, choose_machine, differences, load_machines,
                       machine_to_dict, parse_machine, travel_text)
from .model import GRBL_MAQUINA_REAL, MachineProfile, Tool
from .oprows import (ACTION_LABEL, ACTIONS, OpRow, build_recipe, ignored_colors, plain, recipe_dict, recipe_to_toml,
                     rows_from_recipe, rows_from_shapes, shape_key)
from .pipeline import (Drawing, JobPlan, JobSettings, Message, find_machines_file, find_tools_file, lay_out,
                       load_drawing, plan_job, suggested_offset)
from .records import ID
from .recipe import Recipe, RecipeError, _color, classify, load_recipe, parse_recipe
from .svg_import import ImportedShape, SvgImport, nesting_levels, suggest_sides
from .tabs import Tab, TabError, TabSettings, propose_tabs
from . import safety
from .tools import ToolsFileError, get_tool, load_tools
from .i18n import _

PROJECT_VERSION = 1
HOLD_WHOLE = "dibujo"                                           # clave de los puntos de sujecion de un dibujo sin tableros
ROTATIONS = ("auto", "0", "90", "180", "270", "vertical")      # "vertical": --portrait
# colores-etiqueta de los grupos de vectores (se elige el primero que no use el dibujo): bien distintos entre si
GROUP_COLORS = ("#e6194b", "#f58231", "#911eb4", "#00a6a6", "#f032e6", "#9a6324", "#3cb44b", "#4363d8", "#808000",
                "#800000", "#008080", "#bcbd22")


def gui_messages(plan: JobPlan) -> list[Message]:
    """Los mensajes del plan con las palabras de la ventana: el aviso de que la trayectoria se sale del tablero no
    habla de --offset (la ventana tiene su propio desplazamiento y un boton para aplicarlo)."""
    sides = iter(plan.overhang.items())
    out = []
    for m in plan.messages:
        if m.code == "overhang":
            side, amount = next(sides, ("", 0.0))
            m = Message(_("La trayectoria se sale del tablero {side} {amount:.1f} mm: parte de una pieza queda fuera del "
                          "material.", side=side, amount=amount), m.level, m.severe, m.code)
        out.append(m)
    return out


class ProjectError(ValueError):
    """Algo que el usuario debe corregir antes de generar (el mensaje dice que y donde)."""


@dataclass
class General:
    """Ajustes de todo el trabajo. Los avances vacios salen de la receta o de la ficha de la fresa."""

    tool: str = ""                      # id de una ficha de herramientas.json o un diametro en mm
    machine: str = ""                   # id de una ficha de maquinas.json; vacio = la unica que haya
    thickness: Optional[float] = None
    margin: float = 0.4
    feed: Optional[float] = None
    plunge: Optional[float] = None
    stepdown: Optional[float] = None
    rotate: str = "auto"                # ROTATIONS
    origin: str = "bbox_bottom_left"    # donde queda el cero si no hay tableros
    origin_margin: float = 4.0          # sin tableros: mm del cero a la esquina inferior izquierda de lo que se corta
    safe_z: float = 5.0
    clearance_z: float = 1.0
    emit_spindle: bool = True
    pause_at_start: bool = False
    home_at_end: bool = False
    return_to_origin: bool = False
    end_code: str = ""
    bridges: bool = True                # interruptor general de los puentes: apagado, no se pone ninguno (ni a mano)
    tab_width: float = 6.0              # puentes de las filas que los piden: mm del puente que queda...
    tab_height: float = 2.0             # ...mm de material que queda sobre el fondo del tablero...
    tab_spacing: float = 150.0          # ...y mm de perimetro por puente

    @classmethod
    def from_config(cls, cfg: dict) -> "General":
        """Ajustes iniciales desde un cnc_app.toml ya leido (las mismas claves que la linea de comandos)."""
        g = cls()
        for key, value in cfg.items():
            key = str(key).replace("-", "_")
            if key == "no_spindle":
                g.emit_spindle = not value
            elif key == "rotate":
                g.rotate = "auto" if value == "auto" else f"{float(value):g}"
            elif key == "tool":
                g.tool = str(value)
            elif key == "machine":
                g.machine = str(value)
            elif key in {f.name for f in fields(cls)} and key not in ("emit_spindle",):
                setattr(g, key, value)
        if cfg.get("portrait") and "rotate" not in cfg:
            g.rotate = "vertical"
        return g


@dataclass
class BoardOverrides:
    """Lo que un tablero cambia de los ajustes generales (None = el general)."""

    thickness: Optional[float] = None
    margin: Optional[float] = None
    offset: Optional[tuple] = None      # (dx, dy) mm
    rotate: Optional[str] = None
    selected: bool = True               # se genera al pulsar Generar


@dataclass
class JobUnit:
    """Lo que sale en un .nc: un tablero, o todo el dibujo si no hay tableros."""

    number: Optional[int]
    label: str
    shapes: list[ImportedShape]
    stock: list[ImportedShape]
    bounds: tuple


def _rotation(text: str):
    """(rotate, portrait) de JobSettings para el valor del desplegable."""
    if text == "vertical":
        return None, True
    if text == "auto":
        return "auto", False
    try:
        return float(text), False
    except ValueError:
        raise ProjectError(_("el giro debe ser uno de {join} (hay {text!r}).", join=', '.join(ROTATIONS), text=text)) from None


def _positive(value, what: str) -> Optional[float]:
    if value is None:
        return None
    if not value > 0:
        raise ProjectError(_("{what} debe ser mayor que 0.", what=what))
    return float(value)


@dataclass
class _Step:
    """Un paso de deshacer/rehacer: como estaba el dibujo (formas, ediciones, grupos) antes de la accion. Los ajustes de la
    tabla y de los tableros no se deshacen: se conservan los de ahora (un tablero o una fila que reaparece vuelve con los
    que tenia)."""

    label: str
    drawing: Drawing
    edits: list
    assign: dict
    rows: list
    index_map: Optional[dict]           # forma del estado actual -> forma del estado que se restaura (None: las mismas)
    overrides: dict = field(default_factory=dict)       # ajustes de cada tablero de ese estado (solo para los tableros que
    hold_points: dict = field(default_factory=dict)     # reaparecen al deshacer o rehacer: los demas conservan los de ahora)


def _invert(index_map: Optional[dict]) -> Optional[dict]:
    if index_map is None:
        return None
    out: dict = {}
    for k in sorted(index_map):
        out.setdefault(index_map[k], k)
    return out


class Project:
    def __init__(self, tools: Optional[dict] = None, machines: Optional[dict] = None):
        self.tools: dict[str, Tool] = tools or {}
        self.tools_path: Optional[Path] = None      # el herramientas.json de donde salieron (None: no hay archivo)
        # las fichas de maquina; sin archivo (uso como libreria) vale la de siempre. La ventana y la linea de comandos
        # las leen de maquinas.json y sin ficha no generan.
        self.machines: dict[str, MachineProfile] = (
            dict(machines) if machines is not None else {DEFAULT_MACHINE_ID: GRBL_MAQUINA_REAL})
        self.machines_path: Optional[Path] = None   # el maquinas.json de donde salieron (None: no hay archivo)
        self.general = General()
        self.overrides: dict[int, BoardOverrides] = {}
        self.drawing: Optional[Drawing] = None
        self.rows: list[OpRow] = []
        self.title = ""
        self.exclude_colors: list[str] = []
        self.recipe_file: str = ""
        self.use_boards = True          # convencion de tableros (un rectangulo con formas dentro es un tablero)
        self.hold = HoldSettings()      # sujecion de la plancha: tipo, alcance y margenes
        self.hold_points: dict[Optional[int], list[tuple]] = {}   # puntos editados a mano por tablero (None = dibujo sin tableros; sin entrada: la propuesta)
        self.default_recipe: str = ""   # receta del archivo de configuracion, para aplicarla al abrir un dibujo
        self.assign: dict[int, str] = {}      # vectores movidos a mano a un grupo: indice en drawing.shapes -> color-etiqueta
        self.load_notes: list[Message] = []   # avisos de la ultima vez que se aplico un trabajo guardado
        self.base: Optional[Drawing] = None   # el dibujo tal como se abrio, sin las ediciones de las herramientas de dibujo
        self.edits: list[dict] = []           # esas ediciones, en orden (se guardan en el trabajo y se rehacen al abrirlo)
        self.scratch = False                  # dibujo nuevo, sin SVG detras
        self.draw_color = "#000000"           # trazo de las formas que se dibujan
        self._undo: list[_Step] = []
        self._redo: list[_Step] = []
        self._levels_cache: Optional[tuple] = None
        self._bridge_cache: Optional[tuple] = None

    # --- dibujo -----------------------------------------------------------------------------------------
    def load_svg(self, path, *, ppi: float = 96.0) -> Drawing:
        self.drawing = load_drawing(path, ppi=ppi, exclude_colors=self.exclude_colors, boards=self.use_boards)
        self.base, self.edits, self.scratch, self._undo, self._redo = self.drawing, [], False, [], []
        self.assign, self.load_notes = {}, []
        self.rows = rows_from_shapes(self._cuttable())
        self.sort_rows()
        self.overrides = {b.number: BoardOverrides() for b in self.drawing.layout.boards}
        self.hold_points = {}
        return self.drawing

    def new_drawing(self, folder=".", name: str = "dibujo_nuevo.svg") -> Drawing:
        """Un dibujo vacio para dibujarlo desde cero con las herramientas de dibujo (no hay SVG: se guarda en el trabajo)."""
        excluded = {str(c).lower() for c in self.exclude_colors}
        self.drawing = Drawing(Path(folder) / name, SvgImport([], (0.0, 0.0)), [], [], BoardLayout(), [], excluded, [])
        self.base, self.edits, self.scratch, self._undo, self._redo = self.drawing, [], True, [], []
        self.assign, self.load_notes = {}, []
        self.rows = []
        self.overrides = {}
        self.hold_points = {}
        return self.drawing

    def rename(self, path) -> None:
        """Cambia el nombre del dibujo (para un dibujo nuevo: el del trabajo que se guarda; sale en los nombres de los .nc)."""
        if self.drawing is not None:
            self.drawing = replace(self.drawing, path=Path(path))
            self.base = replace(self.base, path=Path(path)) if self.base is not None else None

    @staticmethod
    def _cuttable_indices(drawing: Drawing) -> list[int]:
        """Indices (en drawing.shapes) de lo que puede cortarse: lo que hay dentro de los tableros (y lo suelto), sin los
        rectangulos de tablero ni las elipses de agrupacion."""
        lay = drawing.layout
        if not lay.boards:
            return list(range(len(drawing.shapes)))
        idx = [j for b in lay.boards for j in b.contents] + list(lay.loose)
        return sorted(set(idx))

    def _cuttable(self) -> list[ImportedShape]:
        """Las formas que pueden cortarse, con el color de su grupo si se movieron a uno."""
        shapes = self.shapes()
        return [shapes[j] for j in self._cuttable_indices(self.drawing)] if self.drawing else []

    # --- grupos de vectores --------------------------------------------------------------------------------
    def shapes(self) -> list[ImportedShape]:
        """Las formas del dibujo; las que se movieron a un grupo llevan el color-etiqueta del grupo (y sin relleno), asi
        que para la tabla, la receta y el lienzo un grupo es un color mas."""
        d = self.drawing
        if d is None:
            return []
        if not self.assign:
            return d.shapes
        out = list(d.shapes)
        for i, color in self.assign.items():
            if 0 <= i < len(out):
                out[i] = replace(out[i], stroke=color, fill=None)
        return out

    def selectable(self) -> list[int]:
        """Los vectores que se pueden elegir: lo que se corta, sin los rectangulos de tablero."""
        return self._cuttable_indices(self.drawing) if self.drawing else []

    def levels(self) -> dict[int, int]:
        """Nivel de anidamiento de cada vector cerrado (0 exterior, 1 dentro de otro, 2 dentro de ese...), por indice en
        drawing.shapes. Es de la geometria: no cambia al mover vectores de grupo."""
        d = self.drawing
        if d is None:
            return {}
        if self._levels_cache is None or self._levels_cache[0] is not d:
            idx = self._cuttable_indices(d)
            found = nesting_levels([d.shapes[i] for i in idx])
            self._levels_cache = (d, {i: lv for i, lv in zip(idx, found) if lv is not None})
        return self._levels_cache[1]

    def group_color(self, index: int) -> Optional[str]:
        return self.assign.get(index)

    def groups(self) -> list[OpRow]:
        """Las filas que son grupos de vectores, en el orden de la tabla."""
        return [r for r in self.rows if r.group]

    def _picked(self, indices) -> list[int]:
        allowed = set(self.selectable())
        out: list[int] = []
        for i in indices:
            if isinstance(i, bool) or not isinstance(i, int) or i not in allowed:
                raise ProjectError(_("la forma {i!r} no existe o no se puede elegir.", i=i))
            if i not in out:
                out.append(i)
        return out

    def _new_group_color(self) -> str:
        used = {c for c in self.assign.values()}
        for sh in (self.drawing.shapes if self.drawing else []):
            used.update(c for c in (_color(sh.stroke), _color(sh.fill)) if c)
        for c in GROUP_COLORS:
            if c not in used:
                return c
        n = 0
        while True:                                   # una vez gastados los de la lista: tonos repartidos por el circulo
            import colorsys
            r, g, b = colorsys.hsv_to_rgb((n * 0.61803398875) % 1.0, 0.75, 0.8)
            c = "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))
            if c not in used:
                return c
            n += 1

    def _regroup(self, names: Optional[dict] = None) -> None:
        """Rehace la tabla tras mover vectores entre grupos: las filas que siguen teniendo vectores conservan sus ajustes,
        las que se quedan vacias desaparecen y las nuevas van al final (los grupos nuevos con el nombre de `names`)."""
        names = names or {}
        groups = set(self.assign.values())
        fresh = rows_from_shapes(self._cuttable())
        counts = {r.key: r.count for r in fresh}
        kept = [replace(r, count=counts[r.key]) for r in self.rows if r.key in counts]
        have = {r.key for r in kept}
        for f in fresh:
            if f.key in have:
                continue
            if f.key[1] is None and f.key[0] in groups:
                f = replace(f, group=True, name=names.get(f.key[0]) or "Grupo")
            kept.append(f)
        self.rows = [r for r in kept if not r.group] + [r for r in kept if r.group]      # los grupos, al final
        self.sort_rows()                                  # y todo en el orden correcto: taladros, pozos, interiores, exteriores

    def create_group(self, indices, name: str, action: str = "corte") -> str:
        """Un grupo nuevo con estos vectores y su fila en la tabla. Devuelve el color-etiqueta del grupo."""
        name = str(name).strip()
        if not name:
            raise ProjectError(_("el grupo necesita un nombre."))
        if action not in ACTIONS:
            raise ProjectError(_("la operacion del grupo debe ser una de {join} (hay {action!r}).", join=', '.join(ACTIONS), action=action))
        picked = self._picked(indices)
        if not picked:
            raise ProjectError(_("no hay ninguna forma elegida."))
        self._push(_("Grupo nuevo"))
        color = self._new_group_color()
        for i in picked:
            self.assign[i] = color
        self._regroup({color: name})
        next(r for r in self.rows if r.key == (color, None)).action = action
        self.sort_rows()                                  # un pozo o un taladro va antes que los cortes
        return color

    def move_to_group(self, indices, color: str) -> None:
        """Pasa estos vectores a un grupo que ya existe (su color-etiqueta)."""
        if color not in set(self.assign.values()):
            raise ProjectError(_("ese grupo no existe."))
        picked = self._picked(indices)
        self._push(_("Mover a grupo"))
        for i in picked:
            self.assign[i] = color
        self._regroup()

    def ungroup(self, indices) -> None:
        """Devuelve estos vectores a su color de siempre (un grupo que se queda sin vectores desaparece)."""
        picked = self._picked(indices)
        self._push(_("Quitar del grupo"))
        for i in picked:
            self.assign.pop(i, None)
        self._regroup()

    # --- separar interiores y exteriores ---------------------------------------------------------------------------------
    SIDE_ORDER = ("inside", "on", "outside")                                 # huecos, lineas, piezas: como se cortan
    SPLIT_ROWS = {"inside": (_("Interiores"), "interiores", "dentro"), "on": (_("Líneas"), _("líneas"), "linea"),
                  "outside": (_("Exteriores"), "exteriores", "fuera")}
    SPLIT_WORDS = {"inside": _("interiores (huecos)"), "on": _("líneas abiertas"), "outside": _("exteriores (piezas)")}

    def cut_sides(self) -> dict[int, str]:
        """El lado de corte de cada vector del dibujo como lo decide el modo sin receta: por anidamiento, dentro de su
        tablero y sin contar lo que esta en Ignorar. "inside" es un hueco, "outside" una pieza y "on" una linea abierta.
        Es de la geometria (no cambia al mover vectores de grupo). Por indice en drawing.shapes."""
        d = self.drawing
        if d is None:
            return {}
        shapes = self.shapes()
        ignored = {r.key for r in self.rows if not r.active}
        lay = d.layout
        units = [list(b.contents) for b in lay.boards] + [list(lay.loose)] if lay.boards else [list(range(len(shapes)))]
        out: dict[int, str] = {}
        for idx in units:
            idx = [i for i in idx if 0 <= i < len(shapes) and shape_key(shapes[i]) not in ignored]
            for i, side in zip(idx, suggest_sides([shapes[i] for i in idx])):
                out[i] = side
        return out

    def _split_classes(self, row: OpRow, sides: Optional[dict] = None) -> dict[str, list[int]]:
        """Los vectores de esta fila, repartidos en huecos ("inside"), lineas ("on") y piezas ("outside")."""
        sides = self.cut_sides() if sides is None else sides
        shapes = self.shapes()
        out: dict[str, list[int]] = {k: [] for k in self.SIDE_ORDER}
        for i in self.selectable():
            if shape_key(shapes[i]) == row.key:
                out[sides.get(i, "outside")].append(i)
        return out

    def splittable_rows(self) -> list[OpRow]:
        """Las filas de Corte en Auto que tienen de verdad dos clases de formas (huecos, lineas o piezas) que separar."""
        if self.drawing is None:
            return []
        sides = self.cut_sides()
        return [r for r in self.rows if r.action == "corte" and r.side == "auto"
                and sum(1 for v in self._split_classes(r, sides).values() if v) >= 2]

    def split_cuts(self, key=None) -> list[str]:
        """Separa una fila de Corte en Auto en una fila por clase de forma, para verlas y ajustarlas por separado: los
        **interiores** (huecos, por dentro), las **lineas** abiertas (sobre la linea) y los **exteriores** (piezas, por fuera),
        en ese orden, que es el que corta el programa. Cada fila nueva es un grupo con su color, su lado fijo (ya no depende
        de otras filas) y los mismos ajustes que tenia la de origen (pasante, profundidad, pasada, avances), asi que el
        G-code no cambia. `key`: la fila (trazo, relleno); sin ella vale la unica fila que se puede separar. Devuelve los
        colores-etiqueta de las filas nuevas, de dentro a fuera. Se puede deshacer."""
        if self.drawing is None:
            raise ProjectError(_("abre un dibujo antes de separar cortes."))
        if key is None:
            candidates = self.splittable_rows()
            if not candidates:
                raise ProjectError(_("no hay ninguna fila de Corte en «Auto» con interiores y exteriores que separar."))
            if len(candidates) > 1:
                raise ProjectError(_("hay varias filas que se pueden separar: elige una en la tabla."))
            row = candidates[0]
        else:
            row = next((r for r in self.rows if r.key == tuple(key)), None)
            if row is None:
                raise ProjectError(_("esa fila no existe."))
        if row.action != "corte":
            raise ProjectError(_("«{title}» es un {lower}: solo se separan las filas de Corte.", title=row.title, lower=ACTION_LABEL[row.action].lower()))
        if row.side != "auto":
            raise ProjectError(_("«{title}» ya tiene el lado fijado: ponla en «Auto» si quieres separarla.", title=row.title))
        classes = self._split_classes(row)
        found = [k for k in self.SIDE_ORDER if classes[k]]
        if len(found) < 2:
            only = found[0] if found else None
            what = self.SPLIT_WORDS[only] if only else _("ninguna forma")
            raise ProjectError(_("«{title}» solo tiene {what}: no hay nada que separar.", title=row.title, what=what))
        taken = {r.title for r in self.rows}
        names: dict[str, str] = {}
        self._push(_("Separar interiores y exteriores"))
        for kind in found:
            base, lower, __ = self.SPLIT_ROWS[kind]
            base = f"{row.name}: {lower}" if row.group and row.name else base
            name, n = base, 2
            while name in taken:                          # dos separaciones seguidas: «Interiores (2)»
                name, n = f"{base} ({n})", n + 1
            taken.add(name)
            color = self._new_group_color()
            for i in classes[kind]:
                self.assign[i] = color
            names[kind] = (color, name)
        self._regroup({c: n for c, n in names.values()})
        made = []
        for kind in found:
            color, __ = names[kind]
            new = next(r for r in self.rows if r.key == (color, None))
            new.action, new.side = "corte", self.SPLIT_ROWS[kind][2]
            new.through, new.depth, new.stepdown = row.through, row.depth, row.stepdown
            new.feed, new.plunge = row.feed, row.plunge
            made.append(new)
        self.sort_rows()                                                 # de dentro a fuera, con las demas filas
        return [r.stroke for r in made]

    # --- el orden de las filas: taladros, pozos, interiores, lineas y exteriores -----------------------------------------
    ORDER_NAMES = (_("taladros"), _("pozos"), _("interiores"), _("líneas"), _("exteriores"))
    SIDE_RANK = {"dentro": 2, "linea": 3, "fuera": 4}                         # lo que es una fila de corte con el lado fijo
    CLASS_RANK = {"inside": 2, "on": 3, "outside": 4}                         # y lo que es cada forma de una en «Auto»

    def _rank_span(self, row: OpRow, sides: Optional[dict] = None) -> Optional[tuple]:
        """(el primero, el ultimo) de ORDER_NAMES que corta esta fila: un taladro es 0, un pozo 1; un corte es de
        interiores (2), lineas (3) o exteriores (4) segun su lado y, en «Auto», segun las formas que tenga (una fila
        que mezcla huecos y piezas abarca de 2 a 4). None si no hay nada que ordenar (Ignorar, o sin formas)."""
        if row.action == "taladro":
            return (0, 0)
        if row.action == "vaciado":
            return (1, 1)
        if row.action != "corte":
            return None
        if row.side != "auto":
            rank = self.SIDE_RANK[row.side]
            return (rank, rank)
        found = [self.CLASS_RANK[k] for k, v in self._split_classes(row, sides).items() if v]
        return (min(found), max(found)) if found else None

    @staticmethod
    def _late_rows(items: Sequence[tuple]) -> list:
        """Las filas (nombre, (primero, ultimo)) que se cortan antes de lo que tocaba: (nombre, su primero, la que ya habia
        llegado mas lejos, hasta donde)."""
        late = []
        top, top_name = -1, ""
        for name, (lo, hi) in items:
            if lo < top:
                late.append((name, lo, top_name, top))
            if hi > top:
                top, top_name = hi, name
        return late

    @classmethod
    def _order_text(cls, items: Sequence[tuple]) -> Optional[str]:
        """Si las filas (nombre, (primero, ultimo)), en el orden en que se cortan, no van en el orden correcto, lo que hay que
        decirle a quien las ha movido; None si van bien. Si ni poniendolas en orden se arregla (dos filas que mezclan huecos
        y piezas), dice que hay que separarlas."""
        late = cls._late_rows(items)
        if not late:
            return None
        names = cls.ORDER_NAMES
        examples = "; ".join(_("«{n}» ({lo_name}) va después de «{tn}» ({t_name})", n=n, lo_name=names[lo], tn=tn, t_name=names[t])
                             for n, lo, tn, t in late[:2])
        more = _(" (y {v} más)", v=len(late) - 2) if len(late) > 2 else ""
        head = (_("El orden de las filas no es el correcto: tiene que ser taladros, pozos, interiores, líneas y "
                  "exteriores. {examples}{more}.", examples=examples, more=more))
        if cls._late_rows(sorted(items, key=lambda it: it[1])):
            return (_("{head} Una fila que mezcla interiores y exteriores no se puede ordenar con otras: sepárala con "
                      "«Separar interiores y exteriores» y pulsa «Ordenar».", head=head))
        return _("{head} Pulsa «Ordenar» para ponerlas bien.", head=head)

    def order_problem(self) -> Optional[str]:
        """Lo que esta mal en el orden de la tabla (ver `_order_text`), o None si va bien o no hay nada que ordenar. Con todo en
        corte normal («plain») no hay orden que estropear: se corta todo junto y el programa decide interiores antes que exteriores."""
        if self.drawing is None or plain(self.rows):
            return None
        sides = self.cut_sides()
        items = []
        for r in self.rows:
            span = self._rank_span(r, sides) if r.active else None
            if span is not None:
                items.append((r.title, span))
        return self._order_text(items)

    def sort_rows(self, moved: Optional[OpRow] = None) -> bool:
        """Pone las filas activas en el orden correcto (taladros, pozos, interiores, lineas y exteriores; a igualdad, como
        estaban) sin mover las que estan en Ignorar. Con `moved` (la fila cuyo tipo o lado acaba de cambiar) solo se ordena si
        todas las demas ya iban bien: si alguien las puso a mano en otro orden, se respeta y el programa avisa. La lista se
        cambia en el sitio (la tabla la comparte). Devuelve True si algo cambio de sitio."""
        if self.drawing is None:
            return False
        sides = self.cut_sides()
        spans = {id(r): self._rank_span(r, sides) if r.active else None for r in self.rows}
        slots = [i for i, r in enumerate(self.rows) if spans[id(r)] is not None]
        if moved is not None:
            rest = [(self.rows[i].title, spans[id(self.rows[i])]) for i in slots if self.rows[i] is not moved]
            if self._order_text(rest) is not None:
                return False
        ordered = sorted(slots, key=lambda i: spans[id(self.rows[i])])
        new = list(self.rows)
        for slot, i in zip(slots, ordered):
            new[slot] = self.rows[i]
        changed = any(a is not b for a, b in zip(new, self.rows))
        self.rows[:] = new
        return changed

    def _plan_notes(self, recipe: Recipe, shapes: Sequence[ImportedShape]) -> list[str]:
        """Lo que la tabla hace mal y el G-code no dice (se confirma al generar): filas en «Auto» que decidirian mal si lo
        dejas a su anidamiento y filas que no van en el orden correcto."""
        per_op, __, __ = classify(recipe, shapes)
        overall = {id(s): side for s, side in zip(shapes, suggest_sides(shapes))}
        out = self._auto_side_notes(recipe, shapes, per_op, overall)
        items = []
        for spec, group in zip(recipe.operations, per_op):
            if not group:
                continue
            if spec.kind == "taladro":
                span = (0, 0)
            elif spec.kind == "vaciado":
                span = (1, 1)
            elif spec.side != "auto":
                span = (self.SIDE_RANK[spec.side], self.SIDE_RANK[spec.side])
            else:
                found = [self.CLASS_RANK[overall.get(id(s)) or "outside"] for s in group]
                span = (min(found), max(found))
            items.append((spec.name, span))
        text = self._order_text(items)
        return out + ([text] if text else [])

    def _auto_side_notes(self, recipe: Recipe, shapes: Sequence[ImportedShape], per_op=None, overall=None) -> list[str]:
        """En modo receta, un corte en «Auto» decide por anidamiento solo entre las formas de su propia fila; en el modo sin
        receta (y en lo que se ve en el dibujo) cuenta todo el dibujo. Si no coinciden, un hueco puede salir cortado por
        fuera: se avisa de cada fila en la que pasa."""
        if per_op is None:
            per_op, __, __ = classify(recipe, shapes)
        if overall is None:
            overall = {id(s): side for s, side in zip(shapes, suggest_sides(shapes))}
        out = []
        for spec, group in zip(recipe.operations, per_op):
            if spec.kind != "corte" or spec.side != "auto" or not group:
                continue
            holes = pieces = 0
            for s, own in zip(group, suggest_sides(group)):
                real = overall.get(id(s), own)
                holes += own == "outside" and real == "inside"        # se cortaria por fuera siendo un hueco
                pieces += own == "inside" and real == "outside"       # se cortaria por dentro siendo una pieza
            if not (holes or pieces):
                continue
            what = []
            if holes:
                what.append(_("{holes} forma(s) se cortarían por fuera siendo huecos", holes=holes))
            if pieces:
                what.append(_("{pieces} forma(s) se cortarían por dentro siendo piezas", pieces=pieces))
            out.append(_("«{name}» está en «Auto», que solo mira el anidamiento entre las formas de su fila: {join} según "
                         "el dibujo entero. Pon «Por dentro» o «Por fuera» en esa fila (o usa «Separar interiores y "
                         "exteriores»).", name=spec.name, join=' y '.join(what)))
        return out

    # --- puentes -------------------------------------------------------------------------------------------------------
    def _bridge_settings(self) -> TabSettings:
        g = self.general
        try:
            return TabSettings(width=g.tab_width, height=g.tab_height, spacing=g.tab_spacing)
        except TabError:
            return TabSettings()

    def bridge_row(self, index: int) -> Optional[OpRow]:
        """La fila de la tabla a la que pertenece ese vector (None si no hay)."""
        shapes = self.shapes()
        if not 0 <= index < len(shapes):
            return None
        key = shape_key(shapes[index])
        return next((r for r in self.rows if r.key == key), None)

    def bridge_problem(self, index: int) -> str:
        """Por que no se pueden poner puentes en ese vector ('' si se puede): tiene que ser una forma cerrada que se corta de
        lado a lado."""
        if not self.general.bridges:
            return _("los puentes están desactivados: marca «Poner puentes» en los ajustes del trabajo.")
        shapes = self.shapes()
        if self.drawing is None or index not in set(self.selectable()):
            return _("esa forma no se corta.")
        s = shapes[index]
        if not s.closed or not isinstance(s.shape, Polygon):
            return _("los puentes solo se ponen en formas cerradas.")
        row = self.bridge_row(index)
        if row is None or row.action != "corte":
            return _("los puentes son de los cortes: la fila de esa forma no es un corte.")
        if not row.through:
            return _("la fila de esa forma no corta de lado a lado: no necesita puentes.")
        return ""

    def bridge_map(self) -> dict:
        """Los puentes que tiene cada vector de la vista del dibujo: indice en drawing.shapes -> (puentes, a mano). Los
        puestos a mano valen tal cual; los demas son los que propone el programa a las piezas de las filas que los piden
        (el lado «Auto» se mira con el dibujo entero, como en la vista del dibujo)."""
        d = self.drawing
        if d is None or not self.general.bridges:
            return {}
        try:
            diameter = self.resolve_tool().diameter
        except ProjectError:
            diameter = None
        settings = self._bridge_settings()
        shapes = self.shapes()
        rows = {r.key: r for r in self.rows}
        wanted = tuple((r.key, r.side) for r in self.rows if r.active and r.tabs and r.action == "corte" and r.through)      # sin ordenar: las llaves llevan None
        sig = (id(d), diameter, settings, wanted, tuple((i, s.tabs) for i, s in enumerate(shapes) if s.tabs is not None))
        if self._bridge_cache is not None and self._bridge_cache[0] == sig:
            return self._bridge_cache[1]
        idx = self.selectable()
        overall = {}
        if diameter is not None and wanted:
            overall = dict(zip(idx, suggest_sides([shapes[i] for i in idx])))
        out: dict = {}
        for i in idx:
            s = shapes[i]
            if not s.closed:
                continue
            if s.tabs is not None:
                out[i] = (tuple(s.tabs), True)
                continue
            row = rows.get(shape_key(s))
            if diameter is None or row is None or not (row.active and row.tabs and row.action == "corte" and row.through):
                continue
            side = overall.get(i) if row.side == "auto" else {"fuera": "outside", "dentro": "inside", "linea": "on"}[row.side]
            if side in ("outside", "inside"):
                found = propose_tabs(s.shape, settings, diameter, inside=side == "inside")
                if found:
                    out[i] = (found, False)
        self._bridge_cache = (sig, out)
        return out

    def set_bridges(self, indices, tabs: Optional[Sequence[Tab]]) -> EditResult:
        """Pone a mano los puentes de esos vectores (`tabs` vacio: sin puentes) o los devuelve a automaticos (None). Se
        puede deshacer."""
        picked = self._picked(indices)
        for i in picked:
            if tabs is not None and (why := self.bridge_problem(i)):
                raise ProjectError(why[:1].upper() + why[1:])
        return self.edit(tabs_edit(picked, tabs))

    # --- herramientas de dibujo: ediciones, deshacer y rehacer ---------------------------------------------------------
    MAX_UNDO = 200

    def _step(self, label: str, index_map: Optional[dict]) -> _Step:
        return _Step(label, self.drawing, list(self.edits), dict(self.assign), [replace(r) for r in self.rows], index_map,
                     {n: replace(o) for n, o in self.overrides.items()},
                     {k: list(v) for k, v in self.hold_points.items()})

    def _push(self, label: str, index_map: Optional[dict] = None) -> None:
        """Guarda como esta ahora el dibujo, para poder deshacer lo que se va a hacer."""
        self._undo.append(self._step(label, index_map))
        del self._undo[:-self.MAX_UNDO]
        self._redo.clear()

    @property
    def undo_label(self) -> str:
        return self._undo[-1].label if self._undo else ""

    @property
    def redo_label(self) -> str:
        return self._redo[-1].label if self._redo else ""

    def edit(self, edit: dict) -> EditResult:
        """Hace una edicion de las herramientas de dibujo (ver cnc.drawing_edit). Se puede deshacer. Las formas conservan su
        grupo (las copias, el de la forma de la que salen) y los tableros sus ajustes."""
        if self.drawing is None:
            raise ProjectError(_("abre un dibujo o crea uno nuevo antes de dibujar."))
        try:
            result = apply_edit(self.drawing.shapes, edit)
        except EditError as e:
            raise ProjectError(str(e)) from None
        labels = set(self.assign.values())
        for shape, src in zip(result.shapes, result.origin):
            if src is None and labels & {_color(shape.stroke), _color(shape.fill)}:
                raise ProjectError(_("ese color es la etiqueta de un grupo de formas: elige otro para la forma nueva."))
        self._push(edit_label(edit), {new: old for new, old in enumerate(result.origin) if old is not None})
        self._install(result)
        self.edits.append(edit)
        return result

    def _install(self, result: EditResult) -> None:
        """Pone las formas de un resultado como el dibujo: tableros nuevos, grupos, tabla y ajustes de tablero al dia."""
        old = self.drawing
        layout = detect_boards(result.shapes) if self.use_boards else BoardLayout()
        self.drawing = replace(old, shapes=result.shapes, layout=layout)
        assign = {new: self.assign[src] for new, src in enumerate(result.origin) if src in self.assign}
        self.assign = {i: c for i, c in assign.items() if i in set(self.selectable())}
        self._carry_boards(old, result.index_map())
        self._regroup()

    def _carry_boards(self, old: Drawing, forward: Optional[dict], saved: Optional[_Step] = None) -> None:
        """Tras cambiar las formas: cada tablero nuevo recibe los ajustes (y los puntos de sujecion) del tablero antiguo que
        tiene el mismo rectangulo (`forward`: forma antigua -> nueva; None si no cambia). Los puntos solo pasan si el
        tablero sigue del mismo tamano (van con el, aunque lo muevan); si no, vuelve la propuesta. Un tablero que no viene
        de ninguno (reaparece al deshacer un borrado) recupera lo que `saved` guardo de el."""
        source: dict[int, Board] = {}
        for b in old.layout.boards:
            k = b.index if forward is None else forward.get(b.index)
            if k is not None:
                source.setdefault(k, b)
        overrides: dict[int, BoardOverrides] = {}
        points: dict[Optional[int], list[tuple]] = {}
        for nb in self.drawing.layout.boards:
            ob = source.get(nb.index)
            if ob is None and saved is not None:
                if nb.number in saved.overrides:
                    overrides[nb.number] = replace(saved.overrides[nb.number])
                else:
                    overrides[nb.number] = BoardOverrides()
                if nb.number in saved.hold_points:
                    points[nb.number] = list(saved.hold_points[nb.number])
                continue
            overrides[nb.number] = replace(self.overrides[ob.number]) if ob and ob.number in self.overrides else BoardOverrides()
            if ob and ob.number in self.hold_points and abs(ob.width - nb.width) < 1e-6 and abs(ob.height - nb.height) < 1e-6:
                dx, dy = nb.bounds[0] - ob.bounds[0], nb.bounds[1] - ob.bounds[1]
                points[nb.number] = [(x + dx, y + dy) for x, y in self.hold_points[ob.number]]
        if None in self.hold_points and not self.drawing.layout.boards and not old.layout.boards:
            points[None] = list(self.hold_points[None])
        self.overrides, self.hold_points = overrides, points

    def _restore(self, step: _Step) -> None:
        old = self.drawing
        current = {r.key: r for r in self.rows}
        self.drawing = replace(step.drawing, path=old.path)
        self.edits, self.assign = list(step.edits), dict(step.assign)
        self.rows = [current.get(r.key, replace(r)) for r in step.rows]      # los ajustes de ahora ganan a los guardados
        self._carry_boards(old, step.index_map, step)
        self._regroup()

    def undo(self) -> str:
        """Deshace la ultima edicion o cambio de grupos. Devuelve su nombre ('' si no hay nada que deshacer)."""
        if not self._undo:
            return ""
        step = self._undo.pop()
        self._redo.append(self._step(step.label, _invert(step.index_map)))
        self._restore(step)
        return step.label

    def redo(self) -> str:
        if not self._redo:
            return ""
        step = self._redo.pop()
        self._undo.append(self._step(step.label, _invert(step.index_map)))
        self._restore(step)
        return step.label

    def _apply_edits(self, raw) -> None:
        """Rehace las ediciones de dibujo de un trabajo guardado sobre el dibujo abierto. Si el SVG cambio desde que se guardo
        (otro numero de formas) las ediciones no valdrian y se descartan con un aviso; una edicion que ya no se puede hacer
        corta ahi la lista."""
        ops = raw.get("operaciones") if isinstance(raw, dict) else None
        ops = ops if isinstance(ops, list) else []
        had = bool(self.edits)
        self.edits, self._undo, self._redo = [], [], []
        if self.base is None or not (had or ops):
            return
        self.drawing, self.assign = replace(self.base, path=self.drawing.path), {}      # se parte del dibujo sin editar
        self.rows = rows_from_shapes(self._cuttable())
        self.overrides = {b.number: BoardOverrides() for b in self.drawing.layout.boards}
        self.hold_points = {}
        if not ops:
            return
        total = raw.get("formas_base")
        if not self.scratch and total is not None and total != len(self.base.shapes):
            self.load_notes.append(Message(
                _("El dibujo ha cambiado desde que se guardo el trabajo (tenia {total} formas y ahora tiene "
                  "{len}): se descartan las ediciones hechas con las herramientas de dibujo.", total=total, len=len(self.base.shapes)), "warning"))
            return
        color = str(raw.get("color") or "").strip().lower()
        if re.fullmatch(r"#[0-9a-f]{6}", color):
            self.draw_color = color
        for n, op in enumerate(ops, start=1):
            try:
                result = apply_edit(self.drawing.shapes, op)
            except EditError as e:
                self.load_notes.append(Message(
                    _("No se pudo rehacer la edicion {n} de {len} del trabajo guardado ({e}): se descartan ella y las "
                      "siguientes.", n=n, len=len(ops), e=e), "warning"))
                break
            self._install(result)
            self.edits.append(op)

    def units(self) -> list[JobUnit]:
        if self.drawing is None:
            return []
        d = self.drawing
        shapes = self.shapes()
        if not d.layout.boards:
            xs = [s.shape.bounds for s in d.shapes] or [(0, 0, 0, 0)]
            bounds = (min(b[0] for b in xs), min(b[1] for b in xs), max(b[2] for b in xs), max(b[3] for b in xs))
            return [JobUnit(None, _("Dibujo completo"), list(shapes), list(d.stock), bounds)]
        return [JobUnit(b.number, _("Tablero {number} ({width:.0f} x {height:.0f} mm)", number=b.number, width=b.width, height=b.height),
                        [shapes[j] for j in b.contents], d.board_stock(b), b.bounds) for b in d.layout.boards]

    def unit(self, number: Optional[int]) -> JobUnit:
        for u in self.units():
            if u.number == number:
                return u
        raise ProjectError(_("no existe el tablero {number}", number=number))

    # --- operaciones ------------------------------------------------------------------------------------
    def active_shapes(self, shapes: Sequence[ImportedShape]) -> list[ImportedShape]:
        """Las formas sin las de filas en Ignorar."""
        ignored = {r.key for r in self.rows if not r.active}
        return [s for s in shapes if shape_key(s) not in ignored]

    def recipe(self) -> Optional[Recipe]:
        """None cuando todo es corte pasante normal (se corta todo junto, como sin receta)."""
        if plain(self.rows):
            return None
        try:
            return build_recipe(self.rows, self.title)
        except RecipeError as e:
            raise ProjectError(str(e)) from None

    def reset_rows(self) -> None:
        """Todo vuelve a corte pasante normal (la tabla como al abrir el dibujo). Los vectores siguen en sus grupos:
        cada grupo conserva su nombre y su fila, con los ajustes de siempre."""
        names = {r.key[0]: r.name for r in self.rows if r.group}
        self.rows = []
        if self.drawing:
            self._regroup(names)
        self.title = ""
        self.recipe_file = ""

    def apply_default_recipe(self) -> Optional[Message]:
        """Aplica la receta del archivo de configuracion, si hay. Devuelve un aviso si no se pudo."""
        if not self.default_recipe:
            return None
        try:
            self.load_recipe_file(self.default_recipe)
        except ProjectError as e:
            return Message(_("No se aplica la receta del archivo de configuracion: {e}", e=e), "warning")
        return Message(_("Receta del archivo de configuracion: {name}", name=Path(self.default_recipe).name))

    def apply_recipe(self, recipe: Recipe, source: str = "") -> None:
        """Una receta es de colores: no toca los grupos de vectores (siguen como estaban, al final de la tabla)."""
        self.rows = rows_from_recipe(recipe, [r for r in self.rows if not r.group]) + [r for r in self.rows if r.group]
        self.title = recipe.title
        self.recipe_file = source

    def load_recipe_file(self, path) -> Recipe:
        try:
            recipe = load_recipe(path)
        except RecipeError as e:
            raise ProjectError(str(e)) from None
        self.apply_recipe(recipe, str(path))
        return recipe

    def recipe_text(self) -> str:
        try:
            data = recipe_dict(self.rows, self.title, ignored_colors(self.rows))
            parse_recipe(data, _("tabla de operaciones"))                 # que lo que se guarda se pueda volver a leer
        except RecipeError as e:
            raise ProjectError(str(e)) from None
        return recipe_to_toml(data)

    # --- ajustes ----------------------------------------------------------------------------------------
    def resolve_tool(self) -> Tool:
        spec = (self.general.tool or "").strip()
        if not spec:
            raise ProjectError(_("elige una fresa (una ficha o un diametro en mm)."))
        try:
            diameter = float(spec.replace(",", "."))
        except ValueError:
            try:
                return get_tool(self.tools, spec)
            except ToolsFileError as e:
                raise ProjectError(str(e)) from None
        if not diameter > 0 or diameter != diameter or diameter == float("inf"):
            raise ProjectError(_("el diametro de la fresa debe ser un numero mayor que 0."))
        return Tool(diameter=diameter)

    def machine_id(self) -> str:
        """El identificador de la maquina del trabajo: la elegida, o la unica ficha que haya."""
        ident, why = choose_machine(self.machines, (self.general.machine or "").strip())
        if ident is None:
            raise ProjectError(why)
        return ident

    def resolve_machine(self) -> MachineProfile:
        return self.machines[self.machine_id()]

    def tool_hints(self, for_rows: bool = False) -> dict[str, str]:
        """Que valor se usa cuando una casilla de avance/pasada queda vacia (texto gris de la ventana).
        En las filas de la tabla vale primero el del trabajo y despues el de la ficha; en el trabajo, el de la ficha."""
        try:
            tool = self.resolve_tool()
        except ProjectError:
            tool = None
        g = self.general
        out: dict[str, str] = {}
        for key, job, card in (("feed", g.feed, tool.feed_xy if tool else None),
                               ("plunge", g.plunge, tool.feed_z if tool else None),
                               ("stepdown", g.stepdown, tool.stepdown if tool else None)):
            if for_rows and job is not None:
                out[key] = _("{job:g} (trabajo)", job=job)
            elif card is not None:
                out[key] = _("{card:g} (ficha)", card=card)
        if tool is not None and tool.stepover is not None:
            out["stepover"] = _("{v:g} (ficha)", v=tool.stepover * 100)
        return out

    def board_values(self, number: Optional[int]) -> dict:
        o = self.overrides.get(number, BoardOverrides()) if number is not None else BoardOverrides()
        g = self.general
        return {
            "thickness": o.thickness if o.thickness is not None else g.thickness,
            "margin": o.margin if o.margin is not None else g.margin,
            "rotate": o.rotate if o.rotate is not None else g.rotate,
            "offset": o.offset,
        }

    def job_settings(self, number: Optional[int]) -> JobSettings:
        g = self.general
        v = self.board_values(number)
        tool = self.resolve_tool()
        machine = self.resolve_machine()
        if v["thickness"] is None:
            raise ProjectError(_("falta el grosor del material."))
        if not v["thickness"] > 0:
            raise ProjectError(_("el grosor del material debe ser mayor que 0."))
        if v["margin"] < 0:
            raise ProjectError(_("el margen no puede ser negativo."))
        if not g.origin_margin >= 0:
            raise ProjectError(_("el margen del cero no puede ser negativo."))
        if not 0 < g.clearance_z <= g.safe_z:
            raise ProjectError(_("la Z de aproximacion debe ser mayor que 0 y no mayor que la Z segura."))
        rotate, portrait = _rotation(v["rotate"])
        try:
            tabs = TabSettings(width=g.tab_width, height=g.tab_height, spacing=g.tab_spacing)
        except TabError as e:
            if g.bridges and any(r.tabs and r.active for r in self.rows):
                raise ProjectError(str(e)) from None
            tabs = TabSettings()                    # nadie pide puentes: un ajuste que no vale no estorba
        recipe_given = not plain(self.rows)
        feed, plunge, stepdown = (_positive(x, n) for x, n in (
            (g.feed, _("el avance de corte")), (g.plunge, _("el avance de bajada")), (g.stepdown, _("la pasada"))))
        sources = {}
        if not recipe_given:
            feed, sources["feed"] = (feed, "ajuste") if feed is not None else (tool.feed_xy, "ficha")
            plunge, sources["plunge"] = (plunge, "ajuste") if plunge is not None else (tool.feed_z, "ficha")
            if stepdown is None and tool.stepdown is not None:
                stepdown, sources["stepdown"] = tool.stepdown, "ficha"
            elif stepdown is not None:
                sources["stepdown"] = "ajuste"
            if feed is None:
                raise ProjectError(_("falta el avance de corte: rellenalo o ponlo en la ficha de la fresa."))
            if plunge is None:
                raise ProjectError(_("falta el avance de bajada: rellenalo o ponlo en la ficha de la fresa."))
        else:
            feed = feed if feed is not None else tool.feed_xy
            plunge = plunge if plunge is not None else tool.feed_z
            stepdown = stepdown if stepdown is not None else tool.stepdown
        return JobSettings(
            tool=tool, thickness=float(v["thickness"]), margin=float(v["margin"]), feed=feed, plunge=plunge,
            stepdown=stepdown, rotate=rotate, portrait=portrait, origin=g.origin, origin_margin=float(g.origin_margin),
            offset=v["offset"],
            safe_z=g.safe_z, clearance_z=g.clearance_z, emit_spindle=g.emit_spindle,
            pause_at_start=g.pause_at_start, home_at_end=g.home_at_end, return_to_origin=g.return_to_origin,
            end_code=g.end_code, machine=machine, tabs=tabs, bridges=g.bridges, sources=sources)

    # --- planificar y escribir -------------------------------------------------------------------------------
    def plan(self, number: Optional[int]) -> JobPlan:
        """El plan de un tablero. Un ajuste que falta o no vale es un plan con error, no una excepcion. Un plan que
        falla antes de colocar el trabajo (falta la fresa o el grosor) dice igualmente donde cae la maquina."""
        plan = self._plan(number)
        if not plan.frame:
            plan.frame = self.machine_frame(number)
        return plan

    def _plan(self, number: Optional[int]) -> JobPlan:
        unit = self.unit(number)
        if not unit.shapes:
            text = _("El dibujo esta vacio: dibuja algo con el menu Dibujo (o abre un SVG).")
            return JobPlan(messages=[Message(text, "error")], error=text)
        try:
            settings = self.job_settings(number)
            recipe = self.recipe()
        except ProjectError as e:
            return JobPlan(messages=[Message(str(e), "error")], error=str(e))
        shapes = self.active_shapes(unit.shapes)
        plan = plan_job(shapes, unit.stock, settings, recipe, number)
        if recipe is not None and plan.ok:
            for text in self._plan_notes(recipe, shapes):
                plan.messages.append(Message(text, "warning", severe=True))
                plan.alarms.append(text)
        ignored = len(unit.shapes) - len(shapes)
        if ignored:
            plan.messages.insert(0, Message(_("Ignoradas en la tabla: {ignored} forma(s)", ignored=ignored)))
        return plan

    def machine_frame(self, number: Optional[int]) -> tuple:
        """Las cuatro esquinas del recorrido de la maquina elegida (de 0,0 a X,Y) sobre el dibujo tal como viene del SVG,
        para el tablero `number`: con el mismo giro, cero y desplazamiento que tendra el G-code. No hace falta tener
        elegida la fresa ni el grosor (sin fresa el giro automatico no cuenta con su radio). Vacio si no se sabe: sin
        maquina elegida, sin formas que cortar o con un ajuste que no vale."""
        try:
            machine = self.resolve_machine()
            unit = self.unit(number)
            shapes = self.active_shapes(unit.shapes)
            recipe = self.recipe()
            values = self.board_values(number)
            rotate, portrait = _rotation(values["rotate"])
        except ProjectError:
            return ()
        kept = [s for group in classify(recipe, shapes)[0] for s in group] if recipe is not None else shapes
        if not kept or not self.general.origin_margin >= 0:
            return ()
        try:
            tool = self.resolve_tool()
        except ProjectError:
            tool = Tool(diameter=1e-6)
        g = self.general
        return lay_out(kept, unit.stock, rotate_spec=rotate, portrait=portrait, tool=tool, machine=machine,
                       boards_mode=number is not None, origin=g.origin, origin_margin=float(g.origin_margin),
                       offset=values["offset"]).frame

    def snapshot(self) -> "Project":
        """Copia para planificar en otro hilo mientras la ventana sigue editando los ajustes (el dibujo se comparte:
        no se modifica nunca)."""
        other = Project(self.tools, self.machines)
        other.general = replace(self.general)
        other.overrides = {n: replace(o) for n, o in self.overrides.items()}
        other.drawing = self.drawing
        other.rows = [replace(r) for r in self.rows]
        other.title, other.recipe_file, other.use_boards = self.title, self.recipe_file, self.use_boards
        other.exclude_colors = list(self.exclude_colors)
        other.hold = replace(self.hold)
        other.hold_points = {n: list(v) for n, v in self.hold_points.items()}
        other.assign = dict(self.assign)
        return other

    # --- sujecion ----------------------------------------------------------------------------------------------
    def marks_text(self, number: Optional[int], points: Sequence[tuple]) -> str:
        """El programa que lleva la maquina a cada punto de sujecion para marcarlo (termina como el trabajo:
        con Home o volviendo al origen si asi se pidio)."""
        g, h = self.general, self.hold
        home = safety.home_problem(self.resolve_machine(), g.home_at_end)
        if home:
            raise ProjectError(home)
        try:
            return marks_gcode(points, title=self.unit(number).label, kind=h.kind, safe_z=g.safe_z, mark_z=h.mark_z,
                               home_at_end=g.home_at_end, return_to_origin=g.return_to_origin)
        except HoldError as e:
            raise ProjectError(str(e)) from None

    def marks_path(self, base: Union[str, Path], number: Optional[int]) -> Path:
        """NOMBRE_tablero1_marcas.nc (o NOMBRE_marcas.nc si el dibujo solo tiene un tablero)."""
        path = self.output_path(base, number, len(self.units()))
        return path.with_name(path.stem + _("_marcas") + path.suffix)

    def suggest_offset(self, plan: JobPlan, number: Optional[int]) -> Optional[tuple]:
        """Desplazamiento total (dx, dy) que mete en el tablero lo que se sale, sumado al que ya tenga el tablero.
        None si el plan no se sale."""
        if not plan.overhang:
            return None
        dx, dy = self.board_values(number)["offset"] or (0, 0)
        for side, amount in plan.overhang.items():
            sx, sy = suggested_offset(side, amount)
            dx, dy = dx + sx, dy + sy
        return (dx, dy)

    def selected_numbers(self) -> list[Optional[int]]:
        units = self.units()
        if len(units) == 1 and units[0].number is None:
            return [None]
        return [u.number for u in units if self.overrides.get(u.number, BoardOverrides()).selected]

    def plan_selected(self) -> list[tuple[Optional[int], JobPlan]]:
        """Los planes de los tableros marcados, en orden. No escribe nada."""
        numbers = self.selected_numbers()
        if not numbers:
            raise ProjectError(_("no hay ningun tablero marcado para generar."))
        return [(n, self.plan(n)) for n in numbers]

    def write(self, base: Union[str, Path], plans: Sequence[tuple[Optional[int], JobPlan]]) -> list[Path]:
        """Escribe un .nc por plan (nombres como en la linea de comandos). Todo o nada: si algun plan tiene error o un
        tope que lo impide (no cabe, se sale del tablero...) no se escribe ninguno."""
        for n, plan in plans:
            if not plan.writable:                       # sin G-code, o con un tope que lo impide
                why = plan.error or " ".join(plan.blockers) or _("no se pudo generar")
                raise ProjectError(("" if n is None else _("Tablero {n}: ", n=n)) + why)
        targets = [self.output_path(base, n, len(plans)) for n, __ in plans]
        texts = []
        for (n, plan), target in zip(plans, targets):
            try:
                texts.append(plan.text.encode("ascii"))
            except UnicodeEncodeError as e:
                raise ProjectError(_("el G-code tiene un caracter que no es ASCII ({text!r}); GRBL no lo admite: revisa el codigo "
                                     "final.", text=plan.text[e.start])) from None
        for target, data in zip(targets, texts):
            Path(target).write_text(data.decode("ascii"), encoding="ascii")
        return targets

    @staticmethod
    def output_path(base: Union[str, Path], number: Optional[int], count: int) -> Path:
        """Como en la linea de comandos: con varios tableros, NOMBRE_tablero1.nc, NOMBRE_tablero2.nc..."""
        base = Path(base)
        return base if count == 1 or number is None else base.with_name(base.stem + _("_tablero{number}", number=number) + base.suffix)

    # --- guardar y abrir el trabajo ---------------------------------------------------------------------------
    def to_dict(self) -> dict:
        try:
            ident = self.machine_id()
        except ProjectError:
            ident = ""
        data = {
            "version": PROJECT_VERSION,
            "svg": "" if self.scratch or not self.drawing else str(self.drawing.path),
            "titulo": self.title,
            "detectar_tableros": self.use_boards,
            "general": {**asdict(self.general), **({"machine": ident} if ident else {})},
            "tableros": {str(n): {**asdict(o), "offset": list(o.offset) if o.offset else None}
                         for n, o in self.overrides.items()},
            "filas": [{k: v for k, v in asdict(r).items()} for r in self.rows],
            "num_formas": len(self.drawing.shapes) if self.drawing else 0,
            "grupos": [{"color": r.key[0], "nombre": r.name,
                        "formas": sorted(i for i, c in self.assign.items() if c == r.key[0])}
                       for r in self.rows if r.group],
            "sujecion": {"tipo": self.hold.kind, "alcance": self.hold.reach, "margen_corte": self.hold.kerf_margin,
                         "margen_borde": self.hold.edge_margin, "altura_marca": self.hold.mark_z,
                         "separacion_minima": self.hold.min_gap, "distancia_esquinas": self.hold.corner_clear,
                         "distancia_cortes": self.hold.cut_clear, "puntos_minimos": self.hold.min_points,
                         "filas_y_columnas": self.hold.grid, "fila_borde_inferior": self.hold.bottom_row,
                         "puntos": {HOLD_WHOLE if n is None else str(n): [list(p) for p in pts]
                                    for n, pts in self.hold_points.items()}},
        }
        if ident:                           # cada trabajo lleva su maquina: el id y la ficha tal como era al guardarlo
            data["maquina"] = {"id": ident, "ficha": machine_to_dict(self.machines[ident], ident)}
        if self.edits or self.scratch:
            data["edicion"] = {"nuevo": self.scratch, "formas_base": len(self.base.shapes) if self.base else 0,
                               "color": self.draw_color, "operaciones": self.edits}
        return data

    def save(self, path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def apply_dict(self, data: dict) -> None:
        """Pone los ajustes, tableros y filas de un trabajo guardado (el dibujo ya debe estar abierto)."""
        if not isinstance(data, dict) or data.get("version") != PROJECT_VERSION:
            raise ProjectError(_("el archivo de trabajo no es de esta version de la aplicación."))
        self.load_notes = []
        edicion = data.get("edicion")
        if isinstance(edicion, dict) and bool(edicion.get("nuevo")) != self.scratch:
            raise ProjectError(_("el trabajo es de un dibujo nuevo (sin SVG) y hay un SVG abierto, o al reves."))
        self._apply_edits(edicion)
        names = {f.name for f in fields(General)}
        saved = {k: v for k, v in data.get("general", {}).items() if k in names}
        saved.setdefault("bridges", self.general.bridges)        # un trabajo de antes del interruptor no lo cambia
        self.general = General(**saved)
        self.title = data.get("titulo", "")
        self._apply_machine(data.get("maquina"))
        self._apply_groups(data)
        for n, o in data.get("tableros", {}).items():
            off = o.get("offset")
            self.overrides[int(n)] = BoardOverrides(
                thickness=o.get("thickness"), margin=o.get("margin"), offset=tuple(off) if off else None,
                rotate=o.get("rotate"), selected=o.get("selected", True))
        saved = {tuple(r.get(k) for k in ("stroke", "fill")): r for r in data.get("filas", [])}
        row_fields = {f.name for f in fields(OpRow)}
        new = []
        for r in self.rows:
            raw = saved.get(r.key)
            new.append(replace(r, **{k: v for k, v in raw.items() if k in row_fields and k != "count"}) if raw else r)
        order = {tuple(r.get(k) for k in ("stroke", "fill")): i for i, r in enumerate(data.get("filas", []))}
        self.rows = sorted(new, key=lambda r: order.get(r.key, len(order)))
        self._apply_hold(data.get("sujecion"))

    def _apply_machine(self, raw) -> None:
        """La maquina con la que se guardo el trabajo. Si ya no esta en maquinas.json se usa la copia guardada con el
        trabajo (y se avisa); si esta pero ha cambiado, se usa la de ahora (y se avisa de lo que cambio). Nunca impide abrir
        el trabajo."""
        if not isinstance(raw, dict):
            return
        ident = raw.get("id")
        try:
            if not isinstance(ident, str) or not ID.fullmatch(ident):
                raise MachinesFileError("identificador")
            saved = parse_machine(ident, raw.get("ficha"), _("el trabajo"))
        except MachinesFileError:
            self.load_notes.append(Message(_("La ficha de la maquina guardada con el trabajo no se puede leer: se usa la "
                                           "de maquinas.json."), "warning"))
            return
        if (self.general.machine or "").strip() not in ("", ident):
            return                                      # el trabajo dice otra maquina que su copia: manda el trabajo
        self.general.machine = ident
        current = self.machines.get(ident)
        if current is None:
            self.machines[ident] = saved
            self.load_notes.append(Message(
                _("La maquina «{ident}» del trabajo no esta en maquinas.json: se usa la ficha guardada con el "
                  "trabajo ({name}, recorrido {travel_text}). Guardala en Maquinas... si es la tuya.", ident=ident, name=saved.name, travel_text=travel_text(saved)), "warning"))
            return
        changes = differences(saved, current)
        if changes:
            self.load_notes.append(Message(
                _("La ficha de la maquina «{ident}» ha cambiado desde que se guardo el trabajo (", ident=ident)
                + "; ".join(changes) + _("): se usa la de ahora."), "warning"))

    def _apply_groups(self, data: dict) -> None:
        """Los grupos de vectores de un trabajo guardado. Lo que no vale (el dibujo cambio, un vector que ya no existe, un
        color que ahora usa el dibujo) se salta con un aviso: no impide abrir el trabajo."""
        self.assign = {}
        raw = data.get("grupos")
        names: dict = {}
        if raw and self.drawing is not None:
            total = data.get("num_formas")
            if total is not None and total != len(self.drawing.shapes):
                self.load_notes.append(Message(
                    _("El dibujo ha cambiado desde que se guardo el trabajo (tenia {total} formas y ahora tiene "
                      "{len}): se descartan los grupos de formas.", total=total, len=len(self.drawing.shapes)), "warning"))
                raw = None
            allowed = set(self.selectable())
            real = {c for sh in self.drawing.shapes for c in (_color(sh.stroke), _color(sh.fill)) if c}
            skipped = 0
            for entry in (raw if isinstance(raw, list) else []):
                try:
                    color = str(entry["color"]).strip().lower()
                    picked = [i for i in entry["formas"]]
                    if not re.fullmatch(r"#[0-9a-f]{6}", color) or color in real or color in names \
                            or not picked or any(isinstance(i, bool) or not isinstance(i, int) or i not in allowed
                                                 for i in picked):
                        raise ValueError
                except (KeyError, TypeError, ValueError):
                    skipped += 1
                    continue
                names[color] = str(entry.get("nombre") or "").strip()          # vacio: _regroup le pone "Grupo"
                for i in picked:
                    self.assign[i] = color
            if skipped:
                self.load_notes.append(Message(_("{skipped} grupo(s) de formas del trabajo guardado no valen y se descartan.", skipped=skipped), "warning"))
        if self.drawing is not None:
            self._regroup(names)

    def _apply_hold(self, raw) -> None:
        """La sujecion de un trabajo guardado. Es opcional y no impide abrir el trabajo: lo que no valga se deja
        con los valores de siempre y los puntos mal escritos se saltan."""
        self.hold, self.hold_points = HoldSettings(), {}
        if not isinstance(raw, dict):
            return
        try:
            default = HoldSettings()
            hold = HoldSettings(kind=raw.get("tipo", "clavo"), reach=float(raw.get("alcance", default.reach)),
                                min_gap=float(raw.get("separacion_minima", default.min_gap)),
                                corner_clear=float(raw.get("distancia_esquinas", default.corner_clear)),
                                cut_clear=float(raw.get("distancia_cortes", default.cut_clear)),
                                min_points=float(raw.get("puntos_minimos", default.min_points)),
                                grid=raw.get("filas_y_columnas", default.grid),
                                bottom_row=float(raw.get("fila_borde_inferior", default.bottom_row)),
                                kerf_margin=None if raw.get("margen_corte") is None else float(raw["margen_corte"]),
                                edge_margin=None if raw.get("margen_borde") is None else float(raw["margen_borde"]),
                                mark_z=float(raw.get("altura_marca", default.mark_z)))
            hold.validate()                          # el minimo de puntos tiene que ser un entero: 2.5 no vale
            hold.min_points = int(hold.min_points)
            self.hold = hold
        except (HoldError, TypeError, ValueError, OverflowError):
            pass
        points = raw.get("puntos")
        for key, pts in (points.items() if isinstance(points, dict) else ()):
            try:
                number = None if key == HOLD_WHOLE else int(key)
                self.hold_points[number] = [(float(x), float(y)) for x, y in pts]
            except (TypeError, ValueError):
                continue


def startup_project(cwd, folder) -> tuple[Project, list[Message]]:
    """El proyecto vacio con lo que dicen cnc_app.toml y herramientas.json (se buscan en `cwd` y en `folder`, como
    la linea de comandos). Lo que falte o este mal no impide abrir la ventana: sale como mensaje."""
    notes: list[Message] = []
    cfg: dict = {}
    cfg_path = find_config(Path(cwd), Path(folder))
    if cfg_path is not None:
        try:
            cfg = read_config(cfg_path)
        except ConfigError as e:
            notes.append(Message(str(e), "error"))
    tools: dict = {}
    # Empaquetado (.exe / binario de Linux) las fichas de fresas y de maquinas son las que hay junto al programa, se lance
    # desde donde se lance: asi los editores "Fichas..." y "Maquinas..." guardan siempre en el mismo archivo. Desde el
    # codigo fuente, primero la carpeta actual.
    data_folders = [Path(folder), Path(cwd)] if getattr(sys, "frozen", False) else [Path(cwd), Path(folder)]
    tools_path = find_tools_file(cfg.get("tools_file"), data_folders)
    if tools_path is None:
        notes.append(Message(_("No encuentro herramientas.json (busque en {cwd} y en {folder}): escribe el diametro de la fresa "
                               "a mano.", cwd=cwd, folder=folder), "warning"))
    else:
        try:
            tools = load_tools(tools_path)
        except ToolsFileError as e:
            notes.append(Message(str(e), "error"))
    machines: dict = {}
    machines_path = find_machines_file(cfg.get("machines_file"), data_folders)
    if machines_path is None:
        notes.append(Message(_("No encuentro maquinas.json (busque en {cwd} y en {folder}): sin una ficha de maquina no se "
                               "genera nada. Creala con Maquinas... > Nueva desde un log.", cwd=cwd, folder=folder), "error",
                             code="machines"))
    else:
        try:
            machines = load_machines(machines_path)
        except MachinesFileError as e:
            notes.append(Message(str(e), "error", code="machines"))
    project = Project(tools, machines)
    project.machines_path = machines_path
    project.tools_path = tools_path
    project.general = General.from_config(cfg)
    project.exclude_colors = [str(c) for c in cfg.get("exclude_color", [])]
    name = cfg.get("receta")
    if name:
        path = Path(name)
        if not path.is_file() and not path.is_absolute() and (Path(folder) / name).is_file():
            path = Path(folder) / name
        project.default_recipe = str(path)
    return project, notes
