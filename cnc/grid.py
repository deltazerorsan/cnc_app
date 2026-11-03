"""La rejilla del lienzo, sin Qt: de que paso es, que lineas salen y cuales llevan numero.

El lienzo solo las dibuja. Aqui esta lo que hay que acertar: que el paso se adapte al zoom sin que las lineas se
amontonen, que con un paso fijo (la cuadricula del raton) las lineas caigan siempre en sus multiplos, y que nunca se
pidan miles de lineas de golpe.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence
from .i18n import _

MIN_PIXELS = 14.0       # separacion minima, en pantalla, entre dos lineas finas
LABEL_PIXELS = 52.0     # separacion minima, en pantalla, entre dos lineas con numero
MAX_LINES = 600         # por eje; con mas no se dibuja nada antes que colgar la ventana
NICE = (1.0, 2.0, 5.0)  # la serie de los pasos "redondos": ... 0,5 1 2 5 10 20 50 ...
RATIOS = (2, 5, 10)     # cada cuantas lineas finas hay una con numero


def is_nice(step: float) -> bool:
    """Cierto si el paso es de la serie 1-2-5 (por una potencia de diez)."""
    if not step > 0:
        return False
    mantissa = step / 10 ** math.floor(math.log10(step))
    return any(abs(mantissa - n) < 1e-9 for n in NICE) or abs(mantissa - 10) < 1e-9


def nice_step(minimum: float) -> float:
    """El menor paso de la serie 1-2-5 que es al menos `minimum` mm."""
    if not minimum > 0 or not math.isfinite(minimum):
        raise ValueError(_("el paso minimo tiene que ser un numero positivo."))
    k = math.floor(math.log10(minimum))
    for e in (k, k + 1):
        for n in NICE:
            step = n * 10.0 ** e
            if step >= minimum * (1 - 1e-12):
                return round(step, 9)
    return round(10.0 ** (k + 1), 9)           # no se llega: la serie siempre da con un paso en k o k+1


def step_for(pixels_per_mm: float, fixed: float = 0.0) -> float:
    """El paso (mm) de las lineas finas con este zoom. Con `fixed` (> 0) el paso es un multiplo de ese, el menor que
    deja las lineas a MIN_PIXELS o mas: asi todas caen donde caeria la cuadricula de verdad."""
    if not pixels_per_mm > 0:
        raise ValueError(_("el zoom tiene que ser positivo."))
    need = MIN_PIXELS / pixels_per_mm
    if fixed > 0:
        return round(fixed * max(1, math.ceil(need / fixed - 1e-9)), 9)
    return nice_step(need)


def label_ratio(step: float, pixels_per_mm: float) -> int:
    """Cada cuantas lineas finas hay una con numero: la menor razon (2, 5 o 10) que las deja a LABEL_PIXELS o mas y, si el
    paso es redondo, da otro paso redondo (de 5 mm en 5 mm, de 10 en 10, nunca de 15 en 15)."""
    for r in RATIOS:
        if step * r * pixels_per_mm >= LABEL_PIXELS and (not is_nice(step) or is_nice(step * r)):
            return r
    return RATIOS[-1]


def decimals(step: float) -> int:
    """Cuantos decimales hacen falta para escribir las posiciones de un paso: 1 mm -> 0, 0,5 mm -> 1, 0,25 mm -> 2."""
    for d in range(0, 7):
        if abs(step * 10 ** d - round(step * 10 ** d)) < 1e-6:
            return d
    return 6


def label(value: float, step: float) -> str:
    text = f"{value:.{decimals(step)}f}"
    return "0" if float(text) == 0 else text


@dataclass(frozen=True)
class Grid:
    step: float
    ratio: int
    x_fine: tuple
    x_label: tuple
    y_fine: tuple
    y_label: tuple

    @property
    def count(self) -> int:
        return len(self.x_fine) + len(self.x_label) + len(self.y_fine) + len(self.y_label)


def _positions(lo: float, hi: float, step: float, ratio: int) -> tuple:
    """(finas, con numero): las posiciones i*step dentro de [lo, hi]; con numero las i multiplo de `ratio`."""
    first, last = math.ceil(lo / step - 1e-9), math.floor(hi / step + 1e-9)
    fine, label_ = [], []
    for i in range(first, last + 1):
        (label_ if i % ratio == 0 else fine).append(round(i * step, 9))
    return tuple(fine), tuple(label_)


def grid_for(view: Sequence[float], pixels_per_mm: float, fixed: float = 0.0) -> Grid:
    """Las lineas de la rejilla que caen en `view` = (x0, y0, x1, y1) en mm, con este zoom (pixeles por mm). Si saldrian
    mas de MAX_LINES por eje, no sale ninguna (un Grid vacio)."""
    x0, y0, x1, y1 = view
    if not (x1 >= x0 and y1 >= y0 and all(math.isfinite(v) for v in view)):
        raise ValueError(_("la zona a rayar no es un rectangulo valido."))
    step = step_for(pixels_per_mm, fixed)
    ratio = label_ratio(step, pixels_per_mm)
    if (x1 - x0) / step > MAX_LINES or (y1 - y0) / step > MAX_LINES:
        return Grid(step, ratio, (), (), (), ())
    xf, xl = _positions(x0, x1, step, ratio)
    yf, yl = _positions(y0, y1, step, ratio)
    return Grid(step, ratio, xf, xl, yf, yl)
