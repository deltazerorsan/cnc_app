"""Herramientas de dibujo dentro del proyecto (cnc.project, sin Qt): ediciones, deshacer/rehacer, grupos, tableros y guardado."""
import json
from pathlib import Path

import pytest

from cnc import drawing_edit as de
from cnc.drawing_edit import make_circle, make_polyline, make_rectangle
from cnc.model import Tool
from cnc.project import General, Project, ProjectError

DATA = Path(__file__).parent / "data"
BOARDS19 = DATA / "tableros_19mm.svg"
BIG = DATA / "dibujo_grande.svg"
K = "#000000"
R = "#ff0000"


def tools():
    return {"em4": Tool(diameter=4, name="End mill 4 mm", feed_xy=300, feed_z=200, stepdown=6.5, stepover=0.6)}


def scratch(**general) -> Project:
    p = Project(tools())
    p.new_drawing()
    p.general = General(tool="em4", thickness=19.0, margin=0.4, rotate="0", **general)
    return p


def job19() -> Project:
    p = Project(tools())
    p.load_svg(BOARDS19)
    p.general = General(tool="em4", thickness=19.0, margin=0.4, rotate="0")
    return p


def add(p, *shapes):
    return p.edit(de.add_edit(shapes))


def circle(cx=0, cy=0, r=10, stroke=K):
    return make_circle(cx, cy, r, stroke=stroke)


def wkts(p):
    return [s.shape.wkt for s in p.drawing.shapes]


def board(p, number):
    return next(b for b in p.drawing.layout.boards if b.number == number)


def flat(points):
    return [v for pt in points for v in pt]


def board_indices(p, number):
    b = board(p, number)
    return [b.index, *b.contents]


# --- dibujo nuevo -----------------------------------------------------------------------------------------------------
def test_a_new_drawing_is_empty_and_has_no_svg_behind_it():
    p = scratch()
    assert p.scratch and p.drawing.shapes == [] and p.rows == [] and p.selectable() == []
    assert [(u.number, u.shapes) for u in p.units()] == [(None, [])]
    data = p.to_dict()
    assert data["svg"] == "" and data["edicion"] == {"nuevo": True, "formas_base": 0, "color": K, "operaciones": []}


def test_drawing_on_a_new_drawing_makes_the_table_and_the_job_cut_it():
    p = scratch()
    add(p, circle(50, 30, 8), circle(100, 30, 8))
    assert [(r.key, r.count) for r in p.rows] == [((K, None), 2)] and p.drawing.layout.boards == []
    plan = p.plan(None)
    assert plan.ok and "G1" in plan.text and plan.virtual_stock


def test_a_rectangle_around_other_shapes_is_a_board_unless_the_convention_is_off():
    p = scratch()
    r = add(p, make_rectangle(0, 0, 100, 60, stroke=K), circle(50, 30, 8))
    boards = p.drawing.layout.boards
    assert len(boards) == 1 and boards[0].index == 0 and boards[0].contents == [1] and set(p.overrides) == {1}
    assert r.selected == [0, 1]
    q = scratch()
    q.use_boards = False
    add(q, make_rectangle(0, 0, 100, 60, stroke=K), circle(50, 30, 8))
    assert q.drawing.layout.boards == [] and q.overrides == {} and [(r.key, r.count) for r in q.rows] == [((K, None), 2)]


def test_shapes_added_to_a_drawing_with_boards_belong_to_the_board_they_fall_in():
    p = job19()
    inside = len(board(p, 1).contents)
    add(p, circle(300, 500, 6))
    assert len(board(p, 1).contents) == inside + 1 and len(p.drawing.shapes) - 1 in board(p, 1).contents
    assert p.plan(1).ok


def test_a_shape_outside_every_board_is_loose_and_belongs_to_no_job():
    p = job19()
    add(p, circle(660, 500, 6))                                      # entre los dos tableros
    idx = len(p.drawing.shapes) - 1
    new = p.drawing.shapes[idx]
    assert idx in p.drawing.layout.loose and idx in p.selectable()
    assert not any(s is new for u in p.units() for s in u.shapes)


def test_editing_without_a_drawing_or_with_a_bad_edit_is_an_error_and_changes_nothing():
    with pytest.raises(ProjectError, match="abre un dibujo"):
        Project(tools()).edit(de.delete_edit([0]))
    p = scratch()
    add(p, circle())
    before = wkts(p)
    with pytest.raises(ProjectError, match="no existe"):
        p.edit(de.delete_edit([5]))
    with pytest.raises(ProjectError, match="desconocida"):
        p.edit({"op": "volar"})
    assert wkts(p) == before and len(p.edits) == 1
    assert p.undo_label == "Dibujar forma" and len(p._undo) == 1


# --- deshacer y rehacer ---------------------------------------------------------------------------------------------
def test_undo_and_redo_walk_back_and_forth_through_the_edits():
    p = scratch()
    assert p.undo() == "" and p.redo() == "" and p.undo_label == "" and p.redo_label == ""
    add(p, circle(0, 0, 10))
    one = wkts(p)
    p.edit(de.move_edit([0], 5, 0))
    two = wkts(p)
    p.edit(de.delete_edit([0]))
    assert wkts(p) == [] and p.undo_label == "Borrar" and p.redo_label == ""
    assert p.undo() == "Borrar" and wkts(p) == two and p.redo_label == "Borrar" and p.undo_label == "Mover"
    assert p.undo() == "Mover" and wkts(p) == one
    assert p.undo() == "Dibujar forma" and wkts(p) == [] and p.drawing.shapes == [] and p.edits == []
    assert p.undo() == ""
    assert p.redo() == "Dibujar forma" and wkts(p) == one and len(p.edits) == 1
    assert p.redo() == "Mover" and p.redo() == "Borrar" and wkts(p) == [] and p.redo() == "" and len(p.edits) == 3


def test_a_new_edit_forgets_what_could_be_redone():
    p = scratch()
    add(p, circle())
    p.edit(de.move_edit([0], 1, 1))
    p.undo()
    assert p.redo_label == "Mover"
    p.edit(de.move_edit([0], 9, 9))
    assert p.redo_label == "" and p.redo() == ""


def test_undo_keeps_a_limited_number_of_steps():
    p = scratch()
    add(p, circle())
    for _ in range(Project.MAX_UNDO + 20):
        p.edit(de.move_edit([0], 1, 0))
    assert len(p._undo) == Project.MAX_UNDO
    for _ in range(Project.MAX_UNDO):
        assert p.undo() == "Mover"
    assert p.undo() == ""


def test_undo_restores_the_edit_list_so_a_saved_job_does_not_replay_what_was_undone():
    p = scratch()
    add(p, circle())
    p.edit(de.move_edit([0], 1, 1))
    p.undo()
    assert [e["op"] for e in p.edits] == ["anadir"] and [e["op"] for e in p.to_dict()["edicion"]["operaciones"]] == ["anadir"]
    p.redo()
    assert [e["op"] for e in p.edits] == ["anadir", "mover"]


def test_table_settings_changed_after_an_edit_survive_undoing_it():
    p = scratch()
    add(p, circle(0, 0, 10))
    p.edit(de.array_edit([0], 2, 1, 50, 0))
    row = p.rows[0]
    row.through, row.depth = False, 6.0                               # un ajuste de la tabla: no se deshace
    p.undo()
    assert len(p.drawing.shapes) == 1 and p.rows[0].depth == 6.0 and p.rows[0].count == 1 and not p.rows[0].through
    p.redo()
    assert len(p.drawing.shapes) == 2 and p.rows[0].depth == 6.0 and p.rows[0].count == 2


def test_a_row_that_disappears_with_its_shapes_comes_back_with_its_settings_when_undone():
    p = scratch()
    add(p, circle(0, 0, 10), circle(50, 0, 10, stroke=R))
    red = next(r for r in p.rows if r.key == (R, None))
    red.through, red.depth, red.feed = False, 4.0, 250.0
    p.edit(de.delete_edit([1]))
    assert [r.key for r in p.rows] == [(K, None)]
    p.undo()
    back = next(r for r in p.rows if r.key == (R, None))
    assert (back.depth, back.feed, back.through, back.count) == (4.0, 250.0, False, 1)


def test_group_changes_can_be_undone_and_redone_too():
    p = scratch()
    add(p, circle(0, 0, 10), circle(50, 0, 10), circle(100, 0, 10))
    color = p.create_group([1, 2], "Pozo", "vaciado")
    assert p.undo_label == "Grupo nuevo" and [r.name for r in p.groups()] == ["Pozo"]
    p.undo()
    assert p.assign == {} and p.groups() == [] and [(r.key, r.count) for r in p.rows] == [((K, None), 3)]
    p.redo()
    assert p.assign == {1: color, 2: color} and [r.name for r in p.groups()] == ["Pozo"] and p.groups()[0].action == "vaciado"
    p.ungroup([1])
    assert p.undo_label == "Quitar del grupo" and p.assign == {2: color}
    p.undo()
    assert p.assign == {1: color, 2: color}
    p.move_to_group([0], color)
    assert p.undo_label == "Mover a grupo" and set(p.assign) == {0, 1, 2}
    p.undo()
    assert set(p.assign) == {1, 2}


def test_a_group_that_was_undone_away_and_redone_keeps_its_name_and_row_settings():
    p = scratch()
    add(p, circle(0, 0, 10), circle(50, 0, 10))
    color = p.create_group([1], "Pozo", "vaciado")
    row = p.groups()[0]
    row.through, row.depth = False, 8.0
    p.undo()
    p.redo()
    g = p.groups()[0]
    assert g.name == "Pozo" and g.key == (color, None) and g.depth == 8.0 and g.action == "vaciado"


# --- los grupos siguen a las formas ----------------------------------------------------------------------------------
def grouped():
    p = scratch()
    add(p, circle(0, 0, 10), circle(50, 0, 10), circle(100, 0, 10), circle(150, 0, 10))
    color = p.create_group([1, 3], "Pozo", "vaciado")
    return p, color


def test_moving_keeps_the_group_and_deleting_before_it_shifts_it_down():
    p, color = grouped()
    p.edit(de.move_edit([1], 3, 3))
    assert p.assign == {1: color, 3: color}
    p.edit(de.delete_edit([0]))
    assert p.assign == {0: color, 2: color} and [(r.key, r.count) for r in p.rows] == [((color, None), 2), ((K, None), 1)]
    p.edit(de.delete_edit([0, 2]))
    assert p.assign == {} and p.groups() == [] and [r.key for r in p.rows] == [(K, None)]


def test_copies_stay_in_the_group_of_the_shape_they_come_from():
    p, color = grouped()
    p.edit(de.array_edit([1], 3, 1, 30, 0))
    assert p.assign == {1: color, 3: color, 4: color, 5: color} and p.groups()[0].count == 4


def test_the_result_of_an_offset_or_a_boolean_takes_the_group_of_its_source():
    p, color = grouped()
    p.edit(de.offset_edit([1], 2))
    assert p.assign[len(p.drawing.shapes) - 1] == color and p.groups()[0].count == 2
    p2, color2 = grouped()
    p2.edit(de.union_edit([1, 2], keep=False))                       # los primeros de la lista dan el estilo: la 1 es del grupo
    last = len(p2.drawing.shapes) - 1
    assert p2.assign.get(last) == color2


def test_a_grouped_shape_moved_out_of_its_board_keeps_its_group_but_is_no_longer_cut():
    p = job19()
    inside = board(p, 1).contents
    color = p.create_group(inside[:2], "Pozo", "vaciado")
    p.edit(de.move_edit(inside[:1], 0, 5000))
    moved = inside[0]
    assert p.assign == {inside[0]: color, inside[1]: color} and moved in p.drawing.layout.loose
    assert not any(s is p.drawing.shapes[moved] for u in p.units() for s in u.shapes)
    q = job19()
    q.apply_dict(json.loads(json.dumps(p.to_dict())))
    assert q.load_notes == [] and q.assign == p.assign


def test_a_grouped_shape_that_becomes_a_board_leaves_its_group():
    p = scratch()
    add(p, make_rectangle(0, 0, 100, 60, stroke=K))
    p.create_group([0], "Pieza", "corte")
    add(p, circle(50, 30, 8))                                         # ahora el rectangulo es un tablero: no se elige
    assert [b.index for b in p.drawing.layout.boards] == [0] and 0 not in p.selectable()
    assert p.assign == {} and p.groups() == []
    p.undo()
    assert p.assign == {0: p.groups()[0].key[0]} and p.drawing.layout.boards == [] and p.groups()[0].name == "Pieza"


# --- tableros -------------------------------------------------------------------------------------------------------
def test_board_settings_follow_the_board_when_the_numbers_change():
    p = job19()
    p.overrides[2].thickness = 7.0
    p.overrides[1].thickness = 3.0
    p.overrides[2].margin = 1.5
    p.edit(de.delete_edit(board_indices(p, 1)))
    assert [b.number for b in p.drawing.layout.boards] == [1] and set(p.overrides) == {1}
    assert p.overrides[1].thickness == 7.0 and p.overrides[1].margin == 1.5
    p.undo()
    assert [b.number for b in p.drawing.layout.boards] == [1, 2] and set(p.overrides) == {1, 2}
    assert p.overrides[1].thickness == 3.0 and p.overrides[2].thickness == 7.0 and p.overrides[2].margin == 1.5   # el borrado vuelve con lo suyo
    p.redo()
    assert set(p.overrides) == {1} and p.overrides[1].thickness == 7.0


def test_a_board_that_is_new_gets_the_usual_settings():
    p = job19()
    p.overrides[1].thickness = 3.0
    p.overrides[2].thickness = 7.0
    add(p, make_rectangle(2000, 0, 200, 200, stroke=K), circle(2100, 100, 10))
    assert [b.number for b in p.drawing.layout.boards] == [1, 2, 3]
    assert (p.overrides[1].thickness, p.overrides[2].thickness, p.overrides[3].thickness) == (3.0, 7.0, None)


def test_hold_points_go_with_a_board_that_is_moved_and_are_dropped_if_it_changes_size():
    p = job19()
    p.hold_points = {1: [(100.0, 100.0), (200.0, 50.0)], 2: [(800.0, 100.0)]}
    p.edit(de.move_edit(board_indices(p, 1), 10, 5))
    assert set(p.hold_points) == {1, 2} and p.hold_points[2] == [(800.0, 100.0)]
    assert flat(p.hold_points[1]) == pytest.approx([110.0, 105.0, 210.0, 55.0])
    p.edit(de.move_edit([board(p, 1).index], 0, 7))                    # solo el rectangulo: los puntos van con el
    assert flat(p.hold_points[1]) == pytest.approx([110.0, 112.0, 210.0, 62.0])
    p.edit(de.scale_edit([board(p, 2).index], 1.1, 1.0, 700, 0))      # otro tamano: vuelve la propuesta
    assert 2 not in p.hold_points and 1 in p.hold_points


def test_hold_points_of_a_drawing_without_boards_stay_and_those_of_boards_do_not_jump_to_other_boards():
    p = scratch()
    add(p, circle())
    p.hold_points = {None: [(5.0, 5.0)]}
    p.edit(de.move_edit([0], 3, 3))
    assert p.hold_points == {None: [(5.0, 5.0)]}
    p.edit(de.add_edit([make_rectangle(-50, -50, 200, 200, stroke=K)]))          # ahora hay un tablero
    assert p.hold_points == {}


def test_turning_off_board_detection_after_edits_is_done_by_replaying_them():
    p = job19()
    add(p, circle(300, 500, 6))
    data = json.loads(json.dumps(p.to_dict()))
    q = Project(tools())
    q.use_boards = False
    q.load_svg(BOARDS19)
    q.apply_dict(data)
    assert q.drawing.layout.boards == [] and len(q.drawing.shapes) == 129 and q.edits == p.edits


# --- guardar y abrir ---------------------------------------------------------------------------------------------------
def test_a_job_without_edits_looks_exactly_like_before():
    p = job19()
    assert "edicion" not in p.to_dict()
    p.edit(de.delete_edit([2]))
    assert "edicion" in p.to_dict()
    p.undo()
    assert "edicion" not in p.to_dict()


def test_a_new_drawing_with_edits_and_groups_survives_saving_and_opening(tmp_path):
    p, color = grouped()
    p.edit(de.offset_edit([0], 2, keep=True))
    p.groups()[0].through, p.groups()[0].depth = False, 5.5
    p.draw_color = R
    p.title = "Soporte"
    path = tmp_path / "soporte.cncjob"
    p.save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["svg"] == "" and data["edicion"]["nuevo"] and len(data["edicion"]["operaciones"]) == 2
    assert data["num_formas"] == len(p.drawing.shapes) == 5
    q = Project(tools())
    q.new_drawing()
    q.apply_dict(data)
    assert wkts(q) == wkts(p) and q.edits == p.edits and q.assign == p.assign and q.load_notes == []
    assert q.draw_color == R and q.title == "Soporte"
    g = q.groups()[0]
    assert (g.name, g.key, g.depth, g.through, g.count) == ("Pozo", (color, None), 5.5, False, 2)
    assert q.undo() == "" and q.undo_label == ""                         # lo de antes de guardar no se deshace


def test_an_svg_job_with_edits_survives_saving_and_opening():
    p = job19()
    inside = board(p, 1).contents
    p.create_group(inside[:3], "Pozo", "corte")
    add(p, circle(300, 500, 6))
    p.edit(de.array_edit(inside[3:4], 1, 2, 0, 7))
    p.overrides[2].thickness = 7.0
    data = json.loads(json.dumps(p.to_dict()))
    q = job19()
    q.apply_dict(data)
    assert wkts(q) == wkts(p) and q.assign == p.assign and q.load_notes == [] and q.edits == p.edits
    assert q.overrides[2].thickness == 7.0 and [r.name for r in q.groups()] == ["Pozo"]
    assert q.plan(1).ok and q.plan(2).ok


def test_the_saved_edits_apply_to_the_same_drawing_only():
    p = job19()
    p.edit(de.delete_edit([2]))
    data = json.loads(json.dumps(p.to_dict()))
    other = Project(tools())
    other.load_svg(BIG)
    other.apply_dict(data)
    assert other.edits == [] and len(other.drawing.shapes) == 26
    assert len(other.load_notes) >= 1 and any("ha cambiado" in n.text and "herramientas de dibujo" in n.text for n in other.load_notes)


def test_an_edit_that_no_longer_applies_stops_the_replay_with_a_notice():
    p = scratch()
    add(p, circle(0, 0, 10))
    p.edit(de.move_edit([0], 5, 5))
    p.edit(de.move_edit([0], 1, 1))
    data = json.loads(json.dumps(p.to_dict()))
    data["edicion"]["operaciones"][1] = {"op": "borrar", "formas": [99]}
    q = Project(tools())
    q.new_drawing()
    q.apply_dict(data)
    assert len(q.edits) == 1 and len(q.drawing.shapes) == 1
    assert len(q.load_notes) >= 1 and "edicion 2 de 3" in q.load_notes[0].text and "no existe" in q.load_notes[0].text


def test_a_job_of_a_new_drawing_does_not_open_over_an_svg_nor_the_other_way_round():
    p = scratch()
    add(p, circle())
    data = json.loads(json.dumps(p.to_dict()))
    with pytest.raises(ProjectError, match="dibujo nuevo"):
        job19().apply_dict(data)
    v = job19()
    v.edit(de.delete_edit([2]))
    with pytest.raises(ProjectError, match="dibujo nuevo"):
        scratch().apply_dict(json.loads(json.dumps(v.to_dict())))


def test_opening_over_a_project_with_edits_starts_from_the_drawing_as_it_was():
    p = job19()
    p.edit(de.delete_edit([2]))
    clean = json.loads(json.dumps(job19().to_dict()))
    p.apply_dict(clean)
    assert p.edits == [] and len(p.drawing.shapes) == 128 and p.undo() == ""


def test_a_bad_color_in_the_saved_edits_is_ignored():
    p = scratch()
    add(p, circle())
    data = json.loads(json.dumps(p.to_dict()))
    data["edicion"]["color"] = "rojo"
    q = Project(tools())
    q.new_drawing()
    q.apply_dict(data)
    assert q.draw_color == K and len(q.edits) == 1


def test_renaming_a_new_drawing_changes_the_name_the_outputs_use(tmp_path):
    p = scratch()
    add(p, circle())
    p.rename(tmp_path / "soporte.svg")
    assert p.drawing.path.stem == "soporte" and p.base.path.stem == "soporte"
    p.undo()
    assert p.drawing.path.stem == "soporte"
    p.redo()
    assert p.drawing.path.stem == "soporte"
    q = Project(tools())
    q.new_drawing()
    q.rename(tmp_path / "x.svg")
    q.apply_dict(json.loads(json.dumps(p.to_dict())))
    assert q.drawing.path.stem == "x" and len(q.drawing.shapes) == 1


def test_the_planning_copy_of_an_edited_project_plans_the_same():
    p = scratch()
    add(p, make_rectangle(0, 0, 100, 60, stroke=K), circle(30, 30, 8), circle(70, 30, 8))
    p.edit(de.move_edit([2], 0, 5))
    a, b = p.plan(1), p.snapshot().plan(1)
    assert a.ok and a.text == b.text


def test_levels_and_rows_are_recomputed_after_an_edit():
    p = scratch()
    p.use_boards = False
    add(p, make_rectangle(0, 0, 100, 60, stroke=K), circle(30, 30, 8))
    assert p.levels() == {0: 0, 1: 1}
    p.edit(de.delete_edit([0]))
    assert p.levels() == {0: 0}
    p.edit(de.add_edit([make_polyline([(0, 0), (5, 5)], stroke=R)]))
    assert [(r.key, r.count) for r in p.rows] == [((R, None), 1), ((K, None), 1)] and p.levels() == {0: 0}      # la linea antes que la pieza


def test_a_new_shape_cannot_use_the_colour_that_labels_a_group():
    p, color = grouped()
    steps = len(p._undo)
    with pytest.raises(ProjectError, match="etiqueta de un grupo"):
        add(p, circle(300, 0, 10, stroke=color))
    assert len(p.drawing.shapes) == 4 and len(p._undo) == steps and len(p.edits) == 1
    add(p, circle(300, 0, 10, stroke=R))                                   # otro color si vale
    assert len(p.drawing.shapes) == 5


def test_redoing_the_copy_of_a_board_leaves_the_original_with_its_settings():
    p = job19()
    p.overrides[1].thickness = 3.0
    p.edit(de.array_edit(board_indices(p, 2), 2, 1, 1500, 0))                 # el tablero 2 y una copia mas a la derecha
    assert [b.number for b in p.drawing.layout.boards] == [1, 2, 3]
    p.overrides[2].thickness = 7.0
    p.undo()
    assert [b.number for b in p.drawing.layout.boards] == [1, 2] and p.overrides[2].thickness == 7.0
    p.redo()
    assert [b.number for b in p.drawing.layout.boards] == [1, 2, 3]
    assert (p.overrides[1].thickness, p.overrides[2].thickness, p.overrides[3].thickness) == (3.0, 7.0, None)


def test_an_empty_drawing_says_it_is_empty_instead_of_blaming_the_table():
    p = scratch()
    plan = p.plan(None)
    assert not plan.ok and "vacio" in plan.error and "Ignorar" not in plan.error
    add(p, circle())
    p.edit(de.delete_edit([0]))
    assert "vacio" in p.plan(None).error


def test_a_new_shape_cannot_fill_with_the_colour_of_a_group_label_either():
    p, color = grouped()
    steps = len(p._undo)
    with pytest.raises(ProjectError, match="etiqueta de un grupo"):
        add(p, make_circle(300, 0, 10, stroke=R, fill=color))
    assert len(p.drawing.shapes) == 4 and len(p._undo) == steps
    add(p, make_circle(300, 0, 10, stroke=R, fill="#00ff00"))
    assert len(p.drawing.shapes) == 5


def test_the_menu_names_point_to_the_next_step_in_each_direction():
    p = scratch()
    add(p, circle())
    p.edit(de.move_edit([0], 5, 0))
    assert (p.undo_label, p.redo_label) == ("Mover", "")
    assert p.undo() == "Mover" and p.undo() == "Dibujar forma" and p.undo() == ""
    assert (p.undo_label, p.redo_label) == ("", "Dibujar forma")
    assert p.redo() == "Dibujar forma"
    assert (p.undo_label, p.redo_label) == ("Dibujar forma", "Mover")
    assert p.redo() == "Mover" and p.redo() == "" and p.redo_label == ""


def test_undoing_the_delete_of_a_board_brings_it_back_with_its_own_settings_and_points():
    p = job19()
    b1 = board(p, 1)
    p.overrides[1].thickness = 3.0
    p.overrides[2].thickness = 7.0
    p.hold_points[1] = [(b1.bounds[0] + 50.0, b1.bounds[1] + 50.0)]
    p.edit(de.delete_edit(board_indices(p, 1)))
    assert [b.number for b in p.drawing.layout.boards] == [1]
    assert p.overrides[1].thickness == 7.0 and p.hold_points == {}                  # el antiguo tablero 2 es ahora el 1
    p.undo()
    assert [b.number for b in p.drawing.layout.boards] == [1, 2]
    assert (p.overrides[1].thickness, p.overrides[2].thickness) == (3.0, 7.0)       # el borrado vuelve con lo suyo
    assert p.hold_points == {1: [(b1.bounds[0] + 50.0, b1.bounds[1] + 50.0)]}
    p.redo()
    assert [b.number for b in p.drawing.layout.boards] == [1] and p.overrides[1].thickness == 7.0 and p.hold_points == {}


def test_redoing_the_copy_of_a_board_gives_the_copy_back_the_settings_it_had_when_it_was_undone():
    p = job19()
    p.edit(de.array_edit(board_indices(p, 2), 2, 1, 1500, 0))
    p.overrides[3].thickness = 5.0                                                  # ajustes de la copia
    b3 = board(p, 3)
    p.hold_points[3] = [(b3.bounds[0] + 40.0, b3.bounds[1] + 40.0)]
    p.undo()
    assert [b.number for b in p.drawing.layout.boards] == [1, 2] and 3 not in p.overrides and 3 not in p.hold_points
    p.redo()
    assert p.overrides[3].thickness == 5.0 and p.overrides[1].thickness is None
    assert p.hold_points == {3: [(b3.bounds[0] + 40.0, b3.bounds[1] + 40.0)]}


def test_the_saved_settings_of_a_step_are_copies_so_later_changes_do_not_leak_into_it():
    p = job19()
    p.overrides[1].thickness = 3.0
    p.edit(de.delete_edit(board_indices(p, 1)))
    step = p._undo[-1]
    p.overrides[1].thickness = 99.0                                                  # el tablero que queda
    assert step.overrides[1].thickness == 3.0 and step.overrides is not p.overrides


def test_board_settings_follow_the_rectangle_when_deleting_another_board_shifts_its_index():
    p = job19()
    assert (board(p, 1).index, board(p, 2).index) == (1, 0)                          # el recuadro del tablero 2 va el primero
    p.overrides[1].thickness = 3.0
    p.overrides[2].thickness = 7.0
    p.edit(de.delete_edit(board_indices(p, 2)))
    assert [b.number for b in p.drawing.layout.boards] == [1] and board(p, 1).index == 0     # el recuadro del 1 baja de 1 a 0
    assert p.overrides[1].thickness == 3.0
    p.overrides[1].thickness = 4.0                                                   # lo que se cambia despues gana al deshacer
    p.undo()
    assert [b.number for b in p.drawing.layout.boards] == [1, 2]
    assert (p.overrides[1].thickness, p.overrides[2].thickness) == (4.0, 7.0)        # el tablero 2 vuelve con lo suyo
    p.overrides[1].thickness = 5.0                                                   # ...y tambien al rehacer
    p.redo()
    assert [b.number for b in p.drawing.layout.boards] == [1] and p.overrides[1].thickness == 5.0
    p.undo()
    p.overrides[1].thickness = 11.0
    p.redo()
    assert p.overrides[1].thickness == 11.0
    p.undo()
    assert (p.overrides[1].thickness, p.overrides[2].thickness) == (11.0, 7.0)


def test_hold_points_follow_a_moved_board_but_start_again_when_its_width_or_height_changes():
    for sx, sy in ((1.0, 1.2), (1.2, 1.0)):
        p = job19()
        b = board(p, 2)
        p.hold_points[2] = [(b.bounds[0] + 100.0, b.bounds[1] + 100.0)]
        p.edit(de.scale_edit(board_indices(p, 2), sx, sy, b.bounds[0], b.bounds[1]))
        assert [x.number for x in p.drawing.layout.boards] == [1, 2] and p.hold_points == {}, (sx, sy)
    p = job19()
    b = board(p, 2)
    p.hold_points[2] = [(b.bounds[0] + 100.0, b.bounds[1] + 100.0)]
    p.edit(de.move_edit(board_indices(p, 2), 10.0, 20.0))
    assert p.hold_points == {2: [(b.bounds[0] + 110.0, b.bounds[1] + 120.0)]}


def test_a_job_of_a_new_drawing_ignores_the_shape_count_and_one_without_it_still_applies_its_edits():
    p = scratch()
    add(p, circle(), circle(50, 0))
    data = json.loads(json.dumps(p.to_dict()))
    for change in (lambda e: e.update(formas_base=7), lambda e: e.pop("formas_base")):
        d = json.loads(json.dumps(data))
        change(d["edicion"])
        q = Project(tools())
        q.new_drawing()
        q.apply_dict(d)
        assert len(q.drawing.shapes) == 2 and q.load_notes == [] and len(q.edits) == 1
    v = job19()
    v.edit(de.move_edit([5], 3, 0))
    d = json.loads(json.dumps(v.to_dict()))
    d["edicion"].pop("formas_base")                                                   # sin el dato se aplica igual
    q = job19()
    q.apply_dict(d)
    assert len(q.edits) == 1 and q.load_notes == []
    d["edicion"]["formas_base"] = 3                                                   # con un dato que no cuadra, se descarta
    q = job19()
    q.apply_dict(d)
    assert q.edits == [] and any("ha cambiado" in n.text for n in q.load_notes)


def test_starting_a_new_drawing_forgets_the_notices_of_the_previous_job():
    p = job19()
    p.edit(de.delete_edit([2]))
    data = json.loads(json.dumps(p.to_dict()))
    data["edicion"]["formas_base"] = 3
    q = job19()
    q.apply_dict(data)
    assert q.load_notes
    q.new_drawing()
    assert q.load_notes == [] and q.scratch and q.edits == []
