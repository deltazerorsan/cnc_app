"""La herramienta Puente: poner, deslizar, estirar y quitar puentes con el raton, y la tira con sus numeros."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent, QTransform
from PySide6.QtWidgets import QApplication, QCheckBox, QGraphicsPathItem

from cnc import bridge_tools as bt
from cnc import draw_tools as dt
from cnc import drawing_edit as de
from cnc.gui.main_window import MainWindow
from cnc.model import Tool
from cnc.project import Project
from cnc.tabs import Tab

LEFT, NONE = Qt.LeftButton, Qt.NoButton
TABS_COLUMN = 5


def tools():
    return {"em4": Tool(diameter=4, name="End mill 4 mm", feed_xy=300, feed_z=200, stepdown=6.5, stepover=0.6)}


@pytest.fixture
def win(qtbot):
    w = MainWindow(Project(tools()), [], use_settings=False)
    qtbot.addWidget(w)
    w.show()
    w.new_drawing()
    w.detect_boards.setChecked(False)                           # un rectangulo con algo dentro no es aqui un tablero
    w.job.tool.setCurrentIndex(w.job.tool.findData("em4"))
    w.job.thickness.setText("19")
    w.job.thickness.valueChanged.emit()
    # un rectangulo de (10, 10) a (60, 40): 160 mm de contorno que empieza en su esquina inferior derecha, y un circulo dentro
    w.apply_edit(de.add_edit([de.make_rectangle(10, 10, 50, 30, 0, stroke="#000000")]), fit=True)
    w.apply_edit(de.add_edit([de.make_circle(35, 25, 5, stroke="#000000")]), fit=False)
    w.canvas.setTransform(QTransform.fromScale(6, -6))
    w.canvas.centerOn(35, 25)
    return w


# --- el raton simulado ------------------------------------------------------------------------------------------------
def at(w, x, y) -> QPointF:
    p = QPointF(w.canvas.mapFromScene(QPointF(x, y)))
    assert w.canvas.viewport().rect().contains(p.toPoint()), f"({x}, {y}) cae fuera de la vista"
    return p


def send(w, kind, x, y, button=LEFT, buttons=None):
    v = w.canvas.viewport()
    p = at(w, x, y)
    if buttons is None:
        buttons = NONE if kind == QEvent.MouseButtonRelease or button == NONE else button
    QApplication.sendEvent(v, QMouseEvent(kind, p, v.mapToGlobal(p), button, buttons, Qt.NoModifier))


def hover(w, x, y):
    send(w, QEvent.MouseMove, x, y, NONE, NONE)


def click(w, x, y):
    hover(w, x, y)
    send(w, QEvent.MouseButtonPress, x, y)
    send(w, QEvent.MouseButtonRelease, x, y)


def drag(w, a, b):
    hover(w, *a)
    send(w, QEvent.MouseButtonPress, *a)
    send(w, QEvent.MouseMove, *b, button=NONE, buttons=LEFT)
    send(w, QEvent.MouseButtonRelease, *b)


def key(w, k):
    QApplication.sendEvent(w.canvas, QKeyEvent(QEvent.KeyPress, k, Qt.NoModifier))


def tool(w):
    return w.canvas.tools.bridge


def tabs_of(w, i=0):
    return w.project.drawing.shapes[i].tabs


def drawn(w):
    return [i for i in w.canvas._scene.items() if isinstance(i, QGraphicsPathItem) and "puente(s)" in i.toolTip()]


# --- la herramienta ----------------------------------------------------------------------------------------------------
def test_the_bridge_tool_is_in_the_row_and_swaps_the_measures_for_its_own_bar(win):
    assert "puente" in dt.TOOLS and dt.KEYS["b"] == "puente"
    assert win._tool_actions["puente"].text() == "Puente"
    assert win.props.isVisible() and not win.bridge_bar.isVisible()
    win.choose_tool("puente")
    assert win.canvas.tools.name == "puente" and win.bridge_bar.isVisible() and not win.props.isVisible()
    assert win.bridge_bar.title.text() == "Sin puente" and not win.bridge_bar.edits[0].isEnabled()
    win.canvas.tools.escape()
    assert win.canvas.tools.name == "elegir" and win.props.isVisible() and not win.bridge_bar.isVisible()


def test_a_click_on_a_contour_puts_a_bridge_there_by_hand_and_undo_takes_it_away(win):
    win.choose_tool("puente")
    assert tabs_of(win) is None
    click(win, 30, 10)                                          # el lado de abajo del rectangulo
    tabs = tabs_of(win)
    assert tabs is not None and len(tabs) == 1 and (tabs[0].width, tabs[0].height) == (6, 2)
    x, y = bt.describe(win.project.drawing.shapes[0].shape, tabs[0])["x"], bt.describe(win.project.drawing.shapes[0].shape, tabs[0])["y"]
    assert abs(x - 30) < 0.3 and abs(y - 10) < 1e-6
    assert win.project.undo_label == "Puentes" and len(drawn(win)) == 1 and "a mano" in drawn(win)[0].toolTip()
    assert win.bridge_bar.title.text() == "Puente" and win.bridge_bar.remove.isEnabled()
    win.undo()
    assert tabs_of(win) is None and not drawn(win)


def test_a_click_away_from_every_contour_does_nothing(win):
    win.choose_tool("puente")
    steps = len(win.project._undo)
    click(win, 35, 32)
    assert tabs_of(win) is None and tabs_of(win, 1) is None and len(win.project._undo) == steps


def test_a_bridge_can_be_put_on_a_hole_too(win):
    win.choose_tool("puente")
    click(win, 35, 20)                                          # abajo del circulo
    assert tabs_of(win, 1) and not tabs_of(win, 0)
    plan = win.project.plan(None)
    assert plan.ok and any(m.text.startswith("Puentes: 1 en 1 forma") for m in plan.messages)
    assert any(op.side == "inside" and op.tabs for op in plan.operations)


def test_clicking_on_a_bridge_chooses_it_and_shows_its_numbers_without_editing(win):
    win.choose_tool("puente")
    click(win, 30, 10)
    before = len(win.project._undo)
    win.canvas.tools.bridge.cur = None
    click(win, 31, 10)                                          # sobre el mismo puente: no pone otro
    assert len(tabs_of(win)) == 1 and len(win.project._undo) == before
    bar = win.bridge_bar
    assert bar.title.text() == "Puente" and [e.text() for e in bar.edits][1:] == ["6", "2"]
    assert abs(float(bar.edits[0].text()) - tabs_of(win)[0].at * 160) < 0.01


def test_dragging_a_bridge_slides_it_along_the_contour_in_one_undo_step(win):
    win.choose_tool("puente")
    click(win, 30, 10)
    steps = len(win.project._undo)
    drag(win, (30, 10), (45, 10))
    tabs = tabs_of(win)
    assert len(tabs) == 1 and len(win.project._undo) == steps + 1
    cx = bt.describe(win.project.drawing.shapes[0].shape, tabs[0])["x"]
    assert abs(cx - 45) < 0.3 and (tabs[0].width, tabs[0].height) == (6, 2)
    win.undo()
    assert abs(bt.describe(win.project.drawing.shapes[0].shape, tabs_of(win)[0])["x"] - 30) < 0.3


def test_pulling_an_end_of_the_chosen_bridge_changes_its_width(win):
    win.choose_tool("puente")
    click(win, 30, 10)
    left_end = bt.describe(win.project.drawing.shapes[0].shape, tabs_of(win)[0])["x"] - 3
    drag(win, (left_end, 10), (left_end - 4, 10))               # el extremo izquierdo, 4 mm mas alla
    tab = tabs_of(win)[0]
    assert abs(tab.width - 10) < 0.3
    shape = win.project.drawing.shapes[0].shape
    assert abs(bt.describe(shape, tab)["x"] - (30 - 2)) < 0.4   # el otro extremo no se ha movido: el centro corre con el ancho


def test_supr_removes_the_chosen_bridge_and_never_the_shape(win):
    win.choose_tool("puente")
    click(win, 30, 10)
    assert len(win.project.drawing.shapes) == 2
    key(win, Qt.Key_Delete)
    assert len(win.project.drawing.shapes) == 2 and tabs_of(win) == ()
    assert win.bridge_bar.title.text() == "Sin puente" and not drawn(win)


def test_the_bar_numbers_move_and_resize_the_bridge(win):
    win.choose_tool("puente")
    click(win, 30, 10)
    bar = win.bridge_bar
    bar.edits[0].setText("20")
    bar.edits[1].setText("8")
    bar.edits[2].setText("3")
    bar.edits[2].editingFinished.emit()
    tab = tabs_of(win)[0]
    assert (round(tab.at * 160, 3), tab.width, tab.height) == (20, 8, 3)
    assert bar.edits[0].text() == "20" and bar.edits[1].text() == "8" and bar.edits[2].text() == "3"
    bar.edits[1].setText("0")
    bar.edits[1].editingFinished.emit()
    assert tabs_of(win)[0].width == 8 and "mayores que cero" in win.statusBar().currentMessage()
    assert bar.edits[1].text() == "8"


def test_automatic_bridges_are_shown_and_the_first_touch_turns_them_into_manual_ones(win):
    row = next(i for i, r in enumerate(win.project.rows) if r.stroke == "#000000")
    win.ops.table.cellWidget(row, TABS_COLUMN).findChild(QCheckBox).setChecked(True)
    auto = win.project.bridge_map()
    assert 0 in auto and auto[0][1] is False and len(auto[0][0]) >= 2
    win.refresh_canvas()
    assert any("automáticos" in i.toolTip() for i in drawn(win))
    win.choose_tool("puente")
    click(win, 35, 40)                                          # el lado de arriba
    assert len(tabs_of(win)) == len(auto[0][0]) + 1 and win.project.bridge_map()[0][1] is True
    win.bridge_bar.auto.click()
    assert tabs_of(win) is None and win.project.bridge_map()[0][1] is False


def test_the_menu_entries_turn_the_chosen_shapes_back_to_automatic_or_strip_them(win):
    win.choose_tool("puente")
    click(win, 30, 10)
    win.canvas.tools.escape()
    win.selection = {0}
    win._selection_changed()
    win.bridges_none()
    assert tabs_of(win) == ()
    win.bridges_automatic()
    assert tabs_of(win) is None
    win.bridges_automatic()
    assert "ya llevan los automáticos" in win.statusBar().currentMessage()


def test_bridges_are_refused_on_a_line_and_on_a_row_that_is_not_a_cut(win):
    win.apply_edit(de.add_edit([de.make_polyline([(70, 10), (90, 10)], False, stroke="#00ff00")]), fit=False)
    win.canvas.setTransform(QTransform.fromScale(4, -4))
    win.canvas.centerOn(50, 25)
    win.choose_tool("puente")
    click(win, 80, 10)
    assert tabs_of(win, 2) is None and "formas cerradas" in win.statusBar().currentMessage()
    row = next(i for i, r in enumerate(win.project.rows) if r.stroke == "#000000")
    action = win.ops.table.cellWidget(row, 2)
    action.setCurrentIndex(action.findData("vaciado"))
    win.canvas.setTransform(QTransform.fromScale(6, -6))
    win.canvas.centerOn(35, 25)
    click(win, 30, 10)
    assert tabs_of(win, 0) is None and "no es un corte" in win.statusBar().currentMessage()


def test_the_machine_view_shows_the_bridges_put_by_hand(win, qtbot):
    win.choose_tool("puente")
    click(win, 30, 10)
    win.canvas.tools.escape()
    win.set_view("machine")
    qtbot.waitUntil(lambda: win.last_plan is not None and any(getattr(op, "tabs", ()) for op in win.last_plan.operations), timeout=20000)
    assert win.last_plan.ok and "Z-17" in win.last_plan.text
    qtbot.waitUntil(lambda: bool(drawn(win)), timeout=20000)
