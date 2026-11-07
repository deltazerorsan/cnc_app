"""Los avisos de espacio (esquinas que la fresa no alcanza, piezas mas juntas que la fresa) dicen donde estan: las marcas que
pinta la vista de maquina y las coordenadas del aviso. Con demasiados sitios no se marcan uno a uno: se avisa del exceso.
Ninguna de estas marcas toca el G-code."""
from pathlib import Path

import pytest
from shapely.geometry import Point, Polygon, box

import cnc.generate as gen
from cnc.generate import AlarmMark, MAX_MARKS, generate_gcode
from cnc.model import ContourOp, Job, Stock, Tool
from cnc.pipeline import JobSettings, plan_job
from cnc.svg_import import ImportedShape, import_svg, place, rotate, suggest_sides

DATA = Path(__file__).parent / "data"
EM4 = Tool(diameter=4, name="End mill 4 mm", feed_xy=300, feed_z=200, stepdown=5.0, rpm=10000)


def op(shape, side="outside"):
    return ContourOp(shape, side=side, through=True, stepdown=3.0)


def run(operations, diameter=4.0, thickness=6.0):
    return generate_gcode(Job(Stock(thickness=thickness), Tool(diameter=diameter), operations))


def grid(n=6, size=10.0, gap=1.0):
    """n x n cuadrados separados `gap` mm: con una fresa de 4 mm, cada uno esta demasiado cerca de sus vecinos."""
    pitch = size + gap
    return [box(x * pitch, y * pitch, x * pitch + size, y * pitch + size) for x in range(n) for y in range(n)]


GRID_PAIRS = 110        # 30 parejas en horizontal + 30 en vertical + 50 en diagonal (a 1,4 mm)


# --- los puntos agudos ------------------------------------------------------------------------------------------------
def test_a_sharp_corner_of_a_hole_is_marked_inside_the_hole_near_its_vertex():
    tri = Polygon([(0, 0), (60, 0), (60, 16)])                  # el vertice de 15 grados, como el hueco de una A
    r = run([op(tri, "inside")])
    assert len(r.marks) == 1 and r.excess == 0
    m = r.marks[0]
    assert m.kind == "reach" and m.line is None
    assert tri.contains(Point(m.x, m.y)) and Point(m.x, m.y).distance(Point(0, 0)) < 16     # en la punta, no en las otras esquinas


def test_a_sharp_notch_of_a_piece_is_marked_in_the_notch():
    wedge = Polygon([(0, 0), (60, 0), (60, 40), (32, 40), (30, 10), (28, 40), (0, 40)])     # la muesca entre las patas de una A
    r = run([op(wedge)], diameter=6.0)
    assert len(r.marks) == 1
    m = r.marks[0]
    assert m.x == pytest.approx(30.0, abs=1.5) and 10 < m.y < 40


def test_a_rectangle_and_a_tool_that_fits_have_no_marks():
    assert run([op(box(0, 0, 60, 40))], diameter=6.0).marks == []
    tri = Polygon([(0, 0), (60, 0), (60, 16)])
    assert run([op(tri, "inside")], diameter=1.0).marks == []


def test_the_real_drawing_marks_the_two_inner_triangles_with_a_6mm_tool_and_nothing_with_3mm():
    imp = import_svg(DATA / "placa_doble.svg")
    cuts = place(rotate([s for s in imp.shapes if s.stroke != "#2b2a29"], 90))
    ops = [op(s.shape, side) for s, side in zip(cuts, suggest_sides(cuts))]
    six = run(ops, 6.0, 18.0)
    assert [m.kind for m in six.marks] == ["reach", "reach"]
    assert [(round(m.x), round(m.y)) for m in six.marks] == [(189, 336), (83, 483)]
    assert run(ops, 3.0, 18.0).marks == []


# --- las piezas muy juntas --------------------------------------------------------------------------------------------
def test_two_pieces_too_close_are_marked_in_the_middle_of_their_gap_with_the_line_between_the_closest_points():
    r = run([op(box(0, 0, 20, 20)), op(box(22.5, 5, 42.5, 25))])
    assert len(r.marks) == 1
    m = r.marks[0]
    assert m.kind == "gap"
    (x1, y1), (x2, y2) = m.line
    assert (x1, x2) == (20.0, 22.5) and y1 == y2 and 5 <= y1 <= 20
    assert (m.x, m.y) == pytest.approx((21.25, y1))
    assert "2.5 mm" in m.text and f"X{m.x:.1f} Y{m.y:.1f}" in m.text


def test_pieces_that_fit_the_tool_between_them_are_not_marked():
    assert run([op(box(0, 0, 20, 20)), op(box(24.5, 5, 44.5, 25))]).marks == []


def test_the_real_drawing_marks_its_two_tight_gaps_with_a_10mm_tool():
    imp = import_svg(DATA / "placa_doble.svg")
    cuts = place(rotate([s for s in imp.shapes if s.stroke != "#2b2a29"], 90))
    r = run([op(s.shape, side) for s, side in zip(cuts, suggest_sides(cuts))], 10.0, 18.0)
    gaps = [m for m in r.marks if m.kind == "gap"]
    assert len(gaps) == 2 and all(m.line for m in gaps)
    assert sorted(round(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5, 1) for a, b in (m.line for m in gaps)) == [8.9, 9.4]


# --- el aviso de texto dice donde -------------------------------------------------------------------------------------
def test_the_alarm_texts_keep_their_words_and_add_the_place():
    tri = run([op(Polygon([(0, 0), (60, 0), (60, 16)]), "inside")])
    assert len(tri.alarms) == 1 and "no se pueden cortar" in tri.alarms[0]
    m = tri.marks[0]
    assert tri.alarms[0].endswith(f"Donde: X{m.x:.1f} Y{m.y:.1f}.")
    two = run([op(box(0, 0, 20, 20)), op(box(22.5, 5, 42.5, 25))])
    assert len(two.alarms) == 1 and "no cabe entre ellas" in two.alarms[0] and "Donde: X21.2 Y5.0." in two.alarms[0]
    assert two.alarms == [w for w in two.warnings if "no cabe entre ellas" in w]


def test_the_place_of_a_shape_with_many_problems_lists_three_and_counts_the_rest():
    text = gen._where([f"X{i}.0 Y0.0" for i in range(5)])
    assert text == "Donde: X0.0 Y0.0; X1.0 Y0.0; X2.0 Y0.0 y 2 mas."
    assert gen._where(["X1.0 Y2.0"]) == "Donde: X1.0 Y2.0."


# --- demasiados sitios: se avisa del exceso ---------------------------------------------------------------------------
def test_with_too_many_places_nothing_is_marked_and_one_alarm_warns_of_the_excess():
    r = run([op(b) for b in grid()])
    assert r.excess == GRID_PAIRS > MAX_MARKS and r.marks == []
    assert len(r.alarms) == 1 and r.alarms[0] == r.excess_text
    assert r.excess_text.startswith("DEMASIADAS ZONAS CON PROBLEMA") and "110 sitios" in r.excess_text and "4 mm" in r.excess_text
    assert f"{MAX_MARKS} que se senalan" in r.excess_text
    assert [w for w in r.warnings if "no cabe entre ellas" in w] == []        # los avisos de cada pareja no salen
    assert r.excess_text in r.warnings


def test_the_limit_is_exactly_max_marks(monkeypatch):
    ops = [op(b) for b in grid()]
    monkeypatch.setattr(gen, "MAX_MARKS", GRID_PAIRS)
    at_limit = run(ops)
    assert len(at_limit.marks) == GRID_PAIRS and at_limit.excess == 0 and len(at_limit.alarms) == GRID_PAIRS
    monkeypatch.setattr(gen, "MAX_MARKS", GRID_PAIRS - 1)
    over = run(ops)
    assert over.marks == [] and over.excess == GRID_PAIRS and len(over.alarms) == 1


def test_the_marks_do_not_change_the_gcode(monkeypatch):
    ops = [op(b) for b in grid(n=4)]                                         # 24 parejas: pasa del limite de 20
    many = run(ops)
    assert many.excess and not many.marks
    monkeypatch.setattr(gen, "MAX_MARKS", 1000)
    marked = run(ops)
    assert marked.marks and not marked.excess
    assert marked.text == many.text and marked.min_z == many.min_z and marked.bounds == many.bounds


def test_a_piece_inside_another_is_an_island_and_is_not_marked_as_a_gap():
    r = run([op(box(0, 0, 60, 60)), op(box(10, 10, 20, 20))])
    assert r.marks == [] and not [w for w in r.warnings if "no cabe entre ellas" in w]


def test_a_drawing_with_few_problems_keeps_one_alarm_per_problem():
    r = run([op(b) for b in grid(n=2)])                                      # 4 cuadrados: 6 parejas
    assert len(r.marks) == 6 == len(r.alarms) and r.excess == 0 and r.excess_text == ""


# --- el plan ----------------------------------------------------------------------------------------------------------
def shp(geom):
    return ImportedShape(shape=geom, closed=True, stroke="#ff0000", fill=None)


def plan(geoms):
    return plan_job([shp(g) for g in geoms], [], JobSettings(tool=EM4, thickness=19.0, margin=0.4, feed=300.0, plunge=200.0,
                                                                 stepdown=5.0))


def test_the_plan_carries_the_marks_and_nothing_blocks_generating_for_a_few_problems():
    p = plan([box(0, 0, 20, 20), box(22.5, 5, 42.5, 25)])
    assert p.ok and p.writable and p.blockers == []
    assert [m.kind for m in p.result.marks] == ["gap"] and len(p.alarms) == 1


def test_the_plan_shows_the_excess_as_a_severe_message_and_still_writes_the_gcode():
    p = plan(grid())
    assert p.ok and p.writable and p.result.excess == GRID_PAIRS and p.alarms == [p.result.excess_text]
    severe = [m for m in p.messages if m.severe]
    assert [m.text for m in severe] == [p.result.excess_text]
    assert not [m for m in p.messages if "no cabe entre ellas" in m.text]


def test_among_the_warnings_only_the_excess_is_severe():
    from cnc.tabs import Tab
    tabbed = ImportedShape(shape=box(300, 300, 330, 330), closed=True, stroke="#ff0000", fill=None, tabs=(Tab(0.25, 6, 2),))
    p = plan_job([*[shp(g) for g in grid()], tabbed], [], JobSettings(tool=EM4, thickness=19.0, margin=0.4, feed=300.0,
                                                                      plunge=200.0, stepdown=5.0, bridges=False))
    other = [m for m in p.messages if m.level == "warning" and m.text != p.result.excess_text]
    assert other and not any(m.severe for m in other)                # hay otros avisos y ninguno se resalta
    assert [m.text for m in p.messages if m.severe] == [p.result.excess_text]
