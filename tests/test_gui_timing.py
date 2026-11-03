"""El tiempo de maquina estimado en la ventana (sin pantalla): sale en el resumen de cada tablero, con el desglose en el
globo de ayuda, y lo caro se calcula en el hilo del plan y no al pintar."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from cnc.gui.main_window import MainWindow
from cnc.model import Tool
from cnc.project import Project

DATA = Path(__file__).parent / "data"
PLACA = DATA / "placa_doble.svg"


def tools():
    return {"em4": Tool(diameter=4, name="End mill 4 mm", feed_xy=1200, feed_z=300, stepdown=6.5, stepover=0.6)}


@pytest.fixture
def win(qtbot):
    w = MainWindow(Project(tools()), [], use_settings=False)
    qtbot.addWidget(w)
    w.show()
    assert w.open_svg(PLACA, apply_default_recipe=False)
    w.job.tool.setCurrentIndex(w.job.tool.findData("em4"))
    w.job.thickness.setText("12")
    w.job.thickness.valueChanged.emit()
    w.wait_for_plan()
    return w


def test_the_summary_says_how_long_the_machine_will_take(win):
    plan = win.last_plan
    assert plan is not None and plan.ok and plan.time is not None
    text = win.summary.text()
    assert f"≈ {plan.time.text} de máquina" in text and "líneas" in text
    assert win.summary.toolTip().startswith(f"unos {plan.time.text} de maquina")


def test_the_time_is_worked_out_with_the_plan_not_when_painting(win):
    assert win.last_plan._time is not None                  # el hilo del plan lo dejo hecho
    assert win.last_plan._time is win.last_plan.time


def test_a_plan_with_an_error_shows_no_time_and_no_tooltip(win):
    win.job.tool.setCurrentIndex(-1)                        # sin fresa no hay G-code
    win.wait_for_plan()
    assert not win.last_plan.ok
    assert "≈" not in win.summary.text() and win.summary.toolTip() == ""


def test_the_time_changes_when_the_feed_changes(win):
    slow = win.last_plan.time.seconds
    win.job.feed.setText("2400")
    win.job.feed.valueChanged.emit()
    win.wait_for_plan()
    assert win.last_plan.time.seconds < slow


def test_the_generated_message_gives_each_boards_time(win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    shown = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda parent, title, text, *a, **k: shown.append(text)))
    win.generate(base=tmp_path / "placa.nc", confirm=True)
    paths = list(tmp_path.glob("*.nc"))
    assert paths
    mine = f"≈ {win.last_plan.time.text} de máquina"
    assert any(mine in t for t in win.messages.texts())
