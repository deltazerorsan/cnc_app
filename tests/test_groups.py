"""Grupos de vectores: elegir vectores a mano y darles su propia fila en la tabla (cnc.project, sin Qt).

El caso real es un dibujo grande: 26 vectores cerrados, todos negros, que son 4 niveles de contornos concentricos. Los de nivel 0 y
3 son corte pasante (exterior y agujero); los de nivel 1 y 2 son un pozo (el nivel 2 es la isla que se respeta).
"""
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
from gcodecmp import assert_same_gcode
from shapely.geometry import box

from cnc.model import ContourOp, PocketOp, Tool
from cnc.oprows import OpRow
from cnc.project import GROUP_COLORS, General, Project, ProjectError
from cnc.recipe import load_recipe
from cnc.svg_import import ImportedShape

DATA = Path(__file__).parent / "data"
BIG = DATA / "dibujo_grande.svg"
BOARDS19 = DATA / "tableros_19mm.svg"
RECIPE = DATA / "receta_ejemplo.toml"
BLACK = "#000000"
POZO = (1, 2)                                          # niveles de anidamiento que son el pozo


def tools():
    return {"em4": Tool(diameter=4, name="End mill 4 mm", feed_xy=300, feed_z=200, stepdown=6.5, stepover=0.6)}


def big_drawing(**general) -> Project:
    p = Project(tools())
    p.load_svg(BIG)
    p.general = General(tool="em4", thickness=19.0, margin=0.4, rotate="0", **general)
    return p


def pozo_indices(p: Project) -> list:
    return sorted(i for i, lv in p.levels().items() if lv in POZO)


def with_pozo(depth=10.0) -> tuple:
    p = big_drawing()
    color = p.create_group(pozo_indices(p), "Pozo", "vaciado")
    row = next(r for r in p.rows if r.group)
    row.through, row.depth = False, depth
    return p, color


# --- niveles y vectores elegibles -----------------------------------------------------------------------------
def test_the_big_drawing_has_four_levels_of_concentric_outlines_and_every_vector_can_be_picked():
    p = big_drawing()
    lv = p.levels()
    assert {k: sum(1 for v in lv.values() if v == k) for k in range(4)} == {0: 8, 1: 8, 2: 5, 3: 5}
    assert p.selectable() == list(range(26)) and len(p.rows) == 1 and p.rows[0].key == (BLACK, None)


def test_the_rectangle_of_a_board_cannot_be_picked_but_what_is_inside_can():
    p = Project(tools())
    p.load_svg(BOARDS19)
    boards = {b.index for b in p.drawing.layout.boards}
    assert boards and not boards & set(p.selectable())
    with pytest.raises(ProjectError, match="no existe o no se puede elegir"):
        p.create_group([next(iter(boards))], "Tablero", "corte")
    inside = p.drawing.layout.boards[0].contents[0]
    assert inside in p.selectable() and p.levels()[inside] >= 0


# --- crear, mover y quitar -------------------------------------------------------------------------------------
def test_a_new_group_takes_its_vectors_out_of_their_color_and_gets_its_own_row_before_the_cuts():
    p = big_drawing()
    picked = pozo_indices(p)
    color = p.create_group(picked, "Pozo", "vaciado")
    assert color == GROUP_COLORS[0]
    assert [(r.key, r.count, r.group, r.name, r.action) for r in p.rows] == [          # un pozo se hace antes que los cortes
        ((color, None), 13, True, "Pozo", "vaciado"), ((BLACK, None), 13, False, "", "corte")]
    shapes = p.shapes()
    assert all(shapes[i].stroke == color and shapes[i].fill is None for i in picked)
    assert all(s.stroke == BLACK for i, s in enumerate(shapes) if i not in picked)
    assert all(s.stroke == BLACK for s in p.drawing.shapes)                      # el dibujo abierto no se toca
    assert p.groups() == [p.rows[0]] and p.group_color(picked[0]) == color and p.group_color(2) is None


def test_without_groups_the_shapes_are_the_drawing_itself():
    p = big_drawing()
    assert p.shapes() is p.drawing.shapes and p.assign == {}


def test_moving_to_an_existing_group_and_back_keeps_the_settings_of_the_rows_that_stay():
    p, pozo = with_pozo()
    black = next(r for r in p.rows if r.key == (BLACK, None))
    black.feed = 450.0
    other = p.create_group([2, 4], "Rebaje", "corte")
    assert sorted(r.name for r in p.groups()) == ["Pozo", "Rebaje"] and other != pozo
    p.move_to_group([2], pozo)
    rows = {r.key[0]: r for r in p.rows}
    assert {k: r.count for k, r in rows.items()} == {BLACK: 11, pozo: 14, other: 1} and p.rows[0].key[0] == pozo
    assert rows[BLACK].feed == 450.0 and rows[pozo].depth == 10.0 and rows[pozo].through is False
    p.ungroup([2, 4])
    assert [(r.key[0], r.count) for r in p.rows] == [(pozo, 13), (BLACK, 13)]    # el grupo vacio desaparece
    assert other not in set(p.assign.values())


def test_ungrouping_everything_gives_back_the_original_table():
    p, _ = with_pozo()
    p.ungroup(list(range(26)))
    assert p.assign == {} and [(r.key, r.count, r.group) for r in p.rows] == [((BLACK, None), 26, False)]
    assert p.shapes() is p.drawing.shapes


def test_a_group_color_is_never_one_the_drawing_already_uses_and_never_repeats():
    p = Project(tools())
    p.load_svg(BIG)
    p.drawing.shapes[0] = replace(p.drawing.shapes[0], stroke=GROUP_COLORS[0])      # el dibujo ya usa el primero
    p.rows = []
    p._regroup()
    first = p.create_group([1], "A", "corte")
    second = p.create_group([2], "B", "corte")
    assert (first, second) == (GROUP_COLORS[1], GROUP_COLORS[2])


def test_when_the_palette_runs_out_there_are_always_more_colors():
    p = big_drawing()
    colors = [p.create_group([i], f"G{i}", "corte") for i in range(len(GROUP_COLORS) + 3)]
    assert len(set(colors)) == len(colors) and all(len(c) == 7 and c.startswith("#") for c in colors)


@pytest.mark.parametrize("call, message", [
    (lambda p: p.create_group([1], "   ", "corte"), "necesita un nombre"),
    (lambda p: p.create_group([1], "X", "fresar"), "debe ser una de"),
    (lambda p: p.create_group([], "X", "corte"), "ninguna forma"),
    (lambda p: p.create_group([99], "X", "corte"), "no existe o no se puede elegir"),
    (lambda p: p.create_group([True], "X", "corte"), "no existe o no se puede elegir"),
    (lambda p: p.create_group(["3"], "X", "corte"), "no existe o no se puede elegir"),
    (lambda p: p.create_group([-1], "X", "corte"), "no existe o no se puede elegir"),
    (lambda p: p.move_to_group([1], "#123456"), "no existe"),
    (lambda p: p.ungroup([40]), "no existe o no se puede elegir"),
])
def test_bad_requests_say_what_is_wrong_and_change_nothing(call, message):
    p = big_drawing()
    before = (dict(p.assign), [replace(r) for r in p.rows])
    with pytest.raises(ProjectError, match=message):
        call(p)
    assert (p.assign, p.rows) == before


def test_repeated_indices_count_once():
    p = big_drawing()
    p.create_group([3, 3, 4, 3], "X", "corte")
    assert next(r for r in p.rows if r.group).count == 2


# --- la tabla, la receta y las operaciones ---------------------------------------------------------------------------
def test_resetting_the_table_keeps_the_groups_with_their_names_but_plain_cuts():
    p, color = with_pozo()
    p.title = "algo"
    p.reset_rows()
    assert [(r.key, r.action, r.name, r.group, r.through, r.depth) for r in p.rows] == [
        ((BLACK, None), "corte", "", False, True, None), ((color, None), "corte", "Pozo", True, True, None)]
    assert p.title == "" and p.assign


def test_a_recipe_of_colors_does_not_touch_the_groups():
    p = Project(tools())
    p.load_svg(DATA / "tablero_610x1015.svg")
    group_of = p.selectable()[:2]
    color = p.create_group(group_of, "Mio", "vaciado")
    mine = next(r for r in p.rows if r.group)
    mine.depth, mine.through = 4.0, False
    p.apply_recipe(load_recipe(RECIPE), "r.toml")
    assert p.rows[-1].group and p.rows[-1].key == (color, None)
    assert (p.rows[-1].action, p.rows[-1].depth, p.rows[-1].through, p.rows[-1].name) == ("vaciado", 4.0, False, "Mio")
    assert all(r.action == "ignorar" or not r.group for r in p.rows[:-1])


def test_the_operations_are_a_through_cut_of_outline_and_hole_and_a_pocket_with_islands():
    p, _ = with_pozo()
    plan = p.plan(None)
    assert plan.ok, plan.error
    ops = plan.job.operations
    kinds = [(type(op).__name__, getattr(op, "side", None)) for op in ops]
    assert kinds.count(("ContourOp", "outside")) == 8 and kinds.count(("ContourOp", "inside")) == 5
    pockets = [op for op in ops if isinstance(op, PocketOp)]
    assert len(pockets) == 8 and all(op.depth == 10.0 and not op.through for op in pockets)
    islands = sorted(len(op.shape.interiors) for op in pockets)
    assert islands == [0, 0, 0, 1, 1, 1, 1, 1]                              # las 5 letras con agujero llevan una isla
    assert all(op.through for op in ops if isinstance(op, ContourOp))
    cuts = [op for op in ops if isinstance(op, ContourOp)]
    assert len(cuts) == 13


def test_grouping_gives_exactly_the_gcode_of_recoloring_the_vectors_in_the_svg(tmp_path):
    p, color = with_pozo()
    picked = set(pozo_indices(p))
    text = BIG.read_text(encoding="utf-8")
    paths = list(re.finditer(r'<path [^>]*/>', text))
    assert len(paths) == 26
    out, last = [], 0
    for i, m in enumerate(paths):
        out.append(text[last:m.start()])
        out.append(m.group(0).replace('stroke="black"', f'stroke="{color}"') if i in picked else m.group(0))
        last = m.end()
    out.append(text[last:])
    recolored = tmp_path / "big_drawing_color.svg"
    recolored.write_text("".join(out), encoding="utf-8")
    q = Project(tools())
    q.load_svg(recolored)
    q.general = General(tool="em4", thickness=19.0, margin=0.4, rotate="0")
    by_key = {r.key: r for r in q.rows}
    assert set(by_key) == {(BLACK, None), (color, None)}
    red = by_key[(color, None)]
    red.action, red.through, red.depth = "vaciado", False, 10.0
    q.rows = [red, by_key[(BLACK, None)]]                                       # mismo orden que en la tabla del grupo
    a, b = p.plan(None), q.plan(None)
    assert a.ok and b.ok
    assert_same_gcode(a.text, b.text, "agrupado frente a recoloreado")


def test_ignoring_a_group_leaves_its_vectors_out_of_the_job():
    p, color = with_pozo()
    next(r for r in p.rows if r.group).action = "ignorar"
    plan = p.plan(None)
    assert plan.ok and not any(isinstance(op, PocketOp) for op in plan.job.operations)
    assert "Ignoradas en la tabla: 13 forma(s)" in [m.text for m in plan.messages]


def test_a_saved_recipe_names_the_group_and_can_be_read_back():
    p, color = with_pozo()
    text = p.recipe_text()
    assert 'nombre = "Pozo"' in text and f'trazo = "{color}"' in text and 'tipo = "vaciado"' in text


def test_the_snapshot_for_the_planning_thread_does_not_follow_later_changes():
    p, color = with_pozo()
    snap = p.snapshot()
    p.ungroup(list(range(26)))
    assert snap.assign and len(snap.assign) == 13 and snap.shapes()[pozo_indices(snap)[0]].stroke == color


def test_units_carry_the_group_colors_on_boards_too():
    p = Project(tools())
    p.load_svg(BOARDS19)
    first = p.drawing.layout.boards[0].contents[:3]
    color = p.create_group(first, "Tres", "corte")
    strokes = [s.stroke for s in p.units()[0].shapes]
    assert strokes.count(color) == 3 and all(s.stroke != color for s in p.units()[1].shapes)


# --- guardar y abrir el trabajo --------------------------------------------------------------------------------------
def saved(p: Project, tmp_path) -> dict:
    path = tmp_path / "t.cncjob"
    p.save(path)
    return json.loads(path.read_text(encoding="utf-8"))


def reopened(data: dict) -> Project:
    q = Project(tools())
    q.load_svg(BIG)
    q.apply_dict(data)
    return q


def test_groups_and_their_rows_survive_saving_and_opening(tmp_path):
    p, color = with_pozo(depth=11.5)
    next(r for r in p.rows if r.group).feed = 280.0
    data = saved(p, tmp_path)
    assert data["num_formas"] == 26 and data["grupos"] == [{"color": color, "nombre": "Pozo", "formas": pozo_indices(p)}]
    q = reopened(data)
    assert q.assign == p.assign and q.load_notes == []
    assert [replace(r) for r in q.rows] == [replace(r) for r in p.rows]
    pozo = q.rows[0]                                                             # el pozo, antes que los cortes
    assert pozo.group and pozo.name == "Pozo" and pozo.depth == 11.5 and pozo.feed == 280.0
    assert [s.stroke for s in q.shapes()] == [s.stroke for s in p.shapes()]
    assert_same_gcode(q.plan(None).text, p.plan(None).text, "el trabajo abierto da lo mismo")


def test_a_job_without_groups_still_opens_and_has_none(tmp_path):
    p = big_drawing()
    data = saved(p, tmp_path)
    del data["grupos"], data["num_formas"]
    q = reopened(data)
    assert q.assign == {} and [r.group for r in q.rows] == [False]


def test_opening_a_job_replaces_the_groups_of_whatever_was_open():
    p, _ = with_pozo()
    data = p.to_dict()
    data["grupos"], data["filas"] = [], [r for r in data["filas"] if not r["group"]]
    p.apply_dict(data)
    assert p.assign == {} and [r.group for r in p.rows] == [False]


def test_if_the_drawing_has_changed_the_groups_are_dropped_with_a_warning(tmp_path):
    p, _ = with_pozo()
    data = saved(p, tmp_path)
    data["num_formas"] = 25
    q = reopened(data)
    assert q.assign == {} and len(q.rows) == 1
    assert len(q.load_notes) == 1 and q.load_notes[0].level == "warning" and "ha cambiado" in q.load_notes[0].text


@pytest.mark.parametrize("entry", [
    {"color": "rojo", "nombre": "X", "formas": [1]},                     # no es un color
    {"color": "#fff", "nombre": "X", "formas": [1]},                     # color corto: no es el que usa el programa
    {"color": "rgba(255,0,0,0.5)", "nombre": "X", "formas": [1]},        # con transparencia
    {"color": "#000000", "nombre": "X", "formas": [1]},                  # el que ya usa el dibujo
    {"color": "#e6194b", "nombre": "X", "formas": []},                   # sin vectores
    {"color": "#e6194b", "nombre": "X", "formas": [1, 99]},              # un vector que no existe
    {"color": "#e6194b", "nombre": "X", "formas": ["1"]},
    {"color": "#e6194b", "nombre": "X", "formas": [True]},
    {"color": "#e6194b", "nombre": "X"},
    {"nombre": "X", "formas": [1]},
    "nada",
    7,
])
def test_a_bad_group_in_the_file_is_skipped_with_a_warning_and_the_job_still_opens(tmp_path, entry):
    data = saved(big_drawing(), tmp_path)
    data["grupos"] = [entry]
    q = reopened(data)
    assert q.assign == {} and [r.group for r in q.rows] == [False]
    assert q.load_notes and "no valen" in q.load_notes[0].text


def test_two_groups_cannot_share_a_color_in_the_file(tmp_path):
    data = saved(big_drawing(), tmp_path)
    data["grupos"] = [{"color": "#e6194b", "nombre": "A", "formas": [1]}, {"color": "#e6194b", "nombre": "B", "formas": [2]}]
    q = reopened(data)
    assert set(q.assign) == {1} and [r.name for r in q.groups()] == ["A"] and "1 grupo(s)" in q.load_notes[0].text


def test_a_group_without_name_in_the_file_gets_a_default_one(tmp_path):
    data = saved(big_drawing(), tmp_path)
    data["grupos"] = [{"color": "#e6194b", "nombre": "  ", "formas": [1]}]
    assert [r.name for r in reopened(data).groups()] == ["Grupo"]


def test_toggling_board_detection_rereads_the_drawing_and_keeps_the_groups(tmp_path):
    p = Project(tools())
    p.load_svg(BOARDS19)
    inside = p.drawing.layout.boards[0].contents[:2]
    color = p.create_group(inside, "Dos", "corte")
    data = p.to_dict()
    p.load_svg(BOARDS19)
    p.apply_dict(data)
    assert set(p.assign) == set(inside) and p.group_color(inside[0]) == color


def test_a_filled_vector_that_goes_into_a_group_loses_its_fill_and_its_row_is_the_groups_own():
    p = Project(tools())
    p.load_svg(BOARDS19)
    filled = next(i for i in p.selectable() if p.drawing.shapes[i].fill and not p.drawing.shapes[i].stroke)
    color = p.create_group([filled], "Relleno", "corte")
    shape = p.shapes()[filled]
    assert (shape.stroke, shape.fill) == (color, None) and p.drawing.shapes[filled].fill     # el dibujo sigue igual
    assert (color, None) in [r.key for r in p.rows]


def test_the_levels_are_recomputed_for_each_drawing():
    p = big_drawing()
    first = dict(p.levels())
    p.load_svg(BOARDS19)
    assert p.levels() != first and set(p.levels()) <= set(p.selectable())


def test_grouping_everything_and_ungrouping_it_gives_back_a_plain_color_row():
    p = big_drawing()
    color = p.create_group(list(range(26)), "Todo", "corte")
    assert [(r.key[0], r.group) for r in p.rows] == [(color, True)]
    p.ungroup(list(range(26)))
    assert [(r.key, r.group, r.name) for r in p.rows] == [((BLACK, None), False, "")]


def test_two_groups_keep_their_own_vectors_through_a_save(tmp_path):
    p, _ = with_pozo()
    p.create_group([2, 4], "Rebaje", "corte")
    q = reopened(saved(p, tmp_path))
    assert q.assign == p.assign and sorted(r.name for r in q.groups()) == ["Pozo", "Rebaje"]
    assert [(r.name, r.count) for r in q.groups()] == [(r.name, r.count) for r in p.groups()]


def test_a_job_with_groups_but_without_the_shape_count_is_still_accepted(tmp_path):
    p, _ = with_pozo()
    data = saved(p, tmp_path)
    del data["num_formas"]
    q = reopened(data)
    assert q.assign == p.assign and q.load_notes == []
