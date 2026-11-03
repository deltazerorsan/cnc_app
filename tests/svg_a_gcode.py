"""Herramienta de pruebas (ya no forma parte del programa): de un SVG a G-code por linea de comandos.

Las pruebas la usan como atajo para generar un .nc y compararlo con G-code de referencia; el programa de verdad es la
ventana (cnc_gui.py / cnc_app.exe). Esta herramienta no se documenta ni se traduce.

De un SVG a G-code para la CNC (GRBL), con la fresa tomada de herramientas.json.

Cada vector cerrado se corta de forma pasante: los huecos (contornos interiores) primero y las
piezas (contornos exteriores) al final, para que ninguna pieza quede suelta antes de cortar sus
huecos. Los lados se sugieren por anidamiento. Dentro de cada grupo el orden por defecto es de
lejos a cerca (Y de mayor a menor), que es el habitual en taller.

Prioridad de los valores: linea de comandos > cnc_app.toml > valor por defecto. Los avances y el
paso de profundidad que no se indiquen aqui salen de la ficha de la fresa (herramientas.json).
"""
from __future__ import annotations

import argparse
import difflib
import math
import sys
from pathlib import Path

from cnc.boards import describe
from cnc import checklist as cl
from cnc.checklist import build_checklist
from cnc.config import CONFIG_NAME, ConfigError, find_config, read_config
from cnc.machinebook import MachineEditError, MachineEditor
from cnc.machines import (DEFAULT_MACHINES_FILE, MachinesFileError, choose_machine, load_machines, n, parse_log, travel_text,
                          understood)
from cnc.model import Tool
from cnc.pipeline import (OVERHANG_SLACK, JobSettings, app_dir, find_machines_file, find_tools_file,  # noqa: F401
                          load_drawing, plan_job)
from cnc.recipe import RecipeError, load_recipe
from cnc.tabs import TabError, TabSettings
from cnc.timing import describe as describe_time
from cnc.tools import DEFAULT_TOOLS_FILE, KINDS, ToolsFileError, get_tool, load_tools

# carpeta del programa: junto al .exe si va empaquetado con PyInstaller, si no junto a este archivo
SCRIPT_DIR = app_dir()

EPILOG = f"""\
ejemplos:
  python svg_a_gcode.py dibujo.svg --tool em4 --thickness 6 --margin 0.4 --stock-color "#2b2a29" -o salida.nc
      fresa em4 de herramientas.json (feed, entrada y paso salen de su ficha)
  python svg_a_gcode.py dibujo.svg --tool 4 --thickness 6 --feed 300 --plunge 200 -o salida.nc
      fresa sin ficha: un numero es el diametro en mm y el avance hay que indicarlo
  python svg_a_gcode.py --list-tools
      muestra las fresas de herramientas.json
  python svg_a_gcode.py --import-machine log_ugs.txt --machine taller
      crea (o actualiza) la ficha «taller» de maquinas.json con lo que UGS muestra al conectar ($$ y $I)
  python svg_a_gcode.py --list-machines
      muestra las fichas de maquina; cada trabajo usa la elegida con --machine (o la unica que haya)

valores por defecto:
  Se leen de {CONFIG_NAME} (en la carpeta actual o junto a este programa). Cada clave es el nombre de
  una opcion sin los guiones iniciales y con _ en vez de - (por ejemplo --safe-z se escribe safe_z).
  Lo que se escriba en la linea de comandos manda sobre el archivo; --no-config lo ignora.
  Las opciones que se pueden repetir (--exclude-color) se suman a las del archivo.
"""


class Ayuda(argparse.RawDescriptionHelpFormatter):
    """Como RawDescription, pero anade '(por defecto: X)' con el valor efectivo (el del archivo si lo hay)."""

    def _get_help_string(self, action):
        text = action.help or ""
        default = action.default
        if not action.option_strings or default is None or default is False or default == [] or default is argparse.SUPPRESS:
            return text
        return f"{text} (por defecto: {default})"


class Parser(argparse.ArgumentParser):
    """Errores de uso en una linea (sin volcar toda la sintaxis) y con la misma forma que los demas ERROR."""

    def error(self, message):
        self.exit(2, f"ERROR: {message}\n(python {self.prog} --help muestra todas las opciones)\n")


def _rotate_value(text: str):
    if text.strip().lower() == "auto":
        return "auto"
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"debe ser un numero de grados o 'auto' (hay {text!r})") from None
    if not math.isfinite(value):
        raise argparse.ArgumentTypeError(f"debe ser un numero de grados o 'auto' (hay {text!r})")
    return value


def _offset_value(text: str):
    try:
        dx, dy = (float(v) for v in text.replace(";", ",").split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(f"debe ser DX,DY en mm, por ejemplo 0,7 (hay {text!r})") from None
    if not (math.isfinite(dx) and math.isfinite(dy)):
        raise argparse.ArgumentTypeError(f"debe ser DX,DY en mm, por ejemplo 0,7 (hay {text!r})")
    return dx, dy


# --- textos de la linea de comandos (solo en español: ya no forman parte del programa) -------------------------------
def render(message) -> str:
    """Texto de linea de comandos: los avisos con 'AVISO:' y los errores con 'ERROR:'."""
    return {"info": "{}", "warning": "AVISO: {}", "error": "ERROR: {}"}[message.level].format(message.text)


def parse_board_selection(text, layout) -> list:
    """'all' / vacio = todos; '1,3' = esos. Error claro si no existe alguno."""
    if text is None or str(text).strip().lower() in ("", "all", "todos"):
        return list(layout.boards)
    wanted: list[int] = []
    for part in str(text).replace(" ", "").split(","):
        if not part.isdigit():
            raise ValueError(f"--board debe ser 'all' o una lista de numeros (1,3); hay {text!r}.")
        wanted.append(int(part))
    known = {b.number: b for b in layout.boards}
    missing = [n for n in wanted if n not in known]
    if missing:
        raise ValueError(f"No existe el tablero {', '.join(map(str, missing))}; hay {len(known)} (1 a {len(known)}). "
                         "--list-boards los muestra.")
    return [known[n] for n in dict.fromkeys(wanted)]


def _table(head: tuple, rows: list) -> list:
    widths = [max(len(str(r[i])) for r in [head, *rows]) for i in range(len(head))]
    fmt = lambda r: "  ".join(str(c).ljust(w) for c, w in zip(r, widths)).rstrip()
    return [fmt(head), fmt(tuple("-" * w for w in widths))] + [fmt(r) for r in rows]


def tools_table(tools: dict) -> str:
    """Tabla legible para --list-tools. '-' = sin dato."""
    head = ("id", "tipo", "diam", "flautas", "util", "rpm", "paso", "feed", "entrada", "stepover")

    def f(v, fmt="{:g}"):
        return "-" if v is None else fmt.format(v)

    kinds_back = {v: k for k, v in KINDS.items()}
    rows = []
    for ident, t in tools.items():
        kind = kinds_back[t.kind] + (f" {t.v_angle:g}" if t.v_angle else "")
        rows.append((ident, kind, f"{t.diameter:g}", f(t.flutes, "{}"), f(t.flute_length), f(t.rpm),
                     f(t.stepdown), f(t.feed_xy), f(t.feed_z), f(None if t.stepover is None else t.stepover * 100, "{:g} %")))
    return "\n".join(_table(head, rows) + ["", "diam, util y paso en mm; feed y entrada en mm/min; '-' = sin dato "
                                                "(se pide por linea de comandos)."])


def machines_table(machines: dict) -> str:
    """Tabla legible para --list-machines. '-' = sin dato."""
    head = ("id", "recorrido (mm)", "avance XY", "avance Z", "homing", "limites", "laser")

    def yes(v):
        return "-" if v is None else ("sí" if v else "no")

    def num(v):
        return "-" if v is None else n(v)

    rows = []
    for ident, m in machines.items():
        limits = "-" if m.soft_limits is None and m.hard_limits is None else (
            f"blandos {yes(m.soft_limits)}, duros {yes(m.hard_limits)}")
        rows.append((ident, " x ".join(n(v) for v in m.travel), num(m.max_feed), num(m.max_feed_z), yes(m.homing), limits,
                     yes(m.laser_mode)))
    return "\n".join(_table(head, rows) + ["", "avances en mm/min; '-' = sin dato (no se comprueba)."])


def build_parser() -> argparse.ArgumentParser:
    ap = Parser(
        prog="svg_a_gcode.py", description=__doc__, epilog=EPILOG, formatter_class=Ayuda, allow_abbrev=False
    )
    ap.add_argument("svg", nargs="?", help="archivo SVG a cortar")
    bool_opt = argparse.BooleanOptionalAction

    g = ap.add_argument_group("fresa y material")
    g.add_argument("--tool", metavar="FICHA|MM",
                   help="fresa: nombre de una ficha de herramientas.json (p. ej. em4) o un diametro en mm (p. ej. 4)")
    g.add_argument("--tools-file", metavar="ARCHIVO",
                   help=f"archivo de fresas; si no se indica, {DEFAULT_TOOLS_FILE} en la carpeta actual o junto a este programa")
    g.add_argument("--list-tools", action="store_true", help="mostrar las fresas disponibles y salir")
    g.add_argument("--machine", metavar="FICHA",
                   help="maquina para la que es el trabajo: una ficha de maquinas.json (sin indicar, la unica que haya)")
    g.add_argument("--machines-file", metavar="ARCHIVO",
                   help=f"archivo de maquinas; si no se indica, {DEFAULT_MACHINES_FILE} en la carpeta actual o junto a este programa")
    g.add_argument("--list-machines", action="store_true", help="mostrar las maquinas disponibles y salir")
    g.add_argument("--import-machine", metavar="LOG",
                   help="leer un log de UGS (el $$ y el $I al conectar) y guardarlo como ficha --machine FICHA en el "
                        "archivo de maquinas")
    g.add_argument("--update-machine", action="store_true",
                   help="con --import-machine y una ficha que ya existe: cambiar solo lo que el log trae (se deja una "
                        "copia .bak); sin esto, una ficha que existe no se toca")
    g.add_argument("--thickness", type=float, metavar="MM", help="grosor del material (mm)")
    g.add_argument("--margin", type=float, default=0.2, metavar="MM",
                   help="profundidad extra en el sufridor en los cortes pasantes (mm)")

    g = ap.add_argument_group("parametros de corte (si se indican, mandan sobre la ficha de la fresa)")
    g.add_argument("--feed", type=float, metavar="MM/MIN", help="avance de corte en XY")
    g.add_argument("--plunge", type=float, metavar="MM/MIN", help="avance de bajada (entrada) en Z")
    g.add_argument("--stepdown", type=float, metavar="MM",
                   help="profundidad maxima por pasada; sin indicar y sin dato en la ficha = una sola pasada")

    g = ap.add_argument_group("que se corta y como se coloca")
    g.add_argument("--exclude-color", action="append", default=[], metavar="#RRGGBB",
                   help="no cortar las formas con este color de trazo (se puede repetir)")
    g.add_argument("--boards", action=bool_opt, default=True,
                   help="convencion de tableros: un rectangulo que contiene formas es un tablero (un .nc, no se "
                        "corta, el cero en su esquina inferior izquierda) y una elipse que contiene tableros es una "
                        "agrupacion que se descarta; --no-boards trata todo como formas a cortar")
    g.add_argument("--board", metavar="N,N|all",
                   help="que tableros generar (numeros de --list-boards); por defecto todos, un .nc por tablero")
    g.add_argument("--list-boards", action="store_true", help="mostrar los tableros del SVG y salir")
    g.add_argument("--receta", metavar="ARCHIVO",
                   help="receta de trabajo (TOML): que operacion es cada color (corte, vaciado, taladro) con su "
                        "profundidad y avances; sin receta, todo lo cerrado se corta pasante (ver receta_ejemplo.toml)")
    g.add_argument("--offset", type=_offset_value, metavar="DX,DY",
                   help="mover el dibujo DX,DY mm respecto al cero despues de colocarlo (p. ej. 0,7 lo sube 7 mm)")
    g.add_argument("--stock-color", metavar="#RRGGBB",
                   help="color de trazo del contorno del tablero (alternativa a --boards): no se corta y el cero "
                        "queda en su esquina inferior izquierda (como es habitual)")
    g.add_argument("--portrait", action=bool_opt, default=False,
                   help="si el dibujo es apaisado, girarlo 90 grados antihorario para ponerlo vertical "
                        "(el giro que se haria a mano en una maquina vertical)")
    g.add_argument("--rotate", type=_rotate_value, metavar="GRADOS|auto",
                   help="girar el dibujo (antihorario); 'auto' lo gira 90 grados solo si no cabe en el recorrido de "
                        "la maquina y cabe girado (por defecto con tableros); manda sobre --portrait del archivo")
    g.add_argument("--origin", default="bbox_bottom_left", choices=["bbox_bottom_left", "center", "page_bottom_left"],
                   help="donde queda el cero si no hay --stock-color")
    g.add_argument("--origin-margin", type=float, default=4.0, metavar="MM",
                   help="sin tablero y con --origin bbox_bottom_left: el cero queda a estos mm, por fuera, de la esquina "
                        "inferior izquierda de lo que se corta (el borde estimado del material); 0 pone la esquina en el cero")
    g.add_argument("--tab-width", type=float, default=6.0, metavar="MM",
                   help="puentes de las operaciones de la receta con puentes = true: mm del puente que queda (la fresa "
                        "se levanta ese ancho mas su diametro)")
    g.add_argument("--tab-height", type=float, default=2.0, metavar="MM",
                   help="puentes: mm de material que queda sobre el fondo del tablero")
    g.add_argument("--tab-spacing", type=float, default=150.0, metavar="MM",
                   help="puentes: uno por cada tantos mm de perimetro de la pieza (minimo dos si caben)")
    g.add_argument("--order", default="far-to-near", choices=["far-to-near", "file"],
                   help="orden dentro de cada grupo: de lejos a cerca, el habitual en taller (huecos: al mas cercano; piezas: de arriba "
                        "abajo) o el del dibujo")
    g.add_argument("--ppi", type=float, default=96.0, metavar="PPI", help="pixeles por pulgada para SVG sin unidades")

    g = ap.add_argument_group("g-code de salida")
    g.add_argument("--safe-z", type=float, default=5.0, metavar="MM",
                   help="Z de desplazamiento libre sobre la superficie")
    g.add_argument("--clearance-z", type=float, default=1.0, metavar="MM",
                   help="altura hasta la que se baja en rapido antes de entrar a avance")
    g.add_argument("--no-spindle", action="store_true", help="no emitir M3/M5 (husillo manual)")
    g.add_argument("--pause-at-start", action=bool_opt, default=False,
                   help="M0 antes de arrancar el husillo; se reanuda desde UGS")
    g.add_argument("--home-at-end", action=bool_opt, default=False, help="Home ($H) al terminar")
    g.add_argument("--return-to-origin", action=bool_opt, default=False,
                   help="al terminar, volver a X0 Y0 (si no, se queda donde acaba)")
    g.add_argument("--end-code", metavar="TEXTO",
                   help="G-code propio que se anade al final (tras el Home, antes de M30); \\n separa lineas")
    g.add_argument("-o", "--out", default="salida.nc", metavar="ARCHIVO", help="archivo G-code de salida")

    g = ap.add_argument_group("configuracion")
    g.add_argument("--config", metavar="ARCHIVO",
                   help=f"archivo de valores por defecto (TOML); si no se indica, {CONFIG_NAME} en la carpeta actual o junto a este programa")
    g.add_argument("--no-config", action="store_true", help="ignorar el archivo de configuracion")
    return ap


_NOT_CONFIGURABLE = {"help", "config", "no_config", "list_tools", "list_machines", "import_machine", "update_machine", "list_boards", "out",
                     "offset"}


def _configurable(ap: argparse.ArgumentParser) -> dict:
    return {a.dest: a for a in ap._actions if a.option_strings and a.dest not in _NOT_CONFIGURABLE}


def _coerce(action: argparse.Action, key: str, value, source: str):
    where = f"{source}: '{key}'"
    if isinstance(action, (argparse._StoreTrueAction, argparse.BooleanOptionalAction)):
        if not isinstance(value, bool):
            raise ConfigError(f"{where} debe ser true o false (hay {value!r}).")
        return value
    if action.dest == "rotate":
        if isinstance(value, str) and value.strip().lower() == "auto":
            return "auto"
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{where} debe ser un numero de grados o \"auto\" (hay {value!r}).")
        return float(value)
    if action.dest == "board":
        if isinstance(value, bool) or not isinstance(value, (str, int, list)) or (
            isinstance(value, list) and not all(isinstance(v, int) and not isinstance(v, bool) for v in value)
        ):
            raise ConfigError(f"{where} debe ser \"all\", un numero de tablero o una lista de ellos (hay {value!r}).")
        return ",".join(map(str, value)) if isinstance(value, list) else str(value)
    if action.type is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{where} debe ser un numero (hay {value!r}).")
        return float(value)
    if action.dest == "exclude_color":
        values = [value] if isinstance(value, str) else value
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise ConfigError(f"{where} debe ser un texto o una lista de textos (hay {value!r}).")
        return list(values)
    if action.dest == "tool":
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ConfigError(f"{where} debe ser el nombre de una ficha o un diametro (hay {value!r}).")
        return str(value)
    if not isinstance(value, str):
        raise ConfigError(f"{where} debe ser texto (hay {value!r}).")
    if action.choices and value not in action.choices:
        raise ConfigError(f"{where} debe ser uno de {', '.join(action.choices)} (hay {value!r}).")
    return value


def config_defaults(ap: argparse.ArgumentParser, cfg: dict, source: str) -> dict:
    """Valida el contenido del archivo de configuracion y lo convierte en valores por defecto."""
    actions = _configurable(ap)
    out = {}
    for raw_key, value in cfg.items():
        key = str(raw_key).replace("-", "_")
        if key not in actions:
            close = difflib.get_close_matches(key, list(actions), n=1)
            hint = f" Quisiste decir '{close[0]}'?" if close else ""
            raise ConfigError(f"{source}: opcion desconocida '{raw_key}'.{hint} Opciones validas: {', '.join(actions)}.")
        out[key] = _coerce(actions[key], raw_key, value, source)
    return out


def _load_tools_or_exit(ap, explicit):
    path = find_tools_file(explicit)
    if path is None:
        ap.error(f"no encuentro {DEFAULT_TOOLS_FILE} (busque en {Path.cwd()} y en {SCRIPT_DIR}); "
                 "usa --tools-file ARCHIVO.")
    try:
        return path, load_tools(path)
    except ToolsFileError as e:
        ap.error(str(e))


def _machines_not_found(ap):
    ap.error(f"no encuentro {DEFAULT_MACHINES_FILE} (busque en {Path.cwd()} y en {SCRIPT_DIR}); usa --machines-file ARCHIVO, "
             "o crea la ficha de tu maquina con --import-machine LOG --machine NOMBRE.")


def _load_machines_or_exit(ap, explicit):
    path = find_machines_file(explicit)
    if path is None:
        _machines_not_found(ap)
    try:
        return path, load_machines(path)
    except MachinesFileError as e:
        ap.error(str(e))


def _import_machine(ap, a) -> int:
    """--import-machine: lee el log, ensena lo que entendio y lo que cambia, y escribe la ficha en el archivo."""
    log = Path(a.import_machine)
    try:
        text = log.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as e:
        ap.error(f"no puedo leer el log {log}: {e.strerror or e}")
    ident = (a.machine or "").strip()
    if not ident:
        ap.error("falta el nombre de la ficha: --import-machine LOG --machine NOMBRE (por ejemplo taller)")
    path = find_machines_file(a.machines_file) or Path.cwd() / DEFAULT_MACHINES_FILE
    try:
        editor = MachineEditor(load_machines(path) if path.is_file() else {})
        parsed = parse_log(text)
        print(f"Log {log.name}:")
        for line in understood(parsed):
            print(f"  {line}")
        known = [i for i, e in enumerate(editor.entries) if e.ident == ident]
        if known:
            changes = editor.refresh_from_log(known[0], text, source=f"log {log.name}")
            if not a.update_machine:
                print(f"\nLa ficha «{ident}» ya existe y no se toca." + (" Cambiaria:" if changes else
                                                                      " El log no cambia ningun dato."))
                for change in changes:
                    print(f"  {change}")
                if changes:
                    print("Si es lo que quieres, repite el comando con --update-machine.")
                return 0 if not changes else 1
            print(f"\nFicha «{ident}» actualizada." + ("" if changes else " No cambia ningun dato."))
            for change in changes:
                print(f"  {change}")
        else:
            editor.import_log(text, ident=ident, source=f"log {log.name}")
            print(f"\nFicha «{ident}» nueva: {travel_text(editor.entries[-1].machine)} de recorrido.")
        for note in parsed.notes:
            print(f"AVISO: {note}")
        editor.save(path)
    except (MachinesFileError, MachineEditError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"ERROR: no se pudo guardar {path}: {e.strerror or e}", file=sys.stderr)
        return 1
    print(f"Guardado en {path}")
    return 0


def _load_recipe_or_exit(ap, name):
    if not name:
        return None
    path = Path(name)
    if not path.is_file() and not path.is_absolute() and (SCRIPT_DIR / name).is_file():
        path = SCRIPT_DIR / name
    try:
        return load_recipe(path)
    except RecipeError as e:
        ap.error(str(e))


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = build_parser()

    # el archivo de configuracion se localiza antes de analizar el resto de opciones
    pre = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    pre.add_argument("--config")
    pre.add_argument("--no-config", action="store_true")
    pre_args, _ = pre.parse_known_args(argv)
    help_requested = any(x in ("-h", "--help") for x in argv)
    config_path = None
    if not pre_args.no_config:
        config_path = Path(pre_args.config) if pre_args.config else find_config(Path.cwd(), SCRIPT_DIR)
    if config_path is not None:
        try:
            ap.set_defaults(**config_defaults(ap, read_config(config_path), config_path.name))
        except ConfigError as e:
            if not help_requested:  # con --help se muestra la ayuda aunque el archivo tenga un fallo
                ap.error(str(e))
            config_path = None

    a = ap.parse_args(argv)

    if a.list_tools:
        path, tools = _load_tools_or_exit(ap, a.tools_file)
        print(f"Fresas de {path}:\n")
        print(tools_table(tools))
        return 0
    if a.list_machines:
        path, machines = _load_machines_or_exit(ap, a.machines_file)
        print(f"Maquinas de {path}:\n")
        print(machines_table(machines))
        return 0
    if a.import_machine:
        return _import_machine(ap, a)
    if not a.svg:
        ap.error("falta el archivo SVG (o usa --list-tools, --list-machines)")
    recipe = _load_recipe_or_exit(ap, a.receta)
    if a.list_boards:
        drawing = load_drawing(a.svg, ppi=a.ppi, exclude_colors=a.exclude_color)
        print(describe(drawing.layout, drawing.shapes, recipe))
        return 0
    if a.tool is None:
        ap.error("falta la fresa: --tool em4 (ficha de herramientas.json) o --tool 4 (diametro en mm); "
                 f"tambien puede ir en {CONFIG_NAME}")
    if a.thickness is None:
        ap.error("falta el grosor del material: --thickness 6")
    if not a.thickness > 0:
        ap.error("--thickness debe ser mayor que 0")
    if a.margin < 0:
        ap.error("--margin no puede ser negativo")
    if not a.origin_margin >= 0:
        ap.error("--origin-margin no puede ser negativo")
    if not 0 < a.clearance_z <= a.safe_z:
        ap.error("--clearance-z debe ser > 0 y no mayor que --safe-z")
    try:
        tabs = TabSettings(width=a.tab_width, height=a.tab_height, spacing=a.tab_spacing)
    except TabError as e:
        ap.error(f"puentes: {e}")
    if config_path is not None:
        print(f"Configuracion: {config_path}")

    # --- maquina: la elegida, o la unica ficha que haya; sin ficha no se genera nada ---
    machines_path, machines = _load_machines_or_exit(ap, a.machines_file)
    machine_id, why = choose_machine(machines, a.machine)
    if machine_id is None:
        ap.error(why + " Usa --machine FICHA.")
    machine = machines[machine_id]
    named = f" ({machine.name})" if machine.name and machine.name != machine_id else ""
    print(f"Maquina: {machine_id}{named}, recorrido {travel_text(machine)}")

    # --- fresa: un numero es un diametro; un nombre, una ficha de herramientas.json ---
    try:
        diameter = float(a.tool)
    except ValueError:
        diameter = None
    if diameter is not None:
        if not (math.isfinite(diameter) and diameter > 0):
            ap.error("el diametro de la fresa debe ser un numero mayor que 0")
        tool = Tool(diameter=diameter)
    else:
        _, tools = _load_tools_or_exit(ap, a.tools_file)
        try:
            tool = get_tool(tools, a.tool)
        except ToolsFileError as e:
            ap.error(str(e))

    def pick(cli, from_tool, what, option, field):
        if cli is not None:
            return cli, "linea de comandos"
        if from_tool is not None:
            return from_tool, "ficha"
        ap.error(f"falta {what}: usa {option}"
                 + (f" o rellena '{field}' en la ficha de la fresa" if diameter is None else ""))

    depth = a.thickness + a.margin
    if recipe is None:
        feed, feed_src = pick(a.feed, tool.feed_xy, "el avance de corte", "--feed MM/MIN", "feed")
        plunge, plunge_src = pick(a.plunge, tool.feed_z, "el avance de bajada", "--plunge MM/MIN", "entrada")
        for name, v in (("--feed", feed), ("--plunge", plunge)):
            if not v > 0:
                ap.error(f"{name} debe ser mayor que 0")
        if a.stepdown is not None or tool.stepdown is not None:
            stepdown, step_src = pick(a.stepdown, tool.stepdown, "la profundidad de pasada", "--stepdown MM", "profundidad_pasada")
            if not stepdown > 0:
                ap.error("--stepdown debe ser mayor que 0")
        else:
            stepdown, step_src = depth, None  # una sola pasada
    else:
        # con receta cada operacion trae lo suyo; lo que falte sale de la linea de comandos y de la ficha
        for name, v in (("--feed", a.feed), ("--plunge", a.plunge), ("--stepdown", a.stepdown)):
            if v is not None and not v > 0:
                ap.error(f"{name} debe ser mayor que 0")
        feed, feed_src = (a.feed, "linea de comandos") if a.feed is not None else (tool.feed_xy, "ficha")
        plunge, plunge_src = (a.plunge, "linea de comandos") if a.plunge is not None else (tool.feed_z, "ficha")
        stepdown, step_src = (a.stepdown, "linea de comandos") if a.stepdown is not None else (tool.stepdown, "ficha")

    drawing = load_drawing(a.svg, ppi=a.ppi, exclude_colors=a.exclude_color, stock_color=a.stock_color,
                           boards=a.boards)
    print(drawing.imported.summary(), "\n")
    for m in drawing.messages:
        print(render(m))
    if a.portrait and a.rotate is not None and "--portrait" in argv:
        ap.error("--portrait y --rotate son excluyentes")
    kept, stock, layout = drawing.shapes, drawing.stock, drawing.layout
    if a.board is not None and not layout.boards:
        ap.error("--board solo vale si el SVG tiene tableros (rectangulos que contengan formas)")

    settings = JobSettings(
        tool=tool, thickness=a.thickness, margin=a.margin, feed=feed, plunge=plunge,
        stepdown=None if step_src is None else stepdown, rotate=a.rotate, portrait=a.portrait, origin=a.origin,
        origin_margin=a.origin_margin, offset=a.offset, order=a.order, safe_z=a.safe_z, clearance_z=a.clearance_z, emit_spindle=not a.no_spindle,
        pause_at_start=a.pause_at_start, home_at_end=a.home_at_end, return_to_origin=a.return_to_origin,
        end_code=(a.end_code or "").replace("\\n", "\n"), machine=machine, tabs=tabs,
        sources={"feed": feed_src, "plunge": plunge_src, "stepdown": step_src})
    excluded = drawing.excluded
    excluded_note = f" (excluido el color {', '.join(sorted(excluded))})" if excluded else ""
    total = len(drawing.imported.shapes)
    if not layout.boards:
        print(f"Se cortan {len(kept)} de {total} formas{excluded_note}")
        return _run_job(settings, kept, stock, Path(a.out), recipe)

    try:
        selected = parse_board_selection(a.board, layout)
    except ValueError as e:
        ap.error(str(e))
    print(f"Formas: {len(kept)} utiles de {total}{excluded_note}")
    print(f"\nTableros: {len(layout.boards)}"
          + (f"; {len(layout.groupings)} elipse(s) de agrupacion descartada(s)" if layout.groupings else ""))
    if layout.loose:
        print(f"AVISO: {len(layout.loose)} forma(s) fuera de todo tablero: no se cortan.")
    out = Path(a.out)
    code = 0
    for board in selected:
        target = out if len(selected) == 1 else out.with_name(f"{out.stem}_tablero{board.number}{out.suffix}")
        print(f"\n=== Tablero {board.number} ({board.width:.0f} x {board.height:.0f} mm) ===")
        job_code = _run_job(settings, drawing.board_shapes(board), drawing.board_stock(board), target, recipe,
                            board_number=board.number)
        code = code or job_code
    return code


def _run_job(settings, shapes, stock, out_path, recipe=None, board_number=None) -> int:
    """Un trabajo = un .nc: lo planifica `cnc.pipeline.plan_job`; aqui se cuenta y se escribe el archivo."""
    plan = plan_job(shapes, stock, settings, recipe, board_number)
    for m in plan.messages:
        print(render(m), file=sys.stderr if m.level == "error" else sys.stdout)
    if not plan.ok:
        return 1
    if plan.blockers:                                    # no cabe, se sale del tablero...: no se escribe nada
        for why in plan.blockers:
            print(f"ERROR: {why}", file=sys.stderr)
        print(f"No se escribe {out_path}: corrige lo anterior.", file=sys.stderr)
        return 1
    out_path.write_text(plan.text, encoding="ascii")
    print(f"{out_path}: {len(plan.text.splitlines())} lineas, Z minima {plan.result.min_z:.3f} mm")
    print(f"Tiempo: {describe_time(plan.time)}")
    if cl.ENABLED:                                       # desconectada de momento: ver cnc/checklist.py
        print("Antes de ejecutarlo, comprueba:")
        for check in build_checklist([(board_number, plan)]).checks:
            print(("  ! " if check.alarm else "  - ") + check.text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
