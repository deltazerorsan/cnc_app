"""La tira de medidas de lo elegido (ver y escribir X, Y, ancho y alto; centro y diametro de un circulo) y el sentido de
la rueda del raton, con la ventana sin pantalla."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import math

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QTransform, QWheelEvent
from PySide6.QtTest import QTest

from cnc import drawing_edit as de
from cnc.gui.canvas import WHEEL_STEP, wheel_factor
from cnc.gui.main_window import MainWindow
from cnc.gui.props_bar import NO_SELECTION, fmt_mm
from cnc.model import Tool
from cnc.project import Project

K = "#000000"
RECT = (10, 10, 50, 30)
CIRCLE = (120, 50, 10)


def tools():
    return {"em4": Tool(diameter=4, name="End mill 4 mm", feed_xy=300, feed_z=200, stepdown=6.5, stepover=0.6)}


def make(qtbot, settings_file=None):
    w = MainWindow(Project(tools()), [], use_settings=False, settings_file=settings_file)
    qtbot.addWidget(w)
    w.resize(1400, 880)
    w.show()
    return w


@pytest.fixture
def win(qtbot):
    return make(qtbot)


@pytest.fixture
def two(win):
    """Un dibujo nuevo con un rectangulo de esquinas redondas (10, 10, 50 x 30, radio 5) y un circulo de 20 en (120, 50)."""
    win.new_drawing()
    win.apply_edit(de.add_edit([de.make_rectangle(*RECT, 5, stroke=K), de.make_circle(*CIRCLE, stroke=K)]))
    win.pick_none()
    return win


def odd_circle(cx=50.0, cy=50.0, r=10.0, n=30):
    """Un circulo como los de un SVG: 30 puntos, su caja no es exactamente cuadrada."""
    from shapely.geometry import Polygon
    from cnc.svg_import import ImportedShape
    pts = [(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n)) for k in range(n)]
    return ImportedShape(Polygon(pts), True, K, None, kind="circle")


def pick(w, *indices):
    w.selection = set(indices)
    w._selection_changed()


def texts(w):
    return [e.text() for e in w.props.edits]


def labels(w):
    return [n.text() for n in w.props.names]


def type_into(edit, text, key=Qt.Key_Return):
    edit.setFocus()
    edit.selectAll()
    QTest.keyClicks(edit, text)
    if key is not None:
        QTest.keyClick(edit, key)


def bounds(w, i):
    return w.project.drawing.shapes[i].shape.bounds


# --- que se ve -----------------------------------------------------------------------------------------------------
def test_without_a_selection_the_bar_is_off_and_says_how_to_choose(two):
    p = two.props
    assert p.kind_label.text() == "Sin forma" and p.info.text() == NO_SELECTION
    assert not any(e.isEnabled() for e in p.edits) and texts(two) == ["", "", "", ""]


def test_a_rectangle_shows_its_corner_its_size_and_its_centre(two):
    pick(two, 0)
    p = two.props
    assert p.kind_label.text() == "Rectángulo" and labels(two) == ["X", "Y", "Ancho", "Alto"]
    assert texts(two) == ["10", "10", "50", "30"] and all(e.isEnabled() for e in p.edits)
    assert p.info.text() == "Centro: 35, 25"


def test_a_circle_shows_its_centre_its_diameter_and_its_radius(two):
    pick(two, 1)
    p = two.props
    assert p.kind_label.text() == "Círculo" and labels(two) == ["X", "Y", "Diámetro", "Radio"]
    assert texts(two) == ["120", "50", "20", "10"] and p.info.text() == "Esquina: 110, 40"
    assert p.picker.anchor() == (0.5, 0.5) and "el centro" in p.edits[0].toolTip()          # (X e Y son las del centro)


def test_several_figures_are_one_box(two):
    pick(two, 0, 1)
    assert two.props.kind_label.text() == "2 formas" and labels(two) == ["X", "Y", "Ancho", "Alto"]
    assert texts(two) == ["10", "10", "120", "50"]


def test_choosing_with_the_mouse_fills_the_bar(two):
    two.canvas.shapeClicked.emit(1)
    assert two.props.kind_label.text() == "Círculo" and texts(two)[0] == "120"
    two.canvas.shapesSelected.emit([], "replace")
    assert two.props.kind_label.text() == "Sin forma"


def test_numbers_are_shown_with_up_to_three_decimals_and_no_spare_zeros():
    assert [fmt_mm(v) for v in (4, 4.5, 4.125, 4.1259, 0.0004, -0.0004, 1011.0, -12.5, 100.000)] == \
        ["4", "4.5", "4.125", "4.126", "0", "0", "1011", "-12.5", "100"]


# --- escribir ------------------------------------------------------------------------------------------------------------
def test_writing_the_corner_moves_the_figure_and_can_be_undone(two):
    pick(two, 0)
    type_into(two.props.edits[0], "4")
    type_into(two.props.edits[1], "4")
    assert bounds(two, 0) == pytest.approx((4, 4, 54, 34))
    assert texts(two) == ["4", "4", "50", "30"] and two.selection == {0}
    assert two.project.undo_label == "Cambiar medidas"
    two.undo()
    assert bounds(two, 0) == pytest.approx((4, 10, 54, 40))
    two.undo()
    assert bounds(two, 0) == pytest.approx((10, 10, 60, 40))


def test_the_example_of_roman_corner_at_4_4_width_100_height_250(two):
    pick(two, 0)
    for edit, value in zip(two.props.edits, ("4", "4", "100", "250")):
        type_into(edit, value)
    s = two.project.drawing.shapes[0]
    assert s.shape.bounds == pytest.approx((4, 4, 104, 254)) and s.kind == "rect"
    assert texts(two) == ["4", "4", "100", "250"]
    assert two.props.info.text() == "Centro: 54, 129"


def test_the_corner_radius_of_a_rounded_rectangle_survives_resizing_from_the_bar(two):
    pick(two, 0)
    type_into(two.props.edits[2], "100")
    s = two.project.drawing.shapes[0]
    assert s.shape.bounds == pytest.approx((10, 10, 110, 40))
    assert de._rect_corner(s.shape) == pytest.approx(5, abs=1e-6)


def test_a_decimal_comma_is_understood_and_the_text_is_tidied(two):
    pick(two, 0)
    type_into(two.props.edits[0], "12,5")
    assert bounds(two, 0)[0] == pytest.approx(12.5) and texts(two)[0] == "12.5"


def test_negative_positions_can_be_written(two):
    pick(two, 0)
    type_into(two.props.edits[0], "-20")
    assert bounds(two, 0)[0] == pytest.approx(-20) and texts(two)[0] == "-20"


def test_writing_the_centre_of_a_circle_moves_it_without_changing_its_size(two):
    pick(two, 1)
    before = two.project.drawing.shapes[1].shape
    type_into(two.props.edits[0], "200")
    type_into(two.props.edits[1], "80")
    s = two.project.drawing.shapes[1]
    assert s.kind == "circle" and texts(two) == ["200", "80", "20", "10"]
    assert s.shape.equals_exact(__import__("shapely").affinity.translate(before, 80, 30), 1e-9)


def test_the_diameter_of_a_circle_changes_around_its_centre_and_stays_a_circle(two):
    pick(two, 1)
    type_into(two.props.edits[2], "50")
    s = two.project.drawing.shapes[1]
    assert s.kind == "circle" and texts(two) == ["120", "50", "50", "25"]
    assert s.shape.bounds == pytest.approx((95, 25, 145, 75))
    assert max(abs(math.hypot(x - 120, y - 50) - 25) for x, y in s.shape.exterior.coords) < 1e-6


def test_the_radius_of_a_circle_works_the_same_as_the_diameter(two):
    pick(two, 1)
    type_into(two.props.edits[3], "4,5")
    assert two.project.drawing.shapes[1].shape.bounds == pytest.approx((115.5, 45.5, 124.5, 54.5))
    assert texts(two) == ["120", "50", "9", "4.5"]


def test_the_centre_and_the_diameter_can_be_written_one_after_the_other(two):
    pick(two, 1)
    type_into(two.props.edits[2], "6")
    type_into(two.props.edits[0], "30")
    type_into(two.props.edits[1], "30")
    assert two.project.drawing.shapes[1].shape.bounds == pytest.approx((27, 27, 33, 33))


def test_the_centre_of_a_circle_is_not_rounded_when_only_its_diameter_is_written(two):
    two.project.edit(de.measures_edit([1], 100.12345, 20.54321, 20, 20))
    pick(two, 1)
    assert texts(two)[:2] == ["110.123", "30.543"]
    type_into(two.props.edits[2], "30")
    m = de.measures_of(two.project.drawing.shapes, [1])
    assert (m.cx, m.cy, m.width) == pytest.approx((110.12345, 30.54321, 30), abs=1e-9)


def test_moving_an_odd_circle_does_not_redraw_it(two):
    odd = odd_circle()
    two.apply_edit(de.add_edit([odd]))
    pick(two, 2)
    before = two.project.drawing.shapes[2].shape
    type_into(two.props.edits[0], "200")
    after = two.project.drawing.shapes[2].shape
    assert len(after.exterior.coords) == len(before.exterior.coords)
    assert after.equals_exact(__import__("shapely").affinity.translate(before, 200 - 50, 0), 1e-9)


def test_several_figures_are_resized_together_from_their_common_corner(two):
    pick(two, 0, 1)
    type_into(two.props.edits[2], "240")                                  # el doble de ancho: de 120 a 240
    assert bounds(two, 0) == pytest.approx((10, 10, 110, 40))
    assert bounds(two, 1)[0] == pytest.approx(10 + (110 - 10) * 2)
    assert two.selection == {0, 1}


def test_only_the_edited_field_changes_the_figure_not_the_rounding_of_the_others(two):
    two.project.edit(de.measures_edit([0], 10.12345, 10.54321, 50.33333, 30.77777))
    pick(two, 0)
    before = two.project.drawing.shapes[0].shape
    type_into(two.props.edits[1], "20")                                    # solo Y: lo demas se manda sin redondear
    after = two.project.drawing.shapes[0].shape
    assert after.equals_exact(__import__("shapely").affinity.translate(before, 0, 20 - 10.54321), 1e-9)


# --- lo que no cambia nada o esta mal escrito ----------------------------------------------------------------------------------------
def test_pressing_enter_without_changing_anything_makes_no_edit_and_no_complaint(two):
    pick(two, 0)
    n, label = len(two.project.edits), two.project.undo_label
    two.statusBar().showMessage("antes")
    QTest.keyClick(two.props.edits[2], Qt.Key_Return)
    type_into(two.props.edits[2], "50")
    type_into(two.props.edits[2], "50,0004")
    assert len(two.project.edits) == n and two.project.undo_label == label and texts(two)[2] == "50"
    assert two.statusBar().currentMessage() == "antes"                     # ni un error: no se ha pedido nada


def test_a_number_that_shows_rounded_is_not_taken_for_a_change_when_left_alone(two):
    two.apply_edit(de.add_edit([de.make_rectangle(0, 100, 2.0025, 20, stroke=K)]))          # se ve 2.002
    pick(two, 2)
    assert texts(two)[2] == "2.002"
    n = len(two.project.edits)
    two.statusBar().showMessage("antes")
    QTest.keyClick(two.props.edits[2], Qt.Key_Return)                                       # Intro sin tocar nada
    type_into(two.props.edits[2], "2,002")                                                  # o escribir lo que se ve
    assert len(two.project.edits) == n and two.statusBar().currentMessage() == "antes"
    type_into(two.props.edits[0], "5")                                                      # y cambiar solo la X
    assert len(two.project.edits) == n + 1 and two.project.drawing.shapes[2].shape.bounds == pytest.approx((5, 100, 7.0025, 120))
    assert "ya tiene" not in two.statusBar().currentMessage()


def test_an_empty_box_is_refused_and_the_old_number_comes_back(two):
    pick(two, 0)
    n = len(two.project.edits)
    edit = two.props.edits[2]
    edit.setFocus()
    edit.clear()
    QTest.keyClick(edit, Qt.Key_Return)
    assert len(two.project.edits) == n and texts(two) == ["10", "10", "50", "30"]
    assert "Falta una medida" in two.statusBar().currentMessage()


@pytest.mark.parametrize("field", [2, 3])
def test_a_width_or_height_of_zero_is_refused_and_the_old_number_comes_back(two, field):
    pick(two, 0)
    n = len(two.project.edits)
    type_into(two.props.edits[field], "0")
    assert len(two.project.edits) == n and texts(two) == ["10", "10", "50", "30"]
    assert two.statusBar().currentMessage() == "El ancho y el alto tienen que ser mayores que cero."          # lo dice la tira


def test_a_diameter_of_zero_is_refused(two):
    pick(two, 1)
    n = len(two.project.edits)
    type_into(two.props.edits[2], "0")
    type_into(two.props.edits[3], "0")
    assert len(two.project.edits) == n and texts(two) == ["120", "50", "20", "10"]
    assert two.statusBar().currentMessage() == "El diámetro y el radio tienen que ser mayores que cero."


def test_a_line_cannot_be_given_a_height_and_the_bar_goes_back_to_its_real_numbers(two):
    two.apply_edit(de.add_edit([de.make_polyline([(0, 0), (30, 0)], stroke=K)]))
    pick(two, 2)
    assert two.props.kind_label.text() == "Línea" and texts(two) == ["0", "0", "30", "0"]
    n = len(two.project.edits)
    type_into(two.props.edits[3], "5")
    assert len(two.project.edits) == n and texts(two) == ["0", "0", "30", "0"]
    assert "no tiene alto" in two.statusBar().currentMessage()
    type_into(two.props.edits[2], "50")                                    # el largo si se puede cambiar
    assert bounds(two, 2) == pytest.approx((0, 0, 50, 0))


def test_a_number_with_more_than_the_allowed_digits_cannot_be_typed(two):
    pick(two, 0)
    type_into(two.props.edits[0], "12345678", key=None)
    assert two.props.edits[0].text() == "123456"


def test_leaving_a_box_with_a_new_number_applies_it_too(two, qtbot):
    pick(two, 0)
    two.activateWindow()
    type_into(two.props.edits[0], "33", key=None)
    two.props.edits[1].setFocus()                                          # Tab o clic en otra casilla
    qtbot.waitUntil(lambda: bounds(two, 0)[0] == pytest.approx(33), timeout=2000)


def test_what_is_being_typed_is_not_overwritten_by_a_refresh(two):
    pick(two, 0)
    two.activateWindow()
    edit = two.props.edits[2]
    edit.setFocus()
    edit.selectAll()
    QTest.keyClicks(edit, "77")
    two._update_props()
    assert edit.text() == "77"


# --- la tira sigue a lo que pasa en el dibujo -----------------------------------------------------------------------------------
def test_the_bar_follows_a_move_done_by_any_other_tool_and_undo_and_redo(two):
    pick(two, 0)
    two.apply_edit(de.move_edit([0], 5, 7))
    assert texts(two)[:2] == ["15", "17"]
    two.undo()
    assert two.props.kind_label.text() == "Sin forma"                       # deshacer quita la eleccion
    pick(two, 0)
    assert texts(two)[:2] == ["10", "10"]
    two.redo()
    pick(two, 0)
    assert texts(two)[:2] == ["15", "17"]


def test_the_machine_view_turns_the_bar_off_and_the_drawing_view_brings_it_back(two):
    pick(two, 0)
    two.set_view("machine")
    p = two.props
    assert p.kind_label.text() == "Sin forma" and not any(e.isEnabled() for e in p.edits)
    assert "vista Dibujo" in p.info.text()
    two.set_view("drawing")
    assert p.kind_label.text() == "Rectángulo" and texts(two) == ["10", "10", "50", "30"]


def test_a_window_with_no_drawing_has_an_empty_quiet_bar(win):
    p = win.props
    assert p.kind_label.text() == "Sin forma" and p.info.text() == "" and not any(e.isEnabled() for e in p.edits)


def test_the_bar_is_part_of_the_window_between_the_tool_row_and_the_drawing(two):
    p = two.props
    assert p.isVisible() and p.height() > 0 and p.width() > 400
    assert p.mapTo(two, QPoint(0, 0)).y() < two.canvas.mapTo(two, QPoint(0, 0)).y()


def test_the_figure_that_was_chosen_stays_chosen_after_writing_a_number(two):
    pick(two, 1)
    type_into(two.props.edits[0], "150")
    assert two.selection == {1} and two.props.kind_label.text() == "Círculo"


# --- la rueda del raton -----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("delta,inverted,expected", [
    (120, True, 1 / WHEEL_STEP), (-120, True, WHEEL_STEP), (120, False, WHEEL_STEP), (-120, False, 1 / WHEEL_STEP),
    (0, True, 1.0), (0, False, 1.0), (15, True, 1 / WHEEL_STEP), (-15, True, WHEEL_STEP)])
def test_the_wheel_factor_follows_the_direction(delta, inverted, expected):
    assert wheel_factor(delta, inverted) == pytest.approx(expected)


def test_a_bare_canvas_is_inverted_too():
    from cnc.gui.canvas import Canvas
    assert Canvas().wheel_inverted is True


def test_the_wheel_is_inverted_by_default():
    assert wheel_factor(120) == pytest.approx(1 / WHEEL_STEP) and wheel_factor(-120) == pytest.approx(WHEEL_STEP)


def wheel(w, delta, horizontal=False):
    c = w.canvas
    angle = QPoint(delta, 0) if horizontal else QPoint(0, delta)
    pos = QPointF(c.viewport().width() / 2, c.viewport().height() / 2)
    event = QWheelEvent(pos, c.viewport().mapToGlobal(pos), QPoint(), angle, Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase,
                        False)
    c.wheelEvent(event)


def test_turning_the_wheel_forward_zooms_out_and_backward_zooms_in(two):
    c = two.canvas
    c.setTransform(QTransform.fromScale(4, -4))
    wheel(two, 120)
    assert c.scale_factor == pytest.approx(4 / WHEEL_STEP)
    wheel(two, -120)
    wheel(two, -120)
    assert c.scale_factor == pytest.approx(4 * WHEEL_STEP)


def test_a_sideways_wheel_does_not_zoom(two):
    c = two.canvas
    c.setTransform(QTransform.fromScale(4, -4))
    wheel(two, 120, horizontal=True)
    wheel(two, -120, horizontal=True)
    assert c.scale_factor == pytest.approx(4)


def test_the_menu_can_turn_the_inversion_off_and_the_window_remembers_it(qtbot, tmp_path):
    ini = str(tmp_path / "ajustes.ini")
    w = make(qtbot, settings_file=ini)
    assert w.act_wheel.isChecked() and w.canvas.wheel_inverted
    w.new_drawing()
    w.canvas.setTransform(QTransform.fromScale(4, -4))
    w.act_wheel.setChecked(False)
    assert not w.canvas.wheel_inverted
    wheel(w, 120)
    assert w.canvas.scale_factor == pytest.approx(4 * WHEEL_STEP)
    again = make(qtbot, settings_file=ini)
    assert not again.act_wheel.isChecked() and not again.canvas.wheel_inverted
    again.act_wheel.setChecked(True)
    third = make(qtbot, settings_file=ini)
    assert third.act_wheel.isChecked() and third.canvas.wheel_inverted


def test_the_wheel_option_is_in_the_view_menu(win):
    menu = next(a.menu() for a in win.menuBar().actions() if a.text().replace("&", "") == "Ver")
    assert win.act_wheel in menu.actions()


# --- el selector de punto de referencia --------------------------------------------------------------------------------------
ALL_POINTS = [(fx, fy) for fy in (0.0, 0.5, 1.0) for fx in (0.0, 0.5, 1.0)]


def click_anchor(w, fx, fy):
    """Pulsa en el selector el punto (fx, fy): (0, 0) abajo a la izquierda, (1, 1) arriba a la derecha."""
    picker = w.props.picker
    col, row = int(fx * 2), int(fy * 2)
    QTest.mouseClick(picker, Qt.LeftButton, pos=QPoint(int((col + 0.5) * picker.width() / 3), int((2 - row + 0.5) * picker.height() / 3)))


def test_the_picker_starts_on_the_lower_left_corner_for_a_rectangle_and_on_the_centre_for_a_circle(two):
    pick(two, 0)
    assert two.props.picker.anchor() == (0.0, 0.0) and two.props.anchor() == (0.0, 0.0)
    pick(two, 1)
    assert two.props.picker.anchor() == (0.5, 0.5)
    pick(two, 0, 1)
    assert two.props.picker.anchor() == (0.0, 0.0)                         # varias figuras: una caja, como un rectangulo


def test_a_stretched_circle_is_an_ellipse_and_starts_on_the_corner_like_any_box(two):
    two.apply_edit(de.measures_edit([1], 100, 30, 40, 20), fit=False)
    pick(two, 1)
    assert two.props.kind_label.text() == "Elipse" and two.props.picker.anchor() == (0.0, 0.0)


def test_the_picker_is_off_without_a_figure_and_on_with_one(two):
    assert not two.props.picker.isEnabled()
    pick(two, 0)
    assert two.props.picker.isEnabled()
    pick(two)
    assert not two.props.picker.isEnabled()


@pytest.mark.parametrize("fx, fy", ALL_POINTS)
def test_clicking_a_point_makes_x_and_y_those_of_that_point_of_the_box(two, fx, fy):
    pick(two, 0)                                                            # caja: de (10, 10) a (60, 40)
    click_anchor(two, fx, fy)
    assert two.props.picker.anchor() == (fx, fy)
    assert texts(two) == [fmt_mm(10 + 50 * fx), fmt_mm(10 + 30 * fy), "50", "30"]
    assert bounds(two, 0) == pytest.approx((10, 10, 60, 40))                # mirar no cambia nada
    assert two.project.undo_label == "Dibujar forma"


POINT_WORDS = {(0.0, 0.0): "la esquina inferior izquierda", (0.5, 0.0): "la mitad del lado inferior",
               (1.0, 0.0): "la esquina inferior derecha", (0.0, 0.5): "la mitad del lado izquierdo", (0.5, 0.5): "el centro",
               (1.0, 0.5): "la mitad del lado derecho", (0.0, 1.0): "la esquina superior izquierda",
               (0.5, 1.0): "la mitad del lado superior", (1.0, 1.0): "la esquina superior derecha"}


@pytest.mark.parametrize("fx, fy", ALL_POINTS)
def test_every_point_has_its_own_name_in_the_tool_tips(two, fx, fy):
    pick(two, 0)
    click_anchor(two, fx, fy)
    word = POINT_WORDS[(fx, fy)]
    assert word in two.props.picker.toolTip() and all(word in e.toolTip() for e in two.props.edits[:2])
    others = [w for w in POINT_WORDS.values() if w != word]
    assert not any(w in two.props.picker.toolTip() for w in others)


def test_only_the_left_button_chooses_a_point(two):
    pick(two, 0)
    picker = two.props.picker
    QTest.mouseClick(picker, Qt.RightButton, pos=QPoint(picker.width() - 6, 6))
    QTest.mouseClick(picker, Qt.MiddleButton, pos=QPoint(picker.width() - 6, 6))
    assert picker.anchor() == (0.0, 0.0)


def test_pulling_the_mouse_over_the_picker_does_not_change_the_point(two):
    pick(two, 0)
    picker = two.props.picker
    seen = []
    picker.anchorChanged.connect(lambda fx, fy: seen.append((fx, fy)))
    QTest.mouseMove(picker, QPoint(picker.width() - 6, 6))
    assert picker._hover == (2, 2) and picker.anchor() == (0.0, 0.0)             # se ilumina el punto, pero no se elige
    QTest.mouseMove(picker, QPoint(6, picker.height() - 6))
    assert picker._hover == (0, 0) and picker.anchor() == (0.0, 0.0) and seen == [] and texts(two)[:2] == ["10", "10"]
    picker.leaveEvent(None)
    assert picker._hover is None


def test_the_tool_tips_say_which_point_x_and_y_are_about(two):
    pick(two, 0)
    assert "la esquina inferior izquierda" in two.props.edits[0].toolTip()
    click_anchor(two, 1, 1)
    assert "la esquina superior derecha" in two.props.edits[1].toolTip()
    assert "la esquina superior derecha" in two.props.picker.toolTip()
    click_anchor(two, 0.5, 0)
    assert "la mitad del lado inferior" in two.props.edits[0].toolTip()


def test_the_other_reading_in_the_strip_is_the_centre_unless_the_centre_is_the_chosen_point(two):
    pick(two, 0)
    assert two.props.info.text() == "Centro: 35, 25"
    click_anchor(two, 1, 1)
    assert two.props.info.text() == "Centro: 35, 25"
    click_anchor(two, 0.5, 0.5)
    assert two.props.info.text() == "Esquina: 10, 10"


def test_the_chosen_point_stays_for_the_next_figure(two):
    pick(two, 0)
    click_anchor(two, 1, 1)
    pick(two, 1)
    assert two.props.picker.anchor() == (1.0, 1.0) and texts(two) == ["130", "60", "20", "10"]
    pick(two, 0, 1)
    assert two.props.picker.anchor() == (1.0, 1.0) and texts(two) == ["130", "60", "120", "50"]


def test_after_choosing_a_point_the_circle_does_not_go_back_to_its_centre(two):
    pick(two, 1)
    click_anchor(two, 0, 0)
    pick(two, 0)
    pick(two, 1)
    assert two.props.picker.anchor() == (0.0, 0.0) and texts(two)[:2] == ["110", "40"]


def test_choosing_the_centre_for_a_rectangle(two):
    pick(two, 0)
    click_anchor(two, 0.5, 0.5)
    assert texts(two) == ["35", "25", "50", "30"]


def test_the_chosen_point_stays_on_after_the_bar_is_off_and_on_again(two):
    pick(two, 0)
    click_anchor(two, 0, 1)
    pick(two)
    assert two.props.picker.anchor() == (0.0, 1.0)
    pick(two, 0)
    assert texts(two)[:2] == ["10", "40"]


def test_the_arrow_keys_move_the_point_when_the_picker_has_focus(two, qtbot):
    pick(two, 0)
    picker = two.props.picker
    seen = []
    picker.anchorChanged.connect(lambda fx, fy: seen.append((fx, fy)))
    picker.setFocus()
    QTest.keyClick(picker, Qt.Key_Right)
    QTest.keyClick(picker, Qt.Key_Up)
    assert picker.anchor() == (0.5, 0.5) and seen == [(0.5, 0.0), (0.5, 0.5)]
    QTest.keyClick(picker, Qt.Key_Right)
    QTest.keyClick(picker, Qt.Key_Right)                                    # ya esta al borde: no se mueve ni avisa
    QTest.keyClick(picker, Qt.Key_Up)
    QTest.keyClick(picker, Qt.Key_Up)
    assert picker.anchor() == (1.0, 1.0) and seen[-2:] == [(1.0, 0.5), (1.0, 1.0)] and len(seen) == 4
    QTest.keyClick(picker, Qt.Key_Left)
    QTest.keyClick(picker, Qt.Key_Down)
    assert picker.anchor() == (0.5, 0.5)
    assert texts(two)[:2] == ["35", "25"]


def test_other_keys_and_a_picker_that_is_off_do_nothing(two):
    picker = two.props.picker
    pick(two)
    QTest.keyClick(picker, Qt.Key_Right)
    click_anchor(two, 1, 1)
    assert picker.anchor() == (0.0, 0.0)                                    # apagado: ni teclas ni raton
    pick(two, 0)
    picker.setFocus()
    QTest.keyClick(picker, Qt.Key_A)
    QTest.keyClick(picker, Qt.Key_Return)
    assert picker.anchor() == (0.0, 0.0)


def test_the_picker_is_picked_with_the_right_cells(two):
    """Las nueve celdas son iguales y arriba del selector es arriba de la figura."""
    picker = two.props.picker
    pick(two, 0)
    n = picker.width()
    for fx, fy in ALL_POINTS:
        col, row = int(fx * 2), int(fy * 2)
        for dx in (1, n // 3 - 2):                                          # los dos bordes de la celda
            for dy in (1, n // 3 - 2):
                picker._choose(0, 0)
                QTest.mouseClick(picker, Qt.LeftButton, pos=QPoint(col * n // 3 + dx, (2 - row) * n // 3 + dy))
                assert picker.anchor() == (fx, fy), (fx, fy, dx, dy)


# --- escribir con otro punto de referencia ------------------------------------------------------------------------------------
def test_writing_the_centre_of_a_rectangle_puts_its_centre_there(two):
    pick(two, 0)
    click_anchor(two, 0.5, 0.5)
    type_into(two.props.edits[0], "100")
    type_into(two.props.edits[1], "50")
    assert bounds(two, 0) == pytest.approx((75, 35, 125, 65)) and texts(two) == ["100", "50", "50", "30"]


def test_the_bottom_of_a_circle_can_be_put_on_y_0(two):
    pick(two, 1)
    click_anchor(two, 0.5, 0)
    assert texts(two)[:2] == ["120", "40"]
    type_into(two.props.edits[1], "0")
    s = two.project.drawing.shapes[1]
    assert s.kind == "circle" and s.shape.bounds == pytest.approx((110, 0, 130, 20))
    assert texts(two) == ["120", "0", "20", "10"]


def test_the_side_of_a_circle_can_be_put_against_a_wall(two):
    pick(two, 1)
    click_anchor(two, 1, 0.5)
    assert texts(two)[:2] == ["130", "50"]
    type_into(two.props.edits[0], "300")
    assert two.project.drawing.shapes[1].shape.bounds == pytest.approx((280, 40, 300, 60))


def test_the_top_right_corner_of_a_rectangle_can_be_written(two):
    pick(two, 0)
    click_anchor(two, 1, 1)
    type_into(two.props.edits[0], "104")
    type_into(two.props.edits[1], "254")
    assert bounds(two, 0) == pytest.approx((54, 224, 104, 254))


def test_with_a_chosen_point_the_size_grows_from_that_point_and_not_from_the_corner(two):
    pick(two, 0)                                                            # (10, 10) a (60, 40)
    click_anchor(two, 1, 1)
    type_into(two.props.edits[2], "100")
    assert bounds(two, 0) == pytest.approx((-40, 10, 60, 40))               # el borde derecho se queda en 60
    type_into(two.props.edits[3], "10")
    assert bounds(two, 0) == pytest.approx((-40, 30, 60, 40))               # y el de arriba en 40
    assert texts(two) == ["60", "40", "100", "10"]
    assert de._rect_corner(two.project.drawing.shapes[0].shape) is not None


def test_a_circle_grows_up_from_its_bottom_point(two):
    pick(two, 1)
    click_anchor(two, 0.5, 0)                                               # abajo en el centro: (120, 40)
    type_into(two.props.edits[2], "40")
    s = two.project.drawing.shapes[1]
    assert s.kind == "circle" and s.shape.bounds == pytest.approx((100, 40, 140, 80))
    assert texts(two) == ["120", "40", "40", "20"]


def test_the_radius_works_with_a_chosen_point_too(two):
    pick(two, 1)
    click_anchor(two, 0, 0)
    type_into(two.props.edits[3], "20")
    assert two.project.drawing.shapes[1].shape.bounds == pytest.approx((110, 40, 150, 80))


def test_writing_the_point_and_the_size_together_places_the_point_at_the_number_with_the_new_size(two):
    pick(two, 0)
    click_anchor(two, 0.5, 0.5)
    two.props.edits[0].setText("100")
    two.props.edits[1].setText("50")
    two.props.edits[2].setText("20")
    two.props.edits[3].setText("10")
    two.props.edits[0].editingFinished.emit()
    assert bounds(two, 0) == pytest.approx((90, 45, 110, 55))


def test_what_is_not_touched_is_not_rounded_with_a_chosen_point_either(two):
    two.apply_edit(de.measures_edit([0], 10.12345, 10.54321, 50.5, 30.25), fit=False)
    pick(two, 0)
    click_anchor(two, 0.5, 0.5)
    before = two.project.drawing.shapes[0].shape.wkt
    type_into(two.props.edits[1], "77")                                      # solo la Y del centro: la X y el tamano no se redondean
    x0, y0, x1, y1 = bounds(two, 0)
    assert (x0, x1 - x0, y1 - y0) == pytest.approx((10.12345, 50.5, 30.25), abs=1e-9)
    assert (y0 + y1) / 2 == pytest.approx(77)
    assert before != two.project.drawing.shapes[0].shape.wkt


def test_a_cell_left_as_it_was_changes_nothing_with_a_chosen_point(two):
    pick(two, 0)
    click_anchor(two, 1, 0.5)
    type_into(two.props.edits[0], "60")                                      # (60 es lo que ya vale)
    assert two.project.undo_label == "Dibujar forma" and bounds(two, 0) == pytest.approx((10, 10, 60, 40))


def test_choosing_a_point_with_a_number_half_typed_delivers_the_number_first_with_the_old_point(two):
    pick(two, 0)
    edit = two.props.edits[0]
    edit.setFocus()
    edit.selectAll()
    QTest.keyClicks(edit, "4")
    click_anchor(two, 1, 1)
    assert bounds(two, 0) == pytest.approx((4, 10, 54, 40))                  # la X era la de la esquina de abajo a la izquierda
    assert two.props.picker.anchor() == (1.0, 1.0) and texts(two)[:2] == ["54", "40"]


def test_a_chosen_point_makes_the_undo_of_a_written_number_still_work(two):
    pick(two, 0)
    click_anchor(two, 0.5, 0.5)
    type_into(two.props.edits[0], "100")
    assert two.project.undo_label == "Cambiar medidas"
    two.undo()
    assert bounds(two, 0) == pytest.approx((10, 10, 60, 40))
    pick(two, 0)
    assert two.props.picker.anchor() == (0.5, 0.5) and texts(two)[0] == "35"          # el punto sigue siendo el centro


def test_several_figures_are_measured_by_the_point_of_their_common_box(two):
    pick(two, 0, 1)                                                          # caja de (10, 10) a (130, 60)
    click_anchor(two, 1, 0)
    assert texts(two) == ["130", "10", "120", "50"]
    type_into(two.props.edits[0], "200")
    assert bounds(two, 1) == pytest.approx((180, 40, 200, 60))
    assert bounds(two, 0) == pytest.approx((80, 10, 130, 40))


# --- alinear -----------------------------------------------------------------------------------------------------------------
SIDES = ("izquierda", "centro_x", "derecha", "arriba", "centro_y", "abajo")


def test_there_are_six_align_buttons_in_the_strip_and_the_same_six_in_the_menu(two):
    assert len(two.props.align_buttons) == 6 and len(two._align_actions) == 6
    assert [b.defaultAction() for b in two.props.align_buttons] == two._align_actions
    menu = next(m for m in two.menuBar().findChildren(type(two.menuBar().addMenu("x"))) if m.title() == "Alinear")
    assert menu.actions() == two._align_actions
    assert [a.text() for a in two._align_actions] == [de.ALIGN_LABELS[s] for s in SIDES]
    assert all(not a.icon().isNull() for a in two._align_actions)
    from cnc.gui import align_icons
    assert [a.toolTip() for a in two._align_actions] == [align_icons.TIPS[s] for s in SIDES]
    assert all(a.toolTip().startswith(a.text()) for a in two._align_actions)


def test_the_align_buttons_are_off_until_two_figures_are_chosen(two):
    assert not any(a.isEnabled() for a in two._align_actions)
    pick(two, 0)
    assert not any(a.isEnabled() for a in two._align_actions) and not any(b.isEnabled() for b in two.props.align_buttons)
    pick(two, 0, 1)
    assert all(a.isEnabled() for a in two._align_actions) and all(b.isEnabled() for b in two.props.align_buttons)
    pick(two, 1)
    assert not any(a.isEnabled() for a in two._align_actions)


def test_the_align_buttons_are_off_without_a_drawing(win):
    assert not any(a.isEnabled() for a in win._align_actions)


@pytest.mark.parametrize("side", SIDES)
def test_each_button_aligns_the_chosen_figures_by_its_side(two, side):
    pick(two, 0, 1)
    button = two.props.align_buttons[SIDES.index(side)]
    assert button.defaultAction().text() == de.ALIGN_LABELS[side]
    button.click()
    a, b = bounds(two, 0), bounds(two, 1)
    expect = {"izquierda": (a[0], b[0]), "derecha": (a[2], b[2]), "abajo": (a[1], b[1]), "arriba": (a[3], b[3]),
              "centro_x": ((a[0] + a[2]) / 2, (b[0] + b[2]) / 2), "centro_y": ((a[1] + a[3]) / 2, (b[1] + b[3]) / 2)}[side]
    assert expect[0] == pytest.approx(expect[1])
    assert two.project.undo_label == de.ALIGN_LABELS[side]
    assert two.selection == {0, 1}
    assert two.statusBar().currentMessage().startswith(de.ALIGN_LABELS[side])


def test_aligning_the_bottom_of_a_circle_with_a_rectangle_and_undoing_it(two):
    pick(two, 0, 1)
    two.props.align_buttons[SIDES.index("abajo")].click()
    assert bounds(two, 0) == pytest.approx((10, 10, 60, 40)) and bounds(two, 1) == pytest.approx((110, 10, 130, 30))
    assert texts(two) == ["10", "10", "120", "30"]                           # la tira se pone al dia con la caja nueva
    two.undo()
    assert bounds(two, 1) == pytest.approx((110, 40, 130, 60))
    assert two.act_undo.text() == "Deshacer: Dibujar forma" and two.act_redo.text() == "Rehacer: Alinear abajo"
    two.redo()
    assert bounds(two, 1) == pytest.approx((110, 10, 130, 30)) and two.act_undo.text() == "Deshacer: Alinear abajo"


def test_aligning_does_not_refit_the_view_you_are_looking_at(two, monkeypatch):
    fits = []
    real = two.refresh_all
    monkeypatch.setattr(two, "refresh_all", lambda *a, **k: (fits.append(k.get("fit", False)), real(*a, **k))[1])
    pick(two, 0, 1)
    two.props.align_buttons[SIDES.index("abajo")].click()
    assert fits == [False]


def test_the_menu_action_does_the_same_as_the_button(two):
    pick(two, 0, 1)
    two._align_actions[SIDES.index("izquierda")].trigger()
    assert bounds(two, 1)[0] == pytest.approx(10) and bounds(two, 0)[0] == pytest.approx(10)


def test_aligning_what_is_already_aligned_says_so_and_changes_nothing(two):
    pick(two, 0, 1)
    two.props.align_buttons[SIDES.index("abajo")].click()
    before = [s.shape.wkt for s in two.project.drawing.shapes]
    undo_label = two.project.undo_label
    two.props.align_buttons[SIDES.index("abajo")].click()
    assert [s.shape.wkt for s in two.project.drawing.shapes] == before and two.project.undo_label == undo_label
    assert "ya estan alineadas" in two.statusBar().currentMessage()


def test_draw_align_with_a_single_figure_asks_for_more(two):
    pick(two, 0)
    two.draw_align("izquierda")
    assert "al menos 2" in two.statusBar().currentMessage()
    assert two.project.undo_label == "Dibujar forma"


def test_after_aligning_a_job_saved_and_reopened_keeps_the_alignment(two, tmp_path):
    pick(two, 0, 1)
    two.props.align_buttons[SIDES.index("derecha")].click()
    want = [s.shape.wkt for s in two.project.drawing.shapes]
    data = two.project.to_dict()
    q = Project(tools())
    q.new_drawing()
    q.apply_dict(data)
    assert [s.shape.wkt for s in q.drawing.shapes] == want


def test_the_strip_has_room_for_everything_on_a_laptop_screen(qtbot):
    w = make(qtbot)
    w.resize(1366, 697)
    w.new_drawing()
    w.apply_edit(de.add_edit([de.make_rectangle(*RECT, 5, stroke=K), de.make_circle(*CIRCLE, stroke=K)]))
    pick(w, 0, 1)
    qtbot.wait(30)
    bar = w.props
    assert bar.width() >= bar.minimumSizeHint().width() and w.minimumSizeHint().width() <= 1366 - 100
    for part in (bar.picker, *bar.edits, *bar.align_buttons):
        assert part.isVisible() and 0 <= part.mapTo(bar, QPoint(0, 0)).x() and part.mapTo(bar, QPoint(part.width(), 0)).x() <= bar.width()
    assert bar.info.width() > 60                                              # y aun queda sitio para leer el otro punto
