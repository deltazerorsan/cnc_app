"""De un dibujo SVG a G-code: la logica del trabajo, sin imprimir nada ni leer la linea de comandos.

La usan `svg_a_gcode.py` (que imprime los mensajes) y la interfaz grafica (que los muestra en un panel):

    drawing = load_drawing("dibujo.svg")                         # formas, tableros
    plan = plan_job(shapes, stock, JobSettings(...), recipe)     # gira, coloca, ordena y genera
    plan.text            # el G-code (None si plan.error)
    plan.messages        # lo que hay que contar al usuario, en orden

Los mensajes llevan un nivel ("info", "warning", "error") para que cada interfaz los muestre a su manera.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence, Union

from shapely.geometry import box

from .boards import BoardLayout, Board, detect_boards
from .generate import GcodeResult, generate_gcode, tool_label
from .machines import DEFAULT_MACHINES_FILE
from .model import GRBL_MAQUINA_REAL, ContourOp, Job, MachineProfile, Stock, Tool
from . import safety
from .ordering import order_cuts
from .post_grbl import PostOptions
from .recipe import Built, Recipe, RecipeError, build_operations, classify
from .svg_import import ImportedShape, SvgImport, import_svg, is_degenerate, place, rotate, shift, suggest_sides
from .tabs import TabSettings
from .timing import TimeEstimate, estimate_for_machine
from .tools import DEFAULT_TOOLS_FILE
from .i18n import _

OVERHANG_SLACK = 4        # mm de holgura al borde que sugiere el aviso de --offset
SIDE_ORDER = {"inside": 0, "on": 1, "outside": 2}


def app_dir() -> Path:
    """Carpeta del programa: junto al .exe si va empaquetado con PyInstaller, si no junto a `svg_a_gcode.py`."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _find_file(name: str, explicit, folders: Optional[Sequence[Path]]) -> Optional[Path]:
    """El archivo indicado, o el primero con ese nombre que haya en las carpetas (por defecto, la actual y la del programa)."""
    if explicit:
        return Path(explicit)
    for folder in folders if folders is not None else (Path.cwd(), app_dir()):
        candidate = Path(folder) / name
        if candidate.is_file():
            return candidate
    return None


def find_tools_file(explicit=None, folders: Optional[Sequence[Path]] = None) -> Optional[Path]:
    return _find_file(DEFAULT_TOOLS_FILE, explicit, folders)


def find_machines_file(explicit=None, folders: Optional[Sequence[Path]] = None) -> Optional[Path]:
    """El maquinas.json: el indicado, o el primero que haya junto al trabajo y junto al programa."""
    return _find_file(DEFAULT_MACHINES_FILE, explicit, folders)


# --- mensajes ---------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Message:
    text: str
    level: str = "info"         # info | warning | error
    severe: bool = False        # un info que la interfaz debe resaltar (p. ej. "NO CABE")
    code: str = ""              # identifica el mensaje para que una interfaz lo escriba a su manera (p. ej. "overhang")

    def __post_init__(self):
        if self.level not in ("info", "warning", "error"):
            raise ValueError(_("nivel de mensaje desconocido: {level!r}", level=self.level))


# --- ajustes ----------------------------------------------------------------------------------------
@dataclass
class JobSettings:
    """Todo lo que decide el usuario para un trabajo, ya validado y con numeros concretos."""

    tool: Tool
    thickness: float
    margin: float = 0.2
    feed: Optional[float] = None           # mm/min de corte; None = el de la operacion de la receta o la ficha
    plunge: Optional[float] = None
    stepdown: Optional[float] = None
    rotate: Union[str, float, None] = None  # "auto" | grados antihorarios | None (sin giro, salvo portrait/tableros)
    portrait: bool = False
    origin: str = "bbox_bottom_left"
    origin_margin: float = 0.0             # sin tablero y con origin bbox_bottom_left: el cero queda estos mm por fuera de la esquina
    offset: Optional[tuple[float, float]] = None
    order: str = "far-to-near"
    safe_z: float = 5.0
    clearance_z: float = 1.0
    emit_spindle: bool = True
    pause_at_start: bool = False
    home_at_end: bool = False
    return_to_origin: bool = False
    end_code: str = ""
    machine: MachineProfile = GRBL_MAQUINA_REAL
    tabs: TabSettings = field(default_factory=TabSettings)     # como se reparten los puentes de las filas que los piden
    bridges: bool = True                                       # interruptor general: apagado, no se pone ningun puente
    # de donde sale cada valor (solo para el texto del resumen sin receta): "ficha", "linea de comandos"...
    sources: dict = field(default_factory=dict)

    def post_options(self) -> PostOptions:
        return PostOptions(
            emit_spindle=self.emit_spindle, home_at_end=self.home_at_end, pause_at_start=self.pause_at_start,
            return_to_origin=self.return_to_origin, end_code=self.end_code)


# --- el dibujo --------------------------------------------------------------------------------------
@dataclass
class Drawing:
    path: Path
    imported: SvgImport
    shapes: list[ImportedShape]              # lo que queda tras excluir colores y restos sin sentido
    stock: list[ImportedShape]               # contorno del tablero por color (--stock-color)
    layout: BoardLayout
    junk: list[ImportedShape]
    excluded: set
    messages: list[Message] = field(default_factory=list)

    def board_shapes(self, board: Board) -> list[ImportedShape]:
        return [self.shapes[j] for j in board.contents]

    def board_stock(self, board: Board) -> list[ImportedShape]:
        return [self.shapes[board.index]]


def load_drawing(path, *, ppi: float = 96.0, exclude_colors: Sequence[str] = (), stock_color: Optional[str] = None,
                 boards: bool = True) -> Drawing:
    imp = import_svg(path, ppi=ppi)
    stock_color = (stock_color or "").lower()
    excluded = {c.lower() for c in exclude_colors} | ({stock_color} if stock_color else set())
    stock = [s for s in imp.shapes if stock_color and (s.stroke or "").lower() == stock_color]
    kept = [s for s in imp.shapes if (s.stroke or "").lower() not in excluded]
    junk = [s for s in kept if is_degenerate(s)]
    messages = []
    if junk:
        kept = [s for s in kept if not is_degenerate(s)]
        messages.append(Message(
            _("{len} resto(s) sin sentido descartado(s) (lineas de menos de 0,5 mm): ", len=len(junk))
            + "; ".join(f"X {s.shape.centroid.x:.0f}, Y {s.shape.centroid.y:.0f}" for s in junk[:5])
            + (" ..." if len(junk) > 5 else ""), "warning"))
    layout = detect_boards(kept) if boards and not stock_color else BoardLayout()
    return Drawing(Path(path), imp, kept, stock, layout, junk, excluded, messages)


# --- giro, tamano y avisos --------------------------------------------------------------------------
def toolpath_span(kept, stock, degrees, radius):
    """(ancho, alto) que ocupa la trayectoria desde el cero (esquina del tablero o de lo que se corta)
    si se gira `degrees`; la trayectoria se estima como las formas ensanchadas el radio de la fresa."""
    turned = rotate(kept + stock, degrees)
    k, st = turned[: len(kept)], turned[len(kept):]
    k = place(k, "bbox_bottom_left", reference=st or None)
    xs = [b for s in k for b in (s.shape.bounds[0] - radius, s.shape.bounds[2] + radius)]
    ys = [b for s in k for b in (s.shape.bounds[1] - radius, s.shape.bounds[3] + radius)]
    return max(max(xs), 0.0) - min(min(xs), 0.0), max(max(ys), 0.0) - min(min(ys), 0.0)


def choose_rotation(spec, portrait: bool, kept, stock, tool: Tool, boards_mode: bool,
                    machine: MachineProfile = GRBL_MAQUINA_REAL):
    """(grados, explicacion). Prioridad: numero > portrait > auto (por defecto con tableros)."""
    if isinstance(spec, (int, float)) and not isinstance(spec, bool):
        spec = float(spec)
        return spec, (_("sin giro") if spec % 360 == 0 else _("giro de {spec:g} grados", spec=spec))
    portrait = portrait and spec is None
    ref = stock or kept
    xs = [b for s in ref for b in (s.shape.bounds[0], s.shape.bounds[2])]
    ys = [b for s in ref for b in (s.shape.bounds[1], s.shape.bounds[3])]
    if portrait:
        landscape = (max(xs) - min(xs)) > (max(ys) - min(ys))
        return (90.0, _("dibujo apaisado: se gira 90 grados antihorario")) if landscape else (
            0.0, _("dibujo ya vertical: no se gira"))
    if spec == "auto" or (spec is None and boards_mode):
        tx, ty = machine.travel[0], machine.travel[1]
        r = tool.diameter / 2
        w0, h0 = toolpath_span(kept, stock, 0.0, r)
        if w0 <= tx and h0 <= ty:
            return 0.0, _("cabe en el recorrido: no se gira")
        w1, h1 = toolpath_span(kept, stock, 90.0, r)
        if w1 <= tx and h1 <= ty:
            return 90.0, _("no cabe tal cual ({w0:.0f} x {h0:.0f} mm en {tx:g} x {ty:g}): se gira 90 grados antihorario", w0=w0, h0=h0, tx=tx, ty=ty)
        return 0.0, _("no cabe ni girado ({w0:.0f} x {h0:.0f} mm y {w1:.0f} x {h1:.0f} mm en {tx:g} x {ty:g})", w0=w0, h0=h0, w1=w1, h1=h1, tx=tx, ty=ty)
    return 0.0, _("sin giro")


def overhang(bounds, stock) -> dict:
    """Cuanto se sale la trayectoria del tablero por cada lado (mm; solo los lados donde se sale)."""
    bx = [b for s in stock for b in (s.shape.bounds[0], s.shape.bounds[2])]
    by = [b for s in stock for b in (s.shape.bounds[1], s.shape.bounds[3])]
    minx, miny, maxx, maxy = bounds
    out = {"por la izquierda": min(bx) - minx, "por abajo": min(by) - miny,
           "por la derecha": maxx - max(bx), "por arriba": maxy - max(by)}
    return {side: v for side, v in out.items() if v > 0.05}


def virtual_stock(kept, margin: float) -> Optional[ImportedShape]:
    """El tablero que se supone cuando el dibujo no trae recuadro: del cero a la cota maxima de lo que se corta mas el
    mismo margen que separa el cero de la esquina inferior izquierda. None si eso no deja ningun rectangulo."""
    if not kept:
        return None
    maxx = max(s.shape.bounds[2] for s in kept) + margin
    maxy = max(s.shape.bounds[3] for s in kept) + margin
    if not (maxx > 0 and maxy > 0):
        return None
    return ImportedShape(box(0.0, 0.0, maxx, maxy), True, kind="rect")


def machine_frame(before: Sequence[ImportedShape], after: Sequence[ImportedShape], degrees: float,
                  machine: MachineProfile) -> tuple:
    """Las cuatro esquinas del recorrido de la maquina (de 0,0 a X,Y), contadas sobre el DIBUJO tal como viene del SVG.
    El trabajo se gira, se coloca en el cero de la maquina y se desplaza; aqui se deshace eso: de lo que hay antes
    (`before`) y despues (`after`, mismas formas en el mismo orden) sale el movimiento, y las esquinas de la maquina
    vuelven por el. Vacio si no se puede (sin formas)."""
    if not before or not after or len(before) != len(after):
        return ()
    a, b = before[0].shape.centroid, after[0].shape.centroid
    if a.is_empty or b.is_empty:
        return ()
    t = math.radians(degrees)
    c, s = math.cos(t), math.sin(t)
    ux, uy = b.x - (c * a.x - s * a.y), b.y - (s * a.x + c * a.y)         # despues = girar(antes) + u
    tx, ty = machine.travel[0], machine.travel[1]
    out = []
    for mx, my in ((0.0, 0.0), (tx, 0.0), (tx, ty), (0.0, ty)):
        dx, dy = mx - ux, my - uy
        out.append((round(c * dx + s * dy, 6) + 0.0, round(-s * dx + c * dy, 6) + 0.0))   # girar al reves
    return tuple(out)


@dataclass
class LayOut:
    """Lo que sale de colocar un trabajo en la maquina: girado, con el cero en su sitio y, si no hay recuadro, con el
    tablero supuesto."""
    kept: list
    stock: list
    degrees: float
    virtual: bool
    frame: tuple
    messages: list


def lay_out(kept: Sequence[ImportedShape], stock: Sequence[ImportedShape], *, rotate_spec, portrait: bool, tool: Tool,
            machine: MachineProfile, boards_mode: bool, origin: str, origin_margin: float, offset) -> LayOut:
    """Gira (si toca), pone el cero y desplaza el dibujo, igual para generar el G-code que para dibujar en la ventana
    donde cae el recorrido de la maquina. No genera nada: solo hace falta saber que hay que cortar y que fresa."""
    kept, stock = list(kept), list(stock)
    messages: list = []
    degrees, why = choose_rotation(rotate_spec, portrait, kept, stock, tool, boards_mode, machine)
    messages.append(Message(_("Giro: ") + why))

    # se gira todo junto (cortes y tablero) para que compartan centro de giro
    original = list(kept)
    turned = rotate(kept + stock, degrees)
    kept, stock = turned[: len(kept)], turned[len(kept):]
    if stock:
        kept = place(kept, "bbox_bottom_left", reference=stock)
        stock = place(stock, "bbox_bottom_left", reference=stock)
        bx = [b for s in stock for b in (s.shape.bounds[0], s.shape.bounds[2])]
        by = [b for s in stock for b in (s.shape.bounds[1], s.shape.bounds[3])]
        messages.append(Message(_("Tablero: {v:.1f} x {v2:.1f} mm; el cero es su esquina inferior izquierda", v=max(bx) - min(bx), v2=max(by) - min(by))))
    else:
        kept = place(kept, origin)
        if origin == "bbox_bottom_left" and origin_margin:
            m = origin_margin                          # el borde del material se estima a este margen de lo que se corta
            kept = shift(kept, m, m)
            messages.append(Message(_("Sin tablero: el cero queda {m:g} mm por fuera de la esquina inferior izquierda de lo que se "
                                      "corta", m=m)))
    if offset:
        kept = shift(kept, *offset)
        messages.append(Message(_("Dibujo movido {offset:g}, {offset2:g} mm respecto al cero", offset=offset[0], offset2=offset[1])))
    virtual_used = False
    if not stock and origin == "bbox_bottom_left":
        virtual = virtual_stock(kept, origin_margin)           # sin recuadro: la plancha se supone con el mismo margen
        if virtual is not None:
            stock, virtual_used = [virtual], True
            b = virtual.shape.bounds
            messages.append(Message(_("Tablero supuesto: {b:.1f} x {b2:.1f} mm (del cero a lo que se corta mas {origin_margin:g} mm); "
                                      "sirve para calcular la sujecion", b=b[2], b2=b[3], origin_margin=origin_margin)))
    return LayOut(kept, stock, degrees, virtual_used, machine_frame(original, kept, degrees, machine), messages)


def suggested_offset(side: str, amount: float) -> tuple[float, float]:
    """DX,DY que mete en el tablero una trayectoria que se sale `amount` mm por `side`, con holgura."""
    n = math.ceil(amount) + OVERHANG_SLACK
    return {"por la izquierda": (n, 0), "por abajo": (0, n), "por la derecha": (-n, 0), "por arriba": (0, -n)}[side]


def fmt_depth(spec) -> str:
    return "pasante" if spec.through else _("{depth:g} mm", depth=spec.depth)


# --- el trabajo -------------------------------------------------------------------------------------
@dataclass
class JobPlan:
    messages: list[Message] = field(default_factory=list)
    error: Optional[str] = None
    text: Optional[str] = None                   # G-code
    result: Optional[GcodeResult] = None
    job: Optional[Job] = None
    operations: list = field(default_factory=list)
    built: Optional[Built] = None
    degrees: float = 0.0
    shapes: list[ImportedShape] = field(default_factory=list)   # lo que se corta, girado y colocado (mm de maquina)
    stock: list[ImportedShape] = field(default_factory=list)    # el tablero, girado y colocado
    overhang: dict = field(default_factory=dict)
    virtual_stock: bool = False                  # el tablero no es un recuadro del dibujo: lo supone el programa
    frame: tuple = ()                            # las 4 esquinas del recorrido de la maquina sobre el dibujo (mm del SVG)
    blockers: list[str] = field(default_factory=list)   # por que NO se puede escribir este G-code (aunque la trayectoria se vea)
    alarms: list[str] = field(default_factory=list)     # se genera, pero cada una hay que confirmarla a mano
    settings: Optional["JobSettings"] = None
    _time: Optional[TimeEstimate] = field(default=None, init=False, repr=False, compare=False)

    @property
    def time(self) -> Optional[TimeEstimate]:
        """El tiempo de maquina estimado de este G-code (None si no hay). Es lento con trabajos de cientos de miles de
        lineas, asi que no se calcula con el plan: se calcula la primera vez que se pide y se guarda."""
        if self._time is None and self.text is not None and self.job is not None:
            self._time = estimate_for_machine(self.text, self.job.machine)
        return self._time

    @property
    def ok(self) -> bool:
        return self.error is None and self.text is not None

    @property
    def writable(self) -> bool:
        """Se puede escribir el .nc: hay G-code y ningun tope lo impide."""
        return self.ok and not self.blockers


def plan_job(shapes: Sequence[ImportedShape], stock: Sequence[ImportedShape], settings: JobSettings,
             recipe: Optional[Recipe] = None, board_number: Optional[int] = None) -> JobPlan:
    """Un trabajo = un G-code: coloca las formas, ordena los cortes y genera. No imprime ni escribe nada."""
    plan = JobPlan(settings=settings)
    msg = plan.messages
    tool = settings.tool
    kept, stock = list(shapes), list(stock)

    def fail(text):
        plan.error = text
        msg.append(Message(text, "error"))
        return plan

    problems = safety.settings_problems(settings.thickness, settings.margin)
    if problems:
        return fail(" ".join(problems))

    if recipe is not None:
        per_op, ignored, unmatched = classify(recipe, kept)
        if ignored:
            msg.append(Message(_("Ignoradas por la receta: {len} forma(s)", len=len(ignored))))
        if unmatched:
            colors = sorted({(s.stroke or s.fill or _("sin color")) for s in unmatched})
            msg.append(Message(_("{len} forma(s) sin operacion en la receta, no se cortan (colores: {join})", len=len(unmatched), join=', '.join(colors)), "warning"))
        kept = [s for group in per_op for s in group]          # solo lo que se corta decide giro y colocacion
        if not kept:
            return fail(_("la receta no encuentra ninguna forma que cortar"))
    elif not kept:
        return fail(_("no hay ninguna forma que cortar"))

    lay = lay_out(kept, stock, rotate_spec=settings.rotate, portrait=settings.portrait, tool=tool, machine=settings.machine,
                  boards_mode=board_number is not None, origin=settings.origin, origin_margin=settings.origin_margin,
                  offset=settings.offset)
    plan.degrees, plan.virtual_stock, plan.frame = lay.degrees, lay.virtual, lay.frame
    msg.extend(lay.messages)
    kept, stock = lay.kept, lay.stock
    plan.shapes, plan.stock = kept, stock

    depth = settings.thickness + settings.margin
    material = Stock(settings.thickness, spoilboard_extra=settings.margin,
                     max_spoilboard_extra=safety.MARGIN_MAX)
    notes: list[str] = []
    sides: list[str] = []
    if recipe is None:
        feed, plunge, stepdown = settings.feed, settings.plunge, settings.stepdown
        if feed is None or plunge is None:
            return fail(_("faltan el avance de corte y el de bajada (feed y entrada)"))
        stepdown = depth if stepdown is None else stepdown      # sin dato: una sola pasada
        try:
            sides = suggest_sides(kept)
            order = order_cuts([s.shape for s in kept], sides, tool.diameter / 2, settings.order)
        except ValueError as e:                                  # p. ej. una fresa mas gorda que un hueco
            return fail(str(e))
        ops = [ContourOp(kept[i].shape, side=sides[i], through=True, stepdown=stepdown, feed_xy=feed, feed_z=plunge,
                         tabs=tuple(kept[i].tabs or ()) if settings.bridges else ())   # (los puestos a mano valen aunque no haya receta)
               for i in order]
    else:
        try:
            plan.built = build_operations(kept, recipe, tool, material, feed=settings.feed, plunge=settings.plunge,
                                          stepdown=settings.stepdown, order=settings.order,
                                          tabs=settings.tabs, bridges_on=settings.bridges)
        except RecipeError as e:
            return fail(str(e))
        ops, notes = plan.built.operations, plan.built.notes
    plan.operations = ops
    try:
        step_problems = safety.operation_problems(ops, material, tool)
    except ValueError as e:
        return fail(str(e))
    if step_problems:
        more = _(" (y {v} operacion(es) mas)", v=len(step_problems) - 1) if len(step_problems) > 1 else ""
        return fail(step_problems[0] + more)
    job = Job(material, tool, ops, machine=settings.machine, safe_z=settings.safe_z,
              clearance_z=settings.clearance_z)
    plan.job = job
    try:
        result = generate_gcode(job, settings.post_options())
    except ValueError as e:
        return fail(str(e))
    plan.result, plan.text = result, result.text
    thickness_alarm = safety.thickness_alarm(settings.thickness)
    plan.alarms = [*([thickness_alarm] if thickness_alarm else []), *result.alarms]
    if thickness_alarm:                       # sin la lista de comprobacion es el unico sitio donde se lee
        msg.append(Message(thickness_alarm, "warning", severe=True))
    plan.blockers += [_("El G-code no pasa la verificacion: {p}", p=p) for p in safety.gcode_problems(result.text)]
    plan.blockers += safety.machine_problems(job.machine, depth=-result.min_z, safe_z=job.safe_z,
                                             home_at_end=settings.home_at_end, emit_spindle=settings.emit_spindle)

    src = settings.sources
    if recipe is None:
        tag = lambda key: f" ({src[key]})" if src.get(key) else ""
        how = (_("una sola pasada") if settings.stepdown is None else
               _("pasadas de hasta {stepdown:g} mm{tag}", stepdown=stepdown, tag=tag('stepdown')))
        msg.append(Message(_("Fresa: {tool_label} | feed {feed:g}{tag} | entrada {plunge:g}{tag2} | {how}", tool_label=tool_label(tool), feed=feed, tag=tag('feed'), plunge=plunge, tag2=tag('plunge'), how=how)))
    else:
        msg.append(Message(_("Fresa: {tool_label} | receta {source}", tool_label=tool_label(tool), source=recipe.source)
                           + (f" ({recipe.title})" if recipe.title else "")))
        for spec in recipe.operations:
            n = plan.built.counts.get(spec.name, 0)
            msg.append(Message(_("  {name}: {kind}, {n} forma(s), {fmt_depth}", name=spec.name, kind=spec.kind, n=n, fmt_depth=fmt_depth(spec)) if n else
                               _("  {name}: sin formas en este trabajo", name=spec.name)))
    if tool.rpm is not None:
        msg.append(Message(_("RPM aconsejadas (variador): {rpm:g}", rpm=tool.rpm)))
    by_hand = sum(1 for s in kept if s.tabs)
    if not settings.bridges and by_hand:
        notes = [*notes, _("Puentes desactivados: {count} forma(s) con puentes puestos a mano se cortan sin ellos.", count=by_hand)]
    if recipe is None:
        n_passes = max(1, math.ceil(depth / stepdown - 1e-9))
        counts = {k: sides.count(k) for k in SIDE_ORDER if sides.count(k)}
        msg.append(Message(_("Operaciones: {counts}; profundidad total {depth:g} mm en {n_passes} pasada(s)", counts=counts, depth=depth, n_passes=n_passes)))
    else:
        msg.append(Message(_("Operaciones generadas: {len}; profundidad maxima {v:.1f} mm", len=len(ops), v=-result.min_z)))
    bridged = [op for op in ops if isinstance(op, ContourOp) and op.tabs]
    if bridged:
        sizes = sorted({(t.width, t.height) for op in bridged for t in op.tabs})
        size = _("de {width:g} mm de ancho y {height:g} mm de alto", width=sizes[0][0], height=sizes[0][1]) if len(sizes) == 1 \
            else _("de varios tamaños")
        msg.append(Message(_("Puentes: {count} en {pieces} forma(s), {size}", count=sum(len(op.tabs) for op in bridged),
                             pieces=len(bridged), size=size)))
    for w in [*notes, *result.warnings]:
        msg.append(Message(w, "warning", severe=bool(result.excess_text) and w == result.excess_text))
    minx, miny, maxx, maxy = result.bounds
    for axis, size, travel in (("X", maxx - minx, job.machine.travel[0]), ("Y", maxy - miny, job.machine.travel[1])):
        room = travel - size
        if room < 0:
            plan.blockers.append(_("No cabe en la maquina: la trayectoria mide {size:.1f} mm en {axis} y el recorrido es de "
                                   "{travel:g} mm (faltan {v:.1f} mm).", size=size, axis=axis, travel=travel, v=-room))
        msg.append(Message(
            _("Trayectoria en {axis}: {size:.1f} mm de {travel:g} mm de recorrido -> ", axis=axis, size=size, travel=travel)
            + (_("margen total {room:.1f} mm", room=room) if room >= 0 else _("NO CABE (faltan {v:.1f} mm)", v=-room)), severe=room < 0))
    if stock and not plan.virtual_stock:             # el tablero supuesto es lo que se corta mas el margen: no hay de que avisar
        plan.overhang = overhang(result.bounds, stock)
        for side, v in plan.overhang.items():
            dx, dy = suggested_offset(side, v)
            flag = f"{dx:g},{dy:g}"
            plan.alarms.append(_("La trayectoria se sale del tablero {side} {v:.1f} mm: parte de la pieza queda fuera del "
                                 "material.", side=side, v=v))
            msg.append(Message(
                _("la trayectoria se sale del tablero {side} {v:.1f} mm: parte de una pieza queda fuera del "
                  "material. Mueve el dibujo hacia dentro con --offset DX,DY (por ejemplo --offset {flag}, que "
                  "deja unos {OVERHANG_SLACK} mm de holgura)", side=side, v=v, flag=flag, OVERHANG_SLACK=OVERHANG_SLACK), "warning", severe=True, code="overhang"))
    return plan
