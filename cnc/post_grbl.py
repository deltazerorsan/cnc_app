"""Postprocesador GRBL 1.1: lista de Move -> texto G-code (solo ASCII).

GRBL no soporta ciclos fijos (G81-G83): todo se genera con G0/G1 explicitos.
El husillo del router es solo encendido/apagado: M3 con un S fijo y M5 al final.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Optional, Sequence

from .toolpath import Move
from .i18n import _


@dataclass
class PostOptions:
    wcs: Optional[str] = "G54"      # sistema de coordenadas de trabajo; None para no emitirlo
    emit_spindle: bool = True       # M3 S.. al inicio y M5 al final
    spindle_s: float = 20000.0      # S fijo: la maquina no interpreta revoluciones, solo on/off
    spinup_dwell: float = 2.0       # segundos de espera tras M3 (0 para no esperar)
    return_to_origin: bool = False  # al final, G0 X0 Y0 a Z segura; por defecto sube Z, para el husillo y se queda donde acaba
    pause_at_start: bool = False    # M0 antes de arrancar el husillo (costumbre de taller): reanudar en UGS
    home_at_end: bool = False       # al final, Home ($H); requiere homing activado ($22=1)
    end_code: str = ""              # G-code propio que se anade al final (tras el Home, antes de M30)
    decimals: int = 3


def _num(v: float, decimals: int = 3) -> str:
    s = f"{v:.{decimals}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _comment(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    clean = " ".join(folded.replace("(", "[").replace(")", "]").replace(";", ",").split())
    return f"({clean})"


def _end_code_lines(text: str) -> list[str]:
    """Lineas del codigo final: sin acentos (GRBL solo ASCII), sin lineas vacias."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return [ln.strip() for ln in folded.splitlines() if ln.strip()]


def to_gcode(
    moves: Sequence[Move],
    *,
    comments: Sequence[str],
    safe_z: float,
    opts: PostOptions,
) -> str:
    lines = [_comment(c) for c in comments]
    lines.append("G21 G90 G17 G94" + (f" {opts.wcs}" if opts.wcs else ""))

    z_safe = _num(safe_z, opts.decimals)
    lines.append(f"G0 Z{z_safe}")  # subir antes de arrancar el husillo
    if opts.pause_at_start:
        lines.append("M0")
    if opts.emit_spindle:
        lines.append(f"M3 S{_num(opts.spindle_s, 0)}")
        if opts.spinup_dwell > 0:
            lines.append(f"G4 P{_num(opts.spinup_dwell, 1)}")

    last: dict[str, Optional[str]] = {"X": None, "Y": None, "Z": z_safe}
    feed: Optional[float] = None
    for m in moves:
        words = []
        for axis, value in (("X", m.x), ("Y", m.y), ("Z", m.z)):
            if value is None:
                continue
            s = _num(value, opts.decimals)
            if s != last[axis]:
                words.append(f"{axis}{s}")
                last[axis] = s
        if not words:
            continue
        line = ["G0" if m.rapid else "G1", *words]
        if not m.rapid:
            if m.feed is not None and m.feed != feed:
                line.append(f"F{_num(m.feed, 1)}")
                feed = m.feed
            elif feed is None:
                raise ValueError(_("Movimiento de corte sin avance definido."))
        lines.append(" ".join(line))

    if opts.emit_spindle:
        lines.append("M5")
    if opts.home_at_end:
        # $H solo lo acepta GRBL en reposo: el G4 espera a que acabe el movimiento en cola
        lines += ["G4 P0.1", "$H"]
    elif opts.return_to_origin:
        lines.append("G0 X0 Y0")
    end_lines = _end_code_lines(opts.end_code)
    if end_lines:
        lines.append(_comment(_("Codigo final definido por el usuario")))
        lines += end_lines
    lines.append("M30")
    return "\n".join(lines) + "\n"
