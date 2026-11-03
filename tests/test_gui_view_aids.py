"""Soltar un SVG en la ventana, la rejilla del lienzo y el contorno de puntos del recorrido de la maquina (PySide6 sin
pantalla)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import math
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QMimeData, QPoint, QPointF, QRectF, Qt, QUrl
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication, QGraphicsPathItem, QGraphicsPolygonItem, QGraphicsRectItem, QMessageBox, QWidget

from cnc import draw_tools as dt
from cnc import grid as gr
from cnc.gui import theme
from cnc.gui.main_window import MainWindow
from cnc.model import GRBL_MAQUINA_REAL, MachineProfile, Tool
from cnc.project import Project

DATA = Path(__file__).parent / "data"
BOARDS19 = DATA / "tableros_19mm.svg"
BIG = DATA / "dibujo_grande.svg"
SMALL = MachineProfile(name="Chica", max_feed=3000.0, travel=(300.0, 500.0, 80.0))
TOOLS = {"em4": Tool(diameter=4, name="Fresa 4", feed_xy=300, feed_z=200, stepdown=6)}


@pytest.fixture(autouse=True)
def no_boxes(monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)


def make(qtbot, machines=None, settings_file=None, drawing=BOARDS19):
    project = Project(dict(TOOLS), dict(machines or {"real": GRBL_MAQUINA_REAL}))
    w = MainWindow(project, [], use_settings=False, settings_file=settings_file)
    qtbot.addWidget(w)
    w.resize(1400, 880)
    w.show()
    if drawing is not None:
        assert w.open_svg(drawing, apply_default_recipe=False)
        w.wait_for_plan()
    return w


@pytest.fixture
def win(qtbot):
    return make(qtbot)


def ready(w):
    """El tablero 1 con fresa y grosor: el plan sale bien."""
    w.job.tool.setCurrentIndex(w.job.tool.findData("em4"))
    w.job.thickness.setText("19")
    w.job.thickness.valueChanged.emit()
    w.wait_for_plan()
    assert w.last_plan.ok


def frame_items(w, kind="frame"):
    return [i for i in w.canvas._scene.items() if i.data(0) == kind]


def mime(*paths_or_urls):
    m = QMimeData()
    m.setUrls([QUrl(p) if "://" in str(p) else QUrl.fromLocalFile(str(p)) for p in paths_or_urls])
    return m


def drag_enter(w, *paths):
    data = mime(*paths)                                   # el evento no es dueño de los datos: que no se borren antes de usarlos
    event = QDragEnterEvent(QPoint(20, 20), Qt.CopyAction, data, Qt.LeftButton, Qt.NoModifier)
    event.ignore()
    event.keep = data
    QApplication.sendEvent(w, event)
    return event


def drop(w, *paths, entered=True):
    """Soltar como lo hace el sistema: primero entra el arrastre y, si la ventana lo acepta, llega la suelta (Qt no
    entrega una suelta a quien no aceptó la entrada). `entered=False`: la suelta sola, directa a la ventana."""
    data = mime(*paths)
    event = QDropEvent(QPointF(20, 20), Qt.CopyAction, data, Qt.LeftButton, Qt.NoModifier)
    event.ignore()
    event.keep = data
    if entered:
        if not drag_enter(w, *paths).isAccepted():
            return event
        QApplication.sendEvent(w, event)
    else:
        w.dropEvent(event)
    return event


# --- soltar un archivo en la ventana -------------------------------------------------------------------------------
def test_only_the_window_accepts_drops_so_that_nothing_inside_keeps_the_file(win):
    """Qt entrega la suelta al primer widget (de dentro afuera) que acepte soltar: si el lienzo o un campo de texto lo
    aceptan, la rechazan y la ventana ni se entera. Antes el lienzo (casi toda la ventana) se la quedaba."""
    assert win.acceptDrops()
    greedy = [type(c).__name__ for c in win.findChildren(QWidget) if c.acceptDrops()]
    assert greedy == []
    assert not win.canvas.acceptDrops() and not win.canvas.viewport().acceptDrops()


def test_the_drop_goes_to_the_window_from_wherever_it_lands(win):
    """Lo mismo que hace Qt para elegir el destino: desde el widget bajo el cursor hacia afuera, el primero que acepte."""
    def target(widget):
        while widget is not None:
            if widget.isEnabled() and widget.acceptDrops():
                return widget
            if widget.isWindow():
                return None
            widget = widget.parentWidget()
        return None
    widgets = [c for c in win.findChildren(QWidget) if c.window() is win]        # sin las listas que se despliegan (otra ventana)
    assert len(widgets) > 50
    assert [type(c).__name__ for c in widgets if target(c) is not win] == []


def test_it_stays_that_way_after_working_with_the_drawing(win):
    ready(win)
    win.set_view("machine")
    win.wait_for_plan()
    win.tabs.setCurrentWidget(win.hold_panel)
    win.set_view("drawing")
    win.act_grid.setChecked(False)
    win.act_frame.setChecked(False)
    assert [c for c in win.findChildren(QWidget) if c.acceptDrops()] == []
    assert not win.canvas.viewport().acceptDrops()


def test_the_window_accepts_a_dragged_svg_or_job_and_nothing_else(win, tmp_path):
    for name in ("a.svg", "A.SVG", "plano.Svg", "trabajo.cncjob", "x.CNCJOB"):
        assert drag_enter(win, tmp_path / name).isAccepted(), name
    for name in ("a.txt", "a.svg.bak", "a.nc", "sinextension", "a.png"):
        assert not drag_enter(win, tmp_path / name).isAccepted(), name
    assert not drag_enter(win, "https://ejemplo.org/plano.svg").isAccepted()          # una direccion web no es un archivo
    assert not drag_enter(win).isAccepted()                                          # nada que soltar
    assert not drag_enter(win, tmp_path / "a.txt", tmp_path / "b.svg").isAccepted()  # manda el primero


def test_dropping_an_svg_opens_it_and_tells_the_dragger_it_was_taken(qtbot, win):
    event = drop(win, BIG)
    assert event.isAccepted()
    assert win.project.drawing.path.name == "dibujo_grande.svg"


def test_dropping_a_job_file_opens_the_job(qtbot, win, tmp_path):
    ready(win)
    saved = tmp_path / "uno.cncjob"
    assert win.save_job(saved) and saved.exists()
    other = make(qtbot, drawing=BIG)
    assert drop(other, saved).isAccepted()
    assert other.project.drawing.path.name == "tableros_19mm.svg" and other.project.general.thickness == 19.0


def test_other_files_are_ignored_and_do_not_ask_about_discarding_changes(win, tmp_path, monkeypatch):
    asked, opened = [], []
    monkeypatch.setattr(win, "_confirm_discard", lambda: asked.append(1) or True)
    monkeypatch.setattr(win, "open_svg", lambda *a, **k: opened.append(a))
    for entered in (True, False):
        event = drop(win, tmp_path / "notas.txt", entered=entered)
        assert not event.isAccepted() and asked == [] and opened == []
        assert not drop(win, entered=entered).isAccepted() and asked == []


def test_a_refused_discard_keeps_the_drawing_but_the_drop_was_taken(win, monkeypatch):
    name = win.project.drawing.path.name
    monkeypatch.setattr(win, "_confirm_discard", lambda: False)
    event = drop(win, BIG)
    assert event.isAccepted() and win.project.drawing.path.name == name


# --- el contorno de puntos del recorrido de la maquina -------------------------------------------------------------
def test_the_machine_travel_is_drawn_as_a_dotted_polygon_over_the_board(win):
    plan = win.last_plan
    items = frame_items(win)
    assert len(items) == 1 and isinstance(items[0], QGraphicsPolygonItem)
    poly = items[0].polygon()
    assert [(round(p.x(), 3), round(p.y(), 3)) for p in poly] == [(round(x, 3), round(y, 3)) for x, y in plan.frame]
    pen = items[0].pen()
    assert pen.style() == Qt.CustomDashLine and pen.capStyle() == Qt.RoundCap and pen.isCosmetic()
    assert pen.dashPattern()[0] < 0.1                                           # trazo casi nulo con punta redonda: un punto
    assert pen.color().name() == theme.MACHINE
    assert items[0].brush().style() == Qt.NoBrush                                # no tapa nada
    assert items[0].acceptedMouseButtons() == Qt.NoButton                        # y los clics pasan de largo
    assert "Taller" in items[0].toolTip() and "651 x 1011 mm" in items[0].toolTip()


def test_the_zero_of_the_machine_is_marked_with_a_cross_at_the_first_corner(win):
    marks = frame_items(win, "frame-zero")
    assert len(marks) == 1 and isinstance(marks[0], QGraphicsPathItem)
    assert (marks[0].pos().x(), marks[0].pos().y()) == pytest.approx(win.last_plan.frame[0])
    assert marks[0].pen().color().name() == theme.ERROR and marks[0].acceptedMouseButtons() == Qt.NoButton
    assert "Cero de la máquina" in marks[0].toolTip()


def test_the_legend_names_the_machine_and_its_size(win):
    text = win.legend.text()
    assert "Taller" in text and "651 x 1011 mm" in text and "cero" in text and theme.MACHINE in text


def test_the_frame_shows_before_the_tool_and_the_thickness_are_chosen(win):
    assert not win.last_plan.ok and "fresa" in win.last_plan.error
    assert len(frame_items(win)) == 1 and len(win.canvas._frame) == 4


def test_a_working_plan_keeps_the_same_frame(win):
    before = win.canvas._frame
    ready(win)
    assert win.canvas._frame == before == win.last_plan.frame


def test_the_second_board_has_its_frame_at_its_own_corner(win):
    first = win.canvas._frame[0]
    win.boards.setCurrentRow(1)
    win.wait_for_plan()
    second = win.canvas._frame[0]
    b = win.project.unit(2).bounds
    assert second == pytest.approx((b[0], b[1]), abs=0.01) and second[0] > first[0] + 600
    assert len(frame_items(win)) == 1


def test_the_frame_changes_with_the_machine_chosen(qtbot):
    w = make(qtbot, {"real": GRBL_MAQUINA_REAL, "chica": SMALL})
    w.job.machine.setCurrentIndex(w.job.machine.findData("real"))
    w.wait_for_plan()
    f = w.canvas._frame
    assert sorted((round(math.dist(f[0], f[1])), round(math.dist(f[1], f[2])))) == [651, 1011]
    w.job.machine.setCurrentIndex(w.job.machine.findData("chica"))
    w.wait_for_plan()
    f = w.canvas._frame
    assert sorted((round(math.dist(f[0], f[1])), round(math.dist(f[1], f[2])))) == [300, 500]
    assert "Chica" in w.legend.text() and "300 x 500 mm" in w.legend.text()
    assert "Chica" in frame_items(w)[0].toolTip()


def test_without_a_machine_there_is_no_frame_and_no_legend(win):
    assert frame_items(win)
    win.project.machines = {}
    win.request_plan()
    win.wait_for_plan()
    assert frame_items(win) == [] and frame_items(win, "frame-zero") == []
    assert win.canvas._frame == () and win.legend.text() == ""


def test_the_frame_is_gone_while_the_next_drawing_loads_and_comes_back_for_it(win):
    first = win.canvas._frame
    assert win.open_svg(BIG, apply_default_recipe=False)
    assert win.canvas._frame == () and frame_items(win) == []                     # el del dibujo anterior no se queda
    win.wait_for_plan()
    assert len(frame_items(win)) == 1 and win.canvas._frame != first


def test_the_recorrido_button_and_the_view_menu_entry_are_the_same_action(win):
    assert win.btn_frame.defaultAction() is win.act_frame and win.btn_grid.defaultAction() is win.act_grid
    view = next(m for m in win.menuBar().findChildren(type(win.menuBar().addMenu("x"))) if m.title() == "&Ver")
    assert win.act_frame in view.actions() and win.act_grid in view.actions()
    assert win.act_frame.isCheckable() and win.act_frame.isChecked()


def test_switching_the_recorrido_off_removes_the_dots_the_cross_and_the_legend(win):
    win.act_frame.setChecked(False)
    assert frame_items(win) == [] and frame_items(win, "frame-zero") == [] and win.legend.text() == ""
    assert not win.canvas.show_frame
    win.act_frame.setChecked(True)
    assert len(frame_items(win)) == 1 and len(frame_items(win, "frame-zero")) == 1 and "Taller" in win.legend.text()


def test_the_frame_does_not_change_what_fit_shows(win):
    with_frame = win.canvas.content_rect()
    win.act_frame.setChecked(False)
    assert win.canvas.content_rect() == with_frame


def test_fit_frames_the_drawing_even_when_the_machine_is_much_bigger(qtbot):
    big = MachineProfile(name="Grande", max_feed=3000.0, travel=(3000.0, 3000.0, 100.0))
    w = make(qtbot, {"big": big})
    assert len(frame_items(w)) == 1
    w.canvas.fit()
    seen = w.canvas.mapToScene(w.canvas.viewport().rect()).boundingRect()
    assert seen.height() < 1500 and seen.width() < 3000                          # se ve el dibujo, no los 3 m de la maquina
    f = w.canvas._frame
    assert w.canvas.sceneRect().contains(QRectF(f[0][0], f[0][1], 3000, 3000))      # pero se puede ir hasta el contorno


def test_the_scene_can_be_scrolled_to_the_whole_frame(win):
    f = win.canvas._frame
    xs, ys = [p[0] for p in f], [p[1] for p in f]
    assert win.canvas.sceneRect().contains(QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)))


def test_the_frame_is_not_drawn_in_the_machine_view_where_the_travel_is_already_dotted(win):
    ready(win)
    win.set_view("machine")
    win.wait_for_plan()
    assert frame_items(win) == [] and frame_items(win, "frame-zero") == []
    travel = [i for i in win.canvas._scene.items() if isinstance(i, QGraphicsRectItem) and i.zValue() == -20]
    assert len(travel) == 1
    assert travel[0].rect().width() == 651 and travel[0].rect().height() == 1011
    assert travel[0].pen().style() == Qt.CustomDashLine and travel[0].pen().capStyle() == Qt.RoundCap
    assert travel[0].pen().color().name() == theme.MACHINE
    win.act_frame.setChecked(False)                                           # ni al apagar y encender el boton
    win.act_frame.setChecked(True)
    win._show_machine_frame(win.last_plan)                                    # ni cuando llega un plan nuevo
    win.canvas.set_machine_frame(win.last_plan.frame, "otra")
    assert frame_items(win) == [] and frame_items(win, "frame-zero") == []
    win.set_view("drawing")
    win.wait_for_plan()
    assert len(frame_items(win)) == 1


def test_the_choice_is_remembered_on_this_pc(qtbot, tmp_path):
    ini = tmp_path / "ajustes.ini"
    w = make(qtbot, settings_file=str(ini))
    assert w.act_frame.isChecked() and w.act_grid.isChecked()                    # sin dato, encendidos
    w.act_frame.setChecked(False)
    w.act_grid.setChecked(False)
    again = make(qtbot, settings_file=str(ini))
    assert not again.act_frame.isChecked() and not again.act_grid.isChecked()
    assert not again.canvas.show_frame and not again.canvas.grid_on and frame_items(again) == []
    again.act_frame.setChecked(True)
    third = make(qtbot, settings_file=str(ini))
    assert third.act_frame.isChecked() and not third.act_grid.isChecked() and len(frame_items(third)) == 1


def test_an_empty_window_has_no_frame_and_no_grid(qtbot):
    w = make(qtbot, drawing=None)
    assert frame_items(w) == [] and w.canvas.mode == "empty"
    assert not w.canvas._grid_visible()


# --- la rejilla -----------------------------------------------------------------------------------------------------
def pixels(canvas):
    return canvas.grab().toImage()


def lines_in_row(canvas, y):
    """Cuantos pixeles de la fila `y` del lienzo no son del color del fondo."""
    image = pixels(canvas)
    bg = QColor(theme.PANEL).rgb()
    return [x for x in range(image.width()) if image.pixelColor(x, y).rgb() != bg]


def test_the_grid_is_on_by_default_and_the_canvas_paints_it(qtbot):
    w = make(qtbot, drawing=None)
    assert w.act_grid.isChecked() and w.canvas.grid_on
    w.new_drawing()
    w.act_frame.setChecked(False)
    assert w.canvas.mode == "drawing"
    row = 4                                                                      # pegado al borde de arriba: ahi no hay mas dibujo
    on = lines_in_row(w.canvas, row)
    w.act_grid.setChecked(False)
    off = lines_in_row(w.canvas, row)
    assert off == [] and len(on) >= 5                                            # sin rejilla, solo fondo; con ella, sus lineas


def lines_in_column(canvas, x):
    image = pixels(canvas)
    bg = QColor(theme.PANEL).rgb()
    return [y for y in range(image.height()) if image.pixelColor(x, y).rgb() != bg]


def test_the_horizontal_lines_are_painted_too(qtbot):
    w = make(qtbot, drawing=None)
    w.new_drawing()
    w.act_frame.setChecked(False)
    canvas = w.canvas
    col = canvas.viewport().width() - 12                                          # lejos del dibujo vacio, junto al borde derecho
    view = canvas.mapToScene(canvas.viewport().rect()).boundingRect()
    g = canvas.grid_lines(view)
    painted = lines_in_column(canvas, col)
    wanted = [canvas.mapFromScene(QPointF(0, y)).y() for y in sorted(g.y_fine + g.y_label)]
    assert wanted
    for py in wanted:
        assert any(abs(py - p) <= 1 for p in painted), py
    w.act_grid.setChecked(False)
    assert lines_in_column(canvas, col) == []


def test_each_grid_line_is_painted_where_the_scene_says(qtbot):
    w = make(qtbot, drawing=None)
    w.new_drawing()
    w.act_frame.setChecked(False)
    canvas = w.canvas
    view = canvas.mapToScene(canvas.viewport().rect()).boundingRect()
    g = canvas.grid_lines(view)
    painted = lines_in_row(canvas, 4)
    wanted = [canvas.mapFromScene(QPointF(x, 0)).x() for x in sorted(g.x_fine + g.x_label)]
    assert wanted
    for px in wanted:
        assert any(abs(px - p) <= 1 for p in painted), px


def test_the_step_follows_the_zoom(qtbot):
    w = make(qtbot, drawing=None)
    w.new_drawing()
    c = w.canvas
    ppm = c.scale_factor
    assert c.grid_step() == gr.step_for(ppm)
    c.zoom_by(0.5)
    far = c.grid_step()
    c.zoom_by(8.0)
    near = c.grid_step()
    assert far > near and gr.is_nice(far) and gr.is_nice(near)
    assert near * c.scale_factor >= gr.MIN_PIXELS - 1e-9


def test_the_mouse_grid_sets_the_visible_one(qtbot):
    w = make(qtbot, drawing=None)
    w.new_drawing()
    c = w.canvas
    c.zoom_by(8.0)
    auto = c.grid_step()
    c.tools.configure(dt.Options(grid=25.0))
    step = c.grid_step()
    assert step / 25.0 == pytest.approx(round(step / 25.0)) and step >= 25.0
    view = c.mapToScene(c.viewport().rect()).boundingRect()
    g = c.grid_lines(view)
    assert all(abs(x / step - round(x / step)) < 1e-6 for x in g.x_fine + g.x_label)
    c.tools.configure(dt.Options(grid=0.0))
    assert c.grid_step() == auto


def test_changing_the_mouse_grid_repaints_the_canvas(qtbot):
    w = make(qtbot, drawing=None)
    w.new_drawing()
    w.act_frame.setChecked(False)
    before = lines_in_row(w.canvas, 4)
    w.canvas.zoom_by(8.0)
    w.canvas.tools.configure(dt.Options(grid=100.0))
    assert lines_in_row(w.canvas, 4) != before


def test_changing_the_mouse_grid_asks_the_canvas_to_repaint_at_once(qtbot, monkeypatch):
    w = make(qtbot, drawing=None)
    w.new_drawing()
    calls = []
    monkeypatch.setattr(w.canvas.viewport(), "update", lambda *a: calls.append(a))
    w.canvas.tools.configure(dt.Options(grid=100.0))
    assert calls                                                              # sin esperar a que algo mueva la vista


def test_the_grid_also_shows_in_the_machine_view(win):
    ready(win)
    win.set_view("machine")
    win.wait_for_plan()
    assert win.canvas.mode == "machine" and win.canvas._grid_visible()
    win.act_grid.setChecked(False)
    assert not win.canvas._grid_visible()


def test_labels_and_scale_note_are_painted_without_disturbing_the_view(win):
    ready(win)
    before = win.canvas.transform(), win.canvas.centre()
    image = pixels(win.canvas)
    assert image.width() > 100
    assert (win.canvas.transform(), win.canvas.centre()) == before
    win.canvas.scroll_dummy = None
    win.canvas.horizontalScrollBar().setValue(win.canvas.horizontalScrollBar().value() + 50)
    assert pixels(win.canvas).width() == image.width()


def test_scrolling_repaints_the_whole_canvas_so_the_numbers_at_the_edge_do_not_smear(win, monkeypatch):
    calls = []
    viewport = win.canvas.viewport()
    monkeypatch.setattr(viewport, "update", lambda *a: calls.append(a))
    bar = win.canvas.horizontalScrollBar()
    bar.setValue(bar.value() + 40)
    assert calls


def test_the_grid_is_not_painted_when_there_is_only_a_message(qtbot):
    w = make(qtbot, drawing=None)
    assert w.canvas.mode == "empty" and not w.canvas._grid_visible()
    bg = QColor(theme.PANEL).rgb()
    image = pixels(w.canvas)
    assert all(image.pixelColor(x, 4).rgb() == bg for x in range(image.width()))


# --- el zoom pasa de la vista del dibujo a la de la maquina y al reves ----------------------------------------------------
def a_piece(w):
    """Una forma grande de la pieza: su centro en el dibujo y en la trayectoria (ya girado y colocado)."""
    placed = w.last_plan.shapes
    big = max(range(len(placed)), key=lambda i: placed[i].shape.area)
    twin = min(w.project.shapes(), key=lambda s: abs(s.shape.area - placed[big].shape.area))
    return twin.shape.centroid, placed[big].shape.centroid


def zoom_in_on(w, point, times=4.0):
    w.canvas.zoom_by(times)
    w.canvas.centerOn(QPointF(point.x, point.y))
    QApplication.processEvents()


def switch(w, view):
    w.set_view(view)
    w.wait_for_plan()
    QApplication.processEvents()


def test_the_zoom_and_the_zone_follow_from_the_drawing_to_the_machine_and_back(win):
    ready(win)
    c = win.canvas
    seen, placed = a_piece(win)
    zoom_in_on(win, seen)
    scale, centre = c.scale_factor, c.centre()
    switch(win, "machine")
    assert c.mode == "machine" and c.scale_factor == pytest.approx(scale)
    assert (c.centre().x(), c.centre().y()) == pytest.approx((placed.x, placed.y), abs=1.5)
    switch(win, "drawing")
    assert c.mode == "drawing" and c.scale_factor == pytest.approx(scale)
    assert (c.centre().x(), c.centre().y()) == pytest.approx((centre.x(), centre.y()), abs=1.5)


@pytest.mark.parametrize("degrees", ["90", "180", "270"])
def test_the_zone_is_the_same_when_the_job_is_turned(win, degrees):
    ready(win)
    win.job.rotate.setCurrentIndex(win.job.rotate.findData(degrees))
    win.wait_for_plan()
    c = win.canvas
    seen, placed = a_piece(win)
    zoom_in_on(win, seen)
    scale = c.scale_factor
    switch(win, "machine")
    assert c.scale_factor == pytest.approx(scale)
    assert (c.centre().x(), c.centre().y()) == pytest.approx((placed.x, placed.y), abs=1.5)


def test_changing_the_zoom_in_the_machine_view_goes_back_to_the_drawing(win):
    ready(win)
    c = win.canvas
    switch(win, "machine")
    c.zoom_by(3.0)
    QApplication.processEvents()
    scale = c.scale_factor
    switch(win, "drawing")
    assert c.scale_factor == pytest.approx(scale)


def test_going_again_to_the_view_you_are_in_keeps_the_zoom(win):
    ready(win)
    c = win.canvas
    seen, _placed = a_piece(win)
    zoom_in_on(win, seen)
    before = c.scale_factor, c.centre()
    switch(win, "drawing")
    assert c.scale_factor == pytest.approx(before[0]) and c.centre() == before[1]


def test_if_what_you_look_at_is_not_in_the_other_view_it_shows_everything(win):
    ready(win)
    c = win.canvas
    work = c.work_rect()
    c.zoom_by(4.0)
    c.centerOn(QPointF(work.right() + 3000, work.bottom() + 3000))       # muy lejos de lo dibujado
    QApplication.processEvents()
    switch(win, "machine")
    zoomed = c.scale_factor
    c.fit()
    assert zoomed == pytest.approx(c.scale_factor)                       # se ajusto, no se quedo mirando al vacio


def test_the_zoom_is_kept_when_the_hold_tab_takes_you_to_the_machine_view(win):
    ready(win)
    c = win.canvas
    seen, _placed = a_piece(win)
    zoom_in_on(win, seen)
    scale = c.scale_factor
    win.tabs.setCurrentWidget(win.hold_panel)
    win.wait_for_plan()
    assert c.mode == "machine" and c.scale_factor == pytest.approx(scale)
