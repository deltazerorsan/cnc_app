"""Verificador independiente de G-code GRBL (no usa el generador).

Lee un .nc, sigue el estado modal linea a linea y comprueba lo que realmente
contiene: codigos usados, avances, niveles de Z, husillo, desplazamientos
peligrosos, tiempos y limites. Sirve para certificar la salida, y la aplicacion
lo pasa por cada programa antes de dejar escribirlo (cnc.safety).

Tambien se puede usar suelto:  python verificar_nc.py salida.nc [--travel X Y Z] [--max-feed 3600]
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

WORD = re.compile(r"([A-Za-z])\s*(-?\d+\.?\d*|-?\.\d+)")
KNOWN_G = {0, 1, 4, 17, 21, 54, 90, 94}
KNOWN_M = {0, 2, 3, 5, 30}   # M0 pausa; M2/M30 fin de programa
KNOWN_LETTERS = set("GMXYZFSP")
EPS = 1e-6
_COMMENT = re.compile(r"\(.*?\)")
_NUMBER = r"-?(?:\d+\.?\d*|\.\d+)"
_PLAIN = re.compile(rf"[A-Z]{_NUMBER}(?: [A-Z]{_NUMBER})*")      # como escribe el programa: «G1 X10 Y5.5 F300»


def code_of(raw: str) -> str:
    """La linea sin comentarios `( )`, sin lo que sigue a `;` y sin espacios a los lados."""
    if "(" in raw:
        raw = _COMMENT.sub("", raw)
    return raw.split(";")[0].strip()


def _is_m3(code: str) -> bool:
    return any(l.upper() == "M" and int(float(v)) == 3 for l, v in WORD.findall(code))


@dataclass
class Report:
    lines: int = 0
    code_lines: int = 0
    system_cmds: list = field(default_factory=list)
    unknown: list = field(default_factory=list)          # codigos o palabras no reconocidos
    issues: list = field(default_factory=list)           # problemas (lo que hay que revisar)
    cut_feeds: set = field(default_factory=set)          # F usados en cortes XY
    plunge_feeds: set = field(default_factory=set)       # F usados en bajadas Z
    cut_z_levels: set = field(default_factory=set)       # Z de los cortes XY
    plunge_targets: set = field(default_factory=set)     # Z finales de las bajadas
    z_min: Optional[float] = None
    z_max: Optional[float] = None
    cut_segments: list = field(default_factory=list)     # ((x0, y0), (x1, y1)) de los cortes XY
    cut_length: float = 0.0
    cut_minutes: float = 0.0
    air_mm: float = 0.0                                  # recorrido a avance (G1) por encima de la superficie, en el aire
    air_seconds: float = 0.0
    rapid_minutes: float = 0.0
    dwell_seconds: float = 0.0
    bounds: Optional[tuple] = None                       # (minx, miny, maxx, maxy) de los cortes
    spindle: list = field(default_factory=list)          # (linea, 'M3 S..'|'M5', Z en ese momento)
    spindle_s: set = field(default_factory=set)
    ended: bool = False
    pauses: list = field(default_factory=list)           # lineas con M0 (pausa)
    contours: list = field(default_factory=list)         # puntos XY de cada pasada, de la bajada a la subida
    units_mm: bool = False
    absolute: bool = False
    wcs: Optional[int] = None

    @property
    def ok(self) -> bool:
        return not self.issues and not self.unknown

    @property
    def minutes(self) -> float:
        return self.cut_minutes + self.rapid_minutes + self.dwell_seconds / 60


def analyze(text: str, travel: Optional[tuple] = None, max_feed: float = 3600.0) -> Report:
    r = Report()
    # Un corte con el husillo apagado solo es un problema si el programa arranca el husillo en algun sitio (si no, se
    # maneja a mano). Eso no se sabe hasta el final: se apunta el primero, con su sitio entre los avisos, y se decide alli.
    has_m3 = False
    cut_off: Optional[tuple] = None      # (cuantos avisos habia, linea) del primer corte con el husillo apagado
    pos: dict = {"X": None, "Y": None, "Z": None}
    mode: Optional[int] = None
    feed: Optional[float] = None
    s_value: Optional[float] = None
    spindle_on = False
    moved = False
    warned: set = set()
    current: Optional[list] = None   # pasada en curso (desde que baja a cortar hasta que sube)

    def issue(key: str, msg: str) -> None:
        if key not in warned:  # un aviso por tipo, con la primera linea afectada
            warned.add(key)
            r.issues.append(msg)

    for n, raw in enumerate(text.splitlines(), start=1):
        r.lines += 1
        if len(raw) > 79:
            issue("long", f"linea {n}: {len(raw)} caracteres (GRBL estandar admite 79)")
        if not raw.isascii():
            issue("ascii", f"linea {n}: caracteres no ASCII")
        code = code_of(raw)
        if not code:
            continue
        r.code_lines += 1
        if code.startswith("$"):
            r.system_cmds.append(code)
            has_m3 = has_m3 or _is_m3(code)
            continue

        if _PLAIN.fullmatch(code):                       # el formato del propio programa: basta con partir por los espacios
            words = [(w[0], float(w[1:])) for w in code.split(" ")]
            leftover = ""
        else:
            words = [(m.group(1).upper(), float(m.group(2))) for m in WORD.finditer(code)]
            leftover = WORD.sub("", code).strip()
        if leftover:
            r.unknown.append(f"linea {n}: texto no reconocido {leftover!r}")
        g_words = [int(v) for l, v in words if l == "G"]
        m_words = [int(v) for l, v in words if l == "M"]
        r.unknown += [f"linea {n}: G{g} no esperado" for g in g_words if g not in KNOWN_G]
        r.unknown += [f"linea {n}: M{m} no esperado" for m in m_words if m not in KNOWN_M]
        r.unknown += [f"linea {n}: palabra {l!r} no esperada" for l, _ in words if l not in KNOWN_LETTERS]

        for g in g_words:
            if g in (0, 1):
                mode = g
            elif g == 21:
                r.units_mm = True
            elif g == 90:
                r.absolute = True
            elif g == 54:
                r.wcs = 54
            elif g == 4:
                p = [v for l, v in words if l == "P"]
                r.dwell_seconds += p[0] if p else 0.0
        for l, v in words:
            if l == "F":
                feed = v
            elif l == "S":
                s_value = v
        if 3 in m_words:
            has_m3 = True
            spindle_on = True
            if s_value is not None:
                r.spindle_s.add(s_value)
            r.spindle.append((n, f"M3 S{s_value:g}" if s_value is not None else "M3", pos["Z"]))
        if 5 in m_words:
            spindle_on = False
            r.spindle.append((n, "M5", pos["Z"]))
        if 30 in m_words or 2 in m_words:
            r.ended = True
        if 0 in m_words:
            r.pauses.append(n)

        axes = {l: v for l, v in words if l in "XYZ"}
        if not axes:
            continue
        if mode is None:
            issue("nomode", f"linea {n}: movimiento sin G0/G1 activo")
            continue
        if not (r.units_mm and r.absolute) and not moved:
            issue("preamble", f"linea {n}: primer movimiento sin G21 y G90 previos")
        moved = True
        new = dict(pos)
        new.update(axes)
        known = all(pos[a] is not None for a in "XYZ")
        dx = (new["X"] - pos["X"]) if known else 0.0
        dy = (new["Y"] - pos["Y"]) if known else 0.0
        dz = (new["Z"] - pos["Z"]) if known else 0.0
        xy = math.hypot(dx, dy)
        dist = math.sqrt(xy * xy + dz * dz)
        z_new = new["Z"]

        if mode == 1:
            if feed is None:
                issue("nofeed", f"linea {n}: G1 sin avance (F) definido")
            elif known:
                r.cut_minutes += dist / max(feed, 1e-9)
                hi, lo = max(pos["Z"], z_new), min(pos["Z"], z_new)
                frac = 1.0 if lo >= 0 else (0.0 if hi <= 0 else hi / (hi - lo))
                r.air_mm += dist * frac
                r.air_seconds += dist * frac / max(feed, 1e-9) * 60
            if known and xy > EPS:
                if current is not None:
                    current.append((new["X"], new["Y"]))
                r.cut_feeds.add(feed)
                r.cut_z_levels.add(round(z_new, 4))
                r.cut_length += xy
                r.cut_segments.append(((pos["X"], pos["Y"]), (new["X"], new["Y"])))
                xs, ys = (pos["X"], new["X"]), (pos["Y"], new["Y"])
                b = r.bounds or (min(xs), min(ys), max(xs), max(ys))
                r.bounds = (min(b[0], *xs), min(b[1], *ys), max(b[2], *xs), max(b[3], *ys))
                if z_new > EPS:
                    issue("air", f"linea {n}: corte en XY por encima de la superficie (Z={z_new:g})")
                if not spindle_on and cut_off is None:
                    cut_off = (len(r.issues), n)
                if abs(dz) > EPS:
                    issue("ramp", f"linea {n}: corte con Z variable (rampa), Z de {pos['Z']:g} a {z_new:g}")
            elif known and abs(dz) > EPS:
                r.plunge_feeds.add(feed)
                if dz < 0:
                    r.plunge_targets.add(round(z_new, 4))
                    current = [(pos["X"], pos["Y"])]
        else:
            if known:
                r.rapid_minutes += dist / max_feed
            if known and xy > EPS and (z_new < -EPS or pos["Z"] < -EPS):
                issue("rapid", f"linea {n}: G0 con desplazamiento XY por debajo de la superficie (Z={z_new:g})")

        if current is not None and dz > EPS:  # subida: cierra la pasada
            if len(current) > 1:
                r.contours.append(current)
            current = None
        pos = new
        r.z_min = z_new if r.z_min is None else min(r.z_min, z_new)
        r.z_max = z_new if r.z_max is None else max(r.z_max, z_new)

    if has_m3 and cut_off is not None:
        r.issues.insert(cut_off[0], f"linea {cut_off[1]}: corte con el husillo apagado")
    if not r.units_mm:
        issue("units", "no hay G21 (milimetros)")
    if not r.absolute:
        issue("abs", "no hay G90 (coordenadas absolutas)")
    if has_m3:
        first_on = next((z for _, c, z in r.spindle if c.startswith("M3")), None)
        if first_on is not None and first_on <= EPS:  # con Z desconocida (antes del primer movimiento) no se juzga
            issue("m3z", "el husillo arranca con la fresa en la superficie o por debajo (Z<=0)")
        if spindle_on:
            issue("m5", "el husillo no se apaga al final (falta M5)")
    if not r.ended:
        issue("m30", "falta M30 (o M2) al final")
    if travel and r.bounds:
        w, h = r.bounds[2] - r.bounds[0], r.bounds[3] - r.bounds[1]
        if w > travel[0] or h > travel[1]:
            issue("travel", f"los cortes ocupan {w:.1f} x {h:.1f} mm y el recorrido es {travel[0]:g} x {travel[1]:g} mm")
        if r.z_min is not None and len(travel) > 2 and -r.z_min > travel[2]:
            issue("travelz", f"Z minima {r.z_min:g} supera el recorrido en Z ({travel[2]:g} mm)")
    return r


def format_report(r: Report, name: str = "") -> str:
    def lst(values) -> str:
        return ", ".join(f"{v:g}" for v in sorted(values, reverse=True)) or "ninguno"

    out = [f"Archivo: {name}  ({r.lines} lineas, {r.code_lines} con codigo)"]
    out.append(f"Unidades y modo: {'G21 mm' if r.units_mm else 'SIN G21'}, {'G90 absoluto' if r.absolute else 'SIN G90'}"
               + (f", G{r.wcs}" if r.wcs else ""))
    if r.spindle:
        out.append("Husillo: " + "; ".join(f"{c} (linea {n}, Z={z:g})" if z is not None else f"{c} (linea {n})"
                                           for n, c, z in r.spindle))
    else:
        out.append("Husillo: sin M3/M5 (se maneja a mano)")
    out.append(f"Avance de corte XY (F): {lst(r.cut_feeds)} | de bajada Z: {lst(r.plunge_feeds)} mm/min")
    out.append(f"Niveles Z de corte: {lst(r.cut_z_levels)} | bajadas hasta: {lst(r.plunge_targets)}")
    if r.z_min is not None:
        out.append(f"Z minima: {r.z_min:g} | Z maxima: {r.z_max:g}")
    if r.bounds:
        b = r.bounds
        out.append(f"XY de los cortes: X[{b[0]:.2f}, {b[2]:.2f}] Y[{b[1]:.2f}, {b[3]:.2f}]  ->  {b[2]-b[0]:.1f} x {b[3]-b[1]:.1f} mm")
    out.append(f"Cortes: {len(r.cut_segments)} segmentos, {r.cut_length:.0f} mm de recorrido")
    out.append(f"A avance en el aire (Z>0): {r.air_mm:.0f} mm, ~{r.air_seconds:.0f} s (se puede hacer en G0)")
    out.append(f"Tiempo estimado sin aceleraciones: {r.minutes:.1f} min "
               f"(corte {r.cut_minutes:.1f}, rapidos {r.rapid_minutes:.1f}, esperas {r.dwell_seconds:g} s)")
    if r.pauses:
        out.append("Pausas (M0) en las lineas: " + ", ".join(str(n) for n in r.pauses))
    if r.system_cmds:
        out.append("Comandos de sistema: " + ", ".join(r.system_cmds))
    out.append("No reconocido: " + ("ninguno" if not r.unknown else "; ".join(r.unknown[:5])))
    out.append("PROBLEMAS: " + ("ninguno" if not r.issues else ""))
    out += [f"  - {m}" for m in r.issues]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Verifica un G-code GRBL de forma independiente del generador.")
    ap.add_argument("archivo")
    ap.add_argument("--travel", type=float, nargs="+", metavar=("X", "Y"), help="recorrido X Y [Z] en mm")
    ap.add_argument("--max-feed", type=float, default=3600.0, help="avance de los G0 (mm/min), por defecto 3600")
    ap.add_argument("--machine", metavar="FICHA",
                    help="tomar el recorrido y el avance de una ficha de maquinas.json (manda sobre --travel y --max-feed)")
    ap.add_argument("--machines-file", metavar="ARCHIVO", help="archivo de maquinas (por defecto maquinas.json junto al programa)")
    a = ap.parse_args(argv)
    travel, max_feed = a.travel, a.max_feed
    if a.machine:
        from .machines import MachinesFileError, get_machine, load_machines
        from .pipeline import find_machines_file
        path = find_machines_file(a.machines_file)
        if path is None:
            ap.error("no encuentro maquinas.json; usa --machines-file ARCHIVO")
        try:
            machine = get_machine(load_machines(path), a.machine)
        except MachinesFileError as e:
            ap.error(str(e))
        travel = list(machine.travel)
        max_feed = machine.max_feed or max_feed
    rep = analyze(Path(a.archivo).read_text(encoding="ascii", errors="replace"), travel, max_feed)
    print(format_report(rep, Path(a.archivo).name))
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
