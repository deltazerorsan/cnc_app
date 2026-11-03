"""Puntos de referencia de la caja (cnc.drawing_edit.Measures.point / box_corner) y alinear formas por una cara o por el centro
(«alinear», sin Qt)."""
import json

import pytest
from shapely import affinity

from cnc import drawing_edit as de
from cnc.drawing_edit import (ALIGN_LABELS, ALIGN_SIDES, EditError, align_edit, apply_edit, box_corner, edit_label,
                              make_circle, make_polygon, make_rectangle, make_slot, measures_edit, measures_of)
from cnc.model import Tool
from cnc.project import General, Project, ProjectError

K = "#000000"
FRACTIONS = (0.0, 0.5, 1.0)


def rect(x=10, y=10, w=50, h=30, r=0.0):
    return make_rectangle(x, y, w, h, r, stroke=K)


def circle(cx=120, cy=50, r=10):
    return make_circle(cx, cy, r, stroke=K)


def box(s):
    return s.shape.bounds


def align(shapes, indices, side):
    return apply_edit(shapes, align_edit(indices, side))


# --- los puntos de la caja ------------------------------------------------------------------------------------------------
def test_the_nine_points_of_a_box_are_its_corners_its_side_middles_and_its_centre():
    m = measures_of([rect(10, 20, 50, 30)], [0])
    assert m.point(0, 0) == (10, 20) and m.point(1, 1) == (60, 50)
    assert m.point(1, 0) == (60, 20) and m.point(0, 1) == (10, 50)
    assert m.point(0.5, 0.5) == (35, 35) == (m.cx, m.cy)
    assert m.point(0.5, 0) == (35, 20) and m.point(0.5, 1) == (35, 50)
    assert m.point(0, 0.5) == (10, 35) and m.point(1, 0.5) == (60, 35)


def test_the_points_of_a_box_with_negative_corner():
    m = measures_of([rect(-30, -10, 20, 8)], [0])
    assert m.point(0, 0) == (-30, -10) and m.point(1, 1) == (-10, -2)


@pytest.mark.parametrize("bad", [-0.1, 1.01, 2, "0", None, True, float("nan")])
def test_a_point_outside_the_box_is_refused(bad):
    m = measures_of([rect()], [0])
    with pytest.raises(EditError, match="entre 0 y 1"):
        m.point(bad, 0)
    with pytest.raises(EditError, match="entre 0 y 1"):
        m.point(0, bad)
    with pytest.raises(EditError, match="entre 0 y 1"):
        box_corner(0, 0, bad, 0, 10, 10)


@pytest.mark.parametrize("fx", FRACTIONS)
@pytest.mark.parametrize("fy", FRACTIONS)
def test_the_corner_of_a_box_is_found_back_from_any_of_its_points(fx, fy):
    m = measures_of([rect(7, -3, 41, 17)], [0])
    px, py = m.point(fx, fy)
    assert box_corner(px, py, fx, fy, m.width, m.height) == pytest.approx((m.x, m.y))


def test_a_box_with_another_size_keeps_the_chosen_point_where_it_was():
    m = measures_of([rect(10, 10, 50, 30)], [0])
    for fx in FRACTIONS:
        for fy in FRACTIONS:
            px, py = m.point(fx, fy)
            x, y = box_corner(px, py, fx, fy, 120, 80)
            assert (x + fx * 120, y + fy * 80) == pytest.approx((px, py))


def test_box_corner_is_the_lower_left_for_the_lower_left_point_and_the_top_right_minus_the_size_for_the_top_right():
    assert box_corner(4, 4, 0, 0, 100, 250) == (4, 4)
    assert box_corner(104, 254, 1, 1, 100, 250) == (4, 4)
    assert box_corner(54, 129, 0.5, 0.5, 100, 250) == (4, 4)


# --- alinear -------------------------------------------------------------------------------------------------------------
def three():
    return [rect(10, 10, 50, 30), circle(120, 50, 10), make_polygon(80, 100, 6, 15, stroke=K)]


def test_aligning_left_puts_every_left_face_where_the_leftmost_one_is():
    shapes = three()
    left = min(box(s)[0] for s in shapes)
    res = align(shapes, [0, 1, 2], "izquierda")
    assert [box(s)[0] for s in res.shapes] == pytest.approx([left] * 3)
    assert res.selected == [0, 1, 2]


def test_aligning_right_puts_every_right_face_where_the_rightmost_one_is():
    shapes = three()
    right = max(box(s)[2] for s in shapes)
    res = align(shapes, [0, 1, 2], "derecha")
    assert [box(s)[2] for s in res.shapes] == pytest.approx([right] * 3)


def test_aligning_the_bottom_and_the_top_faces():
    shapes = three()
    low, high = min(box(s)[1] for s in shapes), max(box(s)[3] for s in shapes)
    assert [box(s)[1] for s in align(shapes, [0, 1, 2], "abajo").shapes] == pytest.approx([low] * 3)
    assert [box(s)[3] for s in align(shapes, [0, 1, 2], "arriba").shapes] == pytest.approx([high] * 3)


def test_centring_puts_the_centres_on_the_centre_of_all_of_them_together():
    shapes = three()
    xs = [box(s) for s in shapes]
    cx, cy = (min(b[0] for b in xs) + max(b[2] for b in xs)) / 2, (min(b[1] for b in xs) + max(b[3] for b in xs)) / 2
    res = align(shapes, [0, 1, 2], "centro_x")
    assert [(box(s)[0] + box(s)[2]) / 2 for s in res.shapes] == pytest.approx([cx] * 3)
    assert [box(s)[1] for s in res.shapes] == pytest.approx([b[1] for b in xs])          # la otra direccion no se toca
    res = align(shapes, [0, 1, 2], "centro_y")
    assert [(box(s)[1] + box(s)[3]) / 2 for s in res.shapes] == pytest.approx([cy] * 3)
    assert [box(s)[0] for s in res.shapes] == pytest.approx([b[0] for b in xs])


def test_aligning_in_one_direction_does_not_move_the_other():
    shapes = three()
    res = align(shapes, [0, 1, 2], "izquierda")
    assert [box(s)[1] for s in res.shapes] == pytest.approx([box(s)[1] for s in shapes])
    res = align(shapes, [0, 1, 2], "abajo")
    assert [box(s)[0] for s in res.shapes] == pytest.approx([box(s)[0] for s in shapes])


def test_a_circle_bottom_goes_to_the_rectangle_bottom():
    """El caso de una forma que no es un rectangulo: se alinea por su caja circunscrita (la parte de abajo del circulo)."""
    res = align([rect(10, 10, 50, 30), circle(120, 50, 10)], [0, 1], "abajo")
    assert box(res.shapes[1])[1] == pytest.approx(10) and box(res.shapes[0]) == box(rect(10, 10, 50, 30))


def test_shapes_keep_their_exact_form_and_style_only_translated():
    shapes = three()
    res = align(shapes, [0, 1, 2], "derecha")
    for old, new in zip(shapes, res.shapes):
        dx = box(new)[2] - box(old)[2]
        assert new.shape.equals_exact(affinity.translate(old.shape, dx, 0), 1e-9)
        assert (new.kind, new.stroke, new.closed) == (old.kind, old.stroke, old.closed)


def test_the_shape_that_is_already_in_place_is_not_even_touched():
    shapes = three()
    res = align(shapes, [0, 1, 2], "izquierda")
    leftmost = min(range(3), key=lambda i: box(shapes[i])[0])
    assert res.shapes[leftmost] is shapes[leftmost]


def test_shapes_not_chosen_are_left_alone_and_do_not_count_for_the_reference():
    shapes = three() + [rect(-500, 0, 10, 10)]
    res = align(shapes, [0, 1], "izquierda")
    assert res.shapes[3] is shapes[3] and res.shapes[2] is shapes[2]
    assert box(res.shapes[1])[0] == pytest.approx(10)                       # no es -500: la forma 3 no estaba elegida


def test_the_origin_map_is_the_identity_so_boards_and_groups_follow():
    res = align(three(), [0, 1], "arriba")
    assert res.origin == [0, 1, 2] and res.index_map() == {0: 0, 1: 1, 2: 2}


@pytest.mark.parametrize("side", ALIGN_SIDES)
def test_every_side_works_with_just_two_shapes_and_is_idempotent(side):
    shapes = [rect(0, 0, 10, 10), rect(33, 21, 4, 7)]
    once = align(shapes, [0, 1], side).shapes
    with pytest.raises(EditError, match="ya estan alineadas"):
        apply_edit(once, align_edit([0, 1], side))


def test_a_shape_a_few_microns_off_counts_as_aligned_but_a_tenth_of_a_millimetre_does_not():
    ok = [rect(0, 0, 10, 10), rect(0.0003, 20, 5, 5)]
    with pytest.raises(EditError, match="ya estan alineadas"):
        align(ok, [0, 1], "izquierda")
    res = align([rect(0, 0, 10, 10), rect(0.1, 20, 5, 5)], [0, 1], "izquierda")
    assert box(res.shapes[1])[0] == pytest.approx(0)


def test_two_shapes_already_sharing_the_centre_are_refused_for_that_centre_only():
    shapes = [rect(0, 0, 10, 10), rect(2, 20, 6, 6)]
    with pytest.raises(EditError, match="ya estan alineadas"):
        align(shapes, [0, 1], "centro_x")
    res = align(shapes, [0, 1], "centro_y")             # el centro de todas juntas (de 0 a 26) es 13: las dos se centran ahi
    assert box(res.shapes[0])[1] == pytest.approx(8) and box(res.shapes[1])[1] == pytest.approx(10)


def test_a_slot_and_an_open_line_are_aligned_by_their_box():
    from cnc.drawing_edit import make_polyline
    line = make_polyline([(0, 0), (30, 40)], stroke=K)
    res = align([make_slot(50, 50, 80, 50, 6, stroke=K), line], [0, 1], "izquierda")
    assert box(res.shapes[0])[0] == pytest.approx(box(res.shapes[1])[0])


# --- lo que no se puede ------------------------------------------------------------------------------------------------
def test_one_shape_is_not_enough_to_align():
    with pytest.raises(EditError, match="al menos dos"):
        align(three(), [0], "izquierda")


def test_two_equal_indices_are_one_shape_and_not_enough_either():
    with pytest.raises(EditError, match="al menos dos"):
        apply_edit(three(), {"op": "alinear", "formas": [1, 1], "lado": "izquierda"})


@pytest.mark.parametrize("side", ["centro", "left", "", None, 3, ["izquierda"]])
def test_an_unknown_side_is_refused(side):
    with pytest.raises(EditError, match="no se puede alinear"):
        apply_edit(three(), {"op": "alinear", "formas": [0, 1], "lado": side})


def test_nothing_chosen_or_a_missing_shape_is_refused():
    with pytest.raises(EditError, match="ninguna forma"):
        apply_edit(three(), {"op": "alinear", "formas": [], "lado": "izquierda"})
    with pytest.raises(EditError, match="no existe"):
        align(three(), [0, 7], "izquierda")


def test_the_input_list_is_not_modified():
    shapes = three()
    before = [s.shape.wkt for s in shapes]
    align(shapes, [0, 1, 2], "arriba")
    assert [s.shape.wkt for s in shapes] == before


# --- nombres ------------------------------------------------------------------------------------------------------------
def test_every_side_has_its_name_for_the_undo_menu():
    assert set(ALIGN_LABELS) == set(ALIGN_SIDES)
    for side in ALIGN_SIDES:
        assert edit_label(align_edit([0, 1], side)) == ALIGN_LABELS[side]
    assert ALIGN_LABELS == {"izquierda": "Alinear a la izquierda", "centro_x": "Centrar en horizontal",
                            "derecha": "Alinear a la derecha", "arriba": "Alinear arriba",
                            "centro_y": "Centrar en vertical", "abajo": "Alinear abajo"}
    assert edit_label({"op": "alinear", "formas": [0, 1], "lado": "otro"}) == "Alinear"
    assert de.LABELS["alinear"] == "Alinear"


# --- dentro del proyecto -----------------------------------------------------------------------------------------------
def tools():
    return {"em4": Tool(diameter=4, name="End mill 4 mm", feed_xy=300, feed_z=200, stepdown=6.5, stepover=0.6)}


def scratch() -> Project:
    p = Project(tools())
    p.new_drawing()
    p.general = General(tool="em4", thickness=19.0, margin=0.4, rotate="0")
    return p


def test_aligning_can_be_undone_and_redone_and_says_where():
    p = scratch()
    p.edit(de.add_edit([rect(10, 10, 50, 30), circle(120, 50, 10)]))
    before = [s.shape.wkt for s in p.drawing.shapes]
    p.edit(align_edit([0, 1], "abajo"))
    assert p.undo_label == "Alinear abajo" and box(p.drawing.shapes[1])[1] == pytest.approx(10)
    assert p.undo() == "Alinear abajo" and [s.shape.wkt for s in p.drawing.shapes] == before
    assert p.redo() == "Alinear abajo" and box(p.drawing.shapes[1])[1] == pytest.approx(10)


def test_a_saved_job_replays_the_alignment():
    p = scratch()
    p.edit(de.add_edit([rect(10, 10, 50, 30), circle(120, 50, 10), rect(200, 5, 20, 20)]))
    p.edit(align_edit([0, 1, 2], "derecha"))
    p.edit(measures_edit([0], 4, 4, 100, 250))
    data = json.loads(json.dumps(p.to_dict()))
    assert [e["op"] for e in data["edicion"]["operaciones"]] == ["anadir", "alinear", "medidas"]
    assert data["edicion"]["operaciones"][1]["lado"] == "derecha"
    q = Project(tools())
    q.new_drawing()
    q.apply_dict(data)
    assert [s.shape.wkt for s in q.drawing.shapes] == [s.shape.wkt for s in p.drawing.shapes]
    assert q.load_notes == []


def test_a_refused_alignment_leaves_the_drawing_and_the_history_alone():
    p = scratch()
    p.edit(de.add_edit([rect(0, 0, 10, 10), rect(0, 20, 5, 5)]))
    before = [s.shape.wkt for s in p.drawing.shapes]
    with pytest.raises(ProjectError, match="ya estan alineadas"):
        p.edit(align_edit([0, 1], "izquierda"))
    assert [s.shape.wkt for s in p.drawing.shapes] == before and len(p.edits) == 1 and p.undo_label == "Dibujar forma"


def test_a_part_aligned_to_the_board_edge_moves_with_its_job():
    """Una pieza dentro de un tablero: alinearla con el tablero (que no se mueve) cambia el trabajo."""
    p = scratch()
    p.edit(de.add_edit([make_rectangle(0, 0, 100, 60, stroke=K), circle(50, 30, 8)]))
    assert len(p.drawing.layout.boards) == 1
    p.edit(align_edit([0, 1], "izquierda"))
    assert box(p.drawing.shapes[1])[0] == pytest.approx(0) and box(p.drawing.shapes[0]) == (0, 0, 100, 60)
    assert len(p.drawing.layout.boards) == 1
