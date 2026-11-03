"""Dibujar y mover con el raton: herramientas, iman, medidas en vivo, Esc y desplazamiento, con eventos simulados."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import math
import re

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QMouseEvent, QTransform
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QToolButton

from cnc import draw_tools as dt
from cnc import drawing_edit as de
from cnc.gui.draw_dialogs import ToolDialog
from cnc.gui.draw_layer import HUD_OFFSET, Z_GHOST, Z_MARKER, Z_PREVIEW, marker_path
from cnc.gui import theme
from cnc.gui.main_window import MainWindow
from cnc.model import Tool
from cnc.project import Project

K = "#000000"
LEFT, NONE = Qt.LeftButton, Qt.NoButton


def tools():
    return {"em4": Tool(diameter=4, name="End mill 4 mm", feed_xy=300, feed_z=200, stepdown=6.5, stepover=0.6)}


@pytest.fixture
def win(qtbot):
    w = MainWindow(Project(tools()), [], use_settings=False)
    qtbot.addWidget(w)
    w.show()
    return w


def zoom(w):
    """4 pixeles por mm y a la vista de X -25..225, Y -2..122: asi un punto del dibujo cae a menos de 0.15 mm de su pixel."""
    w.canvas.setTransform(QTransform.fromScale(4, -4))
    w.canvas.centerOn(100, 60)


@pytest.fixture
def blank(win):
    win.new_drawing()
    zoom(win)
    return win


def answer(monkeypatch, **values):
    seen = {}

    def fake_exec(self):
        seen["dialog"] = self
        for key, value in values.items():
            self.set_value(key, value)
        return QDialog.Accepted if self.buttons.button(QDialogButtonBox.Ok).isEnabled() else QDialog.Rejected

    monkeypatch.setattr(ToolDialog, "exec", fake_exec)
    return seen


@pytest.fixture
def two(blank, monkeypatch):
    """Un rectangulo de (10, 10) a (60, 40) y un circulo de 20 de diametro en (120, 50)."""
    answer(monkeypatch, x=10, y=10, w=50, h=30, r=0)
    blank.draw_rectangle()
    answer(monkeypatch, x=120, y=50, d=20)
    blank.draw_circle()
    blank.selection = {0}
    blank._selection_changed()
    zoom(blank)
    return blank


# --- el raton simulado ---------------------------------------------------------------------------------------------------
def at(w, x, y) -> QPointF:
    p = QPointF(w.canvas.mapFromScene(QPointF(x, y)))
    assert w.canvas.viewport().rect().contains(p.toPoint()), f"({x}, {y}) cae fuera de la vista"
    return p


def send(w, kind, x, y, button=LEFT, buttons=None, mods=Qt.NoModifier):
    v = w.canvas.viewport()
    p = at(w, x, y)
    if buttons is None:
        buttons = NONE if kind == QEvent.MouseButtonRelease or button == NONE else button
    QApplication.sendEvent(v, QMouseEvent(kind, p, v.mapToGlobal(p), button, buttons, mods))


def hover(w, x, y, mods=Qt.NoModifier):
    send(w, QEvent.MouseMove, x, y, NONE, NONE, mods)


def press(w, x, y, mods=Qt.NoModifier, button=LEFT):
    send(w, QEvent.MouseButtonPress, x, y, button, mods=mods)


def release(w, x, y, mods=Qt.NoModifier, button=LEFT):
    send(w, QEvent.MouseButtonRelease, x, y, button, mods=mods)


def click(w, x, y, mods=Qt.NoModifier, button=LEFT):
    hover(w, x, y, mods)
    press(w, x, y, mods, button)
    release(w, x, y, mods, button)


def double_click(w, x, y):
    click(w, x, y)
    send(w, QEvent.MouseButtonDblClick, x, y)
    release(w, x, y)


def drag(w, a, b, mods=Qt.NoModifier):
    press(w, *a, mods)
    send(w, QEvent.MouseMove, *b, button=NONE, buttons=LEFT, mods=mods)
    release(w, *b, mods)


def layer(w):
    return w.canvas.tools


def shape_bounds(w, i=-1):
    return tuple(w.project.drawing.shapes[i].shape.bounds)


def count(w):
    return len(w.project.drawing.shapes)


def status(w):
    return w.statusBar().currentMessage()


def items(w, key):
    return layer(w)._items.get(key)


def use(w, name):
    w.choose_tool(name)
    assert layer(w).name == name


# --- la tira y el menu ---------------------------------------------------------------------------------------------------
def test_a_window_without_a_drawing_has_the_tools_off_and_select_ticked(win):
    assert set(win._tool_actions) == set(dt.TOOLS)
    assert win._tool_actions["elegir"].isChecked() and layer(win).name == "elegir"
    assert not any(a.isEnabled() for a in win._tool_actions.values())


def test_a_drawing_turns_the_tools_on_and_each_button_says_its_key(blank):
    assert all(a.isEnabled() for a in blank._tool_actions.values())
    buttons = [b for b in blank.findChildren(QToolButton) if b.defaultAction() in blank._tool_actions.values()]
    assert len(buttons) == len(dt.TOOLS)
    for b in buttons:
        name = b.defaultAction().text()
        key = next(k for k, v in dt.KEYS.items() if dt.LABELS[v] == name)
        assert f"({key.upper()})" in b.toolTip() and name in b.toolTip()


def test_the_draw_menu_has_a_submenu_with_the_same_actions_and_the_options(blank):
    sub = next(a.menu() for m in blank.menuBar().actions() if m.text() == "&Dibujo" for a in m.menu().actions()
               if a.text() == "Dibujar con el ratón")
    acts = sub.actions()
    assert [a for a in acts if a in blank._tool_actions.values()] == [blank._tool_actions[n] for n in dt.TOOLS]
    assert acts[-1].text() == "Opciones del ratón…"


def test_choosing_a_tool_ticks_it_and_shows_the_first_hint(blank):
    blank._tool_actions["rectangulo"].trigger()
    assert layer(blank).name == "rectangulo" and layer(blank).active
    assert blank._tool_actions["rectangulo"].isChecked() and not blank._tool_actions["elegir"].isChecked()
    assert status(blank) == dt.Session("rectangulo").hint()
    assert blank.canvas.dragMode() == blank.canvas.DragMode.NoDrag
    blank._tool_actions["elegir"].trigger()
    assert layer(blank).name == "elegir" and blank.canvas.dragMode() == blank.canvas.DragMode.ScrollHandDrag
    assert not layer(blank).active


def test_move_needs_something_chosen_and_says_so_without_changing_tool(blank):
    use(blank, "rectangulo")
    blank._tool_actions["mover"].trigger()
    assert layer(blank).name == "rectangulo" and blank._tool_actions["rectangulo"].isChecked()
    assert not blank._tool_actions["mover"].isChecked() and "elige primero" in status(blank)


def test_the_machine_view_switches_the_tools_off_and_back_to_select(blank):
    use(blank, "circulo")
    blank.set_view("machine")
    assert layer(blank).name == "elegir" and blank._tool_actions["elegir"].isChecked()
    assert not any(a.isEnabled() for a in blank._tool_actions.values())
    blank.set_view("drawing")
    assert all(a.isEnabled() for a in blank._tool_actions.values())
    assert not layer(blank).set_tool("circulo") or layer(blank).canvas.mode == "drawing"


def test_a_tool_cannot_be_chosen_while_the_drawing_is_not_shown(blank):
    blank.set_view("machine")
    assert layer(blank).set_tool("rectangulo") is False and layer(blank).name == "elegir"
    with pytest.raises(ValueError):
        layer(blank).set_tool("taladro")


def test_loading_another_drawing_leaves_the_tool(blank):
    use(blank, "linea")
    click(blank, 10, 10)
    assert layer(blank).busy
    blank.new_drawing()
    assert layer(blank).name == "elegir" and not layer(blank).busy


# --- rectangulo, circulo, elipse, poligono, ranura --------------------------------------------------------------------------
def test_two_clicks_draw_a_rectangle_and_the_tool_stays_for_the_next(blank):
    use(blank, "rectangulo")
    click(blank, 10, 20)
    assert layer(blank).busy and count(blank) == 0
    click(blank, 60, 50)
    assert count(blank) == 1 and shape_bounds(blank) == pytest.approx((10, 20, 60, 50), abs=0.2)
    assert blank.project.drawing.shapes[0].kind == "rect" and blank.selection == {0} and blank._dirty
    assert layer(blank).name == "rectangulo" and not layer(blank).busy
    click(blank, 100, 20)
    click(blank, 120, 30)
    assert count(blank) == 2 and shape_bounds(blank) == pytest.approx((100, 20, 120, 30), abs=0.2)
    assert blank.selection == {1}


def test_the_first_shape_drawn_with_the_mouse_does_not_move_the_view(blank):
    use(blank, "rectangulo")
    before = (blank.canvas.transform(), blank.canvas.centre())
    drag(blank, (10, 20), (60, 50))
    assert blank.canvas.transform() == before[0]
    c = blank.canvas.centre()
    assert (c.x(), c.y()) == pytest.approx((before[1].x(), before[1].y()), abs=1)       # sin saltos (un par de pixeles de redondeo)


def test_dragging_draws_a_rectangle_in_one_gesture(blank):
    use(blank, "rectangulo")
    drag(blank, (10, 20), (60, 50))
    assert count(blank) == 1 and shape_bounds(blank) == pytest.approx((10, 20, 60, 50), abs=0.2)
    assert not layer(blank).busy


def test_the_preview_follows_a_drag_before_the_button_is_released(blank):
    layer(blank).configure(dt.Options(grid=5))
    use(blank, "rectangulo")
    press(blank, 10, 20)
    assert layer(blank).busy and count(blank) == 0
    send(blank, QEvent.MouseMove, 60, 50, button=NONE, buttons=LEFT)
    assert items(blank, "preview").isVisible() and layer(blank).hud.text() == "X 60  Y 50\n50 × 30 mm"
    release(blank, 60, 50)
    assert count(blank) == 1 and shape_bounds(blank) == pytest.approx((10, 20, 60, 50), abs=1e-9)


def test_a_tiny_movement_between_press_and_release_is_a_click_not_a_drag(blank):
    use(blank, "rectangulo")
    press(blank, 10, 20)
    release(blank, 10.5, 20)                        # dos pixeles: un clic
    assert layer(blank).busy and count(blank) == 0
    click(blank, 60, 50)
    assert count(blank) == 1


def test_a_drawn_shape_can_be_undone_and_the_drawing_is_marked_changed(blank):
    use(blank, "rectangulo")
    drag(blank, (10, 20), (60, 50))
    blank.undo()
    assert count(blank) == 0
    blank.redo()
    assert count(blank) == 1


def test_the_new_shape_uses_the_colour_of_the_drawing(blank):
    blank.project.draw_color = "#ff0000"
    use(blank, "circulo")
    drag(blank, (50, 50), (60, 50))
    assert blank.project.drawing.shapes[0].stroke == "#ff0000"
    blank.project.draw_color = "#00aa00"                          # cambiado con la herramienta ya elegida
    drag(blank, (100, 50), (110, 50))
    assert blank.project.drawing.shapes[1].stroke == "#00aa00"


def test_a_circle_takes_the_centre_and_a_point_on_its_edge(blank):
    use(blank, "circulo")
    click(blank, 50, 50)
    click(blank, 60, 50)
    b = shape_bounds(blank)
    assert blank.project.drawing.shapes[0].kind == "circle" and b == pytest.approx((40, 40, 60, 60), abs=0.2)


def test_an_ellipse_fills_its_box_and_shift_makes_it_a_circle(blank):
    use(blank, "elipse")
    click(blank, 10, 20)
    click(blank, 70, 50)
    assert shape_bounds(blank) == pytest.approx((10, 20, 70, 50), abs=0.2)
    click(blank, 100, 20)
    hover(blank, 130, 30, Qt.ShiftModifier)
    a, b = re.search(r"([\d.]+) × ([\d.]+) mm", layer(blank).hud.text()).groups()
    assert a == b and float(a) == pytest.approx(30, abs=0.3)
    press(blank, 130, 30, Qt.ShiftModifier)
    release(blank, 130, 30, Qt.ShiftModifier)
    x0, y0, x1, y1 = shape_bounds(blank)
    assert (x1 - x0) == pytest.approx(y1 - y0, abs=1e-6) and (x1 - x0) == pytest.approx(30, abs=0.3)


def test_a_polygon_uses_the_sides_from_the_options_and_its_first_vertex_points_at_the_cursor(blank):
    layer(blank).configure(dt.Options(sides=5))
    use(blank, "poligono")
    click(blank, 50, 50)
    click(blank, 50, 70)
    s = blank.project.drawing.shapes[0]
    assert s.kind == "polygon" and len(s.shape.exterior.coords) == 6
    assert math.dist(s.shape.exterior.coords[0], (50, 70)) < 0.3


def test_a_slot_uses_the_width_from_the_options(blank):
    layer(blank).configure(dt.Options(slot_width=12))
    use(blank, "ranura")
    drag(blank, (30, 50), (90, 50))
    b = shape_bounds(blank)
    assert b == pytest.approx((24, 44, 96, 56), abs=0.3)


def test_the_rectangle_corner_radius_comes_from_the_options(blank):
    layer(blank).configure(dt.Options(radius=4))
    use(blank, "rectangulo")
    drag(blank, (10, 20), (60, 50))
    plain = 50 * 30
    assert blank.project.drawing.shapes[0].shape.area == pytest.approx(plain - (4 - math.pi) * 16, rel=0.01)


def test_a_release_that_nobody_pressed_does_nothing(blank):
    use(blank, "rectangulo")
    release(blank, 10, 20)
    assert not layer(blank).busy and count(blank) == 0


def test_a_double_click_with_a_tool_that_is_not_a_line_adds_nothing_more(blank, qtbot):
    use(blank, "rectangulo")
    click(blank, 10, 20)
    with qtbot.assertNotEmitted(layer(blank).status):                    # no intenta acabar nada: ni siquiera repite la ayuda
        send(blank, QEvent.MouseButtonDblClick, 10, 20)
        release(blank, 10, 20)
    assert layer(blank).session.points == [pytest.approx((10, 20), abs=0.2)] and count(blank) == 0


def test_enter_and_backspace_in_a_tool_that_is_not_a_line(blank, qtbot):
    use(blank, "rectangulo")
    QTest.keyClick(blank.canvas, Qt.Key_Return)
    click(blank, 10, 20)
    with qtbot.assertNotEmitted(layer(blank).status):
        QTest.keyClick(blank.canvas, Qt.Key_Return)
    assert layer(blank).busy and count(blank) == 0
    QTest.keyClick(blank.canvas, Qt.Key_Backspace)
    assert not layer(blank).busy


def test_a_second_click_on_the_same_spot_is_refused_with_the_reason_and_the_tool_goes_on(blank):
    use(blank, "rectangulo")
    click(blank, 10, 20)
    press(blank, 10, 20)
    release(blank, 10, 20)
    assert layer(blank).busy and count(blank) == 0
    assert "coinciden" in status(blank) and "coinciden" in layer(blank).hud.text()
    click(blank, 60, 50)
    assert count(blank) == 1 and layer(blank).problem == ""


def test_a_rectangle_with_no_height_is_refused(blank):
    use(blank, "rectangulo")
    click(blank, 10, 20)
    click(blank, 60, 20)
    assert count(blank) == 0 and layer(blank).busy and layer(blank).problem
    assert layer(blank).problem in status(blank)


# --- polilinea -----------------------------------------------------------------------------------------------------------------
def test_clicks_make_a_polyline_and_a_double_click_ends_it(blank):
    use(blank, "linea")
    click(blank, 10, 10)
    click(blank, 50, 10)
    click(blank, 50, 40)
    assert layer(blank).busy and count(blank) == 0
    double_click(blank, 90, 40)
    assert count(blank) == 1 and not layer(blank).busy
    s = blank.project.drawing.shapes[0]
    assert s.kind == "polyline" and not s.closed and len(s.shape.coords) == 4
    assert s.shape.coords[-1] == pytest.approx((90, 40), abs=0.2)


def test_enter_ends_a_polyline_and_right_click_too(blank):
    use(blank, "linea")
    click(blank, 10, 10)
    click(blank, 50, 10)
    QTest.keyClick(blank.canvas, Qt.Key_Return)
    assert count(blank) == 1 and blank.project.drawing.shapes[0].kind == "line"
    click(blank, 10, 30)
    click(blank, 50, 30)
    click(blank, 50, 60)
    click(blank, 90, 60, button=Qt.RightButton)
    assert count(blank) == 2 and len(blank.project.drawing.shapes[1].shape.coords) == 3


def test_enter_with_nothing_started_does_nothing_and_with_one_point_says_why(blank):
    use(blank, "linea")
    QTest.keyClick(blank.canvas, Qt.Key_Return)
    assert count(blank) == 0
    click(blank, 10, 10)
    QTest.keyClick(blank.canvas, Qt.Key_Enter)
    assert count(blank) == 0 and layer(blank).busy and "al menos dos puntos" in status(blank)


def test_clicking_the_start_closes_the_contour(blank):
    use(blank, "linea")
    for p in ((10, 10), (60, 10), (60, 40)):
        click(blank, *p)
    hover(blank, 10, 10)
    assert layer(blank).spot.kind == "inicio" and "cerrar" in layer(blank).hud.text()
    assert items(blank, "marker").isVisible()
    click(blank, 10, 10)
    s = blank.project.drawing.shapes[0]
    assert s.closed and s.kind == "polygon" and s.shape.area == pytest.approx(750, rel=0.01)


def test_the_start_closes_even_with_the_snap_turned_off_but_not_with_ctrl(blank):
    layer(blank).configure(dt.Options(snap=False))
    use(blank, "linea")
    for p in ((10, 10), (60, 10), (60, 40)):
        click(blank, *p)
    click(blank, 10.3, 10.2, Qt.ControlModifier)
    assert count(blank) == 0 and len(layer(blank).session.points) == 4
    layer(blank).session.back()
    click(blank, 10.3, 10.2)
    assert count(blank) == 1 and blank.project.drawing.shapes[0].closed


def test_backspace_takes_back_the_last_point_and_esc_drops_the_whole_line(blank):
    use(blank, "linea")
    click(blank, 10, 10)
    click(blank, 50, 10)
    click(blank, 50, 40)
    QTest.keyClick(blank.canvas, Qt.Key_Backspace)
    assert layer(blank).session.points == [pytest.approx(p, abs=0.2) for p in ((10, 10), (50, 10))]
    QTest.keyClick(blank.canvas, Qt.Key_Backspace)
    QTest.keyClick(blank.canvas, Qt.Key_Backspace)
    assert not layer(blank).busy
    QTest.keyClick(blank.canvas, Qt.Key_Backspace)                    # sin nada que quitar
    assert layer(blank).name == "linea"
    click(blank, 10, 10)
    click(blank, 50, 10)
    blank.escape()
    assert not layer(blank).busy and layer(blank).name == "linea" and count(blank) == 0


def test_shift_keeps_the_line_horizontal_vertical_or_at_45_degrees(blank):
    use(blank, "linea")
    click(blank, 10, 10)
    hover(blank, 60, 13, Qt.ShiftModifier)
    assert layer(blank).spot.y == pytest.approx(layer(blank).session.points[0][1])
    hover(blank, 40, 42, Qt.ShiftModifier)
    sp = layer(blank).spot
    a = layer(blank).session.points[0]
    assert (sp.x - a[0]) == pytest.approx(sp.y - a[1], abs=1e-6)
    assert "45°" in layer(blank).hud.text()


def test_a_polyline_that_crosses_itself_cannot_be_closed(blank):
    use(blank, "linea")
    for p in ((10, 10), (60, 60), (60, 10), (10, 60)):
        click(blank, *p)
    click(blank, 10, 10)
    assert count(blank) == 0 and len(layer(blank).session.points) == 4 and "cruza" in layer(blank).problem


# --- esc y clic derecho --------------------------------------------------------------------------------------------------------
def test_esc_first_cancels_what_is_being_drawn_then_leaves_the_tool_then_clears_the_selection(two):
    two.selection = {0}
    two._selection_changed()
    use(two, "rectangulo")
    click(two, 100, 80)
    assert layer(two).busy
    two.escape()
    assert not layer(two).busy and layer(two).name == "rectangulo" and two.selection == {0}
    two.escape()
    assert layer(two).name == "elegir" and two._tool_actions["elegir"].isChecked() and two.selection == {0}
    two.escape()
    assert two.selection == set()


def test_the_esc_key_in_the_window_does_the_same(two):
    use(two, "circulo")
    click(two, 100, 80)
    QTest.keyClick(two.canvas, Qt.Key_Escape)
    assert not layer(two).busy and layer(two).name == "circulo"
    QTest.keyClick(two.canvas, Qt.Key_Escape)
    assert layer(two).name == "elegir" and two.selection == {0}


def test_esc_on_the_canvas_alone_works_without_the_window_shortcut(qtbot):
    from cnc.gui.canvas import Canvas
    c = Canvas()
    qtbot.addWidget(c)
    assert c.tools.escape() is False
    c.mode = "drawing"
    c.tools.set_tool("rectangulo")
    c.tools.session.add((1, 1))
    QTest.keyClick(c, Qt.Key_Escape)
    assert not c.tools.busy and c.tools.name == "rectangulo"
    QTest.keyClick(c, Qt.Key_Escape)
    assert c.tools.name == "elegir"


def test_right_click_cancels_then_leaves_the_tool(blank):
    use(blank, "ranura")
    click(blank, 10, 10)
    click(blank, 50, 10, button=Qt.RightButton)
    assert not layer(blank).busy and layer(blank).name == "ranura" and count(blank) == 0
    click(blank, 50, 10, button=Qt.RightButton)
    assert layer(blank).name == "elegir"


def test_the_panel_button_that_clears_the_selection_does_not_cancel_the_drawing(two):
    use(two, "rectangulo")
    click(two, 100, 80)
    two.pick_none()
    assert two.selection == set() and layer(two).busy


# --- teclas de herramienta --------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("key,name", [(Qt.Key_R, "rectangulo"), (Qt.Key_C, "circulo"), (Qt.Key_E, "elipse"),
                                      (Qt.Key_P, "poligono"), (Qt.Key_S, "ranura"), (Qt.Key_L, "linea")])
def test_a_letter_on_the_canvas_chooses_the_tool_and_v_goes_back_to_select(blank, key, name):
    QTest.keyClick(blank.canvas, key)
    assert layer(blank).name == name and blank._tool_actions[name].isChecked()
    QTest.keyClick(blank.canvas, Qt.Key_V)
    assert layer(blank).name == "elegir" and blank._tool_actions["elegir"].isChecked()


def test_the_letter_m_needs_a_selection(two):
    two.selection = set()
    two._selection_changed()
    QTest.keyClick(two.canvas, Qt.Key_M)
    assert layer(two).name == "elegir" and "elige primero" in status(two)
    two.selection = {0}
    two._selection_changed()
    QTest.keyClick(two.canvas, Qt.Key_M)
    assert layer(two).name == "mover"


def test_letters_with_ctrl_or_unknown_ones_are_left_alone(blank):
    QTest.keyClick(blank.canvas, Qt.Key_R, Qt.ControlModifier)
    QTest.keyClick(blank.canvas, Qt.Key_X)
    assert layer(blank).name == "elegir"
    QTest.keyClick(blank.canvas, Qt.Key_R, Qt.ShiftModifier)
    assert layer(blank).name == "rectangulo"
    QTest.keyClick(blank.canvas, Qt.Key_R)                              # la misma otra vez no cambia nada
    assert layer(blank).name == "rectangulo"


def test_letters_do_nothing_when_the_drawing_is_not_shown(blank):
    blank.set_view("machine")
    QTest.keyClick(blank.canvas, Qt.Key_R)
    assert layer(blank).name == "elegir"


# --- iman y medidas en vivo ------------------------------------------------------------------------------------------------------
def test_the_cursor_snaps_to_corners_centres_and_middles_and_shows_a_marker(two):
    use(two, "linea")
    hover(two, 60.4, 40.3)
    sp = layer(two).spot
    assert (sp.x, sp.y, sp.kind) == (60, 40, "esquina")
    m = items(two, "marker")
    assert m.isVisible() and (m.pos().x(), m.pos().y()) == (60, 40)
    assert "esquina" in layer(two).hud.text()
    hover(two, 35.5, 25.4)
    assert (layer(two).spot.x, layer(two).spot.y, layer(two).spot.kind) == (35, 25, "centro")
    hover(two, 35.2, 40.4)
    assert layer(two).spot.kind == "medio" and layer(two).spot.xy == (35, 40)
    hover(two, 120.3, 40.2)
    assert layer(two).spot.kind == "cuadrante" and layer(two).spot.xy == pytest.approx((120, 40), abs=1e-3)
    hover(two, 90, 90)
    assert layer(two).spot.kind == "" and not items(two, "marker").isVisible()


def test_ctrl_turns_the_snap_off_and_so_does_the_option(two):
    use(two, "linea")
    hover(two, 60.4, 40.3, Qt.ControlModifier)
    assert layer(two).spot.kind == "" and layer(two).spot.xy == pytest.approx((60.4, 40.3), abs=0.2)
    layer(two).configure(dt.Options(snap=False))
    hover(two, 60.4, 40.3)
    assert layer(two).spot.kind == ""
    layer(two).configure(dt.Options(snap=True))
    hover(two, 60.4, 40.3)
    assert layer(two).spot.kind == "esquina"


def test_the_snap_reach_is_ten_pixels_whatever_the_zoom(two):
    use(two, "linea")
    hover(two, 62, 40)                                                  # 8 px a 4 px/mm: se pega
    assert layer(two).spot.kind == "esquina"
    hover(two, 63.5, 40)                                                # 14 px: libre
    assert layer(two).spot.kind == ""
    two.canvas.setTransform(QTransform.fromScale(1, -1))
    two.canvas.centerOn(100, 60)
    hover(two, 68, 40)                                                  # a 1 px/mm, 8 mm son 8 px: se pega
    assert layer(two).spot.kind == "esquina"
    hover(two, 72, 40)
    assert layer(two).spot.kind == ""


def test_a_new_shape_can_start_and_end_on_snap_points_exactly(two):
    use(two, "rectangulo")
    click(two, 60.3, 40.2)                                              # esquina (60, 40)
    click(two, 119.8, 59.7)                                             # cuadrante... (120, 60)
    assert shape_bounds(two) == pytest.approx((60, 40, 120, 60), abs=1e-3)


def test_the_measures_follow_the_cursor_next_to_the_coordinates(blank):
    layer(blank).configure(dt.Options(grid=5))
    use(blank, "rectangulo")
    hover(blank, 20, 20)
    assert layer(blank).hud.isVisible() and layer(blank).hud.text() == "X 20  Y 20"
    click(blank, 20, 20)
    hover(blank, 70, 50)
    assert layer(blank).hud.text() == "X 70  Y 50\n50 × 30 mm"
    hover(blank, 100, 80)
    assert layer(blank).hud.text() == "X 100  Y 80\n80 × 60 mm"


def test_the_preview_shows_the_shape_that_would_come_out_and_goes_away_with_it(blank):
    layer(blank).configure(dt.Options(grid=5))
    use(blank, "circulo")
    hover(blank, 50, 50)
    assert not items(blank, "preview").isVisible()
    click(blank, 50, 50)
    hover(blank, 60, 50)
    p = items(blank, "preview")
    assert p.isVisible() and p.path().boundingRect().width() == pytest.approx(20, abs=1e-6)
    assert "Ø 20 mm · radio 10" in layer(blank).hud.text()
    click(blank, 60, 50)
    hover(blank, 70, 70)
    assert not items(blank, "preview").isVisible()


def test_the_label_leaves_when_the_mouse_leaves_and_the_tool_is_off_for_select(blank):
    use(blank, "rectangulo")
    hover(blank, 20, 20)
    assert layer(blank).hud.isVisible()
    QApplication.sendEvent(blank.canvas, QEvent(QEvent.Leave))
    assert not layer(blank).hud.isVisible() and layer(blank).spot is None
    assert not items(blank, "marker") or not items(blank, "marker").isVisible()
    use(blank, "elegir")
    hover(blank, 20, 20)
    assert not layer(blank).hud.isVisible()


def test_the_label_stays_inside_the_view_near_the_edges(blank):
    use(blank, "rectangulo")
    vp = blank.canvas.viewport()
    hover(blank, 220, 5)
    h = layer(blank).hud
    assert vp.rect().contains(h.geometry())
    top = blank.canvas.mapToScene(QPoint(5, 8))                      # a 8 px del borde de arriba (el alto de la vista depende de las tiras de encima)
    hover(blank, top.x(), top.y())
    assert vp.rect().contains(h.geometry())
    hover(blank, 50, 50)
    p = at(blank, 50, 50).toPoint()
    assert h.pos().x() == p.x() + 16 and h.pos().y() == p.y() + 18


def test_a_half_made_shape_survives_a_repaint_of_the_drawing(two):
    use(two, "rectangulo")
    click(two, 100, 80)
    two.refresh_canvas(keep=True)
    hover(two, 140, 100)
    assert layer(two).busy and items(two, "preview").isVisible()
    click(two, 140, 100)
    assert count(two) == 3


# --- mover -------------------------------------------------------------------------------------------------------------------------
def test_moving_with_the_mouse_from_a_corner_of_the_selection_to_a_free_point(two):
    layer(two).configure(dt.Options(grid=5))
    use(two, "mover")
    hover(two, 10.2, 10.3)
    assert layer(two).spot.kind in ("esquina", "caja")
    click(two, 10.2, 10.3)                                              # agarra la esquina inferior izquierda
    hover(two, 100.4, 90)
    ghost = items(two, "ghost")
    assert ghost.isVisible() and "ΔX 90 · ΔY 80 mm" in layer(two).hud.text()
    click(two, 100, 90)
    assert shape_bounds(two, 0) == pytest.approx((100, 90, 150, 120))


def test_the_moved_shape_lands_exactly_on_the_snap_point_of_another(two):
    use(two, "mover")
    click(two, 60.1, 40.2)                                              # agarra su esquina superior derecha (60, 40)
    hover(two, 110.3, 49.8)
    assert layer(two).spot.kind == "cuadrante" and "ΔX 50 · ΔY 10 mm" in layer(two).hud.text()
    click(two, 110.3, 49.8)                                             # y la suelta en el lado izquierdo del circulo
    assert shape_bounds(two, 0) == pytest.approx((60, 20, 110, 50), abs=1e-6)


def test_the_selection_does_not_snap_to_itself_while_it_moves(two):
    use(two, "mover")
    click(two, 10.1, 10.1)
    hover(two, 35.2, 25.1)
    assert layer(two).spot.kind == ""                                   # ahi esta su propio centro: no cuenta
    hover(two, 60.2, 40.2)
    assert layer(two).spot.kind == ""                                   # ni su esquina de arriba a la derecha
    hover(two, 119.7, 50.2)
    assert layer(two).spot.kind == "centro"


def test_dragging_moves_in_one_gesture_and_undo_puts_it_back(two):
    use(two, "mover")
    drag(two, (10, 10), (80, 90))
    assert shape_bounds(two, 0) == pytest.approx((80, 90, 130, 120)) and two.selection == {0}
    assert not layer(two).busy and layer(two).name == "mover"
    two.undo()
    assert shape_bounds(two, 0) == pytest.approx((10, 10, 60, 40))


def test_a_move_can_be_repeated_without_choosing_the_tool_again(two):
    use(two, "mover")
    drag(two, (10, 10), (20, 10))
    drag(two, (20, 10), (20, 30))
    assert shape_bounds(two, 0) == pytest.approx((20, 30, 70, 60))


def test_move_moves_everything_chosen_and_only_that(two):
    two.selection = {0, 1}
    two._selection_changed()
    use(two, "mover")
    drag(two, (10, 10), (30, 20))
    assert shape_bounds(two, 0) == pytest.approx((30, 20, 80, 50))
    assert shape_bounds(two, 1) == pytest.approx((130, 50, 150, 70), abs=1e-3)


def test_shift_keeps_the_move_in_line(two):
    use(two, "mover")
    click(two, 10.1, 10.1)
    hover(two, 70, 14, Qt.ShiftModifier)
    assert "ΔY 0 mm" in layer(two).hud.text() and layer(two).spot.y == 10
    click(two, 70, 14, Qt.ShiftModifier)
    assert shape_bounds(two, 0)[1] == pytest.approx(10) and shape_bounds(two, 0)[0] > 60


def test_esc_cancels_a_move_in_progress_and_nothing_changes(two):
    use(two, "mover")
    click(two, 10.1, 10.1)
    hover(two, 70, 70)
    two.escape()
    assert not layer(two).busy and not items(two, "ghost").isVisible()
    assert shape_bounds(two, 0) == pytest.approx((10, 10, 60, 40))
    assert "Mover" not in (two.project.undo_label or "")


def test_a_move_in_progress_is_dropped_if_the_selection_changes(two):
    use(two, "mover")
    click(two, 10.1, 10.1)
    two.pick_all()
    assert not layer(two).busy and layer(two).name == "mover" and layer(two).session.selection == (0, 1)
    assert "ha cambiado" in status(two)


def test_the_move_tool_is_left_when_nothing_is_chosen_any_more(two):
    use(two, "mover")
    two.pick_none()
    assert layer(two).name == "elegir" and two._tool_actions["elegir"].isChecked()
    assert "ya no hay nada elegido" in status(two)


def test_the_selection_box_handles_are_where_to_grab_a_selection(two):
    two.selection = {0, 1}                                              # el rectangulo y el circulo: caja de (10, 10) a (130, 60)
    two._selection_changed()
    use(two, "mover")
    for x, y in ((70, 60), (70, 35), (130, 35), (130, 10), (10, 60), (10, 35)):
        hover(two, x + 0.3, y - 0.2)
        assert layer(two).spot.kind == "caja" and layer(two).spot.xy == (x, y)
    hover(two, 10.2, 10.2)
    assert layer(two).spot.kind == "caja" and layer(two).spot.xy == (10, 10)      # varias formas: la esquina es la de la caja
    click(two, 70.2, 60.2)
    hover(two, 70.2, 60.2)
    assert layer(two).spot.kind == ""                                   # las asas son para agarrar, no para soltar


# --- desplazar la vista --------------------------------------------------------------------------------------------------------------------
def test_the_middle_button_scrolls_the_view_instead_of_drawing(blank):
    use(blank, "rectangulo")
    h = blank.canvas.horizontalScrollBar().value()
    v = blank.canvas.verticalScrollBar().value()
    v_ = blank.canvas.viewport()
    p0, p1 = at(blank, 50, 50), at(blank, 70, 40)
    QApplication.sendEvent(v_, QMouseEvent(QEvent.MouseButtonPress, p0, v_.mapToGlobal(p0), Qt.MiddleButton, Qt.MiddleButton, Qt.NoModifier))
    QApplication.sendEvent(v_, QMouseEvent(QEvent.MouseMove, p1, v_.mapToGlobal(p1), NONE, Qt.MiddleButton, Qt.NoModifier))
    QApplication.sendEvent(v_, QMouseEvent(QEvent.MouseButtonRelease, p1, v_.mapToGlobal(p1), Qt.MiddleButton, NONE, Qt.NoModifier))
    assert blank.canvas.horizontalScrollBar().value() == h - round(p1.x() - p0.x())
    assert blank.canvas.verticalScrollBar().value() == v - round(p1.y() - p0.y())
    assert not layer(blank).busy and count(blank) == 0
    hover(blank, 50, 50)
    assert layer(blank)._pan is None


def test_space_with_the_left_button_scrolls_too(blank):
    use(blank, "rectangulo")
    QTest.keyPress(blank.canvas, Qt.Key_Space)
    assert layer(blank).space
    h = blank.canvas.horizontalScrollBar().value()
    p0 = at(blank, 50, 50)
    v_ = blank.canvas.viewport()
    p1 = QPointF(p0.x() + 30, p0.y())
    QApplication.sendEvent(v_, QMouseEvent(QEvent.MouseButtonPress, p0, v_.mapToGlobal(p0), LEFT, LEFT, Qt.NoModifier))
    QApplication.sendEvent(v_, QMouseEvent(QEvent.MouseMove, p1, v_.mapToGlobal(p1), NONE, LEFT, Qt.NoModifier))
    QApplication.sendEvent(v_, QMouseEvent(QEvent.MouseButtonRelease, p1, v_.mapToGlobal(p1), LEFT, NONE, Qt.NoModifier))
    assert blank.canvas.horizontalScrollBar().value() == h - 30 and not layer(blank).busy and count(blank) == 0
    QTest.keyRelease(blank.canvas, Qt.Key_Space)
    assert not layer(blank).space


def test_space_does_nothing_special_in_select_mode(blank):
    QTest.keyPress(blank.canvas, Qt.Key_Space)
    assert not layer(blank).space


def test_the_cursor_is_an_arrow_in_select_mode_and_a_cross_with_a_tool_and_a_hand_only_while_panning(blank):
    v = blank.canvas.viewport()
    assert v.cursor().shape() == Qt.ArrowCursor
    use(blank, "circulo")
    assert v.cursor().shape() == Qt.CrossCursor
    QTest.keyPress(blank.canvas, Qt.Key_Space)                    # con la barra espaciadora la vista se mueve con el raton
    assert v.cursor().shape() == Qt.OpenHandCursor
    QTest.keyRelease(blank.canvas, Qt.Key_Space)
    assert v.cursor().shape() == Qt.CrossCursor
    use(blank, "elegir")
    assert v.cursor().shape() == Qt.ArrowCursor


def test_in_select_mode_a_click_keeps_the_arrow_and_dragging_the_view_shows_a_hand_until_released(blank):
    v = blank.canvas.viewport()
    hover(blank, 150, 100)
    press(blank, 150, 100)
    assert v.cursor().shape() == Qt.ArrowCursor                   # pulsar no basta: Qt ponia aqui una mano cerrada
    release(blank, 150, 100)
    assert v.cursor().shape() == Qt.ArrowCursor
    press(blank, 150, 100)
    send(blank, QEvent.MouseMove, 120, 80, button=NONE, buttons=LEFT)
    assert v.cursor().shape() == Qt.ClosedHandCursor              # se esta moviendo la vista
    release(blank, 120, 80)
    assert v.cursor().shape() == Qt.ArrowCursor


def test_the_machine_view_also_starts_with_an_arrow(win):
    assert win.canvas.viewport().cursor().shape() == Qt.ArrowCursor


def test_the_viewport_follows_the_mouse_without_buttons(blank):
    assert blank.canvas.viewport().hasMouseTracking() and blank.canvas.hasMouseTracking()


def test_the_wheel_still_zooms_with_a_tool_on(blank):
    use(blank, "rectangulo")
    before = blank.canvas.scale_factor
    blank.canvas.zoom_by(1.25)
    assert blank.canvas.scale_factor == pytest.approx(before * 1.25)


def test_a_double_click_in_select_mode_still_fits_the_view_but_not_with_a_tool(two):
    two.canvas.setTransform(QTransform.fromScale(2, -2))
    two.canvas.mouseDoubleClickEvent(QMouseEvent(QEvent.MouseButtonDblClick, QPointF(10, 10), QPointF(10, 10), LEFT, LEFT, Qt.NoModifier))
    assert two.canvas.scale_factor != 2
    zoom(two)
    use(two, "rectangulo")
    two.canvas.mouseDoubleClickEvent(QMouseEvent(QEvent.MouseButtonDblClick, QPointF(10, 10), QPointF(10, 10), LEFT, LEFT, Qt.NoModifier))
    assert two.canvas.scale_factor == pytest.approx(4)


# --- elegir sigue igual ----------------------------------------------------------------------------------------------------------------------
def test_in_select_mode_a_click_still_selects_and_nothing_is_drawn(two):
    click(two, 120, 60)                                                  # el borde del circulo
    assert two.selection == {1} and count(two) == 2
    assert not layer(two).hud.isVisible()


# --- opciones ------------------------------------------------------------------------------------------------------------------------------------
def test_the_options_dialog_starts_from_the_current_values_and_applies_the_new_ones(blank, monkeypatch):
    seen = answer(monkeypatch, radius=3, sides=8, width=12, grid=5, snap=False, color="#ff0000")
    blank.draw_mouse_options()
    d = seen["dialog"]
    assert layer(blank).options == dt.Options(3, 8, 12, 5, False)
    assert blank.project.draw_color == "#ff0000"
    seen = answer(monkeypatch)
    blank.draw_mouse_options()
    assert seen["dialog"].values() == {"radius": 3, "sides": 8, "width": 12, "grid": 5, "snap": False, "color": "#ff0000"}


def test_new_options_reach_a_tool_that_is_already_running(blank, monkeypatch):
    use(blank, "poligono")
    click(blank, 50, 50)
    answer(monkeypatch, radius=0, sides=3, width=8, grid=0, snap=True, color="#000000")
    blank.draw_mouse_options()
    assert layer(blank).session.options.sides == 3 and layer(blank).busy
    click(blank, 50, 70)
    assert len(blank.project.drawing.shapes[0].shape.exterior.coords) == 4


def test_options_that_make_no_sense_are_refused_and_nothing_changes(blank, monkeypatch):
    errors = []
    monkeypatch.setattr(blank, "_error", lambda title, text: errors.append((title, text)))
    before = layer(blank).options
    answer(monkeypatch, radius=0, sides=2, width=8, grid=0, snap=True)
    blank.draw_mouse_options()
    assert errors and errors[0][0] == "Opciones del ratón" and "3 lados" in errors[0][1]
    assert layer(blank).options == before
    errors.clear()
    answer(monkeypatch, radius=0, sides=6, width=0, grid=0, snap=True)
    blank.draw_mouse_options()
    assert errors and layer(blank).options == before


def test_a_cancelled_options_dialog_changes_nothing(blank, monkeypatch):
    monkeypatch.setattr(ToolDialog, "exec", lambda self: QDialog.Rejected)
    blank.project.draw_color = "#123456"
    layer(blank).options  # noqa
    blank.draw_mouse_options()
    assert layer(blank).options == dt.Options() and blank.project.draw_color == "#123456"


def test_the_grid_rounds_free_points(blank):
    layer(blank).configure(dt.Options(grid=5))
    use(blank, "rectangulo")
    click(blank, 11.4, 21.3)
    click(blank, 58.8, 48.9)
    assert shape_bounds(blank) == pytest.approx((10, 20, 60, 50), abs=1e-9)


# --- mas casos: estado, foco, capas y colores ------------------------------------------------------------------------------------
def px_event(w, kind, px, button=LEFT, buttons=None, mods=Qt.NoModifier):
    v = w.canvas.viewport()
    p = QPointF(*px)
    if buttons is None:
        buttons = NONE if kind == QEvent.MouseButtonRelease or button == NONE else button
    QApplication.sendEvent(v, QMouseEvent(kind, p, v.mapToGlobal(p), button, buttons, mods))


def test_a_movement_of_four_pixels_is_a_drag_and_three_is_still_a_click(blank):
    use(blank, "rectangulo")
    px_event(blank, QEvent.MouseButtonPress, (300, 200))
    px_event(blank, QEvent.MouseButtonRelease, (302, 202))
    assert count(blank) == 1 and not layer(blank).busy                    # 2 + 2 pixeles: un arrastre, y sale un rectangulo
    px_event(blank, QEvent.MouseButtonPress, (500, 300))
    px_event(blank, QEvent.MouseButtonRelease, (501, 302))
    assert count(blank) == 1 and layer(blank).busy and len(layer(blank).session.points) == 1      # 1 + 2: un clic


def test_the_drag_gives_two_points_on_a_line_tool(blank):
    use(blank, "linea")
    px_event(blank, QEvent.MouseButtonPress, (300, 200))
    px_event(blank, QEvent.MouseMove, (360, 240), button=NONE, buttons=LEFT)
    px_event(blank, QEvent.MouseButtonRelease, (360, 240))
    assert len(layer(blank).session.points) == 2


def test_the_preview_and_marker_sit_above_the_drawing_and_ignore_the_mouse(two):
    use(two, "mover")
    click(two, 10.2, 10.2)
    hover(two, 60.2, 40.2)
    ghost, preview, marker = items(two, "ghost"), items(two, "preview"), items(two, "marker")
    assert Z_GHOST < Z_PREVIEW < Z_MARKER and Z_GHOST > 2
    assert (ghost.zValue(), preview.zValue(), marker.zValue()) == (Z_GHOST, Z_PREVIEW, Z_MARKER)
    for it in (ghost, preview, marker):
        assert it.acceptedMouseButtons() == NONE
    assert marker.flags() & marker.GraphicsItemFlag.ItemIgnoresTransformations
    assert not preview.flags() & preview.GraphicsItemFlag.ItemIgnoresTransformations
    assert not ghost.flags() & ghost.GraphicsItemFlag.ItemIgnoresTransformations


def test_the_preview_is_dashed_and_tinted_for_a_closed_shape_and_plain_for_a_line(blank):
    use(blank, "rectangulo")
    click(blank, 10, 20)
    hover(blank, 60, 50)
    p = items(blank, "preview")
    assert p.pen().style() == Qt.DashLine and p.pen().color().name() == theme.ACCENT and p.pen().isCosmetic()
    assert p.brush().style() == Qt.SolidPattern and p.brush().color().alpha() == 40
    layer(blank).cancel()
    use(blank, "linea")
    click(blank, 10, 20)
    hover(blank, 60, 50)
    p = items(blank, "preview")
    assert p.brush().style() == Qt.NoBrush and p.pen().style() == Qt.DashLine


def test_the_ghost_of_what_moves_is_a_solid_outline_that_follows_the_offset(two):
    use(two, "mover")
    click(two, 10.2, 10.2)
    hover(two, 40, 30)
    g = items(two, "ghost")
    assert g.pen().style() == Qt.SolidLine and g.pen().color().name() == theme.ACCENT and g.pen().color().alpha() == 170
    assert g.brush().style() == Qt.NoBrush
    assert g.path().boundingRect().getCoords() == pytest.approx((10, 10, 60, 40))
    assert (g.pos().x(), g.pos().y()) == pytest.approx((30, 20), abs=0.3)
    hover(two, 70, 60)
    assert (g.pos().x(), g.pos().y()) == pytest.approx((60, 50), abs=0.3)


def test_the_marker_is_green_and_turns_amber_when_it_would_close_a_contour(two):
    use(two, "linea")
    hover(two, 60.2, 40.2)
    m = items(two, "marker")
    assert m.pen().color().name() == theme.OK and m.pen().width() == 2 and m.pen().isCosmetic()
    for p in ((70, 90), (110, 90), (110, 110)):
        click(two, *p)
    hover(two, 70.2, 90.2)
    assert items(two, "marker").pen().color().name() == theme.WARN
    hover(two, 60.2, 40.2)
    assert items(two, "marker").pen().color().name() == theme.OK


def test_each_kind_of_snap_has_its_own_marker_shape():
    assert marker_path("esquina").elementCount() == 5 and marker_path("inicio").elementCount() == 5
    assert marker_path("centro").elementCount() > 8 and marker_path("caja").elementCount() > 8 and marker_path("cero").elementCount() > 8
    for kind in ("medio", "cuadrante"):
        p = marker_path(kind)
        assert p.elementCount() == 5 and p.boundingRect().getCoords() == (-7, -7, 7, 7)
    assert marker_path("esquina").boundingRect().getCoords() == (-6, -6, 6, 6)
    assert marker_path("esquina") != marker_path("medio")


def test_the_marker_in_a_snap_over_a_shape_matches_the_kind_of_snap(two):
    use(two, "linea")
    hover(two, 60.2, 40.2)
    assert items(two, "marker").path() == marker_path("esquina")
    hover(two, 35.1, 25.1)
    assert items(two, "marker").path() == marker_path("centro")
    hover(two, 35.1, 40.1)
    assert items(two, "marker").path() == marker_path("medio")


def test_events_are_left_to_the_view_when_the_drawing_is_not_shown(blank):
    use(blank, "rectangulo")
    blank.canvas.mode = "machine"
    ev = QMouseEvent(QEvent.MouseButtonPress, QPointF(5, 5), QPointF(5, 5), LEFT, LEFT, Qt.NoModifier)
    t = layer(blank)
    assert not t.active
    assert t.press(ev) is False and t.move(ev) is False and t.release(ev) is False and t.double_click(ev) is False
    for key, text in [(Qt.Key_R, "r"), (Qt.Key_C, "c"), (Qt.Key_V, "v"), (Qt.Key_Space, " "), (Qt.Key_Return, ""),
                      (Qt.Key_Enter, ""), (Qt.Key_Backspace, ""), (Qt.Key_Escape, "")]:
        assert t.key(_key_event(key, text=text)) is False, key
    assert t.key_release(_key_event(Qt.Key_Space, text=" ")) is False
    assert t.session is not None and t.name == "rectangulo" and not t.space


def _key_event(key, mods=Qt.NoModifier, text=""):
    from PySide6.QtGui import QKeyEvent
    return QKeyEvent(QEvent.KeyPress, key, mods, text)


def test_switching_tool_forgets_the_old_problem_the_cursor_and_a_pressed_button(blank):
    use(blank, "rectangulo")
    click(blank, 10, 20)
    press(blank, 10, 20)
    release(blank, 10, 20)
    assert layer(blank).problem
    use(blank, "circulo")
    assert layer(blank).problem == "" and layer(blank).spot is None and not layer(blank).hud.isVisible()
    hover(blank, 30, 30)
    press(blank, 30, 30)                                                 # un boton a medias
    use(blank, "rectangulo")
    release(blank, 90, 90)                                               # el soltar de antes no da ningun punto
    assert not layer(blank).busy and count(blank) == 0


def test_esc_clears_the_problem_and_backspace_too(blank):
    use(blank, "rectangulo")
    click(blank, 10, 20)
    press(blank, 10, 20)
    assert layer(blank).problem
    blank.escape()
    assert layer(blank).problem == "" and status(blank) == dt.Session("rectangulo").hint()
    use(blank, "linea")
    for p in ((10, 10), (60, 60), (60, 10), (10, 60)):
        click(blank, *p)
    click(blank, 10, 10)
    assert layer(blank).problem
    QTest.keyClick(blank.canvas, Qt.Key_Backspace)
    assert layer(blank).problem == "" and "Línea: clic en cada punto" in status(blank)


def test_the_reason_a_point_was_refused_starts_with_a_capital_in_the_status_bar(blank):
    use(blank, "rectangulo")
    click(blank, 10, 20)
    press(blank, 10, 20)
    assert status(blank).startswith("Los dos puntos coinciden")


def test_after_a_first_point_the_status_bar_gives_the_hint_for_the_second(blank):
    use(blank, "circulo")
    assert status(blank) == dt.Session("circulo").hint()
    click(blank, 50, 50)
    assert status(blank) == "Círculo: clic donde acaba el radio. · Esc: cancelar"


def test_selected_indices_that_are_not_in_the_drawing_are_ignored(two):
    t = layer(two)
    t.set_shapes(two.project.shapes(), {0, 1, 99, -1})
    assert t.selected == (0, 1)
    t.set_shapes(two.project.shapes(), {2})
    assert t.selected == ()


def test_the_snap_points_follow_the_shapes_that_are_added_after_the_tool_started(blank):
    use(blank, "rectangulo")
    hover(blank, 60.2, 50.2)
    assert layer(blank).spot.kind == ""
    drag(blank, (10, 20), (60, 50))
    hover(blank, 60.2, 50.2)
    assert (layer(blank).spot.xy, layer(blank).spot.kind) == ((60, 50), "esquina")


def test_a_multiple_selection_is_grabbed_by_its_circumscribed_box_and_not_by_the_corners_of_its_shapes(two, monkeypatch):
    answer(monkeypatch, x=0, y=20, w=8, h=10, r=0)                      # la mas a la izquierda, pero no la mas baja
    two.draw_rectangle()
    two.selection = {0, 1, 2}                                           # la caja va de (0, 10) a (130, 60)
    two._selection_changed()
    zoom(two)
    use(two, "mover")
    hover(two, 0.2, 10.2)
    assert layer(two).spot.kind == "caja" and layer(two).spot.xy == (0, 10)
    hover(two, 0.2, 20.2)                                               # la esquina de la forma de mas a la izquierda no se ofrece
    assert layer(two).spot.kind == "" and layer(two).spot.xy != (0, 20)
    hover(two, 130.2, 60.2)
    assert layer(two).spot.kind == "caja" and layer(two).spot.xy == (130, 60)
    click(two, 0.2, 10.2)                                               # agarrada por la esquina inferior izquierda de la caja
    hover(two, 50, 90)
    assert "ΔX 50 · ΔY 80 mm" in layer(two).hud.text()


def test_one_shape_keeps_its_own_corners_when_it_is_grabbed(two):
    two.selection = {0}
    two._selection_changed()
    use(two, "mover")
    hover(two, 10.2, 40.2)
    assert layer(two).spot.kind == "esquina" and layer(two).spot.xy == (10, 40)


def test_the_handles_of_the_selection_follow_the_selection(two):
    two.selection = {1}
    two._selection_changed()
    use(two, "mover")
    hover(two, 110.2, 60.2)
    assert layer(two).spot.kind == "caja" and layer(two).spot.xy == (110, 60)
    two.escape()                                                         # deja Mover
    two.selection = {0}
    two._selection_changed()
    use(two, "mover")
    hover(two, 110.2, 60.2)
    assert layer(two).spot.kind == ""


def test_pressing_in_the_canvas_gives_it_the_keyboard_focus(blank, monkeypatch):
    use(blank, "rectangulo")
    asked = []
    monkeypatch.setattr(blank.canvas, "setFocus", lambda *a: asked.append(a))
    press(blank, 10, 20)
    assert asked
    assert blank.canvas.focusPolicy() == Qt.StrongFocus


def test_the_same_letter_again_does_not_throw_away_what_is_half_drawn(blank):
    QTest.keyClick(blank.canvas, Qt.Key_R)
    click(blank, 10, 20)
    QTest.keyClick(blank.canvas, Qt.Key_R)
    assert layer(blank).busy
    QTest.keyClick(blank.canvas, Qt.Key_C)
    assert layer(blank).name == "circulo" and not layer(blank).busy


def test_the_alt_and_meta_modifiers_also_leave_letters_alone(blank):
    QTest.keyClick(blank.canvas, Qt.Key_R, Qt.AltModifier)
    QTest.keyClick(blank.canvas, Qt.Key_R, Qt.MetaModifier)
    assert layer(blank).name == "elegir"


def test_the_tool_row_options_button_opens_the_options(blank, monkeypatch):
    seen = answer(monkeypatch)
    button = next(b for b in blank.findChildren(QToolButton) if b.text() == "Opciones…")
    button.click()
    assert "dialog" in seen and seen["dialog"].windowTitle() == "Opciones del ratón"


def test_the_options_numbers_cannot_be_negative_and_come_in_the_right_order(blank, monkeypatch):
    def fake_exec(self):
        assert list(self.widgets) == ["radius", "sides", "width", "grid", "snap", "color"]
        for key in ("radius", "sides", "width", "grid"):
            self.widgets[key].setText("")
            QTest.keyClicks(self.widgets[key], "-3")                    # el signo menos no se admite
            assert self.values()[key] == 3, key
        self.widgets["sides"].setText("5.5")
        self.widgets["sides"].valueChanged.emit()
        assert not self.buttons.button(QDialogButtonBox.Ok).isEnabled()
        self.set_value("sides", 5)
        self.widgets["sides"].valueChanged.emit()
        self.set_value("radius", 1)
        self.set_value("width", 2)
        self.set_value("grid", 3)
        return QDialog.Accepted

    monkeypatch.setattr(ToolDialog, "exec", fake_exec)
    blank.draw_mouse_options()
    assert layer(blank).options == dt.Options(1, 5, 2, 3, True)


def test_the_machine_view_and_messages_drop_a_half_made_shape(blank):
    use(blank, "linea")
    click(blank, 10, 10)
    blank.canvas.show_message("nada")
    assert layer(blank).name == "elegir" and not layer(blank).busy
    blank.canvas.mode = "drawing"
    use(blank, "linea")
    click(blank, 10, 10)
    plan = type("P", (), {"stock": [], "shapes": [], "virtual_stock": False})()
    machine = type("M", (), {"travel": (651, 1011)})()
    blank.canvas.show_machine(plan, [], machine, 4.0)
    assert layer(blank).name == "elegir" and not layer(blank).busy


def test_shift_on_a_line_with_several_points_is_relative_to_the_last_one(blank):
    use(blank, "linea")
    click(blank, 10, 10)
    click(blank, 60, 10)
    hover(blank, 62, 45, Qt.ShiftModifier)
    assert layer(blank).spot.x == layer(blank).session.points[-1][0]
    assert layer(blank).spot.y == pytest.approx(45, abs=0.5)


def test_right_click_with_one_point_cancels_and_with_two_ends_the_line(blank):
    use(blank, "linea")
    click(blank, 10, 10)
    click(blank, 50, 10, button=Qt.RightButton)
    assert not layer(blank).busy and count(blank) == 0 and layer(blank).name == "linea"
    click(blank, 10, 10)
    click(blank, 50, 10)
    click(blank, 90, 10, button=Qt.RightButton)
    assert count(blank) == 1 and blank.project.drawing.shapes[0].kind == "line"


def test_a_stray_release_after_a_click_does_nothing(blank):
    use(blank, "rectangulo")
    click(blank, 10, 20)
    release(blank, 100, 100)
    assert layer(blank).session.points == [pytest.approx((10, 20), abs=0.2)] and count(blank) == 0


def test_the_middle_button_keeps_scrolling_with_every_move_until_it_is_released(blank):
    use(blank, "rectangulo")
    v_ = blank.canvas.viewport()
    h0 = blank.canvas.horizontalScrollBar().value()
    v0 = blank.canvas.verticalScrollBar().value()
    pts = [QPointF(500, 300), QPointF(510, 305), QPointF(530, 295)]
    steps = [(QEvent.MouseButtonPress, pts[0], Qt.MiddleButton, Qt.MiddleButton),
             (QEvent.MouseMove, pts[1], NONE, Qt.MiddleButton), (QEvent.MouseMove, pts[2], NONE, Qt.MiddleButton),
             (QEvent.MouseButtonRelease, pts[2], Qt.MiddleButton, NONE)]
    for kind, p, b, bs in steps:
        QApplication.sendEvent(v_, QMouseEvent(kind, p, v_.mapToGlobal(p), b, bs, Qt.NoModifier))
    assert blank.canvas.horizontalScrollBar().value() == h0 - 30 and blank.canvas.verticalScrollBar().value() == v0 + 5
    p = QPointF(600, 350)
    QApplication.sendEvent(v_, QMouseEvent(QEvent.MouseMove, p, v_.mapToGlobal(p), NONE, NONE, Qt.NoModifier))
    assert blank.canvas.horizontalScrollBar().value() == h0 - 30


def test_the_label_is_placed_by_a_press_even_without_a_move_before_it(blank):
    use(blank, "rectangulo")
    press(blank, 30, 40)
    p = at(blank, 30, 40).toPoint()
    assert layer(blank).hud.isVisible()
    assert (layer(blank).hud.pos().x(), layer(blank).hud.pos().y()) == (p.x() + HUD_OFFSET.x(), p.y() + HUD_OFFSET.y())


def test_line_widths_of_the_preview_and_the_ghost(two):
    use(two, "mover")
    click(two, 10.2, 10.2)
    hover(two, 40, 30)
    assert items(two, "preview").pen().widthF() == 1.6 and items(two, "ghost").pen().widthF() == 1.4


# --- cosas que las mutaciones encontraron sin cubrir -------------------------------------------------------------
def test_the_start_marker_of_a_contour_is_a_square_like_a_corner_and_the_origin_a_circle():
    assert marker_path("inicio") == marker_path("esquina")
    assert marker_path("inicio").boundingRect().getCoords() == (-6, -6, 6, 6)
    for kind in ("centro", "caja", "cero"):
        assert marker_path(kind).boundingRect().getCoords() == (-6, -6, 6, 6)
        assert marker_path(kind) != marker_path("esquina") and marker_path(kind) != marker_path("medio")


def test_a_move_in_progress_survives_a_repaint_that_leaves_the_selection_alone(two):
    use(two, "mover")
    click(two, 10.1, 10.1)
    assert layer(two).busy
    two.refresh_canvas(keep=True)
    assert layer(two).busy
    two.refresh_canvas()                                                    # este ajusta la vista: se vuelve al zoom de la prueba
    zoom(two)
    assert layer(two).busy and layer(two).session.selection == (0,)
    hover(two, 70, 70)
    assert items(two, "ghost").isVisible()
    click(two, 70, 70)
    assert shape_bounds(two, 0) == pytest.approx((70, 70, 120, 100), abs=0.2)


def test_resetting_while_already_on_select_announces_no_change(two, qtbot):
    assert layer(two).name == "elegir"
    with qtbot.assertNotEmitted(layer(two).changed):
        layer(two).reset()
    use(two, "circulo")
    with qtbot.waitSignal(layer(two).changed) as got:
        layer(two).reset()
    assert got.args == ["elegir"] and layer(two).name == "elegir"


def test_space_only_makes_the_left_button_scroll_the_right_one_still_cancels(blank):
    use(blank, "rectangulo")
    click(blank, 10, 20)
    assert layer(blank).busy
    QTest.keyPress(blank.canvas, Qt.Key_Space)
    press(blank, 30, 40, button=Qt.RightButton)
    assert layer(blank)._pan is None and not layer(blank).busy
    release(blank, 30, 40, button=Qt.RightButton)
    QTest.keyRelease(blank.canvas, Qt.Key_Space)
    assert count(blank) == 0


@pytest.mark.parametrize("mods", [Qt.ControlModifier, Qt.AltModifier, Qt.MetaModifier, Qt.ControlModifier | Qt.AltModifier])
def test_a_letter_with_ctrl_alt_or_meta_is_a_shortcut_of_something_else_and_is_left_alone(blank, mods):
    assert layer(blank).key(_key_event(Qt.Key_R, mods, "r")) is False
    assert layer(blank).name == "elegir"


@pytest.mark.parametrize("mods", [Qt.NoModifier, Qt.ShiftModifier])
def test_a_capital_letter_chooses_the_tool_like_the_small_one(blank, mods):
    assert layer(blank).key(_key_event(Qt.Key_C, mods, "C")) is True              # con Bloq Mayus o con Mayus pulsada
    assert layer(blank).name == "circulo"
    assert layer(blank).key(_key_event(Qt.Key_E, mods, "e")) is True and layer(blank).name == "elipse"


def test_releasing_space_in_the_middle_of_a_scroll_ends_the_scroll(blank):
    use(blank, "rectangulo")
    QTest.keyPress(blank.canvas, Qt.Key_Space)
    press(blank, 50, 50)
    assert layer(blank)._pan is not None
    QTest.keyRelease(blank.canvas, Qt.Key_Space)
    assert layer(blank)._pan is None and not layer(blank).space
    h, v = blank.canvas.horizontalScrollBar().value(), blank.canvas.verticalScrollBar().value()
    send(blank, QEvent.MouseMove, 60, 60, NONE, LEFT)                     # el boton sigue pulsado, pero ya no desplaza
    assert (blank.canvas.horizontalScrollBar().value(), blank.canvas.verticalScrollBar().value()) == (h, v)


def test_leaving_the_view_or_the_tool_hides_the_preview_the_marker_and_the_label(two):
    use(two, "rectangulo")
    click(two, 100, 80)
    hover(two, 60.2, 40.2)
    for key in ("preview", "marker"):
        assert items(two, key).isVisible(), key
    assert layer(two).hud.isVisible()
    layer(two).leave()
    for key in ("preview", "marker"):
        assert not items(two, key).isVisible(), key
    assert not layer(two).hud.isVisible()
    hover(two, 60.2, 40.2)
    assert items(two, "preview").isVisible()
    use(two, "elegir")
    for key in ("preview", "marker"):
        assert not items(two, key).isVisible(), key
    assert not layer(two).hud.isVisible()


def test_a_label_bigger_than_the_view_is_kept_at_the_top_left_corner_not_pushed_outside(blank):
    use(blank, "rectangulo")
    hover(blank, 200, 100)                                                 # abajo a la derecha: la etiqueta se da la vuelta
    layer(blank)._refuse("muy largo " * 200 + "\n".join(["y alto"] * 60))
    h = layer(blank).hud
    vp = blank.canvas.viewport()
    assert h.width() > vp.width() and h.height() > vp.height()
    assert (h.pos().x(), h.pos().y()) == (0, 0)


def test_the_scene_always_reaches_everything_that_is_visible_even_when_the_drawing_is_tiny(blank):
    c = blank.canvas
    vp = c.viewport()
    centre = QPointF(300, 200)
    c._set_scene_rect(QRectF(0, 0, 10, 10), (QTransform.fromScale(0.5, -0.5), centre))      # 0,5 px/mm: se ve mucho mas que el dibujo
    r = c.sceneRect()
    half_w, half_h = vp.width() / 0.5 / 2, vp.height() / 0.5 / 2
    assert r.left() <= 300 - half_w + 1e-6 and r.right() >= 300 + half_w - 1e-6
    assert r.top() <= 200 - half_h + 1e-6 and r.bottom() >= 200 + half_h - 1e-6
    assert r.contains(QRectF(-100, -100, 210, 210))                                          # y el dibujo con su margen de siempre
    c._set_scene_rect(QRectF(0, 0, 10, 10), None)
    assert c.sceneRect() == QRectF(-100, -100, 210, 210)
