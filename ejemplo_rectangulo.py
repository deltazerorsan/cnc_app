"""Ejemplo minimo: rectangulo 100 x 60 mm cortado por fuera, pasante."""
from pathlib import Path

from shapely.geometry import box

from cnc.generate import generate_gcode
from cnc.model import ContourOp, Job, Stock, Tool

job = Job(
    stock=Stock(thickness=18.0, spoilboard_extra=0.2),
    tool=Tool(diameter=6.0),
    operations=[
        ContourOp(
            shape=box(0, 0, 100, 60),  # esquina inferior izquierda = (0, 0)
            side="outside",
            through=True,
            stepdown=3.0,
            feed_xy=1200,
            feed_z=300,
        )
    ],
)

result = generate_gcode(job)
for w in result.warnings:
    print("AVISO:", w)

out = Path(__file__).with_name("rectangulo_exterior.nc")
out.write_text(result.text, encoding="ascii")
print(f"{out.name}: {len(result.text.splitlines())} lineas, Z minima {result.min_z:.3f} mm")
