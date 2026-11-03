"""De un SVG a G-code: filtrar por color y asignar una operacion a cada vector.

Con ejemplo_placa.svg (o el SVG que indiques como argumento):
  negro (#000000) -> cortes pasantes: lo de fuera es la pieza y lo de dentro son
                     huecos (sugerencia por anidamiento, revisala siempre)
  azul  (#0000ff) -> vaciado de 6 mm de profundidad
  rojo y demas    -> notas o cotas: se descartan
Orden de ejecucion: huecos, vaciado y, al final, el contorno exterior.
"""
import sys
from pathlib import Path

from cnc.generate import generate_gcode
from cnc.model import ContourOp, Job, PocketOp, Stock, Tool
from cnc.svg_import import import_svg, place, suggest_sides

CUT, POCKET = "#000000", "#0000ff"

src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("ejemplo_placa.svg")
imp = import_svg(src)
print(imp.summary(), "\n")

kept = place([s for s in imp.shapes if s.stroke in (CUT, POCKET)], "bbox_bottom_left")
cuts = [s for s in kept if s.stroke == CUT]
pockets = [s for s in kept if s.stroke == POCKET]
sides = suggest_sides(cuts)

holes = [ContourOp(s.shape, side="inside", through=True, stepdown=3.0)
         for s, side in zip(cuts, sides) if side == "inside"]
outer = [ContourOp(s.shape, side="outside", through=True, stepdown=3.0)
         for s, side in zip(cuts, sides) if side == "outside"]
areas = [PocketOp(s.shape, depth=6.0, stepdown=3.0) for s in pockets]

job = Job(Stock(thickness=18.0, spoilboard_extra=0.2), Tool(diameter=6.0), holes + areas + outer)
result = generate_gcode(job)
for w in result.warnings:
    print("AVISO:", w)

out = Path(__file__).with_name("desde_svg.nc")
out.write_text(result.text, encoding="ascii")
print(f"{len(holes)} huecos, {len(areas)} vaciado(s), {len(outer)} contorno(s) exterior(es)")
print(f"{out.name}: {len(result.text.splitlines())} lineas, Z minima {result.min_z:.3f} mm")
