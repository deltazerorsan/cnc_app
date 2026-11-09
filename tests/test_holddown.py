"""Sujecion de la plancha: zonas, comprobacion de un punto, propuesta y programa de marcas (sin Qt)."""
import functools
import math
import random
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from shapely.geometry import Point, box

from cnc import holddown
from cnc.holddown import (FASTENERS, GAP_TOL, HoldAnalysis, HoldError, HoldSettings, ROW_GAP, _corners, _spread, analyse_plan,
                          board_points, build_zones, grid_cells, grid_extra, grid_nodes, grid_shape, marks_gcode,
                          mark_order, order_marks)
from cnc.preview import OpPreview, preview_ops
from cnc.project import Project
from cnc.tools import load_tools

DATA = Path(__file__).parent / "data"
ROOT = Path(__file__).parent.parent
R = 2.0                      # radio de la fresa de las pruebas sinteticas


def rect_path(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]


def circle_path(cx, cy, r, n=64):
    return [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)] + [(cx + r, cy)]


def op(kind, *cuts, drills=()):
    return OpPreview(1, kind, cuts=[list(c) for c in cuts], drills=list(drills))


def board(w=400, h=300):
    return box(0, 0, w, h)


def piece_with_hole():
    """Una pieza rectangular con un hueco redondo dentro, en una plancha de 400 x 300."""
    return [op("pieza", rect_path(100, 80, 300, 220)), op("hueco", circle_path(200, 150, 20))]


def analysis(previews, settings=None, w=400, h=300):
    return HoldAnalysis(build_zones(board(w, h), previews, R), settings)


# --- ajustes ----------------------------------------------------------------------------------------------
def test_each_fastener_brings_its_own_margins_and_they_can_be_overridden():
    nail, screw = HoldSettings(kind="clavo"), HoldSettings(kind="tirafondo")
    assert (nail.kerf, nail.edge, nail.fastener.head) == (6.0, 12.0, 2.0)
    assert (screw.kerf, screw.edge, screw.fastener.head) == (10.0, 15.0, 6.0)
    mine = HoldSettings(kind="clavo", kerf_margin=3.0, edge_margin=0.0)
    assert (mine.kerf, mine.edge) == (3.0, 0.0)
    assert HoldSettings().reach == 300.0 and set(FASTENERS) == {"clavo", "tirafondo"}


def test_the_defaults_are_the_ones_the_workshop_asked_for():
    d = HoldSettings()
    assert (d.reach, d.min_gap, d.corner_clear, d.min_points) == (300.0, 200.0, 100.0, 6)
    assert (d.cut_clear, d.grid, d.bottom_row) == (20.0, True, 40.0)


@pytest.mark.parametrize("bad, text", [(dict(kind="grapa"), "desconocido"), (dict(reach=0), "alcance"),
                                       (dict(kerf_margin=-1), "margen al corte"), (dict(edge_margin=-0.5), "margen al borde"),
                                       (dict(mark_z=-1), "altura"), (dict(min_gap=-1), "separacion minima"),
                                       (dict(min_gap=float("nan")), "separacion minima"),
                                       (dict(corner_clear=-5), "esquinas"), (dict(min_points=-1), "entero"),
                                       (dict(min_points=2.5), "entero"), (dict(min_points=float("inf")), "entero"),
                                       (dict(cut_clear=-1), "cortes"), (dict(cut_clear=float("nan")), "cortes"),
                                       (dict(bottom_row=-1), "borde inferior"), (dict(bottom_row=float("nan")), "borde inferior"),
                                       (dict(grid="si"), "filas y columnas"), (dict(grid=1), "filas y columnas")])
def test_bad_settings_are_refused_with_the_reason(bad, text):
    with pytest.raises(HoldError, match=text):
        HoldSettings(**bad).validate()
    with pytest.raises(HoldError, match=text):
        analysis(piece_with_hole(), HoldSettings(**bad))


# --- zonas ------------------------------------------------------------------------------------------------
def test_the_board_splits_into_pieces_islands_and_frame():
    z = build_zones(board(), piece_with_hole(), R)
    assert (len(z.pieces), len(z.islands), len(z.frame)) == (1, 1, 1)
    assert z.pieces[0].contains(Point(120, 100)) and z.islands[0].contains(Point(200, 150))
    assert z.frame[0].contains(Point(50, 50)) and not z.frame[0].contains(Point(120, 100))


def test_a_closed_cut_line_makes_an_island_because_what_is_inside_comes_loose():
    z = build_zones(board(), [op("linea", circle_path(200, 150, 40))], R)
    assert len(z.islands) == 1 and len(z.pieces) == 0 and z.islands[0].contains(Point(200, 150))


def test_an_open_cut_line_that_crosses_the_board_splits_the_frame():
    z = build_zones(board(), [op("linea", [(150, 0), (150, 300)])], R)
    assert len(z.frame) == 2 and not z.pieces and not z.islands


def test_a_drill_is_a_corridor_not_a_piece():
    z = build_zones(board(), [op("taladro", drills=[(200, 150, -19)])], R)
    assert len(z.frame) == 1 and not z.islands and z.frame[0].contains(Point(200, 160))
    assert not z.frame[0].contains(Point(200, 150))


# --- un punto ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("x, y, ok, text", [
    (50, 50, True, ""),
    (200, 150, False, "se suelta al cortar"),
    (120, 100, False, "dentro de una pieza"),
    (100, 150, False, "encima del recorrido"),
    (95, 150, False, "a 3.0 mm del corte (minimo 6)"),
    (5, 150, False, "a 5.0 mm del borde de la plancha (minimo 12)"),
    (-1, 5, False, "fuera de la plancha"),
    (450, 150, False, "fuera de la plancha"),
])
def test_a_point_says_whether_it_is_good_and_if_not_why(x, y, ok, text):
    c = analysis(piece_with_hole()).check(x, y)
    assert c.ok is ok and (text in c.reason if text else c.reason == "")


def test_every_point_it_accepts_respects_every_distance_and_never_lands_on_a_piece_or_island():
    a = analysis(piece_with_hole())
    z, s = a.zones, a.settings
    rnd = random.Random(7)
    good = bad = 0
    for _ in range(1500):
        x, y = rnd.uniform(-20, 420), rnd.uniform(-20, 320)
        c = a.check(x, y)
        p = Point(x, y)
        if c.ok:
            good += 1
            assert z.board.boundary.distance(p) >= s.edge - 1e-6
            assert z.axis.distance(p) - R >= s.kerf - 1e-6
            assert not any(comp.contains(p) for comp in z.pieces + z.islands)
        else:
            bad += 1
            assert c.reason
    assert good > 100 and bad > 100


def test_a_screw_needs_more_room_than_a_nail():
    nail, screw = analysis(piece_with_hole()), analysis(piece_with_hole(), HoldSettings(kind="tirafondo"))
    assert screw.free.area < nail.free.area
    assert nail.check(93, 150).ok is False and nail.check(91, 150).ok is True        # 6 mm del borde de corte (x = 98)
    assert screw.check(89, 150).ok is False and screw.check(87, 150).ok is True       # 10 mm
    assert nail.check(13, 150).ok and not nail.check(11, 150).ok
    assert screw.check(16, 150).ok and not screw.check(14, 150).ok


def test_with_no_cuts_at_all_only_the_board_edge_limits():
    a = analysis([])
    assert a.check(200, 150).ok and not a.check(5, 150).ok
    assert a.suggest()                                                  # y se puede proponer


# --- propuesta ----------------------------------------------------------------------------------------------
def test_the_suggestion_is_always_inside_the_free_zone_and_covers_the_board():
    a = analysis(piece_with_hole())
    pts = a.suggest()
    assert pts and all(a.check(*p).ok for p in pts)
    assert a.gap_ok(pts) and a.worst_gap(pts)[0] <= a.settings.reach + GAP_TOL


def test_a_huge_reach_needs_only_the_minimum_number_of_points():
    pts = analysis(piece_with_hole(), HoldSettings(reach=5000)).suggest()       # un solo punto cubriria todo
    assert len(pts) == 3                                                         # pero una plancha no se deja con uno


def test_a_longer_reach_needs_fewer_points():
    shorter = analysis(piece_with_hole(), HoldSettings(reach=120)).suggest()
    longer = analysis(piece_with_hole(), HoldSettings(reach=200)).suggest()
    assert len(shorter) > len(longer) >= 3


def test_the_suggestion_is_deterministic_and_numbered_from_far_to_near():
    a, b = analysis(piece_with_hole()), analysis(piece_with_hole())
    assert a.suggest() == b.suggest() == a.suggest()
    pts = a.suggest()
    assert pts == order_marks(pts)                                      # ya viene en el orden en que se marcan
    assert pts[0][1] == max(p[1] for p in pts)                          # empieza por el mas lejos (Y mayor)
    assert pts[-1][1] == min(p[1] for p in pts)                         # y acaba por el mas cerca


def test_every_big_loose_strip_of_frame_gets_its_own_point():
    previews = [op("linea", [(150, 0), (150, 300)]), op("linea", [(250, 0), (250, 300)])]
    a = analysis(previews)
    assert len(a.zones.frame) == 3
    pts = a.suggest()
    assert any(155 < x < 245 for x, _ in pts)                           # la tira del medio no tiene esquinas
    assert all(a.check(*p).ok for p in pts)


def test_a_strip_where_nothing_fits_is_reported_instead_of_invented(monkeypatch):
    monkeypatch.setattr(holddown, "MIN_FRAGMENT", 1000.0)
    previews = [op("linea", [(150, 0), (150, 300)]), op("linea", [(162, 0), (162, 300)])]     # tira de 8 mm
    a = analysis(previews)
    assert a.unreachable_fragments() == [24]                            # cm2
    assert all(not (152 < x < 160) for x, _ in a.suggest())


def test_when_there_is_no_room_the_suggestion_is_empty_and_the_gap_is_infinite():
    a = analysis([op("pieza", rect_path(6, 6, 394, 294))])
    assert a.suggest() == [] and a.worst_gap([]) == (math.inf, None) and not a.gap_ok([])


def test_worst_gap_finds_the_farthest_place_and_shrinks_as_points_are_added():
    a = analysis([])
    d1, where = a.worst_gap([(15, 15)])
    d2, _ = a.worst_gap([(15, 15), (385, 285)])
    assert d1 > d2 > 0 and math.hypot(where[0] - 15, where[1] - 15) == pytest.approx(d1)
    assert a.worst_gap([(200, 150)])[0] < a.worst_gap([(15, 15)])[0]


def test_edited_points_win_over_the_suggestion_and_an_empty_list_is_an_edit():
    a = analysis(piece_with_hole())
    assert board_points(a, None) == a.suggest()
    assert board_points(a, [(50.0, 50.0)]) == [(50.0, 50.0)]
    assert board_points(a, []) == []


def test_marks_go_from_far_to_near_row_by_row_and_left_to_right_inside_a_row():
    assert order_marks([(100, 0), (10, 0), (50, 0)]) == [(10, 0), (50, 0), (100, 0)]          # una fila: de izquierda a derecha
    assert order_marks([(10, 100), (10, 700), (10, 400)]) == [(10, 700), (10, 400), (10, 100)]  # una columna: de arriba abajo
    two_rows = [(500, 100), (20, 700), (300, 705), (40, 98)]
    assert order_marks(two_rows) == [(20, 700), (300, 705), (40, 98), (500, 100)]              # 705 y 700 son la misma fila
    assert order_marks([]) == []


def test_two_points_are_in_the_same_row_up_to_row_gap_and_in_different_rows_beyond_it():
    gap = ROW_GAP
    assert order_marks([(10, 100), (500, 100 + gap)]) == [(10, 100), (500, 100 + gap)]         # justo el hueco: misma fila
    assert order_marks([(10, 100), (500, 100 + gap + 0.5)]) == [(500, 100 + gap + 0.5), (10, 100)]   # mas: la de arriba antes


def test_a_row_is_chained_so_a_slope_of_close_points_stays_in_one_row():
    pts = [(300, 175), (200, 150), (100, 125), (0, 100)]                # cada uno a 25 mm del siguiente: una sola fila
    assert order_marks(pts) == [(0, 100), (100, 125), (200, 150), (300, 175)]    # de izquierda a derecha, aunque suba


def test_mark_order_gives_positions_and_does_not_lose_repeated_points():
    pts = [(5, 5), (5, 5), (9, 300)]
    assert mark_order(pts) == [2, 0, 1] and sorted(mark_order(pts)) == [0, 1, 2]
    assert order_marks(pts) == [(9, 300), (5, 5), (5, 5)]


# --- programa de marcas ------------------------------------------------------------------------------------
def test_the_marks_program_visits_each_point_and_pauses_with_the_spindle_off():
    text = marks_gcode([(30.5, 90.25), (10, 20)], title="Tablero 1", kind="clavo", safe_z=5, mark_z=3)
    lines = text.splitlines()
    assert lines[0] == "(Marcas de sujecion: Tablero 1)" and "G21 G90 G17 G94 G54" in lines
    words = {w for ln in lines if not ln.startswith("(") for w in ln.split()}
    assert lines.count("M0") == 2 and not words & {"M3", "M4", "M5"} and lines[-1] == "M30"
    body = [ln for ln in lines if not ln.startswith("(")]
    assert body[body.index("G0 X10 Y20") + 1:body.index("G0 X10 Y20") + 4] == ["G0 Z3", "M0", "G0 Z5"]
    assert "G0 X30.5 Y90.25" in body and body[1] == "G0 Z5" and "G0 X0 Y0" not in body and "$H" not in body
    assert "(Punto 2 de 2)" in lines and "(2 puntos, clavo, el husillo no arranca)" in lines


def visits(text):
    return [ln for ln in text.splitlines() if ln.startswith("G0 X")]


def test_the_marks_program_visits_the_points_from_far_to_near_whatever_order_they_come_in():
    pts = [(100, 100), (500, 700), (20, 705), (300, 400)]
    lines = marks_gcode(pts).splitlines()
    assert visits("\n".join(lines)) == ["G0 X20 Y705", "G0 X500 Y700", "G0 X300 Y400", "G0 X100 Y100"]
    assert any(ln.startswith("(Orden: de lejos a cerca") for ln in lines)


def test_each_point_says_which_one_it_is_in_the_window_only_when_the_order_differs():
    pts = [(100, 100), (500, 700), (20, 705), (300, 400)]
    lines = marks_gcode(pts).splitlines()
    assert "(Punto 1 de 4 - es el 3 de la ventana)" in lines and "(Punto 2 de 4 - es el 2 de la ventana)" not in lines
    assert "(Punto 2 de 4)" in lines                                    # el 500,700 era el 2 y sigue siendo el 2
    assert "(Punto 3 de 4 - es el 4 de la ventana)" in lines and "(Punto 4 de 4 - es el 1 de la ventana)" in lines
    ordered = marks_gcode(order_marks(pts)).splitlines()
    assert not any("de la ventana" in ln for ln in ordered) and visits("\n".join(ordered)) == visits("\n".join(lines))


def test_the_marks_program_ends_like_the_job_does():
    assert marks_gcode([(1, 2)], return_to_origin=True).splitlines()[-2:] == ["G0 X0 Y0", "M30"]
    home = marks_gcode([(1, 2)], home_at_end=True, return_to_origin=True).splitlines()
    assert home[-3:] == ["G4 P0.1", "$H", "M30"] and "G0 X0 Y0" not in home


def test_the_marks_program_never_descends_below_the_travel_height_and_is_ascii():
    text = marks_gcode([(1, 2)], title="Tablero ñ (1)", safe_z=5, mark_z=8)
    assert "G0 Z8" in text and "G0 Z5" not in text
    text.encode("ascii")
    assert "(Marcas de sujecion: Tablero n [1])" in text.splitlines()[0]


def test_the_marks_program_needs_points():
    with pytest.raises(HoldError, match="no hay puntos"):
        marks_gcode([])


# --- propuesta: esquinas, separacion, minimo y reparto -------------------------------------------------------------
def corners_of(w, h):
    return [(0, 0), (w, 0), (w, h), (0, h)]


def nearest_to(pts, to):
    return min(math.hypot(x - to[0], y - to[1]) for x, y in pts)


def pair_gaps(pts):
    return [math.dist(p, q) for i, p in enumerate(pts) for q in pts[i + 1:]]


@pytest.mark.parametrize("clear", [60.0, 100.0, 140.0])
def test_the_suggestion_keeps_away_from_the_corners(clear):
    """Una esquina de la plancha cae siempre en el mismo sitio de la bancada, que acaba machacado."""
    a = analysis(piece_with_hole(), HoldSettings(corner_clear=clear))
    pts = a.suggest()
    assert len(pts) >= 2
    for c in corners_of(400, 300):
        assert nearest_to(pts, c) >= clear - 1e-6


def test_the_corner_distance_is_the_one_asked_for_and_not_a_fixed_one():
    strip = [op("pieza", rect_path(70, 70, 398, 298))]               # el marco es una L pegada a dos lados
    near = analysis(strip, HoldSettings(corner_clear=0.0)).suggest()
    far = analysis(strip, HoldSettings(corner_clear=100.0)).suggest()

    def closest(pts):
        return min(nearest_to(pts, c) for c in corners_of(400, 300))
    assert closest(near) < 100 <= closest(far)


def test_the_corner_distance_gives_way_when_it_is_the_only_way_to_cover_the_board():
    a = analysis([], HoldSettings(corner_clear=2000.0), 610, 1015)   # toda la plancha esta "cerca de una esquina"
    pts = a.suggest()
    assert len(pts) >= 4 and a.gap_ok(pts) and all(a.check(*p).ok for p in pts)      # y aun asi se cubre entera


def test_a_loose_pocket_in_a_corner_still_gets_its_point(monkeypatch):
    monkeypatch.setattr(holddown, "MIN_FRAGMENT", 1000.0)
    a = analysis([op("linea", [(60, 0), (60, 60), (0, 60)])])        # un bolsillo de 60 x 60 en la esquina
    assert len(a.zones.frame) == 2
    assert any(x < 60 and y < 60 for x, y in a.suggest())            # soltar el trozo pesa mas que huir de la esquina


@pytest.mark.parametrize("gap", [150.0, 200.0, 260.0])
def test_the_suggestion_never_puts_two_points_closer_than_the_minimum(gap):
    pts = analysis([], HoldSettings(min_gap=gap), 610, 1015).suggest()
    assert len(pts) >= 4 and min(pair_gaps(pts)) >= gap - 1e-6


def test_the_separation_gives_way_when_the_reach_needs_closer_points():
    a = analysis([], HoldSettings(reach=100.0, min_gap=300.0))
    pts = a.suggest()
    assert a.gap_ok(pts) and min(pair_gaps(pts)) < 300
    assert min(pair_gaps(pts)) >= 0.25 * 300 - 1e-6                    # cede lo justo, poco a poco: no los pega


@pytest.mark.parametrize("w, h, reach, kwargs, expected", [
    (610, 1015, 300.0, {}, 6), (600, 600, 300.0, {}, 6), (599, 600, 300.0, {}, 3), (400, 300, 300.0, {}, 3),
    (300, 300, 150.0, {}, 6), (299, 300, 150.0, {}, 3), (610, 1015, 300.0, dict(min_points=10), 10),
    (400, 300, 300.0, dict(min_points=10), 3), (610, 1015, 300.0, dict(min_points=2), 2), (610, 1015, 300.0, dict(min_points=0), 0),
])
def test_how_many_points_a_board_asks_for_depends_on_its_size_and_the_reach(w, h, reach, kwargs, expected):
    assert analysis([], HoldSettings(reach=reach, **kwargs), w, h).required_points() == expected


def test_a_big_board_gets_the_minimum_even_if_fewer_points_would_cover_it():
    few = analysis([], HoldSettings(min_points=0, grid=False), 600, 600).suggest()
    enough = analysis([], HoldSettings(min_points=6, grid=False), 600, 600).suggest()
    assert len(few) < 6 == len(enough)


def test_the_point_added_to_reach_the_minimum_is_the_one_farthest_from_the_others(monkeypatch):
    monkeypatch.setattr(holddown, "_spread", lambda d2, cand, idx, rank, region, min_gap: idx)       # sin repartir
    base = analysis([], HoldSettings(min_points=0, grid=False), 610, 1015).suggest()
    more = analysis([], HoldSettings(min_points=len(base) + 1, grid=False), 610, 1015).suggest()
    extra = [p for p in more if p not in base]
    assert len(more) == len(base) + 1 and len(extra) == 1
    assert min(math.dist(extra[0], q) for q in base) >= 250           # con la separacion minima solo (200) no basta


def test_a_sliver_of_free_zone_between_two_grid_nodes_still_gets_its_point(monkeypatch):
    monkeypatch.setattr(holddown, "MIN_FRAGMENT", 1000.0)
    a = analysis([op("linea", [(150, 0), (150, 300)]), op("linea", [(167.1, 0), (167.1, 300)])])
    pts = a.suggest()                                          # entre las dos lineas queda una tira libre de 1,1 mm
    assert any(158 <= x <= 159.1 for x, _ in pts) and all(a.check(*p).ok for p in pts)


def test_a_place_is_never_proposed_twice_even_when_the_minimum_asks_for_more_than_there_are_places(monkeypatch):
    two = np.array([[100.0, 100.0], [500.0, 900.0]])
    monkeypatch.setattr(HoldAnalysis, "_candidates", lambda self, demand_count: two)
    pts = analysis([], HoldSettings(min_gap=0.0, min_points=6), 610, 1015).suggest()
    assert sorted(pts) == [(100.0, 100.0), (500.0, 900.0)]


def test_a_small_board_only_gets_a_few_points():
    a = analysis([], HoldSettings(min_points=6), 400, 300)
    assert len(a.suggest()) == 3


def test_when_the_minimum_does_not_fit_with_the_separation_it_gives_what_fits():
    pts = analysis([], HoldSettings(min_points=14), 600, 600).suggest()
    assert 6 <= len(pts) < 14 and min(pair_gaps(pts)) >= 200 - 1e-6


def test_every_loose_strip_keeps_a_point_after_the_points_are_spread():
    a = analysis([op("linea", [(150, 0), (150, 300)]), op("linea", [(250, 0), (250, 300)])])
    pts = a.suggest()
    assert len(a.zones.frame) == 3
    for comp in a.zones.frame:
        assert any(comp.contains(Point(*p)) for p in pts)


def test_the_analysis_key_changes_with_what_changes_the_proposal():
    base = analysis(piece_with_hole()).key
    assert analysis(piece_with_hole()).key == base
    for change in (dict(min_gap=150.0), dict(corner_clear=50.0), dict(min_points=4), dict(reach=250.0)):
        assert analysis(piece_with_hole(), HoldSettings(**change)).key != base


def test_the_corners_are_where_the_edge_changes_direction():
    assert sorted(_corners(box(0, 0, 300, 200))) == [(0, 0), (0, 200), (300, 0), (300, 200)]
    two = box(0, 0, 300, 300).union(box(300, 0, 600, 300))                       # dos tableros pegados: un solo rectangulo
    assert sorted(_corners(two)) == [(0, 0), (0, 300), (600, 0), (600, 300)]
    ell = box(0, 0, 300, 100).union(box(0, 100, 100, 300))
    assert len(_corners(ell)) == 6


def test_a_huge_free_board_gets_a_coarser_grid_of_candidates_that_still_fits_in_memory(monkeypatch):
    monkeypatch.setattr(holddown, "MAX_MATRIX", 4_000_000)
    a = analysis([], None, 1220, 2440)
    demand = a._demand()
    cand = a._candidates(len(demand))
    assert 20 < len(cand) and len(cand) * len(demand) <= 4_000_000 + len(demand) * 4         # + los sitios sueltos
    pts = a.suggest()
    assert pts and all(a.check(*p).ok for p in pts)


# --- repartir los puntos --------------------------------------------------------------------------------------------
def open_board(w=600, h=600, step=20.0, reach=300.0):
    """Candidatos y sitios a cubrir en una rejilla (los mismos), y la tabla de distancias que usa _spread."""
    xs, ys = np.arange(step / 2, w, step), np.arange(step / 2, h, step)
    gx, gy = np.meshgrid(xs, ys)
    cand = np.column_stack([gx.ravel(), gy.ravel()])
    d2 = (((cand[:, None, :] - cand[None, :, :]) ** 2).sum(axis=2) / reach ** 2).astype(np.float32)
    return cand, d2


def at(cand, p):
    return int(np.argmin(np.hypot(cand[:, 0] - p[0], cand[:, 1] - p[1])))


def worst(d2, idx):
    return float(np.sqrt(d2[idx].min(axis=0).max()))


def tightest(cand, idx):
    return min(math.dist(cand[i], cand[j]) for a, i in enumerate(idx) for j in idx[a + 1:])


def test_spreading_separates_points_that_start_together_and_improves_the_worst_place():
    cand, d2 = open_board()
    start = [at(cand, p) for p in ((300, 300), (330, 300), (300, 330), (330, 330))]
    out = _spread(d2, cand, start, np.zeros(len(cand), int), np.zeros(len(cand), int), 0.0)
    assert len(out) == 4 and len(set(out)) == 4
    assert tightest(cand, out) > 4 * tightest(cand, start) and worst(d2, out) < 0.8 * worst(d2, start)


def test_spreading_never_leaves_what_is_allowed_nor_its_own_region_nor_the_minimum_gap():
    cand, d2 = open_board()
    allowed = (cand[:, 0] > 100) & (cand[:, 0] < 500) & (cand[:, 1] > 100)           # lejos del borde y de abajo
    region = np.where(cand[:, 0] < 300, 0, 1)                                        # mitad izquierda y mitad derecha
    start = [at(cand, p) for p in ((140, 400), (160, 420), (460, 400), (440, 420))]
    out = _spread(d2, cand, start, np.where(allowed, 0, 1), region, 150.0)
    assert all(allowed[i] for i in out)
    assert [region[i] for i in out] == [region[i] for i in start]                    # cada uno sigue en su mitad
    assert min(math.dist(cand[i], cand[j]) for a, i in enumerate(out) for j in out[a + 1:]) >= 150 - 1e-6
    assert out != start                                                              # y aun asi se movieron


def test_spreading_never_uncovers_a_place_that_was_covered():
    cand, d2 = open_board()
    rnd = random.Random(3)
    covered_cases = 0
    for _ in range(40):
        start = [at(cand, (rnd.uniform(0, 600), rnd.uniform(0, 600))) for _ in range(7)]
        if len(set(start)) < 7 or d2[start].min(axis=0).max() > 1.0:
            continue
        covered_cases += 1
        out = _spread(d2, cand, start, np.zeros(len(cand), int), np.zeros(len(cand), int), 0.0)
        assert d2[out].min(axis=0).max() <= 1.0 + 1e-6
        assert worst(d2, out) <= worst(d2, start) + 1e-6 or tightest(cand, out) >= tightest(cand, start)
    assert covered_cases >= 5


def test_spreading_does_not_drop_a_place_only_one_point_covers_even_if_that_would_be_better_on_average():
    """Una fila de candidatos y de sitios: casi todos los sitios en 50 y uno solo en 10. El punto de la izquierda estaria
    mejor en 44, pero entonces el sitio de 10 quedaria a mas del alcance (30): se queda en 40, lo mas lejos que cubre."""
    reach = 30.0
    cand = np.column_stack([np.arange(0.0, 101.0), np.zeros(101)])
    sites = np.array([10.0] + [50.0] * 600)
    d2 = ((cand[:, 0:1] - sites[None, :]) ** 2 / reach ** 2).astype(np.float32)
    ones, region = np.zeros(len(cand), int), np.zeros(len(cand), int)
    out = _spread(d2, cand, [30, 70], ones, region, 0.0)
    assert cand[out[0], 0] == 40                                                     # se movio hasta donde aun cubre el de 10
    assert d2[out[0], 0] <= 1.0 and d2[out].min(axis=0).max() <= 1.0


def test_spreading_one_point_or_a_board_with_nothing_to_cover_changes_nothing():
    cand, d2 = open_board()
    ones = np.zeros(len(cand), int)
    assert _spread(d2, cand, [5], ones, np.zeros(len(cand), int), 0.0) == [5]
    assert _spread(d2, cand, [], ones, np.zeros(len(cand), int), 0.0) == []
    assert _spread(d2[:, :0], cand, [1, 2], ones, np.zeros(len(cand), int), 0.0) == [1, 2]


def test_a_point_in_a_worse_place_can_move_to_a_better_one_and_not_only_to_one_as_bad():
    cand, d2 = open_board()
    rank = np.where(cand[:, 0] < 100, 1, 0)                                          # pegado al borde izquierdo: peor sitio
    start = [at(cand, (50, 200)), at(cand, (50, 400))]
    out = _spread(d2, cand, start, rank, np.zeros(len(cand), int), 0.0)
    assert [rank[i] for i in start] == [1, 1] and [rank[i] for i in out] == [0, 0]


def test_a_point_that_cannot_improve_stays_where_it_is():
    cand, d2 = open_board(step=100.0)                                               # 6 x 6 candidatos
    start = [at(cand, (50, 50)), at(cand, (550, 550))]
    worse = np.ones(len(cand), int)
    worse[start] = 0                                                                 # todo lo demas es peor que donde estan
    assert _spread(d2, cand, start, worse, np.zeros(len(cand), int), 0.0) == start


def test_a_point_with_nowhere_allowed_to_go_stays_even_if_another_place_would_be_better():
    cand = np.column_stack([[50.0, 0.0, 100.0], np.zeros(3)])                      # el mejor sitio (50) es el primero
    d2 = ((cand[:, 0:1] - np.full((1, 10), 50.0)) ** 2 / 100.0 ** 2).astype(np.float32)
    assert _spread(d2, cand, [1, 2], np.array([1, 0, 0]), np.zeros(3, int), 0.0) == [1, 2]


# --- tu dibujo --------------------------------------------------------------------------------------------------
HAND = [(116.8, 36.1), (476.8, 41.7), (75.1, 351.7), (550.5, 353.1), (161.3, 889.7), (443.5, 891.1), (171.0, 658.9),
        (446.2, 678.4)]
HAND_NINTH = (441.0, 475.0)              # el que anadio despues, leido de la captura (aproximado)


@functools.lru_cache(maxsize=None)
def board610_zones():
    """Las zonas del tablero de 610 x 1015 (3 pasadas): calcular la trayectoria cuesta unos segundos, y las zonas
    no cambian con los ajustes, asi que se calculan una sola vez para todas las pruebas."""
    plan = project("tablero_610x1015.svg", stepdown=6.5, **BOARD610).plan(1)
    assert plan.ok, plan.error
    return analyse_plan(plan, preview_ops(plan), HoldSettings()).zones


def board610_analysis(**settings):
    return HoldAnalysis(board610_zones(), HoldSettings(**settings))        # un analisis nuevo cada vez: no arrastra la propuesta


def test_the_points_chosen_by_hand_on_the_board610_are_all_free_and_cover_it():
    a = board610_analysis()
    assert all(a.check(*p).ok for p in HAND + [HAND_NINTH]) and a.gap_ok(HAND) and a.gap_ok(HAND + [HAND_NINTH])
    assert all(not a.check(*p).warn for p in HAND + [HAND_NINTH])                  # a mano, ninguno a menos de 20 mm del corte


def test_the_board610_proposal_is_spread_away_from_the_corners_with_room_between_points():
    a = board610_analysis()
    pts = a.suggest()
    assert 6 <= len(pts) <= 9                                                        # 6 es el minimo; a mano salieron 8
    assert all(a.check(*p).ok for p in pts)
    assert min(pair_gaps(pts)) >= 200 - 1e-6
    for c in corners_of(610, 1015):
        assert nearest_to(pts, c) >= 100 - 1e-6
    assert a.worst_gap(pts)[0] <= 260                                                # repartidos: a mano, 255; sin repartir, 299


def test_a_bigger_minimum_on_the_board610_gives_more_points_and_a_longer_gap_gives_fewer_close_ones():
    assert len(board610_analysis(min_points=10).suggest()) >= 10
    for gap in (150.0, 260.0):
        assert min(pair_gaps(board610_analysis(min_gap=gap).suggest())) >= gap - 1e-6


# --- varias pasadas ------------------------------------------------------------------------------------------------
def passes(cut, n):
    """El mismo contorno recorrido n veces seguidas en una sola polilinea, como lo deja la previsualizacion cuando el corte
    va en n pasadas (la bajada entre pasadas es vertical y no corta la polilinea)."""
    return cut + cut[1:] * (n - 1)


def sizes(a):
    z = a.zones
    return (len(z.pieces), len(z.islands), len(z.frame)), [round(g.area, 3) for g in z.pieces + z.islands + z.frame]


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 6])
def test_a_contour_cut_in_n_passes_is_the_same_as_cut_in_one(n):
    """Con un numero PAR de pasadas el poligono del contorno no tenia interior y las piezas dejaban de reconocerse."""
    base = analysis(piece_with_hole())
    multi = analysis([op("pieza", passes(rect_path(100, 80, 300, 220), n)), op("hueco", passes(circle_path(200, 150, 20), n))])
    assert sizes(multi) == sizes(base) and sizes(multi)[0] == (1, 1, 1)
    assert multi.suggest() == base.suggest() and multi.worst_gap(multi.suggest()) == base.worst_gap(base.suggest())
    for spot in ((150, 100), (200, 150), (200, 60)):                         # en la pieza, en el hueco y en el marco
        assert multi.check(*spot) == base.check(*spot)
    assert "pieza" in multi.check(150, 100).reason and not multi.check(200, 150).ok and multi.check(200, 40).ok


def test_the_passes_of_a_line_that_closes_also_count_once():
    one = analysis([op("linea", rect_path(100, 80, 300, 220))])
    four = analysis([op("linea", passes(rect_path(100, 80, 300, 220), 4))])
    assert sizes(four) == sizes(one) and sizes(four)[0] == (0, 1, 1)


def test_a_path_that_does_not_close_is_never_a_piece():
    open_path = [(100, 80), (300, 80), (300, 220), (100, 220)]
    for cut in (open_path, open_path + open_path[::-1][1:]):                 # una pasada, e ida y vuelta por el mismo trazado
        a = analysis([op("pieza", cut)])
        assert sizes(a)[0] == (0, 0, 1)


# --- con trabajos reales ----------------------------------------------------------------------------------------
def project(svg, **general):
    p = Project(load_tools(ROOT / "herramientas.json"))
    p.load_svg(DATA / svg)
    p.general = replace(p.general, **general)
    return p


BOARDS19 = dict(tool="em4", thickness=19.0, stepdown=6.5)
PVC = dict(tool="em3175", thickness=3.0, feed=300.0, plunge=200.0)


@pytest.mark.parametrize("svg, general, board_no, kind", [
    ("tableros_19mm.svg", BOARDS19, 1, "clavo"), ("tableros_19mm.svg", BOARDS19, 2, "clavo"),
    ("tableros_19mm.svg", BOARDS19, 1, "tirafondo"), ("pvc_negro_3_y_5mm.svg", PVC, 1, "clavo"),
    ("pvc_negro_3_y_5mm.svg", PVC, 5, "tirafondo"),
])
def test_real_boards_get_safe_points_that_cover_the_sheet(svg, general, board_no, kind):
    plan = project(svg, **general).plan(board_no)
    a = analyse_plan(plan, preview_ops(plan), HoldSettings(kind=kind))
    pts = a.suggest()
    assert 3 <= len(pts) <= 14
    assert all(a.check(*p).ok for p in pts) and a.gap_ok(pts)
    assert all(not comp.contains(Point(*p)) for comp in a.zones.pieces + a.zones.islands for p in pts)
    assert a.unreachable_fragments() == []


BOARD610 = dict(tool="em4", thickness=19.0)


@pytest.mark.parametrize("stepdown, passes_expected", [(8.0, 3), (6.5, 3), (4.85, 4), (3.0, 7), (2.4, 9)])    # con 4 mm, 8 es lo maximo
def test_the_number_of_passes_does_not_change_what_is_a_piece_on_a_real_drawing(stepdown, passes_expected):
    """El dibujo de las letras del tablero de 610 x 1015: con 2 o 4 pasadas se proponian 19 puntos sobre las letras."""
    plan = project("tablero_610x1015.svg", stepdown=stepdown, **BOARD610).plan(1)
    assert plan.ok, plan.error
    prev = preview_ops(plan)
    levels = {round(m.z, 3) for moves in plan.result.op_moves for m in moves if m.z is not None and m.z < 0}
    assert len(levels) == passes_expected
    a = analyse_plan(plan, prev, HoldSettings())
    assert [len(a.zones.pieces), len(a.zones.islands), len(a.zones.frame)] == [15, 6, 1]
    pts = a.suggest()
    assert 4 <= len(pts) <= 14 and all(a.check(*p).ok for p in pts) and a.gap_ok(pts)
    assert all(not comp.contains(Point(*p)) for comp in a.zones.pieces + a.zones.islands for p in pts)
    assert a.unreachable_fragments() == []


def test_a_screw_leaves_less_free_zone_on_a_real_board():
    plan = project("pvc_negro_3_y_5mm.svg", **PVC).plan(1)
    prev = preview_ops(plan)
    nail = analyse_plan(plan, prev, HoldSettings(kind="clavo")).free.area
    screw = analyse_plan(plan, prev, HoldSettings(kind="tirafondo")).free.area
    assert 0 < screw < nail


def test_no_analysis_without_a_valid_plan_or_without_a_board():
    p = project("pvc_negro_3_y_5mm.svg", tool="3.175", thickness=3.0)        # una fresa escrita por su diametro no trae feed
    plan = p.plan(1)
    assert not plan.ok and analyse_plan(plan, []) is None
    assert analyse_plan(None, []) is None
    assert analyse_plan(SimpleNamespace(ok=True, stock=[], job=object()), []) is None


# --- holgura al corte, filas y columnas ------------------------------------------------------------------------
def cut_clearance(a, p):
    """Distancia del punto al borde de corte (al del pasillo de la fresa)."""
    return a.zones.axis.distance(Point(*p)) - a.zones.radius


def clusters(values, tol):
    """Los valores agrupados: cada uno a `tol` o menos del anterior (ya ordenados) cae en el mismo grupo."""
    groups = []
    for v in sorted(values):
        if groups and v - groups[-1][-1] <= tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    return groups


def test_a_point_nearer_the_cut_than_is_comfortable_is_free_but_warns():
    a = analysis([op("linea", [(200, 0), (200, 300)])])                      # el borde del corte, en x = 198 y x = 202
    near, far, bad = a.check(212, 150), a.check(230, 150), a.check(203, 150)
    assert near.ok and "10.0 mm" in near.warn and "20" in near.warn and not near.reason
    assert far == holddown.PointCheck(True)
    assert not bad.ok and "corte" in bad.reason and not bad.warn            # pegado al corte: no vale, y no es un aviso
    assert analysis([op("linea", [(200, 0), (200, 300)])], HoldSettings(cut_clear=0)).check(212, 150) == holddown.PointCheck(True)
    assert analysis([op("linea", [(200, 0), (200, 300)])], HoldSettings(cut_clear=9.5)).check(212, 150).warn == ""
    assert analysis([]).check(200, 150) == holddown.PointCheck(True)         # sin cortes no hay de que avisar


def test_the_comfortable_distance_is_measured_from_the_cut_edge_like_the_margin():
    a = analysis([op("linea", [(200, 0), (200, 300)])], HoldSettings(cut_clear=10.0))
    assert a.check(212.0, 150).warn == "" and a.check(211.9, 150).warn != ""      # 212 - 200 - 2 = 10 mm justos


def test_the_clearance_of_the_candidates_is_the_true_distance_to_the_cut_edge():
    a = analysis(piece_with_hole() + [op("linea", [(10, 10), (390, 290)])])
    rnd = random.Random(5)
    cand = np.array([(rnd.uniform(0, 400), rnd.uniform(0, 300)) for _ in range(300)])
    assert np.allclose(a._clearance(cand), [cut_clearance(a, p) for p in cand])
    assert np.isinf(analysis([])._clearance(cand)).all()                          # sin cortes, ninguno cerca


# --- la rejilla en si --------------------------------------------------------------------------------------------
def test_the_cells_of_a_grid_put_the_bottom_row_at_the_edge_and_the_others_evenly_up_to_half_a_cell_from_the_top():
    assert grid_cells(610, 1015, 2, 4, 40) == pytest.approx((305.0, 975 / 3.5))
    assert grid_cells(610, 1015, 2, 4, 0) == pytest.approx((305.0, 1015 / 4))        # sin fila pegada: celdas iguales
    assert grid_cells(610, 1015, 2, 1, 40) == pytest.approx((305.0, 1015.0))          # una sola fila: sin borde que pegar


def test_the_nodes_of_a_grid_go_row_by_row_from_the_bottom():
    nodes = grid_nodes((0, 0, 610, 1015), 2, 4, 40)
    assert [(i, j) for i, j, _x, _y in nodes] == [(i, j) for j in range(4) for i in range(2)]
    assert sorted({round(x, 6) for _i, _j, x, _y in nodes}) == [152.5, 457.5]
    ys = sorted({round(y, 6) for _i, _j, _x, y in nodes})
    step = 975 / 3.5
    assert ys == pytest.approx([40.0, 40 + step, 40 + 2 * step, 40 + 3 * step]) and 1015 - ys[-1] == pytest.approx(step / 2)
    assert all(y == pytest.approx(40 + j * step) for _i, j, _x, y in nodes)


def test_the_nodes_follow_a_board_that_does_not_start_at_the_origin_and_have_no_bottom_row_when_asked():
    moved = grid_nodes((100, 200, 710, 1215), 2, 4, 40)
    assert (moved[0][2], moved[0][3]) == pytest.approx((252.5, 240.0))
    centred = grid_nodes((0, 0, 610, 1015), 2, 4, 0)
    assert sorted({round(y, 6) for _i, _j, _x, y in centred}) == pytest.approx([1015 / 8 * k for k in (1, 3, 5, 7)])
    assert [(x, y) for _i, _j, x, y in grid_nodes((0, 0, 610, 1015), 1, 1, 40)] == [(305.0, 507.5)]    # una sola celda: el centro
    assert grid_nodes((0, 0, 610, 1015), 1, 3, 40)[0][3] == pytest.approx(40.0)


@pytest.mark.parametrize("w, h, n, expected", [
    (610, 1015, 8, (2, 4)), (610, 1015, 6, (2, 3)), (610, 1015, 7, (2, 4)),            # 7 puntos: la de 8 es la que cuadra
    (1015, 610, 8, (4, 2)), (1015, 610, 6, (3, 2)), (1015, 610, 7, (4, 2)),             # apaisada: mas columnas que filas
    (1200, 1200, 14, (4, 4)),
])
def test_the_grid_that_replaces_n_points_has_nearly_square_cells_and_hardly_any_extra_node(w, h, n, expected):
    assert grid_shape(w, h, n, 40.0, 300.0, 200.0) == expected


@pytest.mark.parametrize("w, h, n, reach, gap", [
    (100, 100, 6, 300.0, 200.0),          # no caben filas ni columnas a 200 mm
    (610, 1015, 8, 100.0, 200.0),         # una celda no cabe en un alcance de 100 mm
    (610, 1015, 6, 300.0, 400.0),         # ni a 400 mm
    (800, 800, 7, 300.0, 200.0),          # solo hay celdas muy alargadas (2 x 4: 400 x 217)
])
def test_no_grid_when_none_fits_the_reach_the_gap_and_a_nearly_square_cell(w, h, n, reach, gap):
    assert grid_shape(w, h, n, 40.0, reach, gap) is None


def test_a_grid_never_has_fewer_nodes_than_points_nor_more_than_a_few_extra():
    for n in range(4, 40):
        shape = grid_shape(2440, 1220, n, 40.0, 300.0, 200.0)
        if shape:
            assert n <= shape[0] * shape[1] <= n + grid_extra(n)
    assert [grid_extra(n) for n in (1, 4, 7, 8, 11, 12, 20)] == [1, 1, 1, 2, 2, 3, 5]


# --- que la propuesta mantenga la holgura al corte -----------------------------------------------------------------
TWO_CUTS = [op("linea", [(200, 0), (200, 1015)]), op("linea", [(410, 0), (410, 1015)])]


@pytest.mark.parametrize("grid", [True, False])
@pytest.mark.parametrize("clear", [20.0, 40.0, 60.0])
def test_the_proposal_keeps_the_asked_distance_to_the_cuts_when_there_is_room(clear, grid):
    a = analysis(TWO_CUTS, HoldSettings(cut_clear=clear, grid=grid), 610, 1015)
    pts = a.suggest()
    assert len(pts) >= 6 and a.gap_ok(pts)
    assert min(cut_clearance(a, p) for p in pts) >= clear - 1e-6
    assert all(not a.check(*p).warn for p in pts)


def test_without_the_comfortable_distance_the_free_spread_puts_points_nearer_the_cuts():
    close = analysis(TWO_CUTS, HoldSettings(cut_clear=0, grid=False), 610, 1015)
    far = analysis(TWO_CUTS, HoldSettings(cut_clear=20, grid=False), 610, 1015)
    assert min(cut_clearance(close, p) for p in close.suggest()) < 20 <= min(cut_clearance(far, p) for p in far.suggest())


def test_the_comfortable_distance_gives_way_when_there_is_no_room_and_the_points_say_so():
    cut = [op("linea", [(0, 50), (600, 50)])]                                  # una plancha de 600 x 100 cortada por la mitad
    roomy = analysis(cut, HoldSettings(cut_clear=20), 600, 100)
    tight = analysis(cut, HoldSettings(cut_clear=100), 600, 100)
    assert all(not roomy.check(*p).warn for p in roomy.suggest()) and roomy.gap_ok(roomy.suggest())
    pts = tight.suggest()
    assert pts and tight.gap_ok(pts) and all(tight.check(*p).ok for p in pts)    # llega a todo aunque no haya holgura
    assert all(tight.check(*p).warn for p in pts)                                # y cada punto avisa de lo cerca que esta
    assert min(cut_clearance(tight, p) for p in pts) >= 6.0                      # sin pasar nunca del margen al corte


@pytest.mark.parametrize("grid", [True, False])
def test_the_board610_proposal_keeps_at_least_20_mm_from_every_cut(grid):
    a = board610_analysis(grid=grid)
    pts = a.suggest()
    assert len(pts) == 8 and min(cut_clearance(a, p) for p in pts) >= 20 - 1e-6 and all(not a.check(*p).warn for p in pts)
    assert a.gap_ok(pts) and min(pair_gaps(pts)) >= 200 - 1e-6
    for c in corners_of(610, 1015):
        assert nearest_to(pts, c) >= 100 - 1e-6


def test_on_the_board610_less_room_asked_gives_points_nearer_the_cuts_and_more_room_gives_way_little_by_little():
    near = board610_analysis(cut_clear=0)
    assert min(cut_clearance(near, p) for p in near.suggest()) < 15                 # sin pedir holgura, a 7 mm
    more = board610_analysis(cut_clear=30)
    pts = more.suggest()
    assert more.gap_ok(pts) and min(cut_clearance(more, p) for p in pts) >= 0.75 * 30 - 1e-6   # cede un escalon, no de golpe


# --- filas y columnas --------------------------------------------------------------------------------------------
def test_the_board610_proposal_is_two_columns_by_four_rows_with_two_points_by_the_bottom_edge():
    a = board610_analysis()
    pts = a.suggest()
    cols, rows = clusters([x for x, _ in pts], 70), clusters([y for _, y in pts], 70)
    assert [len(c) for c in cols] == [4, 4] and [len(r) for r in rows] == [2, 2, 2, 2]
    assert all(abs(c[0] - ref) < 70 and abs(c[-1] - ref) < 70 for c, ref in zip(cols, (152.5, 457.5)))
    bottom = [p for p in pts if p[1] <= 60]
    assert len(bottom) == 2 and bottom[0][0] < 305 < bottom[1][0] or bottom[1][0] < 305 < bottom[0][0]
    for c in corners_of(610, 1015):
        assert nearest_to(pts, c) >= 100 - 1e-6                                       # junto al borde, pero no en la esquina
    assert all(a.check(*p).ok for p in pts) and a.gap_ok(pts) and a.worst_gap(pts)[0] <= 260
    assert min(pair_gaps(pts)) >= 200 - 1e-6
    for (_i, _j, nx, ny) in grid_nodes((0, 0, 610, 1015), 2, 4, 40):                  # cada nudo, con su punto cerca
        assert nearest_to(pts, (nx, ny)) <= 0.45 * 279 + 1e-6


def test_the_points_of_a_row_are_kept_at_the_same_height_when_the_cuts_allow_it():
    a = board610_analysis()
    pts = sorted(a.suggest(), key=lambda p: p[1])
    for lo, hi in zip(pts[::2], pts[1::2]):
        assert abs(lo[1] - hi[1]) <= 70                                                # aunque cada lado tenga sus cortes


def test_without_the_grid_the_board610_points_are_spread_freely_not_in_two_columns():
    a = board610_analysis(grid=False)
    pts = a.suggest()
    assert len(clusters([x for x, _ in pts], 70)) > 2 and a.gap_ok(pts) and all(a.check(*p).ok for p in pts)
    assert pts != board610_analysis().suggest()


def test_a_landscape_board_gets_more_columns_than_rows():
    pts = analysis([], HoldSettings(), 1015, 610).suggest()
    assert [len(c) for c in clusters([x for x, _ in pts], 70)] == [2, 2, 2, 2]
    assert [len(r) for r in clusters([y for _, y in pts], 70)] == [4, 4]


def test_with_nothing_in_the_way_every_point_sits_on_its_node():
    pts = analysis([], HoldSettings(), 610, 1015).suggest()
    nodes = [(x, y) for _i, _j, x, y in grid_nodes((0, 0, 610, 1015), 2, 4, 40)]
    assert len(pts) == 8 and all(nearest_to(pts, n) <= 8.0 for n in nodes)


@pytest.mark.parametrize("bottom, lowest", [(0.0, 1015 / 8), (40.0, 40.0), (100.0, 100.0)])
def test_the_bottom_row_goes_where_the_setting_says(bottom, lowest):
    pts = analysis([], HoldSettings(bottom_row=bottom), 610, 1015).suggest()
    assert abs(min(y for _, y in pts) - lowest) <= 8.0 and len(pts) == 8


def test_a_board_that_is_not_big_or_has_no_grid_that_fits_is_spread_freely():
    for w, h in ((500, 700), (610, 610), (800, 800)):
        assert analysis([], HoldSettings(grid=True), w, h).suggest() == analysis([], HoldSettings(grid=False), w, h).suggest()


def test_a_small_board_is_never_laid_out_in_a_grid_even_when_it_needs_four_points_and_a_grid_would_fit():
    """190 x 190 con alcance 100: es pequena (area bajo 4 x alcance^2) pero hacen falta 4 puntos para llegar a toda ella."""
    kw = dict(reach=100.0, min_gap=40.0)
    a = analysis([], HoldSettings(grid=True, **kw), 190, 190)
    free = analysis([], HoldSettings(grid=False, **kw), 190, 190)
    assert not a.is_large() and len(free.suggest()) == 4
    assert grid_shape(190, 190, 4, 40.0, 100.0, 40.0) is not None                           # una rejilla de 2 x 2 si cabria
    assert a.suggest() == free.suggest()


def test_three_points_are_never_laid_out_in_a_grid_even_on_a_big_board_where_a_row_would_fit():
    """900 x 300 con alcance 250: grande (area sobre 4 x alcance^2) y tres puntos llegan a toda ella: no hay rejilla de 3."""
    kw = dict(reach=250.0, min_gap=100.0, min_points=3)
    a = analysis([], HoldSettings(grid=True, **kw), 900, 300)
    free = analysis([], HoldSettings(grid=False, **kw), 900, 300)
    assert a.is_large() and len(free.suggest()) < holddown.GRID_MIN_POINTS
    assert grid_shape(900, 300, 3, 40.0, 250.0, 100.0) is not None                          # una fila de 3 si cabria
    assert a.suggest() == free.suggest()


def test_a_grid_that_leaves_part_of_the_board_uncovered_is_not_used_when_the_free_spread_covers_it(monkeypatch):
    """Con el tope de puntos en 8, ocho nudos amontonados en una esquina gastan todos los puntos y dejan sin cubrir el resto."""
    monkeypatch.setattr(holddown, "MAX_POINTS", 8)
    monkeypatch.setattr(holddown, "grid_nodes", lambda *a, **k: [(0, j, 150.0, 150.0) for j in range(8)])
    pts = board610_analysis(min_gap=0.0).suggest()
    assert pts == board610_analysis(min_gap=0.0, grid=False).suggest()
    assert max(y for _, y in pts) > 800                                                     # y llega a lo alto de la plancha


def test_a_grid_that_asks_for_many_more_points_than_the_free_spread_is_not_used(monkeypatch):
    monkeypatch.setattr(holddown, "grid_shape", lambda *a, **k: (3, 6))                       # 18 puntos contra 8
    assert board610_analysis().suggest() == board610_analysis(grid=False).suggest()


def test_a_grid_node_that_falls_on_a_piece_looks_for_room_nearby_or_the_free_spread_fills_in():
    a = analysis([op("pieza", rect_path(100, 100, 510, 915))], HoldSettings(), 610, 1015)      # el centro, tapado
    pts = a.suggest()
    assert len(pts) >= 6 and a.gap_ok(pts) and all(a.check(*p).ok for p in pts)
    assert all(not comp.contains(Point(*p)) for comp in a.zones.pieces for p in pts)


@pytest.mark.parametrize("w, h", [(1200, 1200), (2440, 1220)])
def test_a_big_sheet_gets_a_grid_with_hardly_more_points_than_the_free_spread(w, h):
    grid = analysis([], HoldSettings(), w, h)
    free = analysis([], HoldSettings(grid=False), w, h)
    pts, loose = grid.suggest(), free.suggest()
    assert grid.gap_ok(pts) and all(grid.check(*p).ok for p in pts)
    assert len(loose) <= len(pts) <= len(loose) + grid_extra(len(loose))
    assert len(clusters([x for x, _ in pts], 70)) < len(clusters([x for x, _ in loose], 70))        # menos columnas: en rejilla


def test_the_proposal_key_changes_with_every_new_setting():
    base = analysis([]).key
    for change in (dict(cut_clear=30.0), dict(grid=False), dict(bottom_row=0.0), dict(min_points=7), dict(min_gap=210.0),
                   dict(corner_clear=90.0)):
        assert analysis([], HoldSettings(**change)).key != base
    assert analysis([], HoldSettings()).key == base


def test_the_ladder_gives_way_first_the_separation_then_the_corners_and_the_clearance_to_the_cut_last():
    rank = np.arange(2 * len(holddown.CLEAR_STEPS))        # 0: lejos de esquinas y con holgura; 1: cerca de esquina; 2: ...
    steps = holddown._ladder(rank, 200.0)
    top = [int(pool.sum()) - 1 for pool, _gap in steps]    # el rango mas alto que admite cada escalon
    gaps = [gap for _pool, gap in steps]
    assert len(steps) == len(holddown.CLEAR_STEPS) * (len(holddown.GAP_STEPS) + 1)
    assert top[:6] == [0, 0, 0, 0, 0, 1] and gaps[:6] == [200.0, 150.0, 100.0, 50.0, 0.0, 0.0]     # separacion, luego esquinas
    assert top[6:12] == [2, 2, 2, 2, 2, 3] and gaps[6:12] == [200.0, 150.0, 100.0, 50.0, 0.0, 0.0]  # y vuelta a empezar con menos holgura
    assert top[-1] == len(rank) - 1 and gaps[-1] == 0.0                                          # al final, todo vale
    assert top == sorted(top)


def test_a_node_with_no_comfortable_place_within_its_window_is_left_to_the_free_spread(monkeypatch):
    monkeypatch.setattr(holddown, "_spread", lambda d2, cand, idx, rank, region, min_gap: idx)       # sin repartir: compara lo elegido
    monkeypatch.setattr(holddown, "grid_shape", lambda *a, **k: (1, 1))
    monkeypatch.setattr(holddown, "grid_nodes", lambda *a, **k: [(0, 0, 305.0, 507.5)])             # el centro, bajo una pieza enorme
    monkeypatch.setattr(holddown, "GRID_WINDOW", 0.3)       # 183 mm del lado menor (610): el marco queda a unos 265; 304 por el largo
    big = [op("pieza", rect_path(60, 60, 550, 955))]
    with_grid = analysis(big, HoldSettings(grid=True), 610, 1015).suggest()
    without = analysis(big, HoldSettings(grid=False), 610, 1015).suggest()
    assert with_grid == without and len(without) >= 6


def test_the_points_of_a_row_share_their_height_even_when_one_had_to_move_away_from_a_cut():
    pts = analysis([op("pieza", rect_path(40, 309, 280, 329))], HoldSettings(), 610, 1015).suggest()   # sobre el nudo de la 2.ª fila
    rows = clusters([y for _, y in pts], 20)
    assert [len(r) for r in rows] == [2, 2, 2, 2] and all(r[-1] - r[0] <= 8 for r in rows)
    assert any(abs(r[0] - 319) > 40 for r in rows)                      # la fila del obstaculo si se movio


def test_the_points_of_a_column_share_their_distance_from_the_edge_even_when_one_had_to_move_away_from_a_cut():
    pts = analysis([op("pieza", rect_path(143, 20, 163, 200))], HoldSettings(), 610, 1015).suggest()   # sobre el nudo de abajo a la izquierda
    cols = clusters([x for x, _ in pts], 20)
    assert [len(c) for c in cols] == [4, 4] and all(c[-1] - c[0] <= 8 for c in cols)
    assert cols[0][0] > 190                                             # la columna del obstaculo si se movio


def test_a_node_close_to_a_cut_moves_to_where_there_is_twice_the_comfortable_distance_when_that_is_near():
    cut = [op("linea", [(180, 0), (180, 1015)])]                           # el nudo de la columna izquierda queda a 25,5 mm del corte
    a = analysis(cut, HoldSettings(), 610, 1015)
    left = [p for p in a.suggest() if p[0] < 180]
    assert len(left) == 4 and all(cut_clearance(a, p) >= 2 * 20 - 1e-6 for p in left)
    assert all(abs(p[0] - 152.5) <= 25 for p in left)                       # pero sin irse lejos del nudo
    b = analysis(cut, HoldSettings(cut_clear=0), 610, 1015)                 # sin holgura que pedir, cada punto en su nudo
    assert all(abs(p[0] - 152.5) <= 8 for p in b.suggest() if p[0] < 180)


# --- puentes: la pieza sigue siendo pieza -----------------------------------------------------------------------------------
def piece_with_bridges():
    """Una pieza de 100 x 60 cuyo contorno lleva dos puentes (tramos que la fresa recorre levantada): los cortes quedan en
    tres trozos abiertos y los puentes los cierran, como en la previsualizacion de un trabajo real."""
    x0, y0, x1, y1 = 150, 120, 250, 180
    top = [(x0, y1), (190, y1)]
    bridge_top = [(190, y1), (210, y1)]
    rest = [(210, y1), (x1, y1), (x1, y0), (x0, y0), (x0, 150)]
    bridge_left = [(x0, 150), (x0, 160)]
    last = [(x0, 160), (x0, y1)]
    return OpPreview(1, "pieza", cuts=[top, rest, last], bridges=[bridge_top, bridge_left])


def test_a_piece_whose_cut_has_bridges_is_still_a_piece_and_is_never_held():
    z = build_zones(board(), [piece_with_bridges()], R)
    assert len(z.pieces) == 1 and len(z.frame) == 1 and z.pieces[0].contains(Point(200, 150))
    a = HoldAnalysis(z, HoldSettings())
    assert a.check(200, 150).reason.startswith("dentro de una pieza")
    assert not any(box(150, 120, 250, 180).contains(Point(p)) for p in a.suggest())


def test_without_the_bridges_closing_the_contour_the_piece_would_be_taken_for_scrap():
    """Lo que pasaba: solo contaban los cortes, que con puentes son trozos abiertos, y el cuerpo de la pieza era marco."""
    p = piece_with_bridges()
    z = build_zones(board(), [OpPreview(1, "pieza", cuts=p.cuts)], R)
    assert not z.pieces and any(f.contains(Point(200, 150)) for f in z.frame)


def test_a_contour_that_does_not_close_to_the_last_digit_is_still_a_piece():
    """Lo que se vio en un Mac con Apple Silicon: el recorrido de varias pasadas con puentes acababa a 0,0000000001 mm de donde
    empezo (y los puentes a esa distancia de los cortes), el contorno se tomaba por abierto y se proponia sujetar la pieza."""
    nudge = 1e-10
    x0, y0, x1, y1 = 150, 120, 250, 180
    top = [(x0, y1), (190, y1)]
    bridge_top = [(190, y1), (210 + nudge, y1)]
    rest = [(210, y1 - nudge), (x1, y1), (x1, y0), (x0, y0), (x0, 150)]
    bridge_left = [(x0 - nudge, 150), (x0, 160)]
    last = [(x0, 160 + nudge), (x0 + nudge, y1 - nudge)]
    shifted = OpPreview(1, "pieza", cuts=[top, rest, last], bridges=[bridge_top, bridge_left])
    z = build_zones(board(), [shifted], R)
    assert len(z.pieces) == 1 and z.pieces[0].contains(Point(200, 150))
    assert not any(box(150, 120, 250, 180).contains(Point(p)) for p in HoldAnalysis(z, HoldSettings()).suggest())


@pytest.mark.parametrize("kind, expected", [("pieza", (1, 0, 1)), ("hueco", (0, 1, 1)), ("linea", (0, 1, 1))])
def test_a_closed_path_that_ends_a_hair_away_from_where_it_started_still_closes(kind, expected):
    cut = rect_path(100, 80, 300, 220)
    cut[-1] = (100 + 1e-10, 80 - 1e-10)
    assert sizes(analysis([op(kind, cut)]))[0] == expected


def test_a_real_piece_cut_in_several_passes_with_bridges_is_not_held_on_its_body():
    """Una pieza en L con un circulo, cortada con la fresa de 3,175 (varias pasadas) y con puentes: la propuesta era un solo
    punto, en medio de la pieza."""
    p = project("pieza_en_l_con_puentes.svg", tool="em3175", thickness=3.0, feed=300.0, plunge=200.0)
    for row in p.rows:
        if row.action == "corte":
            row.tabs = True
    plan = p.plan(1)
    assert plan.ok, plan.error
    prev = preview_ops(plan)
    assert prev[0].bridges
    a = analyse_plan(plan, prev, HoldSettings())
    assert len(a.zones.pieces) == 1
    pts = a.suggest()
    assert pts and all(a.check(*pt).ok for pt in pts)
    assert all(not piece.contains(Point(*pt)) for piece in a.zones.pieces for pt in pts)


def test_the_tool_passes_over_a_bridge_so_nothing_is_held_on_it():
    long_bridge = OpPreview(1, "pieza", cuts=[[(150, 120), (150, 180), (250, 180), (250, 120)], [(190, 120), (150, 120)]],
                            bridges=[[(250, 120), (190, 120)]])                  # puente de 60 mm: la fresa pasa por el
    a = analysis([long_bridge])
    assert a.zones.axis.distance(Point(220, 120)) == 0
    assert a.check(220, 120).reason == "encima del recorrido de la fresa"
    assert a.check(220, 128).reason != "" and not a.check(220, 128).ok            # y a 8 mm de el, dentro del pasillo o pegado a el
