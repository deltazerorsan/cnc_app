"""Menu Dibujo de la ventana: dialogos con medidas, seleccion, deshacer, cambios sin guardar y trabajos de dibujo nuevo."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import math
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QDialogButtonBox

from cnc import drawing_edit as de
from cnc.gui.canvas import ITEM_KIND
from cnc.gui.draw_dialogs import Check, Choice, Colour, Num, Points, ToolDialog, parse_points
from cnc.gui.main_window import MainWindow
from cnc.model import Tool
from cnc.project import Project

DATA = Path(__file__).parent / "data"
BOARDS19 = DATA / "tableros_19mm.svg"
K = "#000000"


def tools():
    return {"em4": Tool(diameter=4, name="End mill 4 mm", feed_xy=300, feed_z=200, stepdown=6.5, stepover=0.6)}


@pytest.fixture
def win(qtbot):
    w = MainWindow(Project(tools()), [], use_settings=False)
    qtbot.addWidget(w)
    w.show()
    return w


def set_job(w):
    w.job.tool.setCurrentIndex(w.job.tool.findData("em4"))
    w.job.thickness.setText("19")
    w.job.thickness.valueChanged.emit()


@pytest.fixture
def blank(win):
    win.new_drawing()
    set_job(win)
    return win


@pytest.fixture
def job19(win):
    assert win.open_svg(BOARDS19, apply_default_recipe=False)
    set_job(win)
    return win


def answer(monkeypatch, **values):
    """Contesta el siguiente dialogo con estos valores (y lo acepta si el boton Aceptar esta activo)."""
    seen = {}

    def fake_exec(self):
        seen["dialog"] = self
        for key, value in values.items():
            self.set_value(key, value)
        return QDialog.Accepted if self.buttons.button(QDialogButtonBox.Ok).isEnabled() else QDialog.Rejected

    monkeypatch.setattr(ToolDialog, "exec", fake_exec)
    return seen


def refuse_dialogs(monkeypatch):
    def boom(self):
        raise AssertionError("no tenia que abrirse ningun dialogo")

    monkeypatch.setattr(ToolDialog, "exec", boom)


def bounds(w, i=0):
    return tuple(round(v, 6) for v in w.project.drawing.shapes[i].shape.bounds)


def halos(w):
    return [i for i in w.canvas.scene().items() if i.data(ITEM_KIND) == "halo"]


def status(w):
    return w.statusBar().currentMessage()


# --- los dialogos -------------------------------------------------------------------------------------------------------
def test_points_are_read_with_dot_or_comma_decimals_one_per_line():
    assert parse_points("0 0\n50 0\n50 30") == [(0, 0), (50, 0), (50, 30)]
    assert parse_points("0; 0\n10,5; 20,25\n") == [(0, 0), (10.5, 20.25)]
    assert parse_points("1,5 2,5") == [(1.5, 2.5)]
    assert parse_points("") == [] and parse_points("\n  \n") == []
    for bad in ("1", "1 2 3", "a b", "1; 2; 3", "1 ; "):
        assert parse_points(bad) is None


def test_the_ok_button_waits_for_every_number(qtbot):
    d = ToolDialog("Prueba", [Num("a", "Ancho", 5), Num("n", "Cantidad", 2, integer=True), Check("c", "Casilla"),
                              Choice("o", "Opcion", [("x", "X"), ("y", "Y")], "y")])
    qtbot.addWidget(d)
    ok = d.buttons.button(QDialogButtonBox.Ok)
    assert ok.isEnabled() and d.values() == {"a": 5.0, "n": 2, "c": False, "o": "y"} and d.problem.text() == ""
    d.widgets["a"].setText("")
    d._changed("a")
    assert not ok.isEnabled() and "Ancho" in d.problem.text() and d.values()["a"] is None
    d.set_value("a", 7.5)
    assert ok.isEnabled()
    d.widgets["n"].setText("2,5")
    d._changed("n")
    assert not ok.isEnabled() and "entero" in d.problem.text()
    d.set_value("n", 3)
    d.set_value("c", True)
    d.set_value("o", "x")
    assert ok.isEnabled() and d.values() == {"a": 7.5, "n": 3, "c": True, "o": "x"}


def test_numbers_accept_a_decimal_comma_and_negatives_only_when_allowed(qtbot):
    d = ToolDialog("Prueba", [Num("a", "A", 0), Num("b", "B", 1, negative=False)])
    qtbot.addWidget(d)
    d.widgets["a"].setText("")
    QTest.keyClicks(d.widgets["a"], "-12,5")
    d.widgets["b"].setText("")
    QTest.keyClicks(d.widgets["b"], "-3")
    assert d.values()["a"] == -12.5 and d.values()["b"] == 3.0


def test_the_point_list_must_be_filled_and_well_written(qtbot):
    d = ToolDialog("Prueba", [Points("p", "Puntos")])
    qtbot.addWidget(d)
    ok = d.buttons.button(QDialogButtonBox.Ok)
    assert not ok.isEnabled() and "Puntos" in d.problem.text()
    d.set_value("p", "1 2\nxx")
    assert not ok.isEnabled() and "mal escrita" in d.problem.text()
    d.set_value("p", "1 2\n3 4")
    assert ok.isEnabled() and d.values()["p"] == [(1, 2), (3, 4)]


def test_the_colour_choice_lists_the_colours_of_the_drawing_and_the_default_comes_first(qtbot):
    d = ToolDialog("Prueba", [Colour("c", "Color", ["#ff0000", "#00ff00"], "#0000ff")])
    qtbot.addWidget(d)
    w = d.widgets["c"]
    assert [w.itemData(i) for i in range(w.count())][:3] == ["#0000ff", "#ff0000", "#00ff00"] and d.values()["c"] == "#0000ff"
    d.set_value("c", "#00FF00")
    assert d.values()["c"] == "#00ff00" and w.count() == 4
    d.set_value("c", "#123456")                                     # un color nuevo se anade antes de «Otro color»
    assert d.values()["c"] == "#123456" and w.itemData(w.count() - 1) == "__otro__"


def test_a_change_hook_can_rewrite_other_fields_without_looping(qtbot):
    d = ToolDialog("Prueba", [Num("w", "Ancho", 10), Num("h", "Alto", 20), Check("keep", "Proporcion", True)])
    qtbot.addWidget(d)
    calls = []

    def hook(dialog, key):
        calls.append(key)
        if key == "w":
            dialog.set_value("h", dialog.values()["w"] * 2)

    d.hook = hook
    d.set_value("w", 30)                                              # set_value no llama al gancho
    assert calls == [] and d.values()["h"] == 20
    d._changed("w")
    assert calls == ["w"] and d.values()["h"] == 60


def test_enter_does_not_accept_an_incomplete_dialog(qtbot):
    d = ToolDialog("Prueba", [Num("a", "Ancho", None)])
    qtbot.addWidget(d)
    d.show()
    QTest.keyClick(d, Qt.Key_Return)
    assert d.result() == QDialog.Rejected and d.isVisible()
    d.set_value("a", 3)
    QTest.keyClick(d, Qt.Key_Return)
    assert d.result() == QDialog.Accepted


# --- menu y estado de las acciones --------------------------------------------------------------------------------------
def test_the_draw_menu_sits_between_shapes_and_view_with_its_shortcuts(win):
    titles = [a.text() for a in win.menuBar().actions()]
    assert titles.index("&Dibujo") == titles.index("&Formas") + 1 == titles.index("&Ver") - 1
    assert win.act_undo.shortcut() == QKeySequence("Ctrl+Z") and win.act_redo.shortcut() == QKeySequence("Ctrl+Y")
    assert win.act_delete.shortcut() == QKeySequence(QKeySequence.Delete) and win.act_new_drawing.shortcut() == QKeySequence("Ctrl+N")


def test_the_edit_menu_gathers_undo_redo_and_the_selection_functions(win):
    bar = win.menuBar().actions()
    titles = [a.text() for a in bar]
    assert titles[:2] == ["&Archivo", "&Edición"]
    edit = bar[1].menu()
    names = [a.text() for a in edit.actions() if not a.isSeparator()]
    assert names == ["Deshacer", "Rehacer", "Elegir todos", "Elegir el tablero actual (con su recuadro)", "Quitar la elección",
                     "Borrar lo elegido"]
    shortcuts = {a.text(): a.shortcut() for a in edit.actions()}
    assert shortcuts["Elegir todos"] == QKeySequence("Ctrl+A") and shortcuts["Quitar la elección"] == QKeySequence("Esc")
    assert shortcuts["Deshacer"] == QKeySequence("Ctrl+Z") and shortcuts["Rehacer"] == QKeySequence("Ctrl+Y")
    for other in ("&Formas", "&Dibujo"):                                  # y ya no andan sueltas en los demás menús
        gone = [a.text() for a in bar[titles.index(other)].menu().actions()]
        assert not {"Deshacer", "Rehacer", "Elegir todos", "Quitar la elección", "Borrar lo elegido",
                    "Elegir el tablero actual (con su recuadro)"} & set(gone)


def test_actions_follow_the_drawing_and_the_selection(win, monkeypatch):
    shape_acts = [win.act_rectangle, win.act_circle, win.act_ellipse, win.act_polygon, win.act_slot, win.act_polyline]
    assert not any(a.isEnabled() for a in shape_acts + win._selection_actions + win._pair_actions)
    assert not win.act_undo.isEnabled() and not win.act_redo.isEnabled() and not win.act_pick_board.isEnabled()
    win.new_drawing()
    assert all(a.isEnabled() for a in shape_acts) and not any(a.isEnabled() for a in win._selection_actions + win._pair_actions)
    answer(monkeypatch, x=0, y=0, w=20, h=10, r=0)
    win.draw_rectangle()
    assert all(a.isEnabled() for a in win._selection_actions) and not any(a.isEnabled() for a in win._pair_actions)
    answer(monkeypatch, x=50, y=0, d=10)
    win.draw_circle()
    win.selection = {0, 1}
    win._update_selection_ui()
    assert all(a.isEnabled() for a in win._pair_actions)
    win.pick_none()
    assert not any(a.isEnabled() for a in win._selection_actions + win._pair_actions)
    assert win.act_undo.isEnabled() and win.act_undo.text() == "Deshacer: Dibujar forma" and win.act_redo.text() == "Rehacer"


def test_a_tool_that_needs_a_selection_says_so_instead_of_opening_a_dialog(blank, monkeypatch):
    refuse_dialogs(monkeypatch)
    for tool in (blank.draw_move, blank.draw_place, blank.draw_rotate, blank.draw_scale, blank.draw_mirror, blank.draw_array,
                 blank.draw_polar, blank.draw_offset, blank.draw_close, blank.draw_delete):
        blank.statusBar().clearMessage()
        tool()
        assert "Elige primero" in status(blank) and blank.project.edits == []
    blank.statusBar().clearMessage()
    blank.draw_union()
    assert "al menos 2" in status(blank)


# --- dibujo nuevo ----------------------------------------------------------------------------------------------------------
def test_a_new_drawing_is_empty_shows_the_page_and_says_what_to_do(win):
    win.new_drawing()
    assert win.project.scratch and win.project.drawing.shapes == [] and "dibujo nuevo" in win.windowTitle()
    assert win.canvas.mode == "drawing" and win.canvas.scene().items()
    texts = [i for i in win.canvas.scene().items() if hasattr(i, "text") and "Dibujo vac" in i.text()]
    assert len(texts) == 1 and texts[0].transform().isIdentity()          # derecho: no se voltea con el eje Y de la vista
    assert win.canvas.mapFromScene(texts[0].pos()).y() > 0
    set_job(win)
    win.wait_for_plan()
    assert "vacio" in win.summary.text()


def test_a_new_drawing_asks_before_throwing_away_changes(blank, monkeypatch):
    answer(monkeypatch, x=0, y=0, w=20, h=10, r=0)
    blank.draw_rectangle()
    assert blank._dirty
    asked = []
    monkeypatch.setattr(blank, "_ask_discard", lambda: asked.append(1) or False)
    blank.new_drawing()
    assert asked == [1] and len(blank.project.drawing.shapes) == 1
    monkeypatch.setattr(blank, "_ask_discard", lambda: asked.append(2) or True)
    blank.new_drawing()
    assert asked == [1, 2] and blank.project.drawing.shapes == [] and not blank._dirty


def test_opening_something_asks_before_throwing_away_changes(blank, monkeypatch):
    answer(monkeypatch, x=0, y=0, w=20, h=10, r=0)
    blank.draw_rectangle()
    monkeypatch.setattr(blank, "_ask_discard", lambda: False)
    monkeypatch.setattr("cnc.gui.main_window.QFileDialog.getOpenFileName",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no tenia que abrir el selector")))
    blank.open_svg_dialog()
    blank.open_job_dialog()
    assert len(blank.project.drawing.shapes) == 1


def test_closing_with_unsaved_changes_can_be_cancelled_saved_or_discarded(blank, monkeypatch, tmp_path):
    answer(monkeypatch, x=0, y=0, w=20, h=10, r=0)
    blank.draw_rectangle()
    monkeypatch.setattr(blank, "_ask_save_before_close", lambda: "cancel")
    assert blank.close() is False and blank.isVisible()
    saved = tmp_path / "pieza.cncjob"
    monkeypatch.setattr(blank, "_ask_save_before_close", lambda: "save")
    monkeypatch.setattr("cnc.gui.main_window.QFileDialog.getSaveFileName", lambda *a, **k: (str(saved), ""))
    assert blank.close() is True and saved.is_file()


def test_closing_and_discarding_does_not_save(blank, monkeypatch, tmp_path):
    answer(monkeypatch, x=0, y=0, w=20, h=10, r=0)
    blank.draw_rectangle()
    monkeypatch.setattr(blank, "_ask_save_before_close", lambda: "discard")
    assert blank.close() is True


def test_a_cancelled_save_keeps_the_window_open(blank, monkeypatch):
    answer(monkeypatch, x=0, y=0, w=20, h=10, r=0)
    blank.draw_rectangle()
    monkeypatch.setattr(blank, "_ask_save_before_close", lambda: "save")
    monkeypatch.setattr("cnc.gui.main_window.QFileDialog.getSaveFileName", lambda *a, **k: ("", ""))
    assert blank.close() is False
    monkeypatch.setattr(blank, "_ask_save_before_close", lambda: "discard")
    assert blank.close() is True                                      # y se cierra: una ventana abierta estorba a las pruebas siguientes


def test_closing_without_changes_asks_nothing(blank, monkeypatch):
    monkeypatch.setattr(blank, "_ask_save_before_close", lambda: (_ for _ in ()).throw(AssertionError("no tenia que preguntar")))
    assert blank.close() is True


# --- formas nuevas ------------------------------------------------------------------------------------------------------------
def test_drawing_a_rectangle_adds_it_selects_it_and_fills_the_table(blank, monkeypatch):
    seen = answer(monkeypatch, x=10, y=20, w=100, h=50, r=5)
    blank.draw_rectangle()
    assert bounds(blank) == pytest.approx((10, 20, 110, 70)) and blank.selection == {0} and blank._dirty
    assert [(r.key, r.count) for r in blank.project.rows] == [((K, None), 1)] and len(halos(blank)) == 1
    assert "Dibujar forma" in status(blank) and seen["dialog"].windowTitle() == "Rectángulo"
    assert blank.project.edits[0]["op"] == "anadir"


def test_the_default_values_put_the_shape_in_the_middle_of_what_is_there(blank, monkeypatch):
    seen = answer(monkeypatch)
    blank.draw_circle()
    assert seen["dialog"].values()["x"] == 60 and seen["dialog"].values()["y"] == 40            # dibujo vacio
    assert len(blank.project.drawing.shapes) == 1 and bounds(blank) == pytest.approx((50, 30, 70, 50))


def test_with_boards_the_shape_is_proposed_in_the_middle_of_the_board_that_is_shown(job19, monkeypatch):
    seen = answer(monkeypatch)
    job19.draw_rectangle()
    v = seen["dialog"].values()
    assert v["x"] + v["w"] / 2 == pytest.approx(310.04, abs=0.01) and v["y"] + v["h"] / 2 == pytest.approx(501.01, abs=0.01)
    job19._board_clicked(2)
    seen = answer(monkeypatch)
    job19.draw_circle()
    assert seen["dialog"].values()["x"] == pytest.approx(1011.37, abs=0.01)


def test_a_circle_is_asked_by_its_diameter(blank, monkeypatch):
    answer(monkeypatch, x=50, y=50, d=30)
    blank.draw_circle()
    assert bounds(blank) == pytest.approx((35, 35, 65, 65)) and blank.project.drawing.shapes[0].kind == "circle"


def test_the_colour_chosen_for_a_shape_is_remembered_for_the_next_one(blank, monkeypatch):
    answer(monkeypatch, x=0, y=0, d=10, color="#ff0000")
    blank.draw_circle()
    assert blank.project.drawing.shapes[0].stroke == "#ff0000" and blank.project.draw_color == "#ff0000"
    seen = answer(monkeypatch)
    blank.draw_circle()
    assert seen["dialog"].values()["color"] == "#ff0000"
    assert sorted(r.key[0] for r in blank.project.rows) == ["#ff0000"] and blank.project.rows[0].count == 2


def test_an_ellipse_a_polygon_and_a_slot(blank, monkeypatch):
    answer(monkeypatch, x=0, y=0, w=40, h=20, a=0)
    blank.draw_ellipse()
    assert bounds(blank, 0) == pytest.approx((-20, -10, 20, 10))
    answer(monkeypatch, x=0, y=0, n=6, size=16, measure="caras", a=0)                  # tuerca de 16 mm entre caras
    blank.draw_polygon()
    assert bounds(blank, 1)[3] == pytest.approx(8) and len(blank.project.drawing.shapes[1].shape.exterior.coords) == 7
    answer(monkeypatch, x=0, y=0, n=6, size=10, measure="radio", a=0)
    blank.draw_polygon()
    assert bounds(blank, 2)[2] == pytest.approx(10)
    answer(monkeypatch, x1=0, y1=0, x2=40, y2=0, w=10)
    blank.draw_slot()
    assert bounds(blank, 3) == pytest.approx((-5, -5, 45, 5))


def test_a_polyline_from_a_list_of_points_open_or_closed(blank, monkeypatch):
    answer(monkeypatch, pts=[(0, 0), (50, 0), (50, 30)], closed=False)
    blank.draw_polyline()
    s = blank.project.drawing.shapes[0]
    assert not s.closed and s.kind == "polyline" and len(blank.project.rows) == 1
    answer(monkeypatch, pts=[(0, 0), (50, 0), (50, 30)], closed=True)
    blank.draw_polyline()
    assert blank.project.drawing.shapes[1].closed and blank.project.drawing.shapes[1].shape.area == pytest.approx(750)


def test_a_shape_that_cannot_be_made_is_reported_and_nothing_changes(blank, monkeypatch):
    errors = []
    monkeypatch.setattr(blank, "_error", lambda title, text: errors.append((title, text)))
    answer(monkeypatch, x=0, y=0, w=10, h=10, r=8)                          # radio mayor que la mitad del lado
    blank.draw_rectangle()
    assert errors and "mitad del lado menor" in errors[0][1] and blank.project.drawing.shapes == [] and not blank._dirty
    answer(monkeypatch, x1=5, y1=5, x2=5, y2=5, w=4)
    blank.draw_slot()
    assert "coinciden" in errors[1][1]


def test_a_cancelled_dialog_changes_nothing(blank, monkeypatch):
    monkeypatch.setattr(ToolDialog, "exec", lambda self: QDialog.Rejected)
    blank.draw_rectangle()
    assert blank.project.drawing.shapes == [] and not blank._dirty and blank.project.edits == []


# --- editar lo elegido -------------------------------------------------------------------------------------------------------
@pytest.fixture
def two(blank, monkeypatch):
    """Un rectangulo (0..40 x 0..20) y un circulo de 10 de diametro en (100, 50)."""
    answer(monkeypatch, x=0, y=0, w=40, h=20, r=0)
    blank.draw_rectangle()
    answer(monkeypatch, x=100, y=50, d=10)
    blank.draw_circle()
    blank.selection = {0}
    blank._selection_changed()
    return blank


def test_moving_and_placing(two, monkeypatch):
    answer(monkeypatch, dx=10, dy=-5)
    two.draw_move()
    assert bounds(two) == pytest.approx((10, -5, 50, 15)) and two.selection == {0}
    def fake_exec(self):
        assert (self.values()["x"], self.values()["y"]) == (10, -5)         # propone donde esta ahora
        self.set_value("x", 0)
        self.set_value("y", 0)
        return QDialog.Accepted

    monkeypatch.setattr(ToolDialog, "exec", fake_exec)
    two.draw_place()
    assert bounds(two) == pytest.approx((0, 0, 40, 20))
    answer(monkeypatch, x=200, y=100)
    two.draw_place()
    assert bounds(two) == pytest.approx((200, 100, 240, 120))


def test_rotating_about_the_centre_by_default_and_about_another_point(two, monkeypatch):
    seen = answer(monkeypatch, a=90)
    two.draw_rotate()
    v = seen["dialog"].values()
    assert (v["x"], v["y"]) == (20, 10) and bounds(two) == pytest.approx((10, -10, 30, 30))
    answer(monkeypatch, a=90, x=0, y=0)
    two.draw_rotate()
    assert bounds(two) == pytest.approx((-30, 10, 10, 30))


def test_changing_the_size_keeps_the_proportions_from_the_corner(two, monkeypatch):
    def fake_exec(self):
        v = self.values()
        assert (v["w"], v["h"], v["keep"], v["from"]) == (40, 20, True, "esquina")
        self.set_value("w", 80)
        self.widgets["w"].valueChanged.emit()                      # como si se hubiera tecleado
        assert self.values()["h"] == pytest.approx(40)             # el gancho mantiene las proporciones
        return QDialog.Accepted

    monkeypatch.setattr(ToolDialog, "exec", fake_exec)
    two.draw_scale()
    assert bounds(two) == pytest.approx((0, 0, 80, 40))


def test_changing_only_the_width(two, monkeypatch):
    answer(monkeypatch, w=80, h=20, keep=False)
    two.draw_scale()
    assert bounds(two) == pytest.approx((0, 0, 80, 20))


def test_scaling_from_the_centre(two, monkeypatch):
    answer(monkeypatch, w=80, h=40, keep=False, **{"from": "centro"})
    two.draw_scale()
    assert bounds(two) == pytest.approx((-20, -10, 60, 30))


def test_a_straight_line_cannot_be_given_a_height(blank, monkeypatch):
    errors = []
    monkeypatch.setattr(blank, "_error", lambda title, text: errors.append(text))
    answer(monkeypatch, pts=[(0, 0), (50, 0)], closed=False)
    blank.draw_polyline()
    answer(monkeypatch, w=100, h=20, keep=False)
    blank.draw_scale()
    assert errors and "no tiene ancho o alto" in errors[0] and bounds(blank) == pytest.approx((0, 0, 50, 0))
    answer(monkeypatch, w=100, h=0, keep=False)
    blank.draw_scale()
    assert bounds(blank) == pytest.approx((0, 0, 100, 0))


def test_mirroring_moves_the_axis_with_the_choice(two, monkeypatch):
    def fake_exec(self):
        assert self.values()["pos"] == 20
        self.set_value("axis", "horizontal")
        self.widgets["axis"].currentIndexChanged.emit(1)
        assert self.values()["pos"] == 10                            # el centro en Y
        self.set_value("pos", 0)
        return QDialog.Accepted

    monkeypatch.setattr(ToolDialog, "exec", fake_exec)
    two.draw_mirror()
    assert bounds(two) == pytest.approx((0, -20, 40, 0))


def test_a_grid_of_copies_selects_the_copies(two, monkeypatch):
    seen = answer(monkeypatch, nx=3, ny=2)
    two.draw_array()
    v = seen["dialog"].values()
    assert (v["dx"], v["dy"]) == (50, 30)                            # lo elegido + 10 mm
    assert len(two.project.drawing.shapes) == 2 + 5 and two.selection == {2, 3, 4, 5, 6}
    assert [(r.key, r.count) for r in two.project.rows] == [((K, None), 7)]


def test_copies_in_a_circle(two, monkeypatch):
    two.selection = {1}
    two._selection_changed()
    answer(monkeypatch, n=4, a=360, x=100, y=0)
    two.draw_polar()
    centres = sorted((round(s.shape.centroid.x), round(s.shape.centroid.y)) for s in two.project.drawing.shapes[1:])
    assert centres == [(50, 0), (100, -50), (100, 50), (150, 0)] and two.selection == {2, 3, 4}


def test_offsetting_outwards_and_inwards(two, monkeypatch):
    answer(monkeypatch, dir="fuera", d=2, corners="vivas", keep=True)
    two.draw_offset()
    assert len(two.project.drawing.shapes) == 3 and bounds(two, 2) == pytest.approx((-2, -2, 42, 22)) and two.selection == {2}
    two.selection = {2}
    answer(monkeypatch, dir="dentro", d=3, corners="vivas", keep=False)
    two.draw_offset()
    assert bounds(two, len(two.project.drawing.shapes) - 1) == pytest.approx((1, 1, 39, 19))


def test_union_subtract_intersect_and_close(blank, monkeypatch):
    blank.detect_boards.setChecked(False)                                 # un rectangulo con un hueco dentro no es un tablero
    answer(monkeypatch, x=0, y=0, w=100, h=100, r=0)
    blank.draw_rectangle()
    answer(monkeypatch, x=50, y=50, d=20)
    blank.draw_circle()
    blank.selection = {0, 1}
    blank._selection_changed()
    answer(monkeypatch, base="primera", keep=False)
    blank.draw_subtract()
    assert len(blank.project.drawing.shapes) == 2 and blank.selection == {0, 1}      # contorno y hueco
    blank.undo()
    blank.selection = {0, 1}
    blank.draw_union()
    assert len(blank.project.drawing.shapes) == 1 and blank.project.drawing.shapes[0].shape.area == pytest.approx(10000)
    blank.undo()
    blank.selection = {0, 1}
    blank.draw_intersect()
    assert len(blank.project.drawing.shapes) == 1 and blank.project.drawing.shapes[0].shape.area < 400


def test_closing_an_open_outline(blank, monkeypatch):
    answer(monkeypatch, pts=[(0, 0), (50, 0), (50, 30)], closed=False)
    blank.draw_polyline()
    blank.draw_close()
    assert blank.project.drawing.shapes[0].closed and blank.selection == {0}


def test_deleting_and_the_selection_after_it(two, monkeypatch):
    two.selection = {0, 1}
    two.draw_delete()
    assert two.project.drawing.shapes == [] and two.selection == set() and two.project.rows == []
    assert "Borrar" in status(two)


def test_an_edit_that_fails_is_reported_and_leaves_the_drawing_alone(two, monkeypatch):
    errors = []
    monkeypatch.setattr(two, "_error", lambda title, text: errors.append((title, text)))
    two.selection = {0, 1}
    answer(monkeypatch, dir="dentro", d=500, corners="vivas", keep=False)
    two.draw_offset()
    assert errors and errors[0][0] == "Desfase" and "no deja nada" in errors[0][1] and len(two.project.drawing.shapes) == 2


# --- deshacer y rehacer --------------------------------------------------------------------------------------------------------
def test_undo_and_redo_from_the_menu(two, monkeypatch):
    answer(monkeypatch, dx=10, dy=0)
    two.draw_move()
    moved = bounds(two)
    assert two.act_undo.text() == "Deshacer: Mover"
    two.act_undo.trigger()
    assert bounds(two) == pytest.approx((0, 0, 40, 20)) and "Deshecho: Mover" in status(two) and two.act_redo.text() == "Rehacer: Mover"
    assert two.selection == set()
    two.act_redo.trigger()
    assert bounds(two) == pytest.approx(moved) and "Rehecho: Mover" in status(two)
    two.undo()
    two.undo()
    two.undo()
    assert two.project.drawing.shapes == [] and not two.act_undo.isEnabled()
    two.undo()                                                      # sin nada que deshacer: no pasa nada
    two.redo()
    assert len(two.project.drawing.shapes) == 1


def test_undoing_the_first_shape_back_to_empty_and_redoing_it_fits_the_view(blank, monkeypatch):
    answer(monkeypatch, x=0, y=0, w=1000, h=500, r=0)
    blank.draw_rectangle()
    blank.undo()
    assert blank.project.drawing.shapes == [] and blank.canvas.scene().items()
    blank.redo()
    assert len(halos(blank)) == 0 and len(blank.project.drawing.shapes) == 1


def test_group_changes_are_undone_from_the_same_menu(two, monkeypatch):
    monkeypatch.setattr("cnc.gui.main_window.MainWindow._ask_group", lambda self: ("Pozo", "corte"))
    two.new_group()
    assert two.project.groups() and two.act_undo.text() == "Deshacer: Grupo nuevo"
    two.undo()
    assert not two.project.groups()


# --- avisos, tableros y grupos ------------------------------------------------------------------------------------------------
def test_a_rectangle_around_other_shapes_becomes_a_board_and_the_window_says_so(blank, monkeypatch):
    answer(monkeypatch, x=0, y=0, d=10)
    blank.draw_circle()
    answer(monkeypatch, x=-30, y=-30, w=60, h=60, r=0)
    blank.draw_rectangle()
    assert [b.index for b in blank.project.drawing.layout.boards] == [1] and blank.boards.count() == 1
    assert any("se toma como tablero" in m.text for m in blank._extra_notes)
    blank.pick_current_board()
    assert blank.selection == {0, 1} and "1 recuadro de tablero" in blank._selection_text()
    assert len(halos(blank)) == 2


def draw_board_around_a_circle(w, monkeypatch, x=-30, y=-30):
    answer(monkeypatch, x=x + 30, y=y + 30, d=10)
    w.draw_circle()
    answer(monkeypatch, x=x, y=y, w=60, h=60, r=0)
    w.draw_rectangle()


def board_corner(w):
    return tuple(round(v, 3) for v in w.project.drawing.layout.boards[0].bounds[:2])


def circle_center(w):
    b = w.project.drawing.shapes[0].shape.bounds
    return round((b[0] + b[2]) / 2, 3), round((b[1] + b[3]) / 2, 3)


def test_a_board_drawn_by_hand_is_offered_the_zero_and_goes_there_with_the_whole_drawing(blank, monkeypatch):
    asked = []
    monkeypatch.setattr(type(blank), "_ask_board_at_zero", lambda self, x, y: asked.append((x, y)) or True)
    draw_board_around_a_circle(blank, monkeypatch)
    assert asked == [(-30.0, -30.0)]
    assert board_corner(blank) == (0, 0) and circle_center(blank) == (30, 30)
    assert "al 0,0" in status(blank)
    blank.undo()                                                    # un solo paso devuelve el dibujo a donde estaba
    assert board_corner(blank) == (-30, -30) and circle_center(blank) == (0, 0)
    blank.redo()
    assert board_corner(blank) == (0, 0)


def test_the_board_stays_where_it_was_drawn_when_the_answer_is_no(blank, monkeypatch):
    asked = []
    monkeypatch.setattr(type(blank), "_ask_board_at_zero", lambda self, x, y: asked.append((x, y)) or False)
    draw_board_around_a_circle(blank, monkeypatch)
    assert asked == [(-30.0, -30.0)]
    assert board_corner(blank) == (-30, -30) and circle_center(blank) == (0, 0)
    assert blank.act_undo.text() == "Deshacer: Dibujar forma"


def test_the_zero_is_not_offered_to_a_board_that_is_already_there(blank, monkeypatch):
    monkeypatch.setattr(type(blank), "_ask_board_at_zero", lambda self, x, y: (_ for _ in ()).throw(AssertionError("no tenia que preguntar")))
    draw_board_around_a_circle(blank, monkeypatch, x=0, y=0)
    assert board_corner(blank) == (0, 0)


def test_the_zero_is_offered_once_and_not_for_the_boards_a_drawing_already_has(blank, monkeypatch):
    asked = []
    monkeypatch.setattr(type(blank), "_ask_board_at_zero", lambda self, x, y: asked.append((x, y)) or False)
    draw_board_around_a_circle(blank, monkeypatch)
    answer(monkeypatch, x=200, y=200, d=10)
    blank.draw_circle()
    answer(monkeypatch, x=170, y=170, w=60, h=60, r=0)
    blank.draw_rectangle()
    assert len(blank.project.drawing.layout.boards) == 2 and len(asked) == 1


def test_an_svg_that_brings_its_board_is_not_asked_and_not_moved(win, monkeypatch):
    monkeypatch.setattr(type(win), "_ask_board_at_zero", lambda self, x, y: (_ for _ in ()).throw(AssertionError("no tenia que preguntar")))
    assert win.open_svg(BOARDS19, apply_default_recipe=False)
    assert win.project.drawing.layout.boards


def test_a_new_shape_outside_every_board_is_flagged(job19, monkeypatch):
    answer(monkeypatch, x=660, y=500, d=12)
    job19.draw_circle()
    assert any("fuera de todo tablero" in m.text for m in job19._extra_notes) and "fuera de todo tablero" in status(job19)


def test_the_board_you_were_looking_at_stays_after_an_edit(job19, monkeypatch):
    job19._board_clicked(2)
    assert job19.current == 2
    answer(monkeypatch, x=900, y=500, d=12)
    job19.draw_circle()
    assert job19.current == 2 and job19.boards.currentRow() == 1


def test_picking_a_whole_board_and_moving_it_takes_the_frame_along(job19, monkeypatch):
    job19._board_clicked(2)
    b = job19._current_board()
    rect_before = job19.project.drawing.shapes[b.index].shape.bounds
    job19.pick_current_board()
    assert b.index in job19.selection and len(job19.selection) == len(b.contents) + 1
    answer(monkeypatch, dx=0, dy=25)
    job19.draw_move()
    assert job19.project.drawing.shapes[b.index].shape.bounds[1] == pytest.approx(rect_before[1] + 25)
    assert [x.number for x in job19.project.drawing.layout.boards] == [1, 2]


def test_a_board_frame_in_the_selection_is_left_out_of_groups(job19, monkeypatch):
    job19.pick_current_board()
    n = len(job19._picked_vectors())
    assert n == len(job19.selection) - 1 and job19.act_new_group.isEnabled()
    monkeypatch.setattr("cnc.gui.main_window.MainWindow._ask_group", lambda self: ("Todo", "corte"))
    job19.new_group()
    assert job19.project.groups()[0].count == n


def test_toggling_board_detection_after_drawing_on_a_new_drawing_keeps_the_drawing(blank, monkeypatch):
    answer(monkeypatch, x=0, y=0, d=10)
    blank.draw_circle()
    answer(monkeypatch, x=-30, y=-30, w=60, h=60, r=0)
    blank.draw_rectangle()
    assert blank.project.drawing.layout.boards
    blank.detect_boards.setChecked(False)
    assert blank.project.scratch and len(blank.project.drawing.shapes) == 2 and not blank.project.drawing.layout.boards
    assert blank._dirty
    blank.detect_boards.setChecked(True)
    assert blank.project.drawing.layout.boards and len(blank.project.drawing.shapes) == 2


def test_the_plan_of_a_drawing_made_here_is_computed(blank, monkeypatch):
    blank.detect_boards.setChecked(False)
    answer(monkeypatch, x=10, y=10, w=80, h=50, r=6)
    blank.draw_rectangle()
    answer(monkeypatch, x=50, y=35, d=12)
    blank.draw_circle()
    plan = blank.plan_now()
    assert plan.ok and plan.virtual_stock and blank.generate_btn.isEnabled()


# --- guardar y abrir un dibujo nuevo ----------------------------------------------------------------------------------------
def test_saving_a_new_drawing_names_it_and_opening_the_job_brings_it_back(blank, monkeypatch, tmp_path, qtbot):
    answer(monkeypatch, x=0, y=0, w=40, h=20, r=0)
    blank.draw_rectangle()
    answer(monkeypatch, x=100, y=50, d=10, color="#ff0000")
    blank.draw_circle()
    path = tmp_path / "soporte.cncjob"
    assert blank.save_job(path) and not blank._dirty
    assert "soporte.cncjob" in blank.windowTitle() and blank.project.drawing.path.stem == "soporte"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["svg"] == "" and data["edicion"]["nuevo"] and len(data["edicion"]["operaciones"]) == 2
    other = MainWindow(Project(tools()), [], use_settings=False)
    qtbot.addWidget(other)
    other.show()
    assert other.open_job(path)
    assert other.project.scratch and [s.shape.wkt for s in other.project.drawing.shapes] == \
        [s.shape.wkt for s in blank.project.drawing.shapes]
    assert "soporte.cncjob" in other.windowTitle() and not other._dirty and other.project.undo_label == ""
    assert other.project.drawing.path.stem == "soporte" and len(other.project.rows) == 2


def test_a_saved_job_of_a_new_drawing_keeps_its_groups(blank, monkeypatch, tmp_path, qtbot):
    answer(monkeypatch, x=0, y=0, d=10)
    blank.draw_circle()
    answer(monkeypatch, x=50, y=0, d=10)
    blank.draw_circle()
    monkeypatch.setattr("cnc.gui.main_window.MainWindow._ask_group", lambda self: ("Pozo", "vaciado"))
    blank.selection = {1}
    blank.new_group()
    path = tmp_path / "grupos.cncjob"
    blank.save_job(path)
    other = MainWindow(Project(tools()), [], use_settings=False)
    qtbot.addWidget(other)
    assert other.open_job(path)
    assert [r.name for r in other.project.groups()] == ["Pozo"] and set(other.project.assign) == {1}
    assert other.project.load_notes == []


# --- lo que la prueba de mutantes echo en falta -------------------------------------------------------------------------
def test_picking_the_current_board_needs_a_board_and_stale_selection_indices_are_ignored(blank, monkeypatch):
    assert not blank.act_pick_board.isEnabled()
    answer(monkeypatch, x=0, y=0, w=40, h=20, r=0)
    blank.draw_rectangle()
    assert not blank.act_pick_board.isEnabled()                                    # un rectangulo suelto no es un tablero
    blank.selection = {0, 99}
    assert blank._edit_selection() == [0]
    blank.selection = {99}
    blank._update_draw_actions()
    assert blank._edit_selection() == [] and not any(a.isEnabled() for a in blank._selection_actions)


def test_a_drawing_with_boards_offers_to_pick_the_board_that_is_shown(job19):
    assert job19.act_pick_board.isEnabled()


def test_the_colour_choice_lists_each_stroke_colour_once_and_leaves_out_the_group_labels(blank, monkeypatch):
    answer(monkeypatch, x=0, y=0, w=10, h=10, r=0)
    blank.draw_rectangle()
    answer(monkeypatch, x=50, y=0, d=10)
    blank.draw_circle()
    blank.apply_edit(de.add_edit([de.make_rectangle(100, 0, 10, 10, stroke="#ff0000", fill="#00ff00"),
                                  de.make_circle(150, 5, 5, stroke="#ff0000")]))
    assert blank._colors() == [K, "#ff0000"]                                        # el rojo esta en dos filas y sale una vez
    label = blank.project.create_group([0], "Pozo", "vaciado")
    assert label not in blank._colors() and blank._colors() == [K, "#ff0000"]


def test_union_intersect_and_subtract_refuse_a_single_shape_without_asking_anything(two, monkeypatch):
    refuse_dialogs(monkeypatch)
    edits = list(two.project.edits)
    for tool in (two.draw_union, two.draw_intersect, two.draw_subtract):
        two.statusBar().clearMessage()
        tool()
        assert "al menos 2" in status(two) and two.project.edits == edits


def test_the_view_is_fitted_only_when_the_first_shape_appears(blank, monkeypatch):
    fits = []
    real = blank.refresh_all
    monkeypatch.setattr(blank, "refresh_all", lambda *a, **k: (fits.append(k.get("fit", False)), real(*a, **k))[1])
    answer(monkeypatch, x=0, y=0, w=40, h=20, r=0)
    blank.draw_rectangle()
    answer(monkeypatch, x=100, y=50, d=10)
    blank.draw_circle()
    assert fits == [True, False]


def test_a_plain_edit_inside_a_board_says_nothing_about_boards(job19):
    inside = job19.project.drawing.layout.boards[0].contents[0]
    assert job19.apply_edit(de.move_edit([inside], 2, 0))
    assert job19._extra_notes == []


def test_an_edit_drops_the_cached_plan_and_the_cached_holding_zone(job19):
    sentinel = object()
    job19._hold_cache, job19.last_plan = sentinel, sentinel
    inside = job19.project.drawing.layout.boards[0].contents[0]
    job19.apply_edit(de.move_edit([inside], 2, 0))
    assert job19._hold_cache is not sentinel and job19.last_plan is not sentinel         # el calculo se rehace con lo nuevo


def test_undo_and_redo_count_as_changes_that_are_not_saved_yet(two, tmp_path):
    two.save_job(tmp_path / "t.cncjob")
    assert not two._dirty
    two.undo()
    assert two._dirty
    two.save_job(tmp_path / "t.cncjob")
    two.redo()
    assert two._dirty


def test_a_new_drawing_forgets_the_job_file_of_the_previous_one(two, tmp_path):
    two.save_job(tmp_path / "uno.cncjob")
    assert two.job_path is not None and two.windowTitle().endswith("uno.cncjob")
    two.new_drawing()
    assert two.job_path is None and two.windowTitle().endswith("dibujo nuevo") and not two._dirty


def test_the_size_dialog_follows_the_height_too_and_ticking_the_box_again_refits_the_height(two, monkeypatch):
    def fake_exec(self):
        self.set_value("h", 10)
        self.widgets["h"].valueChanged.emit()                                       # como si se hubiera tecleado
        assert self.values()["w"] == pytest.approx(20)                              # 40 x 20: el ancho sigue al alto
        self.set_value("keep", False)
        self.set_value("w", 60)
        self.widgets["w"].valueChanged.emit()
        assert self.values()["h"] == pytest.approx(10)                              # sin la casilla no se toca el alto
        self.set_value("keep", True)
        assert self.values()["h"] == pytest.approx(30)                              # al marcarla otra vez se ajusta al ancho
        return QDialog.Rejected

    monkeypatch.setattr(ToolDialog, "exec", fake_exec)
    two.draw_scale()
    assert bounds(two) == pytest.approx((0, 0, 40, 20))


def test_a_vertical_line_can_be_given_a_new_height_and_keeps_its_zero_width(blank, monkeypatch):
    answer(monkeypatch, pts=[(0, 0), (0, 50)], closed=False)
    blank.draw_polyline()
    answer(monkeypatch, w=0, h=100, keep=False)
    blank.draw_scale()
    assert bounds(blank) == pytest.approx((0, 0, 0, 100))


def test_a_grid_puts_the_columns_along_x_and_the_rows_along_y(two, monkeypatch):
    answer(monkeypatch, nx=3, ny=2)
    two.draw_array()
    xs = [(bounds(two, i)[0], bounds(two, i)[1]) for i in sorted(two.selection)]
    assert sorted(xs) == [(0, 30), (50, 0), (50, 30), (100, 0), (100, 30)]       # 3 columnas (X) por 2 filas (Y), sin la original


def test_subtracting_can_take_the_last_shape_as_the_material_and_keep_the_originals(blank, monkeypatch):
    answer(monkeypatch, x=40, y=40, w=20, h=20, r=0)
    blank.draw_rectangle()
    answer(monkeypatch, x=50, y=50, d=100)
    blank.draw_circle()
    blank.selection = {0, 1}
    blank._selection_changed()
    answer(monkeypatch, base="ultima", keep=True)
    blank.draw_subtract()
    shapes = blank.project.drawing.shapes
    assert len(shapes) == 4 and blank.selection == {2, 3}                          # las dos originales y el anillo con su hueco
    assert sorted(s.shape.area for s in shapes[2:]) == pytest.approx([400, math.pi * 2500], rel=1e-3)


def test_dropping_a_file_asks_before_throwing_away_changes(two, monkeypatch):
    class Drop:
        def __init__(self, path):
            self.path = path
            self.accepted = 0

        def mimeData(self):
            return self

        def urls(self):
            return [QUrl.fromLocalFile(self.path)]

        def acceptProposedAction(self):
            self.accepted += 1

    asked, opened = [], []
    answers = iter([False, True])
    monkeypatch.setattr(two, "_ask_discard", lambda: (asked.append(1), next(answers))[1])
    monkeypatch.setattr(two, "open_svg", lambda path, **k: opened.append(("svg", Path(path).name)))
    monkeypatch.setattr(two, "open_job", lambda path, **k: opened.append(("job", Path(path).name)))
    assert two._dirty
    first, second = Drop(str(BOARDS19)), Drop(str(DATA / "x.cncjob"))
    two.dropEvent(first)
    assert asked == [1] and opened == []
    two.dropEvent(second)
    assert asked == [1, 1] and opened == [("job", "x.cncjob")]
    assert first.accepted == 1 and second.accepted == 1                  # el archivo se da por cogido aunque se diga que no


def selection_box(w):
    return [i for i in w.canvas.scene().items() if i.data(ITEM_KIND) == "selbox"]


def test_several_chosen_shapes_show_their_circumscribed_box_with_its_four_corners(two):
    assert selection_box(two) == []                                     # una sola forma: solo su halo
    two.selection = {0, 1}                                              # el rectangulo y el circulo: de (0, 0) a (105, 55)
    two._selection_changed()
    two.refresh_all()
    parts = selection_box(two)
    box = next(i for i in parts if i.rect().width() > 7)
    assert len(parts) == 5 and all(i.acceptedMouseButtons() == Qt.NoButton for i in parts)
    assert (box.rect().left(), box.rect().top(), box.rect().right(), box.rect().bottom()) == pytest.approx((0, 0, 105, 55))
    assert sorted((i.pos().x(), i.pos().y()) for i in parts if i is not box) == [(0, 0), (0, 55), (105, 0), (105, 55)]
    two.selection = {1}
    two._selection_changed()
    two.refresh_all()
    assert selection_box(two) == []


def test_the_halo_of_the_chosen_shapes_lets_the_clicks_through(two):
    assert halos(two)
    assert all(h.acceptedMouseButtons() == Qt.NoButton for h in halos(two))
