"""Herramientas de dibujo (cnc.drawing_edit, sin Qt): formas basicas, mover/girar/copiar, desfase y unir/restar formas."""
import json
import math
from dataclasses import replace

import pytest
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box

from cnc import drawing_edit as de
from cnc.boards import is_rectangle
from cnc.drawing_edit import (EditError, apply_edit, arc_segments, make_circle, make_ellipse, make_polygon,
                              make_polyline, make_rectangle, make_slot, shape_from_dict, shape_to_dict)
from cnc.svg_import import ImportedShape

K = "#000000"
R = "#ff0000"


def rect(x=0, y=0, w=100, h=50, stroke=K, **kw):
    return make_rectangle(x, y, w, h, stroke=stroke, **kw)


def circle(cx=0, cy=0, r=10, stroke=K):
    return make_circle(cx, cy, r, stroke=stroke)


def line(*pts, stroke=K):
    return make_polyline(pts, stroke=stroke)


def same(a, b, tol=1e-6):
    return a.shape.equals_exact(b.shape, tol)


# --- formas basicas ----------------------------------------------------------------------------------------------
def test_a_rectangle_is_exact_and_counts_as_a_rectangle_for_the_board_convention():
    s = rect(10, 20, 100, 50)
    assert s.shape.bounds == (10, 20, 110, 70) and s.shape.area == pytest.approx(5000) and s.closed
    assert s.kind == "rect" and s.stroke == K and s.fill is None and is_rectangle(s)
    assert len(s.shape.exterior.coords) == 5 and s.shape.exterior.is_ccw


def test_a_rounded_rectangle_keeps_its_bounds_and_loses_the_corner_area():
    s = rect(0, 0, 100, 50, radius=10)
    assert s.shape.bounds == pytest.approx((0, 0, 100, 50))
    assert s.shape.area == pytest.approx(5000 - (4 - math.pi) * 100, rel=1e-3)
    assert not is_rectangle(s)
    # los lados rectos son exactos: el punto medio de cada lado esta en la caja
    for p in ((50, 0), (100, 25), (50, 50), (0, 25)):
        assert s.shape.exterior.distance(Point(p)) < 1e-9


def test_a_corner_radius_of_half_the_short_side_gives_a_slot_and_more_is_refused():
    s = rect(0, 0, 100, 50, radius=25)
    assert s.shape.area == pytest.approx(100 * 50 - (4 - math.pi) * 625, rel=1e-3)
    with pytest.raises(EditError, match="mitad del lado menor"):
        rect(0, 0, 100, 50, radius=25.5)
    with pytest.raises(EditError, match="negativo"):
        rect(radius=-1)


@pytest.mark.parametrize("w,h", [(0, 10), (10, 0), (-5, 10), (10, -5)])
def test_a_rectangle_needs_positive_sides(w, h):
    with pytest.raises(EditError, match="mayor que cero"):
        make_rectangle(0, 0, w, h, stroke=K)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 1e9, "10", None, True])
def test_numbers_must_be_real_and_reasonable(bad):
    with pytest.raises(EditError, match="no es un numero valido"):
        make_rectangle(bad, 0, 10, 10, stroke=K)


@pytest.mark.parametrize("color", ["red", "#fff", "#gggggg", "ff0000", 5])
def test_the_stroke_must_be_a_hex_color(color):
    with pytest.raises(EditError, match="#rrggbb"):
        rect(stroke=color)


def test_the_color_is_normalised_and_may_be_empty():
    assert rect(stroke="#FF00aa").stroke == "#ff00aa"
    assert make_rectangle(0, 0, 5, 5, stroke=None).stroke is None
    assert make_rectangle(0, 0, 5, 5, stroke=K, fill="#00FF00").fill == "#00ff00"


def test_arcs_are_drawn_finely_enough_for_the_tolerance():
    for r in (0.3, 1, 5, 30, 150, 800):
        n = arc_segments(r)
        sag = r * (1 - math.cos(math.pi / 2 / n / 2))
        assert sag <= de.ARC_TOL + 1e-12
        if n > 1:
            m = arc_segments(r) - 1
            assert r * (1 - math.cos(math.pi / 2 / m / 2)) > de.ARC_TOL
    assert arc_segments(1, sweep=math.pi) >= 2 * arc_segments(1, sweep=math.pi / 2) - 1
    assert arc_segments(0.001) == 1


def test_a_circle_reaches_its_four_extremes_and_stays_within_the_tolerance():
    s = circle(30, 40, 25)
    assert s.shape.bounds == pytest.approx((5, 15, 55, 65)) and s.kind == "circle"
    pts = list(s.shape.exterior.coords)[:-1]
    assert all(math.dist(p, (30, 40)) == pytest.approx(25) for p in pts) and len(pts) % 4 == 0
    # el punto mas lejano de la curva verdadera esta a menos de ARC_TOL (el medio de cada cuerda)
    mid = [((pts[i][0] + pts[i - 1][0]) / 2, (pts[i][1] + pts[i - 1][1]) / 2) for i in range(len(pts))]
    assert max(25 - math.dist(m, (30, 40)) for m in mid) <= de.ARC_TOL + 1e-9
    assert s.shape.area == pytest.approx(math.pi * 625, rel=5e-4)
    with pytest.raises(EditError, match="mayor que cero"):
        make_circle(0, 0, 0, stroke=K)


def test_a_small_circle_still_has_enough_points():
    assert len(list(circle(r=0.5).shape.exterior.coords)) - 1 >= 32


def test_an_ellipse_has_its_semi_axes_and_can_be_turned():
    e = make_ellipse(10, 20, 30, 10, stroke=K)
    assert e.shape.bounds == pytest.approx((-20, 10, 40, 30)) and e.kind == "ellipse"
    assert e.shape.area == pytest.approx(math.pi * 300, rel=1e-3)
    t = make_ellipse(10, 20, 30, 10, 90, stroke=K)
    assert t.shape.bounds == pytest.approx((0, -10, 20, 50), abs=1e-6)
    assert make_ellipse(0, 0, 5, 5, 360, stroke=K).shape.bounds == pytest.approx((-5, -5, 5, 5))
    with pytest.raises(EditError):
        make_ellipse(0, 0, 0, 5, stroke=K)


def test_a_regular_polygon_by_radius_by_apothem_and_with_its_first_vertex_turned():
    h = make_polygon(5, 5, 6, 10, stroke=K)
    pts = list(h.shape.exterior.coords)[:-1]
    assert len(pts) == 6 and all(math.dist(p, (5, 5)) == pytest.approx(10) for p in pts) and h.kind == "polygon"
    assert pts[0] == pytest.approx((15, 5))
    flats = make_polygon(0, 0, 6, 8, measure="apotema", stroke=K)           # tuerca de 16 mm entre caras
    assert flats.shape.bounds[3] == pytest.approx(8) and flats.shape.bounds[2] == pytest.approx(8 / math.cos(math.pi / 6))
    turned = make_polygon(0, 0, 4, 10, 45, stroke=K)                         # cuadrado con los lados en los ejes
    assert turned.shape.bounds == pytest.approx((-math.sqrt(50), -math.sqrt(50), math.sqrt(50), math.sqrt(50)))
    for sides in (2, 0, -1, 2.5, True):
        with pytest.raises(EditError, match="lados"):
            make_polygon(0, 0, sides, 10, stroke=K)
    with pytest.raises(EditError, match="radio o como apotema"):
        make_polygon(0, 0, 5, 10, measure="lado", stroke=K)


def test_a_slot_is_a_stadium_between_two_centres():
    s = make_slot(0, 0, 40, 0, 10, stroke=K)
    assert s.shape.bounds == pytest.approx((-5, -5, 45, 5)) and s.closed
    assert s.shape.area == pytest.approx(40 * 10 + math.pi * 25, rel=2e-3)
    d = make_slot(0, 0, 30, 40, 6, stroke=K)                                  # en diagonal
    assert d.shape.area == pytest.approx(50 * 6 + math.pi * 9, rel=2e-3)
    with pytest.raises(EditError, match="coinciden"):
        make_slot(1, 1, 1, 1, 5, stroke=K)


def test_lines_polylines_and_closed_outlines():
    one = line((0, 0), (10, 5))
    assert not one.closed and one.kind == "line" and one.shape.length == pytest.approx(math.hypot(10, 5))
    poly = line((0, 0), (10, 0), (10, 10))
    assert poly.kind == "polyline" and not poly.closed
    tri = make_polyline([(0, 0), (10, 0), (0, 10)], closed=True, stroke=K)
    assert tri.closed and tri.kind == "polygon" and tri.shape.area == pytest.approx(50) and tri.shape.exterior.is_ccw
    cw = make_polyline([(0, 0), (0, 10), (10, 0)], closed=True, stroke=K)
    assert cw.shape.exterior.is_ccw                                                # siempre antihorario
    auto = make_polyline([(0, 0), (10, 0), (10, 10), (0, 0)], stroke=K)             # el ultimo punto vuelve al primero
    assert auto.closed and auto.kind == "polygon"


def test_polylines_refuse_what_cannot_be_drawn():
    with pytest.raises(EditError, match="dos puntos distintos"):
        line((5, 5), (5, 5))
    with pytest.raises(EditError, match="dos puntos distintos"):
        line((5, 5))
    with pytest.raises(EditError, match="al menos tres"):
        make_polyline([(0, 0), (1, 0)], closed=True, stroke=K)
    with pytest.raises(EditError, match="se cruza"):
        make_polyline([(0, 0), (10, 10), (10, 0), (0, 10)], closed=True, stroke=K)
    with pytest.raises(EditError, match="no encierra nada"):
        make_polyline([(0, 0), (10, 0), (20, 0)], closed=True, stroke=K)
    with pytest.raises(EditError, match="punto mal escrito"):
        make_polyline([(0, 0), 5], stroke=K)
    with pytest.raises(EditError, match="punto mal escrito"):
        make_polyline([(0, 0), (1,)], stroke=K)


def test_a_line_that_goes_and_comes_back_over_three_points_stays_open():
    s = make_polyline([(0, 0), (10, 0), (0, 0)], stroke=K)
    assert not s.closed and s.kind == "polyline"


def test_a_closed_outline_that_encloses_almost_nothing_is_refused():
    with pytest.raises(EditError, match="no encierra nada"):
        make_polyline([(0, 0), (0.1, 0), (0, 0.1)], closed=True, stroke=K)                 # 0,005 mm2
    assert make_polyline([(0, 0), (0.2, 0), (0, 0.2)], closed=True, stroke=K).shape.area == pytest.approx(0.02)


def test_repeated_points_in_a_polyline_are_dropped():
    s = make_polyline([(0, 0), (0, 0), (10, 0), (10, 0), (10, 10)], stroke=K)
    assert list(s.shape.coords) == [(0, 0), (10, 0), (10, 10)]


# --- guardar y leer ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("make", [lambda: rect(1.23456789, 2, 30, 20, radius=3), lambda: circle(5, 5, 7),
                                   lambda: line((0, 0), (3, 4), (9, 1)), lambda: make_slot(0, 0, 10, 10, 4, stroke=R)])
def test_a_shape_survives_being_written_and_read_back_as_json(make):
    s = make()
    back = shape_from_dict(json.loads(json.dumps(shape_to_dict(s))))
    assert same(s, back, 1e-5) and back.closed == s.closed and back.stroke == s.stroke and back.kind == s.kind


def test_writing_twice_gives_the_same_shape():
    once = shape_from_dict(shape_to_dict(circle(1 / 3, 2 / 3, 7.77)))
    twice = shape_from_dict(shape_to_dict(once))
    assert same(once, twice, 0.0)


@pytest.mark.parametrize("bad", [
    {}, {"geom": "nonsense", "cerrada": True}, {"geom": "POINT (1 1)", "cerrada": True},
    {"geom": "LINESTRING (0 0, 1 1)", "cerrada": True}, {"geom": "POLYGON ((0 0, 1 0, 1 1, 0 0))", "cerrada": False},
    {"geom": "POLYGON ((0 0, 10 0, 10 10, 0 10, 0 0), (2 2, 4 2, 4 4, 2 2))", "cerrada": True},
    {"geom": "POLYGON ((0 0, 10 10, 10 0, 0 10, 0 0))", "cerrada": True},
    {"geom": "POLYGON ((0 0, 1 0, 1 1, 0 0))", "cerrada": True, "trazo": "rojo"},
])
def test_a_stored_shape_that_makes_no_sense_is_refused(bad):
    with pytest.raises(EditError):
        shape_from_dict(bad)


# --- aplicar ediciones: anadir y borrar ----------------------------------------------------------------------------
def test_adding_shapes_appends_them_and_selects_them():
    base = [rect(), circle()]
    new = [circle(50, 25, 5, R), line((0, 0), (1, 1))]
    res = apply_edit(base, de.add_edit(new))
    assert len(res.shapes) == 4 and res.shapes[:2] == base and res.origin == [0, 1, None, None] and res.selected == [2, 3]
    assert res.shapes[2].stroke == R and res.index_map() == {0: 0, 1: 1}
    assert len(base) == 2                                            # la lista de entrada no se toca


def test_adding_nothing_or_too_much_is_refused():
    with pytest.raises(EditError, match="ninguna forma"):
        apply_edit([], {"op": "anadir", "formas": []})
    with pytest.raises(EditError, match="ninguna forma"):
        apply_edit([], {"op": "anadir"})
    big = {"op": "anadir", "formas": [shape_to_dict(circle())] * (de.MAX_NEW + 1)}
    with pytest.raises(EditError, match="demasiadas"):
        apply_edit([], big)


def test_deleting_keeps_the_rest_in_order_and_remembers_where_they_were():
    base = [rect(), circle(), line((0, 0), (5, 5)), circle(9, 9, 2)]
    res = apply_edit(base, de.delete_edit([1, 3]))
    assert res.shapes == [base[0], base[2]] and res.origin == [0, 2] and res.selected == []
    assert res.index_map() == {0: 0, 2: 1}


@pytest.mark.parametrize("indices", [[], [5], [-1], ["a"], [True], [1.0], None])
def test_picking_shapes_that_do_not_exist_is_refused(indices):
    with pytest.raises(EditError, match="ninguna forma elegida|no existe"):
        apply_edit([rect(), circle()], {"op": "borrar", "formas": indices})


def test_a_repeated_index_counts_once():
    res = apply_edit([rect(), circle(), circle(5, 5, 1)], {"op": "borrar", "formas": [1, 1, 1]})
    assert len(res.shapes) == 2


def test_unknown_operations_are_refused():
    with pytest.raises(EditError, match="desconocida"):
        apply_edit([rect()], {"op": "explotar", "formas": [0]})
    with pytest.raises(EditError, match="desconocida"):
        apply_edit([rect()], "mover")
    with pytest.raises(EditError, match="desconocida"):
        apply_edit([rect()], {})


# --- mover, girar, cambiar tamano, reflejar ----------------------------------------------------------------------------
def test_moving_shifts_only_the_picked_shapes_and_keeps_their_index():
    base = [rect(0, 0, 10, 10), circle(50, 50, 5), rect(100, 0, 10, 10)]
    res = apply_edit(base, de.move_edit([0, 2], 3, -4))
    assert res.shapes[0].shape.bounds == (3, -4, 13, 6) and res.shapes[2].shape.bounds == (103, -4, 113, 6)
    assert res.shapes[1] is base[1] and res.origin == [0, 1, 2] and res.selected == [0, 2]
    assert res.shapes[0].stroke == K and res.shapes[0].kind == "rect"


def test_moving_by_nothing_is_refused_and_so_is_a_bad_number():
    with pytest.raises(EditError, match="cero"):
        apply_edit([rect()], de.move_edit([0], 0, 0))
    with pytest.raises(EditError, match="no es un numero"):
        apply_edit([rect()], de.move_edit([0], float("nan"), 1))
    assert apply_edit([rect()], de.move_edit([0], 0, 2)).shapes[0].shape.bounds == (0, 2, 100, 52)


def test_rotating_turns_counterclockwise_about_the_given_centre():
    base = [rect(10, 0, 20, 10)]
    res = apply_edit(base, de.rotate_edit([0], 90, 0, 0))                    # (10..30, 0..10) -> (-10..0, 10..30)
    assert res.shapes[0].shape.bounds == pytest.approx((-10, 10, 0, 30))
    res = apply_edit(base, de.rotate_edit([0], 90, 20, 5))                   # gira sobre su propio centro
    assert res.shapes[0].shape.bounds == pytest.approx((15, -5, 25, 15))
    assert res.shapes[0].shape.area == pytest.approx(200)
    res = apply_edit(base, de.rotate_edit([0], -90, 0, 0))
    assert res.shapes[0].shape.bounds == pytest.approx((0, -30, 10, -10))


@pytest.mark.parametrize("deg", [0, 360, -720])
def test_a_turn_that_changes_nothing_is_refused(deg):
    with pytest.raises(EditError, match="no cambia nada"):
        apply_edit([rect()], de.rotate_edit([0], deg, 0, 0))


def test_scaling_about_a_point_and_a_circle_that_becomes_an_ellipse():
    base = [rect(10, 10, 20, 10), circle(0, 0, 5)]
    res = apply_edit(base, de.scale_edit([0], 2, 3, 10, 10))
    assert res.shapes[0].shape.bounds == pytest.approx((10, 10, 50, 40))
    res = apply_edit(base, de.scale_edit([1], 2, 2, 0, 0))
    assert res.shapes[1].kind == "circle" and res.shapes[1].shape.bounds == pytest.approx((-10, -10, 10, 10))
    res = apply_edit(base, de.scale_edit([1], 2, 1, 0, 0))
    assert res.shapes[1].kind == "ellipse" and res.shapes[1].shape.bounds == pytest.approx((-10, -5, 10, 5))
    res = apply_edit(base, de.scale_edit([0], 2, 1, 0, 0))
    assert res.shapes[0].kind == "rect"


@pytest.mark.parametrize("sx,sy", [(0, 1), (1, 0), (-1, 1), (1, -2)])
def test_scale_factors_must_be_positive(sx, sy):
    with pytest.raises(EditError, match="mayor que cero"):
        apply_edit([rect()], de.scale_edit([0], sx, sy, 0, 0))


def test_a_scale_of_one_is_refused():
    with pytest.raises(EditError, match="no cambia nada"):
        apply_edit([rect()], de.scale_edit([0], 1, 1, 0, 0))
    apply_edit([rect()], de.scale_edit([0], 1, 1.5, 0, 0))


def test_mirroring_about_a_vertical_and_a_horizontal_axis():
    base = [rect(10, 0, 20, 10), line((10, 0), (30, 5))]
    res = apply_edit(base, de.mirror_edit([0, 1], "vertical", 0))
    assert res.shapes[0].shape.bounds == pytest.approx((-30, 0, -10, 10)) and res.shapes[0].shape.exterior.is_ccw
    assert [c for xy in res.shapes[1].shape.coords for c in xy] == pytest.approx([-10, 0, -30, 5])
    res = apply_edit(base, de.mirror_edit([0], "horizontal", 20))
    assert res.shapes[0].shape.bounds == pytest.approx((10, 30, 30, 40))
    res = apply_edit(base, de.mirror_edit([0], "vertical", 20))                # el eje pasa por el centro: queda donde estaba
    assert res.shapes[0].shape.bounds == pytest.approx((10, 0, 30, 10))
    with pytest.raises(EditError, match="vertical u horizontal"):
        apply_edit(base, de.mirror_edit([0], "diagonal", 0))


# --- copias ---------------------------------------------------------------------------------------------------------------
def test_a_grid_of_copies_puts_the_originals_first_and_selects_the_copies():
    base = [rect(0, 0, 10, 10), circle(100, 100, 3)]
    res = apply_edit(base, de.array_edit([0], 3, 2, 20, 30))
    assert len(res.shapes) == 2 + 5 and res.shapes[:2] == base
    boxes = sorted(s.shape.bounds[:2] for s in res.shapes[2:])
    assert boxes == [(0, 30), (20, 0), (20, 30), (40, 0), (40, 30)]
    assert res.origin == [0, 1, 0, 0, 0, 0, 0] and res.selected == [2, 3, 4, 5, 6]
    assert all(s.id is None for s in res.shapes[2:])


def test_copies_of_several_shapes_keep_their_style_and_come_in_order():
    base = [rect(0, 0, 10, 10, stroke=R), circle(50, 0, 3)]
    res = apply_edit(base, de.array_edit([0, 1], 1, 3, 0, 100))
    assert [s.stroke for s in res.shapes] == [R, K, R, K, R, K] and res.origin == [0, 1, 0, 1, 0, 1]
    assert [s.shape.bounds[1] for s in res.shapes] == [0, -3, 100, 97, 200, 197]
    assert res.selected == [2, 3, 4, 5]


def test_a_grid_can_go_backwards_with_negative_spacing():
    res = apply_edit([rect(0, 0, 10, 10)], de.array_edit([0], 2, 1, -25, 0))
    assert res.shapes[1].shape.bounds == (-25, 0, -15, 10)


@pytest.mark.parametrize("nx,ny,dx,dy,match", [
    (1, 1, 10, 10, "ninguna copia"), (2, 2, 0, 0, "unas encima de otras"), (3, 1, 0, 50, "unas encima de otras"),
    (0, 2, 5, 5, "numero entero"), (2.5, 2, 5, 5, "numero entero"), (True, 2, 5, 5, "numero entero"),
    (de.MAX_NEW + 1, 1, 5, 5, "numero entero"), (2, 2, float("nan"), 5, "no es un numero"),
])
def test_grids_that_make_no_sense_are_refused(nx, ny, dx, dy, match):
    with pytest.raises(EditError, match=match):
        apply_edit([rect()], {"op": "copiar", "formas": [0], "nx": nx, "ny": ny, "dx": dx, "dy": dy})


def test_too_many_copies_are_refused_even_if_each_number_is_fine():
    base = [circle(i * 3, 0, 1) for i in range(60)]
    with pytest.raises(EditError, match="copias"):
        apply_edit(base, de.array_edit(list(range(60)), 50, 2, 1000, 1000))


def test_copies_in_a_circle_are_turned_about_the_centre():
    base = [circle(10, 0, 2)]
    res = apply_edit(base, de.polar_edit([0], 4, 360, 0, 0))
    centres = sorted((round(s.shape.centroid.x), round(s.shape.centroid.y)) for s in res.shapes)
    assert centres == [(-10, 0), (0, -10), (0, 10), (10, 0)] and res.selected == [1, 2, 3]
    assert res.origin == [0, 0, 0, 0]


def test_copies_over_part_of_a_circle_include_both_ends():
    res = apply_edit([circle(10, 0, 2)], de.polar_edit([0], 3, 180, 0, 0))     # 0, 90 y 180 grados
    centres = sorted((round(s.shape.centroid.x), round(s.shape.centroid.y)) for s in res.shapes)
    assert centres == [(-10, 0), (0, 10), (10, 0)]
    res = apply_edit([circle(10, 0, 2)], de.polar_edit([0], 3, -180, 0, 0))
    centres = sorted((round(s.shape.centroid.x), round(s.shape.centroid.y)) for s in res.shapes)
    assert centres == [(-10, 0), (0, -10), (10, 0)]


def test_copies_in_a_circle_turn_the_copies_too():
    res = apply_edit([rect(10, -1, 6, 2)], de.polar_edit([0], 4, 360, 0, 0))
    tall = [s for s in res.shapes if s.shape.bounds[3] - s.shape.bounds[1] > 5]
    assert len(tall) == 2


@pytest.mark.parametrize("n,deg,match", [(1, 360, "numero entero"), (3, 0, "cero"), (3, 400, "360"), (3, -361, "360")])
def test_circular_copies_that_make_no_sense_are_refused(n, deg, match):
    with pytest.raises(EditError, match=match):
        apply_edit([circle(5, 5, 1)], de.polar_edit([0], n, deg, 0, 0))


# --- desfase -------------------------------------------------------------------------------------------------------------
def test_offsetting_a_rectangle_outwards_and_inwards():
    base = [rect(0, 0, 100, 50)]
    out = apply_edit(base, de.offset_edit([0], 3, corners="vivas"))
    assert len(out.shapes) == 1 and out.shapes[0].shape.bounds == pytest.approx((-3, -3, 103, 53))
    assert out.shapes[0].shape.area == pytest.approx(106 * 56) and out.origin == [0] and out.selected == [0]
    assert out.shapes[0].kind == "path" and out.shapes[0].stroke == K
    inn = apply_edit(base, de.offset_edit([0], -3, corners="vivas"))
    assert inn.shapes[0].shape.bounds == pytest.approx((3, 3, 97, 47))


def test_round_corners_take_away_area_that_sharp_ones_keep():
    sharp = apply_edit([rect(0, 0, 100, 50)], de.offset_edit([0], 5, corners="vivas")).shapes[0].shape.area
    rounded = apply_edit([rect(0, 0, 100, 50)], de.offset_edit([0], 5, corners="redondas")).shapes[0].shape.area
    assert sharp - rounded == pytest.approx((4 - math.pi) * 25, rel=5e-3)
    assert len(apply_edit([rect()], de.offset_edit([0], 5, corners="vivas")).shapes[0].shape.exterior.coords) == 5


def test_offsetting_can_keep_the_original_and_then_the_copy_goes_last():
    base = [rect(0, 0, 100, 50), circle(500, 500, 3)]
    res = apply_edit(base, de.offset_edit([0], 2, keep=True))
    assert len(res.shapes) == 3 and res.shapes[:2] == base and res.origin == [0, 1, 0] and res.selected == [2]
    res = apply_edit(base, de.offset_edit([0], 2, keep=False))
    assert len(res.shapes) == 2 and res.shapes[0] is base[1] and res.origin == [1, 0] and res.selected == [1]


def test_the_offset_keeps_the_style_of_the_shape():
    s = make_rectangle(0, 0, 10, 10, stroke=R, fill="#00ff00")
    out = apply_edit([s], de.offset_edit([0], 1)).shapes[0]
    assert (out.stroke, out.fill, out.closed) == (R, "#00ff00", True)


def test_an_inward_offset_that_splits_a_shape_gives_two_shapes():
    two = ImportedShape(box(0, 0, 20, 20).union(box(40, 0, 60, 20)).union(box(20, 8, 40, 12)), True, K)
    assert isinstance(two.shape, Polygon)
    res = apply_edit([two], de.offset_edit([0], -3, corners="vivas"))                  # el cuello de 4 mm desaparece
    assert len(res.shapes) == 2 and res.origin == [0, 0] and res.selected == [0, 1]
    assert sorted(s.shape.bounds for s in res.shapes) == [pytest.approx((3, 3, 17, 17)), pytest.approx((43, 3, 57, 17))]


def test_an_open_line_is_wrapped_by_an_outward_offset_only():
    base = [line((0, 0), (50, 0))]
    res = apply_edit(base, de.offset_edit([0], 3))
    assert res.shapes[0].closed and res.shapes[0].shape.bounds == pytest.approx((-3, -3, 53, 3))
    assert res.shapes[0].shape.area == pytest.approx(50 * 6 + math.pi * 9, rel=2e-3)
    with pytest.raises(EditError, match="solo se puede desfasar hacia fuera"):
        apply_edit(base, de.offset_edit([0], -3))


def test_an_offset_that_leaves_nothing_is_refused_and_a_partial_one_says_so():
    with pytest.raises(EditError, match="no deja nada"):
        apply_edit([rect(0, 0, 10, 10)], de.offset_edit([0], -6))
    res = apply_edit([rect(0, 0, 10, 10), rect(0, 20, 100, 100)], de.offset_edit([0, 1], -6))
    assert len(res.shapes) == 1 and res.origin == [1] and res.shapes[0].shape.bounds == pytest.approx((6, 26, 94, 114))
    assert len(res.notes) == 1 and "desaparecen" in res.notes[0] and "1 forma" in res.notes[0]


def test_the_offset_options_are_checked():
    with pytest.raises(EditError, match="cero"):
        apply_edit([rect()], de.offset_edit([0], 0))
    with pytest.raises(EditError, match="redondas o vivas"):
        apply_edit([rect()], de.offset_edit([0], 1, corners="biseladas"))


def test_offset_results_have_no_vertices_that_are_noise():
    res = apply_edit([circle(0, 0, 40)], de.offset_edit([0], 7))
    pts = list(res.shapes[0].shape.exterior.coords)
    assert min(math.dist(a, b) for a, b in zip(pts, pts[1:])) > 0.05


# --- unir, restar, intersecar ------------------------------------------------------------------------------------------
def test_union_of_two_overlapping_shapes_is_one_shape_with_the_area_of_both_minus_the_overlap():
    base = [rect(0, 0, 10, 10), rect(5, 0, 10, 10, stroke=R), circle(100, 100, 2)]
    res = apply_edit(base, de.union_edit([0, 1]))
    assert len(res.shapes) == 2 and res.shapes[0] is base[2] and res.shapes[1].shape.area == pytest.approx(150)
    assert res.shapes[1].stroke == K and res.origin == [2, 0] and res.selected == [1]       # el estilo de la primera


def test_union_of_separate_shapes_stays_separate_and_the_originals_can_be_kept():
    base = [rect(0, 0, 10, 10), rect(50, 0, 10, 10)]
    res = apply_edit(base, de.union_edit([0, 1], keep=True))
    assert len(res.shapes) == 4 and res.shapes[:2] == base and res.origin == [0, 1, 0, 0]
    assert sorted(res.selected) == [2, 3]


def test_subtracting_makes_a_hole_when_the_second_is_inside_the_first():
    base = [rect(0, 0, 100, 100), circle(50, 50, 10, stroke=R)]
    res = apply_edit(base, de.subtract_edit([0, 1]))
    assert len(res.shapes) == 2 and res.origin == [0, 0] and res.selected == [0, 1]
    outer, hole = res.shapes
    assert outer.shape.area == pytest.approx(10000) and hole.shape.area == pytest.approx(math.pi * 100, rel=1e-3)
    assert not outer.shape.interiors and not hole.shape.interiors
    assert outer.shape.contains(hole.shape) and hole.stroke == K                           # el hueco, con el estilo de la base
    assert outer.kind == hole.kind == "path"


def test_subtracting_a_corner_leaves_one_shape():
    res = apply_edit([rect(0, 0, 100, 100), rect(80, 80, 40, 40)], de.subtract_edit([0, 1]))
    assert len(res.shapes) == 1 and res.shapes[0].shape.area == pytest.approx(10000 - 400)


def test_subtracting_takes_the_first_or_the_last_as_the_base():
    base = [circle(50, 50, 10), rect(0, 0, 100, 100)]
    with pytest.raises(EditError, match="vacio"):                               # circulo menos cuadrado: nada
        apply_edit(base, de.subtract_edit([0, 1], base="primera"))
    last = apply_edit(base, de.subtract_edit([0, 1], base="ultima"))             # cuadrado menos circulo: contorno y hueco
    assert len(last.shapes) == 2 and last.origin == [1, 1] and last.shapes[0].shape.area == pytest.approx(10000)
    with pytest.raises(EditError, match="primera o la ultima"):
        apply_edit(base, de.subtract_edit([0, 1], base="segunda"))


def test_subtracting_everything_leaves_an_empty_result_which_is_an_error():
    with pytest.raises(EditError, match="vacio"):
        apply_edit([circle(50, 50, 10), rect(0, 0, 100, 100)], de.subtract_edit([0, 1], base="primera"))


def test_subtracting_several_shapes_removes_them_all():
    base = [rect(0, 0, 100, 100), circle(20, 50, 5), circle(80, 50, 5), circle(50, 80, 5)]
    res = apply_edit(base, de.subtract_edit([0, 1, 2, 3]))
    assert len(res.shapes) == 4 and res.origin == [0] * 4 and res.shapes[0].shape.area == pytest.approx(10000)


def test_intersection_keeps_only_the_common_part():
    res = apply_edit([rect(0, 0, 10, 10), rect(5, 5, 10, 10), rect(7, 0, 10, 10)], de.intersect_edit([0, 1, 2]))
    assert len(res.shapes) == 1 and res.shapes[0].shape.bounds == pytest.approx((7, 5, 10, 10))
    with pytest.raises(EditError, match="vacio"):
        apply_edit([rect(0, 0, 10, 10), rect(50, 50, 10, 10)], de.intersect_edit([0, 1]))


def test_booleans_need_two_closed_shapes():
    with pytest.raises(EditError, match="al menos dos"):
        apply_edit([rect(), circle()], de.union_edit([0]))
    for make in (de.union_edit, de.subtract_edit, de.intersect_edit):
        with pytest.raises(EditError, match="formas cerradas"):
            apply_edit([rect(), line((0, 0), (5, 5))], make([0, 1]))


def test_an_invalid_polygon_is_repaired_before_a_boolean_instead_of_failing():
    bow = ImportedShape(Polygon([(0, 0), (10, 10), (10, 0), (0, 10)]), True, K)          # un lazo: invalido
    assert not bow.shape.is_valid
    res = apply_edit([bow, rect(100, 100, 5, 5)], de.union_edit([0, 1]))
    assert sorted(round(s.shape.area, 6) for s in res.shapes) == [25, 25, 25]            # los dos triangulos del lazo y el cuadrado


def test_vertices_that_a_boolean_leaves_almost_in_line_are_dropped():
    a = rect(0, 0, 10, 10)
    b = ImportedShape(affinity.rotate(a.shape, 0.00001, origin=(5, 5)), True, K)          # el mismo cuadrado, girado una milesima
    assert len(a.shape.intersection(b.shape).exterior.coords) > 5                         # shapely deja 8 vertices
    res = apply_edit([a, b], de.intersect_edit([0, 1]))
    assert len(res.shapes) == 1 and len(res.shapes[0].shape.exterior.coords) == 5


def test_slivers_left_by_a_boolean_are_dropped():
    with pytest.raises(EditError, match="vacio"):                      # queda una tira de 0,005 mm2: es ruido, no una forma
        apply_edit([rect(0, 0, 10, 10), rect(0, 0, 10, 9.9995)], de.subtract_edit([0, 1]))
    res = apply_edit([rect(0, 0, 10, 10), rect(0, 0, 10, 9.5)], de.subtract_edit([0, 1]))
    assert len(res.shapes) == 1 and res.shapes[0].shape.area == pytest.approx(5)


# --- cerrar contornos ------------------------------------------------------------------------------------------------------
def test_closing_an_open_outline_makes_a_polygon_in_place():
    base = [line((0, 0), (10, 0), (10, 10)), rect(), circle()]
    res = apply_edit(base, de.close_edit([0, 1]))
    assert res.shapes[0].closed and res.shapes[0].kind == "polygon" and res.shapes[0].shape.area == pytest.approx(50)
    assert res.shapes[1] is base[1] and res.origin == [0, 1, 2] and res.selected == [0]


def test_closing_refuses_what_cannot_be_closed():
    with pytest.raises(EditError, match="ninguna"):
        apply_edit([rect(), circle()], de.close_edit([0, 1]))
    with pytest.raises(EditError, match="se cruzan"):
        apply_edit([line((0, 0), (10, 10), (10, 0), (0, 10))], de.close_edit([0]))
    res = apply_edit([line((0, 0), (10, 0), (10, 10)), line((0, 0), (10, 0), (20, 0))], de.close_edit([0, 1]))
    assert res.selected == [0] and res.notes and "1 linea" in res.notes[0] and not res.shapes[1].closed


# --- todo junto ---------------------------------------------------------------------------------------------------------
EDITS = [
    de.move_edit([0], 5, 5), de.rotate_edit([1], 30, 10, 10), de.scale_edit([0], 1.5, 2, 0, 0),
    de.mirror_edit([1], "vertical", 20), de.array_edit([0], 2, 2, 60, 60), de.polar_edit([1], 5, 360, 0, 0),
    de.offset_edit([0], 2.5, keep=True), de.union_edit([0, 1], keep=True), de.subtract_edit([0, 1]),
    de.intersect_edit([0, 1]), de.delete_edit([1]), de.close_edit([2]),
    de.add_edit([make_slot(0, 0, 20, 5, 4, stroke=R)]),
]


@pytest.mark.parametrize("edit", EDITS, ids=[e["op"] for e in EDITS])
def test_every_edit_gives_the_same_result_after_a_trip_through_json(edit):
    base = [rect(0, 0, 40, 30), circle(20, 15, 12), line((0, 0), (10, 0), (10, 10))]
    again = json.loads(json.dumps(edit))
    a, b = apply_edit(base, edit), apply_edit(base, again)
    assert [s.shape.wkt for s in a.shapes] == [s.shape.wkt for s in b.shapes]
    assert a.origin == b.origin and a.selected == b.selected


@pytest.mark.parametrize("edit", EDITS, ids=[e["op"] for e in EDITS])
def test_no_edit_touches_the_list_it_is_given_nor_leaves_shapes_without_a_valid_geometry(edit):
    base = [rect(0, 0, 40, 30), circle(20, 15, 12), line((0, 0), (10, 0), (10, 10))]
    before = [s.shape.wkt for s in base]
    res = apply_edit(base, edit)
    assert [s.shape.wkt for s in base] == before and len(base) == 3
    assert len(res.origin) == len(res.shapes) and all(0 <= i < len(res.shapes) for i in res.selected)
    for s in res.shapes:
        assert not s.shape.is_empty and s.shape.is_valid and s.closed == isinstance(s.shape, Polygon)
        assert not (isinstance(s.shape, Polygon) and s.shape.interiors)


def test_the_menu_names_every_edit():
    assert set(de.LABELS) == set(de.OPS)
    assert de.edit_label({"op": "mover"}) == "Mover" and de.edit_label({"op": "?"}) == "Edicion" and de.edit_label(5) == "Edicion"


def test_index_map_keeps_the_first_new_shape_of_each_old_one():
    res = de.EditResult([], [3, None, 0, 3, 0], [])
    assert res.index_map() == {3: 0, 0: 2}


# --- limites de los numeros, indices y colores ------------------------------------------------------------------------
def test_coordinates_up_to_a_million_mm_are_accepted_and_beyond_that_it_is_a_typo():
    assert circle(1.0e6, 0, 1).shape.bounds[2] == pytest.approx(1.0e6 + 1)
    for bad in (1.000001e6, 5.0e6, -5.0e6):
        with pytest.raises(EditError, match="no es un numero valido"):
            circle(bad, 0, 1)
    with pytest.raises(EditError, match="no es un numero valido"):
        circle(0, 0, 5.0e6)


def test_the_largest_whole_numbers_are_accepted_and_one_more_is_refused():
    assert len(make_polygon(0, 0, 5000, 100, stroke=K).shape.exterior.coords) == 5001
    with pytest.raises(EditError, match="numero entero"):
        make_polygon(0, 0, 5001, 100, stroke=K)
    with pytest.raises(EditError, match="numero entero"):
        make_polygon(0, 0, 2, 100, stroke=K)
    with pytest.raises(EditError, match="numero entero"):
        make_polygon(0, 0, 4.5, 100, stroke=K)
    with pytest.raises(EditError, match="numero entero"):
        make_polygon(0, 0, True, 100, stroke=K)


def test_an_index_that_does_not_exist_is_refused_even_if_it_is_the_count():
    shapes = [rect(), circle(200, 0)]
    for bad in ([2], [-1], [True], [0.0], [1, 5]):
        with pytest.raises(EditError, match="no existe"):
            apply_edit(shapes, {"op": "borrar", "formas": bad})
    for bad in ([], None, "01", 0):
        with pytest.raises(EditError, match="ninguna forma elegida"):
            apply_edit(shapes, {"op": "borrar", "formas": bad})


def test_repeated_and_unordered_indices_count_once_and_in_drawing_order():
    shapes = [rect(), circle(200, 0), circle(400, 0)]
    res = apply_edit(shapes, {"op": "mover", "formas": [1, 0, 1], "dx": 5, "dy": 0})
    assert res.selected == [0, 1] and len(res.shapes) == 3
    res = apply_edit(shapes, {"op": "copiar", "formas": [1, 1], "nx": 2, "ny": 1, "dx": 50, "dy": 0})
    assert len(res.shapes) == 4 and res.origin == [0, 1, 2, 1]                           # una sola copia
    # la primera de las elegidas es la mas antigua aunque se escriban al reves: a ella se le resta
    big, small = rect(0, 0, 100, 100), rect(10, 10, 20, 20)
    res = apply_edit([big, small], {"op": "restar", "formas": [1, 0], "base": "primera", "conservar": False})
    assert sorted(round(s.shape.area) for s in res.shapes) == [400, 10000]                # la placa y su hueco, aparte


@pytest.mark.parametrize("bad", ["0abcdef", "#abcde", "#abcdeff", "#abcdeg", "ff0000", "red", 5])
def test_a_colour_has_to_be_hash_and_six_hex_digits(bad):
    with pytest.raises(EditError, match="color"):
        make_circle(0, 0, 5, stroke=bad)


def test_colours_are_stored_in_lower_case_and_may_be_missing():
    s = make_circle(0, 0, 5, stroke="#AABBCC", fill=" #FF0000 ")
    assert (s.stroke, s.fill) == ("#aabbcc", "#ff0000")
    assert make_circle(0, 0, 5, stroke=None).stroke is None


def test_a_big_circle_keeps_the_arc_tolerance_and_the_number_of_points_has_a_floor_and_a_cap():
    r = 500.0
    pts = list(circle(0, 0, r).shape.exterior.coords)
    sagitta = max(r - math.dist((0, 0), ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)) for a, b in zip(pts, pts[1:]))
    assert 0 <= sagitta <= de.ARC_TOL + 1e-9
    assert len(circle(0, 0, 0.5).shape.exterior.coords) - 1 == 32            # un minimo de 8 tramos por cuarto
    assert len(circle(0, 0, 1.0e5).shape.exterior.coords) - 1 == 1024        # y un maximo de 256


def test_points_a_micron_apart_are_the_same_point_but_half_a_micron_is_not():
    assert list(line((0, 0), (5, 0), (5 + 1e-9, 0), (10, 0)).shape.coords) == [(0, 0), (5, 0), (10, 0)]
    assert len(line((0, 0), (5, 0), (5.0005, 0), (10, 0)).shape.coords) == 4
    sq = make_polyline([(0, 0), (10, 0), (10 + 1e-9, 1e-9), (10, 10), (0, 10)], closed=True, stroke=K)
    assert len(sq.shape.exterior.coords) == 5
    tiny = make_polyline([(0, 0), (10, 0), (10.0005, 0.0005), (10, 10), (0, 10)], closed=True, stroke=K)
    assert len(tiny.shape.exterior.coords) == 6


def test_closing_a_contour_with_a_repeated_first_point_still_needs_three_different_points():
    with pytest.raises(EditError, match="tres puntos distintos"):
        make_polyline([(0, 0), (10, 0), (0, 0)], closed=True, stroke=K)
    tri = make_polyline([(0, 0), (10, 0), (10, 10), (0, 0)], closed=True, stroke=K)         # el cierre escrito a mano
    assert len(tri.shape.exterior.coords) == 4 and tri.shape.area == pytest.approx(50)


def test_a_very_long_thin_ellipse_gets_its_points_from_the_long_axis():
    e = make_ellipse(0, 0, 500, 1, stroke=K)
    assert e.shape.area / (math.pi * 500 * 1) == pytest.approx(1.0, abs=5e-5)


def test_the_semi_axes_of_an_ellipse_must_be_positive():
    for rx, ry in ((10, -5), (-10, 5), (0, 5), (10, 0)):
        with pytest.raises(EditError, match="mayor que cero"):
            make_ellipse(0, 0, rx, ry, stroke=K)


def test_the_largest_array_is_allowed_and_one_copy_more_is_refused():
    one = [circle(0, 0, 1)]
    res = apply_edit(one, de.array_edit([0], 3, 1667, 10, 10))                      # 5001 posiciones = 5000 copias
    assert len(res.shapes) == 5001
    with pytest.raises(EditError, match="el maximo"):
        apply_edit(one, de.array_edit([0], 3, 1668, 10, 10))
    two = [circle(0, 0, 1), circle(5, 5, 1)]
    assert len(apply_edit(two, de.array_edit([0, 1], 2501, 1, 20, 0)).shapes) == 2 + 5000
    with pytest.raises(EditError, match="el maximo"):
        apply_edit(two, de.array_edit([0, 1], 2502, 1, 20, 0))


def test_copies_are_new_shapes_without_the_id_of_the_original_but_keep_the_rest_of_its_style():
    src = replace(rect(stroke=R, fill="#00ff00"), id="pieza", layer="capa1")
    res = apply_edit([src, circle(500, 0)], de.array_edit([0], 2, 1, 150, 0))
    copy = res.shapes[2]
    assert res.shapes[0].id == "pieza" and copy.id is None
    assert (copy.stroke, copy.fill, copy.layer, copy.kind) == (R, "#00ff00", "capa1", "rect")


def test_a_boolean_result_is_new_but_keeps_stroke_fill_and_layer_of_the_shape_it_comes_from():
    a = replace(rect(0, 0, 50, 50, stroke=R, fill="#00ff00"), id="a", layer="capa1")
    b = replace(rect(25, 25, 50, 50, stroke="#0000ff", fill="#ffff00"), id="b", layer="capa2")
    for edit in (de.union_edit([0, 1]), de.intersect_edit([0, 1]), de.subtract_edit([0, 1])):
        res = apply_edit([a, b], edit)
        assert len(res.shapes) == 1
        s = res.shapes[0]
        assert (s.stroke, s.fill, s.layer, s.id, s.kind) == (R, "#00ff00", "capa1", None, "path")
    res = apply_edit([a, b], de.subtract_edit([0, 1], base="ultima"))                       # ahora el material es b
    assert (res.shapes[0].stroke, res.shapes[0].layer) == ("#0000ff", "capa2")
    res = apply_edit([a, b], de.offset_edit([1], 2))
    assert (res.shapes[-1].stroke, res.shapes[-1].fill, res.shapes[-1].layer, res.shapes[-1].id) == ("#0000ff", "#ffff00", "capa2", None)


def test_every_polygon_that_an_operation_produces_is_counter_clockwise_holes_included():
    big, hole = rect(0, 0, 100, 100), circle(50, 50, 10)
    results = [apply_edit([big, hole], de.subtract_edit([0, 1])), apply_edit([big, hole], de.union_edit([0, 1])),
               apply_edit([big], de.offset_edit([0], 5)), apply_edit([big], de.offset_edit([0], -5)),
               apply_edit([big, rect(50, 50, 100, 100)], de.intersect_edit([0, 1]))]
    for res in results:
        assert res.shapes and all(s.shape.exterior.is_ccw for s in res.shapes)


def test_an_intersection_that_touches_along_an_edge_keeps_only_the_area():
    a = rect(0, 0, 10, 10)
    c = ImportedShape(Polygon([(2, 2), (10, 2), (10, 0), (15, 0), (15, 10), (10, 10), (10, 8), (2, 8)]), True, K)
    assert a.shape.intersection(c.shape).geom_type == "GeometryCollection"             # area y un trozo de linea
    res = apply_edit([a, c], de.intersect_edit([0, 1]))
    assert len(res.shapes) == 1 and res.shapes[0].shape.area == pytest.approx(48)


def test_the_arcs_of_an_inward_offset_keep_the_same_precision_as_an_outward_one():
    ell = ImportedShape(Polygon([(0, 0), (300, 0), (300, 100), (100, 100), (100, 300), (0, 300)]), True, K, kind="polygon")
    for d, corner in ((-20, (100, 100)), (20, (300, 0))):            # hacia dentro se redondea el codo rehundido; hacia fuera, una esquina
        res = apply_edit([ell], de.offset_edit([0], d))
        arc = [p for s in res.shapes for p in s.shape.exterior.coords if abs(math.dist(p, corner) - 20) < 1e-6]
        assert len(arc) >= arc_segments(20) + 1, d                                       # un cuarto de circulo de 20 mm: 36 tramos


def test_boolean_operations_refuse_a_shape_that_is_not_a_closed_polygon_whatever_its_flags_say():
    a = rect()
    ghost_line = ImportedShape(LineString([(0, 0), (10, 10)]), True, K)                  # dice estar cerrada y no es un poligono
    ghost_poly = ImportedShape(box(0, 0, 5, 5), False, K)                                # al reves
    for other in (ghost_line, ghost_poly):
        for edit in (de.union_edit([0, 1]), de.subtract_edit([0, 1]), de.intersect_edit([0, 1])):
            with pytest.raises(EditError, match="solo vale con formas cerradas"):
                apply_edit([a, other], edit)


def test_closing_skips_a_shape_that_cannot_be_a_line_instead_of_failing():
    a = rect()
    ghost_poly = ImportedShape(box(0, 0, 5, 5), False, K)                                # dice ser una linea abierta y es un poligono
    with pytest.raises(EditError, match="ninguna de las formas elegidas"):
        apply_edit([a, ghost_poly], de.close_edit([0, 1]))
