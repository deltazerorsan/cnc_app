"""Pestaña Sujeción: propuesta de puntos sobre el lienzo, retoque a mano, ajustes y programa de marcas (sin pantalla)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import math
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from cnc.gui import theme
from cnc.gui.canvas import ITEM_ID, ITEM_KIND
from cnc.gui.main_window import MainWindow
from cnc.gui.hold_panel import COL_STATE, COL_X, COL_Y
from shapely.geometry import Point

from cnc.holddown import HoldSettings, order_marks
from cnc.model import Tool
from cnc.project import Project

DATA = Path(__file__).parent / "data"
BOARDS19 = DATA / "tableros_19mm.svg"


def tools():
    return {"em4": Tool(diameter=4, name="End mill 4 mm", feed_xy=300, feed_z=200, stepdown=6.5),
            "em5": Tool(diameter=5, name="End mill 5 mm", feed_xy=300, feed_z=200, stepdown=6.5)}


@pytest.fixture(autouse=True)
def no_boxes(monkeypatch):
    """Ningun cuadro de dialogo real: lo que se pregunta queda anotado y se contesta lo que diga `answers`."""
    asked, answers = [], {"question": QMessageBox.No}

    def question(parent, title, text, *a, **k):
        asked.append((title, text))
        return answers["question"]
    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    return type("Boxes", (), {"asked": asked, "answers": answers})


@pytest.fixture
def hold(qtbot, no_boxes):
    """El dibujo de 2 tableros (19 mm, fresa de 4) con la pestaña Sujeción abierta y la trayectoria calculada."""
    w = MainWindow(Project(tools()), [], use_settings=False)
    qtbot.addWidget(w)
    w.resize(1300, 800)
    w.show()
    assert w.open_svg(BOARDS19, apply_default_recipe=False)
    w.job.tool.setCurrentIndex(w.job.tool.findData("em4"))
    w.job.thickness.setText("19")
    w.job.thickness.valueChanged.emit()
    w.wait_for_plan()
    w.tabs.setCurrentWidget(w.hold_panel)
    w.boxes = no_boxes
    return w


def state_texts(w):
    return [w.hold_panel.table.item(i, COL_STATE).text() for i in range(w.hold_panel.count())]


def is_safe(text):
    """Un punto seguro sale con ✔, o con ⚠ si ademas esta mas cerca del corte de lo comodo."""
    return text.startswith(("✔", "⚠ libre"))


def points(w):
    return list(w._hold_view[1])


def free_spot(w, far_from=()):
    """Un sitio libre de la plancha (el centro de la zona libre mas grande que no quede encima de otro punto)."""
    analysis = w._hold_view[0]
    geoms = list(analysis.free.geoms) if hasattr(analysis.free, "geoms") else [analysis.free]
    for g in sorted(geoms, key=lambda g: -g.area):
        p = g.representative_point()
        if all(abs(p.x - x) + abs(p.y - y) > 5 for x, y in far_from):
            return round(p.x, 1), round(p.y, 1)
    raise AssertionError("no hay sitio libre")


def candidates_near_cut(analysis, dists):
    """Puntos que valen y estan a esas distancias del eje de la trayectoria, lejos del borde de la plancha (para que
    solo cuente el margen al corte)."""
    inner = analysis.zones.board.buffer(-30)
    for dist in dists:
        shape = analysis.zones.axis.buffer(dist)
        for g in (shape.geoms if hasattr(shape, "geoms") else [shape]):
            for ring in [g.exterior, *g.interiors]:
                for i in range(400):
                    p = ring.interpolate(i / 400, normalized=True)
                    if inner.contains(p) and analysis.check(p.x, p.y).ok:
                        yield round(p.x, 1), round(p.y, 1)


def spot_near_cut(w, dist):
    """Un punto que vale ahora y esta a `dist` mm del eje de la trayectoria."""
    for spot in candidates_near_cut(w._hold_view[0], [dist]):
        return spot
    raise AssertionError("no encuentro un punto de prueba")


def analysis_with_tool(tool_id):
    """El analisis de sujecion del tablero 1 del dibujo de 19 mm con otra fresa, calculado sin la ventana."""
    from dataclasses import replace
    from cnc.holddown import analyse_plan
    from cnc.preview import preview_ops
    p = Project(tools())
    p.load_svg(BOARDS19)
    p.general = replace(p.general, tool=tool_id, thickness=19.0)
    plan = p.plan(1)
    assert plan.ok, plan.error
    return analyse_plan(plan, preview_ops(plan), HoldSettings())


def unsafe_spot(w):
    """Un punto dentro de una pieza (nunca se clava ahi)."""
    analysis = w._hold_view[0]
    z = analysis.zones
    piece = max(z.pieces, key=lambda g: g.area)
    p = piece.representative_point()
    assert not analysis.check(p.x, p.y).ok
    return round(p.x, 1), round(p.y, 1)


# --- abrir la pestaña ----------------------------------------------------------------------------------------
def test_opening_the_tab_shows_the_machine_view_with_the_proposal(hold):
    w = hold
    assert w.view == "machine" and w.canvas.hold_mode
    n = w.hold_panel.count()
    assert n >= 4 and w.canvas.hold_item_count() == n == len(points(w))
    assert all(t.startswith("✔") for t in state_texts(w))
    assert "propuestos" in w.hold_panel.info.text() and "✔" in w.hold_panel.info.text()
    assert w.hold_panel.btn_marks.isEnabled() and w.hold_panel.table.isEnabled()
    x, y = (w.hold_panel.table.item(0, c).text() for c in (COL_X, COL_Y))
    assert (float(x), float(y)) == points(w)[0]


def test_the_proposal_is_the_same_as_the_core_gives(hold):
    from cnc.holddown import analyse_plan
    w = hold
    ref = analyse_plan(w.last_plan, w.last_previews, w.project.hold)
    assert points(w) == ref.suggest()


def test_leaving_the_tab_takes_the_layer_off_and_coming_back_puts_it_on(hold):
    w = hold
    n = w.canvas.hold_item_count()
    w.tabs.setCurrentIndex(0)
    assert not w.canvas.hold_mode and w.canvas.hold_item_count() == 0 and w._hold_view is None
    w.tabs.setCurrentWidget(w.hold_panel)
    assert w.canvas.hold_mode and w.canvas.hold_item_count() == n


def test_the_bottom_pane_grows_for_the_table_and_goes_back_when_leaving(qtbot, no_boxes):
    w = MainWindow(Project(tools()), [], use_settings=False)
    qtbot.addWidget(w)
    w.resize(1300, 800)
    w.show()
    before = w.splitter.sizes()
    assert before[1] < 300
    w.tabs.setCurrentWidget(w.hold_panel)
    assert w.splitter.sizes()[1] >= 330
    w.tabs.setCurrentIndex(0)
    assert abs(w.splitter.sizes()[1] - before[1]) <= 3


def test_a_pane_moved_by_hand_is_left_as_it_is_when_leaving(qtbot, no_boxes):
    w = MainWindow(Project(tools()), [], use_settings=False)
    qtbot.addWidget(w)
    w.resize(1300, 800)
    w.show()
    w.tabs.setCurrentWidget(w.hold_panel)
    total = sum(w.splitter.sizes())
    w.splitter.setSizes([total - 500, 500])
    w.tabs.setCurrentIndex(0)
    assert w.splitter.sizes()[1] >= 495


def test_a_tall_enough_pane_is_not_touched(qtbot, no_boxes):
    w = MainWindow(Project(tools()), [], use_settings=False)
    qtbot.addWidget(w)
    w.resize(1300, 900)
    w.show()
    total = sum(w.splitter.sizes())
    w.splitter.setSizes([total - 420, 420])
    w.tabs.setCurrentWidget(w.hold_panel)
    assert abs(w.splitter.sizes()[1] - 420) <= 3
    w.tabs.setCurrentIndex(0)
    assert abs(w.splitter.sizes()[1] - 420) <= 3


def test_the_tab_moves_the_view_to_the_machine_from_the_drawing(qtbot, no_boxes):
    w = MainWindow(Project(tools()), [], use_settings=False)
    qtbot.addWidget(w)
    w.show()
    assert w.open_svg(BOARDS19, apply_default_recipe=False)
    w.job.tool.setCurrentIndex(w.job.tool.findData("em4"))
    w.job.thickness.setText("19")
    w.job.thickness.valueChanged.emit()
    w.wait_for_plan()
    assert w.view == "drawing"
    w.tabs.setCurrentWidget(w.hold_panel)
    assert w.view == "machine" and w.canvas.mode == "machine" and w.canvas.hold_item_count() > 0


# --- el corte en varias pasadas -------------------------------------------------------------------------------------
@pytest.mark.parametrize("stepdown", ["", "8", "4,85"])
def test_with_any_number_of_passes_no_point_goes_on_a_piece(qtbot, no_boxes, stepdown):
    """Regresion: con un numero par de pasadas (aqui 4) la ventana proponia 19 puntos encima de las letras."""
    w = MainWindow(Project(tools()), [], use_settings=False)
    qtbot.addWidget(w)
    w.resize(1300, 800)
    w.show()
    assert w.open_svg(DATA / "tablero_610x1015.svg", apply_default_recipe=False)
    w.job.tool.setCurrentIndex(w.job.tool.findData("em4"))
    w.job.thickness.setText("19")
    w.job.thickness.valueChanged.emit()
    w.job.stepdown.setText(stepdown)
    w.job.stepdown.valueChanged.emit()
    w.wait_for_plan()
    w.tabs.setCurrentWidget(w.hold_panel)
    analysis, pts, _ = w._hold_view
    assert (len(analysis.zones.pieces), len(analysis.zones.islands)) == (15, 6)
    assert 4 <= len(pts) <= 14 and w.hold_panel.count() == len(pts) == w.canvas.hold_item_count()
    assert all(t.startswith("✔") for t in state_texts(w)) and "no es(son) seguro(s)" not in w.hold_panel.info.text()
    assert analysis.free.area < analysis.zones.board.area * 0.7          # las letras no cuentan como sitio libre


# --- sin datos para calcular -------------------------------------------------------------------------------------
def test_without_a_drawing_the_tab_says_so_and_is_disabled(qtbot):
    w = MainWindow(Project(tools()), [], use_settings=False)
    qtbot.addWidget(w)
    w.tabs.setCurrentWidget(w.hold_panel)
    assert not w.hold_panel.btn_marks.isEnabled() and not w.hold_panel.table.isEnabled()
    assert "Abre un dibujo" in w.hold_panel.info.text() and w.hold_panel.count() == 0
    assert w.canvas.hold_item_count() == 0 and w.generate_marks() is None


def test_a_plan_that_fails_says_why_and_shows_no_points(hold):
    w = hold
    w.job.thickness.setText("")
    w.job.thickness.valueChanged.emit()
    w.wait_for_plan()
    assert w.last_plan.error and w.hold_panel.count() == 0 and w.canvas.hold_item_count() == 0
    assert "No hay trayectoria que respetar" in w.hold_panel.info.text() and not w.hold_panel.btn_marks.isEnabled()
    w.job.thickness.setText("19")
    w.job.thickness.valueChanged.emit()
    w.wait_for_plan()
    assert w.hold_panel.count() >= 4 and w.hold_panel.btn_marks.isEnabled()          # al arreglarlo vuelve solo


def test_a_drawing_without_boards_is_held_on_an_assumed_board(hold):
    """Sin recuadro se supone la plancha (del cero a lo que se corta mas el margen): hay zona libre y se puede sujetar."""
    w = hold
    w.detect_boards.setChecked(False)
    w.wait_for_plan()
    assert w.last_plan.virtual_stock and len(w.last_plan.stock) == 1
    assert "Sin recuadro" in w.hold_panel.info.text() and w.hold_panel.count() >= 1
    assert w.canvas.hold_item_count() == w.hold_panel.count()


def test_a_drawing_without_boards_and_with_the_zero_elsewhere_cannot_be_held_and_says_so(hold):
    from dataclasses import replace
    w = hold
    w.detect_boards.setChecked(False)
    w.project.general = replace(w.project.general, origin="center")          # el cero no esta en la esquina: no hay tablero que suponer
    w.job.thickness.valueChanged.emit()
    w.wait_for_plan()
    assert not w.last_plan.stock and not w.last_plan.virtual_stock
    assert "no tiene tablero" in w.hold_panel.info.text() and w.hold_panel.count() == 0
    assert w.canvas.hold_item_count() == 0


# --- clic en el lienzo --------------------------------------------------------------------------------------------
def test_adding_a_point_on_a_free_spot_keeps_it_and_selects_it(hold):
    w = hold
    before = points(w)
    spot = free_spot(w, far_from=before)
    assert w.hold_add(*spot) is True
    after = points(w)
    assert after == before + [spot] and w.hold_panel.count() == len(after) == w.canvas.hold_item_count()
    assert w.project.hold_points[w.current] == after and "editados a mano" in w.hold_panel.info.text()
    assert w.hold_panel.current() == len(after) - 1 and "añadido" in w.statusBar().currentMessage()


def test_points_are_kept_to_a_tenth_of_a_millimetre(hold):
    w = hold
    x, y = free_spot(w, far_from=points(w))
    assert w.hold_add(x + 0.0431, y - 0.0372)
    assert points(w)[-1] == (x, y)
    w.hold_edit(0, 100.04999, 200.0501)                      # lo escrito en la tabla tambien se redondea
    assert points(w)[0] == (100.0, 200.1)


def test_adding_a_point_where_it_is_not_safe_is_refused_with_the_reason(hold):
    w = hold
    before = points(w)
    x, y = unsafe_spot(w)
    assert w.hold_add(x, y) is False
    assert points(w) == before and w.current not in w.project.hold_points
    msg = w.statusBar().currentMessage()
    assert "no se puede sujetar ahí" in msg and "pieza" in msg


def test_removing_a_point_by_number_and_with_the_remove_button(hold):
    w = hold
    before = points(w)
    w.hold_remove(0)
    assert points(w) == before[1:] and w.canvas.hold_item_count() == len(before) - 1
    w.hold_remove(99)                                                         # no existe: no pasa nada
    assert points(w) == before[1:]
    w.hold_panel.select(1)
    w.hold_panel.btn_remove.click()
    assert points(w) == [before[1]] + before[3:]
    assert all(t.startswith("✔") for t in state_texts(w))


def test_a_real_click_on_the_canvas_adds_a_point_and_a_click_on_a_dot_removes_it(hold):
    w = hold
    c = w.canvas
    before = points(w)
    spot = free_spot(w, far_from=before)
    QTest.mouseClick(c.viewport(), Qt.LeftButton, Qt.NoModifier, c.mapFromScene(*spot))
    assert points(w)[:-1] == before and len(points(w)) == len(before) + 1
    new = points(w)[-1]
    assert abs(new[0] - spot[0]) < 3 and abs(new[1] - spot[1]) < 3                           # el clic cae en un pixel
    QTest.mouseClick(c.viewport(), Qt.LeftButton, Qt.NoModifier, c.mapFromScene(*new))       # encima del punto nuevo
    assert points(w) == before


def test_a_drag_to_pan_does_not_add_points(hold):
    w = hold
    before = points(w)
    c = w.canvas
    start = c.mapFromScene(*free_spot(w, far_from=before))
    QTest.mousePress(c.viewport(), Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(c.viewport(), start + QPoint(40, 30))
    QTest.mouseRelease(c.viewport(), Qt.LeftButton, Qt.NoModifier, start + QPoint(40, 30))
    assert points(w) == before


def test_hovering_says_if_you_can_hold_there_and_why_not(hold):
    w = hold
    spot = free_spot(w, far_from=points(w))
    w.canvas.holdHovered.emit(*spot)
    assert "se puede sujetar aquí" in w.statusBar().currentMessage() and w.canvas._ghost is not None
    green = w.canvas._ghost.pen().color().name()
    w.canvas.holdHovered.emit(*unsafe_spot(w))
    assert "pieza" in w.statusBar().currentMessage() and w.canvas._ghost.pen().color().name() != green
    w.canvas.holdLeft.emit()
    assert w.statusBar().currentMessage() == ""


# --- tabla -----------------------------------------------------------------------------------------------------
def type_cell(w, row, col, text):
    item = w.hold_panel.table.item(row, col)
    item.setText(text)                       # dispara itemChanged como al escribir en la celda


def test_typing_a_coordinate_moves_the_point_and_checks_it(hold):
    w = hold
    spot = free_spot(w, far_from=points(w))
    type_cell(w, 0, COL_X, f"{spot[0]}".replace(".", ","))                    # con coma, como se escribe en España
    type_cell(w, 0, COL_Y, f"{spot[1]}")
    assert points(w)[0] == spot and w.project.hold_points[w.current][0] == spot
    assert state_texts(w)[0].startswith("✔")


def test_a_typed_point_in_an_unsafe_place_is_kept_but_shown_in_red(hold):
    w = hold
    x, y = unsafe_spot(w)
    type_cell(w, 1, COL_X, str(x))
    type_cell(w, 1, COL_Y, str(y))
    assert points(w)[1] == (x, y) and state_texts(w)[1].startswith("✖") and "pieza" in state_texts(w)[1]
    assert "no es(son) seguro(s)" in w.hold_panel.info.text() and "✖ 1" in w.hold_panel.info.text()
    assert w.canvas.hold_item_count() == w.hold_panel.count()


def test_something_that_is_not_a_number_leaves_the_table_as_it_was(hold):
    w = hold
    before = points(w)
    type_cell(w, 0, COL_X, "abc")
    assert points(w) == before and w.hold_panel.table.item(0, COL_X).text() == f"{before[0][0]:.1f}"
    assert w.current not in w.project.hold_points


def test_reset_goes_back_to_the_proposal_for_this_board_only(hold):
    w = hold
    proposal = points(w)
    w.hold_remove(0)
    w.hold_add(*free_spot(w, far_from=points(w)))
    assert points(w) != proposal
    w.hold_panel.btn_reset.click()
    assert points(w) == proposal and w.current not in w.project.hold_points
    assert "propuestos" in w.hold_panel.info.text()


def test_each_board_has_its_own_points(hold):
    w = hold
    one = points(w)
    w.hold_remove(0)
    edited = points(w)
    w.boards.setCurrentRow(1)
    w.wait_for_plan()
    assert w.current == 2 and w.hold_panel.count() > 0
    two = points(w)
    assert two != edited and 1 in w.project.hold_points and 2 not in w.project.hold_points
    w.boards.setCurrentRow(0)
    w.wait_for_plan()
    assert points(w) == edited != one


def test_changing_board_does_not_show_the_points_of_the_other_one_while_calculating(hold):
    w = hold
    w.boards.setCurrentRow(1)                         # el plan del tablero nuevo aun no ha llegado
    assert w.last_plan is None and w.hold_panel.count() == 0 and w.canvas.hold_item_count() == 0
    assert "Calculando" in w.hold_panel.info.text()
    w.wait_for_plan()
    assert w.hold_panel.count() > 0


# --- ajustes ----------------------------------------------------------------------------------------------------
def test_changing_to_screws_changes_the_margins_the_proposal_and_the_project(hold):
    w = hold
    set_setting(w, "cut_clear", "0")                    # con la holgura comoda de siempre los margenes no se notan
    nails = points(w)
    w.hold_panel.kind.setCurrentIndex(w.hold_panel.kind.findData("tirafondo"))
    assert w.project.hold.kind == "tirafondo"
    assert w.hold_panel.kerf.placeholderText().startswith("10") and w.hold_panel.edge.placeholderText().startswith("15")
    screws = points(w)
    assert w._hold_view[0].settings.kind == "tirafondo" and screws and all(t.startswith("✔") for t in state_texts(w))
    for (x, y) in screws:                           # con el margen al borde de tirafondo (15), ninguno en la banda de 15 mm
        assert x >= 15 and y >= 15
    assert nails != screws                          # otros margenes, otra propuesta


def test_every_box_of_the_panel_reaches_the_project_and_an_empty_one_means_the_default(hold):
    w = hold
    set_setting(w, "reach", "250")
    set_setting(w, "kerf", "9")
    set_setting(w, "edge", "20,5")
    set_setting(w, "mark_z", "8")
    h = w.project.hold
    assert (h.kind, h.reach, h.kerf_margin, h.edge_margin, h.mark_z) == ("clavo", 250.0, 9.0, 20.5, 8.0)
    set_setting(w, "kerf", "")
    set_setting(w, "edge", "")
    set_setting(w, "reach", "")
    h = w.project.hold
    assert (h.kerf_margin, h.edge_margin, h.reach) == (None, None, 300.0)          # el margen del tipo y el alcance de siempre


def test_the_proposal_boxes_reach_the_project_and_an_empty_one_means_the_default(hold):
    w = hold
    set_setting(w, "min_gap", "230")
    set_setting(w, "corner_clear", "130,5")
    set_setting(w, "min_points", "6,6")                         # el minimo es un numero entero: se redondea
    h = w.project.hold
    assert (h.min_gap, h.corner_clear, h.min_points) == (230.0, 130.5, 7) and isinstance(h.min_points, int)
    for field in ("min_gap", "corner_clear", "min_points"):
        set_setting(w, field, "")
    d, h = HoldSettings(), w.project.hold
    assert (h.min_gap, h.corner_clear, h.min_points) == (d.min_gap, d.corner_clear, d.min_points)


def test_the_cut_distance_the_bottom_row_and_the_grid_reach_the_project_and_an_empty_box_means_the_default(hold):
    w = hold
    d = HoldSettings()
    assert (w.project.hold.cut_clear, w.project.hold.bottom_row, w.project.hold.grid) == (d.cut_clear, d.bottom_row, True)
    set_setting(w, "cut_clear", "27,5")
    set_setting(w, "bottom_row", "65")
    w.hold_panel.grid.setChecked(False)
    h = w.project.hold
    assert (h.cut_clear, h.bottom_row, h.grid) == (27.5, 65.0, False)
    set_setting(w, "cut_clear", "")
    set_setting(w, "bottom_row", "")
    w.hold_panel.grid.setChecked(True)
    h = w.project.hold
    assert (h.cut_clear, h.bottom_row, h.grid) == (d.cut_clear, d.bottom_row, True)
    set_setting(w, "bottom_row", "0")                           # 0 es un valor, no "vacio": sin fila pegada al borde
    assert w.project.hold.bottom_row == 0.0


def test_each_new_proposal_box_changes_the_proposal_the_way_it_says(hold):
    w = hold
    base = points(w)
    assert len(base) >= 6
    assert min(w._hold_view[0].zones.axis.distance(Point(*p)) - 2.0 for p in base) >= 20 - 1e-6
    set_setting(w, "cut_clear", "0")
    assert w.project.hold.cut_clear == 0 and points(w) != base                       # sin pedir holgura, otros sitios
    set_setting(w, "cut_clear", "")
    assert points(w) == base
    w.hold_panel.grid.setChecked(False)
    assert points(w) != base and not w.project.hold.grid                             # repartidos libres, no en filas
    w.hold_panel.grid.setChecked(True)
    assert points(w) == base
    set_setting(w, "bottom_row", "0")
    assert points(w) != base and w.project.hold.bottom_row == 0                       # sin fila pegada al borde: otros sitios
    set_setting(w, "bottom_row", "")
    assert points(w) == base


def test_a_point_nearer_the_cut_than_is_comfortable_shows_a_warning_in_the_table_the_info_and_the_status_bar(hold):
    w = hold
    spot = spot_near_cut(w, 2.0 + 8.0)                                  # a 8 mm del borde del corte: vale (clavo: 6) pero no es comodo
    assert w.hold_add(*spot)
    text = state_texts(w)[-1]
    assert text.startswith("⚠ libre") and "8.0 mm del corte" in text and "20" in text
    last = w.hold_panel.count() - 1
    assert w.hold_panel.table.item(last, COL_STATE).foreground().color().name() == QColor(theme.WARN).name()
    assert all(t.startswith("✔") for t in state_texts(w)[:-1])
    info = w.hold_panel.info.text()
    assert f"Punto {last + 1} más cerca del corte de lo cómodo (20 mm)" in info and "no es(son) seguro(s)" not in info
    assert dots(w)[last].brush().color().name() == QColor(theme.OK).name()        # sigue siendo un punto valido
    w._hold_hover(*spot)
    assert "se puede sujetar aquí, pero a 8.0 mm del corte" in w.statusBar().currentMessage()
    set_setting(w, "cut_clear", "5")                                              # con 5 mm de holgura ya es comodo
    assert state_texts(w)[-1].startswith("✔") and "más cerca del corte" not in w.hold_panel.info.text()


def test_the_info_names_every_point_that_is_nearer_the_cut_than_is_comfortable(hold):
    w = hold
    first = w._hold_view[0]
    a, b = spot_near_cut(w, 2.0 + 8.0), spot_near_cut(w, 2.0 + 9.0)
    assert w.hold_add(*a) and w.hold_add(*b) and a != b
    n = w.hold_panel.count()
    assert f"Puntos {n - 1}, {n} más cerca del corte" in w.hold_panel.info.text()


def corner_distance(p, w=620, h=1000):
    return min(math.hypot(p[0] - cx, p[1] - cy) for cx, cy in ((0, 0), (w, 0), (w, h), (0, h)))


def test_each_proposal_box_changes_the_proposal_the_way_it_says(hold):
    w = hold
    base = points(w)
    assert len(base) >= 6 and min(corner_distance(p) for p in base) >= 100
    set_setting(w, "min_points", "10")
    assert len(points(w)) >= 10 > len(base)
    set_setting(w, "min_points", "")
    assert points(w) == base                                     # vacio: vuelve la propuesta de siempre
    set_setting(w, "corner_clear", "250")
    assert min(corner_distance(p) for p in points(w)) >= 250 - 1e-6 and points(w) != base
    set_setting(w, "corner_clear", "")
    set_setting(w, "min_gap", "300")
    assert w._hold_view[0].settings.min_gap == 300 and points(w) != base
    set_setting(w, "min_gap", "")
    assert points(w) == base


def test_unsafe_points_are_red_and_good_ones_green_on_the_canvas_and_in_the_table(hold):
    w = hold
    x, y = unsafe_spot(w)
    type_cell(w, 1, COL_X, str(x))
    type_cell(w, 1, COL_Y, str(y))
    d = dots(w)
    assert d[1].brush().color().name() == QColor(theme.ERROR).name() and d[0].brush().color().name() == QColor(theme.OK).name()
    table = w.hold_panel.table
    assert table.item(1, COL_STATE).foreground().color().name() == QColor(theme.ERROR).name()
    assert table.item(0, COL_STATE).foreground().color().name() == QColor(theme.OK).name()
    assert "no es seguro" in d[1].toolTip() and "no es seguro" not in d[0].toolTip()


def test_the_grey_hints_of_the_panel_are_the_defaults_it_falls_back_to(hold):
    p = hold.hold_panel
    d = HoldSettings()
    assert p.reach.placeholderText() == f"{d.reach:g}" and p.mark_z.placeholderText() == f"{d.mark_z:g}"
    assert (p.min_gap.placeholderText(), p.corner_clear.placeholderText(), p.min_points.placeholderText()) == (
        f"{d.min_gap:g}", f"{d.corner_clear:g}", f"{d.min_points:g}")
    assert (p.cut_clear.placeholderText(), p.bottom_row.placeholderText()) == (f"{d.cut_clear:g}", f"{d.bottom_row:g}")
    set_setting(hold, "mark_z", "")
    assert hold.project.hold.mark_z == d.mark_z


def test_the_hold_layer_is_not_drawn_over_the_drawing_view(hold):
    w = hold
    w.set_view("drawing")
    assert w.canvas.mode == "drawing" and w.canvas.hold_item_count() == 0
    w.job.thickness.setText("18")                                 # un plan nuevo llega con la vista del dibujo puesta
    w.job.thickness.valueChanged.emit()
    w.wait_for_plan()
    assert w.last_plan.ok and w.canvas.hold_item_count() == 0 and not [i for i in w.canvas.scene().items()
                                                                       if i.data(ITEM_KIND) == "hold"]
    w.set_view("machine")
    assert w.canvas.hold_item_count() == w.hold_panel.count() > 0


def test_a_setting_that_does_not_make_sense_is_reported_and_not_applied(hold):
    w = hold
    before = (w.project.hold, points(w))
    w.hold_panel.reach.setText("0")
    w.hold_panel.reach.valueChanged.emit()
    assert "alcance" in w.hold_panel.info.text() and w.project.hold == before[0] and points(w) == before[1]
    w.hold_panel.reach.setText("200")
    w.hold_panel.reach.valueChanged.emit()
    assert w.project.hold.reach == 200 and len(points(w)) > len(before[1])        # 200 mm pide mas puntos que 300


def test_edited_points_stay_when_the_settings_change_and_are_checked_again(hold):
    w = hold
    spot = spot_near_cut(w, 2.0 + 6.5)                  # a 6.5 mm del borde del corte: vale para clavo (6), no para tirafondo (10)
    assert w._hold_view[0].zones.board.buffer(-30).contains(__import__("shapely").geometry.Point(*spot))
    assert w.hold_add(*spot)
    assert is_safe(state_texts(w)[-1])
    w.hold_panel.kind.setCurrentIndex(w.hold_panel.kind.findData("tirafondo"))
    assert points(w)[-1] == spot and state_texts(w)[-1].startswith("✖")            # sigue ahi, pero ya no vale
    assert "corte" in state_texts(w)[-1]
    w.hold_panel.kind.setCurrentIndex(w.hold_panel.kind.findData("clavo"))
    assert points(w)[-1] == spot and is_safe(state_texts(w)[-1])


def test_a_bigger_tool_makes_a_point_unsafe_when_the_path_changes(hold):
    w = hold
    other = analysis_with_tool("em5")
    spot = next((c for c in candidates_near_cut(w._hold_view[0], [2.0 + 6.2, 2.0 + 6.5, 2.0 + 6.8, 2.0 + 7.0])
                 if not other.check(*c).ok), None)
    assert spot is not None, "no hay un punto que cambie de valer a no valer con la fresa de 5 mm"
    assert w.hold_add(*spot)
    n = w.hold_panel.count()
    assert is_safe(state_texts(w)[-1])
    w.job.tool.setCurrentIndex(w.job.tool.findData("em5"))
    w.wait_for_plan()
    assert w.last_plan.ok and w.last_plan.job.tool.diameter == 5
    assert w.hold_panel.count() == n and points(w)[-1] == spot
    assert state_texts(w)[-1].startswith("✖") and "No hay" not in w.hold_panel.info.text()
    assert state_texts(w)[-1] == "✖ " + other.check(*spot).reason                 # lo mismo que dice el calculo con esa fresa


def test_redrawing_with_the_same_plan_does_not_build_the_zones_again(hold, monkeypatch):
    from cnc.gui import main_window
    calls = []
    real = main_window.analyse_plan
    monkeypatch.setattr(main_window, "analyse_plan", lambda *a, **k: calls.append(1) or real(*a, **k))
    w = hold
    w._update_hold()
    w.hold_remove(0)                                   # editar puntos repinta, pero la geometria es la misma
    w.hold_add(*free_spot(w, far_from=points(w)))
    w.canvas.holdHovered.emit(10.0, 10.0)
    assert calls == []


def test_nothing_is_calculated_while_another_tab_is_in_front(hold, monkeypatch):
    from cnc.gui import main_window
    w = hold
    w.tabs.setCurrentIndex(0)
    calls = []
    real = main_window.analyse_plan
    monkeypatch.setattr(main_window, "analyse_plan", lambda *a, **k: calls.append(1) or real(*a, **k))
    w.job.thickness.setText("18")
    w.job.thickness.valueChanged.emit()
    w.wait_for_plan()
    assert w.last_plan.ok and calls == [] and w.canvas.hold_item_count() == 0
    w.tabs.setCurrentWidget(w.hold_panel)               # al volver a la pestaña si se calcula
    assert len(calls) == 1 and w.hold_panel.count() > 0


def test_a_project_that_starts_with_its_own_hold_settings_shows_them(qtbot, no_boxes):
    project = Project(tools())
    project.hold = HoldSettings(kind="tirafondo", reach=250.0, kerf_margin=11.0, edge_margin=17.0, mark_z=8.0,
                                min_gap=230.0, corner_clear=120.0, min_points=8, cut_clear=27.5, grid=False, bottom_row=65.0)
    w = MainWindow(project, [], use_settings=False)
    qtbot.addWidget(w)
    p = w.hold_panel
    assert (p.kind.currentData(), p.reach.value(), p.kerf.value(), p.edge.value(), p.mark_z.value()) == (
        "tirafondo", 250.0, 11.0, 17.0, 8.0)
    assert (p.min_gap.value(), p.corner_clear.value(), p.min_points.value()) == (230.0, 120.0, 8.0)
    assert (p.cut_clear.value(), p.bottom_row.value(), p.grid.isChecked()) == (27.5, 65.0, False)


def test_a_change_that_does_not_move_the_geometry_keeps_the_calculated_proposal(hold):
    w = hold
    first = w._hold_view[0]
    w.job.feed.setText("250")
    w.job.feed.valueChanged.emit()
    w.wait_for_plan()
    assert w._hold_view[0] is first


def test_a_change_that_moves_the_geometry_calculates_it_again(hold):
    w = hold
    first = w._hold_view[0]
    w.job.tool.setCurrentIndex(w.job.tool.findData("em5"))
    w.wait_for_plan()
    assert w._hold_view[0] is not first and w._hold_view[0].key != first.key


def set_setting(w, field, text):
    box = getattr(w.hold_panel, field)
    box.setText(text)
    box.valueChanged.emit()


def dots(w):
    found = [i for i in w.canvas.scene().items() if i.data(ITEM_KIND) == "hold"]
    return {int(i.data(ITEM_ID)): i for i in found}


def test_the_info_says_everything_is_covered_and_how_far_the_worst_place_is(hold):
    text = hold.hold_panel.info.text()
    assert "Ningún sitio de la plancha a más de 300 mm" in text and "el más lejos" in text and "Hay sitios a" not in text


def test_the_info_warns_when_a_place_is_farther_than_the_reach(hold):
    w = hold
    for _ in range(w.hold_panel.count() - 1):
        w.hold_remove(0)
    text = w.hold_panel.info.text()
    assert "Hay sitios a" in text and "alcance 300 mm" in text and "Ningún sitio de la plancha" not in text
    assert "1 punto(s)" in text


def test_the_info_says_when_the_minimum_number_of_points_does_not_fit(hold):
    w = hold
    assert "mínimo para una plancha" not in w.hold_panel.info.text()
    set_setting(w, "min_points", "30")
    text = w.hold_panel.info.text()
    assert "Solo se han podido proponer" in text and "es 30" in text and "200 mm o más de los demás" in text


def test_the_info_warns_when_the_points_edited_by_hand_are_fewer_than_the_minimum(hold):
    w = hold
    while w.hold_panel.count() > 5:
        w.hold_remove(0)
    text = w.hold_panel.info.text()
    assert "Hay 5 punto(s) seguro(s) y el mínimo para una plancha de este tamaño es 6" in text
    assert "Solo se han podido proponer" not in text
    set_setting(w, "min_points", "5")                             # con un minimo de 5 ya no hay nada que avisar
    assert "mínimo para una plancha" not in w.hold_panel.info.text()


def test_an_unsafe_point_does_not_count_towards_the_minimum_number_of_points(hold):
    w = hold
    while w.hold_panel.count() > 6:
        w.hold_remove(0)
    assert "mínimo para una plancha" not in w.hold_panel.info.text()
    x, y = unsafe_spot(w)
    type_cell(w, 1, COL_X, str(x))
    type_cell(w, 1, COL_Y, str(y))
    assert "Hay 5 punto(s) seguro(s) y el mínimo para una plancha de este tamaño es 6" in w.hold_panel.info.text()


def test_an_unsafe_point_does_not_count_as_holding_anything(hold):
    """Se sustituye un punto necesario por otro MALO que, por distancia, cubriria igual: no debe contar."""
    import math
    w = hold
    analysis, pts, _ = w._hold_view
    case = None
    for i in [i for i in range(len(pts)) if not analysis.gap_ok(pts[:i] + pts[i + 1:])]:
        for r in (10, 20, 30, 45):
            for k in range(16):
                ang = 2 * math.pi * k / 16
                u = (round(pts[i][0] + r * math.cos(ang), 1), round(pts[i][1] + r * math.sin(ang), 1))
                if (0 <= u[0] <= 620 and 0 <= u[1] <= 1000 and not analysis.check(*u).ok
                        and analysis.gap_ok(pts[:i] + [u] + pts[i + 1:])):
                    case = (i, u)
                    break
            if case:
                break
        if case:
            break
    assert case, "no hay un punto malo que cubra igual que uno necesario"
    i, (x, y) = case
    type_cell(w, i, COL_X, str(x))
    type_cell(w, i, COL_Y, str(y))
    assert state_texts(w)[i].startswith("✖")
    text = w.hold_panel.info.text()
    assert "Hay sitios a" in text and "Ningún sitio de la plancha" not in text and "no es(son) seguro(s)" in text


def test_the_info_warns_about_a_big_scrap_where_no_point_fits(hold):
    w = hold
    assert "trozo(s) de desecho" not in w.hold_panel.info.text()
    set_setting(w, "kerf", "45")                                # con 45 mm al corte, un trozo de 182 cm2 se queda sin sitio
    text = w.hold_panel.info.text()
    assert "1 trozo(s) de desecho grande(s)" in text and "182 cm²" in text


def test_the_info_says_when_the_margins_leave_no_room_at_all(hold):
    w = hold
    set_setting(w, "kerf", "400")
    assert w.hold_panel.count() == 0 and w.canvas.hold_item_count() == 0
    assert "no queda sitio libre" in w.hold_panel.info.text() and not w.hold_panel.btn_marks.isEnabled()
    set_setting(w, "kerf", "")                                  # vacio: vuelve el margen del tipo
    assert w.hold_panel.count() > 0 and "no queda sitio" not in w.hold_panel.info.text()


def test_the_selected_row_is_highlighted_on_the_canvas(hold):
    w = hold
    w.hold_panel.select(2)
    d = dots(w)
    assert d[2].pen().color().name() == QColor(theme.TEXT).name() and d[0].pen().color().name() == "#ffffff"
    w.hold_panel.select(0)
    d = dots(w)
    assert d[0].pen().color().name() == QColor(theme.TEXT).name() and d[2].pen().color().name() == "#ffffff"


# --- programa de marcas -------------------------------------------------------------------------------------------
def test_the_marks_program_is_written_next_to_the_job_with_every_point(hold, tmp_path):
    w = hold
    target = tmp_path / "job19_tablero1_marcas.nc"
    out = w.generate_marks(target, confirm=False)
    assert out == target and target.exists()
    text = target.read_text(encoding="ascii")
    n = w.hold_panel.count()
    lines = text.splitlines()
    assert lines[0].startswith("(Marcas de sujecion: Tablero 1") and lines[-1] == "M30"
    assert sum(1 for l in lines if l == "M0") == n and sum(1 for l in lines if l.startswith("(Punto ")) == n
    visited = [l for l in lines if l.startswith("G0 X")]
    assert visited == [f"G0 X{x:g} Y{y:g}" for x, y in order_marks(points(w))]              # de lejos a cerca
    assert visited == [f"G0 X{x:g} Y{y:g}" for x, y in points(w)]                           # la propuesta ya viene asi
    assert "Y mayor a menor" in " ".join(w.messages.texts())
    assert not any(l.split()[0] in ("M3", "M4", "M03", "M04") for l in lines if l and not l.startswith("("))   # el husillo no arranca
    assert "Marcas guardadas" in " ".join(w.messages.texts()) and "Reanudar" in " ".join(w.messages.texts())


def test_the_marks_program_follows_the_job_end_flags(hold, tmp_path):
    w = hold
    w.project.general.home_at_end = True
    text = w.project.marks_text(w.current, points(w))
    assert "$H" in text
    w.project.general.home_at_end = False
    w.project.general.return_to_origin = True
    text = w.project.marks_text(w.current, points(w))
    assert "$H" not in text and "G0 X0 Y0" in text


def test_the_marks_use_the_edited_points(hold, tmp_path):
    w = hold
    w.hold_remove(0)
    spot = free_spot(w, far_from=points(w))
    w.hold_add(*spot)
    out = w.generate_marks(tmp_path / "m.nc", confirm=False)
    lines = out.read_text(encoding="ascii").splitlines()
    assert [l for l in lines if l.startswith("G0 X")] == [f"G0 X{x:g} Y{y:g}" for x, y in order_marks(points(w))]
    assert lines.count("M0") == w.hold_panel.count() and f"G0 X{spot[0]:g} Y{spot[1]:g}" in lines


def test_edited_points_are_marked_from_far_to_near_whatever_their_number(hold, tmp_path):
    """Si el orden de la tabla es otro (puntos escritos o anadidos a mano), el programa los visita de lejos a cerca y
    cada comentario dice cual es en la ventana."""
    w = hold
    table = list(reversed(points(w)))                                        # de cerca a lejos: el contrario de lo que se quiere
    w.project.hold_points[w.current] = table
    w._update_hold()
    assert points(w) == table and len(set(p[1] for p in table)) > 1
    out = w.generate_marks(tmp_path / "m.nc", confirm=False)
    lines = out.read_text(encoding="ascii").splitlines()
    visited = [tuple(float(v[1:]) for v in l.split()[1:3]) for l in lines if l.startswith("G0 X")]
    assert sorted(visited) == sorted(table)                                  # ni falta ni sobra ninguno
    ys = [p[1] for p in visited]
    assert ys[0] == max(ys) and ys[-1] == min(ys)                            # empieza por lo mas lejos y acaba por lo mas cerca
    n = len(table)
    first = table.index(visited[0]) + 1
    assert f"(Punto 1 de {n} - es el {first} de la ventana)" in lines
    assert any(l.startswith("(Orden: de lejos a cerca") for l in lines)


def test_marks_with_an_unsafe_point_ask_first(hold, tmp_path):
    w = hold
    x, y = unsafe_spot(w)
    type_cell(w, 0, COL_X, str(x))
    type_cell(w, 0, COL_Y, str(y))
    target = tmp_path / "m.nc"
    assert w.generate_marks(target) is None and not target.exists()                           # contesto que no
    assert any("no son seguros" in t or "no se debe sujetar" in t for _title, t in w.boxes.asked)
    assert "1" in w.boxes.asked[-1][1]
    w.boxes.answers["question"] = QMessageBox.Yes
    assert w.generate_marks(target) == target and target.exists()
    assert any("no son seguros" in t for t in w.messages.texts())


def test_the_folder_of_the_marks_is_remembered_for_next_time(hold, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(hold, "_remember_folder", seen.append)
    out = hold.generate_marks(tmp_path / "m.nc", confirm=False)
    assert seen == [out]


def test_marks_ask_before_replacing_a_file(hold, tmp_path):
    w = hold
    target = tmp_path / "m.nc"
    target.write_text("antes")
    assert w.generate_marks(target) is None and target.read_text() == "antes"
    w.boxes.answers["question"] = QMessageBox.Yes
    assert w.generate_marks(target) == target and target.read_text(encoding="ascii").startswith("(Marcas")


def test_marks_without_points_say_there_is_nothing_to_mark(hold, tmp_path):
    w = hold
    for _ in range(w.hold_panel.count()):
        w.hold_remove(0)
    assert w.hold_panel.count() == 0 and not w.hold_panel.btn_marks.isEnabled()
    assert w.generate_marks(tmp_path / "m.nc", confirm=False) is None and not (tmp_path / "m.nc").exists()
    assert "Sin puntos" in w.hold_panel.info.text()
    assert "No hay puntos que marcar" in w.statusBar().currentMessage()
    assert any("No hay puntos que marcar" in t for t in w.messages.texts())


def test_marks_that_cannot_be_written_are_reported(hold, tmp_path, monkeypatch):
    w = hold
    bad = tmp_path / "no_existe" / "m.nc"
    assert w.generate_marks(bad, confirm=False) is None and not bad.exists()
    assert "No se pudo escribir las marcas" in w.statusBar().currentMessage()
    assert any("no_existe" in t or "No such file" in t or "cannot find" in t.lower() for t in w.messages.texts())
    assert w.messages.errors == 1


# --- se guarda con el trabajo ---------------------------------------------------------------------------------------
def test_the_points_and_settings_travel_with_the_job_file(hold, tmp_path, qtbot):
    w = hold
    w.hold_panel.kind.setCurrentIndex(w.hold_panel.kind.findData("tirafondo"))
    w.hold_panel.reach.setText("250")
    w.hold_panel.reach.valueChanged.emit()
    set_setting(w, "min_gap", "230")
    set_setting(w, "corner_clear", "120")
    set_setting(w, "min_points", "7")
    set_setting(w, "cut_clear", "25")
    set_setting(w, "bottom_row", "55")
    w.hold_panel.grid.setChecked(False)
    w.hold_remove(0)
    w.hold_add(*free_spot(w, far_from=points(w)))
    saved = points(w)
    job = tmp_path / "job19.cncjob"
    assert w.save_job(job)
    data = json.loads(job.read_text(encoding="utf-8"))
    assert data["sujecion"]["tipo"] == "tirafondo" and data["sujecion"]["alcance"] == 250
    assert (data["sujecion"]["separacion_minima"], data["sujecion"]["distancia_esquinas"],
            data["sujecion"]["puntos_minimos"]) == (230, 120, 7)
    assert (data["sujecion"]["distancia_cortes"], data["sujecion"]["fila_borde_inferior"],
            data["sujecion"]["filas_y_columnas"]) == (25, 55, False)
    other = MainWindow(Project(tools()), [], use_settings=False)
    qtbot.addWidget(other)
    other.show()
    assert other.open_job(job)
    other.wait_for_plan()
    assert other.project.hold.kind == "tirafondo" and other.hold_panel.kind.currentData() == "tirafondo"
    assert other.hold_panel.reach.value() == 250
    assert (other.hold_panel.min_gap.value(), other.hold_panel.corner_clear.value(),
            other.hold_panel.min_points.value()) == (230.0, 120.0, 7.0)
    assert (other.hold_panel.cut_clear.value(), other.hold_panel.bottom_row.value(),
            other.hold_panel.grid.isChecked()) == (25.0, 55.0, False)
    other.tabs.setCurrentWidget(other.hold_panel)
    assert points(other) == saved and "editados a mano" in other.hold_panel.info.text()


def test_opening_another_drawing_forgets_the_points_but_keeps_the_settings(hold):
    w = hold
    w.hold_panel.kind.setCurrentIndex(w.hold_panel.kind.findData("tirafondo"))
    w.hold_remove(0)
    assert w.project.hold_points
    assert w.open_svg(DATA / "pvc_negro_3_y_5mm.svg", apply_default_recipe=False)
    assert not w.project.hold_points and w.hold_panel.kind.currentData() == "tirafondo"


# --- comprobacion del programa instalado ----------------------------------------------------------------------------
def test_the_autotest_of_the_installed_program_also_checks_the_hold_proposal(tmp_path, qtbot):
    from cnc.gui.app import autotest
    out = tmp_path / "autotest.txt"
    assert autotest(str(BOARDS19), str(out)) == 0
    text = out.read_text(encoding="utf-8")
    import re
    m = re.search(r"sujecion: (\d+) punto\(s\) propuesto\(s\)", text)
    assert m and int(m.group(1)) >= 4 and text.rstrip().endswith("RESULTADO: CORRECTO")


def test_the_autotest_works_with_several_machine_files_by_taking_the_first_and_saying_so(tmp_path, qtbot, monkeypatch):
    import json
    import re
    import shutil
    from cnc.gui.app import autotest
    here = Path(__file__).resolve().parent.parent
    shutil.copy(here / "herramientas.json", tmp_path / "herramientas.json")
    book = json.loads((here / "maquinas.json").read_text(encoding="utf-8"))
    book["maquinas"]["taller"] = {"nombre": "Taller", "recorrido": [650, 1017, 100]}
    (tmp_path / "maquinas.json").write_text(json.dumps(book), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "autotest.txt"
    assert autotest(str(BOARDS19), str(out)) == 0
    text = out.read_text(encoding="utf-8")
    assert "fichas de maquina: emulador, taller" in text and re.search(r"maquina emulador, grosor", text)
    assert text.rstrip().endswith("RESULTADO: CORRECTO")


def test_the_autotest_fails_if_the_hold_calculation_breaks(tmp_path, qtbot, monkeypatch):
    from cnc import holddown
    from cnc.gui.app import autotest

    def boom(*a, **k):
        raise RuntimeError("roto")
    monkeypatch.setattr(holddown, "analyse_plan", boom)
    out = tmp_path / "autotest.txt"
    assert autotest(str(BOARDS19), str(out)) == 1
    text = out.read_text(encoding="utf-8")
    assert "sujecion: ERROR: RuntimeError: roto" in text and text.rstrip().endswith("RESULTADO: CON ERRORES")
