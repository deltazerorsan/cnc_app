"""Puentes (tabs): la propuesta automatica, la trayectoria levantada, la vista previa, la receta y el trabajo. Sin Qt."""
import math
from dataclasses import replace
from pathlib import Path

import pytest
from shapely.geometry import Point, box

from cnc import drawing_edit as de
from cnc.generate import generate_gcode
from cnc.model import ContourOp, Job, Stock, Tool
from cnc.oprows import OpRow, plain, recipe_dict, recipe_to_toml, row_to_dict, rows_from_recipe
from cnc.preview import preview_moves, preview_ops
from cnc.project import General, Project, ProjectError
from cnc.recipe import RecipeError, parse_recipe
from cnc.tabs import Tab, TabError, TabSettings, lengths, point_at, propose_tabs, raised_path, tab_spans
from cnc.toolpath import Move, op_moves
from cnc.verify_nc import analyze

DATA = Path(__file__).parent / "data"
BOARDS19 = DATA / "tableros_19mm.svg"
TOP = -17.0                      # cima de un puente de 2 mm en un material de 19 mm


def tools():
    return {"em4": Tool(diameter=4, name="End mill 4 mm", feed_xy=1000, feed_z=300, stepdown=6.5, stepover=0.6)}


def tabbed(shape=None, tabs=None, **kw):
    shape = shape or box(0, 0, 100, 60)
    tabs = propose_tabs(shape, TabSettings(), 4) if tabs is None else tabs
    return ContourOp(shape, side="outside", stepdown=8, feed_xy=1000, feed_z=300, tabs=tabs, **kw)


def moves_of(op, thickness=19.0, extra=0.2):
    return op_moves(op, Stock(thickness, spoilboard_extra=extra), 2.0, 5.0, 1.0)


def centre_of(shape, tab):
    pts = [(float(x), float(y)) for x, y in shape.exterior.coords]
    cum = lengths(pts)
    return point_at(pts, cum, tab.at * cum[-1])


# --- ajustes ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kw", [{"width": 0}, {"height": -1}, {"spacing": 0}, {"width": float("nan")}, {"width": True},
                                {"minimum": 0}, {"minimum": 1.5}, {"clear": -1}])
def test_a_setting_that_does_not_make_sense_is_refused(kw):
    with pytest.raises(TabError):
        TabSettings(**kw)


def test_the_defaults_are_6_mm_wide_and_2_mm_high():
    s = TabSettings()
    assert (s.width, s.height) == (6.0, 2.0)


# --- la propuesta ---------------------------------------------------------------------------------------------
def test_a_rectangle_gets_two_tabs_in_the_middle_of_opposite_sides():
    shape = box(0, 0, 100, 60)
    tabs = propose_tabs(shape, TabSettings(), 4)
    assert len(tabs) == 2 and all(t.width == 6 and t.height == 2 for t in tabs)
    centres = sorted(tuple(round(v) for v in centre_of(shape, t)) for t in tabs)
    assert centres == [(50, 0), (50, 60)]


def test_a_longer_perimeter_gets_more_tabs_one_every_150_mm():
    assert len(propose_tabs(box(0, 0, 400, 300), TabSettings(), 4)) == 9            # 1400 mm de perimetro
    assert len(propose_tabs(box(0, 0, 100, 60), TabSettings(spacing=40), 4)) == 8
    assert len(propose_tabs(box(0, 0, 400, 300), TabSettings(minimum=3, spacing=10000), 4)) == 3


def test_tabs_keep_away_from_the_corners_of_an_l_shape():
    from shapely.geometry import Polygon
    shape = Polygon([(0, 0), (200, 0), (200, 50), (50, 50), (50, 200), (0, 200)])
    tabs = propose_tabs(shape, TabSettings(), 4)
    assert len(tabs) >= 3
    corners = [(float(x), float(y)) for x, y in shape.exterior.coords][:-1]
    for t in tabs:
        c = Point(centre_of(shape, t))
        assert min(c.distance(Point(p)) for p in corners) >= 5 + 4 / 2 + 3        # la mitad de lo que se levanta, mas margen


def test_a_tiny_piece_gets_no_tabs_and_a_small_one_gets_just_one():
    assert propose_tabs(box(0, 0, 6, 6), TabSettings(), 4) == ()
    assert len(propose_tabs(box(0, 0, 10, 10), TabSettings(), 4)) == 1


def test_a_circle_is_balanced_and_open_lines_get_nothing():
    tabs = propose_tabs(Point(0, 0).buffer(40, 64), TabSettings(), 4)
    assert len(tabs) == 2 and abs((tabs[1].at - tabs[0].at) - 0.5) < 0.02
    from shapely.geometry import LineString
    assert propose_tabs(LineString([(0, 0), (100, 0)]), TabSettings(), 4) == ()


def test_a_tab_follows_the_piece_when_it_moves_turns_or_is_scaled():
    from shapely import affinity
    shape = box(0, 0, 100, 60)
    tabs = propose_tabs(shape, TabSettings(), 4)
    for move in (lambda g: affinity.translate(g, 300, 10), lambda g: affinity.rotate(g, 90, origin=(0, 0)),
                 lambda g: affinity.scale(g, 2, 2, origin=(0, 0)), lambda g: affinity.scale(g, -1, 1, origin=(0, 0))):
        moved = move(shape)
        for t in tabs:
            x, y = centre_of(shape, t)
            expected = move(Point(x, y))
            got = Point(centre_of(moved, t))
            assert got.distance(expected) < 1e-6


# --- la trayectoria levantada --------------------------------------------------------------------------------
def test_only_the_passes_below_the_top_of_a_tab_are_lifted_and_only_over_the_tab():
    moves = moves_of(tabbed())
    cuts = [m for m in moves if not m.rapid and (m.x is not None or m.y is not None)]
    lifted = [m for m in cuts if m.tab]
    assert lifted and all(m.z == pytest.approx(TOP) for m in lifted)
    zs = sorted({round(m.z, 3) for m in cuts})
    assert zs == [-19.2, -17.0, -12.8, -6.4]                       # las pasadas de 6.4 mm y la cima del puente
    assert {round(m.z, 3) for m in cuts if not m.tab} == {-19.2, -12.8, -6.4}      # solo la ultima pasada se levanta
    assert min(m.z for m in moves if m.z is not None) == pytest.approx(-19.2)


def test_the_bridge_left_is_6_mm_wide_and_the_cutter_is_lifted_6_plus_its_diameter():
    shape = box(0, 0, 100, 60)
    one = propose_tabs(shape, TabSettings(), 4)[0]
    bridges = preview_moves(moves_of(tabbed(shape, [one]))).bridges
    assert len(bridges) == 1
    (x0, y0), (x1, y1) = bridges[0][0], bridges[0][-1]
    lifted = math.hypot(x1 - x0, y1 - y0)
    assert lifted == pytest.approx(6 + 4)                       # el diametro de la fresa son 4 mm (radio 2)
    assert lifted - 4 == pytest.approx(6)                       # lo que queda sin cortar entre los dos filos
    assert y0 == pytest.approx(y1) and (x0 + x1) / 2 == pytest.approx(50)         # en el centro del lado


def test_the_lift_is_a_vertical_step_at_the_plunge_feed_and_the_pass_ends_back_at_full_depth():
    moves = moves_of(tabbed())
    runs = 0
    for i, m in enumerate(moves):
        if m.tab and not moves[i - 1].tab:
            up = moves[i - 1]
            assert (up.x, up.y, up.rapid, up.feed) == (None, None, False, 300) and up.z == pytest.approx(TOP)
            runs += 1
        if not m.tab and moves[i - 1].tab:
            down = m
            assert (down.x, down.y, down.rapid, down.feed) == (None, None, False, 300) and down.z == pytest.approx(-19.2)
    assert runs == 2


def test_no_tabs_gives_exactly_the_same_moves_as_before():
    plain_op = ContourOp(box(0, 0, 100, 60), side="outside", stepdown=8, feed_xy=1000, feed_z=300)
    assert op_moves(plain_op, Stock(19, spoilboard_extra=0.2), 2.0, 5.0, 1.0) == \
        op_moves(replace(plain_op, tabs=()), Stock(19, spoilboard_extra=0.2), 2.0, 5.0, 1.0)
    assert not any(m.tab for m in op_moves(plain_op, Stock(19, spoilboard_extra=0.2), 2.0, 5.0, 1.0))


def test_a_single_pass_cut_also_lifts_over_the_bridge():
    op = replace(tabbed(), stepdown=19.2)
    cuts = [m for m in moves_of(op) if not m.rapid and (m.x is not None or m.y is not None)]
    assert {round(m.z, 3) for m in cuts} == {-19.2, -17.0}


def test_a_tab_over_the_start_of_the_ring_moves_the_plunge_out_of_the_bridge():
    shape = box(0, 0, 100, 60)
    corner = 0.6875                                                  # el vertice (0, 0): ahi arranca la fresa
    moves = moves_of(tabbed(shape, [Tab(corner, 6, 2)]))
    first = next(m for m in moves if m.rapid and m.x is not None)
    assert math.hypot(first.x - (-1.414), first.y - (-1.414)) > 3            # ya no arranca en la esquina
    # en la ultima pasada, justo despues de bajar, la fresa no va levantada: el puente quedo al final del anillo
    i = next(i for i, m in enumerate(moves) if m.z == pytest.approx(-19.2) and m.x is None and not m.rapid)
    assert not moves[i + 1].tab and moves[i + 1].x is not None
    lifted = [m for m in moves if m.tab]
    assert lifted and (lifted[-1].x, lifted[-1].y) == pytest.approx((first.x, first.y))


def test_a_bridge_wider_than_the_piece_is_refused():
    with pytest.raises(ValueError, match="demasiado ancho"):
        moves_of(tabbed(box(0, 0, 10, 10), [Tab(0.5, 60, 2)]))


def test_a_bridge_as_high_as_the_material_or_in_a_blind_cut_is_refused():
    with pytest.raises(ValueError):
        moves_of(tabbed(tabs=[Tab(0.3, 6, 19)]))
    with pytest.raises(ValueError):
        moves_of(tabbed(through=False, depth=5))


def test_a_tab_that_no_longer_matches_the_shape_is_skipped():
    shape = box(0, 0, 100, 60)
    spans = tab_spans(box(500, 500, 600, 560), [Tab(0.3, 6, 2)], [op_ring(shape)], 2.0, "outside", 19.0)
    assert spans == [[]]


def op_ring(shape):
    from cnc.toolpath import tool_path_rings
    return tool_path_rings(shape, "outside", 2.0)[0][0]


# --- recorrer por tramos ----------------------------------------------------------------------------------------
def square_ring():
    return [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (0.0, 0.0)]


def test_a_path_without_bridges_over_its_start_keeps_its_start_and_covers_the_whole_ring():
    start, steps = raised_path(square_ring(), [(40.0, 60.0, TOP)])
    assert start == (0.0, 0.0)
    assert steps[-1][:2] == (0.0, 0.0)
    lifted = [(x, y) for x, y, top in steps if top is not None]
    assert lifted == [(60.0, 0.0)]
    assert sum(1 for s in steps if s[2] is None) == len(steps) - 1


def test_a_bridge_across_the_closing_point_restarts_after_it():
    start, steps = raised_path(square_ring(), [(-5.0, 5.0, TOP)])
    assert start == (5.0, 0.0)
    assert steps[-1][:2] == (5.0, 0.0)
    assert [(x, y) for x, y, top in steps if top == TOP] == [(0.0, 0.0), (5.0, 0.0)]       # lo ultimo del recorrido


def test_overlapping_bridges_take_the_highest_top():
    start, steps = raised_path(square_ring(), [(10.0, 30.0, -17.0), (20.0, 40.0, -15.0)])
    tops = [(round(x), round(y), top) for x, y, top in steps]
    assert (20, 0, -17.0) in tops and (30, 0, -15.0) in tops and (40, 0, -15.0) in tops


def test_bridges_that_fill_the_whole_ring_are_refused():
    with pytest.raises(ValueError):
        raised_path(square_ring(), [(0.0, 400.0, TOP)])
    with pytest.raises(ValueError):
        raised_path(square_ring(), [(0.0, 250.0, TOP), (250.0, 400.0, TOP)])


# --- vista previa y G-code ----------------------------------------------------------------------------------------
def test_the_preview_draws_each_bridge_once_apart_from_the_cut():
    moves = moves_of(tabbed())
    p = preview_moves(moves)
    assert len(p.bridges) == 2
    assert all(len(b) >= 2 for b in p.bridges)
    assert p.z_min == pytest.approx(-19.2)
    plain_cut = preview_moves(moves_of(tabbed(tabs=())))
    assert plain_cut.bridges == [] and len(plain_cut.cuts) == 1


def test_the_gcode_with_bridges_passes_the_independent_checks():
    from cnc import safety
    job = Job(Stock(19, spoilboard_extra=0.2), Tool(diameter=4), [tabbed()])
    result = generate_gcode(job)
    assert safety.gcode_problems(result.text) == []
    assert result.text.count("Z-17") == 2                              # una subida en cada uno de los dos puentes
    assert "Z-19.2" in result.text and result.min_z == pytest.approx(-19.2)


# --- receta y filas -------------------------------------------------------------------------------------------------
def recipe_with(extra=None):
    return {"operacion": [{"nombre": "Piezas", "tipo": "corte", "trazo": "#000000", "pasante": True, **(extra or {})}]}


def test_the_recipe_key_puentes_is_read_and_written():
    r = parse_recipe(recipe_with({"puentes": True}))
    assert r.operations[0].tabs is True and parse_recipe(recipe_with()).operations[0].tabs is False
    row = OpRow(stroke="#000000", action="corte", through=True, tabs=True)
    assert row_to_dict(row, "Piezas")["puentes"] is True
    assert "puentes = true" in recipe_to_toml(recipe_dict([row]))
    assert "puentes" not in row_to_dict(replace(row, tabs=False), "Piezas")
    assert rows_from_recipe(r, [OpRow(stroke="#000000", count=1)])[0].tabs is True


@pytest.mark.parametrize("raw, text", [({"puentes": "si"}, "true o false"),
                                       ({"puentes": True, "tipo": "vaciado", "profundidad": 3, "pasante": False}, "solo vale en operaciones de corte"),
                                       ({"puentes": True, "pasante": False, "profundidad": 5}, "solo valen en cortes pasantes")])
def test_the_recipe_refuses_bridges_where_they_make_no_sense(raw, text):
    data = recipe_with()
    data["operacion"][0].update(raw)
    with pytest.raises(RecipeError, match=text):
        parse_recipe(data)


def test_a_row_with_bridges_is_no_longer_a_plain_cut():
    row = OpRow(stroke="#000000", count=1)
    assert plain([row]) and not plain([replace(row, tabs=True)])


# --- el trabajo ------------------------------------------------------------------------------------------------------
def job19_project(tabs=True):
    p = Project(tools())
    p.load_svg(BOARDS19)
    p.general.tool, p.general.thickness = "em4", 19
    for r in p.rows:
        r.tabs = tabs
    return p


def test_a_job_with_bridges_says_how_many_and_cuts_them():
    p = job19_project()
    plan = p.plan(1)
    assert plan.ok and not plan.blockers
    texts = [m.text for m in plan.messages]
    assert any(t.startswith("Puentes: ") and "6 mm de ancho y 2 mm de alto" in t for t in texts)
    assert "Z-17" in plan.text
    off = job19_project(tabs=False).plan(1)
    assert "Z-17" not in off.text and not any(m.text.startswith("Puentes") for m in off.messages)


def test_pieces_and_holes_get_bridges_lines_do_not_and_the_small_pieces_are_told():
    p = job19_project()
    plan = p.plan(1)
    ops = [op for op in plan.operations if isinstance(op, ContourOp)]
    assert all(op.tabs == () for op in ops if op.side == "on")
    assert any(op.tabs for op in ops if op.side == "outside") and any(op.tabs for op in ops if op.side == "inside")
    small = [m.text for m in plan.messages if "demasiado pequeñas para llevar puentes" in m.text]
    assert len(small) >= 1 and all(m.level == "warning" for m in plan.messages if m.text in small)
    assert len(small) <= len({r.name or r.title for r in p.rows})          # una nota por fila, no una por pieza
    assert analyze(plan.text).ok


def test_a_hole_too_small_for_bridges_is_cut_without_them_and_without_a_warning():
    p = Project(tools())
    p.use_boards = False
    p.new_drawing()
    p.edit(de.add_edit([de.make_rectangle(0, 0, 200, 120, 0, stroke="#000000"), de.make_circle(100, 60, 3, stroke="#000000"),
                        de.make_circle(40, 60, 25, stroke="#000000")]))
    p.general.tool, p.general.thickness = "em4", 19
    p.rows[0].tabs = True
    plan = p.plan(None)
    assert plan.ok
    holes = {round(op.shape.bounds[2] - op.shape.bounds[0]): op for op in plan.operations if op.side == "inside"}
    assert holes[6].tabs == () and len(holes[50].tabs) >= 1
    assert not any("demasiado pequeñas" in m.text for m in plan.messages)


def test_a_hole_never_gets_more_bridges_than_its_shorter_path_has_room_for():
    shape = Point(0, 0).buffer(11.5, quad_segs=32)                         # un agujero de 23 mm: contorno de 72 mm
    settings = TabSettings()
    outside = propose_tabs(shape, settings, 4.0)
    inside = propose_tabs(shape, settings, 4.0, inside=True)               # por dentro la fresa recorre unos 59 mm
    assert len(outside) == 2 and len(inside) == 1
    assert propose_tabs(Point(0, 0).buffer(5, quad_segs=32), settings, 4.0, inside=True) == ()


def test_the_bridge_settings_come_from_the_job_and_a_bad_one_only_matters_when_used():
    p = job19_project()
    p.general.tab_width, p.general.tab_height, p.general.tab_spacing = 8, 3, 100
    text = p.plan(1).text
    assert "Z-16" in text and "Z-17" not in text                         # 3 mm de alto en 19 mm
    p.general.tab_width = 0
    plan = p.plan(1)
    assert plan.error and "anchura del puente" in plan.error
    assert job19_project(tabs=False).plan(1).ok
    q = job19_project(tabs=False)
    q.general.tab_width = 0
    assert q.plan(1).ok


def test_bridges_as_high_as_the_board_stop_the_job():
    p = job19_project()
    p.general.tab_height = 19
    plan = p.plan(1)
    assert plan.error and "no cabe en un material" in plan.error


def test_the_bridge_settings_and_the_rows_survive_saving_the_job(tmp_path):
    p = job19_project()
    p.general.tab_width, p.general.tab_height, p.general.tab_spacing = 7, 2.5, 120
    path = tmp_path / "trabajo.json"
    p.save(path)
    q = Project(tools())
    q.load_svg(BOARDS19)
    q.apply_dict(__import__("json").loads(path.read_text(encoding="utf-8")))
    assert (q.general.tab_width, q.general.tab_height, q.general.tab_spacing) == (7, 2.5, 120)
    assert all(r.tabs for r in q.rows)


def test_the_machine_preview_has_the_bridges_of_every_piece():
    plan = job19_project().plan(1)
    previews = preview_ops(plan)
    assert sum(len(p.bridges) for p in previews) == sum(len(op.tabs) for op in plan.operations if isinstance(op, ContourOp))


def test_the_default_settings_in_the_config_file_name_the_same_fields():
    g = General.from_config({"tab_width": 5, "tab_height": 1.5})
    assert (g.tab_width, g.tab_height, g.tab_spacing) == (5, 1.5, 150)
