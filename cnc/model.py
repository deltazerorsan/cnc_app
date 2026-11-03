"""Modelo de datos del trabajo: maquina, material, herramienta y operaciones.

Convenciones (todo en mm):
  - Z = 0 en la cara superior del material; los cortes van en Z negativa.
  - El (0, 0) de XY es el origen del diseno; en UGS se marca el cero en ese
    mismo punto del material.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal, Optional, Union

from shapely.geometry.base import BaseGeometry


@dataclass(frozen=True)
class MachineProfile:
    """La ficha de una maquina: lo que dice su `$$` de GRBL. Solo el recorrido es imprescindible; lo que no se sabe
    queda en None y el programa no lo comprueba (ver `cnc.machines`, que lee y escribe `maquinas.json` y el log)."""

    name: str
    max_feed: Optional[float]            # mm/min en XY ($110, $111: el menor de los dos); None = sin dato
    travel: tuple[float, float, float]   # recorrido X, Y, Z en mm ($130-$132)
    max_feed_z: Optional[float] = None   # mm/min en Z ($112); None = sin dato
    accel: Optional[tuple[float, float, float]] = None   # mm/s2 en X, Y, Z ($120-$122); informativo
    soft_limits: Optional[bool] = None   # $20
    hard_limits: Optional[bool] = None   # $21
    homing: Optional[bool] = None        # $22: sin homing, $H da error
    spindle_max: Optional[float] = None  # $30 (informativo: el husillo de aqui es on/off)
    laser_mode: Optional[bool] = None    # $32: con el modo laser, M3 no arranca un husillo normal
    firmware: str = ""                   # p. ej. "Grbl 1.1h"
    source: str = ""                     # de donde salieron los datos ("log del 17/10/2026", "a mano"...)
    notes: str = ""


# Valores leidos del emulador de UGS: mismo procesador y firmware que la maquina real, con las dimensiones originales
# (la real quedo mas corta en Y), pero sin electronica: ni homing ni limites. Para el recorrido manda GRBL_MAQUINA_REAL.
GRBL_MKS_DLC32 = MachineProfile(
    name="GRBL 1.1 (MKS DLC32)",
    max_feed=3600.0,
    travel=(650.0, 1017.0, 100.0),
    soft_limits=False, hard_limits=False, homing=False,
)


# Valores leidos de la maquina real con $$ (UGS, COM5): homing y limites blandos/duros activados.
GRBL_MAQUINA_REAL = MachineProfile(
    name="Taller",
    max_feed=3600.0,
    travel=(651.0, 1011.0, 100.0),
    max_feed_z=3600.0,
    accel=(200.0, 200.0, 100.0),
    soft_limits=True, hard_limits=True, homing=True,
    spindle_max=10000.0, laser_mode=False,
    firmware="Grbl 1.1",
    source="$$ de la maquina real (UGS, COM5)",
)


@dataclass
class Stock:
    thickness: float                     # grosor de la pieza en bruto (mm)
    spoilboard_extra: float = 0.2        # cuanto entra en el sufridor en cortes pasantes (mm)
    max_spoilboard_extra: float = 1.0    # tope de seguridad para ese extra (mm)


ToolKind = Literal["endmill", "ball", "vbit"]

# Valores de respaldo si ni la operacion ni la fresa los indican (solo para uso como libreria;
# la linea de comandos los exige explicitamente).
DEFAULT_STEPDOWN = 2.0                   # mm
DEFAULT_FEED_XY = 1200.0                 # mm/min
DEFAULT_FEED_Z = 300.0                   # mm/min
DEFAULT_STEPOVER = 0.4                   # fraccion del diametro


@dataclass
class Tool:
    """Ficha de una fresa. Solo diameter es obligatorio; lo que falte se pide al usar la fresa.

    Los parametros de corte (stepdown, feed_xy, feed_z, stepover) son los que usan las operaciones
    que no indican el suyo propio. rpm es informativa: el husillo es on/off y el variador se
    ajusta a mano.
    """

    diameter: float                      # mm (en una V, el diametro maximo de corte)
    name: Optional[str] = None
    kind: ToolKind = "endmill"           # endmill | ball | vbit
    flutes: Optional[int] = None         # numero de filos (flautas / dientes)
    flute_length: Optional[float] = None # longitud util de corte (mm)
    v_angle: Optional[float] = None      # angulo incluido de la V (grados); solo kind="vbit"
    rpm: Optional[float] = None          # RPM aconsejadas (informativo)
    stepdown: Optional[float] = None     # profundidad de pasada (mm)
    feed_xy: Optional[float] = None      # avance de corte (mm/min)
    feed_z: Optional[float] = None       # avance de bajada / entrada (mm/min)
    stepover: Optional[float] = None     # separacion entre pasadas, fraccion del diametro (0-1]
    notes: str = ""


Side = Literal["outside", "inside", "on"]


@dataclass
class ContourOp:
    shape: BaseGeometry                  # Polygon cerrado, o LineString (solo con side="on")
    side: Side = "outside"               # outside: la forma es la pieza; inside: la forma es un hueco
    through: bool = True                 # True: profundidad = grosor + extra en el sufridor
    depth: Optional[float] = None        # mm desde la superficie (solo si through=False)
    stepdown: Optional[float] = None     # profundidad maxima por pasada (mm); None = la de la fresa
    feed_xy: Optional[float] = None      # mm/min; None = el de la fresa
    feed_z: Optional[float] = None       # mm/min (bajada); None = el de la fresa
    climb: bool = True                   # concordancia, con husillo en M3 (sentido horario)
    tabs: tuple = ()                     # puentes (cnc.tabs.Tab) que se dejan sin cortar; solo en cortes pasantes


@dataclass
class DrillOp:
    points: list[tuple[float, float]]    # centros de los taladros (x, y)
    through: bool = True                 # True: profundidad = grosor + extra en el sufridor
    depth: Optional[float] = None        # mm desde la superficie (solo si through=False)
    peck: Optional[float] = None         # profundidad por picoteo (mm); None = una sola bajada
    feed_z: Optional[float] = None       # mm/min (bajada); None = el de la fresa
    optimize_order: bool = True          # ordena los taladros por vecino mas cercano


@dataclass
class PocketOp:
    shape: BaseGeometry                  # Polygon; sus huecos son islas que se respetan
    through: bool = False                # normalmente un vaciado no es pasante
    depth: Optional[float] = None        # mm desde la superficie (si through=False)
    stepdown: Optional[float] = None     # profundidad maxima por nivel (mm); None = la de la fresa
    stepover: Optional[float] = None     # fraccion del diametro (0-1]; None = el de la fresa
    feed_xy: Optional[float] = None      # mm/min; None = el de la fresa
    feed_z: Optional[float] = None       # mm/min (bajada); None = el de la fresa
    climb: bool = True                   # sentido de la pasada de acabado de las paredes
    strategy: Literal["offset", "zigzag"] = "offset"  # offset: anillos paralelos al borde, de dentro afuera (sin
                                         # saltos); zigzag: lineas paralelas y despues el borde
    angle: float = 0.0                   # angulo de las pasadas de desbaste (solo zigzag; 0 = paralelas a X)


Operation = Union[ContourOp, DrillOp, PocketOp]

_FALLBACKS = {
    "stepdown": DEFAULT_STEPDOWN,
    "feed_xy": DEFAULT_FEED_XY,
    "feed_z": DEFAULT_FEED_Z,
    "stepover": DEFAULT_STEPOVER,
}


def resolved(op, tool: Optional[Tool] = None):
    """Copia de la operacion con stepdown, feeds y stepover concretos.

    Prioridad: valor de la operacion, luego el de la fresa, luego el de respaldo.
    Es idempotente: una operacion ya resuelta se devuelve tal cual.
    """
    changes = {}
    for name, fallback in _FALLBACKS.items():
        if hasattr(op, name) and getattr(op, name) is None:
            from_tool = getattr(tool, name, None) if tool is not None else None
            changes[name] = from_tool if from_tool is not None else fallback
    return replace(op, **changes) if changes else op


@dataclass
class Job:
    stock: Stock
    tool: Tool
    operations: list[Operation] = field(default_factory=list)
    machine: MachineProfile = GRBL_MAQUINA_REAL
    safe_z: float = 5.0                  # Z de desplazamiento libre (mm sobre la superficie)
    clearance_z: float = 1.0             # Z de aproximacion antes de bajar (mm sobre la superficie)
