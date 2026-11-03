"""Puentes puestos a mano: viajan con la forma (mover, girar, reflejar, medidas, copias), se deshacen, se guardan en el trabajo y
mandan sobre la propuesta automatica al generar; mas las cuentas de los gestos del raton (`cnc.bridge_tools`)."""
import json
from pathlib import Path

import pytest
from shapely.geometry import box

from cnc import bridge_tools as bt
from cnc import drawing_edit as de
from cnc.model import Tool
from cnc.project import Project, ProjectError
from cnc.svg_import import ImportedShape
from cnc.tabs import Tab, along, perimeter, tab_line
from cnc.verify_nc import analyze

BOARDS19 = Path(__file__).parent / "data" / "tableros_19mm.svg"
BLACK = "#000000"


def tools():
    return {"em4": Tool(diameter=4, name="End mill 4 mm", feed_xy=300, feed_z=200, stepdown=6.5, stepover=0.6)}


def rect_project(*extra, boards=False):
    """Un rectangulo de (10, 10) a (110, 70) [100 x 60, contorno de 320 mm] y lo que se diga, en un dibujo nuevo."""
    p = Project(tools())
    p.use_boards = boards
    p.new_drawing()
    p.edit(de.add_edit([de.make_rectangle(10, 10, 100, 60, 0, stroke=BLACK), *extra]))
    p.general.tool, p.general.thickness = "em4", 19
    return p


def row_of(p, stroke=BLACK):
    """La fila de ese color (cada edicion rehace la tabla: no se guarda la fila)."""
    return next(r for r in p.rows if r.stroke == stroke)


def centre(p, i=0):
    s = p.drawing.shapes[i]
    return along(s.shape, s.tabs[0].at)


# --- la edicion ---------------------------------------------------------------------------------------------------------
def test_the_edit_sets_clears_and_returns_to_automatic():
    shapes = [ImportedShape(box(0, 0, 100, 60), True, BLACK)]
    r = de.apply_edit(shapes, de.tabs_edit([0], [Tab(0.25, 6, 2)]))
    assert r.shapes[0].tabs == (Tab(0.25, 6, 2),) and shapes[0].tabs is None and r.selected == [0]
    r2 = de.apply_edit(r.shapes, de.tabs_edit([0], []))
    assert r2.shapes[0].tabs == ()                                   # sin puentes, aunque la fila los pida
    r3 = de.apply_edit(r2.shapes, de.tabs_edit([0], None))
    assert r3.shapes[0].tabs is None
    assert de.edit_label(de.tabs_edit([0], None)) == "Puentes"


@pytest.mark.parametrize("bad, why", [
    ([[0.5, 0, 2]], "ancho y alto"), ([[0.5, 6, -1]], "ancho y alto"), ([[1.5, 6, 2]], "posición"), ([[0.5, 6]], "posición"),
    ("hola", "lista"), ([[0.5, 6, 2]] * 201, "lista")])
def test_the_edit_refuses_bad_bridges(bad, why):
    with pytest.raises(de.EditError, match=why):
        de.apply_edit([ImportedShape(box(0, 0, 100, 60), True, BLACK)], {"op": "puentes", "formas": [0], "puentes": bad})


def test_the_edit_refuses_open_lines_and_no_changes():
    from shapely.geometry import LineString
    line = [ImportedShape(LineString([(0, 0), (10, 0)]), False, BLACK, kind="line")]
    with pytest.raises(de.EditError, match="formas cerradas"):
        de.apply_edit(line, de.tabs_edit([0], [Tab(0.5, 6, 2)]))
    with pytest.raises(de.EditError, match="no cambia nada"):
        de.apply_edit([ImportedShape(box(0, 0, 100, 60), True, BLACK)], de.tabs_edit([0], None))


# --- viajan con la forma ------------------------------------------------------------------------------------------------
def bridged(shape_geom=None, at=0.3):
    return [ImportedShape(shape_geom or box(0, 0, 100, 60), True, BLACK, tabs=(Tab(at, 6, 2),))]


def where(s):
    return along(s.shape, s.tabs[0].at)


def test_moving_rotating_scaling_and_copying_keep_the_bridge_on_the_same_spot_of_the_piece():
    s0 = bridged()
    x0, y0 = where(s0[0])
    moved = de.apply_edit(s0, de.move_edit([0], 5, -3)).shapes[0]
    assert moved.tabs == s0[0].tabs and where(moved) == pytest.approx((x0 + 5, y0 - 3))
    turned = de.apply_edit(s0, de.rotate_edit([0], 90, 0, 0)).shapes[0]
    assert turned.tabs == s0[0].tabs and where(turned) == pytest.approx((-y0, x0))
    scaled = de.apply_edit(s0, de.scale_edit([0], 2, 2, 0, 0)).shapes[0]
    assert where(scaled) == pytest.approx((2 * x0, 2 * y0))
    copies = de.apply_edit(s0, de.array_edit([0], 3, 1, 150, 0)).shapes
    assert len(copies) == 3 and all(c.tabs == s0[0].tabs for c in copies)
    assert where(copies[2]) == pytest.approx((x0 + 300, y0))


@pytest.mark.parametrize("axis, pos", [("vertical", 50), ("horizontal", 30), ("vertical", -20)])
def test_mirroring_a_piece_keeps_the_bridge_on_the_same_spot_of_it(axis, pos):
    s0 = bridged(at=0.3)
    x0, y0 = where(s0[0])
    mirrored = de.apply_edit(s0, de.mirror_edit([0], axis, pos)).shapes[0]
    expected = (2 * pos - x0, y0) if axis == "vertical" else (x0, 2 * pos - y0)
    assert where(mirrored) == pytest.approx(expected, abs=1e-6)
    twice = de.apply_edit([mirrored], de.mirror_edit([0], axis, pos)).shapes[0]
    assert where(twice) == pytest.approx((x0, y0), abs=1e-6)


def test_resizing_a_rebuilt_rectangle_or_circle_keeps_the_bridge_on_the_same_side():
    rect = [de.make_rectangle(0, 0, 100, 60, 0, stroke=BLACK)]
    rect[0] = ImportedShape(rect[0].shape, True, BLACK, kind="rect", tabs=(Tab(0.0, 6, 2), Tab(0.3, 6, 2)))
    first, second = [along(rect[0].shape, t.at) for t in rect[0].tabs]
    out = de.apply_edit(rect, de.measures_edit([0], 0, 0, 200, 30)).shapes[0]
    assert len(out.tabs) == 2
    got = sorted(along(out.shape, t.at) for t in out.tabs)
    want = sorted([(first[0] * 2, first[1] / 2), (second[0] * 2, second[1] / 2)])     # el mismo sitio relativo en la caja nueva
    assert got == pytest.approx(want, abs=1e-6)
    circle = [ImportedShape(de.make_circle(0, 0, 10, stroke=BLACK).shape, True, BLACK, kind="circle", tabs=(Tab(0.25, 6, 2),))]
    cx, cy = where(circle[0])
    bigger = de.apply_edit(circle, de.measures_edit([0], -20, -20, 40, 40)).shapes[0]
    assert where(bigger) == pytest.approx((cx * 2, cy * 2), abs=0.05)


def test_ops_that_make_new_shapes_start_them_without_hand_made_bridges():
    out = de.apply_edit(bridged(), de.offset_edit([0], 2, keep=True)).shapes
    assert out[0].tabs is not None and out[1].tabs is None


def test_deleting_a_neighbour_keeps_the_bridges_of_the_others():
    shapes = bridged() + [ImportedShape(box(200, 0, 230, 20), True, BLACK)]
    out = de.apply_edit(shapes, de.delete_edit([1])).shapes
    assert len(out) == 1 and out[0].tabs == shapes[0].tabs
    shapes[1] = ImportedShape(box(200, 0, 230, 20), True, BLACK, tabs=(Tab(0.5, 6, 2),))
    out = de.apply_edit(shapes[::-1], de.delete_edit([1])).shapes
    assert out[0].tabs == (Tab(0.5, 6, 2),)


# --- en el proyecto -----------------------------------------------------------------------------------------------------
def test_the_project_sets_bridges_with_undo_and_redo():
    p = rect_project()
    assert p.bridge_map() == {}
    p.set_bridges([0], [Tab(0.25, 6, 2), Tab(0.75, 6, 2)])
    assert p.drawing.shapes[0].tabs == (Tab(0.25, 6, 2), Tab(0.75, 6, 2)) and p.bridge_map()[0][1] is True
    assert p.undo_label == "Puentes"
    p.undo()
    assert p.drawing.shapes[0].tabs is None and p.bridge_map() == {}
    p.redo()
    assert len(p.drawing.shapes[0].tabs) == 2


def test_the_project_refuses_bridges_where_they_make_no_sense():
    p = rect_project()
    p.edit(de.add_edit([de.make_polyline([(200, 10), (220, 10)], False, stroke="#00ff00")]))
    assert "cerradas" in p.bridge_problem(1) and p.bridge_problem(0) == ""
    with pytest.raises(ProjectError, match="cerradas"):
        p.set_bridges([1], [Tab(0.5, 6, 2)])
    row_of(p).action = "vaciado"
    assert "no es un corte" in p.bridge_problem(0)
    row = row_of(p)
    row.action, row.through, row.depth = "corte", False, 3.0
    assert "no corta de lado a lado" in p.bridge_problem(0)
    assert "no se corta" in p.bridge_problem(7)


def test_automatic_bridges_are_proposed_by_the_row_and_hand_made_ones_win_even_with_the_box_off():
    p = rect_project()
    row_of(p).tabs = True
    auto, manual = p.bridge_map()[0]
    assert len(auto) == 2 and manual is False
    p.set_bridges([0], [Tab(0.1, 8, 3)])
    assert p.bridge_map()[0] == ((Tab(0.1, 8, 3),), True)
    row_of(p).tabs = False
    assert p.bridge_map()[0] == ((Tab(0.1, 8, 3),), True)                  # a mano mandan aunque la fila no pida puentes
    p.set_bridges([0], None)
    assert p.bridge_map() == {}


def test_hand_made_bridges_reach_the_gcode_with_and_without_a_recipe():
    p = rect_project()
    p.set_bridges([0], [Tab(0.5, 10, 3)])
    plan = p.plan(None)                                                    # sin receta (todo corte normal)
    assert plan.ok and analyze(plan.text).ok
    assert "Z-16" in plan.text and any(m.text.startswith("Puentes: 1 en 1 forma(s), de 10 mm de ancho y 3 mm de alto")
                                       for m in plan.messages)
    bridge = next(op for op in plan.operations if op.tabs)
    assert bridge.tabs == (Tab(0.5, 10, 3),)
    row = row_of(p)
    row.name = "Piezas"                                                    # con receta (una fila con nombre propio)
    row.feed = 250
    plan = p.plan(None)
    assert plan.ok and "Z-16" in plan.text
    p.set_bridges([0], [])                                                 # sin puentes
    assert "Z-16" not in p.plan(None).text and not any(m.text.startswith("Puentes") for m in p.plan(None).messages)


def test_bridges_on_a_hole_hold_its_plug():
    hole = de.make_circle(60, 40, 12, stroke=BLACK)
    p = rect_project(hole)
    p.set_bridges([1], [Tab(0.0, 5, 2), Tab(0.5, 5, 2)])
    plan = p.plan(None)
    assert plan.ok and analyze(plan.text).ok
    holes = [op for op in plan.operations if op.side == "inside"]
    assert holes and len(holes[0].tabs) == 2


def test_a_hand_made_bridge_on_a_cut_that_is_not_through_is_left_out_with_a_note():
    p = rect_project()
    p.set_bridges([0], [Tab(0.5, 6, 2)])
    row = row_of(p)
    row.through, row.depth = False, 5.0
    plan = p.plan(None)
    assert plan.ok and not any(op.tabs for op in plan.operations if hasattr(op, "tabs"))
    assert any("puentes puestos a mano" in m.text for m in plan.messages)


def test_the_bridges_are_saved_in_the_job_and_come_back_when_it_is_opened():
    p = rect_project()
    p.set_bridges([0], [Tab(0.2, 6, 2), Tab(0.7, 9, 1.5)])
    p.edit(de.move_edit([0], 3, 4))
    data = json.loads(json.dumps(p.to_dict()))
    assert [e["op"] for e in data["edicion"]["operaciones"]] == ["anadir", "puentes", "mover"]
    q = Project(tools())
    q.new_drawing()
    q.apply_dict(data)
    assert q.drawing.shapes[0].tabs == (Tab(0.2, 6, 2), Tab(0.7, 9, 1.5))
    assert q.drawing.shapes[0].shape.bounds == p.drawing.shapes[0].shape.bounds


def test_bridges_set_on_an_imported_svg_survive_save_and_open():
    p = Project(tools())
    p.load_svg(BOARDS19)
    p.general.tool, p.general.thickness = "em4", 19
    mine = [i for i in p.selectable() if not p.bridge_problem(i)][:3]
    for k, i in enumerate(mine):
        p.set_bridges([i], [Tab(0.1 * (k + 1), 6, 2)])
    data = json.loads(json.dumps(p.to_dict()))
    q = Project(tools())
    q.load_svg(BOARDS19)
    q.apply_dict(data)
    assert [q.drawing.shapes[i].tabs for i in mine] == [p.drawing.shapes[i].tabs for i in mine]
    assert sum(1 for s in q.drawing.shapes if s.tabs is not None) == 3


# --- las cuentas del raton ----------------------------------------------------------------------------------------------
SHAPE = box(0, 0, 100, 60)             # 320 mm de contorno desde la esquina inferior derecha
LENGTH = 320.0


def test_picking_the_bridge_under_a_position():
    tabs = [Tab(0.25, 6, 2), Tab(0.75, 10, 2)]
    assert bt.pick_tab(tabs, 0.25 + 2 / LENGTH, LENGTH) == 0
    assert bt.pick_tab(tabs, 0.75 - 4.9 / LENGTH, LENGTH) == 1
    assert bt.pick_tab(tabs, 0.5, LENGTH) is None
    assert bt.pick_tab(tabs, 0.25 + 4 / LENGTH, LENGTH) is None and bt.pick_tab(tabs, 0.25 + 4 / LENGTH, LENGTH, slack=2) == 0
    assert bt.pick_tab([Tab(0.0, 6, 2)], 0.995, LENGTH) == 0                # por el otro lado del principio


def test_sliding_and_resizing_a_bridge():
    tab = Tab(0.5, 6, 2)
    assert bt.slid(tab, 0.1) == Tab(0.1, 6, 2) and bt.slid(tab, 1.25).at == pytest.approx(0.25)
    longer = bt.resized(tab, +1, 0.5 + 10 / LENGTH, LENGTH)
    assert longer.width == 13 and longer.at * LENGTH == pytest.approx(163.5)    # el extremo de atras (157) no se mueve
    shorter = bt.resized(tab, -1, 0.5 + 1 / LENGTH, LENGTH)
    assert shorter.width == 2 and shorter.at * LENGTH == pytest.approx(162)       # el de delante (163) no se mueve
    assert bt.resized(tab, +1, 0.5 - 20 / LENGTH, LENGTH).width == bt.MIN_WIDTH      # pasar del otro extremo: el minimo
    assert bt.resized(tab, +1, 0.5 + 300 / LENGTH, LENGTH, minimum=1).width <= LENGTH * 0.9
    with pytest.raises(ValueError):
        bt.resized(tab, 0, 0.5, LENGTH)


def test_the_list_of_bridges_after_adding_removing_and_replacing():
    tabs = (Tab(0.2, 6, 2), Tab(0.6, 6, 2))
    new, k = bt.added(tabs, Tab(0.4, 6, 2))
    assert [t.at for t in new] == [0.2, 0.4, 0.6] and k == 1
    assert bt.removed(new, 1) == tabs
    out, k = bt.replaced(tabs, 0, Tab(0.9, 7, 2))
    assert [t.at for t in out] == [0.6, 0.9] and k == 1
    with pytest.raises(ValueError):
        bt.removed(tabs, 5)
    with pytest.raises(ValueError):
        bt.added([Tab(i / 300, 1, 1) for i in range(200)], Tab(0.99, 1, 1))


def test_the_numbers_of_a_bridge_and_back():
    tab = Tab(0.5, 6, 2)
    info = bt.describe(SHAPE, tab)
    assert info["position"] == 160 and info["length"] == 320 and (info["x"], info["y"]) == pytest.approx((0, 60))
    assert bt.from_numbers(SHAPE, 160, 6, 2) == tab
    assert bt.from_numbers(SHAPE, 480, 6, 2).at == pytest.approx(0.5)             # una vuelta de mas da lo mismo
    for bad in ((10, 0, 2), (10, 6, 0), (10, 400, 2), (float("nan"), 6, 2)):
        with pytest.raises(ValueError):
            bt.from_numbers(SHAPE, *bad)


def test_placing_a_bridge_takes_the_nearest_spot_of_the_contour():
    tab, distance = bt.place(SHAPE, (50, -3), 6, 2)
    assert distance == pytest.approx(3) and along(SHAPE, tab.at) == pytest.approx((50, 0))


def test_a_bridge_line_follows_the_contour_round_the_corner():
    line = tab_line(SHAPE, Tab(0.5, 10, 2))
    assert line[0] == pytest.approx((5, 60)) and line[-1] == pytest.approx((0, 55)) and (0.0, 60.0) in [tuple(map(float, p)) for p in line]
    assert perimeter(SHAPE) == LENGTH


# --- el interruptor general de los puentes ------------------------------------------------------------------------------
def test_the_master_switch_off_drops_every_bridge_from_the_gcode_the_map_and_the_tool():
    p = rect_project()
    row_of(p).tabs = True
    p.set_bridges([0], [Tab(0.5, 10, 3)])
    assert p.bridge_map() and "Z-16" in p.plan(None).text
    p.general.bridges = False
    plan = p.plan(None)
    assert plan.ok and analyze(plan.text).ok and "Z-16" not in plan.text
    assert not any(getattr(op, "tabs", ()) for op in plan.operations) and not any(m.text.startswith("Puentes: ") for m in plan.messages)
    assert any("Puentes desactivados: 1 forma(s)" in m.text for m in plan.messages)          # lo puesto a mano se avisa
    assert p.bridge_map() == {} and "desactivados" in p.bridge_problem(0)
    with pytest.raises(ProjectError, match="desactivados"):
        p.set_bridges([0], [Tab(0.1, 6, 2)])
    assert p.drawing.shapes[0].tabs == (Tab(0.5, 10, 3),)                    # lo puesto a mano sigue guardado
    p.general.bridges = True
    assert p.bridge_map()[0] == ((Tab(0.5, 10, 3),), True) and "Z-16" in p.plan(None).text


def test_the_master_switch_off_also_drops_the_bridges_a_row_asks_for_with_a_recipe():
    p = rect_project()
    row = row_of(p)
    row.name, row.tabs, row.feed = "Piezas", True, 250
    assert "Z-17" in p.plan(None).text
    p.general.bridges = False
    plan = p.plan(None)
    assert plan.ok and "Z-17" not in plan.text and not any(m.text.startswith("Puentes") for m in plan.messages)


def test_a_bad_bridge_size_does_not_stop_a_job_with_the_master_switch_off():
    p = rect_project()
    row_of(p).tabs = True
    p.general.tab_width = -1
    assert not p.plan(None).ok
    p.general.bridges = False
    assert p.plan(None).ok


def test_the_master_switch_is_saved_in_the_job_and_an_old_job_leaves_it_as_it_is():
    p = rect_project()
    p.general.bridges = False
    data = json.loads(json.dumps(p.to_dict()))
    assert data["general"]["bridges"] is False
    q = Project(tools())
    q.new_drawing()
    q.apply_dict(data)
    assert q.general.bridges is False
    old = json.loads(json.dumps(p.to_dict()))
    del old["general"]["bridges"]
    r = Project(tools())
    r.new_drawing()
    r.general.bridges = False
    r.apply_dict(old)
    assert r.general.bridges is False                                       # sin dato en el trabajo: queda lo que había
    r.general.bridges = True
    r.apply_dict(old)
    assert r.general.bridges is True
