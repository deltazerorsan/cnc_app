"""«Salir» (Ctrl+Q): su acción lleva el papel de «Salir» de la aplicación. Sin él, macOS pone su propio «Salir» desactivado en el
menú de la aplicación y se queda con ⌘Q: ni el menú ni el atajo cerraban la ventana (sin pantalla).
Y ninguna otra acción del menú puede colarse en el menú de la aplicación: macOS lo decide por el texto («Quitar del grupo»
empieza por «Quit» y se hacía pasar por «Salir», casi siempre desactivada)."""
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


def _acciones_del_menu(win):
    """Todas las acciones de la barra de menús (con sus submenús), sin separadores."""
    found = []

    def walk(menu):
        for action in menu.actions():
            if action.menu() is not None:
                walk(action.menu())
            elif not action.isSeparator():
                found.append(action)
    for top in win.menuBar().actions():
        walk(top.menu())
    return found


def test_ninguna_accion_se_cuela_en_el_menu_de_la_aplicacion(win):
    """Sin papel explícito, macOS decide por el texto; con NoRole no decide nada. Solo «Salir» y «Acerca de» llevan papel."""
    roles = [a.menuRole() for a in _acciones_del_menu(win)]
    assert len(roles) > 30
    assert QAction.MenuRole.TextHeuristicRole not in roles
    assert roles.count(QAction.MenuRole.QuitRole) == 1
    assert roles.count(QAction.MenuRole.AboutRole) == 1


def test_las_acciones_que_empiezan_por_quit_o_exit_no_son_salir(win):
    """«Quitar la elección», «Quitar del grupo», «Quitar todos»… (o «Exit…» en inglés) no son la acción de salir."""
    salir = [a for a in win.findChildren(QAction) if a.shortcut() == QKeySequence("Ctrl+Q")][0]
    parecidas = [a for a in _acciones_del_menu(win) if a is not salir
                 and a.text().replace("&", "").lower().startswith(("quit", "exit"))]
    assert parecidas, "la prueba ya no comprueba nada: no hay acciones que empiecen por «Quit»"
    assert all(a.menuRole() == QAction.MenuRole.NoRole for a in parecidas)
