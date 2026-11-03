"""Colocar, mover y cambiar de tamaño los puentes con el ratón, sin Qt.

Un puente se sitúa por su posición a lo largo del contorno de su forma (una fracción de su perímetro, ver `cnc.tabs`). Aquí
están las cuentas de lo que hace cada gesto: qué puente hay bajo el cursor, a dónde va uno que se arrastra, cómo cambia su ancho
al tirar de un extremo y cómo queda la lista de puentes de la forma. La capa de Qt (`cnc.gui.bridge_layer`) solo traduce el
ratón a estas llamadas y dibuja; todo lo que hay que acertar está aquí y tiene sus pruebas sin pantalla.
"""
from __future__ import annotations

import math
from dataclasses import replace
from typing import Optional, Sequence

from .i18n import _
from .tabs import MAX_TABS, Tab, along, locate, perimeter

MIN_WIDTH = 1.0            # mm: el puente más estrecho que se deja dibujar con el ratón
DECIMALS = 2               # los mm de un puente hecho con el ratón se redondean a la centésima


def wrap(delta: float) -> float:
    """Una diferencia de posiciones (fracciones del perímetro) llevada a [-0.5, 0.5): por el camino más corto."""
    return (delta + 0.5) % 1.0 - 0.5


def round_mm(value: float) -> float:
    return round(value, DECIMALS)


def pick_tab(tabs: Sequence[Tab], at: float, length: float, slack: float = 0.0) -> Optional[int]:
    """El puente que cubre la posición `at` (0 a 1) del contorno, o el que tiene más cerca a menos de `slack` mm de sus
    extremos. None si no hay ninguno."""
    best, best_gap = None, None
    for k, t in enumerate(tabs):
        distance = abs(wrap(at - t.at)) * length
        gap = distance - t.width / 2
        if gap <= slack and (best_gap is None or gap < best_gap):
            best, best_gap = k, gap
    return best


def end_of(tab: Tab, end: int, length: float) -> float:
    """La posición (fracción) de un extremo del puente: +1 el que está más adelante en el contorno, -1 el de atrás."""
    return (tab.at + end * tab.width / 2 / length) % 1.0


def near_end(tab: Tab, at: float, length: float, reach: float) -> Optional[int]:
    """-1 o +1 si la posición está a menos de `reach` mm de un extremo del puente (el más cercano); None si no."""
    found = None
    for end in (-1, 1):
        d = abs(wrap(at - end_of(tab, end, length))) * length
        if d <= reach and (found is None or d < found[0]):
            found = (d, end)
    return None if found is None else found[1]


def slid(tab: Tab, at: float) -> Tab:
    """El mismo puente con el centro en otra posición."""
    return replace(tab, at=at % 1.0)


def resized(tab: Tab, end: int, at: float, length: float, minimum: float = MIN_WIDTH) -> Tab:
    """El puente tras tirar de su extremo `end` (+1 o -1) hasta la posición `at`: el otro extremo no se mueve y el centro se
    desplaza con el ancho."""
    if end not in (-1, 1):
        raise ValueError(_("el extremo de un puente es -1 o +1."))
    fixed = tab.at * length - end * tab.width / 2
    reach = wrap(at - fixed / length) * length                  # con signo, desde el extremo fijo hasta el cursor
    width = min(max(end * reach, minimum), length * 0.9)
    centre = fixed + end * width / 2
    return Tab(at=(centre / length) % 1.0, width=round_mm(width), height=tab.height)


def added(tabs: Sequence[Tab], tab: Tab) -> tuple:
    """(lista, índice del nuevo) con el puente añadido, en orden de posición."""
    if len(tabs) >= MAX_TABS:
        raise ValueError(_("una forma admite a lo sumo {MAX_TABS} puentes.", MAX_TABS=MAX_TABS))
    out = sorted([*tabs, tab], key=lambda t: t.at)
    return tuple(out), out.index(tab)


def removed(tabs: Sequence[Tab], k: int) -> tuple:
    if not 0 <= k < len(tabs):
        raise ValueError(_("ese puente no existe."))
    return tuple(t for i, t in enumerate(tabs) if i != k)


def replaced(tabs: Sequence[Tab], k: int, tab: Tab) -> tuple:
    """(lista, índice del puente) con el puente `k` cambiado por `tab`."""
    if not 0 <= k < len(tabs):
        raise ValueError(_("ese puente no existe."))
    out = sorted([t for i, t in enumerate(tabs) if i != k] + [tab], key=lambda t: t.at)
    return tuple(out), out.index(tab)


def describe(shape, tab: Tab) -> dict:
    """Los números de un puente para la tira de puentes: posición (mm desde el primer vértice), ancho, alto, el largo del
    contorno y el punto central (X, Y)."""
    length = perimeter(shape)
    centre = along(shape, tab.at)
    return {"position": round_mm(tab.at * length), "width": tab.width, "height": tab.height, "length": round_mm(length),
            "x": centre[0] if centre else 0.0, "y": centre[1] if centre else 0.0}


def from_numbers(shape, position: float, width: float, height: float) -> Tab:
    """El puente que dan los números de la tira (posición en mm desde el primer vértice). ValueError si no valen."""
    length = perimeter(shape)
    for what, v in ((_("La posición"), position), (_("El ancho"), width), (_("El alto"), height)):
        if not math.isfinite(v):
            raise ValueError(_("{what} del puente tiene que ser un número.", what=what))
    if width <= 0 or height <= 0:
        raise ValueError(_("el ancho y el alto del puente tienen que ser mayores que cero."))
    if length <= 0 or width >= length:
        raise ValueError(_("un puente de {width:g} mm no cabe en un contorno de {length:g} mm.", width=width, length=round_mm(length)))
    return Tab(at=(position / length) % 1.0, width=width, height=height)


def place(shape, point: Sequence[float], width: float, height: float) -> Optional[tuple]:
    """(puente nuevo, distancia del punto al contorno en mm) con el centro en el punto del contorno más cercano a `point`."""
    found = locate(shape, point)
    if found is None:
        return None
    at, distance = found
    return Tab(at=at, width=width, height=height), distance
