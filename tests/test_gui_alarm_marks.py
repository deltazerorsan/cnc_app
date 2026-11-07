"""Los avisos de espacio sobre el lienzo: un circulo rojo por sitio, solo en la vista de maquina, que no se pulsa; y con demasiados
sitios, ninguno y una pregunta clara al generar (sin pantalla)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGraphicsItem, QMessageBox

import cnc.generate as gen
from cnc.gui.canvas import ITEM_KIND
from cnc.gui.main_window import MainWindow
from cnc.model import Tool
from cnc.project import Project

DATA = Path(__file__).parent / "data"
PLACA = DATA / "placa_doble.svg"


def tools():
    return {f"em{d}": Tool(diameter=d, name=f"End mill {d} mm", feed_xy=300, feed_z=200, stepdown=5.0) for d in (3, 6, 10)}


@pytest.fixture
def boxes(monkeypatch):
    """Ningun cuadro de dialogo real: lo que se pregunta con warning queda anotado y se contesta lo que diga `answer`."""
    state = {"asked": [], "answer": QMessageBox.No, "buttons": []}

    def warning(parent, title, text, *a, **k):
        state["asked"].append((title, text))
        state["buttons"].append(a)                      # (botones, boton por defecto)
        return state["answer"]
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(warning))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    for name in ("critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    return state


def choose(w, tool):
    w.job.tool.setCurrentIndex(w.job.tool.findData(tool))
    w.job.refresh()
    w.wait_for_plan()


def build(qtbot, folder):
    """La placa doble (18 mm) con la fresa de 6 mm, en la vista de maquina."""
    w = MainWindow(Project(tools()), [], use_settings=False, settings_file=str(folder / "ajustes.ini"))
    qtbot.addWidget(w)
    w.resize(1300, 800)
    w.show()
    assert w.open_svg(PLACA, apply_default_recipe=False)
    w.job.thickness.setText("18")
    w.job.thickness.valueChanged.emit()
    choose(w, "em6")
    w.set_view("machine")
    return w


@pytest.fixture
def win(qtbot, tmp_path, boxes):
    return build(qtbot, tmp_path)


@pytest.fixture
def out(tmp_path):
    """Una carpeta solo para los .nc (junto al archivo de ajustes de la ventana)."""
    folder = tmp_path / "salida"
    folder.mkdir()
    return folder


def items(w, kind="alarm"):
    return [it for it in w.canvas._alarm_items if it.data(ITEM_KIND) == kind]


# --- los circulos -----------------------------------------------------------------------------------------------------
def test_the_machine_view_circles_each_problem_where_the_plan_says(win):
    marks = win.last_plan.result.marks
    assert win.canvas.mode == "machine" and len(marks) == 2 == win.canvas.alarm_item_count()
    assert [(it.pos().x(), it.pos().y()) for it in items(win)] == pytest.approx([(m.x, m.y) for m in marks])
    for it, m in zip(items(win), marks):
        assert it.toolTip() == m.text and f"X{m.x:.1f} Y{m.y:.1f}" in it.toolTip()
        assert it.scene() is win.canvas._scene


def test_the_circles_are_only_a_sign_they_take_no_clicks_and_keep_their_size_at_any_zoom(win):
    for it in items(win):
        assert it.acceptedMouseButtons() == Qt.NoButton
        assert it.flags() & QGraphicsItem.ItemIgnoresTransformations
    before = [it.sceneBoundingRect().width() for it in items(win)]
    win.canvas.scale(3, 3)
    assert [it.mapRectToScene(it.boundingRect()).width() for it in items(win)] == pytest.approx(before)


def test_the_legend_explains_the_circle_only_when_there_are_marks(win):
    assert "◯" in win.legend.text()
    choose(win, "em3")
    assert win.last_plan.result.marks == [] and "◯" not in win.legend.text()


def test_the_drawing_view_has_no_circles_and_they_come_back_with_the_machine_view(win):
    win.set_view("drawing")
    assert win.canvas.alarm_item_count() == 0 and win.canvas._alarm_items == [] and "◯" not in win.legend.text()
    win.set_view("machine")
    assert win.canvas.alarm_item_count() == 2 and "◯" in win.legend.text()


def test_the_layer_is_replaced_not_piled_up_it_lies_over_the_drawing_and_only_exists_in_the_machine_view(win):
    c = win.canvas
    marks = win.last_plan.result.marks
    n_items = len(c._scene.items())
    c.set_alarm_layer(marks)
    c.set_alarm_layer(marks)
    assert len(c._scene.items()) == n_items and c.alarm_item_count() == 2
    below = [it.zValue() for it in c._scene.items() if it.data(ITEM_KIND) not in ("alarm", "alarm-line")]
    assert all(it.zValue() > max(below) for it in items(win))
    win.set_view("drawing")
    c.set_alarm_layer(marks)                                      # aunque se pida, en la vista de dibujo no se pinta
    assert c.alarm_item_count() == 0 and c._alarm_items == []


def test_the_circles_follow_the_tool_and_a_gap_also_gets_the_line_between_its_points(win):
    choose(win, "em3")
    assert win.canvas.alarm_item_count() == 0
    choose(win, "em10")
    marks = win.last_plan.result.marks
    assert len(items(win)) == len(marks) == 10
    assert len(items(win, "alarm-line")) == sum(1 for m in marks if m.kind == "gap") == 2
    for it in items(win, "alarm-line"):
        assert it.acceptedMouseButtons() == Qt.NoButton


# --- demasiados sitios ------------------------------------------------------------------------------------------------
@pytest.fixture
def excess(qtbot, tmp_path, boxes, monkeypatch):
    monkeypatch.setattr(gen, "MAX_MARKS", 1)           # con 2 sitios ya son demasiados (antes de calcular el plan)
    return build(qtbot, tmp_path)


def test_with_too_many_places_no_circle_is_drawn_and_the_excess_is_what_the_messages_say(excess):
    plan = excess.last_plan
    assert plan.result.excess == 2 and plan.result.marks == []
    assert excess.canvas.alarm_item_count() == 0 and "◯" not in excess.legend.text()
    texts = excess.messages.texts()
    assert any("DEMASIADAS ZONAS CON PROBLEMA" in t for t in texts)
    assert not any("no se pueden cortar" in t for t in texts)
    assert "1 alarma(s)" in excess.summary.text()


def test_generating_with_too_many_places_asks_first_and_says_it_clearly(excess, boxes, out):
    assert excess.generate(base=out / "placa") == [] and list(out.iterdir()) == []      # contesta que no
    title, text = boxes["asked"][-1]
    assert title == "Demasiadas zonas con problema" and "DEMASIADAS ZONAS CON PROBLEMA" in text and "6 mm" in text
    assert text.endswith("¿Generar de todos modos?")
    buttons, default = boxes["buttons"][-1]
    assert buttons == QMessageBox.Yes | QMessageBox.No and default == QMessageBox.No       # sin pensar, Intro no genera
    boxes["answer"] = QMessageBox.Yes
    written = excess.generate(base=out / "placa")
    assert len(boxes["asked"]) == 2 and len(written) == 1 and written[0].exists()


def test_without_the_dialogs_generating_does_not_ask(excess, boxes, out):
    assert len(excess.generate(base=out / "placa", confirm=False)) == 1 and boxes["asked"] == []


def test_a_few_problems_never_ask_and_the_gcode_is_the_same_with_or_without_circles(win, boxes, out, monkeypatch):
    first = win.generate(base=out / "con_marcas")
    assert boxes["asked"] == [] and len(first) == 1
    monkeypatch.setattr(gen, "MAX_MARKS", 1)                     # generar vuelve a calcular el plan: ahora con exceso
    second = win.generate(base=out / "sin_marcas", confirm=False)
    assert win.last_plan.result.marks and first[0].read_text() == second[0].read_text()      # el de la ventana sigue como estaba
