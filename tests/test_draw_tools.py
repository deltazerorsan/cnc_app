"""Nucleo de las herramientas de raton: iman, restricciones, redondeo, herramientas en marcha y medidas (sin pantalla)."""
import math

import pytest
from shapely.geometry import LineString, MultiLineString, Polygon

from cnc import drawing_edit as de
from cnc.draw_tools import (CONSTRAINT, KEYS, KINDS, LABELS, NEEDS, TOOLS, Done, Draft, Options, Session, SnapMap,
                            SnapPoint, Spot, angle_of, box_points, constrain, coord_text, fmt, free_value, resolve,
                            shape_snaps)
from cnc.svg_import import ImportedShape

K = "#000000"
OPTS = Options()


def rect(x=0, y=0, w=50, h=30, r=0.0):
    return de.make_rectangle(x, y, w, h, r, stroke=K)


def kinds_at(shape, x, y):
    return sorted(k for px, py, k in shape_snaps(shape) if abs(px - x) < 1e-6 and abs(py - y) < 1e-6)


def regular(n, radius=10.0, kind="polygon"):
    pts = [(radius * math.cos(2 * math.pi * k / n), radius * math.sin(2 * math.pi * k / n)) for k in range(n)]
    return ImportedShape(Polygon(pts), True, K, kind=kind)


# --- numeros y textos ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value,text", [(50, "50"), (12.3456, "12.35"), (0.5, "0.5"), (100.0, "100"), (10, "10"), (0, "0"),
                                        (-0.001, "0"), (-7.25, "-7.25"), (3.10, "3.1"), (1000, "1000")])
def test_numbers_are_shown_with_at_most_two_decimals_and_no_spare_zeros(value, text):
    assert fmt(value) == text


@pytest.mark.parametrize("b,deg", [((1, 0), 0), ((0, 1), 90), ((-1, 0), 180), ((0, -1), 270), ((1, 1), 45), ((1, -1), 315),
                                   ((-1, -1), 225)])
def test_angles_run_counterclockwise_from_the_right_between_0_and_360(b, deg):
    assert angle_of((0, 0), b) == pytest.approx(deg)


def test_angles_are_measured_from_the_first_point():
    assert angle_of((5, 5), (5, 9)) == pytest.approx(90)


def test_the_cursor_text_gives_the_position_and_the_kind_of_snap():
    assert coord_text(Spot(12.3456, 7)) == "X 12.35  Y 7"
    assert coord_text(Spot(1, 2, "esquina")) == "X 1  Y 2 · esquina"


def test_the_tool_tables_are_consistent():
    assert set(KEYS.values()) == set(TOOLS) == set(LABELS)
    assert set(NEEDS) == set(TOOLS) - {"elegir"}
    assert set(CONSTRAINT) <= set(NEEDS)
    assert all(len(k) == 1 for k in KEYS)


# --- opciones -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [dict(radius=-1), dict(grid=-0.5), dict(slot_width=0), dict(slot_width=-3), dict(sides=2),
                                 dict(sides=3.5), dict(sides=0)])
def test_options_that_make_no_sense_are_refused(bad):
    with pytest.raises(ValueError):
        Options(**bad)


def test_default_options_are_valid_and_the_limits_are_allowed():
    o = Options()
    assert (o.radius, o.sides, o.slot_width, o.grid, o.snap) == (0.0, 6, 8.0, 0.0, True)
    Options(radius=0, grid=0, slot_width=0.01, sides=3)


# --- puntos de iman de una forma ----------------------------------------------------------------------------------
def test_a_rectangle_offers_its_corners_the_middle_of_each_side_and_its_centre():
    snaps = shape_snaps(rect())
    assert sorted(k for *_, k in snaps) == ["centro"] + ["esquina"] * 4 + ["medio"] * 4
    assert {(x, y) for x, y, k in snaps if k == "esquina"} == {(0, 0), (50, 0), (50, 30), (0, 30)}
    assert {(x, y) for x, y, k in snaps if k == "medio"} == {(25, 0), (50, 15), (25, 30), (0, 15)}
    assert [(x, y) for x, y, k in snaps if k == "centro"] == [(25, 15)]


def test_a_rounded_rectangle_keeps_the_corners_and_sides_of_its_box():
    r = rect(r=5)
    assert kinds_at(r, 0, 0) == ["esquina"] and kinds_at(r, 50, 30) == ["esquina"]
    assert kinds_at(r, 25, 0) == ["medio"] and kinds_at(r, 0, 15) == ["medio"]
    assert not any(k == "esquina" and (x, y) not in {(0, 0), (50, 0), (50, 30), (0, 30)} for x, y, k in shape_snaps(r))


def test_a_circle_offers_its_centre_and_four_quadrants_but_no_vertices():
    snaps = shape_snaps(de.make_circle(100, 50, 10, stroke=K))
    assert sorted(snaps) == sorted([(100, 50, "centro"), (90, 50, "cuadrante"), (110, 50, "cuadrante"),
                                    (100, 40, "cuadrante"), (100, 60, "cuadrante")])


def test_a_turned_ellipse_offers_the_points_where_it_touches_its_box():
    e = de.make_ellipse(0, 0, 20, 5, 30, stroke=K)
    quad = [(x, y) for x, y, k in shape_snaps(e) if k == "cuadrante"]
    xs, ys = sorted(x for x, _ in quad), sorted(y for _, y in quad)
    b = e.shape.bounds
    assert xs[0] == pytest.approx(b[0], abs=1e-6) and xs[-1] == pytest.approx(b[2], abs=1e-6)
    assert ys[0] == pytest.approx(b[1], abs=1e-6) and ys[-1] == pytest.approx(b[3], abs=1e-6)
    assert len(quad) == 4 and all(k in ("centro", "cuadrante") for *_, k in shape_snaps(e))


def test_a_regular_polygon_has_corners_and_side_middles_and_a_centre():
    snaps = shape_snaps(regular(6))
    assert sorted(k for *_, k in snaps) == ["centro"] + ["esquina"] * 6 + ["medio"] * 6
    assert any(abs(x - 10) < 1e-5 and abs(y) < 1e-5 for x, y, k in snaps if k == "esquina")
    assert any(abs(x - 7.5) < 1e-5 and abs(y - 10 * math.sin(math.pi / 3) / 2) < 1e-5 for x, y, k in snaps if k == "medio")


def test_a_curve_is_not_made_of_corners_but_a_coarse_polygon_is():
    assert [k for *_, k in shape_snaps(regular(40))] == ["centro"]                 # 9 grados por vertice: es una curva
    assert sorted(set(k for *_, k in shape_snaps(regular(20)))) == ["centro", "esquina", "medio"]       # 18 grados: esquinas


def test_an_open_line_offers_both_ends_its_turns_and_the_middle_of_straight_runs():
    line = ImportedShape(LineString([(0, 0), (10, 0), (10, 10), (20, 10)]), False, K, kind="polyline")
    snaps = shape_snaps(line)
    assert sorted((x, y) for x, y, k in snaps if k == "esquina") == [(0, 0), (10, 0), (10, 10), (20, 10)]
    assert sorted((x, y) for x, y, k in snaps if k == "medio") == [(5, 0), (10, 5), (15, 10)]
    assert not any(k == "centro" for *_, k in snaps)


def test_a_vertex_in_the_middle_of_a_straight_line_is_not_a_corner():
    line = ImportedShape(LineString([(0, 0), (5, 0), (10, 0)]), False, K, kind="polyline")
    assert sorted((x, y) for x, y, k in shape_snaps(line) if k == "esquina") == [(0, 0), (10, 0)]
    assert [(x, y) for x, y, k in shape_snaps(line) if k == "medio"] == []        # no hay lado entre dos esquinas seguidas


def test_a_two_point_line_has_two_ends_and_a_middle():
    line = de.make_polyline([(0, 0), (10, 0)], stroke=K)
    assert sorted(shape_snaps(line)) == [(0, 0, "esquina"), (5, 0, "medio"), (10, 0, "esquina")]


def test_a_hole_in_a_shape_has_its_own_corners():
    poly = Polygon([(0, 0), (40, 0), (40, 40), (0, 40)], [[(10, 10), (30, 10), (30, 30), (10, 30)]])
    snaps = shape_snaps(ImportedShape(poly, True, K, kind="path"))
    assert {(x, y) for x, y, k in snaps if k == "esquina"} >= {(10, 10), (30, 30), (0, 0), (40, 40)}


def test_pieces_of_a_multi_part_geometry_all_count():
    multi = MultiLineString([[(0, 0), (1, 0)], [(5, 5), (6, 5)]])
    snaps = shape_snaps(ImportedShape(multi, False, K, kind="path"))
    assert {(x, y) for x, y, k in snaps if k == "esquina"} == {(0, 0), (1, 0), (5, 5), (6, 5)}


def test_empty_geometry_has_no_snap_points():
    assert shape_snaps(ImportedShape(Polygon(), True, K)) == []
    assert shape_snaps(ImportedShape(LineString(), False, K)) == []


def test_a_shape_full_of_corners_offers_the_corners_but_no_middles():
    pts = [((10 if k % 2 else 4) * math.cos(2 * math.pi * k / 1000), (10 if k % 2 else 4) * math.sin(2 * math.pi * k / 1000))
           for k in range(1000)]
    snaps = shape_snaps(ImportedShape(Polygon(pts), True, K, kind="path"))
    assert sum(k == "esquina" for *_, k in snaps) == 1000
    assert not any(k == "medio" for *_, k in snaps)


def test_a_smooth_path_with_the_same_point_listed_twice_does_not_repeat_it():
    poly = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    snaps = shape_snaps(ImportedShape(poly, True, K, kind="rect"))
    assert len(snaps) == len(set(snaps))


def test_the_box_handles_are_nine_points():
    pts = box_points((0, 0, 10, 4))
    assert len(pts) == 9 and all(p.kind == "caja" for p in pts)
    assert {(p.x, p.y) for p in pts} == {(x, y) for x in (0, 5, 10) for y in (0, 2, 4)}


# --- el mapa de iman ----------------------------------------------------------------------------------------------
def snap_map(*shapes):
    m = SnapMap()
    m.update(list(shapes))
    return m


def test_the_map_always_has_the_origin_even_with_no_shapes():
    m = snap_map()
    assert len(m) == 1
    assert m.nearest(0.5, -0.5, 2) == SnapPoint(0.0, 0.0, "cero", -1)


def test_the_nearest_point_is_found_and_says_which_shape_it_belongs_to():
    m = snap_map(rect(), rect(100, 0, 20, 20))
    hit = m.nearest(101, 1, 3)
    assert (hit.x, hit.y, hit.kind, hit.shape) == (100, 0, "esquina", 1)
    hit = m.nearest(24, 14, 3)
    assert (hit.x, hit.y, hit.kind, hit.shape) == (25, 15, "centro", 0)


def test_nothing_is_found_beyond_the_radius_and_the_radius_itself_counts():
    m = snap_map(rect())
    assert m.nearest(53, 0, 2.9) is None
    assert m.nearest(53, 0, 3.0).x == 50
    assert m.nearest(53, 0, 3.1).x == 50


@pytest.mark.parametrize("radius", [0, -1, float("nan")])
def test_a_radius_that_is_not_positive_finds_nothing(radius):
    assert snap_map(rect()).nearest(0, 0, radius) is None


def test_the_closest_of_several_wins():
    m = snap_map(rect(0, 0, 4, 4))
    hit = m.nearest(1.0, 0.2, 5)
    assert (hit.x, hit.y) == (0, 0)
    hit = m.nearest(3.0, 0.2, 5)
    assert (hit.x, hit.y) == (4, 0)


def test_at_equal_distance_the_more_important_kind_wins():
    a = rect(0, 0, 10, 10)                       # su esquina (10, 10)
    b = ImportedShape(Polygon([(0, 0), (20, 0), (20, 20), (0, 20)]), True, K, kind="path")      # su centro (10, 10)
    m = snap_map(b, a)
    hit = m.nearest(10, 10, 1)
    assert (hit.x, hit.y, hit.kind) == (10, 10, "esquina")
    assert snap_map(rect(-5, -5, 5, 5)).nearest(0, 0, 1).kind == "esquina"          # la esquina gana al cero del dibujo


def test_shapes_can_be_left_out_of_the_search():
    m = snap_map(rect(), rect(100, 0, 20, 20))
    assert m.nearest(0, 0, 3, exclude={0}).kind == "cero"
    assert m.nearest(100, 0, 3, exclude={0}).shape == 1
    assert m.nearest(100, 0, 3, exclude={1}) is None
    assert m.nearest(100, 0, 3, exclude=[]).shape == 1
    assert m.nearest(0, 0, 3, exclude=frozenset({1})).shape == 0


def test_extra_points_count_next_to_the_shapes_and_can_be_the_only_ones():
    m = snap_map(rect())
    start = SnapPoint(10, 10, "inicio")
    assert m.nearest(10.5, 10.5, 2, extra=[start]) == start
    assert m.nearest(10.5, 10.5, 2) is None
    assert m.nearest(50.5, 0.5, 2, extra=[start]).kind == "esquina"
    assert m.nearest(0.5, 0.5, 2, extra=[start], main=False) is None
    assert m.nearest(10.5, 10.5, 2, extra=[start], main=False) == start
    assert m.nearest(60, 60, 2, extra=[start]) is None


def test_an_extra_point_closer_than_a_shape_point_wins_and_at_equal_distance_the_better_kind():
    m = snap_map(rect())
    near = SnapPoint(50.2, 0.0, "inicio")
    assert m.nearest(50.3, 0, 2, extra=[near]) == near
    assert m.nearest(50.0, 0, 2, extra=[SnapPoint(50, 0, "caja")]).kind == "esquina"
    assert m.nearest(50.0, 0, 2, extra=[SnapPoint(50, 0, "inicio")]).kind == "esquina"      # mismo rango: la forma, que va primero


def test_updating_with_the_same_shapes_keeps_what_was_computed_and_new_ones_are_added():
    a, b = rect(), rect(100, 0, 20, 20)
    m = SnapMap()
    m.update([a])
    first = m._cache[(id(a.shape), a.kind)]
    m.update([a, b])
    assert m._cache[(id(a.shape), a.kind)] is first
    assert len(m._cache) == 2 and m.nearest(100, 0, 1).shape == 1
    m.update([b])
    assert len(m._cache) == 1 and m.nearest(0.5, 0.5, 1).kind == "cero"
    m.update([b])
    assert m.nearest(100, 0, 1).shape == 0


def test_a_shape_that_changes_is_looked_at_again():
    m = SnapMap()
    m.update([rect()])
    assert m.nearest(50, 0, 1).kind == "esquina"
    m.update([rect(0, 0, 60, 30)])
    assert m.nearest(50, 0, 1) is None                         # la esquina de antes ya no esta
    assert m.nearest(60, 0, 1).kind == "esquina"
    assert m.nearest(30, 0, 1).kind == "medio"


def test_the_same_geometry_with_another_kind_is_not_mixed_up():
    poly = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    m = SnapMap()
    m.update([ImportedShape(poly, True, K, kind="path")])
    assert m.nearest(5, 0, 0.5).kind == "medio"
    m.update([ImportedShape(poly, True, K, kind="circle")])
    assert m.nearest(5, 0, 0.5) is None
    assert m.nearest(5, 5, 0.5).kind == "centro"


def test_a_big_drawing_is_searched_quickly():
    shapes = [rect(i * 12, j * 12, 10, 10) for i in range(40) for j in range(40)]
    m = snap_map(*shapes)
    assert len(m) == 1 + 1600 * 9
    hit = m.nearest(12 * 20 + 0.2, 12 * 15 - 0.1, 1)
    assert (hit.x, hit.y, hit.kind) == (240, 180, "esquina")


# --- restricciones y redondeo ---------------------------------------------------------------------------------------
def test_a_square_takes_the_longer_side_with_the_signs_of_the_cursor():
    assert constrain("rectangulo", (0, 0), (30, 10)) == (30, 30)
    assert constrain("rectangulo", (0, 0), (10, 30)) == (30, 30)
    assert constrain("rectangulo", (0, 0), (-30, 10)) == (-30, 30)
    assert constrain("elipse", (0, 0), (10, -30)) == (30, -30)
    assert constrain("rectangulo", (0, 0), (-10, -30)) == (-30, -30)
    assert constrain("rectangulo", (5, 7), (45, 10)) == (45, 47)
    assert constrain("rectangulo", (0, 0), (0, 12)) == (12, 12)           # sin lado en X se va a la derecha
    assert constrain("rectangulo", (0, 0), (12, 0)) == (12, 12)           # y sin lado en Y, hacia arriba
    assert constrain("rectangulo", (3, 3), (3, 3)) == (3, 3)


def test_a_square_is_rounded_to_hundredths():
    x, y = constrain("rectangulo", (0, 0), (10.126, 3))
    assert (x, y) == (10.13, 10.13)


def test_directions_snap_to_45_degrees_and_keep_the_length_rounded_to_hundredths():
    x, y = constrain("linea", (0, 0), (10, 3.5))
    assert (x, y) == (10.59, 0.0)
    x, y = constrain("linea", (0, 0), (10, 9))
    assert x == pytest.approx(y) and math.hypot(x, y) == pytest.approx(13.45)
    assert angle_of((0, 0), (x, y)) == pytest.approx(45)
    x, y = constrain("linea", (0, 0), (-10, 9.5))
    assert angle_of((0, 0), (x, y)) == pytest.approx(135)
    x, y = constrain("linea", (0, 0), (-3, -10))
    assert angle_of((0, 0), (x, y)) == pytest.approx(270)


def test_a_vertical_or_horizontal_constraint_is_exact_even_from_an_awkward_origin():
    ox, oy = 12.3456789, -7.654321
    x, y = constrain("linea", (ox, oy), (ox + 0.3, oy + 20))
    assert x == ox and y == pytest.approx(oy + 20)
    x, y = constrain("ranura", (ox, oy), (ox - 25, oy + 0.4))
    assert y == oy and x == pytest.approx(ox - 25)


def test_the_polygon_turns_in_steps_of_15_degrees_and_move_and_slot_in_steps_of_45():
    x, y = constrain("poligono", (0, 0), (10, 3))
    assert angle_of((0, 0), (x, y)) == pytest.approx(15)
    x, y = constrain("poligono", (0, 0), (10, 1))
    assert y == 0 and x == 10.05
    x, y = constrain("mover", (0, 0), (10, 3))
    assert (x, y) == (10.44, 0.0)
    assert constrain("linea", (4, 4), (4, 4)) == (4, 4)


def test_a_free_value_goes_to_the_grid_or_to_hundredths():
    assert free_value(12.3456) == 12.35
    assert free_value(12.3456, 0) == 12.35
    assert free_value(12.4, 5) == 10
    assert free_value(12.6, 5) == 15
    assert free_value(0.3, 0.1) == 0.3
    assert free_value(-12.6, 5) == -15
    assert free_value(7.0004, 0.5) == 7.0


# --- donde cae el cursor --------------------------------------------------------------------------------------------
def test_a_point_near_a_snap_point_lands_on_it_and_a_far_one_is_rounded():
    m = snap_map(rect())
    s = resolve("rectangulo", (50.4, 0.3), None, OPTS, m, 2)
    assert s == Spot(50, 0, "esquina") and s.xy == (50, 0)
    assert resolve("rectangulo", (20.126, 7.001), None, OPTS, m, 2) == Spot(20.13, 7.0, "")


def test_shift_constrains_from_the_start_point_and_ignores_the_snap():
    m = snap_map(rect())
    s = resolve("linea", (50.1, 0.6), (40, 0), OPTS, m, 5, shift=True)
    assert s.kind == "" and s.y == 0 and s.x == pytest.approx(50.12)
    s = resolve("rectangulo", (30, 8), (0, 0), OPTS, m, 5, shift=True)
    assert (s.x, s.y, s.kind) == (30, 30, "")


def test_shift_does_nothing_without_a_start_point_or_in_a_tool_with_no_constraint():
    m = snap_map(rect())
    assert resolve("linea", (50.2, 0.2), None, OPTS, m, 2, shift=True) == Spot(50, 0, "esquina")
    assert resolve("circulo", (50.2, 0.2), (0, 0), OPTS, m, 2, shift=True) == Spot(50, 0, "esquina")


def test_ctrl_turns_the_snap_off_but_still_rounds():
    m = snap_map(rect())
    assert resolve("linea", (50.2, 0.2), None, OPTS, m, 2, ctrl=True) == Spot(50.2, 0.2)
    assert resolve("linea", (50.2, 0.2), (0, 0), OPTS, m, 2, ctrl=True, extra=[SnapPoint(50.2, 0.2, "inicio")]) == Spot(50.2, 0.2)


def test_with_the_snap_option_off_only_the_extra_points_still_catch_the_cursor():
    m = snap_map(rect())
    off = Options(snap=False)
    assert resolve("linea", (50.2, 0.2), None, off, m, 2) == Spot(50.2, 0.2)
    s = resolve("linea", (10.3, 10.2), (0, 0), off, m, 2, extra=[SnapPoint(10, 10, "inicio")])
    assert s == Spot(10, 10, "inicio")


def test_the_grid_rounds_free_points_but_the_snap_still_wins_near_a_point():
    m = snap_map(rect())
    grid = Options(grid=5)
    assert resolve("linea", (22.4, 12.6), None, grid, m, 1) == Spot(20, 15)
    assert resolve("linea", (49.5, 0.5), None, grid, m, 2) == Spot(50, 0, "esquina")


def test_excluded_shapes_do_not_attract_the_cursor():
    m = snap_map(rect(), rect(100, 0, 20, 20))
    assert resolve("mover", (50.2, 0.2), (0, 0), OPTS, m, 2, exclude={0}) == Spot(50.2, 0.2)
    assert resolve("mover", (100.2, 0.2), (0, 0), OPTS, m, 2, exclude={0}) == Spot(100, 0, "esquina")


# --- una herramienta en marcha: rectangulo y compañia ---------------------------------------------------------------------
def test_an_unknown_tool_is_refused():
    with pytest.raises(ValueError):
        Session("elegir")
    with pytest.raises(ValueError):
        Session("taladro")


def test_a_session_starts_idle_and_gives_the_first_point_without_a_result():
    s = Session("rectangulo")
    assert not s.busy and s.origin is None and s.points == []
    assert s.add((1, 2)) is None
    assert s.busy and s.origin == (1, 2)


def test_two_corners_make_a_rectangle_in_any_order_and_the_session_starts_over():
    for a, b in (((10, 20), (60, 50)), ((60, 50), (10, 20)), ((10, 50), (60, 20)), ((60, 20), (10, 50))):
        s = Session("rectangulo", stroke="#ff0000")
        assert s.add(a) is None
        done = s.add(b)
        assert isinstance(done, Done) and done.kind == "forma"
        assert done.shape.shape.bounds == (10, 20, 60, 50)
        assert done.shape.kind == "rect" and done.shape.stroke == "#ff0000" and done.shape.closed
        assert not s.busy
    done = s.add((0, 0)) or s.add((5, 5))
    assert done.shape.shape.bounds == (0, 0, 5, 5)                       # la misma sesion sirve para otro


def test_the_rectangle_radius_is_used_and_cannot_pass_half_the_short_side():
    plain = Session("rectangulo", Options())
    plain.add((0, 0))
    area = plain.add((20, 10)).shape.shape.area
    s = Session("rectangulo", Options(radius=3))
    s.add((0, 0))
    rounded = s.add((20, 10)).shape.shape
    assert rounded.area < area and rounded.bounds == pytest.approx((0, 0, 20, 10))
    assert not any(abs(x) < 1e-9 and abs(y) < 1e-9 for x, y in rounded.exterior.coords)       # sin la esquina viva
    s = Session("rectangulo", Options(radius=100))
    s.add((0, 0))
    big = s.add((20, 10)).shape.shape                                      # mide 10 de alto: el radio queda en 5
    assert big.bounds == pytest.approx((0, 0, 20, 10)) and big.area == pytest.approx(20 * 10 - (4 - math.pi) * 25, rel=1e-3)
    s = Session("rectangulo", Options(radius=100))
    s.add((0, 0))
    assert s.add((6, 40)).shape.shape.bounds == pytest.approx((0, 0, 6, 40))      # radio 3, el ancho manda


def test_a_second_point_in_the_same_place_is_refused_and_the_first_one_stays():
    s = Session("rectangulo")
    s.add((5, 5))
    with pytest.raises(de.EditError):
        s.add((5, 5))
    assert s.points == [(5, 5)]
    with pytest.raises(de.EditError):
        s.add((5 + 5e-7, 5))
    assert s.points == [(5, 5)]
    assert s.add((15, 15)).kind == "forma"


def test_a_rectangle_with_no_height_is_refused_with_its_reason_and_the_session_survives():
    s = Session("rectangulo")
    s.add((0, 0))
    with pytest.raises(de.EditError):
        s.add((10, 0))
    assert s.points == [(0, 0)] and s.busy
    assert s.add((10, 4)).shape.shape.bounds == (0, 0, 10, 4)


def test_a_circle_has_its_centre_in_the_first_point_and_the_radius_to_the_second():
    s = Session("circulo")
    s.add((10, 20))
    c = s.add((13, 24)).shape
    assert c.kind == "circle" and c.shape.bounds == pytest.approx((5, 15, 15, 25), abs=1e-3)


def test_an_ellipse_fills_the_box_between_the_two_points():
    s = Session("elipse")
    s.add((10, 20))
    e = s.add((50, 30)).shape
    assert e.kind == "ellipse" and e.shape.bounds == pytest.approx((10, 20, 50, 30), abs=1e-6)
    s.add((50, 30))
    assert s.add((10, 20)).shape.shape.bounds == pytest.approx((10, 20, 50, 30), abs=1e-6)


def test_a_polygon_takes_its_centre_the_radius_and_the_first_vertex_from_the_points():
    s = Session("poligono", Options(sides=5))
    s.add((10, 10))
    p = s.add((10, 20)).shape
    assert p.kind == "polygon" and len(p.shape.exterior.coords) == 6
    first = p.shape.exterior.coords[0]
    assert math.dist(first, (10, 20)) < 1e-6
    assert p.shape.centroid.x == pytest.approx(10, abs=1e-6)
    assert all(math.dist(c, (10, 10)) == pytest.approx(10) for c in p.shape.exterior.coords)
    s = Session("poligono", Options(sides=4))
    s.add((0, 0))
    sq = s.add((10, 0)).shape.shape                                       # un vertice a la derecha: un rombo
    assert sq.bounds == pytest.approx((-10, -10, 10, 10), abs=1e-6)
    s.add((0, 0))
    sq = s.add((7.0710678, 7.0710678)).shape.shape                         # a 45 grados: un cuadrado con lados rectos
    assert sq.bounds == pytest.approx((-7.0710678, -7.0710678, 7.0710678, 7.0710678), abs=1e-5)


def test_a_slot_joins_two_centres_with_the_width_of_the_options():
    s = Session("ranura", Options(slot_width=6))
    s.add((0, 0))
    slot = s.add((20, 0)).shape
    assert slot.shape.bounds == pytest.approx((-3, -3, 23, 3), abs=1e-6)


def test_the_move_gives_the_difference_between_the_two_points_and_the_selection():
    s = Session("mover", selection=[7, 3, 5])
    assert s.add((10, 10)) is None
    done = s.add((13, 6))
    assert done == Done("mover", None, 3, -4, (3, 5, 7))
    assert not s.busy
    s.add((1, 1))
    with pytest.raises(de.EditError):
        s.add((1, 1))


# --- lineas ----------------------------------------------------------------------------------------------------------------
def line_session(*pts, **kw):
    s = Session("linea", **kw)
    for p in pts:
        assert s.add(p) is None
    return s


def test_a_line_keeps_taking_points_and_ends_open_with_finish():
    s = line_session((0, 0), (10, 0), (10, 10))
    assert s.busy and s.origin == (10, 10) and s.points == [(0, 0), (10, 0), (10, 10)]
    done = s.finish()
    assert done.kind == "forma" and done.shape.kind == "polyline" and not done.shape.closed
    assert list(done.shape.shape.coords) == [(0, 0), (10, 0), (10, 10)]
    assert not s.busy


def test_two_points_are_a_line():
    s = line_session((0, 0), (10, 5))
    assert s.finish().shape.kind == "line"


def test_clicking_the_start_with_three_points_or_more_closes_the_contour():
    s = line_session((0, 0), (10, 0), (10, 10))
    done = s.add((0, 0))
    assert done.shape.closed and done.shape.kind == "polygon" and done.shape.shape.area == pytest.approx(50)
    assert not s.busy
    s = line_session((0, 0), (10, 0))
    assert s.add((0, 0)) is None                                           # con dos puntos no hay contorno que cerrar
    assert s.points == [(0, 0), (10, 0), (0, 0)]


def test_the_same_point_twice_in_a_row_is_ignored_so_a_double_click_adds_one_point():
    s = line_session((0, 0), (10, 0))
    assert s.add((10, 0)) is None and s.points == [(0, 0), (10, 0)]
    assert s.add((10 + 5e-7, 0)) is None and s.points == [(0, 0), (10, 0)]
    assert s.add((10.001, 0)) is None and len(s.points) == 3


def test_a_contour_that_crosses_itself_is_refused_and_can_be_fixed_with_back():
    s = line_session((0, 0), (10, 10), (10, 0), (0, 10))
    with pytest.raises(de.EditError):
        s.add((0, 0))
    assert len(s.points) == 4
    assert s.back() and s.points == [(0, 0), (10, 10), (10, 0)]
    assert s.add((0, 0)).shape.closed


def test_finish_needs_two_points_and_does_nothing_when_idle_or_in_other_tools():
    assert Session("linea").finish() is None
    s = line_session((0, 0))
    with pytest.raises(de.EditError):
        s.finish()
    assert s.points == [(0, 0)]
    r = Session("rectangulo")
    r.add((0, 0))
    assert r.finish() is None and r.points == [(0, 0)]


def test_back_and_cancel():
    s = line_session((0, 0), (5, 5))
    assert s.back() and s.points == [(0, 0)]
    assert s.back() and not s.busy
    assert not s.back()
    s = line_session((0, 0), (5, 5))
    s.cancel()
    assert not s.busy and s.points == []
    r = Session("rectangulo")
    r.add((1, 1))
    r.cancel()
    assert not r.busy


def test_the_start_of_a_contour_is_a_snap_point_only_with_three_points():
    assert line_session((0, 0), (10, 0)).extras() == []
    assert line_session((0, 0), (10, 0), (10, 10)).extras() == [SnapPoint(0, 0, "inicio")]
    assert Session("rectangulo").extras() == []
    r = Session("rectangulo")
    r.add((0, 0))
    assert r.extras() == []


def test_the_hint_changes_once_a_point_is_given_and_always_says_how_to_leave():
    s = Session("rectangulo")
    first = s.hint()
    assert "Ctrl" in first and "Esc: salir" in first and "esquina" in first
    s.add((0, 0))
    second = s.hint()
    assert second != first and "Esc: cancelar" in second and "Mayús" in second
    assert all(Session(t).hint() for t in NEEDS)


# --- vista previa y medidas ----------------------------------------------------------------------------------------------
def test_nothing_is_previewed_before_the_first_point_or_on_the_same_spot():
    assert Session("rectangulo").draft((5, 5)) == Draft()
    s = Session("rectangulo")
    s.add((5, 5))
    assert s.draft((5, 5)) == Draft()
    assert s.draft((5 + 5e-7, 5)) == Draft()


def test_the_rectangle_preview_has_the_shape_and_its_measures():
    s = Session("rectangulo")
    s.add((10, 10))
    d = s.draft((60, 40))
    assert d.shape.shape.bounds == (10, 10, 60, 40) and d.text == "50 × 30 mm" and d.path == () and d.shift is None
    assert not d.closes
    s = Session("rectangulo", Options(radius=3))
    s.add((10, 10))
    assert s.draft((60, 40)).text == "50 × 30 mm · esquinas R3"
    assert Session("elipse").draft((1, 1)) == Draft()


def test_a_preview_with_no_valid_shape_still_has_its_measures():
    s = Session("rectangulo")
    s.add((0, 0))
    d = s.draft((10, 0))
    assert d.shape is None and d.text == "10 × 0 mm"
    s = Session("poligono", Options(sides=6))
    s.add((0, 0))
    assert s.draft((10, 0)).shape is not None


def test_the_circle_ellipse_polygon_and_slot_measures():
    s = Session("circulo")
    s.add((0, 0))
    assert s.draft((3, 4)).text == "Ø 10 mm · radio 5"
    assert s.draft((3, 4)).shape.kind == "circle"
    s = Session("elipse", Options(radius=3))                 # el radio de las esquinas es cosa del rectangulo
    s.add((0, 0))
    assert s.draft((40, 20)).text == "40 × 20 mm"
    assert not s.draft((40, 20)).text.count("esquinas")
    s = Session("poligono", Options(sides=6))
    s.add((0, 0))
    assert s.draft((0, 10)).text == "6 lados · radio 10 mm · entre caras 17.32 mm · giro 90°"
    s = Session("ranura", Options(slot_width=8))
    s.add((0, 0))
    d = s.draft((30, 30))
    assert d.text == f"largo total {fmt(math.hypot(30, 30) + 8)} mm · ancho 8 · 45°" and d.shape.kind == "path"


def test_the_move_preview_gives_the_offset_and_a_guide_line():
    s = Session("mover", selection=[0])
    s.add((10, 10))
    d = s.draft((7, 16))
    assert d.shift == (-3, 6) and d.path == ((10, 10), (7, 16)) and d.text == "ΔX -3 · ΔY 6 mm" and d.shape is None


def test_the_line_preview_follows_the_cursor_and_gives_length_angle_and_total():
    s = Session("linea")
    assert s.draft((1, 1)) == Draft()
    s.add((0, 0))
    d = s.draft((10, 0))
    assert d.path == ((0, 0), (10, 0)) and d.text == "10 mm · 0°" and not d.closes
    assert s.draft((0, 0)).path == ((0, 0),) and s.draft((0, 0)).text == ""
    s.add((10, 0))
    d = s.draft((10, 5))
    assert d.path == ((0, 0), (10, 0), (10, 5)) and d.text == "5 mm · 90° · total 15 mm"
    assert s.draft((10, 0)).path == ((0, 0), (10, 0)) and s.draft((10, 0)).text == "10 mm · 0°"


def test_the_line_preview_says_when_the_cursor_would_close_the_contour():
    s = line_session((0, 0), (10, 0), (10, 10))
    d = s.draft((0, 0))
    assert d.closes and d.text.endswith(" · cerrar") and d.path[-1] == (0, 0)
    assert not s.draft((0.5, 0)).closes
    s = line_session((0, 0), (10, 0))
    assert not s.draft((0, 0)).closes


# --- mas casos de las esquinas, los rangos y los mensajes ---------------------------------------------------------------
def test_a_turn_to_the_right_is_a_corner_too_and_so_is_a_clockwise_outline():
    right = ImportedShape(LineString([(0, 0), (10, 0), (10, -10)]), False, K, kind="polyline")
    assert sorted((x, y) for x, y, k in shape_snaps(right) if k == "esquina") == [(0, 0), (10, -10), (10, 0)]
    cw = ImportedShape(Polygon([(0, 0), (0, 10), (10, 10), (10, 0)]), True, K, kind="path")
    assert sorted((x, y) for x, y, k in shape_snaps(cw) if k == "esquina") == [(0, 0), (0, 10), (10, 0), (10, 10)]


def test_the_ends_of_an_open_line_are_corners_even_when_the_line_would_look_straight_if_closed():
    pts = [(0, 0), (10, 0), (10, 10), (-10, 0)]
    for line in (pts, pts[::-1]):
        snaps = shape_snaps(ImportedShape(LineString(line), False, K, kind="polyline"))
        assert {(x, y) for x, y, k in snaps if k == "esquina"} >= {line[0], line[-1]}


@pytest.mark.parametrize("kind", ["circle", "ellipse"])
def test_a_coarse_circle_or_ellipse_still_offers_no_vertices(kind):
    snaps = shape_snaps(regular(8, kind=kind))
    assert sorted(k for *_, k in snaps) == ["centro"] + ["cuadrante"] * 4


def jagged(n):
    return ImportedShape(Polygon([((10 if k % 2 else 4) * math.cos(2 * math.pi * k / n), (10 if k % 2 else 4) * math.sin(2 * math.pi * k / n))
                                  for k in range(n)]), True, K, kind="path")


def test_middles_are_offered_up_to_four_hundred_corners_and_not_beyond():
    assert sum(k == "medio" for *_, k in shape_snaps(jagged(400))) == 400
    assert sum(k == "medio" for *_, k in shape_snaps(jagged(402))) == 0
    assert sum(k == "esquina" for *_, k in shape_snaps(jagged(402))) == 402


def test_the_handles_of_a_box_lose_to_the_centre_of_a_shape_in_the_same_place():
    m = snap_map(rect())
    assert m.nearest(25, 15, 1, extra=box_points((0, 0, 50, 30))).kind == "centro"
    assert m.nearest(0, 0, 1, extra=box_points((0, 0, 50, 30))).kind == "esquina"
    assert m.nearest(60, 40, 1, extra=box_points((10, 10, 60, 40))).kind == "caja"


def test_finishing_a_line_with_one_point_says_that_two_are_needed():
    s = line_session((0, 0))
    with pytest.raises(de.EditError, match="al menos dos puntos"):
        s.finish()


def test_a_single_point_repeated_cannot_be_a_line():
    s = Session("linea")
    s.points = [(1, 1), (1, 1)]
    with pytest.raises(de.EditError, match="dos puntos distintos"):
        s.finish()


@pytest.mark.parametrize("n", [399, 400, 401, 402])
def test_middles_are_offered_exactly_while_there_are_at_most_four_hundred_corners(n):
    snaps = shape_snaps(jagged(n))
    corners = sum(k == "esquina" for *_, k in snaps)
    assert corners >= n - 1
    assert sum(k == "medio" for *_, k in snaps) == (corners if corners <= 400 else 0)
    assert (corners <= 400) == (n <= 400)


def test_an_extra_point_exactly_at_the_radius_counts_and_one_just_beyond_does_not():
    m = SnapMap()
    far = SnapPoint(3, 4, "inicio")
    assert m.nearest(0, 0, 5, extra=[far], main=False) == far
    assert m.nearest(0, 0, 4.999, extra=[far], main=False) is None


# --- cosas que las mutaciones encontraron sin cubrir -------------------------------------------------------------
def test_the_slot_and_the_line_snap_their_direction_to_45_degrees_not_15():
    for tool in ("ranura", "linea"):
        x, y = constrain(tool, (0, 0), (10, 4))                      # 21,8 grados: de 45 en 45 es 0, de 15 en 15 seria 15
        assert (x, y) == (10.77, 0.0), tool
        x, y = constrain(tool, (0, 0), (10, 8))                      # 38,7 grados: sube a 45
        assert angle_of((0, 0), (x, y)) == pytest.approx(45), tool


@pytest.mark.parametrize("deg,is_corner", [(12.0, True), (11.99, False), (12.01, True), (0.0, False), (-12.0, True)])
def test_a_turn_of_exactly_twelve_degrees_already_makes_a_corner(deg, is_corner):
    r = math.radians(deg)
    line = ImportedShape(LineString([(0, 0), (10, 0), (10 + 10 * math.cos(r), 10 * math.sin(r))]), False, K, kind="polyline")
    assert ("esquina" in kinds_at(line, 10, 0)) is is_corner


def test_a_quadrant_wins_over_a_middle_in_the_same_place():
    circle = de.make_circle(0, 0, 10, stroke=K)                              # su cuadrante (10, 0)
    line = de.make_polyline([(10, -10), (10, 10)], stroke=K)                # su punto medio (10, 0)
    m = snap_map(circle, line)
    assert m.nearest(10.5, 0.3, 2).kind == "cuadrante"
    assert snap_map(line, circle).nearest(10.5, 0.3, 2).kind == "cuadrante"        # y no depende del orden


def test_a_rounded_rectangle_still_offers_the_corners_and_middles_of_its_box():
    snaps = shape_snaps(rect(0, 0, 40, 20, r=5))
    for x, y in [(0, 0), (40, 0), (0, 20), (40, 20)]:
        assert (x, y, "esquina") in snaps, (x, y)
    for x, y in [(20, 0), (20, 20), (0, 10), (40, 10)]:
        assert (x, y, "medio") in snaps, (x, y)
    assert len([s for s in snaps if s[2] == "medio"]) >= 4


def test_the_nearest_of_a_shape_point_and_an_extra_point_wins_whichever_is_farther():
    m = snap_map(rect())                                                    # esquina de la forma en (50, 0)
    far = SnapPoint(52.0, 0.0, "inicio")
    assert m.nearest(50.5, 0, 3, extra=[far]).kind == "esquina"             # la de la forma esta a 0,5 y el extra a 1,5
    near = SnapPoint(50.2, 0.0, "inicio")
    assert m.nearest(50.5, 0, 3, extra=[near, far]) == near
    assert m.nearest(51.9, 0, 3, extra=[near, far]) == far                  # 0,1 contra 1,7 (la esquina) y 1,9 (el otro extra)


def test_a_move_of_less_than_a_micron_is_refused_like_a_point_in_the_same_place():
    s = Session("mover", Options(), K, (0,))
    s.add((3, 3))
    with pytest.raises(de.EditError):
        s.add((3 + 5e-7, 3))
    assert s.points == [(3, 3)] and s.busy
    done = s.add((3 + 5e-6, 3))
    assert done.kind == "mover" and done.dx == pytest.approx(5e-6)


def test_distance_decides_before_kind_a_nearer_origin_beats_a_farther_better_kind():
    m = snap_map(rect(100, 100, 50, 30))                           # solo el cero del dibujo (rango bajo) cerca del cursor
    far = SnapPoint(2.0, 0.0, "inicio")                            # un extra de la mejor clase, pero mas lejos
    hit = m.nearest(0.5, 0.0, 3, extra=[far])
    assert hit.kind == "cero" and (hit.x, hit.y) == (0, 0)
    assert m.nearest(1.9, 0.0, 3, extra=[far]) == far              # y a 0,1 del extra gana el extra
    same = SnapPoint(0.0, 0.0, "centro")                           # a la misma distancia si manda la clase
    assert m.nearest(0.5, 0.0, 3, extra=[same]).kind == "centro"
