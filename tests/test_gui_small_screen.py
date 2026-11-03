"""La ventana en una pantalla pequeña (un portatil de 1366 x 768): el divisor del panel de abajo, lo que cabe y lo que se
puede alcanzar (PySide6 sin pantalla)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QMessageBox, QScrollArea

from cnc.gui import main_window as mw
from cnc.gui.main_window import MainWindow, fit_rect
from cnc.gui.widgets import CurrentPageTabs
from cnc.model import GRBL_MAQUINA_REAL, Tool
from cnc.project import Project

BOARDS19 = Path(__file__).parent / "data" / "tableros_19mm.svg"
TOOLS = {"em4": Tool(diameter=4, name="Fresa 4", feed_xy=300, feed_z=200, stepdown=6)}
SCREEN = QRect(0, 0, 1366, 728)                  # 1366 x 768 sin la barra de tareas de Windows


@pytest.fixture(autouse=True)
def no_boxes(monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)


def make(qtbot, size=(1366, 697)):
    w = MainWindow(Project(dict(TOOLS), {"real": GRBL_MAQUINA_REAL}), [], use_settings=False)
    qtbot.addWidget(w)
    w.resize(*size)
    w.show()
    assert w.open_svg(BOARDS19, apply_default_recipe=False)
    w.wait_for_plan()
    return w


@pytest.fixture
def win(qtbot):
    return make(qtbot)


def drag_bottom_to(w, height):
    """Lo que hace el raton al arrastrar el divisor: pide que el panel de abajo tenga `height` px."""
    w.splitter.moveSplitter(sum(w.splitter.sizes()) - height, 1)
    return w.splitter.sizes()


# --- el divisor del panel de abajo --------------------------------------------------------------------------------------
def test_the_bottom_pane_follows_the_divider_all_the_way_down_to_its_minimum(win):
    for want in (330, 250, 200, 150, 120, 100, mw.PANE_MIN_HEIGHT):
        top, bottom = drag_bottom_to(win, want)
        assert bottom == want and top == sum(win.splitter.sizes()) - want


def test_below_the_minimum_the_pane_stays_at_it_instead_of_vanishing(win):
    for want in (80, 40, 10, 1, 0):
        assert drag_bottom_to(win, want)[1] == mw.PANE_MIN_HEIGHT


def test_pulling_the_divider_up_never_makes_the_drawing_vanish_either(win):
    top_min = win.splitter.widget(0).minimumSizeHint().height()
    for want_top in (300, 200, 100, 50, 0):
        win.splitter.moveSplitter(want_top, 1)
        top, bottom = win.splitter.sizes()
        assert top >= top_min > 100 and bottom > mw.PANE_MIN_HEIGHT


def test_no_panel_can_be_collapsed_by_the_dividers(win):
    assert [win.splitter.isCollapsible(i) for i in (0, 1)] == [False, False]
    top = win.splitter.widget(0)
    assert [top.isCollapsible(i) for i in (0, 1)] == [False, False]
    for x in (0, 5, 100, 200, 340):                                       # el divisor de la columna de la izquierda
        top.moveSplitter(x, 1)
        assert top.sizes()[0] >= 340 and top.sizes()[1] > 300


def test_the_dividers_are_reachable_with_a_big_pane_too(win):
    assert drag_bottom_to(win, 400)[1] == 400
    assert win.splitter.sizes()[0] >= win.splitter.widget(0).minimumSizeHint().height()


def test_what_does_not_fit_in_a_low_pane_is_reached_with_the_scroll_bar(qtbot, win):
    drag_bottom_to(win, mw.PANE_MIN_HEIGHT)
    qtbot.wait(30)
    win.pane.verticalScrollBar().setValue(0)
    bar = win.pane.verticalScrollBar()
    assert bar.isVisible() and bar.maximum() > 0
    tab_bar = win.tabs.tabBar()
    assert tab_bar.mapTo(win.pane.viewport(), QPoint(0, 0)).y() >= 0       # las pestañas se ven siempre al principio
    assert tab_bar.isVisible() and win.tabs.width() >= win.pane.viewport().width()
    bar.setValue(bar.maximum())                                             # y al final, el pie de la tabla
    bottom_row = win.ops.table.mapTo(win.pane.viewport(), QPoint(0, win.ops.table.height()))
    assert bottom_row.y() <= win.pane.viewport().height()


def test_with_the_usual_pane_height_there_is_no_scroll_bar_to_get_in_the_way(win):
    assert win.splitter.sizes()[1] == 250
    assert not win.pane.verticalScrollBar().isVisible() and win.pane.verticalScrollBar().maximum() == 0


def test_the_pane_has_no_frame_so_it_looks_like_the_tabs_alone(win):
    assert win.pane.frameShape() == QScrollArea.NoFrame and win.pane.widget() is win.tabs


# --- cada pestaña pide su altura ----------------------------------------------------------------------------------------
def test_the_minimum_height_is_that_of_the_tab_you_are_looking_at(win):
    assert isinstance(win.tabs, CurrentPageTabs)
    heights = {}
    for i in range(win.tabs.count()):
        win.tabs.setCurrentIndex(i)
        heights[i] = win.tabs.minimumSizeHint().height()
    bar = win.tabs.tabBar().sizeHint().height()
    assert heights[2] > heights[0] > bar and heights[2] > heights[1] > bar       # Sujecion es la mas alta
    for i, h in heights.items():
        win.tabs.setCurrentIndex(i)
        assert h == bar + win.tabs.currentWidget().minimumSizeHint().height()


def test_opening_the_hold_tab_makes_the_pane_taller_keeping_room_for_the_drawing(win):
    before = win.splitter.sizes()
    win.tabs.setCurrentWidget(win.hold_panel)
    top, bottom = win.splitter.sizes()
    assert bottom == mw.HOLD_PANE_HEIGHT and top >= mw.CANVAS_MIN_HEIGHT - 30
    assert not win.pane.verticalScrollBar().isVisible()
    win.tabs.setCurrentIndex(0)
    assert abs(win.splitter.sizes()[1] - before[1]) <= 3


def test_on_a_short_window_the_hold_pane_only_grows_as_far_as_the_drawing_keeps_its_minimum(qtbot):
    w = make(qtbot, size=(1366, 620))
    total = sum(w.splitter.sizes())
    w.tabs.setCurrentWidget(w.hold_panel)
    top, bottom = w.splitter.sizes()
    assert top + bottom == total and top >= mw.CANVAS_MIN_HEIGHT - 5 and 250 < bottom < mw.HOLD_PANE_HEIGHT
    w.tabs.setCurrentIndex(0)
    assert abs(w.splitter.sizes()[1] - 250) <= 3


def test_on_a_window_with_no_room_to_spare_the_hold_pane_does_not_take_it_from_the_drawing(qtbot):
    w = make(qtbot, size=(1366, 520))
    before = w.splitter.sizes()
    w.tabs.setCurrentWidget(w.hold_panel)
    assert w.splitter.sizes() == before                                    # lo que falta se alcanza con la barra
    qtbot.wait(30)
    assert w.pane.verticalScrollBar().maximum() > 0
    w.tabs.setCurrentIndex(0)


def test_the_scroll_bar_of_the_tall_tab_goes_away_when_you_leave_it(qtbot):
    w = make(qtbot, size=(1366, 520))                                       # sin sitio para ampliar el panel
    qtbot.wait(30)
    assert w.pane.verticalScrollBar().maximum() == 0                        # Operaciones cabe
    w.tabs.setCurrentWidget(w.hold_panel)
    qtbot.wait(30)
    assert w.pane.verticalScrollBar().maximum() > 0                         # Sujecion no: barra
    w.tabs.setCurrentIndex(0)
    qtbot.wait(30)
    assert w.pane.verticalScrollBar().maximum() == 0                        # y al volver, ya no hace falta


def test_the_window_can_be_as_small_as_a_laptop_screen_needs(win):
    hint = win.minimumSizeHint()
    assert hint.height() <= 400 and hint.width() <= 1366 - 100


# --- la columna de la izquierda: nada cortado ---------------------------------------------------------------------------
@pytest.mark.parametrize("size", [(1366, 697), (1400, 880), (1920, 1000)])
def test_the_left_column_shows_the_buttons_beside_the_machine_and_the_tool_complete(qtbot, size):
    w = make(qtbot, size=size)
    scroll = next(s for s in w.findChildren(QScrollArea) if s.widget() is not None and w.job.isAncestorOf(s.widget()) or
                  s.widget() is w.job.parentWidget())
    viewport = scroll.viewport()
    assert not scroll.horizontalScrollBar().isVisible()
    for button in (w.job.edit_machines, w.job.edit_tools):
        right = button.mapTo(viewport, QPoint(button.width(), 0)).x()
        assert 0 < right <= viewport.width(), (button.text(), right, viewport.width())
    assert w.job.width() <= viewport.width()


def test_if_the_panel_is_made_narrower_than_it_needs_the_rest_is_reached_not_cut(qtbot):
    w = make(qtbot)
    top = w.splitter.widget(0)
    top.moveSplitter(340, 1)                                              # lo mas estrecho que deja la columna
    scroll = next(s for s in w.findChildren(QScrollArea) if s.widget() is w.job.parentWidget())
    qtbot.wait(50)
    bar = scroll.horizontalScrollBar()
    assert bar.isVisible() and bar.maximum() > 0


# --- ajustarse a la pantalla --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("frame, expected", [
    (QRect(100, 50, 1400, 880), QRect(0, 0, 1366, 728)),                  # mas grande que la pantalla: se recorta y se mete
    (QRect(100, 50, 1000, 600), QRect(100, 50, 1000, 600)),               # ya cabe: se deja donde esta
    (QRect(900, 400, 800, 600), QRect(566, 128, 800, 600)),               # cabe pero se sale por la derecha y por abajo
    (QRect(-200, -30, 800, 600), QRect(0, 0, 800, 600)),                  # se sale por la izquierda y por arriba
    (QRect(0, 0, 3000, 600), QRect(0, 0, 1366, 600)),                     # solo es demasiado ancha
])
def test_a_frame_is_fitted_into_the_free_part_of_the_screen(frame, expected):
    assert fit_rect(frame, SCREEN) == expected


def test_a_screen_that_does_not_start_at_the_corner_is_respected():
    second = QRect(1920, 0, 1366, 728)                                    # el portatil como segunda pantalla
    assert fit_rect(QRect(1500, 100, 1400, 880), second) == QRect(1920, 0, 1366, 728)
    assert fit_rect(QRect(1930, 10, 800, 600), second) == QRect(1930, 10, 800, 600)


class FakeScreen:
    def __init__(self, rect):
        self.rect = rect

    def availableGeometry(self):
        return self.rect


def test_the_default_window_does_not_fit_a_1366_by_768_screen_so_it_is_adjusted(qtbot):
    w = MainWindow(Project(dict(TOOLS)), [], use_settings=False)
    qtbot.addWidget(w)
    assert (w.width(), w.height()) == mw.DEFAULT_SIZE == (1400, 880)
    w.show()
    w.move(900, 400)                                                      # se abrio en una esquina, con medio cuerpo fuera
    w.screen = lambda: FakeScreen(SCREEN)
    w.fit_to_screen()
    frame = w.frameGeometry()
    assert SCREEN.contains(frame) and frame.width() <= 1366 and frame.height() <= 728
    assert frame.topLeft() == QPoint(0, 0)
    assert w.width() <= 1366 and w.height() <= 728


def test_a_window_that_already_fits_is_left_alone(qtbot):
    w = MainWindow(Project(dict(TOOLS)), [], use_settings=False)
    qtbot.addWidget(w)
    w.resize(1100, 600)
    w.show()
    w.move(40, 30)
    w.screen = lambda: FakeScreen(SCREEN)
    before = (w.pos(), w.size())
    w.fit_to_screen()
    assert (w.pos(), w.size()) == before


def test_a_maximized_window_is_not_touched(qtbot, monkeypatch):
    w = MainWindow(Project(dict(TOOLS)), [], use_settings=False)
    qtbot.addWidget(w)
    w.show()
    monkeypatch.setattr(w, "isMaximized", lambda: True)
    w.screen = lambda: FakeScreen(QRect(0, 0, 500, 400))
    before = w.size()
    w.fit_to_screen()
    assert w.size() == before


def test_a_full_screen_window_is_not_touched_either(qtbot, monkeypatch):
    w = MainWindow(Project(dict(TOOLS)), [], use_settings=False)
    qtbot.addWidget(w)
    w.show()
    monkeypatch.setattr(w, "isFullScreen", lambda: True)
    w.screen = lambda: FakeScreen(QRect(0, 0, 500, 400))
    before = w.size()
    w.fit_to_screen()
    assert w.size() == before


def test_the_app_start_fits_the_window_after_showing_it(monkeypatch):
    """main() llama a fit_to_screen despues de mostrar la ventana (con el marco ya conocido)."""
    from cnc.gui import app as A
    order = []

    class Window:
        def __init__(self, *a, **k):
            pass

        def show(self):
            order.append("show")

        def fit_to_screen(self):
            order.append("fit")

        def receive_external(self, paths):
            pass

        def open_svg(self, path):
            order.append("open")

    class Server:
        requested = type("S", (), {"connect": lambda self, slot: None})()

        def __init__(self, name=None):
            pass

        def start(self):
            return True

    class FakeApp:
        def __init__(self, *a):
            pass

        @staticmethod
        def instance():
            return FakeApp()

        def setApplicationName(self, name):
            pass

        def exec(self):
            return 0

    monkeypatch.setattr(A, "MainWindow", Window)
    monkeypatch.setattr(A, "InstanceServer", Server)
    monkeypatch.setattr(A, "QApplication", FakeApp)
    monkeypatch.setattr(A, "forward_to_running", lambda argv, name: False)
    monkeypatch.setattr(A, "instance_name", lambda: "canal-de-prueba")
    monkeypatch.setattr(A.sys, "excepthook", A.sys.excepthook)
    monkeypatch.setattr(A.theme, "apply_theme", lambda app: None)
    assert A.main(["dibujo.svg"]) == 0
    assert order == ["show", "fit", "open"]
