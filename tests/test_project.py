"""Tabla de operaciones (filas <-> receta) y trabajo de la interfaz (cnc.oprows, cnc.project), sin Qt."""
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest
from gcodecmp import assert_same_gcode
from shapely.geometry import box

import svg_a_gcode
from cnc.model import Tool
from cnc.oprows import (
    OpRow,
    build_recipe,
    ignored_colors,
    plain,
    recipe_dict,
    recipe_to_toml,
    row_to_dict,
    rows_from_recipe,
    rows_from_shapes,
)
from cnc.project import BoardOverrides, General, Project, ProjectError
from cnc.recipe import RecipeError, load_recipe, parse_recipe
from cnc.svg_import import ImportedShape

DATA = Path(__file__).parent / "data"
BOARDS19 = DATA / "tableros_19mm.svg"
RECIPE = DATA / "receta_ejemplo.toml"
SHIPPED_MACHINES = Path(__file__).parent.parent / "maquinas.json"           # el archivo que se entrega
RED, ORANGE, YELLOW, BLACK = "#ff0000", "#e63c24", "#ffcc00", "#000000"


def shp(stroke=None, fill=None, x=0):
    return ImportedShape(shape=box(x, 0, x + 10, 10), closed=True, stroke=stroke, fill=fill)


def tools():
    return {"em4": Tool(diameter=4, name="End mill 4 mm", feed_xy=300, feed_z=200),
            "vacia": Tool(diameter=3.175, name="Sin datos")}


# --- filas ------------------------------------------------------------------------------------------
def test_one_row_per_color_combination_in_drawing_order_with_counts():
    rows = rows_from_shapes([shp(RED), shp(None, BLACK), shp(RED), shp(None, None), shp(RED, BLACK), shp("#FF0000")])
    assert [(r.stroke, r.fill, r.count) for r in rows] == [
        (RED, None, 3), (None, BLACK, 1), (None, None, 1), (RED, BLACK, 1)]
    assert [r.color_label for r in rows] == ["trazo #ff0000", "relleno #000000", "sin color",
                                             "trazo #ff0000 + relleno #000000"]
    assert all(r.action == "corte" and r.is_plain_cut() for r in rows)


def test_plain_means_every_active_row_is_a_normal_through_cut():
    rows = rows_from_shapes([shp(RED), shp(ORANGE), shp(YELLOW)])
    assert plain(rows)
    rows[2].action = "ignorar"
    assert plain(rows)                                       # ignorar no necesita receta
    rows[1].depth, rows[1].through = 3, False
    assert not plain(rows)
    for change in (dict(side="dentro"), dict(stepdown=2.0), dict(feed=500.0), dict(plunge=100.0), dict(action="vaciado")):
        assert not plain([replace(rows[0], **change)])
    assert not plain([replace(r, action="ignorar") for r in rows])        # sin nada activo no hay trabajo


def test_recipe_dict_names_operations_and_keeps_only_what_applies():
    corte = OpRow(stroke=RED, action="corte", side="fuera", stepdown=6.5, feed=900, plunge=300)
    pozo = OpRow(stroke=ORANGE, action="vaciado", through=False, depth=3, stepover=60, feed=1200)
    taladro = OpRow(fill=BLACK, action="taladro", through=False, depth=10, stepdown=5.1, stepover=40)
    ign = OpRow(stroke=YELLOW, action="ignorar")
    data = recipe_dict([corte, pozo, taladro, ign], title="mi trabajo", ignore=ignored_colors([ign]))
    assert data["titulo"] == "mi trabajo" and data["ignorar"] == [YELLOW]
    assert data["operacion"] == [
        {"nombre": f"Corte {RED}", "tipo": "corte", "trazo": RED, "lado": "fuera", "pasante": True,
         "pasada": 6.5, "avance": 900, "entrada": 300},
        {"nombre": f"Vaciado {ORANGE}", "tipo": "vaciado", "trazo": ORANGE, "profundidad": 3, "avance": 1200,
         "stepover": 60},
        {"nombre": f"Taladro {BLACK}", "tipo": "taladro", "relleno": BLACK, "profundidad": 10, "pasada": 5.1},
    ]                                                                              # stepover solo en vaciados; lado solo en cortes


def test_duplicate_operation_names_get_a_number():
    rows = [OpRow(stroke=RED), OpRow(stroke=RED, fill=BLACK), OpRow(stroke="#00ff00", name="Mis cortes"),
            OpRow(stroke="#0000ff", name="Mis cortes")]
    names = [o["nombre"] for o in recipe_dict(rows)["operacion"]]
    assert names == [f"Corte {RED}", f"Corte {RED} (2)", "Mis cortes", "Mis cortes (2)"]
    assert len(build_recipe(rows).operations) == 4


def test_a_row_with_a_bad_value_names_its_operation():
    with pytest.raises(RecipeError, match=r"operacion 1 \('Vaciado #e63c24'\): falta 'profundidad'"):
        build_recipe([OpRow(stroke=ORANGE, action="vaciado", through=False)])
    with pytest.raises(RecipeError, match="mayor que 0"):
        build_recipe([OpRow(stroke=ORANGE, action="vaciado", through=False, depth=-3)])
    with pytest.raises(RecipeError, match="ninguna operacion activa"):
        build_recipe([OpRow(stroke=ORANGE, action="ignorar")])


def test_row_to_dict_a_through_pocket_and_a_row_without_colors():
    assert row_to_dict(OpRow(stroke=RED, fill=BLACK, action="vaciado", through=True), "x") == {
        "nombre": "x", "tipo": "vaciado", "trazo": RED, "relleno": BLACK, "pasante": True}
    assert "trazo" not in row_to_dict(OpRow(), "sin") and OpRow().operation_name() == "Corte sin color"


def test_the_shipped_recipe_assigns_to_the_rows_of_the_real_drawing_and_orders_them():
    p = Project(tools())
    p.load_svg(BOARDS19)
    keys = [(r.stroke, r.fill) for r in p.rows]
    assert (RED, None) in keys and (ORANGE, None) in keys and (None, BLACK) in keys and (YELLOW, None) in keys
    p.load_recipe_file(RECIPE)
    assert [(r.action, r.display_color) for r in p.rows[:3]] == [("vaciado", BLACK), ("vaciado", ORANGE), ("corte", RED)]
    assert p.rows[-1].action == "ignorar"                                           # el amarillo (y lo que no encaja)
    black, orange, red = p.rows[:3]
    assert (black.depth, black.through, black.stepdown, black.feed, black.plunge, black.stepover) == (10, False, 5.1, 600, 400, 60)
    assert (orange.depth, orange.feed, orange.plunge) == (3, 1200, 300) and red.through and red.stepdown == 6.5
    assert p.recipe_file == str(RECIPE) and "valores de unos .nc de referencia" in p.title


def test_rows_without_a_matching_operation_become_ignored():
    recipe = parse_recipe({"operacion": [{"nombre": "c", "tipo": "corte", "trazo": RED}]})
    rows = rows_from_recipe(recipe, rows_from_shapes([shp(ORANGE), shp(RED), shp(None, BLACK)]))
    assert [(r.display_color, r.action) for r in rows] == [(RED, "corte"), (ORANGE, "ignorar"), (BLACK, "ignorar")]


# --- TOML ---------------------------------------------------------------------------------------------
def test_toml_round_trip_gives_back_the_same_recipe_and_is_readable(tmp_path):
    rows = [OpRow(stroke=RED, action="corte", side="dentro", through=False, depth=19.4, stepdown=6.5, feed=900.5, plunge=300),
            OpRow(stroke=ORANGE, action="vaciado", through=False, depth=3, stepover=60),
            OpRow(fill=BLACK, action="taladro", through=False, depth=10, stepdown=5)]
    data = recipe_dict(rows, title='Titulo "con comillas" y acentos: ñ', ignore=[YELLOW])
    text = recipe_to_toml(data)
    assert text.startswith("# Receta de trabajo generada por cnc_app")
    assert tomllib.loads(text) == data
    path = tmp_path / "r.toml"
    path.write_text(text, encoding="utf-8")
    again = load_recipe(path)
    assert again.title == data["titulo"] and again.ignore == [YELLOW]
    assert [(o.kind, o.side, o.depth, o.stepdown) for o in again.operations] == [
        ("corte", "dentro", 19.4, 6.5), ("vaciado", "auto", 3, None), ("taladro", "auto", 10, 5)]
    assert again.operations[1].stepover == pytest.approx(0.6) and again.operations[0].feed_xy == 900.5


# --- grosor, fresa y maquina dentro de la receta ---------------------------------------------------------
JOB_OPS = {"operacion": [{"nombre": "c", "tipo": "corte", "trazo": RED}]}


def test_recipe_job_keys_parse_and_old_recipes_have_none():
    r = parse_recipe({**JOB_OPS, "grosor": 19, "fresa": "em4", "maquina": "emulador"})
    assert (r.thickness, r.tool, r.machine) == (19, "em4", "emulador")
    assert parse_recipe({**JOB_OPS, "fresa": 3.175}).tool == "3.175"
    old = parse_recipe(JOB_OPS)
    assert (old.thickness, old.tool, old.machine) == (None, None, None)
    for bad in ({"grosor": "mucho"}, {"fresa": ""}, {"maquina": 5}, {"fresa": [1]}):
        with pytest.raises(RecipeError):
            parse_recipe({**JOB_OPS, **bad})


def test_a_recipe_with_job_values_sets_them_and_says_what_changed():
    p = Project(tools(), machines={"emulador": object()})
    p.load_svg(BOARDS19)
    p.general = replace(p.general, tool="vacia", thickness=12.0, machine="")
    notes = p.apply_recipe(parse_recipe({**JOB_OPS, "grosor": 19, "fresa": "em4", "maquina": "emulador"}))
    assert (p.general.thickness, p.general.tool, p.general.machine) == (19, "em4", "emulador")
    assert [n.level for n in notes] == ["info", "info"]
    assert "19" in notes[0].text and "12" in notes[0].text and "vacia" in notes[1].text


def test_a_recipe_naming_an_unknown_tool_or_machine_warns_and_keeps_the_current_ones():
    p = Project(tools())
    p.load_svg(BOARDS19)
    p.general = replace(p.general, tool="em4", machine="")
    notes = p.apply_recipe(parse_recipe({**JOB_OPS, "fresa": "no_existe", "maquina": "fantasma"}))
    assert p.general.tool == "em4" and p.general.machine == ""
    assert [n.level for n in notes] == ["warning", "warning"]
    assert "no_existe" in notes[0].text and "fantasma" in notes[1].text


def test_a_recipe_without_job_values_leaves_the_job_alone():
    p = Project(tools())
    p.load_svg(BOARDS19)
    p.general = replace(p.general, tool="em4", thickness=7.0)
    assert p.apply_recipe(parse_recipe(JOB_OPS)) == []
    assert (p.general.tool, p.general.thickness) == ("em4", 7.0)


def test_saving_a_recipe_includes_job_values_and_reading_it_back_restores_them(tmp_path):
    p = Project(tools(), machines={"emulador": object()})
    p.load_svg(BOARDS19)
    p.load_recipe_file(RECIPE)
    p.general = replace(p.general, tool="em4", thickness=19.0, machine="emulador")
    path = tmp_path / "r.toml"
    path.write_text(p.recipe_text(), encoding="utf-8")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert (data["grosor"], data["fresa"], data["maquina"]) == (19.0, "em4", "emulador")
    q = Project(tools(), machines={"emulador": object()})
    q.load_svg(BOARDS19)
    q.general = replace(q.general, tool="vacia", thickness=3.0, machine="")
    q.load_recipe_file(path)
    assert (q.general.tool, q.general.thickness, q.general.machine) == ("em4", 19.0, "emulador")
    assert len(q.recipe_notes) == 2
    p.general = replace(p.general, tool="", machine="")
    assert "fresa" not in tomllib.loads(p.recipe_text()) and "maquina" not in tomllib.loads(p.recipe_text())


def test_saving_a_recipe_checks_that_it_can_be_read_back():
    p = Project(tools())
    p.rows = [OpRow(stroke=RED, action="corte"), OpRow(stroke=RED, fill=BLACK, action="ignorar")]
    with pytest.raises(ProjectError, match="esta en 'ignorar'"):                   # un color a la vez cortado e ignorado
        p.recipe_text()
    p.rows = [OpRow(stroke=ORANGE, action="vaciado", through=False)]
    with pytest.raises(ProjectError, match="falta 'profundidad'"):
        p.recipe_text()


# --- el trabajo con el dibujo real -----------------------------------------------------------------------------
@pytest.fixture(scope="module")
def job19():
    p = Project(tools())
    p.load_svg(BOARDS19)
    return p


def test_opening_the_drawing_lists_boards_and_colors_without_the_board_rectangles(job19):
    assert [(u.number, u.label) for u in job19.units()] == [(1, "Tablero 1 (620 x 1000 mm)"), (2, "Tablero 2 (620 x 1000 mm)")]
    assert sorted((r.stroke or "", r.fill or "", r.count) for r in job19.rows) == sorted(
        [(RED, "", 13), (ORANGE, "", 10), (YELLOW, "", 8), ("", BLACK, 95)])          # (el orden es el de corte, no el del dibujo)
    assert "#2b2a29" not in {r.stroke for r in job19.rows}                             # el rectangulo del tablero no se corta
    assert set(job19.overrides) == {1, 2} and job19.selected_numbers() == [1, 2]


def test_plain_default_rows_give_the_same_gcode_as_the_command_line_without_recipe(tmp_path, job19):
    p = Project(tools())
    p.load_svg(BOARDS19)
    p.general = General(tool="4", thickness=19.0, margin=0.4, feed=900.0, plunge=300.0, stepdown=6.5, rotate="0")
    assert p.recipe() is None
    for n in (1, 2):
        plan = p.plan(n)
        out = tmp_path / f"{n}.nc"
        assert svg_a_gcode.main([str(BOARDS19), "--no-config", "--tool", "4", "--thickness", "19", "--margin", "0.4",
                                 "--feed", "900", "--plunge", "300", "--stepdown", "6.5", "--rotate", "0",
                                 "--board", str(n), "-o", str(out)]) == 0
        assert plan.ok
        assert_same_gcode(plan.text, out.read_text(), f"tablero {n}")


def test_recipe_rows_plus_a_per_board_offset_give_the_same_gcode_as_the_command_line(tmp_path):
    p = Project(tools())
    p.load_svg(BOARDS19)
    p.load_recipe_file(RECIPE)
    p.general = General(tool="em4", thickness=19.0, margin=0.4, rotate="0", pause_at_start=True)
    p.overrides[1].offset = (0.0, 6.97)
    for n, extra in ((1, ["--offset", "0,6.97"]), (2, [])):
        plan = p.plan(n)
        out = tmp_path / f"{n}.nc"
        assert svg_a_gcode.main([str(BOARDS19), "--no-config", "--tool", "em4", "--thickness", "19", "--margin", "0.4",
                                 "--receta", str(RECIPE), "--rotate", "0", "--pause-at-start", "--board", str(n),
                                 *extra, "-o", str(out)]) == 0
        assert plan.ok
        assert_same_gcode(plan.text, out.read_text(), f"tablero {n}")


def test_ignoring_a_row_drops_its_shapes_and_says_so():
    p = Project(tools())
    p.load_svg(BOARDS19)
    p.general = General(tool="4", thickness=19.0, feed=900.0, plunge=300.0, stepdown=6.5, rotate="0")
    base = p.plan(2)
    yellow = next(r for r in p.rows if r.display_color == YELLOW)
    black = next(r for r in p.rows if r.display_color == BLACK)
    yellow.action = black.action = "ignorar"
    plan = p.plan(2)
    assert plan.ok and plan.messages[0].text.startswith("Ignoradas en la tabla: ")
    assert len(plan.operations) < len(base.operations) and p.recipe() is None
    assert len(p.active_shapes(p.unit(2).shapes)) < len(p.unit(2).shapes)


def test_per_board_thickness_margin_rotation_and_selection():
    p = Project(tools())
    p.load_svg(BOARDS19)
    p.general = General(tool="4", thickness=19.0, margin=0.4, feed=900.0, plunge=300.0, stepdown=6.5, rotate="0")
    p.overrides[2] = BoardOverrides(thickness=6.0, margin=0.2, rotate="90", selected=False)
    s1, s2 = p.job_settings(1), p.job_settings(2)
    assert (s1.thickness, s1.margin, s1.rotate) == (19.0, 0.4, 0.0)
    assert (s2.thickness, s2.margin, s2.rotate) == (6.0, 0.2, 90.0)
    assert p.selected_numbers() == [1]
    assert p.plan(2).result.min_z == pytest.approx(-6.2) and p.plan(1).result.min_z == pytest.approx(-19.4)


@pytest.mark.parametrize("rotate,expected", [("auto", ("auto", False)), ("vertical", (None, True)), ("180", (180.0, False))])
def test_rotation_choices_map_to_job_settings(rotate, expected):
    p = Project(tools())
    p.general = General(tool="4", thickness=6.0, feed=300.0, plunge=200.0, rotate=rotate)
    s = p.job_settings(None)
    assert (s.rotate, s.portrait) == expected


@pytest.mark.parametrize("change,message", [
    (dict(tool=""), "elige una fresa"),
    (dict(tool="nada"), "No hay ninguna ficha 'nada'"),
    (dict(tool="-3"), "diametro de la fresa"),
    (dict(thickness=None), "falta el grosor"),
    (dict(thickness=0.0), "grosor del material debe ser mayor que 0"),
    (dict(margin=-1.0), "margen no puede ser negativo"),
    (dict(clearance_z=6.0), "Z de aproximacion"),
    (dict(feed=-5.0), "el avance de corte debe ser mayor que 0"),
    (dict(rotate="45x"), "el giro debe ser uno de"),
    (dict(tool="vacia", feed=None), "falta el avance de corte"),
    (dict(tool="vacia", plunge=None), "falta el avance de bajada"),
])
def test_bad_settings_are_a_plan_with_an_error_not_an_exception(change, message):
    p = Project(tools())
    p.load_svg(BOARDS19)
    base = dict(tool="4", thickness=19.0, feed=900.0, plunge=300.0, rotate="0")
    p.general = General(**{**base, **change})
    plan = p.plan(1)
    assert not plan.ok and message in plan.error and plan.messages[-1].level == "error"


def test_feed_comes_from_the_setting_then_the_tool_card_with_sources():
    p = Project(tools())
    p.rows = [OpRow(stroke=RED)]
    p.general = General(tool="em4", thickness=6.0)
    s = p.job_settings(None)
    assert (s.feed, s.plunge, s.stepdown) == (300, 200, None) and s.sources == {"feed": "ficha", "plunge": "ficha"}
    p.general.feed = 500.0
    assert p.job_settings(None).feed == 500.0 and p.job_settings(None).sources["feed"] == "ajuste"
    p.general.tool = "3.175"
    assert p.resolve_tool().diameter == 3.175
    p.general.tool = "4,5"
    assert p.resolve_tool().diameter == 4.5                                          # coma decimal, como se escribe en España


def test_a_drawing_without_boards_is_one_job(tmp_path):
    svg = tmp_path / "d.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="60mm" viewBox="0 0 100 60">'
                   '<polygon points="10,10 60,10 35,50" stroke="#ff0000" fill="none"/>'
                   '<circle cx="35" cy="25" r="5" stroke="#ff0000" fill="none"/></svg>')
    p = Project(tools())
    p.load_svg(svg)
    p.general = General(tool="4", thickness=6.0, feed=300.0, plunge=200.0)
    assert [(u.number, u.label) for u in p.units()] == [(None, "Dibujo completo")] and p.selected_numbers() == [None]
    plan = p.plan(None)
    assert plan.ok and [o.side for o in plan.operations] == ["inside", "outside"]


def test_a_rectangle_with_shapes_inside_is_a_board_unless_boards_are_turned_off(tmp_path):
    svg = tmp_path / "d.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="60mm" viewBox="0 0 100 60">'
                   '<rect x="10" y="10" width="40" height="30" stroke="#ff0000" fill="none"/>'
                   '<circle cx="30" cy="25" r="5" stroke="#ff0000" fill="none"/></svg>')
    p = Project(tools())
    p.load_svg(svg)
    assert [(u.number, u.label) for u in p.units()] == [(1, "Tablero 1 (40 x 30 mm)")] and [r.count for r in p.rows] == [1]
    p.use_boards = False
    p.load_svg(svg)
    assert [u.label for u in p.units()] == ["Dibujo completo"] and [r.count for r in p.rows] == [2]


def test_output_names_follow_the_command_line_rule():
    assert Project.output_path("/x/corte.nc", None, 1) == Path("/x/corte.nc")
    assert Project.output_path("/x/corte.nc", 2, 1) == Path("/x/corte.nc")
    assert Project.output_path("/x/corte.nc", 2, 3) == Path("/x/corte_tablero2.nc")


def test_save_and_reopen_a_job_restores_settings_boards_and_rows(tmp_path):
    p = Project(tools())
    p.load_svg(BOARDS19)
    p.load_recipe_file(RECIPE)
    p.general = General(tool="em4", thickness=19.0, margin=0.4, rotate="0", end_code="G0 X10 Y10", home_at_end=True,
                       machine="real")
    p.overrides[1].offset = (0.0, 6.97)
    p.overrides[2].selected = False
    p.rows[0].feed = 777.0
    saved = tmp_path / "trabajo.cncjob"
    p.save(saved)

    q = Project(tools())
    q.load_svg(BOARDS19)
    import json
    q.apply_dict(json.loads(saved.read_text(encoding="utf-8")))
    assert q.general == p.general and q.title == p.title
    assert q.overrides[1].offset == (0.0, 6.97) and q.overrides[2].selected is False
    assert [(r.key, r.action, r.feed, r.count) for r in q.rows] == [(r.key, r.action, r.feed, r.count) for r in p.rows]
    assert_same_gcode(q.plan(1).text, p.plan(1).text)
    with pytest.raises(ProjectError, match="no es de esta version"):
        q.apply_dict({"version": 99})


def test_general_seeds_from_the_config_file_keys():
    g = General.from_config({"tool": "em4", "thickness": 6, "margin": 0.4, "rotate": "auto", "no_spindle": True,
                             "safe-z": 6.0, "home_at_end": True, "end_code": "M9", "order": "file"})
    assert (g.tool, g.thickness, g.margin, g.rotate, g.emit_spindle, g.safe_z, g.home_at_end, g.end_code) == (
        "em4", 6, 0.4, "auto", False, 6.0, True, "M9")
    assert not hasattr(g, "order")                  # el orden de corte ya no se elige: siempre de lejos a cerca (una clave vieja se ignora)
    assert General.from_config({"rotate": 90}).rotate == "90" and General.from_config({"portrait": True}).rotate == "vertical"
    assert General.from_config({"exclude_color": ["#ff0000"], "stock_color": "#000"}).tool == ""     # claves ajenas: se ignoran


# --- copia para el hilo de calculo y desplazamiento sugerido -----------------------------------------------------
def test_a_snapshot_is_independent_of_later_edits_and_plans_the_same():
    p = Project(tools())
    p.load_svg(BOARDS19)
    p.load_recipe_file(RECIPE)
    p.general = General(tool="em4", thickness=19.0, rotate="0")
    p.overrides[1].offset = (0.0, 7.0)
    snap = p.snapshot()
    p.general.thickness = 3.0
    p.overrides[1].offset = None
    p.rows[0].action = "ignorar"
    p.title = "otro"
    assert snap.general.thickness == 19.0 and snap.overrides[1].offset == (0.0, 7.0)
    assert snap.rows[0].action != "ignorar" and snap.title != "otro" and snap.drawing is p.drawing
    ref = Project(tools())
    ref.load_svg(BOARDS19)
    ref.load_recipe_file(RECIPE)
    ref.general = General(tool="em4", thickness=19.0, rotate="0")
    ref.overrides[1].offset = (0.0, 7.0)
    assert_same_gcode(snap.plan(1).text, ref.plan(1).text)


def test_the_suggested_offset_brings_the_toolpath_inside_the_board_and_adds_to_the_current_one():
    p = Project(tools())
    p.load_svg(BOARDS19)
    p.load_recipe_file(RECIPE)
    p.general = General(tool="em4", thickness=19.0, rotate="0")
    plan = p.plan(1)
    assert plan.overhang and list(plan.overhang) == ["por abajo"]
    assert p.suggest_offset(plan, 1) == (0, 7)                         # ceil(2.98) + 4 mm de holgura
    p.overrides[1].offset = (0, 7)
    assert p.plan(1).overhang == {} and p.suggest_offset(p.plan(1), 1) is None
    p.overrides[1].offset = (0, 1)                                      # sigue saliendose ~2 mm: se suma a lo que hay
    again = p.plan(1)
    assert list(again.overhang) == ["por abajo"] and p.suggest_offset(again, 1) == (0, 7)   # 1 + ceil(1.98) + 4
    assert p.suggest_offset(p.plan(2), 2) is None


def test_write_saves_one_file_per_planned_board_exactly_as_the_command_line_does(tmp_path):
    p = Project(tools())
    p.load_svg(BOARDS19)
    p.general = General(tool="4", thickness=19.0, margin=0.4, feed=900.0, plunge=300.0, stepdown=6.5, rotate="0")
    p.overrides[2].selected = False
    plans = p.plan_selected()
    paths = p.write(tmp_path / "job19.nc", plans)
    assert paths == [tmp_path / "job19.nc"]                                           # con un solo tablero, el nombre tal cual
    cli = tmp_path / "cli.nc"
    assert svg_a_gcode.main([str(BOARDS19), "--no-config", "--tool", "4", "--thickness", "19", "--margin", "0.4", "--feed",
                             "900", "--plunge", "300", "--stepdown", "6.5", "--rotate", "0", "--board", "1",
                             "-o", str(cli)]) == 0
    assert_same_gcode(paths[0].read_bytes(), cli.read_bytes())
    p.overrides[2].selected = True
    paths = p.write(tmp_path / "job19.nc", p.plan_selected())
    assert [x.name for x in paths] == ["job19_tablero1.nc", "job19_tablero2.nc"] and all(x.exists() for x in paths)


def test_write_is_all_or_nothing_and_says_which_board_failed(tmp_path):
    p = Project(tools())
    p.load_svg(BOARDS19)
    p.general = General(tool="4", thickness=19.0, margin=0.4, feed=900.0, plunge=300.0, stepdown=6.5, rotate="0")
    p.overrides[2].thickness = -1.0
    with pytest.raises(ProjectError, match="Tablero 2: el grosor"):
        p.write(tmp_path / "x.nc", p.plan_selected())
    assert list(tmp_path.iterdir()) == []                                           # el tablero 1 tampoco se escribio
    p.overrides[2].thickness = None
    from cnc.pipeline import JobPlan
    with pytest.raises(ProjectError, match="ASCII"):                                  # red de seguridad: el post ya quita acentos
        p.write(tmp_path / "x.nc", [(1, JobPlan(text="G0 Z5 (ñ)"))])
    p.general.end_code = "M9 ; ñ"
    assert "ñ" not in p.plan(1).text and p.write(tmp_path / "x.nc", [(1, p.plan(1))])
    p.overrides[1].selected = p.overrides[2].selected = False
    with pytest.raises(ProjectError, match="ningun tablero marcado"):
        p.plan_selected()


def test_hints_say_which_value_an_empty_box_will_use():
    p = Project({"em4": Tool(diameter=4, feed_xy=300, feed_z=200, stepdown=2.0, stepover=0.4), "vacia": Tool(diameter=3)})
    p.general = General(tool="em4")
    assert p.tool_hints() == {"feed": "300 (ficha)", "plunge": "200 (ficha)", "stepdown": "2 (ficha)",
                              "stepover": "40 (ficha)"}
    p.general.feed = 900.0
    assert p.tool_hints()["feed"] == "300 (ficha)" and p.tool_hints(for_rows=True)["feed"] == "900 (trabajo)"
    assert p.tool_hints(for_rows=True)["plunge"] == "200 (ficha)"
    p.general.tool = "vacia"
    assert p.tool_hints() == {} and p.tool_hints(for_rows=True) == {"feed": "900 (trabajo)"}
    p.general.tool = "no existe"
    assert p.tool_hints() == {}                                                    # sin fresa valida, sin pistas (y sin error)


def test_gui_messages_replace_the_command_line_advice_with_window_wording():
    from cnc.project import gui_messages
    p = Project(tools())
    p.load_svg(BOARDS19)
    p.load_recipe_file(RECIPE)
    p.general = General(tool="em4", thickness=19.0, rotate="0")
    plan = p.plan(1)
    cli = [m for m in plan.messages if m.code == "overhang"]
    gui = [m for m in gui_messages(plan) if m.code == "overhang"]
    assert len(cli) == len(gui) == 1 and "--offset" in cli[0].text and "--offset" not in gui[0].text
    assert gui[0].text.startswith("La trayectoria se sale del tablero por abajo 3.0 mm") and gui[0].level == "warning"
    assert [m.text for m in gui_messages(plan) if m.code != "overhang"] == [m.text for m in plan.messages if m.code != "overhang"]


# --- arranque ------------------------------------------------------------------------------------------------
def test_startup_reads_config_tools_and_default_recipe_from_the_folders(tmp_path):
    from cnc.project import startup_project
    app, cwd = tmp_path / "app", tmp_path / "cwd"
    app.mkdir(), cwd.mkdir()
    (app / "herramientas.json").write_text(
        '{"herramientas": {"em4": {"diametro": 4, "feed": 300, "entrada": 200}}}', encoding="utf-8")
    (app / "cnc_app.toml").write_text('thickness = 19\nmargin = 0.4\nexclude_color = ["#00ff00"]\n'
                                      'receta = "mi_receta.toml"\n', encoding="utf-8")
    (app / "mi_receta.toml").write_text(RECIPE.read_text(encoding="utf-8"), encoding="utf-8")
    (app / "maquinas.json").write_text(SHIPPED_MACHINES.read_text(encoding="utf-8"), encoding="utf-8")
    project, notes = startup_project(cwd, app)
    assert notes == [] and list(project.tools) == ["em4"] and list(project.machines) == ["emulador"] and project.general.thickness == 19
    assert project.exclude_colors == ["#00ff00"] and project.default_recipe == str(app / "mi_receta.toml")
    project.load_svg(BOARDS19)
    msg = project.apply_default_recipe()
    assert msg.level == "info" and [r.action for r in project.rows].count("ignorar") >= 1
    (cwd / "cnc_app.toml").write_text("thickness = 6\n", encoding="utf-8")        # la carpeta actual manda
    assert startup_project(cwd, app)[0].general.thickness == 6


def test_startup_packaged_program_reads_tools_from_the_program_folder(tmp_path, monkeypatch):
    """Empaquetado, las fichas son las de junto al programa aunque se lance desde otra carpeta (el editor guarda ahi)."""
    import sys
    from cnc.project import startup_project
    app, cwd = tmp_path / "app", tmp_path / "cwd"
    app.mkdir(), cwd.mkdir()
    for folder, ident in ((app, "del_programa"), (cwd, "de_la_carpeta")):
        (folder / "herramientas.json").write_text(
            '{"herramientas": {"%s": {"diametro": 4}}}' % ident, encoding="utf-8")
        (folder / "maquinas.json").write_text(SHIPPED_MACHINES.read_text(encoding="utf-8"), encoding="utf-8")
    assert list(startup_project(cwd, app)[0].tools) == ["de_la_carpeta"]          # desde el codigo fuente manda la actual
    assert startup_project(cwd, app)[0].machines_path == cwd / "maquinas.json"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    project = startup_project(cwd, app)[0]
    assert list(project.tools) == ["del_programa"] and project.tools_path == app / "herramientas.json"
    assert project.machines_path == app / "maquinas.json"


def test_startup_problems_are_messages_not_exceptions(tmp_path):
    from cnc.project import startup_project
    project, notes = startup_project(tmp_path, tmp_path)
    assert [(m.level, "herramientas.json" in m.text) for m in notes if m.code != "machines"] == [("warning", True)]
    assert project.tools == {}
    assert [(m.level, "maquinas.json" in m.text) for m in notes if m.code == "machines"] == [("error", True)]
    assert project.machines == {}                                        # sin archivo no se supone una maquina
    (tmp_path / "maquinas.json").write_text(SHIPPED_MACHINES.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "herramientas.json").write_text("{esto no es json", encoding="utf-8")
    (tmp_path / "cnc_app.toml").write_text("thickness = = 2", encoding="utf-8")
    (tmp_path / "x").mkdir()
    project, notes = startup_project(tmp_path, tmp_path)
    assert [m.level for m in notes] == ["error", "error"] and project.general.tool == ""
    (tmp_path / "cnc_app.toml").write_text('receta = "no_existe.toml"\n', encoding="utf-8")
    (tmp_path / "herramientas.json").write_text('{"herramientas": {}}', encoding="utf-8")
    project, notes = startup_project(tmp_path, tmp_path)
    project.load_svg(BOARDS19)
    assert notes == [] and project.apply_default_recipe().level == "warning"
    assert all(r.action == "corte" for r in project.rows)                            # la tabla sigue como estaba


def test_reset_rows_goes_back_to_plain_cuts():
    p = Project(tools())
    p.load_svg(BOARDS19)
    p.load_recipe_file(RECIPE)
    assert not plain(p.rows)
    p.reset_rows()
    assert plain(p.rows) and p.title == "" and p.recipe_file == "" and sorted(r.count for r in p.rows) == [8, 10, 13, 95]


def test_a_row_that_is_not_through_needs_a_depth():
    rows = [OpRow(stroke=RED, action="corte", through=False)]
    with pytest.raises(RecipeError, match="falta la profundidad"):
        build_recipe(rows)
    p = Project(tools())
    p.load_svg(BOARDS19)
    p.rows[0].through = False
    with pytest.raises(ProjectError, match="falta la profundidad"):
        p.recipe()
    with pytest.raises(ProjectError, match="falta la profundidad"):
        p.recipe_text()
    p.general = General(tool="em4", thickness=19.0)
    plan = p.plan(1)
    assert plan.error and "profundidad" in plan.error
    p.rows[0].depth = 5
    assert p.recipe() is not None


# --- sujecion de la plancha ---------------------------------------------------------------------------------------
def test_hold_settings_and_edited_points_survive_saving_a_job(tmp_path):
    import json
    from cnc.holddown import HoldSettings
    p = Project(tools())
    p.load_svg(BOARDS19)
    p.hold = HoldSettings(kind="tirafondo", reach=250.0, kerf_margin=12.0, edge_margin=None, mark_z=3.0,
                          min_gap=230.0, corner_clear=120.0, min_points=8, cut_clear=27.5, grid=False, bottom_row=65.0)
    p.hold_points = {1: [(15.0, 15.0), (300.5, 400.25)], 2: []}
    saved = tmp_path / "t.cncjob"
    p.save(saved)
    q = Project(tools())
    q.load_svg(BOARDS19)
    q.apply_dict(json.loads(saved.read_text(encoding="utf-8")))
    assert q.hold == p.hold and q.hold_points == {1: [(15.0, 15.0), (300.5, 400.25)], 2: []}      # [] = "sin puntos", no "propuesta"
    assert isinstance(q.hold.min_points, int) and q.hold.min_points == 8
    assert (q.hold.cut_clear, q.hold.grid, q.hold.bottom_row) == (27.5, False, 65.0) and q.hold.grid is False


def test_the_new_proposal_rules_are_written_in_the_job_file_under_their_own_names(tmp_path):
    from cnc.holddown import HoldSettings
    p = Project(tools())
    p.load_svg(BOARDS19)
    p.hold = HoldSettings(cut_clear=27.5, grid=False, bottom_row=65.0)
    raw = p.to_dict()["sujecion"]
    assert (raw["distancia_cortes"], raw["filas_y_columnas"], raw["fila_borde_inferior"]) == (27.5, False, 65.0)


def test_a_job_saved_before_hold_existed_opens_with_the_usual_values():
    from cnc.holddown import HoldSettings
    p = Project(tools())
    p.load_svg(BOARDS19)
    data = p.to_dict()
    del data["sujecion"]
    p.hold, p.hold_points = HoldSettings(kind="tirafondo"), {1: [(1.0, 1.0)]}
    p.apply_dict(data)
    assert p.hold == HoldSettings() and p.hold_points == {}


def test_a_hold_section_with_rubbish_does_not_stop_the_job_from_opening():
    from cnc.holddown import HoldSettings
    p = Project(tools())
    p.load_svg(BOARDS19)
    data = p.to_dict()
    data["sujecion"] = {"tipo": "grapa", "alcance": "mucho", "puntos": {"1": [[5, 6], ["a", 2], [7]], "x": [[1, 2]], "2": [[8, 9]]}}
    p.apply_dict(data)
    assert p.hold == HoldSettings() and p.hold_points == {2: [(8.0, 9.0)]}      # la lista del 1 se salta entera
    data["sujecion"] = "no soy un diccionario"
    p.apply_dict(data)
    assert p.hold == HoldSettings() and p.hold_points == {}
    data["sujecion"] = {"tipo": "tirafondo", "alcance": -4}
    p.apply_dict(data)
    assert p.hold == HoldSettings()                                              # un alcance que no vale: todo el ajuste vuelve al de siempre


def test_a_hold_section_from_before_the_proposal_rules_gets_the_usual_values_for_them():
    from cnc.holddown import HoldSettings
    p = Project(tools())
    p.load_svg(BOARDS19)
    data = p.to_dict()
    data["sujecion"] = {"tipo": "tirafondo", "alcance": 250, "margen_corte": 12, "altura_marca": 3}
    p.apply_dict(data)
    d = HoldSettings()
    assert (p.hold.kind, p.hold.reach, p.hold.kerf_margin, p.hold.mark_z) == ("tirafondo", 250.0, 12.0, 3.0)
    assert (p.hold.min_gap, p.hold.corner_clear, p.hold.min_points) == (d.min_gap, d.corner_clear, d.min_points)
    assert (p.hold.cut_clear, p.hold.grid, p.hold.bottom_row) == (d.cut_clear, d.grid, d.bottom_row)


@pytest.mark.parametrize("key, value", [("separacion_minima", -1), ("separacion_minima", "poca"), ("distancia_esquinas", -3),
                                        ("puntos_minimos", 2.5), ("puntos_minimos", -2), ("puntos_minimos", "muchos"),
                                        ("puntos_minimos", float("inf")), ("puntos_minimos", None), ("puntos_minimos", 10 ** 400),
                                        ("distancia_cortes", -1), ("distancia_cortes", "poca"), ("distancia_cortes", None),
                                        ("fila_borde_inferior", -5), ("fila_borde_inferior", "abajo"),
                                        ("filas_y_columnas", "si"), ("filas_y_columnas", 1), ("filas_y_columnas", None)])
def test_a_proposal_rule_that_does_not_make_sense_sends_the_whole_hold_section_back_to_the_usual_values(key, value):
    from cnc.holddown import HoldSettings
    p = Project(tools())
    p.load_svg(BOARDS19)
    data = p.to_dict()
    data["sujecion"] = {"tipo": "tirafondo", "alcance": 250, key: value, "puntos": {"1": [[5, 6]]}}
    p.apply_dict(data)
    assert p.hold == HoldSettings() and p.hold_points == {1: [(5.0, 6.0)]}      # los puntos se conservan


def test_opening_another_drawing_forgets_the_points_but_keeps_the_settings():
    from cnc.holddown import HoldSettings
    p = Project(tools())
    p.load_svg(BOARDS19)
    p.hold, p.hold_points = HoldSettings(kind="tirafondo", reach=200.0), {1: [(15.0, 15.0)]}
    p.load_svg(BOARDS19)
    assert p.hold_points == {} and p.hold.kind == "tirafondo" and p.hold.reach == 200.0


def test_a_snapshot_copies_the_hold_state():
    p = Project(tools())
    p.load_svg(BOARDS19)
    p.hold_points = {1: [(15.0, 15.0)]}
    s = p.snapshot()
    p.hold_points[1].append((9.0, 9.0))
    p.hold.reach = 99.0
    assert s.hold_points == {1: [(15.0, 15.0)]} and s.hold.reach == 300.0


def test_the_marks_program_is_named_and_closed_like_the_job():
    p = Project(tools())
    p.load_svg(BOARDS19)
    p.general = General(tool="em4", thickness=19.0, safe_z=6.0, return_to_origin=True)
    text = p.marks_text(1, [(15.0, 15.0), (300.0, 400.0)])
    lines = text.splitlines()
    assert lines[0] == "(Marcas de sujecion: Tablero 1 [620 x 1000 mm])" and "(2 puntos, clavo, el husillo no arranca)" in lines
    assert lines[-2:] == ["G0 X0 Y0", "M30"] and "G0 Z6" in lines            # sube a la Z segura del trabajo (6) si la marca es mas baja
    p.general = replace(p.general, home_at_end=True)
    assert p.marks_text(1, [(15.0, 15.0)]).splitlines()[-3:] == ["G4 P0.1", "$H", "M30"]
    with pytest.raises(ProjectError, match="no hay puntos"):
        p.marks_text(1, [])
    assert p.marks_path("/x/corte.nc", 2) == Path("/x/corte_tablero2_marcas.nc")


def test_the_marks_file_of_a_single_board_drawing_has_no_board_number(tmp_path):
    svg = tmp_path / "d.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="60mm" viewBox="0 0 100 60">'
                   '<rect x="10" y="10" width="40" height="30" stroke="#ff0000" fill="none"/>'
                   '<circle cx="30" cy="25" r="5" stroke="#ff0000" fill="none"/></svg>')
    p = Project(tools())
    p.load_svg(svg)
    assert p.marks_path("/x/corte.nc", 1) == Path("/x/corte_marcas.nc")
