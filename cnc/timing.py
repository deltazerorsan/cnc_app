"""Cuanto tarda la maquina en hacer un G-code: una estimacion del tiempo de maquina, sin Qt.

Solo cuenta lo que hace la maquina (cortes, bajadas, subidas, rapidos y las esperas `G4`); las pausas `M0` no suman,
porque ahi manda la persona (y no cuentan marcar, atornillar ni limpiar). Es una estimacion, nunca un dato para decidir
nada: no bloquea ni avisa de nada.

El modelo es el del planificador de GRBL 1.1, el mismo que ejecuta la maquina:

* Cada movimiento va a su avance, limitado por el avance maximo de cada eje (`$110`-`$112`).
* Cada eje acelera como mucho a lo que dice la ficha (`$120`-`$122`); en un movimiento oblicuo manda el eje mas exigente.
* En cada union entre dos movimientos seguidos GRBL no frena del todo: pasa la esquina a la velocidad que le deja la
  *desviacion de union* (`$11`, 0,010 mm de serie): una curva suave no frena casi nada, una esquina viva casi para y una
  vuelta atras (de ida y vuelta por el mismo sitio) para del todo.
* `M3`, `M5`, `G4`, `M0` y el final del programa sincronizan: la maquina se para antes de seguir.

El planificador real solo mira unos 15 movimientos por delante y aqui se mira todo el tramo; con movimientos cortos no
se nota. Contraste con la realidad: un dibujo grande (256.853 lineas, la mitad de los movimientos de menos de 0,1 mm), emulador de
UGS con los mismos `$11`, `$110`-`$112` y `$120`-`$122` que la maquina del taller: estimado 1 h 35 min 00 s, UGS 1:35:02.
Sin aceleraciones en la ficha, solo se puede dar el tiempo a velocidad constante, que es una cota baja. Lo que no entra: los
porcentajes de avance de UGS (override) y lo que tarde el husillo en llegar a regimen mas alla del `G4` que se escriba.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Optional, Sequence

from .verify_nc import code_of
from .i18n import _

JUNCTION_DEVIATION = 0.010           # mm ($11): el de serie de GRBL y el de la maquina del taller
DEFAULT_MAX_FEED = 3600.0            # mm/min, si la ficha no dice el avance maximo
_WORD = re.compile(r"([A-Za-z])\s*([-+]?\d*\.?\d+)")
_STOPS = {0, 2, 3, 5, 30}            # M que sincronizan: pausa, fin, husillo y fin del programa
_EPS = 1e-9


@dataclass(frozen=True)
class TimeEstimate:
    seconds: float                   # tiempo de maquina total (cortes + bajadas + rapidos + esperas); sin pausas M0
    cut_seconds: float = 0.0         # movimientos a avance en XY (corte)
    plunge_seconds: float = 0.0      # movimientos a avance solo en Z (bajadas)
    rapid_seconds: float = 0.0       # movimientos rapidos (G0)
    dwell_seconds: float = 0.0       # esperas G4
    flat_seconds: float = 0.0        # lo mismo a velocidad constante, sin aceleraciones (cota baja)
    pauses: int = 0                  # pausas M0: la persona decide cuanto duran y no suman
    blocks: int = 0                  # movimientos
    accelerations: bool = True       # False: la ficha no tiene aceleraciones y `seconds` es la cota baja
    notes: tuple = field(default=())  # lo que se ha supuesto o no se ha podido contar

    @property
    def minutes(self) -> float:
        return self.seconds / 60

    @property
    def text(self) -> str:
        return format_duration(self.seconds)


def format_duration(seconds: float) -> str:
    """«1 h 35 min», «12 min», «45 s»: al minuto, que es de lo que sirve una estimacion."""
    if seconds < 0 or math.isnan(seconds):
        raise ValueError(_("la duracion no puede ser negativa"))
    total = int(round(seconds))
    if total < 60:
        return _("{total} s", total=total)
    minutes = int(round(total / 60))
    hours, minutes = divmod(minutes, 60)
    if hours:
        return _("{hours} h {minutes:02d} min", hours=hours, minutes=minutes)
    return _("{minutes} min", minutes=minutes)


def _limit(vec: Sequence[float], per_axis: Sequence[float]) -> float:
    """Lo mas que puede valer algo con limite por eje (velocidad o aceleracion) a lo largo de `vec`, que va normalizado."""
    best = math.inf
    for i in (0, 1, 2):
        a = abs(vec[i])
        if a > 1e-12:
            v = per_axis[i] / a
            if v < best:
                best = v
    return best


def _words(code: str):
    """Pares (letra, valor) de una linea ya sin comentarios. Camino rapido para el G-code del programa (palabras separadas
    por espacios) y, si algo no encaja, la expresion regular (vale tambien `G1X10Y5`)."""
    try:
        return [(w[0].upper(), float(w[1:])) for w in code.split()]
    except ValueError:
        return [(m.group(1).upper(), float(m.group(2))) for m in _WORD.finditer(code)]


def _parse(text: str, max_xy: float, max_z: float):
    """Los movimientos del G-code, en tramos que acaban donde la maquina se para. Cada movimiento: (largo mm, vector
    unitario, velocidad nominal mm/s, categoria 'cut' | 'plunge' | 'rapid')."""
    rates = (max_xy / 60, max_xy / 60, max_z / 60)
    pos = [None, None, None]
    mode: Optional[int] = None
    feed: Optional[float] = None
    runs: list[list] = [[]]
    dwell = 0.0
    pauses = 0
    notes: set = set()
    for raw in text.splitlines():
        code = code_of(raw)
        if not code or code[0] == "$":
            continue
        words = _words(code)
        axes = None
        for letter, value in words:
            if letter == "G":
                g = int(value)
                if g in (0, 1):
                    mode = g
                elif g == 4:
                    p = next((v for l, v in words if l == "P"), None)
                    dwell += p if p is not None else 0.0
                    runs.append([])
                elif g in (2, 3):
                    notes.add(_("el G-code lleva arcos (G2/G3): se cuentan como rectas"))
                    mode = 1
                elif g in (20, 91):
                    notes.add(_("el G-code usa pulgadas o coordenadas relativas: la estimacion no vale"))
            elif letter == "M":
                m = int(value)
                if m == 0:
                    pauses += 1
                if m in _STOPS:
                    runs.append([])
            elif letter == "F":
                feed = value
            elif letter in "XYZ":
                if axes is None:
                    axes = {}
                axes[letter] = value
        if axes is None or mode is None:
            continue
        new = list(pos)
        for letter, value in axes.items():
            new["XYZ".index(letter)] = value
        if None not in pos:
            d = (new[0] - pos[0], new[1] - pos[1], new[2] - pos[2])
            length = math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2])
            if length > _EPS:
                u = (d[0] / length, d[1] / length, d[2] / length)
                top = _limit(u, rates)
                if mode == 0:
                    nominal, kind = top, "rapid"
                elif feed is None or feed <= 0:
                    notes.add(_("hay cortes sin avance (F) definido: no se cuentan"))
                    pos = new
                    continue
                else:
                    nominal = min(feed / 60, top)
                    kind = "cut" if (d[0] != 0 or d[1] != 0) else "plunge"
                runs[-1].append((length, u, nominal, kind))
        pos = new
    return runs, dwell, pauses, notes


def _run_times(blocks: list, accel: Sequence[float], junction: float) -> list:
    """El tiempo de cada movimiento de un tramo que arranca y acaba parado, con aceleraciones y desviacion de union."""
    n = len(blocks)
    acc = [_limit(b[1], accel) for b in blocks]
    # velocidad maxima (al cuadrado) con la que se puede entrar en cada movimiento: la de la union y la de cada avance
    entry = [0.0] * (n + 1)
    for i in range(1, n):
        prev, cur = blocks[i - 1], blocks[i]
        pu, u = prev[1], cur[1]
        cos = -(pu[0] * u[0] + pu[1] * u[1] + pu[2] * u[2])
        if cos >= 0.999999:                                  # vuelta atras: parada total
            v2 = 0.0
        elif cos <= -0.999999:                               # recta: sin limite
            v2 = math.inf
        else:
            jv = (u[0] - pu[0], u[1] - pu[1], u[2] - pu[2])
            norm = math.sqrt(jv[0] * jv[0] + jv[1] * jv[1] + jv[2] * jv[2])
            ja = _limit((jv[0] / norm, jv[1] / norm, jv[2] / norm), accel)
            s = math.sqrt(0.5 * (1.0 - cos))
            v2 = ja * junction * s / (1.0 - s)
        entry[i] = min(v2, prev[2] ** 2, cur[2] ** 2)
    # hacia atras: no entrar en un movimiento mas deprisa de lo que deja frenar a tiempo (el final del tramo es parada)
    exit2 = 0.0
    for i in range(n - 1, -1, -1):
        entry[i] = min(entry[i], exit2 + 2.0 * acc[i] * blocks[i][0])
        exit2 = entry[i]
    # hacia delante: ni salir mas deprisa de lo que deja acelerar el propio movimiento; y el tiempo de cada uno
    times = []
    e2 = 0.0
    for i in range(n):
        length, __, nominal, __ = blocks[i]
        a = acc[i]
        e2 = min(entry[i], e2)
        x2 = min(entry[i + 1], e2 + 2.0 * a * length)
        e, x, v2 = math.sqrt(e2), math.sqrt(x2), nominal * nominal
        up, down = (v2 - e2) / (2.0 * a), (v2 - x2) / (2.0 * a)
        if up + down <= length:                              # llega a su velocidad y va a ella un rato
            t = (nominal - e) / a + (nominal - x) / a + (length - up - down) / nominal
        else:                                                # no llega: acelera y frena sin crucero
            peak = math.sqrt((2.0 * a * length + e2 + x2) / 2.0)
            t = (peak - e) / a + (peak - x) / a
        times.append(t)
        e2 = x2
    return times


def estimate_time(text: str, *, accel: Optional[Sequence[float]] = None, max_feed: Optional[float] = None,
                  max_feed_z: Optional[float] = None, junction: float = JUNCTION_DEVIATION) -> TimeEstimate:
    """El tiempo de maquina de un G-code. `accel` son las aceleraciones X, Y, Z de la maquina en mm/s2 (`$120`-`$122`),
    `max_feed` y `max_feed_z` los avances maximos en mm/min (`$110`-`$112`). Sin `accel`, la cifra es a velocidad
    constante (cota baja) y `accelerations` es False."""
    notes = []
    if max_feed is None:
        max_feed = DEFAULT_MAX_FEED
        notes.append(_("la ficha no dice el avance maximo: se supone {DEFAULT_MAX_FEED:g} mm/min", DEFAULT_MAX_FEED=DEFAULT_MAX_FEED))
    runs, dwell, pauses, parse_notes = _parse(text, max_feed, max_feed_z if max_feed_z is not None else max_feed)
    notes.extend(sorted(parse_notes))
    use_accel = accel is not None and all(a > 0 for a in accel)
    if not use_accel:
        notes.append(_("la ficha no tiene aceleraciones: es el tiempo a velocidad constante, que se queda corto"))
    totals = {"cut": 0.0, "plunge": 0.0, "rapid": 0.0}
    flat = 0.0
    blocks = 0
    for run in runs:
        if not run:
            continue
        times = _run_times(run, accel, junction) if use_accel else [b[0] / b[2] for b in run]
        for b, t in zip(run, times):
            totals[b[3]] += t
            flat += b[0] / b[2]
        blocks += len(run)
    return TimeEstimate(
        seconds=sum(totals.values()) + dwell, cut_seconds=totals["cut"], plunge_seconds=totals["plunge"],
        rapid_seconds=totals["rapid"], dwell_seconds=dwell, flat_seconds=flat + dwell, pauses=pauses, blocks=blocks,
        accelerations=use_accel, notes=tuple(notes))


def estimate_for_machine(text: str, machine) -> TimeEstimate:
    """El tiempo de maquina con los datos de una ficha de maquina (`cnc.model.MachineProfile`)."""
    return estimate_time(text, accel=machine.accel, max_feed=machine.max_feed, max_feed_z=machine.max_feed_z)


def describe(est: TimeEstimate) -> str:
    """El tiempo en una frase para la lista de comprobacion y la linea de comandos: la cifra, lo que lleva cada parte y lo
    que se ha supuesto."""
    parts = []
    for label, seconds in (("corte", est.cut_seconds), ("bajadas", est.plunge_seconds), ("rapidos", est.rapid_seconds),
                           ("esperas", est.dwell_seconds)):
        if seconds >= 1:
            parts.append(f"{label} {format_duration(seconds)}")
    text = _("unos {text} de maquina", text=est.text) + (f" ({', '.join(parts)})" if parts else "")
    extras = ([_("sin contar {pauses} pausa(s) M0", pauses=est.pauses)] if est.pauses else []) + list(est.notes)
    return text + (" · " + " · ".join(extras) if extras else "")
