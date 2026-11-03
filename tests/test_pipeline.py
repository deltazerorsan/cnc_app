"""cnc.pipeline: la logica del trabajo que comparten la linea de comandos y la interfaz grafica."""
from pathlib import Path

import pytest
from gcodecmp import assert_same_gcode
from shapely.geometry import box

import svg_a_gcode
from svg_a_gcode import render
from cnc.model import Tool
from cnc.pipeline import (
    OVERHANG_SLACK,
    JobSettings,
    Message,
    choose_rotation,
    find_tools_file,
    load_drawing,
    overhang,
    plan_job,
    suggested_offset,
)
from cnc.recipe import parse_recipe
from cnc.svg_import import ImportedShape

DATA = Path(__file__).parent / "data"
BOARDS19 = DATA / "tableros_19mm.svg"
RECIPE = DATA / "receta_ejemplo.toml"
TOOL = Tool(diameter=4)


def shp(geom, stroke="#ff0000", fill=None, closed=True):
    return ImportedShape(shape=geom, closed=closed, stroke=stroke, fill=fill)


def settings(**kw):
    base = dict(tool=TOOL, thickness=6.0, margin=0.4, feed=300.0, plunge=200.0)
    base.update(kw)
    return JobSettings(**base)


def two_shapes():
    return [shp(box(0, 0, 100, 60)), shp(box(20, 20, 40, 40))]


# --- mensajes ---------------------------------------------------------------------------------------
def test_render_prefixes_by_level_and_rejects_unknown_levels():
    assert render(Message("hecho")) == "hecho"
    assert render(Message("ojo", "warning")) == "AVISO: ojo"
    assert render(Message("mal", "error")) == "ERROR: mal"
    with pytest.raises(ValueError, match="nivel de mensaje desconocido"):
        Message("x", "grave")


# --- planificar un trabajo -------------------------------------------------------------------------------
def test_plan_without_recipe_cuts_holes_before_pieces_and_prints_nothing(capsys):
    plan = plan_job(two_shapes(), [], settings())
    assert plan.ok and plan.error is None and plan.text.startswith("(Generado por cnc_app)")
    assert [o.side for o in plan.operations] == ["inside", "outside"]
    assert plan.result.min_z == pytest.approx(-6.4)
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""                                  # la logica no imprime
    texts = [m.text for m in plan.messages]
    assert texts[0] == "Giro: sin giro"
    assert any(t.startswith("Fresa: ") and "feed 300" in t and "entrada 200" in t and "una sola pasada" in t for t in texts)
    assert any(t.startswith("Operaciones: ") and "1 pasada(s)" in t for t in texts)


def test_plan_sources_and_passes_appear_in_the_summary():
    plan = plan_job(two_shapes(), [], settings(stepdown=2.0, sources={"feed": "ficha", "stepdown": "linea de comandos"}))
    line = next(m.text for m in plan.messages if m.text.startswith("Fresa: "))
    assert "feed 300 (ficha)" in line and "pasadas de hasta 2 mm (linea de comandos)" in line
    assert any("4 pasada(s)" in m.text for m in plan.messages)                       # 6,4 mm en pasadas de 2


def test_plan_places_the_drawing_at_zero_and_applies_the_offset():
    plan = plan_job(two_shapes(), [], settings(offset=(5, 7)))
    minx, miny, *_ = plan.result.bounds
    assert (minx, miny) == pytest.approx((5 - 2, 7 - 2), abs=0.01)                    # cero + offset - radio de la fresa
    assert "Dibujo movido 5, 7 mm respecto al cero" in [m.text for m in plan.messages]
    assert min(s.shape.bounds[0] for s in plan.shapes) == pytest.approx(5)


def test_plan_with_a_board_gives_zero_at_its_corner_and_returns_the_placed_board():
    board = [shp(box(500, 300, 700, 400), stroke="#2b2a29")]
    pieces = [shp(box(520, 320, 560, 360))]
    plan = plan_job(pieces, board, settings(), board_number=1)
    assert plan.ok and plan.stock[0].shape.bounds == pytest.approx((0, 0, 200, 100))
    assert plan.shapes[0].shape.bounds == pytest.approx((20, 20, 60, 60))
    assert any(m.text.startswith("Tablero: 200.0 x 100.0 mm") for m in plan.messages)


@pytest.mark.parametrize("kwargs,text", [
    (dict(feed=None), "faltan el avance de corte y el de bajada"),
    (dict(plunge=None), "faltan el avance de corte y el de bajada"),
])
def test_plan_without_recipe_needs_both_feeds(kwargs, text):
    plan = plan_job(two_shapes(), [], settings(**kwargs))
    assert not plan.ok and text in plan.error and plan.messages[-1] == Message(plan.error, "error")


def test_plan_with_nothing_to_cut_is_an_error_not_a_crash():
    plan = plan_job([], [], settings())
    assert not plan.ok and "no hay ninguna forma que cortar" in plan.error


def test_generation_errors_become_plan_errors_and_keep_the_earlier_messages():
    plan = plan_job(two_shapes(), [], settings(tool=Tool(diameter=4, kind="ball")))     # de momento solo genera la endmill
    assert not plan.ok and plan.text is None
    assert plan.messages[0].text == "Giro: sin giro" and plan.messages[-1].level == "error"


def test_settings_become_post_options_and_the_end_code_reaches_the_file():
    plan = plan_job(two_shapes(), [], settings(pause_at_start=True, home_at_end=True, end_code="G0 X10 Y10\nM9"))
    lines = plan.text.splitlines()
    assert "M0" in lines and "$H" in lines
    assert lines[-4:] == ["(Codigo final definido por el usuario)", "G0 X10 Y10", "M9", "M30"]
    off = plan_job(two_shapes(), [], settings(emit_spindle=False, return_to_origin=True)).text
    assert "M3 S20000" not in off and "G0 X0 Y0" in off.splitlines()


# --- receta -------------------------------------------------------------------------------------------
def recipe_for(*ops, **top):
    return parse_recipe({"operacion": list(ops), **top}, "r.toml")


def test_plan_with_recipe_reports_operations_ignored_and_unmatched_shapes():
    rec = recipe_for({"nombre": "cortes", "tipo": "corte", "trazo": "#ff0000"}, ignorar=["#ffcc00"], titulo="demo")
    shapes = [*two_shapes(), shp(box(0, 0, 3, 3), stroke="#ffcc00"), shp(box(0, 0, 4, 4), stroke="#00ff00")]
    plan = plan_job(shapes, [], settings(), rec)
    assert plan.ok and plan.built.counts == {"cortes": 2}
    texts = {m.level: [] for m in plan.messages}
    for m in plan.messages:
        texts[m.level].append(m.text)
    assert "Ignoradas por la receta: 1 forma(s)" in texts["info"]
    assert any("1 forma(s) sin operacion en la receta" in t and "#00ff00" in t for t in texts["warning"])
    assert any(t.startswith("Fresa: ") and "receta r.toml (demo)" in t for t in texts["info"])
    assert "  cortes: corte, 2 forma(s), pasante" in texts["info"]


def test_recipe_errors_are_plan_errors():
    rec = recipe_for({"nombre": "cortes", "tipo": "corte", "trazo": "#00ff00"})
    plan = plan_job(two_shapes(), [], settings(), rec)
    assert not plan.ok and "no encuentra ninguna forma que cortar" in plan.error
    rec = recipe_for({"nombre": "cortes", "tipo": "corte", "trazo": "#ff0000"})
    plan = plan_job(two_shapes(), [], settings(feed=None), rec)
    assert not plan.ok and "'cortes' no tiene avance de corte" in plan.error


# --- tableros que sobresalen -----------------------------------------------------------------------------
@pytest.mark.parametrize("side,expected", [
    ("por la izquierda", (7, 0)), ("por abajo", (0, 7)), ("por la derecha", (-7, 0)), ("por arriba", (0, -7)),
])
def test_suggested_offset_moves_the_drawing_back_inside_with_slack(side, expected):
    assert suggested_offset(side, 3.0) == expected and OVERHANG_SLACK == 4
    assert suggested_offset(side, 3.01)[0] + suggested_offset(side, 3.01)[1] in (8, -8)     # sube al milimetro siguiente


def test_overhang_reports_only_the_sides_that_stick_out():
    stock = [shp(box(0, 0, 100, 100), stroke="#2b2a29")]
    assert overhang((-3, 10, 50, 120), stock) == pytest.approx({"por la izquierda": 3.0, "por arriba": 20.0})
    assert overhang((0.02, 0, 100, 100), stock) == {}


def test_plan_flags_a_piece_that_sticks_out_of_its_board():
    board = [shp(box(0, 0, 200, 100), stroke="#2b2a29")]
    piece = [shp(box(50, -1, 90, 40))]                                                  # 1 mm fuera por abajo
    plan = plan_job(piece, board, settings(rotate=0.0), board_number=1)
    assert list(plan.overhang) == ["por abajo"] and plan.overhang["por abajo"] == pytest.approx(3.0, abs=0.05)
    warn = next(m for m in plan.messages if m.code == "overhang")
    assert warn.level == "warning" and "--offset 0,7" in warn.text


def test_trajectory_larger_than_the_machine_is_flagged_severe():
    plan = plan_job([shp(box(0, 0, 700, 50))], [], settings(rotate=0.0))
    severe = [m for m in plan.messages if m.severe]
    assert len(severe) == 1 and "NO CABE" in severe[0].text and severe[0].level == "info"


# --- giro -----------------------------------------------------------------------------------------------
def test_choose_rotation_auto_turns_only_what_does_not_fit():
    wide = [shp(box(0, 0, 900, 300))]
    tall = [shp(box(0, 0, 300, 900))]
    assert choose_rotation("auto", False, wide, [], TOOL, False)[0] == 90.0
    assert choose_rotation("auto", False, tall, [], TOOL, False)[0] == 0.0
    assert choose_rotation(None, False, wide, [], TOOL, True)[0] == 90.0               # con tableros, auto por defecto
    assert choose_rotation(None, False, wide, [], TOOL, False)[0] == 0.0
    assert choose_rotation(None, True, wide, [], TOOL, False)[0] == 90.0               # --portrait
    assert choose_rotation(180, True, wide, [], TOOL, True) == (180.0, "giro de 180 grados")
    assert choose_rotation(0, False, wide, [], TOOL, True) == (0.0, "sin giro")
    w, why = choose_rotation("auto", False, [shp(box(0, 0, 900, 900))], [], TOOL, False)
    assert w == 0.0 and why.startswith("no cabe ni girado")


# --- el dibujo ---------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def drawing():
    return load_drawing(BOARDS19)


def test_load_drawing_drops_junk_with_a_warning_and_finds_the_boards(drawing):
    assert len(drawing.imported.shapes) == 130 and len(drawing.shapes) == 128 and len(drawing.junk) == 2
    assert [m.level for m in drawing.messages] == ["warning"] and "2 resto(s) sin sentido" in drawing.messages[0].text
    assert [b.number for b in drawing.layout.boards] == [1, 2]
    board = drawing.layout.boards[0]
    assert len(drawing.board_shapes(board)) == len(board.contents)
    assert drawing.board_stock(board)[0].shape.bounds == pytest.approx(board.bounds)


def test_load_drawing_options_exclude_colors_and_stock_color():
    no_red = load_drawing(BOARDS19, exclude_colors=["#FF0000"])
    assert all((s.stroke or "").lower() != "#ff0000" for s in no_red.shapes) and no_red.excluded == {"#ff0000"}
    by_color = load_drawing(BOARDS19, stock_color="#2b2a29")
    assert len(by_color.stock) == 2 and by_color.layout.boards == []                    # sin tableros: manda el color
    assert load_drawing(BOARDS19, boards=False).layout.boards == []


def test_plan_of_a_real_board_matches_the_cli(tmp_path, drawing):
    """Mismo G-code por la API que por la linea de comandos."""
    board = drawing.layout.boards[1]
    rec = svg_a_gcode.load_recipe(RECIPE)
    plan = plan_job(drawing.board_shapes(board), drawing.board_stock(board),
                    settings(tool=Tool(diameter=4), thickness=19.0, margin=0.4, feed=None, plunge=None,
                             rotate=0.0, pause_at_start=True), rec, board.number)
    out = tmp_path / "cli.nc"
    assert svg_a_gcode.main([str(BOARDS19), "--no-config", "--tool", "4", "--thickness", "19", "--margin", "0.4",
                             "--receta", str(RECIPE), "--rotate", "0", "--pause-at-start", "--board", "2",
                             "-o", str(out)]) == 0
    assert plan.ok
    assert_same_gcode(plan.text, out.read_text())


# --- herramientas y archivos --------------------------------------------------------------------------------
def test_find_tools_file_prefers_the_explicit_one_then_the_folders_in_order(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    assert find_tools_file(None, [a, b]) is None
    (b / "herramientas.json").write_text("{}")
    assert find_tools_file(None, [a, b]) == b / "herramientas.json"
    (a / "herramientas.json").write_text("{}")
    assert find_tools_file(None, [a, b]) == a / "herramientas.json"
    assert find_tools_file(tmp_path / "otra.json", [a, b]) == tmp_path / "otra.json"


def test_end_code_option_on_the_command_line(tmp_path, capsys):
    svg = tmp_path / "d.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="60mm" viewBox="0 0 100 60">'
                   '<rect x="10" y="10" width="40" height="30" stroke="#ff0000" fill="none"/></svg>')
    out = tmp_path / "o.nc"
    args = [str(svg), "--no-config", "--tool", "4", "--thickness", "6", "--feed", "300", "--plunge", "200"]
    assert svg_a_gcode.main([*args, "--end-code", "G0 X10 Y10\\nM9", "--home-at-end", "-o", str(out)]) == 0
    assert out.read_text().splitlines()[-4:] == ["(Codigo final definido por el usuario)", "G0 X10 Y10", "M9", "M30"]
    assert svg_a_gcode.main([*args, "-o", str(out)]) == 0
    assert "Codigo final" not in out.read_text()


def test_settings_defaults_are_the_documented_ones():
    s = settings()
    opts = s.post_options()
    assert (opts.end_code, opts.emit_spindle, opts.pause_at_start, opts.home_at_end) == ("", True, False, False)
    assert (s.order, s.safe_z, s.clearance_z, s.origin, s.rotate, s.offset) == ("far-to-near", 5.0, 1.0, "bbox_bottom_left", None, None)


def test_a_tool_bigger_than_a_hole_is_a_plan_error_not_an_exception():
    pieces = [shp(box(0, 0, 100, 60))] + [shp(box(x, 20, x + 6, 26)) for x in (20, 40, 60)]     # tres huecos de 6 mm
    plan = plan_job(pieces, [], settings(tool=Tool(diameter=10), thickness=6.0))
    assert not plan.ok and "demasiado grande" in plan.error and plan.text is None
    assert [m.level for m in plan.messages][-1] == "error"
    assert plan_job(pieces, [], settings()).ok                                    # con la fresa de 4 mm si se hace
