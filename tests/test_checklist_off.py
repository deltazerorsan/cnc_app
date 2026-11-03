"""La lista de comprobacion esta desconectada de serie (`cnc.checklist.ENABLED`, v0.23): generar escribe sin abrirla, la linea de
comandos no la imprime y, aunque ya no hay que confirmar nada, las alarmas siguen a la vista y los topes siguen bloqueando."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

from test_cli_machines import job, work  # noqa: F401  (el fixture `work` y la funcion que lanza la linea de comandos)

from cnc import checklist as cl
from cnc.model import Tool
from cnc.project import Project

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QDialog, QMessageBox

from cnc.gui.checklist_dialog import ChecklistDialog
from cnc.gui.main_window import MainWindow

PLACA = Path(__file__).parent / "data" / "placa_doble.svg"


def tools():
    return {"em4": Tool(diameter=4, name="End mill 4 mm", feed_xy=1200, feed_z=300, stepdown=6.5, stepover=0.6)}


def test_the_checklist_is_switched_off_by_default():
    assert cl.ENABLED is False


# --- la ventana ----------------------------------------------------------------------------------------------------------------
@pytest.fixture
def win(qtbot, tmp_path, monkeypatch):
    for name in ("warning", "critical", "information", "question"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Yes))
    opened = []
    monkeypatch.setattr(ChecklistDialog, "exec", lambda self: (opened.append(self), QDialog.Rejected)[1])
    w = MainWindow(Project(tools()), [], use_settings=False, settings_file=str(tmp_path / "ajustes.ini"))
    w.opened_checklists = opened
    qtbot.addWidget(w)
    w.show()
    assert w.open_svg(PLACA, apply_default_recipe=False)
    w.job.tool.setCurrentIndex(w.job.tool.findData("em4"))
    w.job.thickness.setText("12")
    w.job.thickness.valueChanged.emit()
    w.wait_for_plan()
    return w


def test_generate_writes_without_opening_the_checklist(win, tmp_path):
    out = tmp_path / "salida"
    out.mkdir()
    paths = win.generate(base=out / "placa.nc", confirm=True)
    assert win.opened_checklists == []                                   # si se hubiera abierto, habria cancelado
    assert paths and all(p.exists() for p in paths)


def test_a_tope_still_blocks_without_the_checklist(win, tmp_path):
    win.project.general.margin = 0.6                                       # por encima del tope de margen
    win.job.refresh()
    assert win.generate(base=tmp_path / "x.nc", confirm=True) == [] and list(tmp_path.glob("*.nc")) == []


def test_the_summary_shows_the_alarms_and_no_longer_says_they_are_confirmed_on_generating(win):
    win.job.thickness.setText("24")                                        # de 22 a 30 mm: alarma
    win.job.thickness.valueChanged.emit()
    win.wait_for_plan()
    plan = win.last_plan
    assert plan.ok and plan.alarms
    text = win.summary.text()
    assert "alarma(s)" in text and "mira Mensajes" in text and "se confirman al generar" not in text
    assert any(m.severe and "Grosor de 24 mm" in m.text for m in plan.messages)      # y el texto esta en Mensajes


def test_with_the_checklist_connected_the_summary_says_the_alarms_are_confirmed_on_generating(win, monkeypatch):
    monkeypatch.setattr(cl, "ENABLED", True)
    win.job.thickness.setText("24")
    win.job.thickness.valueChanged.emit()
    win.wait_for_plan()
    assert "se confirman al generar" in win.summary.text()


def test_connected_again_the_window_asks_before_writing(win, tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "ENABLED", True)
    assert win.generate(base=tmp_path / "placa.nc", confirm=True) == []
    assert len(win.opened_checklists) == 1 and list(tmp_path.glob("*.nc")) == []


# --- la linea de comandos -------------------------------------------------------------------------------------------------------------
def test_the_command_line_writes_without_printing_the_checklist_and_gives_the_time(work, capsys):  # noqa: F811
    code, out, err, nc = job(work, "--machine", "taller", capsys=capsys)
    assert code == 0 and nc, err
    assert "Antes de ejecutarlo" not in out and "  - La fresa montada" not in out
    assert "Tiempo: unos " in out and "de maquina" in out


def test_the_command_line_still_warns_about_alarms_as_it_writes(work, capsys):  # noqa: F811
    code, out, err, nc = job(work, "--machine", "taller", "--thickness", "24", "--stepdown", "6.5", capsys=capsys)
    assert code == 0 and nc, err
    assert "AVISO: Grosor de 24 mm: pasa de 22 mm" in out


def test_the_command_line_prints_the_checklist_again_when_it_is_connected(work, capsys, checklist_on):  # noqa: F811
    code, out, err, nc = job(work, "--machine", "taller", capsys=capsys)
    assert code == 0 and "Antes de ejecutarlo, comprueba:" in out and "  - La fresa montada es" in out
