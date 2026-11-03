"""Medidas de lo elegido (cnc.drawing_edit.measures_of / «medidas», sin Qt): ver la caja de una forma y ponerla en otro
sitio o con otro tamano. Un circulo y un rectangulo con esquinas redondas se rehacen exactos; el resto se estira."""
import json
import math
from dataclasses import replace

import pytest
from shapely import affinity
from shapely.geometry import Polygon

from cnc import drawing_edit as de
from cnc.drawing_edit import (EditError, apply_edit, make_circle, make_ellipse, make_polygon, make_polyline,
                              make_rectangle, make_slot, measures_edit, measures_of)
from cnc.model import Tool
from cnc.project import General, Project, ProjectError
from cnc.svg_import import ImportedShape

K = "#000000"


def rect(x=10, y=10, w=50, h=30, r=0.0):
    return make_rectangle(x, y, w, h, r, stroke=K)


def circle(cx=120, cy=50, r=10):
    return make_circle(cx, cy, r, stroke=K)


def box(s):
    return s.shape.bounds


def do(shapes, indices, x, y, w, h):
    return apply_edit(shapes, measures_edit(indices, x, y, w, h))


# --- leer las medidas ----------------------------------------------------------------------------------------------
def test_the_measures_of_a_rectangle_are_its_corner_and_its_size():
    m = measures_of([rect(4, 5, 100, 250)], [0])
    assert (m.kind, m.count, m.x, m.y, m.width, m.height) == ("rect", 1, 4, 5, 100, 250)
    assert (m.cx, m.cy) == (54, 130)


def test_a_circle_is_told_apart_and_its_centre_is_the_centre_of_its_box():
    m = measures_of([circle(120, 50, 10)], [0])
    assert m.kind == "circle" and (m.cx, m.cy, m.width, m.height) == pytest.approx((120, 50, 20, 20))


def test_an_ellipse_is_not_a_circle():
    assert measures_of([make_ellipse(0, 0, 20, 10, stroke=K)], [0]).kind == "ellipse"
    assert measures_of([make_ellipse(0, 0, 10, 10, stroke=K)], [0]).kind == "ellipse"          # lo que dice la forma manda


def test_a_circle_that_is_two_percent_off_still_counts_but_not_more():
    s = circle(0, 0, 10)
    a = replace(s, shape=affinity.scale(s.shape, xfact=1.019, yfact=1.0))
    b = replace(s, shape=affinity.scale(s.shape, xfact=1.05, yfact=1.0))
    assert measures_of([a], [0]).kind == "circle"
    assert measures_of([b], [0]).kind == "ellipse"                               # un circulo muy estirado ya es una elipse


def test_several_shapes_are_measured_as_one_box():
    m = measures_of([rect(0, 0, 10, 10), circle(50, 40, 5), rect(200, 200, 1, 1)], [0, 1])
    assert m.kind == "varias" and m.count == 2 and (m.x, m.y) == (0, 0) and (m.width, m.height) == pytest.approx((55, 45))


def test_nothing_chosen_or_a_stale_index_gives_none():
    shapes = [rect()]
    assert measures_of(shapes, []) is None and measures_of(shapes, [5]) is None and measures_of([], [0]) is None
    assert measures_of(shapes, [5, 0]).count == 1


def test_a_line_has_no_height():
    m = measures_of([make_polyline([(0, 0), (10, 0)], stroke=K)], [0])
    assert (m.kind, m.width, m.height) == ("line", 10, 0)


# --- cambiar la posicion --------------------------------------------------------------------------------------------
def test_only_moving_moves_without_rebuilding_the_shape():
    s = circle(120, 50, 10)
    res = do([s], [0], 4, 4, 20, 20)
    moved = res.shapes[0]
    assert box(moved) == pytest.approx((4, 4, 24, 24)) and len(moved.shape.exterior.coords) == len(s.shape.exterior.coords)
    assert moved.shape.equals_exact(affinity.translate(s.shape, 4 - 110, 4 - 40), 1e-9)
    assert res.selected == [0] and res.origin == [0]


def odd_circle(cx=50.0, cy=50.0, r=10.0, n=30):
    """Un circulo de un SVG: 30 puntos, ninguno en los ejes de arriba y abajo (su caja no es exactamente cuadrada)."""
    pts = [(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n)) for k in range(n)]
    return ImportedShape(Polygon(pts), True, K, None, kind="circle")


@pytest.mark.parametrize("n", [28, 30])
def test_moving_an_imported_circle_keeps_its_own_points_even_if_they_are_not_what_we_would_draw(n):
    odd = odd_circle(n=n)
    x0, y0, x1, y1 = box(odd)
    assert measures_of([odd], [0]).kind == "circle" and (abs((x1 - x0) - (y1 - y0)) > 0.05) == (n == 30)
    res = do([odd], [0], 0, 0, x1 - x0, y1 - y0)
    assert res.shapes[0].shape.equals_exact(affinity.translate(odd.shape, -x0, -y0), 1e-9)


def test_a_rectangle_with_one_corner_cut_is_not_taken_for_a_rounded_rectangle():
    cut = Polygon([(2, 0), (50, 0), (50, 30), (0, 30), (0, 2)])
    s = ImportedShape(cut, True, K, None, kind="rect")
    assert de._rect_corner(s.shape) is None
    res = do([s], [0], 0, 0, 100, 30)
    assert res.shapes[0].shape.equals_exact(affinity.scale(cut, xfact=2, yfact=1, origin=(0, 0)), 1e-9)


def test_placing_the_lower_left_corner_where_asked():
    res = do([rect(10, 10, 50, 30)], [0], 4, 4, 50, 30)
    assert box(res.shapes[0]) == pytest.approx((4, 4, 54, 34)) and res.shapes[0].kind == "rect"


def test_negative_positions_are_fine():
    res = do([rect(10, 10, 50, 30)], [0], -20, -5, 50, 30)
    assert box(res.shapes[0]) == pytest.approx((-20, -5, 30, 25))


# --- cambiar el tamano ---------------------------------------------------------------------------------------------
def test_a_rectangle_gets_its_corner_at_4_4_and_100_by_250():
    res = do([rect(10, 10, 50, 30)], [0], 4, 4, 100, 250)
    s = res.shapes[0]
    assert box(s) == pytest.approx((4, 4, 104, 254)) and s.kind == "rect" and s.closed
    assert s.shape.area == pytest.approx(25000) and len(s.shape.exterior.coords) == 5


def test_a_rounded_rectangle_keeps_its_corner_radius_when_it_is_resized():
    res = do([rect(10, 10, 50, 30, 5)], [0], 4, 4, 100, 250)
    s = res.shapes[0]
    assert box(s) == pytest.approx((4, 4, 104, 254))
    assert s.shape.area == pytest.approx(100 * 250 - (4 - math.pi) * 25, rel=1e-3)
    assert de._rect_corner(s.shape) == pytest.approx(5, abs=1e-6)
    assert res.notes == []


def test_the_corner_radius_comes_down_if_the_new_size_cannot_hold_it_and_the_user_is_told():
    res = do([rect(10, 10, 50, 30, 10)], [0], 10, 10, 50, 12)
    s = res.shapes[0]
    assert box(s) == pytest.approx((10, 10, 60, 22)) and de._rect_corner(s.shape) == pytest.approx(6, abs=1e-6)
    assert len(res.notes) == 1 and "10" in res.notes[0] and "6" in res.notes[0]


def test_a_rectangle_that_is_a_slot_can_still_be_resized_as_a_rectangle():
    s = rect(0, 0, 50, 20, 10)
    assert de._rect_corner(s.shape) == pytest.approx(10, abs=1e-6)
    res = do([s], [0], 0, 0, 80, 20)
    assert box(res.shapes[0]) == pytest.approx((0, 0, 80, 20)) and de._rect_corner(res.shapes[0].shape) == pytest.approx(10, abs=1e-6)


def test_a_turned_rectangle_is_stretched_like_any_shape_not_rebuilt():
    s = rect(0, 0, 50, 30)
    turned = replace(s, shape=affinity.rotate(s.shape, 30, origin=(0, 0)))
    assert de._rect_corner(turned.shape) is None
    x0, y0, x1, y1 = box(turned)
    res = do([turned], [0], x0, y0, (x1 - x0) * 2, y1 - y0)
    assert box(res.shapes[0]) == pytest.approx((x0, y0, x0 + (x1 - x0) * 2, y1))
    assert res.shapes[0].shape.area == pytest.approx(turned.shape.area * 2)


def test_an_octagon_marked_as_rectangle_is_not_taken_for_a_rounded_rectangle():
    octagon = make_polygon(0, 0, 8, 10, 22.5, stroke=K)
    s = replace(octagon, kind="rect")
    assert de._rect_corner(s.shape) is None
    x0, y0, x1, y1 = box(s)
    res = do([s], [0], -10, -10, 40, 20)
    assert box(res.shapes[0]) == pytest.approx((-10, -10, 30, 10))
    assert res.shapes[0].shape.area == pytest.approx(octagon.shape.area * (40 / (x1 - x0)) * (20 / (y1 - y0)))     # estirado, no rehecho


def test_a_circle_stays_a_circle_with_a_new_diameter_around_the_centre_the_caller_asks_for():
    res = do([circle(120, 50, 10)], [0], 100, 30, 40, 40)
    s = res.shapes[0]
    m = measures_of(res.shapes, [0])
    assert s.kind == "circle" and (m.cx, m.cy, m.width, m.height) == pytest.approx((120, 50, 40, 40))
    assert Polygon(s.shape).area == pytest.approx(math.pi * 400, rel=1e-3)


def test_a_circle_made_much_bigger_is_redrawn_with_enough_points_to_stay_round():
    s = circle(0, 0, 1)
    res = do([s], [0], -100, -100, 200, 200)
    big = res.shapes[0].shape
    assert len(big.exterior.coords) > 3 * len(s.shape.exterior.coords)
    worst = max(abs(math.hypot(x, y) - 100) for x, y in big.exterior.coords)
    assert worst < 1e-6
    mid = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in zip(big.exterior.coords, list(big.exterior.coords)[1:])]
    assert max(100 - math.hypot(x, y) for x, y in mid) <= de.ARC_TOL * 1.2


def test_a_circle_given_two_different_sizes_becomes_an_ellipse():
    res = do([circle(0, 0, 10)], [0], -20, -10, 40, 20)
    assert res.shapes[0].kind == "ellipse" and box(res.shapes[0]) == pytest.approx((-20, -10, 20, 10))


def test_an_ellipse_with_its_axes_on_the_page_is_redrawn_exact():
    s = make_ellipse(0, 0, 30, 10, stroke=K)
    res = do([s], [0], 0, 0, 200, 40)
    out = res.shapes[0]
    assert out.kind == "ellipse" and box(out) == pytest.approx((0, 0, 200, 40))
    assert all(abs(((x - 100) / 100) ** 2 + ((y - 20) / 20) ** 2 - 1) < 1e-9 for x, y in out.shape.exterior.coords)
    assert len(out.shape.exterior.coords) > len(s.shape.exterior.coords)


def test_a_turned_ellipse_is_stretched_not_redrawn():
    s = make_ellipse(0, 0, 30, 10, 30, stroke=K)
    assert not de._axis_ellipse(s.shape, *(lambda b: ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2, (b[2] - b[0]) / 2, (b[3] - b[1]) / 2))(box(s)))
    x0, y0, x1, y1 = box(s)
    res = do([s], [0], x0, y0, x1 - x0, (y1 - y0) * 2)
    assert box(res.shapes[0]) == pytest.approx((x0, y0, x1, y0 + (y1 - y0) * 2))


def test_a_polygon_a_slot_and_an_open_line_are_stretched_from_their_lower_left_corner():
    hexagon = make_polygon(50, 50, 6, 10, stroke=K)
    slot = make_slot(0, 0, 30, 0, 6, stroke=K)
    line = make_polyline([(0, 0), (10, 10), (30, 0)], stroke=K)
    for s in (hexagon, slot, line):
        x0, y0, x1, y1 = box(s)
        res = do([s], [0], 7, 9, (x1 - x0) * 2, (y1 - y0) * 3)
        assert box(res.shapes[0]) == pytest.approx((7, 9, 7 + (x1 - x0) * 2, 9 + (y1 - y0) * 3))
        assert res.shapes[0].kind == s.kind and res.shapes[0].closed == s.closed


def test_a_horizontal_line_can_be_made_longer_and_moved_but_not_given_a_height():
    line = make_polyline([(0, 0), (10, 0)], stroke=K)
    res = do([line], [0], 5, 5, 20, 0)
    assert box(res.shapes[0]) == pytest.approx((5, 5, 25, 5))
    with pytest.raises(EditError, match="alto"):
        do([line], [0], 0, 0, 10, 3)
    vertical = make_polyline([(0, 0), (0, 10)], stroke=K)
    with pytest.raises(EditError, match="ancho"):
        do([vertical], [0], 0, 0, 3, 10)


def test_several_shapes_are_scaled_together_from_the_corner_of_their_common_box():
    a, b = rect(0, 0, 10, 10), rect(30, 20, 10, 10)
    res = do([a, b], [0, 1], 100, 100, 80, 60)
    assert box(res.shapes[0]) == pytest.approx((100, 100, 120, 120))
    assert box(res.shapes[1]) == pytest.approx((160, 140, 180, 160))
    assert res.selected == [0, 1]


def test_shapes_not_chosen_are_left_alone():
    a, b = rect(0, 0, 10, 10), rect(30, 20, 10, 10)
    res = do([a, b], [0], 5, 5, 20, 20)
    assert res.shapes[1] is b and res.origin == [0, 1]


def test_the_style_and_the_id_of_the_shape_are_kept():
    s = replace(circle(), stroke="#ff0000", fill="#00ff00", layer="capa", id="c1")
    out = do([s], [0], 0, 0, 40, 40).shapes[0]
    assert (out.stroke, out.fill, out.layer, out.id, out.kind) == ("#ff0000", "#00ff00", "capa", "c1", "circle")


# --- lo que no se puede ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("w,h,message", [(0, 10, "mayores que cero"), (10, 0, "mayores que cero"),
                                         (-5, 10, "negativos"), (10, -5, "negativos")])
@pytest.mark.parametrize("make", [lambda: rect(), lambda: make_polygon(50, 50, 6, 10, stroke=K),
                                  lambda: make_polyline([(0, 0), (10, 5), (30, 0)], stroke=K)], ids=["rect", "polygon", "polyline"])
def test_a_size_of_zero_or_less_is_refused_for_any_shape(make, w, h, message):
    s = make()
    x0, y0, x1, y1 = box(s)
    with pytest.raises(EditError, match=message):
        do([s], [0], x0, y0, w, h)


def test_a_tiny_negative_size_next_to_a_flat_line_is_still_refused_as_negative():
    line = make_polyline([(0, 0), (10, 0)], stroke=K)
    with pytest.raises(EditError, match="negativos"):
        do([line], [0], 0, 0, 10, -0.0001)


def test_the_same_measures_change_nothing_and_say_so():
    with pytest.raises(EditError, match="ya tiene"):
        do([rect(10, 10, 50, 30)], [0], 10, 10, 50, 30)


def test_rounding_to_a_thousandth_does_not_count_as_a_change_of_size():
    s = circle(120, 50, 10)
    with pytest.raises(EditError, match="ya tiene"):
        do([s], [0], 110.0004, 40.0003, 20.0004, 19.9996)
    res = do([s], [0], 100, 40, 20.0003, 20.0003)
    assert res.shapes[0].shape.equals_exact(affinity.translate(s.shape, -10, 0), 1e-9)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 2e6, "10", None, True])
def test_the_numbers_must_be_real(bad):
    for pos in range(4):
        args = [0, 0, 10, 10]
        args[pos] = bad
        with pytest.raises(EditError):
            do([rect()], [0], *args)


def test_nothing_chosen_or_a_missing_shape_is_refused():
    with pytest.raises(EditError, match="ninguna forma"):
        apply_edit([rect()], {"op": "medidas", "formas": [], "x": 0, "y": 0, "ancho": 1, "alto": 1})
    with pytest.raises(EditError, match="no existe"):
        do([rect()], [3], 0, 0, 10, 10)


# --- dentro del proyecto -----------------------------------------------------------------------------------------------
def tools():
    return {"em4": Tool(diameter=4, name="End mill 4 mm", feed_xy=300, feed_z=200, stepdown=6.5, stepover=0.6)}


def scratch() -> Project:
    p = Project(tools())
    p.new_drawing()
    p.general = General(tool="em4", thickness=19.0, margin=0.4, rotate="0")
    return p


def test_the_edit_has_its_own_name_in_undo_and_can_be_undone_and_redone():
    p = scratch()
    p.edit(de.add_edit([rect(10, 10, 50, 30)]))
    before = p.drawing.shapes[0].shape.wkt
    p.edit(measures_edit([0], 4, 4, 100, 250))
    assert p.undo_label == "Cambiar medidas" and box(p.drawing.shapes[0]) == pytest.approx((4, 4, 104, 254))
    assert p.undo() == "Cambiar medidas" and p.drawing.shapes[0].shape.wkt == before
    assert p.redo() == "Cambiar medidas" and box(p.drawing.shapes[0]) == pytest.approx((4, 4, 104, 254))


def test_a_saved_job_replays_the_new_measures():
    p = scratch()
    p.edit(de.add_edit([circle(50, 30, 8), rect(0, 0, 40, 20, 4)]))
    p.edit(measures_edit([0], 10, 10, 30, 30))
    p.edit(measures_edit([1], 4, 4, 100, 250))
    data = json.loads(json.dumps(p.to_dict()))
    assert [e["op"] for e in data["edicion"]["operaciones"]] == ["anadir", "medidas", "medidas"]
    q = Project(tools())
    q.new_drawing()
    q.apply_dict(data)
    assert [s.shape.wkt for s in q.drawing.shapes] == [s.shape.wkt for s in p.drawing.shapes]
    assert q.load_notes == []


def test_a_refused_measure_leaves_the_drawing_and_the_history_alone():
    p = scratch()
    p.edit(de.add_edit([rect()]))
    before = p.drawing.shapes[0].shape.wkt
    with pytest.raises(ProjectError, match="ya tiene"):
        p.edit(measures_edit([0], 10, 10, 50, 30))
    assert p.drawing.shapes[0].shape.wkt == before and len(p.edits) == 1 and p.undo_label == "Dibujar forma"


def test_resizing_a_board_rectangle_changes_its_job_and_moving_it_moves_the_hold_points_with_it():
    p = scratch()
    p.edit(de.add_edit([make_rectangle(0, 0, 100, 60, stroke=K), circle(50, 30, 8)]))
    assert p.drawing.layout.boards[0].width == pytest.approx(100)
    p.edit(measures_edit([0], 0, 0, 120, 60))
    assert p.drawing.layout.boards[0].width == pytest.approx(120)
