"""Ejemplo con las tres operaciones sobre una placa de 120 x 80 mm.

Orden de ejecucion (el de la lista): taladros, vaciado con isla y, al final,
el contorno exterior (asi la pieza no se suelta hasta el ultimo corte).
"""
from pathlib import Path

from shapely.geometry import Polygon, box

from cnc.generate import generate_gcode
from cnc.model import ContourOp, DrillOp, Job, PocketOp, Stock, Tool

pocket = Polygon(box(25, 20, 95, 60).exterior, [box(50, 30, 70, 50).exterior])  # el hueco es la isla

job = Job(
    stock=Stock(thickness=18.0, spoilboard_extra=0.2),
    tool=Tool(diameter=6.0),
    operations=[
        DrillOp(points=[(10, 10), (110, 10), (110, 70), (10, 70)], peck=6.0),
        PocketOp(shape=pocket, depth=6.0, stepdown=3.0, stepover=0.4),
        ContourOp(shape=box(0, 0, 120, 80), side="outside", through=True, stepdown=3.0),
    ],
)

result = generate_gcode(job)
for w in result.warnings:
    print("AVISO:", w)

out = Path(__file__).with_name("trabajo_completo.nc")
out.write_text(result.text, encoding="ascii")
print(f"{out.name}: {len(result.text.splitlines())} lineas, Z minima {result.min_z:.3f} mm")
