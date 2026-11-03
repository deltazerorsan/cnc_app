"""Regresion con un SVG real exportado de una aplicacion de dibujo vectorial (doble placa, 1015,7 x 295,7 mm)."""
from collections import Counter
from pathlib import Path

import pytest

from cnc.generate import generate_gcode
from cnc.model import ContourOp, Job, Stock, Tool
from cnc.svg_import import import_svg, nesting_levels, place, rotate, suggest_sides

FILE = Path(__file__).parent / "data" / "placa_doble.svg"
BOARD = "#2b2a29"  # trazo grueso: parece el borde del tablero, no un corte


@pytest.fixture(scope="module")
def imp():
    return import_svg(FILE)


def cut_shapes(imp):
    return [s for s in imp.shapes if s.stroke != BOARD]


def job_from(shapes):
    ops = [ContourOp(s.shape, side=side, through=True, stepdown=3.0)
           for s, side in zip(shapes, suggest_sides(shapes))]
    return Job(Stock(thickness=18.0), Tool(diameter=6.0), ops)


def test_page_and_shapes(imp):
    assert imp.page_size == pytest.approx((1015.71, 295.706), abs=0.01)
    assert len(imp.shapes) == 15 and all(s.closed for s in imp.shapes)
    assert Counter(s.stroke for s in imp.shapes) == {"#1f1b20": 14, BOARD: 1}
    assert imp.ignored == {}          # <metadata> del exportador no cuenta como ignorado
    assert imp.warnings == []


def test_every_shape_is_valid_after_cleanup(imp):
    # dos formas acababan a 0,0002 mm de su inicio y shapely las daba por invalidas
    assert all(s.shape.is_valid for s in imp.shapes)
    areas = sorted(round(s.shape.area, 1) for s in imp.shapes if s.shape.area > 16000)
    assert areas == pytest.approx([16134.8, 16134.9, 30281.7, 30281.7, 299426.0], abs=0.2)


def test_nesting_without_the_board_outline(imp):
    cuts = cut_shapes(imp)
    assert len(cuts) == 14
    sides = Counter(suggest_sides(cuts))
    assert sides == {"outside": 10, "inside": 4}
    assert sorted(set(nesting_levels(cuts))) == [0, 1]


def test_job_needs_rotation_to_fit_the_machine(imp):
    cuts = cut_shapes(imp)
    straight = generate_gcode(job_from(place(cuts)))
    assert any("recorrido" in w for w in straight.warnings)          # 1000,7 mm no cabe en X (651)
    rotated = generate_gcode(job_from(place(rotate(cuts, 90))))
    assert not any("recorrido" in w for w in rotated.warnings)       # cabe: lado largo en Y (1011)
    assert rotated.min_z == pytest.approx(-18.2)
    assert len(rotated.text.splitlines()) > 10000


def test_acute_corners_trigger_reach_warning_with_6mm_only(imp):
    cuts = place(rotate(cut_shapes(imp), 90))
    six = generate_gcode(job_from(cuts))
    assert sum("no se pueden cortar" in w for w in six.warnings) == 2   # los dos triangulos interiores
    ops = job_from(cuts).operations
    three = generate_gcode(Job(Stock(thickness=18.0), Tool(diameter=3.0), ops))
    assert not any("no se pueden cortar" in w for w in three.warnings)


def test_real_layout_fits_a_6mm_tool_but_not_a_10mm_one(imp):
    cuts = place(rotate(cut_shapes(imp), 90))
    assert not any("no cabe entre ellas" in w for w in generate_gcode(job_from(cuts)).warnings)  # hueco minimo 8,9 mm
    ten = generate_gcode(Job(Stock(thickness=18.0), Tool(diameter=10.0), job_from(cuts).operations))
    assert any("no cabe entre ellas" in w for w in ten.warnings)
