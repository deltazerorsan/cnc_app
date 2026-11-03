"""Los puentes en la ventana: la columna de la tabla de operaciones, los ajustes del trabajo y lo que se ve en la vista de maquina."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QCheckBox, QGraphicsPathItem

from cnc.gui.main_window import MainWindow
from cnc.model import Tool
from cnc.project import Project

BOARDS19 = Path(__file__).parent / "data" / "tableros_19mm.svg"
TABS_COLUMN = 5


def tools():
    return {"em4": Tool(diameter=4, name="End mill 4 mm", feed_xy=300, feed_z=200, stepdown=6.5, stepover=0.6)}


@pytest.fixture
def job19(qtbot):
    w = MainWindow(Project(tools()), [], use_settings=False)
    qtbot.addWidget(w)
    w.show()
    assert w.open_svg(BOARDS19, apply_default_recipe=False)
    w.job.tool.setCurrentIndex(w.job.tool.findData("em4"))
    w.job.thickness.setText("19")
    w.job.thickness.valueChanged.emit()
    return w


RED = "#ff0000"                  # las piezas grandes del dibujo de prueba


def find(w, stroke=RED):
    """La fila de la tabla de ese color (cambiar el lado de una fila puede recolocarla)."""
    return next(i for i, r in enumerate(w.project.rows) if r.stroke == stroke)


def check(w, row):
    return w.ops.table.cellWidget(row, TABS_COLUMN).findChild(QCheckBox)


def combo(w, row, col):
    return w.ops.table.cellWidget(row, col)


def settle(qtbot, w):
    """Espera a que el calculo de la trayectoria (en otro hilo) termine y se pinte."""
    qtbot.waitUntil(lambda: w.last_plan is not None, timeout=20000)


def bridge_items(w):
    return [i for i in w.canvas._scene.items() if isinstance(i, QGraphicsPathItem) and "puente(s)" in i.toolTip()]


def test_the_table_has_a_bridges_column_after_through_and_no_bridges_by_default(job19):
    t = job19.ops.table
    assert t.horizontalHeaderItem(TABS_COLUMN).text() == "Puentes" and t.horizontalHeaderItem(TABS_COLUMN - 1).text() == "Pasante"
    assert all(not check(job19, i).isChecked() for i in range(t.rowCount()))
    assert all(not r.tabs for r in job19.project.rows)
    assert not any("Puentes" in m.text for m in job19.project.plan(1).messages)


def test_ticking_a_row_gives_it_bridges_and_the_machine_view_draws_them(job19, qtbot):
    row = find(job19)
    check(job19, row).setChecked(True)
    assert job19.project.rows[row].tabs and not job19.project.rows[row].is_plain_cut()
    plan = job19.project.plan(1)
    assert any(m.text.startswith("Puentes: ") for m in plan.messages)
    job19.set_view("machine")
    settle(qtbot, job19)
    pieces = sum(1 for op in job19.last_plan.operations if getattr(op, "tabs", ()))
    assert pieces >= 1 and len(bridge_items(job19)) == pieces                  # un trazo por pieza con puentes
    check(job19, row).setChecked(False)
    qtbot.waitUntil(lambda: not bridge_items(job19), timeout=20000)             # el calculo se rehace aparte, con un pequeño retraso


def test_the_box_is_only_available_for_through_cuts_of_pieces_and_holes(job19):
    assert check(job19, find(job19)).isEnabled()
    for value, enabled in (("dentro", True), ("linea", False), ("fuera", True), ("auto", True)):
        side = combo(job19, find(job19), 3)
        side.setCurrentIndex(side.findData(value))
        assert check(job19, find(job19)).isEnabled() is enabled
    combo(job19, find(job19), 4).findChild(QCheckBox).setChecked(False)
    assert not check(job19, find(job19)).isEnabled()
    combo(job19, find(job19), 4).findChild(QCheckBox).setChecked(True)
    action = combo(job19, find(job19), 2)
    action.setCurrentIndex(action.findData("vaciado"))
    assert not check(job19, find(job19)).isEnabled()


def test_the_bridge_settings_are_in_the_job_panel_and_an_empty_box_goes_back_to_the_default(job19):
    job = job19.job
    assert (job.tab_width.placeholderText(), job.tab_height.placeholderText(), job.tab_spacing.placeholderText()) == ("6", "2", "150")
    job.tab_width.setText("8")
    job.tab_width.valueChanged.emit()
    job.tab_height.setText("2,5")
    job.tab_height.valueChanged.emit()
    g = job19.project.general
    assert (g.tab_width, g.tab_height, g.tab_spacing) == (8, 2.5, 150)
    job.tab_width.setText("")
    job.tab_width.valueChanged.emit()
    assert job19.project.general.tab_width == 6
    job.refresh()
    assert job.tab_height.text() == "2.5" and job.tab_width.text() == "6"


def test_the_settings_change_the_gcode_of_the_rows_that_ask_for_bridges(job19):
    check(job19, find(job19)).setChecked(True)
    assert "Z-17" in job19.project.plan(1).text
    job19.job.tab_height.setText("4")
    job19.job.tab_height.valueChanged.emit()
    text = job19.project.plan(1).text
    assert "Z-15 " in text or "Z-15\n" in text
    assert "Z-17" not in text


def test_a_recipe_with_bridges_ticks_the_box(job19, tmp_path):
    check(job19, find(job19)).setChecked(True)
    path = tmp_path / "receta.toml"
    path.write_text(job19.project.recipe_text(), encoding="utf-8")
    assert "puentes = true" in path.read_text(encoding="utf-8")
    job19.project.reset_rows()
    job19.ops.bind(job19.project.rows)
    assert not any(check(job19, i).isChecked() for i in range(job19.ops.table.rowCount()))
    job19.project.load_recipe_file(path)
    job19.ops.bind(job19.project.rows)
    assert check(job19, find(job19)).isChecked()


def test_the_drawing_view_repaints_by_itself_when_the_bridges_change(job19):
    assert job19.view == "drawing" and not bridge_items(job19)
    check(job19, find(job19)).setChecked(True)                               # la casilla de la fila
    shown = job19.canvas.bridges_shown()
    assert shown and bridge_items(job19) and all(manual is False for __, manual in shown.values())
    first = next(iter(shown))
    job19.job.tab_width.setText("10")                                      # el ancho de los ajustes
    job19.job.tab_width.valueChanged.emit()
    assert job19.canvas.bridges_shown()[first][0][0].width == 10
    job19.job.tab_spacing.setText("60")
    job19.job.tab_spacing.valueChanged.emit()
    assert len(job19.canvas.bridges_shown()[first][0]) > len(shown[first][0])
    check(job19, find(job19)).setChecked(False)
    assert not job19.canvas.bridges_shown() and not bridge_items(job19)


def test_the_automatic_bridges_reach_the_holes_too_and_the_box_stays_free_for_a_row_cut_from_the_inside(job19):
    for i in range(job19.ops.table.rowCount()):
        box = check(job19, i)
        if box.isEnabled():
            box.setChecked(True)
    levels = job19.project.levels()
    shown = job19.canvas.bridges_shown()
    assert any(levels.get(i, 0) % 2 == 1 for i in shown) and any(levels.get(i, 0) % 2 == 0 for i in shown)     # huecos y piezas
    plan = job19.project.plan(1)
    assert plan.ok and any(op.side == "inside" and getattr(op, "tabs", ()) for op in plan.operations)
    row = find(job19)
    side = combo(job19, row, 3)
    side.setCurrentIndex(side.findData("dentro"))
    assert check(job19, find(job19)).isEnabled() and check(job19, find(job19)).isChecked()


# --- el interruptor general de los puentes ------------------------------------------------------------------------------
def test_the_master_switch_is_a_box_on_top_of_the_bridges_group_and_it_starts_on(job19):
    job = job19.job
    assert job.bridges_on.text() == "Poner puentes" and job.bridges_on.isChecked() and job19.project.general.bridges
    assert all(w.isEnabled() for w in (job.tab_width, job.tab_height, job.tab_spacing))


def test_switching_it_off_greys_the_fields_and_the_row_boxes_and_clears_the_drawing(job19, qtbot):
    check(job19, find(job19)).setChecked(True)
    assert job19.canvas.bridges_shown()                                        # el dibujo ya muestra los puentes
    job19.job.bridges_on.setChecked(False)
    assert job19.project.general.bridges is False
    assert not any(w.isEnabled() for w in (job19.job.tab_width, job19.job.tab_height, job19.job.tab_spacing))
    assert not check(job19, find(job19)).isEnabled() and check(job19, find(job19)).isChecked()          # el marcado no se pierde
    assert "desactivados" in check(job19, find(job19)).toolTip()
    assert job19.canvas.bridges_shown() == {}                                  # sin salir de la vista del dibujo
    job19.job.bridges_on.setChecked(True)
    assert check(job19, find(job19)).isEnabled() and check(job19, find(job19)).isChecked() and job19.canvas.bridges_shown()
    assert all(w.isEnabled() for w in (job19.job.tab_width, job19.job.tab_height, job19.job.tab_spacing))


def test_the_job_without_bridges_has_none_in_its_gcode(job19):
    check(job19, find(job19)).setChecked(True)
    assert any(m.text.startswith("Puentes: ") for m in job19.project.plan(1).messages)
    job19.job.bridges_on.setChecked(False)
    assert not any(m.text.startswith("Puentes") for m in job19.project.plan(1).messages)


def test_the_switch_is_remembered_in_this_pc_and_the_next_window_starts_that_way(qtbot, tmp_path):
    ini = tmp_path / "prefs.ini"
    first = MainWindow(Project(tools()), [], use_settings=False, settings_file=str(ini))
    qtbot.addWidget(first)
    assert first.job.bridges_on.isChecked()
    first.job.bridges_on.setChecked(False)
    second = MainWindow(Project(tools()), [], use_settings=False, settings_file=str(ini))
    qtbot.addWidget(second)
    assert not second.job.bridges_on.isChecked() and second.project.general.bridges is False
    second.job.bridges_on.setChecked(True)
    third = MainWindow(Project(tools()), [], use_settings=False, settings_file=str(ini))
    qtbot.addWidget(third)
    assert third.job.bridges_on.isChecked()


def test_opening_a_job_does_not_overwrite_the_remembered_choice(qtbot, tmp_path):
    ini = tmp_path / "prefs.ini"
    w = MainWindow(Project(tools()), [], use_settings=False, settings_file=str(ini))
    qtbot.addWidget(w)
    w.job.bridges_on.setChecked(False)
    w.project.general = replace(w.project.general, bridges=True)           # como si un trabajo guardado lo trajera encendido
    w.job.refresh()
    assert w.job.bridges_on.isChecked()
    other = MainWindow(Project(tools()), [], use_settings=False, settings_file=str(ini))
    qtbot.addWidget(other)
    assert not other.job.bridges_on.isChecked()                              # lo recordado sigue siendo lo último que se eligió
