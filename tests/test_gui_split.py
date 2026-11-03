"""Separar interiores y exteriores y el orden de las filas en la ventana (sin pantalla): los botones, el menu, la tabla,
el aviso en rojo cuando alguien estropea el orden con las flechas y el boton Ordenar."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt

from cnc.gui.main_window import MainWindow
from cnc.model import Tool
from cnc.project import Project

BIG = Path(__file__).parent / "data" / "dibujo_grande.svg"


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


def names(w):
    t = w.ops.table
    return [t.item(i, 0).text() for i in range(t.rowCount())]


def combo(w, row, col):
    return w.ops.table.cellWidget(row, col)


def sides(w):
    return [combo(w, i, 3).currentText() for i in range(w.ops.table.rowCount())]


def group_pozo(w):
    """El pozo (niveles 1 y 2) como grupo de vaciado, con profundidad propia."""
    p = w.project
    idx = sorted(i for i, v in p.levels().items() if v in (1, 2))
    color = p.create_group(idx, "Pozo", "vaciado")
    row = next(r for r in p.rows if r.key == (color, None))
    row.through, row.depth = False, 10.0
    w._groups_changed(color, "x")
    return row


def warning_text(w):
    return w.ops.warning.text() if w.ops.warning.isVisible() else ""


# --- separar ---------------------------------------------------------------------------------------------------------
def test_the_button_is_on_the_panel_and_the_action_in_the_vectors_menu(arr):
    assert arr.ops.split_btn.text() == "Separar interior/exterior" and arr.ops.split_btn.isEnabled()
    assert arr.act_split.text() == "Separar interiores y exteriores" and arr.act_split.shortcut().toString() == "Ctrl+Shift+I"
    menu = next(a.menu() for a in arr.menuBar().actions() if a.menu() and a.text().replace("&", "") == "Formas")
    assert arr.act_split in menu.actions() and arr.act_sort in menu.actions()


def test_pressing_the_button_makes_one_row_per_kind_and_marks_the_first(arr):
    assert names(arr) == ["trazo #000000"] and sides(arr) == ["Auto"]
    arr.ops.split_btn.click()
    assert names(arr) == ["Interiores", "Exteriores"] and sides(arr) == ["Por dentro", "Por fuera"]
    assert [arr.ops.table.item(i, 1).text() for i in range(2)] == ["13", "13"]
    assert arr.ops.table.currentRow() == 0
    msg = arr.statusBar().currentMessage()
    assert "Separado en una fila por clase" in msg and "Interiores (13, por dentro)" in msg and "Exteriores (13, por fuera)" in msg


def thick(w):
    """Los vectores que el lienzo dibuja con la linea gruesa (los de la fila elegida en la tabla)."""
    return {i for i, item in w.canvas._shape_items.items() if item.pen().widthF() > 2.0}


def test_choosing_a_row_marks_its_vectors_in_the_drawing(arr):
    arr.ops.split_btn.click()
    lv = arr.project.levels()
    arr.ops.table.selectRow(1)
    assert thick(arr) == {i for i, v in lv.items() if v % 2 == 0}                             # exteriores: niveles pares
    arr.ops.table.selectRow(0)
    assert thick(arr) == {i for i, v in lv.items() if v % 2 == 1}


def test_each_new_row_has_its_own_color_on_the_drawing(arr):
    arr.ops.split_btn.click()
    p = arr.project
    colors = {r.display_color for r in p.rows}
    assert len(colors) == 2 and "#000000" not in colors
    shape_colors = {s.stroke for s in p.shapes()}
    assert shape_colors == colors


def test_the_gcode_is_the_same_before_and_after_and_the_rows_can_be_set_apart(arr):
    arr.plan_now()
    before = arr.last_plan.text
    assert arr.last_plan.ok
    arr.ops.split_btn.click()
    arr.plan_now()
    assert arr.last_plan.ok and arr.last_plan.text == before
    w = arr.ops._widgets[0]
    w["feed"].setText("120")
    w["feed"].valueChanged.emit()
    arr.plan_now()
    assert arr.last_plan.ok and arr.last_plan.text != before and "F120" in arr.last_plan.text
    assert arr.project.rows[1].feed is None


def test_the_menu_does_the_same_as_the_button(arr):
    arr.act_split.trigger()
    assert names(arr) == ["Interiores", "Exteriores"]


def test_splitting_is_undone_with_the_undo_action(arr):
    arr.ops.split_btn.click()
    assert arr.act_undo.isEnabled() and arr.act_undo.text() == "Deshacer: Separar interiores y exteriores"
    arr.act_undo.trigger()
    assert names(arr) == ["trazo #000000"] and sides(arr) == ["Auto"]
    arr.act_redo.trigger()
    assert names(arr) == ["Interiores", "Exteriores"]


def test_with_nothing_to_split_the_reason_is_shown_and_nothing_changes(arr):
    arr.ops.split_btn.click()
    before = names(arr)
    arr.ops.split_btn.click()                                            # la fila elegida (Interiores) ya tiene su lado
    assert names(arr) == before
    msg = arr.statusBar().currentMessage()
    assert msg.startswith("Separar interiores y exteriores:") and "ya tiene el lado fijado" in msg
    arr.ops.table.clearSelection()
    arr.ops.table.setCurrentCell(-1, -1)
    arr.ops.split_btn.click()
    assert names(arr) == before and "no hay ninguna fila" in arr.statusBar().currentMessage()


def test_with_no_drawing_nothing_happens(win):
    win.act_split.trigger()
    win.act_sort.trigger()
    assert win.project.rows == []


def test_a_row_chosen_in_the_table_is_the_one_split_when_there_are_several(arr):
    p = arr.project
    lv = p.levels()
    p.create_group(sorted(i for i, v in lv.items() if v in (0, 1)), "Cara", "corte")           # dos filas con huecos y piezas
    arr._groups_changed(None, "x")
    assert len(p.splittable_rows()) == 2
    cara = next(r for r in p.rows if r.title == "Cara")
    arr.ops.select_key(cara.key)
    arr.ops.split_btn.click()
    assert [r.title for r in p.splittable_rows()] == ["trazo #000000"]                         # la otra sigue sin separar
    assert "Cara: interiores" in names(arr) and "Cara: exteriores" in names(arr) and "trazo #000000" in names(arr)


def test_with_two_rows_that_can_be_split_and_none_chosen_the_window_asks_to_choose(arr):
    p = arr.project
    lv = p.levels()
    p.create_group(sorted(i for i, v in lv.items() if v in (0, 1)), "Cara", "corte")
    arr._groups_changed(None, "x")
    arr.ops.table.clearSelection()
    arr.ops.table.setCurrentCell(-1, -1)
    arr.ops.split_btn.click()
    assert "elige una en la tabla" in arr.statusBar().currentMessage()
    assert len(p.splittable_rows()) == 2


# --- el orden ---------------------------------------------------------------------------------------------------------
def test_a_new_pozo_goes_before_the_cuts_in_the_table(arr):
    group_pozo(arr)
    assert names(arr) == ["Pozo", "trazo #000000"]
    assert warning_text(arr) == ""


def test_the_warning_is_hidden_when_all_is_in_order_and_in_a_plain_job(arr):
    assert not arr.ops.warning.isVisible()
    arr.ops.split_btn.click()
    assert not arr.ops.warning.isVisible()
    assert arr.ops.warning.objectName() == "problem"


def test_moving_a_row_with_the_arrows_out_of_order_shows_the_red_warning(arr):
    group_pozo(arr)
    arr.ops.table.selectRow(0)
    arr.ops.down.click()
    assert names(arr) == ["trazo #000000", "Pozo"]
    text = warning_text(arr)
    assert text.startswith("⚠ El orden de las filas no es el correcto") and "taladros, pozos, interiores, líneas y exteriores" in text
    assert "«Pozo» (pozos) va después de «trazo #000000» (exteriores)" in text
    assert arr.ops.warning.styleSheet() == "" and arr.ops.warning.wordWrap()
    arr.ops.up.click()
    assert names(arr) == ["Pozo", "trazo #000000"] and warning_text(arr) == ""


def test_the_warning_is_red(arr):
    from cnc.gui.theme import ERROR, STYLE
    group_pozo(arr)
    arr.ops.table.selectRow(0)
    arr.ops.down.click()
    assert arr.ops.warning.isVisible() and arr.ops.warning.objectName() == "problem"
    assert "QLabel#problem { color: %s; }" % ERROR in STYLE


def test_the_order_button_puts_the_rows_in_order_and_says_so(arr):
    group_pozo(arr)
    arr.ops.table.selectRow(0)
    arr.ops.down.click()
    assert warning_text(arr)
    arr.ops.sort_btn.click()
    assert names(arr) == ["Pozo", "trazo #000000"] and warning_text(arr) == ""
    assert arr.statusBar().currentMessage() == "Filas ordenadas: taladros, pozos, interiores, líneas y exteriores."
    arr.ops.sort_btn.click()
    assert arr.statusBar().currentMessage() == "Las filas ya estaban en el orden correcto."
    assert arr.act_sort.text().startswith("Ordenar las filas")


def test_the_order_button_keeps_the_chosen_row_chosen(arr):
    group_pozo(arr)
    arr.ops.table.selectRow(0)
    arr.ops.down.click()
    arr.ops.table.selectRow(1)                                           # el pozo, que esta mal colocado
    arr.act_sort.trigger()
    assert names(arr)[arr.ops.table.currentRow()] == "Pozo"


def test_ordering_cannot_fix_two_mixed_rows_and_says_to_split_them(arr):
    p = arr.project
    lv = p.levels()
    p.create_group(sorted(i for i, v in lv.items() if v in (0, 1)), "Cara", "corte")
    arr._groups_changed(None, "x")
    p.rows[0].feed = 200.0                                                # fuera de «todo normal»: ya hay receta
    arr.ops.bind(p.rows)
    arr._update_ops_note()
    assert "Separar interiores y exteriores" in warning_text(arr)
    arr.ops.sort_btn.click()
    assert "Separar interiores y exteriores" in arr.statusBar().currentMessage()
    arr.ops.table.selectRow(0)
    arr.ops.split_btn.click()
    arr.ops.table.selectRow(arr.ops.table.rowCount() - 1)
    arr.ops.split_btn.click()
    assert warning_text(arr) == ""


def test_changing_the_action_of_a_row_puts_it_where_it_belongs_when_the_rest_was_in_order(arr):
    arr.ops.split_btn.click()
    assert names(arr) == ["Interiores", "Exteriores"]
    combo(arr, 1, 2).setCurrentIndex(combo(arr, 1, 2).findData("taladro"))                 # los exteriores pasan a taladros
    assert names(arr) == ["Exteriores", "Interiores"]
    assert arr.ops.table.currentRow() == 0 and names(arr)[0] == "Exteriores"                  # y la fila sigue elegida
    assert warning_text(arr) == ""


def test_changing_the_side_of_a_row_reorders_it_too(arr):
    arr.ops.split_btn.click()
    combo(arr, 0, 3).setCurrentIndex(combo(arr, 0, 3).findData("fuera"))
    combo(arr, 1, 3).setCurrentIndex(combo(arr, 1, 3).findData("dentro"))
    assert names(arr) == ["Exteriores", "Interiores"] and sides(arr) == ["Por dentro", "Por fuera"]
    assert warning_text(arr) == ""


def test_if_the_rows_were_moved_by_hand_changing_a_row_does_not_reorder_anything_and_the_warning_stays(arr):
    group_pozo(arr)
    arr.ops.table.selectRow(0)
    arr.ops.down.click()
    before = names(arr)
    combo(arr, 0, 2).setCurrentIndex(combo(arr, 0, 2).findData("corte"))                     # otra fila cualquiera
    assert names(arr) == before and warning_text(arr)


def test_generating_with_the_wrong_order_has_to_be_confirmed_as_an_alarm(arr):
    group_pozo(arr)
    arr.ops.table.selectRow(0)
    arr.ops.down.click()
    arr.plan_now()
    assert arr.last_plan.ok
    assert any(a.startswith("El orden de las filas no es el correcto") for a in arr.last_plan.alarms)
    arr.ops.sort_btn.click()
    arr.plan_now()
    assert arr.last_plan.ok and not any("orden de las filas" in a for a in arr.last_plan.alarms)


def test_restoring_the_table_clears_the_warning(arr):
    group_pozo(arr)
    arr.ops.table.selectRow(0)
    arr.ops.down.click()
    assert warning_text(arr)
    arr.ops.reset_btn.click()
    assert warning_text(arr) == ""


def test_the_panel_still_fits_in_a_small_screen_with_the_new_button(arr):
    arr.resize(1366, 768 - 60)
    arr.show()
    panel = arr.ops
    assert panel.split_btn.mapTo(panel, panel.split_btn.rect().bottomRight()).x() <= panel.width()
    assert panel.sel_label.width() >= 40
    assert panel.sort_btn.isVisible() and panel.sort_btn.mapTo(panel, panel.sort_btn.rect().bottomRight()).x() <= panel.width()


def test_the_order_button_needs_two_rows_and_the_split_button_one(arr):
    assert len(arr.project.rows) == 1 and not arr.ops.sort_btn.isEnabled() and arr.ops.split_btn.isEnabled()
    arr.ops.split_btn.click()
    assert arr.ops.sort_btn.isEnabled()


def test_with_no_row_chosen_and_only_one_that_can_be_split_the_button_splits_it(arr):
    arr.ops.table.clearSelection()
    arr.ops.table.setCurrentCell(-1, -1)
    assert arr.ops.current_key() is None
    arr.ops.split_btn.click()
    assert names(arr) == ["Interiores", "Exteriores"]


def test_with_another_row_chosen_and_only_one_that_can_be_split_the_button_splits_that_one(arr):
    row = group_pozo(arr)                                                # el pozo (vaciado) no se puede separar
    arr.ops.select_key(row.key)
    assert arr.ops.current_key() == row.key
    arr.ops.split_btn.click()
    assert names(arr) == ["Pozo", "Interiores", "Exteriores"]
    assert "Separado en una fila por clase" in arr.statusBar().currentMessage()
