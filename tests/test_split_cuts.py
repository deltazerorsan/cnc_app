"""Separar interiores y exteriores, y el orden de las filas (taladros, pozos, interiores, lineas, exteriores): cnc.project, sin Qt.

Una fila de Corte en Auto corta bien (huecos antes que piezas) pero es una sola operacion en la tabla: «Separar» la parte en una fila
por clase de forma, con el lado fijo, para verlas y ajustarlas por separado sin que el G-code cambie. Y la tabla va en el orden en que se
corta: si alguien lo estropea con las flechas, el trabajo avisa.
"""
import re
from dataclasses import replace
from pathlib import Path

import pytest
from gcodecmp import assert_same_gcode

from cnc import drawing_edit as de
from cnc.drawing_edit import make_circle, make_polyline, make_rectangle
from cnc.model import ContourOp, DrillOp, PocketOp, Tool
from cnc.oprows import OpRow, plain
from cnc.project import General, Project, ProjectError

DATA = Path(__file__).parent / "data"
BIG = DATA / "dibujo_grande.svg"
BOARDS19 = DATA / "tableros_19mm.svg"
K, R, B = "#000000", "#ff0000", "#0000ff"


def tools():
    return {"em4": Tool(diameter=4, name="End mill 4 mm", feed_xy=300, feed_z=200, stepdown=6.5, stepover=0.6)}


def big_drawing() -> Project:
    p = Project(tools())
    p.load_svg(BIG)
    p.general = General(tool="em4", thickness=19.0, margin=0.4, rotate="0")
    return p


def scratch() -> Project:
    p = Project(tools())
    p.new_drawing()
    p.general = General(tool="em4", thickness=19.0, margin=0.4, rotate="0")
    p.use_boards = False
    return p


def add(p, *shapes):
    return p.edit(de.add_edit(shapes))


def piece_with_hole_and_line(stroke=K) -> Project:
    """Un rectangulo con un agujero dentro y una linea abierta aparte, todo del mismo color."""
    p = scratch()
    add(p, make_rectangle(0, 0, 100, 60, stroke=stroke), make_circle(30, 30, 8, stroke=stroke),
        make_polyline([(120, 0), (150, 20)], stroke=stroke))
    return p


def titles(p):
    return [r.title for r in p.rows]


def sides_of(plan):
    return [getattr(op, "side", None) for op in plan.operations]


# --- el lado de cada vector --------------------------------------------------------------------------------------
def test_each_vector_gets_the_side_the_plain_mode_would_give_it():
    p = big_drawing()
    sides = p.cut_sides()
    assert sorted(sides) == list(range(26)) and sorted(set(sides.values())) == ["inside", "outside"]
    lv = p.levels()
    assert all(sides[i] == ("outside" if lv[i] % 2 == 0 else "inside") for i in sides)


def test_what_is_ignored_does_not_count_in_the_nesting():
    p = piece_with_hole_and_line()
    p.edit(de.add_edit([make_rectangle(-10, -10, 200, 100, stroke=R)]))                      # un recuadro de guia alrededor
    guide = next(r for r in p.rows if r.key == (R, None))
    assert p.cut_sides()[0] == "inside"                                                     # con la guia, la pieza es un hueco
    guide.action = "ignorar"
    assert p.cut_sides()[0] == "outside" and p.cut_sides()[1] == "inside"


def test_board_drawings_are_nested_inside_each_board():
    p = Project(tools())
    p.load_svg(BOARDS19)
    sides = p.cut_sides()
    assert set(sides) == set(p.selectable()) and set(sides.values()) <= {"inside", "outside", "on"}
    assert "inside" in sides.values() and "outside" in sides.values()


# --- separar ---------------------------------------------------------------------------------------------------------
def test_only_a_row_of_auto_cuts_with_two_kinds_of_shape_can_be_split():
    p = big_drawing()
    assert [r.key for r in p.splittable_rows()] == [(K, None)]
    p.rows[0].side = "fuera"
    assert p.splittable_rows() == []
    p.rows[0].side, p.rows[0].action = "auto", "vaciado"
    assert p.splittable_rows() == []


def test_splitting_makes_one_row_per_kind_in_cutting_order_with_fixed_sides():
    p = big_drawing()
    colors = p.split_cuts()
    assert len(colors) == 2 and len(set(colors)) == 2
    assert [(r.title, r.count, r.action, r.side, r.group, r.key[0]) for r in p.rows] == [
        ("Interiores", 13, "corte", "dentro", True, colors[0]), ("Exteriores", 13, "corte", "fuera", True, colors[1])]
    lv = p.levels()
    inside = {i for i, c in p.assign.items() if c == colors[0]}
    assert inside == {i for i, v in lv.items() if v % 2} and len(p.assign) == 26
    assert p.undo_label == "Separar interiores y exteriores"


def test_the_gcode_after_splitting_is_exactly_the_one_before():
    p = big_drawing()
    before = p.plan(None)
    assert plain(p.rows) and before.ok
    p.split_cuts()
    assert not plain(p.rows)
    after = p.plan(None)
    assert after.ok and after.alarms == []
    assert_same_gcode(before.text, after.text, "separado frente a sin separar")
    messages = [m.text for m in after.messages]
    assert "  Interiores: corte, 13 forma(s), pasante" in messages and "  Exteriores: corte, 13 forma(s), pasante" in messages
    assert sides_of(after) == ["inside"] * 13 + ["outside"] * 13


def test_the_new_rows_keep_the_settings_of_the_row_they_come_from():
    p = big_drawing()
    row = p.rows[0]
    row.through, row.depth, row.stepdown, row.feed, row.plunge = False, 12.0, 5.0, 250.0, 90.0
    before = p.plan(None)
    assert before.ok
    p.split_cuts()
    for r in p.rows:
        assert (r.through, r.depth, r.stepdown, r.feed, r.plunge, r.action) == (False, 12.0, 5.0, 250.0, 90.0, "corte")
    assert_same_gcode(before.text, p.plan(None).text, "con ajustes propios")


def test_after_splitting_each_row_can_be_changed_on_its_own():
    p = big_drawing()
    p.split_cuts()
    a, b = p.plan(None), None
    inner = p.rows[0]
    inner.feed = 150.0
    b = p.plan(None)
    assert b.ok and b.alarms == [] and sides_of(b) == sides_of(a)                          # los lados no se desordenan
    assert b.text != a.text and "F150" in b.text
    outer_only = [m.text for m in b.messages if m.text.startswith("  Exteriores")]
    assert outer_only == ["  Exteriores: corte, 13 forma(s), pasante"]


def test_open_lines_get_their_own_row_between_the_holes_and_the_pieces():
    p = piece_with_hole_and_line()
    before = p.plan(None)
    assert before.ok and sides_of(before) == ["inside", "on", "outside"]
    p.split_cuts()
    assert [(r.title, r.side, r.count) for r in p.rows] == [
        ("Interiores", "dentro", 1), ("Líneas", "linea", 1), ("Exteriores", "fuera", 1)]
    after = p.plan(None)
    assert sides_of(after) == ["inside", "on", "outside"]
    assert_same_gcode(before.text, after.text, "con linea abierta")


def test_a_row_with_two_kinds_is_the_only_one_that_can_be_chosen_without_naming_it():
    p = piece_with_hole_and_line()
    p.edit(de.add_edit([make_circle(300, 30, 5, stroke=R)]))                                # el rojo solo tiene una pieza
    assert [r.key for r in p.splittable_rows()] == [(K, None)]
    p.split_cuts()                                                                          # sin decir cual: la unica
    assert (R, None) in {r.key for r in p.rows} and titles(p).count("Exteriores") == 1


def test_which_row_to_split_has_to_be_said_when_there_are_two():
    p = piece_with_hole_and_line()
    p.edit(de.add_edit([make_rectangle(300, 0, 60, 60, stroke=R), make_circle(320, 30, 5, stroke=R)]))
    assert len(p.splittable_rows()) == 2
    with pytest.raises(ProjectError, match="varias filas"):
        p.split_cuts()
    p.split_cuts((R, None))
    assert [r.key for r in p.splittable_rows()] == [(K, None)]


@pytest.mark.parametrize("what, expect", [("pieces", "exteriores"), ("holes", "interiores"), ("lines", "líneas")])
def test_there_is_nothing_to_split_in_a_row_of_only_one_kind(what, expect):
    p = scratch()
    shapes = {"pieces": [make_rectangle(0, 0, 50, 50, stroke=K), make_rectangle(100, 0, 50, 50, stroke=K)],
              "holes": [make_rectangle(0, 0, 100, 100, stroke=R), make_circle(50, 50, 5, stroke=K),
                        make_circle(20, 20, 5, stroke=K)],
              "lines": [make_polyline([(0, 0), (10, 10)], stroke=K), make_polyline([(20, 0), (30, 10)], stroke=K)]}[what]
    add(p, *shapes)
    with pytest.raises(ProjectError, match=f"solo tiene {expect}"):
        p.split_cuts((K, None))
    assert p.undo_label == "Dibujar forma"                                                  # y no ha tocado nada


def test_a_row_that_is_not_a_cut_or_has_its_side_fixed_is_not_split():
    p = big_drawing()
    p.rows[0].action = "vaciado"
    with pytest.raises(ProjectError, match="solo se separan las filas de Corte"):
        p.split_cuts((K, None))
    p.rows[0].action, p.rows[0].side = "corte", "dentro"
    with pytest.raises(ProjectError, match="ya tiene el lado fijado"):
        p.split_cuts((K, None))
    with pytest.raises(ProjectError, match="esa fila no existe"):
        p.split_cuts(("#123456", None))
    assert p.assign == {} and p.undo_label == ""


def test_with_nothing_to_split_and_no_row_named_it_says_so():
    p = scratch()
    add(p, make_rectangle(0, 0, 50, 50, stroke=K))
    with pytest.raises(ProjectError, match="no hay ninguna fila"):
        p.split_cuts()
    with pytest.raises(ProjectError, match="abre un dibujo"):
        Project(tools()).split_cuts()


def test_splitting_can_be_undone_and_redone():
    p = big_drawing()
    p.split_cuts()
    assert p.undo() == "Separar interiores y exteriores"
    assert p.assign == {} and [(r.key, r.count, r.side, r.group) for r in p.rows] == [((K, None), 26, "auto", False)]
    assert p.redo() == "Separar interiores y exteriores"
    assert titles(p) == ["Interiores", "Exteriores"] and [r.side for r in p.rows] == ["dentro", "fuera"]


def test_the_split_rows_are_saved_and_come_back_in_the_same_order(tmp_path):
    p = piece_with_hole_and_line()
    p.split_cuts()
    p.rows[0].feed = 123.0
    path = tmp_path / "t.cncjob"
    p.save(path)
    import json
    data = json.loads(path.read_text(encoding="utf-8"))
    assert [g["nombre"] for g in data["grupos"]] == ["Interiores", "Líneas", "Exteriores"]
    q = Project(tools())
    q.new_drawing()
    q.use_boards = False
    q.apply_dict(data)
    assert [(r.title, r.side, r.feed) for r in q.rows] == [("Interiores", "dentro", 123.0), ("Líneas", "linea", None),
                                                           ("Exteriores", "fuera", None)]


def test_a_group_is_split_with_names_that_say_where_they_come_from():
    p = piece_with_hole_and_line()
    p.create_group([0, 1, 2], "Marco", "corte")
    assert titles(p) == ["Marco"]
    p.split_cuts(p.rows[0].key)
    assert titles(p) == ["Marco: interiores", "Marco: líneas", "Marco: exteriores"]


def test_a_name_that_is_already_in_the_table_gets_a_number():
    p = piece_with_hole_and_line()
    p.edit(de.add_edit([make_circle(300, 30, 5, stroke=R)]))
    p.create_group([3], "Interiores", "corte")                                              # el rojo, con el nombre que va a salir
    p.split_cuts((K, None))
    assert sorted(titles(p)) == sorted(["Interiores", "Interiores (2)", "Líneas", "Exteriores"])


def test_the_other_rows_stay_where_they_belong_in_cutting_order():
    p = big_drawing()
    lv = p.levels()
    p.create_group(sorted(i for i, v in lv.items() if v in (1, 2)), "Pozo", "vaciado")
    p.split_cuts()
    assert [(r.title, r.action, r.side) for r in p.rows] == [
        ("Pozo", "vaciado", "auto"), ("Interiores", "corte", "dentro"), ("Exteriores", "corte", "fuera")]


# --- el orden de las filas ---------------------------------------------------------------------------------------
def test_drills_pockets_interiors_lines_and_exteriors_are_cut_in_that_order():
    p = big_drawing()
    lv = p.levels()
    p.create_group(sorted(i for i, v in lv.items() if v == 3), "Agujeros", "taladro")
    p.create_group(sorted(i for i, v in lv.items() if v in (1, 2)), "Pozo", "vaciado")
    for r in p.rows:
        if r.group:
            r.through, r.depth, r.stepdown = False, 10.0, 5.0
    assert [(r.title, r.action) for r in p.rows] == [("Agujeros", "taladro"), ("Pozo", "vaciado"),
                                                    ("trazo #000000", "corte")]
    assert p.order_problem() is None
    plan = p.plan(None)
    assert plan.ok and [type(op) for op in plan.operations][0] is DrillOp and PocketOp in map(type, plan.operations)
    kinds = [type(op).__name__ for op in plan.operations]
    assert kinds.index("DrillOp") < kinds.index("PocketOp") < kinds.index("ContourOp")
    assert [a for a in plan.alarms if "orden" in a] == []


def test_the_table_is_in_cutting_order_as_soon_as_the_drawing_is_opened(tmp_path):
    svg = tmp_path / "dos.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="200mm" height="100mm" viewBox="0 0 200 100">'
                   f'<path d="M10,10 H160 V90 H10 Z" fill="none" stroke="{K}" stroke-width="0.5"/>'
                   f'<path d="M50,50 m-10,0 a10,10 0 1,0 20,0 a10,10 0 1,0 -20,0 Z" fill="none" stroke="{R}" stroke-width="0.5"/></svg>', encoding="utf-8")
    p = Project(tools())
    p.use_boards = False                                                 # sin esto el rectangulo grande se tomaria por un tablero
    p.load_svg(svg)
    assert [r.stroke for r in p.rows] == [R, K]                          # el rojo es un hueco dentro de la pieza negra


def test_a_row_that_mixes_holes_and_pieces_goes_after_the_rows_of_only_holes_and_before_those_of_only_pieces():
    p = piece_with_hole_and_line()
    add(p, make_rectangle(200, 0, 40, 40, stroke=B), make_circle(100, 100, 3, stroke=R))
    # K: pieza + hueco + linea (2 a 4); B: solo pieza (4); R: un circulo suelto (4)
    spans = {r.key[0]: p._rank_span(r) for r in p.rows}
    assert spans == {K: (2, 4), B: (4, 4), R: (4, 4)}
    assert [r.key[0] for r in p.rows][0] == K


@pytest.mark.parametrize("action, side, span", [
    ("taladro", "auto", (0, 0)), ("vaciado", "auto", (1, 1)), ("corte", "dentro", (2, 2)), ("corte", "linea", (3, 3)),
    ("corte", "fuera", (4, 4)), ("ignorar", "auto", None)])
def test_what_each_kind_of_row_cuts(action, side, span):
    p = piece_with_hole_and_line()
    row = p.rows[0]
    row.action, row.side = action, side
    assert p._rank_span(row) == span


def wrong_order() -> Project:
    p = big_drawing()
    lv = p.levels()
    p.create_group(sorted(i for i, v in lv.items() if v in (1, 2)), "Pozo", "vaciado")
    p.rows.reverse()                                                    # como si se hubiera movido con las flechas
    return p


def test_a_table_moved_by_hand_out_of_order_says_what_is_wrong():
    p = wrong_order()
    text = p.order_problem()
    assert text.startswith("El orden de las filas no es el correcto: tiene que ser taladros, pozos, interiores, líneas y exteriores.")
    assert "«Pozo» (pozos) va después de «trazo #000000» (exteriores)" in text
    assert text.endswith("Pulsa «Ordenar» para ponerlas bien.") and "Separar" not in text      # ordenar basta


def test_an_out_of_order_table_is_an_alarm_that_has_to_be_confirmed_when_generating():
    p = wrong_order()
    plan = p.plan(None)
    assert plan.ok
    alarms = [a for a in plan.alarms if a.startswith("El orden de las filas no es el correcto")]
    assert len(alarms) == 1 and "«Pozo» (pozos)" in alarms[0]
    msgs = [m for m in plan.messages if m.text == alarms[0]]
    assert len(msgs) == 1 and msgs[0].level == "warning" and msgs[0].severe
    p.sort_rows()
    again = p.plan(None)
    assert again.ok and not [a for a in again.alarms if "orden" in a]


def test_a_plain_job_has_no_order_to_get_wrong():
    p = big_drawing()
    p.rows[0].action = "corte"
    assert plain(p.rows) and p.order_problem() is None
    plan = p.plan(None)
    assert plan.ok and plan.alarms == []


def test_two_rows_that_both_mix_holes_and_pieces_cannot_be_put_in_order_by_sorting():
    p = piece_with_hole_and_line(K)
    add(p, make_rectangle(300, 0, 60, 60, stroke=R), make_circle(320, 30, 5, stroke=R))
    p.rows[0].feed = 200.0                                              # ya no es todo corte normal
    text = p.order_problem()
    assert "«trazo #ff0000» (interiores) va después de «trazo #000000» (exteriores)" in text
    assert text.endswith("sepárala con «Separar interiores y exteriores» y pulsa «Ordenar».")
    p.sort_rows()
    assert p.order_problem() == text                                    # ordenar no lo arregla
    p.split_cuts((K, None))
    p.split_cuts((R, None))
    assert p.order_problem() is None


def test_sorting_puts_the_rows_in_order_and_leaves_what_is_ignored_where_it_was():
    p = big_drawing()
    lv = p.levels()
    p.create_group(sorted(i for i, v in lv.items() if v == 3), "Agujeros", "taladro")
    p.create_group(sorted(i for i, v in lv.items() if v == 2), "Isla", "ignorar")
    assert titles(p)[0] == "Agujeros"
    p.rows.insert(0, p.rows.pop(1))                                     # el corte pasa delante del taladro
    assert [r.action for r in p.rows] == ["corte", "taladro", "ignorar"] and p.order_problem()
    here = 2
    assert p.sort_rows() is True
    assert next(i for i, r in enumerate(p.rows) if r.action == "ignorar") == here
    assert [r.title for r in p.rows if r.active][0] == "Agujeros"
    assert p.sort_rows() is False                                       # ya estaba


def test_sorting_in_place_keeps_the_list_the_table_shares():
    p = wrong_order()
    rows = p.rows
    p.sort_rows()
    assert p.rows is rows and p.order_problem() is None


def test_changing_a_row_puts_it_in_place_only_if_the_rest_was_in_order():
    p = big_drawing()
    p.split_cuts()
    inner, outer = p.rows
    outer.action = "taladro"
    assert p.sort_rows(outer) is True and p.rows == [outer, inner]       # el resto (una fila) iba bien
    # si las demas estan a mano en otro orden, no se toca nada
    q = big_drawing()
    lv = q.levels()
    q.create_group(sorted(i for i, v in lv.items() if v == 3), "Agujeros", "taladro")
    q.create_group(sorted(i for i, v in lv.items() if v in (1, 2)), "Pozo", "vaciado")
    q.rows.reverse()                                                     # [corte, pozo, taladro]: mal
    mixed = q.rows[0]
    mixed.side = "fuera"
    before = list(q.rows)
    assert q.sort_rows(mixed) is False and q.rows == before


def test_restoring_the_table_puts_everything_back_in_order():
    p = wrong_order()
    p.reset_rows()
    assert p.order_problem() is None and plain(p.rows) and sorted(titles(p)) == ["Pozo", "trazo #000000"]


def test_a_recipe_keeps_the_order_of_its_author_and_the_table_complains_if_it_is_not_the_right_one(tmp_path):
    recipe = tmp_path / "r.toml"
    recipe.write_text('[[operacion]]\nnombre = "Cortes"\ntipo = "corte"\ntrazo = "#000000"\npasante = true\n\n'
                      '[[operacion]]\nnombre = "Agujeros"\ntipo = "taladro"\ntrazo = "#ff0000"\npasante = true\npasada = 6\n', encoding="utf-8")
    p = piece_with_hole_and_line()
    p.edit(de.add_edit([make_circle(300, 30, 3, stroke=R)]))
    p.load_recipe_file(recipe)
    assert [r.name for r in p.rows] == ["Cortes", "Agujeros"]
    assert p.order_problem() and "taladros" in p.order_problem()
    plan = p.plan(None)
    assert plan.ok and any(a.startswith("El orden de las filas") for a in plan.alarms)


# --- «Auto» que decidiria mal --------------------------------------------------------------------------------------
def test_an_auto_row_of_holes_that_would_be_cut_from_outside_is_an_alarm():
    p = big_drawing()
    lv = p.levels()
    p.create_group(sorted(i for i, v in lv.items() if v == 3), "Agujeros", "corte")
    p.create_group(sorted(i for i, v in lv.items() if v == 0), "Piezas", "corte")
    p.create_group(sorted(i for i, v in lv.items() if v in (1, 2)), "Resto", "ignorar")
    row = next(r for r in p.rows if r.title == "Agujeros")
    assert p.plan(None).alarms == []                                    # todo en corte normal: se corta todo junto
    row.feed = 150.0                                                    # ahora hay receta y «Auto» solo mira dentro de la fila
    plan = p.plan(None)
    notes = [a for a in plan.alarms if a.startswith("«Agujeros» está en «Auto»")]
    assert len(notes) == 1 and "5 forma(s) se cortarían por fuera siendo huecos" in notes[0]
    assert "Separar interiores y exteriores" in notes[0]
    row.side = "dentro"
    assert [a for a in p.plan(None).alarms if "«Auto»" in a] == []


def test_split_rows_never_trigger_that_alarm():
    p = big_drawing()
    p.split_cuts()
    p.rows[0].feed = 100.0
    p.rows[1].feed = 200.0
    plan = p.plan(None)
    assert plan.ok and plan.alarms == []


def test_an_auto_row_that_decides_the_same_as_the_whole_drawing_raises_no_alarm():
    p = big_drawing()
    lv = p.levels()
    p.create_group(sorted(i for i, v in lv.items() if v in (1, 2)), "Pozo", "vaciado")
    row = next(r for r in p.rows if r.key == (K, None))
    row.feed = 250.0                                                    # el resto (niveles 0 y 3) decide igual solo que con todo
    plan = p.plan(None)
    assert plan.ok and plan.alarms == []


def test_an_auto_row_of_pieces_that_would_be_cut_from_inside_is_an_alarm_too():
    p = big_drawing()
    lv = p.levels()
    p.create_group(sorted(i for i, v in lv.items() if v in (1, 2)), "Medio", "corte")      # 8 huecos y 5 piezas segun el dibujo
    row = next(r for r in p.rows if r.title == "Medio")
    row.feed = 150.0
    plan = p.plan(None)
    notes = [a for a in plan.alarms if a.startswith("«Medio» está en «Auto»")]
    assert len(notes) == 1
    assert "5 forma(s) se cortarían por dentro siendo piezas" in notes[0] and "8 forma(s) se cortarían por fuera siendo huecos" in notes[0]
    assert " y " in notes[0]


def test_an_auto_row_of_open_lines_goes_after_the_holes_and_before_the_pieces():
    p = scratch()
    add(p, make_polyline([(0, 0), (30, 10)], stroke=K), make_rectangle(100, 0, 40, 40, stroke=R),
        make_rectangle(200, 0, 60, 60, stroke=B), make_circle(230, 30, 5, stroke=B))
    by = {r.key[0]: r for r in p.rows}
    by[K].feed = 200.0                                                  # fuera de «todo normal»: la tabla manda
    assert {k: p._rank_span(r) for k, r in by.items()} == {K: (3, 3), R: (4, 4), B: (2, 4)}
    p.rows[:] = [by[R], by[K]]
    assert "«trazo #000000» (líneas) va después de «trazo #ff0000» (exteriores)" in p.order_problem()
    assert p.sort_rows() and [r.key[0] for r in p.rows] == [K, R]
    assert p.order_problem() is None


def test_taking_vectors_out_of_a_group_puts_the_color_row_that_comes_back_in_its_place():
    p = scratch()
    add(p, make_rectangle(0, 0, 100, 100, stroke=K), make_circle(50, 50, 10, stroke=R))
    assert [r.key[0] for r in p.rows] == [R, K]                         # el hueco rojo, antes que la pieza negra
    p.create_group([1], "Hueco", "corte")
    assert [r.title for r in p.rows] == ["Hueco", "trazo #000000"]
    p.ungroup([1])                                                       # el rojo vuelve como fila nueva
    assert [r.key[0] for r in p.rows] == [R, K] and p.order_problem() is None
    p.undo()
    assert [r.title for r in p.rows] == ["Hueco", "trazo #000000"]


def test_a_pocket_before_a_drill_is_flagged_in_the_plan_as_well():
    p = big_drawing()
    lv = p.levels()
    p.create_group(sorted(i for i, v in lv.items() if v == 3), "Agujeros", "taladro")
    p.create_group(sorted(i for i, v in lv.items() if v in (1, 2)), "Pozo", "vaciado")
    for r in p.rows:
        if r.group:
            r.through, r.depth, r.stepdown = False, 10.0, 5.0
    assert p.order_problem() is None
    by = {r.title: r for r in p.rows}
    p.rows[:] = [by["Pozo"], by["Agujeros"], by["trazo #000000"]]
    assert "«Agujeros» (taladros) va después de «Pozo» (pozos)" in p.order_problem()
    plan = p.plan(None)
    assert plan.ok
    assert [a for a in plan.alarms if "«Agujeros» (taladros) va después de «Pozo» (pozos)" in a]


def test_two_auto_rows_that_mix_holes_and_pieces_are_flagged_in_the_plan_with_the_advice_to_split():
    p = piece_with_hole_and_line(K)
    add(p, make_rectangle(300, 0, 60, 60, stroke=R), make_circle(320, 30, 5, stroke=R))
    p.rows[0].feed = 200.0
    plan = p.plan(None)
    assert plan.ok
    notes = [a for a in plan.alarms if a.startswith("El orden de las filas no es el correcto")]
    assert len(notes) == 1 and "(interiores) va después de" in notes[0]
    assert notes[0].endswith("sepárala con «Separar interiores y exteriores» y pulsa «Ordenar».")
    p.split_cuts((K, None))
    p.split_cuts((R, None))
    again = p.plan(None)
    assert again.ok and again.alarms == []
