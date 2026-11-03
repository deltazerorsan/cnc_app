"""La lista de comprobacion antes de generar, en la ventana: no se genera sin marcarlo todo, y lo que es un tope ni se pregunta."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QMessageBox, QPushButton

from cnc import safety
from cnc.checklist import Check, Checklist, Section, build_checklist
from cnc.gui import theme
from cnc.gui.checklist_dialog import ChecklistDialog
from cnc.gui.main_window import MainWindow
from cnc.model import Tool
from cnc.project import Project

DATA = Path(__file__).parent / "data"
BOARDS19 = DATA / "tableros_19mm.svg"
OK = QDialogButtonBox.Ok

# La lista esta desconectada de serie (cnc.checklist.ENABLED, v0.23): aqui se prueba conectada.
pytestmark = pytest.mark.usefixtures("checklist_on")


def tools():
    return {"em4": Tool(diameter=4, name="End mill 4 mm", feed_xy=300, feed_z=200, stepdown=6.5)}


def sample() -> Checklist:
    return Checklist(
        sections=[Section("Tablero 1", [("Fresa", "End mill 4 mm"), ("Material", "19 mm")]),
                  Section("Tablero 2", [("Fresa", "End mill 4 mm")])],
        checks=[Check("La fresa esta apretada."), Check("El cero esta puesto."),
                Check("Operacion 3: no cabe. Lo he mirado y sigo adelante.", alarm=True)])


# --- la ventana sola -------------------------------------------------------------------------------------------------
@pytest.fixture
def dialog(qtbot):
    d = ChecklistDialog(sample())
    qtbot.addWidget(d)
    d.show()
    return d


def test_generar_is_disabled_until_every_box_is_ticked(dialog):
    ok = dialog.buttons.button(OK)
    assert ok.text() == "Generar" and not ok.isEnabled() and not dialog.all_checked
    assert dialog.count.text() == "Marcadas 0 de 3"
    for i, box in enumerate(dialog.boxes, start=1):
        box.setChecked(True)
        assert dialog.count.text() == f"Marcadas {i} de 3"
        assert ok.isEnabled() == (i == 3)
    assert dialog.all_checked
    dialog.boxes[1].setChecked(False)                                      # basta desmarcar una para volver a bloquear
    assert not ok.isEnabled() and not dialog.all_checked and dialog.count.text() == "Marcadas 2 de 3"


def test_pressing_generar_accepts_only_when_everything_is_ticked(dialog):
    ok = dialog.buttons.button(OK)
    dialog.boxes[0].setChecked(True)
    ok.click()                                                             # deshabilitado: no hace nada
    assert dialog.result() != QDialog.Accepted
    for box in dialog.boxes:
        box.setChecked(True)
    ok.click()
    assert dialog.result() == QDialog.Accepted


def test_cancel_is_the_default_button_and_rejects(dialog):
    cancel = dialog.buttons.button(QDialogButtonBox.Cancel)
    assert cancel.text() == "Cancelar" and cancel.isDefault() and not dialog.buttons.button(OK).isDefault()
    for box in dialog.boxes:
        box.setChecked(True)
    cancel.click()
    assert dialog.result() == QDialog.Rejected


def test_enter_does_not_generate_even_with_everything_ticked(dialog):
    """Un Intro despistado con todo marcado cancela: generar exige pulsar el boton."""
    for box in dialog.boxes:
        box.setChecked(True)
    assert dialog.buttons.button(OK).isEnabled() and not dialog.buttons.button(OK).isDefault()
    QTest.keyClick(dialog.boxes[0], Qt.Key_Return)
    assert not dialog.isVisible() and dialog.result() == QDialog.Rejected


def test_there_is_no_tick_all_control(dialog):
    """A proposito: la gracia es mirar cada casilla, una por una."""
    assert sorted(b.text() for b in dialog.findChildren(QPushButton)) == ["Cancelar", "Generar"]
    assert len(dialog.findChildren(QCheckBox)) == len(dialog.boxes) == 3
    assert not any(box.isTristate() for box in dialog.boxes)


def test_the_boxes_start_unticked_every_time(qtbot):
    for _ in range(2):
        d = ChecklistDialog(sample())
        qtbot.addWidget(d)
        assert not any(box.isChecked() for box in d.boxes)


def test_alarms_are_red_bold_and_marked_and_ordinary_checks_are_not(dialog):
    plain, alarm = dialog.boxes[0], dialog.boxes[2]
    assert plain.text() == "La fresa esta apretada." and plain.styleSheet() == ""
    assert alarm.text().startswith("⚠ Operacion 3") and theme.ERROR in alarm.styleSheet() and "bold" in alarm.styleSheet()


def test_the_data_is_listed_by_section_and_is_not_something_to_tick(dialog):
    texts = [label.text() for label in dialog.findChildren(QLabel)]
    for needed in ("Tablero 1", "Tablero 2", "Fresa", "Material", "19 mm", "End mill 4 mm"):
        assert needed in texts
    assert [v.text() for v in dialog.values] == ["End mill 4 mm", "19 mm", "End mill 4 mm"]


def bold_titles(d):
    return [label.text() for label in d.findChildren(QLabel) if label.styleSheet() == "font-weight: bold;"]


def test_section_titles_are_bold_and_one_job_has_none(dialog, qtbot):
    assert bold_titles(dialog) == ["Tablero 1", "Tablero 2"]
    single = ChecklistDialog(Checklist([Section("", [("Fresa", "x")])], [Check("a")]))
    qtbot.addWidget(single)
    texts = [label.text() for label in single.findChildren(QLabel)]
    assert "Fresa" in texts and "x" in texts and bold_titles(single) == []


# --- en la ventana principal -----------------------------------------------------------------------------------------
@pytest.fixture
def boxes(monkeypatch):
    """Ningun cuadro de dialogo real; lo que se muestra queda anotado."""
    shown = []
    for name in ("warning", "critical", "information", "question"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(
            lambda parent, title, text, *a, _n=name, **k: (shown.append((_n, title, text)), QMessageBox.No)[1]))
    return shown


@pytest.fixture
def win(qtbot, tmp_path, boxes):
    """Con un archivo de ajustes (si no, el programa no abre la lista: asi no puede quedar una ventana colgada)."""
    w = MainWindow(Project(tools()), [], use_settings=False, settings_file=str(tmp_path / "ajustes.ini"))
    qtbot.addWidget(w)
    w.show()
    assert w.open_svg(BOARDS19, apply_default_recipe=False)
    w.job.tool.setCurrentIndex(w.job.tool.findData("em4"))
    w.job.thickness.setText("19")
    w.job.thickness.valueChanged.emit()
    w.project.general.rotate = "0"
    w.job.refresh()
    w.wait_for_plan()
    return w


@pytest.fixture
def exec_dialog(monkeypatch):
    """Sustituye el exec() de la lista: anota lo que se le enseno y contesta lo que diga `answer`."""
    state = {"dialogs": [], "answer": QDialog.Rejected, "tick": False}

    def fake_exec(self):
        state["dialogs"].append(self)
        if state["tick"]:
            for box in self.boxes:
                box.setChecked(True)
        if callable(state.get("probe")):
            state["probe"](self)
        return state["answer"]
    monkeypatch.setattr(ChecklistDialog, "exec", fake_exec)
    return state


def test_generate_shows_the_checklist_and_cancelling_writes_nothing(win, exec_dialog, tmp_path):
    out = tmp_path / "salida"
    out.mkdir()
    assert win.generate(base=out / "job19.nc", confirm=True) == []
    assert len(exec_dialog["dialogs"]) == 1 and list(out.iterdir()) == []


def test_accepting_the_checklist_writes_the_files_and_not_before(win, exec_dialog, tmp_path, monkeypatch):
    out = tmp_path / "salida"
    out.mkdir()
    seen = []
    exec_dialog["answer"] = QDialog.Accepted
    exec_dialog["probe"] = lambda dialog: seen.append(sorted(p.name for p in out.iterdir()))
    paths = win.generate(base=out / "job19.nc", confirm=True)
    assert seen == [[]]                                                    # al preguntar todavia no habia nada escrito
    assert [p.name for p in paths] == ["job19_tablero1.nc", "job19_tablero2.nc"] and all(p.exists() for p in paths)


def test_the_list_has_one_section_per_checked_board_and_the_alarms_of_each(win, exec_dialog, tmp_path):
    win.generate(base=tmp_path / "job19.nc", confirm=True)
    (dialog,) = exec_dialog["dialogs"]
    assert [s.title for s in dialog.checklist.sections] == ["Tablero 1", "Tablero 2", "Todos los tableros"]
    plans = win.project.plan_selected()
    alarms = {f"Tablero {n}: {a}" for n, p in plans for a in p.alarms}
    assert alarms and {c.text.rsplit(" Lo he mirado", 1)[0] for c in dialog.checklist.checks if c.alarm} == alarms
    assert len(dialog.boxes) == 6 + len(alarms)                           # 6 confirmaciones fijas mas una por alarma
    assert not any(box.isChecked() for box in dialog.boxes)               # nunca llega nada marcado de antes


def test_a_single_board_gets_a_list_without_board_titles(win, exec_dialog, tmp_path):
    win.boards.item(1).setCheckState(Qt.Unchecked)
    win.generate(base=tmp_path / "solo.nc", confirm=True)
    (dialog,) = exec_dialog["dialogs"]
    assert [s.title for s in dialog.checklist.sections] == [""]
    assert not any(c.text.startswith("Tablero") for c in dialog.checklist.checks)


def test_the_six_fixed_checks_and_every_data_row_are_always_there(win, exec_dialog, tmp_path):
    """No hay una lista corta para quien ya sabe: los datos y las seis confirmaciones salen siempre."""
    win.generate(base=tmp_path / "a.nc", confirm=True)
    (dialog,) = exec_dialog["dialogs"]
    fixed = [c.text for c in dialog.checklist.checks if not c.alarm]
    assert len(fixed) == 6 and fixed[0].startswith("La maquina conectada a UGS es") and fixed[1].startswith("La fresa montada es") \
        and fixed[-1].startswith("El husillo")
    for section in dialog.checklist.sections:
        if section.title == "Todos los tableros":                          # el total del tiempo, solo con varios tableros
            assert [name for name, _ in section.rows] == ["Tiempo"]
            continue
        assert [name for name, _ in section.rows] == ["Maquina", "Fresa", "Material", "Pasadas", "Avances", "Cortes",
                                                       "Trayectoria", "Tiempo", "Giro", "Cero", "Z segura", "Husillo",
                                                       "Al terminar"]


def test_a_job_that_cannot_be_written_never_reaches_the_list(win, exec_dialog, tmp_path, boxes):
    win.project.general.rotate = "90"                                      # 1000 mm a lo largo de X (651)
    win.job.refresh()
    assert win.generate(base=tmp_path / "x.nc", confirm=True) == []
    assert exec_dialog["dialogs"] == [] and list(tmp_path.glob("*.nc")) == []
    assert "No cabe en la maquina" in win.messages.texts()[0]
    kind, title, text = boxes[-1]
    assert (kind, title) == ("warning", "No se puede generar") and "Tablero 1: No cabe en la maquina" in text


def test_a_margin_over_the_limit_never_reaches_the_list(win, exec_dialog, tmp_path, boxes):
    win.project.general.margin = safety.MARGIN_MAX + 0.1
    win.job.refresh()
    assert win.generate(base=tmp_path / "x.nc", confirm=True) == []
    assert exec_dialog["dialogs"] == [] and "margen" in win.messages.texts()[0].lower()


def test_without_confirmation_the_list_is_skipped(win, exec_dialog, tmp_path):
    """confirm=False es solo para las pruebas: por la ventana siempre se pregunta."""
    paths = win.generate(base=tmp_path / "job19.nc", confirm=False)
    assert len(paths) == 2 and exec_dialog["dialogs"] == []


def test_without_a_settings_file_the_list_never_opens_and_nothing_is_generated(qtbot, tmp_path, boxes):
    w = MainWindow(Project(tools()), [], use_settings=False)
    qtbot.addWidget(w)
    assert w.settings is None and w._run_checklist(sample()) is False
    assert w.open_svg(BOARDS19, apply_default_recipe=False)
    w.job.tool.setCurrentIndex(w.job.tool.findData("em4"))
    w.job.thickness.setText("19")
    w.job.thickness.valueChanged.emit()
    assert w.generate(base=tmp_path / "job19.nc", confirm=True) == [] and list(tmp_path.glob("*.nc")) == []


def test_run_checklist_returns_what_the_dialog_returns(win, exec_dialog):
    exec_dialog["answer"] = QDialog.Accepted
    assert win._run_checklist(sample()) is True
    exec_dialog["answer"] = QDialog.Rejected
    assert win._run_checklist(sample()) is False
    assert [type(d) for d in exec_dialog["dialogs"]] == [ChecklistDialog, ChecklistDialog]


def test_replacing_a_file_is_still_asked_after_the_list(win, exec_dialog, tmp_path, boxes):
    exec_dialog["answer"] = QDialog.Accepted
    target = tmp_path / "v.nc"
    target.write_text("viejo")
    win.boards.item(1).setCheckState(Qt.Unchecked)
    assert win.generate(base=target, confirm=True) == [] and target.read_text() == "viejo"
    assert [b for b in boxes if b[0] == "question"][0][1] == "Sustituir archivos"


# --- el resumen de debajo del dibujo ---------------------------------------------------------------------------------
def test_the_summary_says_when_the_job_cannot_be_generated(win):
    win.project.general.rotate = "90"
    win.job.refresh()
    win.plan_now()
    text = win.summary.text()
    assert "No se puede generar" in text and "No cabe en la maquina" in text and theme.ERROR in text
    assert "alarma(s)" not in text                                         # lo que impide generar manda sobre lo que se confirma


def _summary_with(win, blockers):
    plan = win.last_plan
    plan.blockers = list(blockers)
    win._update_summary(plan)
    return win.summary.text()


def test_the_summary_names_the_first_blocker_only_when_there_is_one(win):
    text = _summary_with(win, ["Primero."])
    assert "No se puede generar: Primero." in text and "(y " not in text and "más)" not in text


def test_the_summary_names_the_first_blocker_and_counts_the_rest(win):
    text = _summary_with(win, ["Primero.", "Segundo.", "Tercero."])
    assert "No se puede generar: Primero. (y 2 más)" in text
    assert "Segundo." not in text and "Tercero." not in text


def test_the_summary_counts_one_more_blocker_when_there_are_two(win):
    assert "No se puede generar: Primero. (y 1 más)" in _summary_with(win, ["Primero.", "Segundo."])


def test_the_summary_counts_the_alarms_that_will_be_asked_on_generating(win):
    plan = win.last_plan
    assert plan.alarms and not plan.blockers
    assert f"{len(plan.alarms)} alarma(s)" in win.summary.text() and "No se puede generar" not in win.summary.text()
