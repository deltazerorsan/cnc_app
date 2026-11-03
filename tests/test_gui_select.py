"""Elegir vectores en el dibujo (clic, Ctrl, Mayus, recuadro, menu Elegir) y darles su propia fila: la ventana sin pantalla."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog

from cnc.gui.canvas import ITEM_KIND
from cnc.gui.group_dialog import CHOICES, GroupDialog, free_name
from cnc.gui.main_window import MainWindow
from cnc.model import Tool
from cnc.oprows import ACTIONS, shape_key
from cnc.project import Project

DATA = Path(__file__).parent / "data"
BIG = DATA / "dibujo_grande.svg"
BOARDS19 = DATA / "tableros_19mm.svg"
POZO = {1, 2}


def tools():
    return {"em4": Tool(diameter=4, name="End mill 4 mm", feed_xy=300, feed_z=200, stepdown=6.5, stepover=0.6)}


@pytest.fixture
def win(qtbot):
    w = MainWindow(Project(tools()), [], use_settings=False)
    qtbot.addWidget(w)
    w.show()
    return w


@pytest.fixture
def arr(win):
    assert win.open_svg(BIG, apply_default_recipe=False)
    win.job.tool.setCurrentIndex(win.job.tool.findData("em4"))
    win.job.thickness.setText("19")
    win.job.thickness.valueChanged.emit()
    return win


def halos(w):
    return [i for i in w.canvas.scene().items() if i.data(ITEM_KIND) == "halo"]


def level_set(w, levels=POZO):
    return {i for i, lv in w.project.levels().items() if lv in levels}


def on_line(w, i, t=0.3) -> QPoint:
    """Un punto del lienzo (en pixeles) justo encima de la linea del vector i."""
    line = w.canvas._outlines[i]
    p = line.interpolate(t, normalized=True) if line.geom_type != "MultiLineString" else line.geoms[0].interpolate(t, normalized=True)
    return w.canvas.mapFromScene(QPointF(p.x, p.y))


def click(w, pos, mods=Qt.NoModifier):
    QTest.mouseClick(w.canvas.viewport(), Qt.LeftButton, mods, pos)


def drag(w, a, b, mods=Qt.ShiftModifier):
    v = w.canvas.viewport()
    QTest.mousePress(v, Qt.LeftButton, mods, a)
    QTest.mouseMove(v, b)
    QTest.mouseRelease(v, Qt.LeftButton, mods, b)


def pick_entries(w):
    w.ops._build_pick_menu()
    return [(a.text(), a.isChecked()) for a in w.ops.pick_menu.actions() if not a.isSeparator()]


def table_names(w):
    t = w.ops.table
    return [t.item(i, 0).text() for i in range(t.rowCount())]


def autoask(monkeypatch, w, name="Pozo", action="vaciado"):
    asked = []
    monkeypatch.setattr(w, "_ask_group", lambda: asked.append(len(w.selection)) or (name, action))
    return asked


# --- clics reales sobre el dibujo ---------------------------------------------------------------------------------
def test_a_plain_click_on_a_line_picks_that_vector_and_marks_its_row(arr):
    assert arr.selection == set() and halos(arr) == []
    click(arr, on_line(arr, 5))
    assert arr.selection == {5} and len(halos(arr)) == 1
    assert arr.ops.current_key() == shape_key(arr.project.shapes()[5])


def test_a_click_on_another_vector_replaces_the_pick(arr):
    click(arr, on_line(arr, 5))
    click(arr, on_line(arr, 9))
    assert arr.selection == {9} and len(halos(arr)) == 1


def test_ctrl_click_adds_and_removes_one_vector_without_touching_the_rest(arr):
    click(arr, on_line(arr, 5))
    click(arr, on_line(arr, 9), Qt.ControlModifier)
    assert arr.selection == {5, 9}
    click(arr, on_line(arr, 5), Qt.ControlModifier)
    assert arr.selection == {9} and len(halos(arr)) == 1


def test_shift_click_adds_a_vector(arr):
    click(arr, on_line(arr, 5))
    click(arr, on_line(arr, 9), Qt.ShiftModifier)
    click(arr, on_line(arr, 9), Qt.ShiftModifier)                               # otra vez el mismo: sigue elegido
    assert arr.selection == {5, 9}


def test_a_click_in_empty_space_clears_the_pick_but_ctrl_click_in_empty_space_keeps_it(arr):
    click(arr, on_line(arr, 5))
    corner = QPoint(2, 2)
    assert arr.canvas.shape_at(arr.canvas.mapToScene(corner)) is None
    click(arr, corner, Qt.ControlModifier)
    assert arr.selection == {5}
    click(arr, corner)
    assert arr.selection == set() and halos(arr) == []


def test_a_shift_drag_picks_what_fits_entirely_inside_the_box_and_adds_it(arr):
    c = arr.canvas
    inner = sorted(level_set(arr, {3}))[0]                                      # un agujero: dentro no cabe otro
    x0, y0, x1, y1 = arr.project.drawing.shapes[inner].shape.bounds
    rect = c.mapFromScene(QRectF(x0, y0, x1 - x0, y1 - y0)).boundingRect().adjusted(-3, -3, 3, 3)
    click(arr, on_line(arr, 5))
    drag(arr, rect.topLeft(), rect.bottomRight())
    assert arr.selection == {5, inner}
    assert not c._band.isVisible()


@pytest.mark.parametrize("side", ["left", "right", "top", "bottom"])
def test_a_box_that_cuts_through_a_vector_on_any_side_does_not_pick_it(arr, side):
    x0, y0, x1, y1 = arr.project.drawing.shapes[3].shape.bounds
    full = arr.canvas.mapFromScene(QRectF(x0, y0, x1 - x0, y1 - y0)).boundingRect().adjusted(-3, -3, 3, 3)
    drag(arr, full.topLeft(), full.bottomRight())
    assert 3 in arr.selection                                                   # entero dentro: se elige
    arr.pick_none()
    box = QRect(full)
    {"left": lambda: box.setLeft(box.left() + box.width() // 2), "right": lambda: box.setRight(box.right() - box.width() // 2),
     "top": lambda: box.setTop(box.top() + box.height() // 2),
     "bottom": lambda: box.setBottom(box.bottom() - box.height() // 2)}[side]()
    drag(arr, box.topLeft(), box.bottomRight())
    assert 3 not in arr.selection


def test_a_box_around_the_whole_drawing_picks_every_vector(arr):
    v = arr.canvas.viewport()
    drag(arr, QPoint(1, 1), QPoint(v.width() - 2, v.height() - 2))
    assert arr.selection == set(range(26)) and len(halos(arr)) == 26


def test_shift_without_moving_far_over_empty_space_picks_nothing(arr):
    click(arr, on_line(arr, 5))
    click(arr, QPoint(2, 2), Qt.ShiftModifier)
    assert arr.selection == {5}


def test_the_hit_distance_follows_the_pixels_not_the_millimetres(win, tmp_path):
    svg = tmp_path / "uno.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="300mm" height="200mm" viewBox="0 0 300 200">'
                   '<rect x="20" y="20" width="200" height="100" stroke="#ff0000" fill="none"/></svg>')
    win.detect_boards.setChecked(False)
    assert win.open_svg(svg, apply_default_recipe=False)
    c = win.canvas
    x0, y0, x1, y1 = win.project.drawing.shapes[0].shape.bounds
    top = QPointF((x0 + x1) / 2, y0)                                            # la mitad del lado de arriba del rectangulo
    for factor in (1.0, 0.5, 4.0):
        c.zoom_by(factor)
        c.centerOn(top)
        edge = c.mapFromScene(top)
        assert c.shape_at(c.mapToScene(edge + QPoint(0, -4))) == 0
        assert c.shape_at(c.mapToScene(edge + QPoint(0, 4))) == 0
        assert c.shape_at(c.mapToScene(edge + QPoint(0, -9))) is None
        assert c.shape_at(c.mapToScene(edge + QPoint(0, 9))) is None


def test_the_nearest_line_wins_when_two_are_within_reach(win, tmp_path):
    svg = tmp_path / "dos.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="300mm" height="200mm" viewBox="0 0 300 200">'
                   '<rect x="20" y="20" width="200" height="100" stroke="#ff0000" fill="none"/>'
                   '<rect x="20.5" y="20.5" width="199" height="99" stroke="#0000ff" fill="none"/></svg>')
    win.detect_boards.setChecked(False)
    assert win.open_svg(svg, apply_default_recipe=False)
    c = win.canvas
    shapes = win.project.drawing.shapes
    outer_shape = next(i for i, sh in enumerate(shapes) if sh.stroke == "#ff0000")
    inner_shape = next(i for i, sh in enumerate(shapes) if sh.stroke == "#0000ff")
    x0, y0, x1, y1 = shapes[outer_shape].shape.bounds
    mid = (x0 + x1) / 2
    outer = c.mapFromScene(QPointF(mid, y0))
    inner = c.mapFromScene(QPointF(mid, shapes[inner_shape].shape.bounds[1]))
    assert 0 < (inner - outer).manhattanLength() < 6                            # las dos lineas estan al alcance
    assert c.shape_at(c.mapToScene(inner)) == inner_shape and c.shape_at(c.mapToScene(outer)) == outer_shape


def test_in_the_machine_view_a_click_does_not_change_the_pick_and_it_is_back_in_the_drawing_view(arr):
    click(arr, on_line(arr, 5))
    arr.plan_now()
    arr.set_view("machine")
    assert arr.canvas.mode == "machine" and halos(arr) == []
    click(arr, QPoint(30, 30))
    assert arr.selection == {5}
    arr.set_view("drawing")
    assert len(halos(arr)) == 1


# --- el menu Elegir y los atajos ------------------------------------------------------------------------------------
def test_the_pick_menu_lists_the_levels_with_their_counts_and_checks_the_ones_fully_picked(arr):
    assert pick_entries(arr) == [("Todos", False), ("Ninguno", False), ("Nivel 0: exteriores (8)", False),
                                 ("Nivel 1: dentro de otro (8)", False), ("Nivel 2 (5)", False), ("Nivel 3 (5)", False)]
    arr.pick_level(1, True)
    arr.pick_level(2, True)
    assert [c for _, c in pick_entries(arr)] == [False, False, False, True, True, False]
    assert arr.selection == level_set(arr) and len(halos(arr)) == 13
    arr.pick_level(1, False)
    assert [c for _, c in pick_entries(arr)] == [False, False, False, False, True, False]
    assert arr.selection == level_set(arr, {2})


def test_a_partly_picked_level_is_not_checked_and_choosing_it_completes_it(arr):
    one = sorted(level_set(arr, {1}))[0]
    click(arr, on_line(arr, one))
    assert pick_entries(arr)[3][1] is False
    arr.ops.pick_menu.clear()
    arr.ops._build_pick_menu()
    next(a for a in arr.ops.pick_menu.actions() if a.text().startswith("Nivel 1")).trigger()
    assert arr.selection == level_set(arr, {1})
    assert pick_entries(arr)[3][1] is True


def test_unchecking_a_level_in_the_menu_removes_it_from_the_pick(arr):
    arr.pick_level(1, True)
    arr.ops._build_pick_menu()
    next(a for a in arr.ops.pick_menu.actions() if a.text().startswith("Nivel 1")).trigger()
    assert arr.selection == set()


def test_all_and_none_in_the_menu_and_the_shortcuts(arr):
    arr.ops._build_pick_menu()
    arr.ops.pick_menu.actions()[0].trigger()
    assert arr.selection == set(range(26))
    arr.ops.pick_menu.actions()[1].trigger()
    assert arr.selection == set()
    arr.pick_all()
    assert len(arr.selection) == 26
    arr.pick_none()
    assert arr.selection == set()


def test_picking_everything_in_a_board_drawing_leaves_the_board_rectangles_out(win):
    assert win.open_svg(BOARDS19, apply_default_recipe=False)
    win.pick_all()
    boards = {b.index for b in win.project.drawing.layout.boards}
    assert len(win.selection) == 126 and not win.selection & boards
    entries = [t for t, _ in pick_entries(win)]
    assert entries[-1] == "Líneas abiertas (8)" and sum(t.startswith("Nivel") for t in entries) == len(entries) - 3
    win.pick_none()
    win.pick_level(-1, True)
    assert {win.project.drawing.shapes[i].stroke for i in win.selection} == {"#ffcc00"} and len(win.selection) == 8


def test_open_lines_get_their_own_entry_and_are_picked_by_it(win, tmp_path):
    svg = tmp_path / "abierta.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="60mm" viewBox="0 0 100 60">'
                   '<rect x="10" y="10" width="40" height="30" stroke="#ff0000" fill="none"/>'
                   '<path d="M 60 10 L 90 40" stroke="#ff0000" fill="none"/></svg>')
    win.detect_boards.setChecked(False)
    assert win.open_svg(svg, apply_default_recipe=False)
    assert [t for t, _ in pick_entries(win)][-1] == "Líneas abiertas (1)"
    win.pick_level(-1, True)
    assert win.selection == {1} and "con líneas abiertas" in win.ops.sel_label.text()
    win.pick_level(-1, False)
    assert win.selection == set()


def test_the_label_says_what_is_picked_and_where_it_is_now(arr):
    assert win_hint(arr) == "Ningún vector elegido"
    arr.pick_level(1, True)
    arr.pick_level(2, True)
    assert arr.ops.sel_label.text() == "13 formas · niveles 1, 2 · ahora en: trazo #000000 (13)"
    click(arr, on_line(arr, 5))
    assert arr.ops.sel_label.text() == "1 forma · nivel 0 · ahora en: trazo #000000 (1)"
    assert arr.ops.move_btn.isEnabled()
    arr.pick_none()
    assert not arr.ops.move_btn.isEnabled() and win_hint(arr) == "Ningún vector elegido"


def win_hint(w):
    from cnc.gui.ops_panel import SELECTION_HINT
    assert w.ops.sel_label.text() in (SELECTION_HINT, w._selection_text())
    return "Ningún vector elegido" if w.ops.sel_label.text() == SELECTION_HINT else w.ops.sel_label.text()


def test_the_vector_menu_actions_are_enabled_only_with_something_picked(arr):
    assert not arr.act_new_group.isEnabled() and not arr.act_ungroup.isEnabled()
    arr.pick_level(3, True)
    assert arr.act_new_group.isEnabled() and arr.act_ungroup.isEnabled()
    arr.pick_none()
    assert not arr.act_new_group.isEnabled()


# --- grupos nuevos ------------------------------------------------------------------------------------------------
def test_a_new_group_from_the_picked_vectors_gets_its_row_marked_and_the_halo_stays(arr, monkeypatch):
    asked = autoask(monkeypatch, arr)
    arr.pick_level(1, True)
    arr.pick_level(2, True)
    arr.new_group()
    assert asked == [13]
    assert table_names(arr) == ["Pozo", "trazo #000000"] and arr.ops.table.currentRow() == 0       # el pozo va antes del corte
    row = arr.project.rows[0]
    assert (row.group, row.action, row.count) == (True, "vaciado", 13)
    assert arr.selection == level_set(arr) and len(halos(arr)) == 13
    assert arr.ops.sel_label.text().endswith("ahora en: Pozo (13)")
    assert "13 forma(s) en el grupo «Pozo»" in arr.statusBar().currentMessage()


def test_the_group_is_a_row_with_its_own_parameters_and_the_plan_has_the_pocket(arr, monkeypatch):
    autoask(monkeypatch, arr)
    arr.pick_level(1, True)
    arr.pick_level(2, True)
    arr.new_group()
    w = arr.ops._widgets[0]
    w["through"].setChecked(False)
    w["depth"].setText("10")
    w["depth"].valueChanged.emit()
    arr.plan_now()
    assert arr.last_plan.ok, arr.last_plan.error
    assert (arr.project.rows[0].through, arr.project.rows[0].depth) == (False, 10.0) and arr.project.rows[0].group
    assert any(type(op).__name__ == "PocketOp" for op in arr.last_plan.job.operations)


def test_cancelling_the_dialog_changes_nothing(arr, monkeypatch):
    monkeypatch.setattr(arr, "_ask_group", lambda: None)
    arr.pick_level(1, True)
    before = list(arr.project.rows)
    arr.new_group()
    assert arr.project.assign == {} and arr.project.rows == before and len(arr.selection) == 8


def test_new_group_with_nothing_picked_does_nothing(arr, monkeypatch):
    asked = autoask(monkeypatch, arr)
    arr.new_group()
    arr.ungroup()
    arr.move_to_group("#e6194b")
    assert asked == [] and arr.project.assign == {}


def test_a_group_can_take_more_vectors_from_the_move_menu_and_give_them_back(arr, monkeypatch):
    autoask(monkeypatch, arr)
    arr.pick_level(1, True)
    arr.new_group()
    arr.pick_none()
    arr.pick_level(2, True)
    arr.ops._build_move_menu()
    texts = [a.text() for a in arr.ops.move_menu.actions() if not a.isSeparator()]
    assert texts == ["Pozo", "Grupo nuevo…", "Quitar del grupo (vuelve a su color)"]
    arr.ops.move_menu.actions()[0].trigger()
    assert arr.project.rows[0].count == 13 and "movidas al grupo «Pozo»" in arr.statusBar().currentMessage()
    arr.ops._build_move_menu()
    arr.ops.move_menu.actions()[-1].trigger()
    assert arr.project.rows[0].count == 8 and [r.count for r in arr.project.rows] == [8, 18]
    assert "vuelven a su color" in arr.statusBar().currentMessage()


def test_new_group_from_the_move_menu_entry_asks_for_the_dialog(arr, monkeypatch):
    asked = autoask(monkeypatch, arr, "Taladros", "taladro")
    arr.pick_level(3, True)
    arr.ops._build_move_menu()
    next(a for a in arr.ops.move_menu.actions() if a.text() == "Grupo nuevo…").trigger()
    assert asked == [5] and arr.project.rows[0].action == "taladro" and arr.project.rows[0].name == "Taladros"


def test_the_group_name_is_renamed_in_its_cell_and_an_empty_name_is_refused(arr, monkeypatch):
    autoask(monkeypatch, arr)
    arr.pick_level(3, True)
    arr.new_group()
    t = arr.ops.table
    t.item(0, 0).setText("  Agujeros  ")
    assert arr.project.rows[0].name == "Agujeros" and t.item(0, 0).text() == "Agujeros"
    assert "Agujeros" in t.item(0, 0).toolTip()
    t.item(0, 0).setText("   ")
    assert arr.project.rows[0].name == "Agujeros" and t.item(0, 0).text() == "Agujeros"
    t.item(1, 0).setText("renombrado")                                              # un color del dibujo no se renombra
    assert arr.project.rows[1].name == ""


def test_renaming_a_group_tells_the_window_but_giving_it_the_same_name_does_not(arr, monkeypatch, qtbot):
    autoask(monkeypatch, arr)
    arr.pick_level(3, True)
    arr.new_group()
    item = arr.ops.table.item(0, 0)
    with qtbot.assertNotEmitted(arr.ops.changed):
        item.setText("  Pozo  ")                                                  # es el mismo nombre, con espacios
    assert item.text() == "Pozo"
    with qtbot.waitSignal(arr.ops.changed, timeout=1000):
        item.setText("Agujeros")


def test_only_the_name_of_a_group_can_be_edited_in_the_table(arr, monkeypatch):
    autoask(monkeypatch, arr)
    arr.pick_level(3, True)
    arr.new_group()
    t = arr.ops.table
    editable = [[bool(t.item(r, c) and t.item(r, c).flags() & Qt.ItemIsEditable) for c in range(2)] for r in range(2)]
    assert editable == [[True, False], [False, False]]                                   # la fila del grupo va la primera


def test_the_tooltip_of_a_grouped_vector_uses_the_group_name(arr, monkeypatch):
    autoask(monkeypatch, arr, "Pozo")
    arr.pick_level(1, True)
    arr.new_group()
    tips = {i.data(1): i.toolTip() for i in arr.canvas.scene().items() if i.data(ITEM_KIND) == "shape"}
    grouped = next(iter(level_set(arr, {1})))
    plain = next(iter(level_set(arr, {0})))
    assert tips[grouped] == f"Pozo (forma {grouped})" and tips[plain] == f"#000000 (forma {plain})"


def test_grouped_vectors_are_drawn_in_the_color_of_their_group(arr, monkeypatch):
    autoask(monkeypatch, arr)
    arr.pick_level(1, True)
    arr.new_group()
    assert arr.project.rows[0].group
    color = arr.project.rows[0].display_color
    pens = {i.data(1): i.pen().color().name() for i in arr.canvas.scene().items() if i.data(ITEM_KIND) == "shape"}
    for i in level_set(arr, {1}):
        assert pens[i] == color
    assert all(pens[i] == "#000000" for i in level_set(arr, {0, 2, 3}))


def test_resetting_the_table_keeps_the_groups_and_the_pick(arr, monkeypatch):
    autoask(monkeypatch, arr)
    arr.pick_level(1, True)
    arr.new_group()
    arr.project.rows[0].action = "ignorar"
    arr.ops.reset_btn.click()
    assert [(r.group, r.action) for r in arr.project.rows] == [(True, "corte"), (False, "corte")]
    assert table_names(arr) == ["Pozo", "trazo #000000"]


def test_ctrl_a_and_escape_are_in_the_edit_menu_and_the_group_shortcuts_in_the_vectors_menu(arr):
    def keys(title):
        menu = next(a.menu() for a in arr.menuBar().actions() if a.menu() and a.text().replace("&", "") == title)
        return {a.text().replace("&", ""): a.shortcut().toString() for a in menu.actions() if not a.isSeparator()}
    edit = keys("Edición")
    assert edit["Elegir todos"] == "Ctrl+A" and edit["Quitar la elección"] == "Esc"
    assert keys("Formas") == {"Grupo nuevo con lo elegido…": "Ctrl+Shift+N", "Quitar del grupo": "Ctrl+Shift+U",
                                "Separar interiores y exteriores": "Ctrl+Shift+I",
                                "Ordenar las filas (taladros, pozos, interiores, exteriores)": ""}


# --- guardar y abrir ------------------------------------------------------------------------------------------------
def test_saving_and_opening_a_job_brings_the_groups_back_with_the_table(arr, monkeypatch, tmp_path, qtbot):
    autoask(monkeypatch, arr)
    arr.pick_level(1, True)
    arr.pick_level(2, True)
    arr.new_group()
    arr.project.rows[0].through, arr.project.rows[0].depth = False, 10.0
    job = tmp_path / "big_drawing.cncjob"
    assert arr.save_job(job)
    other = MainWindow(Project(tools()), [], use_settings=False)
    qtbot.addWidget(other)
    other.show()
    assert other.open_job(job)
    assert table_names(other) == ["Pozo", "trazo #000000"] and other.selection == set()
    assert other.project.assign == arr.project.assign
    assert (other.project.rows[0].through, other.project.rows[0].depth) == (False, 10.0)
    assert [(i.data(1), i.toolTip()) for i in other.canvas.scene().items() if i.data(ITEM_KIND) == "shape"
            and i.data(1) in level_set(other, {1})][0][1].startswith("Pozo")


def test_a_job_whose_drawing_has_changed_opens_without_groups_and_says_so(arr, monkeypatch, tmp_path, qtbot):
    autoask(monkeypatch, arr)
    arr.pick_level(1, True)
    arr.new_group()
    job = tmp_path / "big_drawing.cncjob"
    arr.save_job(job)
    data = json.loads(job.read_text(encoding="utf-8"))
    data["num_formas"] = 27
    job.write_text(json.dumps(data), encoding="utf-8")
    other = MainWindow(Project(tools()), [], use_settings=False)
    qtbot.addWidget(other)
    assert other.open_job(job)
    assert other.project.assign == {} and table_names(other) == ["trazo #000000"]
    other.plan_now()
    assert any("ha cambiado" in t for t in other.messages.texts())


def test_opening_another_drawing_clears_the_pick_and_the_groups(arr, monkeypatch):
    autoask(monkeypatch, arr)
    arr.pick_level(1, True)
    arr.new_group()
    assert arr.open_svg(BOARDS19, apply_default_recipe=False)
    assert arr.selection == set() and arr.project.assign == {} and halos(arr) == []
    assert arr.ops.sel_label.text() != "" and not arr.ops.move_btn.isEnabled()


def test_a_selection_survives_a_recalculation_and_switching_views(arr):
    arr.pick_level(3, True)
    arr.plan_now()
    arr.refresh_all()
    assert len(arr.selection) == 5 and len(halos(arr)) == 5


# --- el cuadro de dialogo --------------------------------------------------------------------------------------------
def test_the_dialog_offers_every_operation_of_the_table_with_a_proposed_name(qtbot):
    d = GroupDialog(13, [])
    qtbot.addWidget(d)
    assert [d.kind.itemData(i) for i in range(d.kind.count())] == [c[0] for c in CHOICES]
    assert set(c[0] for c in CHOICES) == set(ACTIONS)
    assert (d.action(), d.name()) == ("vaciado", "Pozo")
    d.kind.setCurrentIndex(d.kind.findData("taladro"))
    assert (d.action(), d.name()) == ("taladro", "Taladros")
    d.kind.setCurrentIndex(d.kind.findData("ignorar"))
    assert d.name() == "Ignorados"


def test_the_proposed_name_follows_the_type_until_the_user_types_one(qtbot):
    d = GroupDialog(2, [])
    qtbot.addWidget(d)
    d.name_edit.clear()
    QTest.keyClicks(d.name_edit, "Mi pozo")
    d.kind.setCurrentIndex(d.kind.findData("corte"))
    assert d.name() == "Mi pozo" and d.action() == "corte"


def test_the_dialog_avoids_names_already_taken(qtbot):
    d = GroupDialog(2, ["Pozo", "Pozo 2"])
    qtbot.addWidget(d)
    assert d.name() == "Pozo 3"
    d.kind.setCurrentIndex(d.kind.findData("corte"))
    assert d.name() == "Corte"


def test_the_dialog_text_and_the_name_is_trimmed(qtbot):
    d = GroupDialog(7, [])
    qtbot.addWidget(d)
    assert d.findChildren(type(d.layout().itemAt(0).widget()))[0].text().startswith("7 forma(s)")
    d.name_edit.setText("  Rebaje  ")
    assert d.name() == "Rebaje"


def test_free_name():
    assert free_name("Pozo", []) == "Pozo"
    assert free_name("Pozo", ["Pozo"]) == "Pozo 2"
    assert free_name("Pozo", ["Pozo", "Pozo 3"]) == "Pozo 2"
    assert free_name("Pozo", ["Pozo", "Pozo 2", "Pozo 3"]) == "Pozo 4"


def test_the_real_dialog_is_used_and_accepting_it_makes_the_group(arr, monkeypatch):
    monkeypatch.setattr(GroupDialog, "exec", lambda self: QDialog.Accepted)
    arr.pick_level(3, True)
    arr.new_group()
    assert arr.project.rows[0].name == "Pozo" and arr.project.rows[0].action == "vaciado"
    monkeypatch.setattr(GroupDialog, "exec", lambda self: QDialog.Rejected)
    arr.pick_none()
    arr.pick_level(2, True)
    arr.new_group()
    assert len(arr.project.groups()) == 1
