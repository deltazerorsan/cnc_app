"""«Salir» (Ctrl+Q): su acción lleva el papel de «Salir» de la aplicación. Sin él, macOS pone su propio «Salir» desactivado en el
menú de la aplicación y se queda con ⌘Q: ni el menú ni el atajo cerraban la ventana (sin pantalla)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMessageBox

from cnc.gui.main_window import MainWindow
from cnc.model import Tool
from cnc.project import Project


@pytest.fixture
def win(qtbot, tmp_path, monkeypatch):
    for name in ("warning", "critical", "information", "question"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Yes))
    tools = {"em4": Tool(diameter=4, name="End mill 4 mm", feed_xy=1200, feed_z=300, stepdown=6.5)}
    w = MainWindow(Project(tools), [], use_settings=False, settings_file=str(tmp_path / "ajustes.ini"))
    qtbot.addWidget(w)
    return w


def test_salir_lleva_el_papel_de_salir_de_la_aplicacion(win):
    salir = [a for a in win.findChildren(QAction) if a.shortcut() == QKeySequence("Ctrl+Q")]
    assert len(salir) == 1
    assert salir[0].menuRole() == QAction.MenuRole.QuitRole


def test_salir_cierra_la_ventana(win, qtbot):
    win.show()
    qtbot.waitExposed(win)
    [a for a in win.findChildren(QAction) if a.shortcut() == QKeySequence("Ctrl+Q")][0].trigger()
    assert not win.isVisible()
