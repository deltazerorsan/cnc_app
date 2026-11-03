"""Las fichas de maquina en la ventana (PySide6 sin pantalla): el dialogo, el pegado del log, el selector del panel y el
enganche con la ventana principal."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QMessageBox

from cnc.gui.job_panel import machine_label
from cnc.gui.machines_dialog import LogImportDialog, MachinesDialog
from cnc.gui.main_window import MainWindow
from cnc.machinebook import MachineEditor
from cnc.machines import load_machines, parse_machine, save_machines
from cnc.model import Tool
from cnc.project import General, Project

DATA = Path(__file__).parent / "data"
BOARDS19 = DATA / "tableros_19mm.svg"

CARDS = {"taller": {"nombre": "Taller", "recorrido": [651, 1011, 100], "avance_max": 3600, "avance_max_z": 3600,
                    "homing": True, "limites_blandos": True, "laser": False},
         "emu": {"nombre": "Emulador", "recorrido": [650, 1017, 100], "homing": False}}
LOG = """\
Grbl 1.1h ['$' for help]
$20=1
$21=1
$22=1
$32=0
$110=3000.000
$111=3000.000
$112=1500.000
$130=500.000
$131=700.000
$132=60.000
ok
"""
TOOLS = '{"herramientas": {"em4": {"diametro": 4, "feed": 300, "entrada": 200, "profundidad_pasada": 6.5}}}'


def machines():
    return {k: parse_machine(k, v) for k, v in CARDS.items()}


@pytest.fixture
def path(tmp_path):
    p = tmp_path / "maquinas.json"
    save_machines(machines(), p)
    return p


@pytest.fixture(autouse=True)
def no_boxes(monkeypatch):
    """Que ninguna pregunta modal se quede esperando a alguien que pulse un boton."""
    calls = {"question": [], "warning": [], "critical": []}
    answer = {"question": QMessageBox.Yes}
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: calls["question"].append(a[2]) or answer["question"])
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: calls["warning"].append(a[2]))
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: calls["critical"].append(a[2]))
    calls["answer"] = answer
    return calls


def type_into(qtbot, edit, text):
    edit.setFocus()
    edit.selectAll()
    if text:
        qtbot.keyClicks(edit, text)
    else:
        qtbot.keyClick(edit, Qt.Key_Backspace)


def labels(d):
    return [d.list.item(i).text() for i in range(d.list.count())]


# --- el dialogo de fichas -------------------------------------------------------------------------------------
@pytest.fixture
def dlg(qtbot, path):
    d = MachinesDialog(load_machines(path), path)
    qtbot.addWidget(d)
    d.show()
    return d


def test_it_opens_on_the_first_card_with_every_field_filled(dlg):
    assert labels(dlg) == ["taller — Taller (651 × 1011 × 100 mm)", "emu — Emulador (650 × 1017 × 100 mm)"]
    assert dlg.row == 0 and dlg.ident.text() == "taller" and dlg.name.text() == "Taller"
    assert [e.text() for e in dlg.travel] == ["651", "1011", "100"] and dlg.feed_xy.text() == "3600"
    assert [dlg.flags[k].currentData() for k in ("limites_blandos", "limites_duros", "homing", "laser")] == [
        "si", "", "si", "no"]
    assert dlg.btn_save.isEnabled() and not dlg.changed() and dlg.error.text() == "" and dlg.btn_refresh.isEnabled()


def test_the_flag_boxes_have_three_states_and_reach_the_card(dlg):
    box = dlg.flags["homing"]
    assert [box.itemData(i) for i in range(box.count())] == ["", "si", "no"]
    assert [box.itemText(i) for i in range(box.count())] == ["sin dato", "sí", "no"]
    box.setCurrentIndex(box.findData("no"))
    assert dlg.editor.entries[0].machine.homing is False
    box.setCurrentIndex(box.findData(""))
    assert dlg.editor.entries[0].machine.homing is None
    dlg.flags["laser"].setCurrentIndex(dlg.flags["laser"].findData("si"))
    assert dlg.editor.entries[0].machine.laser_mode is True


def test_typing_updates_the_card_the_list_and_the_advice(dlg, qtbot):
    dlg.select(1)
    assert "Sin avance máximo" in dlg.advice.text() and "Sin homing" in dlg.advice.text()
    type_into(qtbot, dlg.feed_xy, "2500")
    type_into(qtbot, dlg.feed_z, "1200,5")
    type_into(qtbot, dlg.name, "Emulador UGS")
    type_into(qtbot, dlg.travel[2], "80")
    m = dlg.editor.entries[1].machine
    assert (m.max_feed, m.max_feed_z, m.name, m.travel) == (2500, 1200.5, "Emulador UGS", (650.0, 1017.0, 80.0))
    assert "Sin avance máximo" not in dlg.advice.text() and dlg.changed()
    assert labels(dlg)[1] == "emu — Emulador UGS (650 × 1017 × 80 mm)"


def test_every_number_and_text_reaches_the_saved_card(dlg, qtbot):
    dlg.select(1)
    for edit, text in zip(dlg.accel, ("200", "250", "100")):
        type_into(qtbot, edit, text)
    type_into(qtbot, dlg.spindle, "10000")
    type_into(qtbot, dlg.firmware, "Grbl 1.1h")
    type_into(qtbot, dlg.source, "a mano")
    dlg.notes.setPlainText("  la del emulador  ")
    m = dlg.editor.entries[1].machine
    assert (m.accel, m.spindle_max, m.firmware, m.source, m.notes) == ((200.0, 250.0, 100.0), 10000.0, "Grbl 1.1h",
                                                                        "a mano", "la del emulador")


def test_a_value_that_does_not_work_is_flagged_everywhere_and_blocks_saving(dlg, qtbot):
    type_into(qtbot, dlg.travel[0], "")
    e = dlg.editor.entries[0]
    assert not e.valid and "tres casillas" in dlg.error.text() and dlg.advice.text() == ""
    assert labels(dlg)[0].startswith("⚠ taller") and not dlg.btn_save.isEnabled() and not dlg.btn_refresh.isEnabled()
    assert "taller" in dlg.btn_save.toolTip()
    dlg.select(1)
    assert labels(dlg)[0].startswith("⚠") and not dlg.btn_save.isEnabled() and dlg.error.text() == ""
    dlg.select(0)
    type_into(qtbot, dlg.travel[0], "640")
    assert dlg.error.text() == "" and dlg.btn_save.isEnabled() and not labels(dlg)[0].startswith("⚠")


def test_the_identifier_box_does_not_accept_characters_the_file_rejects(dlg, qtbot):
    type_into(qtbot, dlg.ident, "4 t/aller_2")
    assert dlg.ident.text() == "taller_2" and dlg.editor.entries[0].ident == "taller_2"


def test_a_repeated_identifier_flags_both_cards(dlg, qtbot):
    type_into(qtbot, dlg.ident, "emu")
    assert labels(dlg)[0].startswith("⚠") and labels(dlg)[1].startswith("⚠") and not dlg.btn_save.isEnabled()
    assert "otra ficha" in dlg.error.text()


def test_new_card_is_selected_and_cannot_be_saved_until_it_has_the_travel(dlg, qtbot):
    dlg.btn_new.click()
    assert dlg.list.count() == 3 and dlg.row == 2 and dlg.ident.text() == "maquina"
    assert not dlg.btn_save.isEnabled() and "falta el recorrido" in dlg.error.text()
    for edit, text in zip(dlg.travel, ("300", "400", "50")):
        type_into(qtbot, edit, text)
    assert dlg.btn_save.isEnabled() and labels(dlg)[2] == "maquina (300 × 400 × 50 mm)"


def test_duplicate_copies_the_form_and_puts_you_on_the_identifier(dlg):
    dlg.btn_dup.click()
    assert dlg.list.count() == 3 and dlg.row == 1 and dlg.ident.text() == "taller_copia"
    assert dlg.travel[0].text() == "651" and dlg.flags["homing"].currentData() == "si" and dlg.btn_save.isEnabled()


def test_deleting_moves_the_selection_and_the_last_one_leaves_an_empty_editor(dlg):
    dlg.btn_del.click()
    assert labels(dlg) == ["emu — Emulador (650 × 1017 × 100 mm)"] and dlg.row == 0 and dlg.ident.text() == "emu"
    dlg.btn_del.click()
    assert dlg.list.count() == 0 and not dlg.form_box.isVisible() and dlg.empty.isVisible()
    assert not dlg.btn_save.isEnabled() and not dlg.btn_dup.isEnabled() and not dlg.btn_del.isEnabled()
    assert not dlg.btn_refresh.isEnabled()
    dlg.btn_new.click()
    assert dlg.form_box.isVisible() and not dlg.empty.isVisible()


def test_save_writes_the_file_keeps_the_backup_and_closes(dlg, path, qtbot):
    before = path.read_text(encoding="utf-8")
    type_into(qtbot, dlg.travel[0], "640")
    dlg.btn_dup.click()
    type_into(qtbot, dlg.ident, "taller_2")
    with qtbot.waitSignal(dlg.accepted):
        dlg.btn_save.click()
    assert dlg.saved_path == path and dlg.result() == QDialog.Accepted
    back = load_machines(path)
    assert list(back) == ["taller", "taller_2", "emu"] and back["taller"].travel[0] == 640 == back["taller_2"].travel[0]
    assert path.with_name("maquinas.json.bak").read_text(encoding="utf-8") == before
    assert "_ayuda" in json.loads(path.read_text(encoding="utf-8"))


def test_a_write_error_is_reported_and_the_window_stays_open(dlg, path, qtbot, monkeypatch, no_boxes):
    before = path.read_text(encoding="utf-8")
    type_into(qtbot, dlg.feed_xy, "500")

    def boom(_path):
        raise PermissionError("denegado")
    monkeypatch.setattr(dlg.editor, "save", boom)
    assert dlg.save() is False
    assert dlg.result() != QDialog.Accepted and dlg.saved_path is None and dlg.changed()
    assert "denegado" in no_boxes["critical"][0] and path.read_text(encoding="utf-8") == before


def test_cancel_with_changes_asks_and_only_discards_when_told(dlg, path, qtbot, no_boxes):
    before = path.read_text(encoding="utf-8")
    type_into(qtbot, dlg.feed_xy, "500")
    no_boxes["answer"]["question"] = QMessageBox.No
    dlg.btn_cancel.click()
    assert len(no_boxes["question"]) == 1 and dlg.isVisible()
    no_boxes["answer"]["question"] = QMessageBox.Yes
    dlg.btn_cancel.click()
    assert dlg.result() == QDialog.Rejected and path.read_text(encoding="utf-8") == before


def test_cancel_without_changes_closes_without_asking(dlg, no_boxes):
    dlg.select(1)
    dlg.btn_cancel.click()
    assert no_boxes["question"] == [] and dlg.result() == QDialog.Rejected


def test_without_a_path_nothing_can_be_saved(qtbot):
    d = MachinesDialog(machines(), None)
    qtbot.addWidget(d)
    assert not d.btn_save.isEnabled() and "ruta" in d.btn_save.toolTip() and d.save() is False


def test_the_dialog_says_where_it_saves_and_an_empty_one_says_how_to_start(dlg, path, qtbot):
    assert str(path) in dlg.where.text() and ".bak" in dlg.where.text()
    empty = MachinesDialog({}, path)
    qtbot.addWidget(empty)
    empty.show()
    assert empty.empty.isVisible() and "Nueva desde un log" in empty.empty.text() and not empty.form_box.isVisible()


# --- pegar el log ---------------------------------------------------------------------------------------------
@pytest.fixture
def logdlg(qtbot):
    d = LogImportDialog(MachineEditor(machines()))
    qtbot.addWidget(d)
    d.show()
    return d


def test_the_log_dialog_starts_empty_and_cannot_be_accepted(logdlg):
    assert not logdlg.btn_ok.isEnabled() and logdlg.btn_ok.text() == "Añadir la máquina"
    assert logdlg.read.text() == "(nada todavía)" and logdlg.machine_ident() == "maquina"
    assert "Pega el log" in logdlg.btn_ok.toolTip() and logdlg.form_box.isVisible()


def test_pasting_a_log_shows_what_was_understood_and_enables_the_button(logdlg):
    logdlg.set_text(LOG)
    text = logdlg.read.text()
    assert "• Firmware: Grbl 1.1h" in text and "• Recorrido: 500 × 700 × 60 mm" in text
    assert "• Avances máximos (mm/min): X 3000, Y 3000, Z 1500" in text and "• Homing: sí" in text
    assert logdlg.btn_ok.isEnabled() and logdlg.problems.text() == "" and logdlg.status.text() == ""


def test_a_log_without_travel_says_what_is_missing_and_cannot_be_accepted(logdlg):
    logdlg.set_text("$130=500\n$131=700\n$22=1")
    assert not logdlg.btn_ok.isEnabled() and "Falta $132 (recorrido Z)" in logdlg.notes.text()
    assert "Falta $132" in logdlg.btn_ok.toolTip()


def test_a_contradictory_log_is_shown_in_red_and_cannot_be_accepted(logdlg):
    logdlg.set_text("$130=500\n$131=700\n$132=60\n$130=400")
    assert not logdlg.btn_ok.isEnabled() and "dos veces con valores distintos" in logdlg.problems.text()


def test_the_identifier_is_checked_before_accepting(logdlg, qtbot):
    logdlg.set_text(LOG)
    type_into(qtbot, logdlg.ident, "taller")
    assert not logdlg.btn_ok.isEnabled() and "ya hay una ficha con el identificador 'taller'" in logdlg.status.text()
    type_into(qtbot, logdlg.ident, "")
    assert not logdlg.btn_ok.isEnabled() and "falta el identificador" in logdlg.status.text()
    type_into(qtbot, logdlg.ident, "chica")
    assert logdlg.btn_ok.isEnabled() and logdlg.status.text() == "" and logdlg.machine_ident() == "chica"


def test_the_update_dialog_shows_what_would_change(qtbot):
    ed = MachineEditor(machines())
    d = LogImportDialog(ed, update_index=0)
    qtbot.addWidget(d)
    d.show()
    assert d.btn_ok.text() == "Actualizar la ficha" and not d.form_box.isVisible() and not d.btn_ok.isEnabled()
    assert "«taller»" in d.help.text()
    d.set_text(LOG)
    assert d.btn_ok.isEnabled() and d.changes.text().startswith("Cambiaría:")
    assert "recorrido (mm): 651 × 1011 × 100 → 500 × 700 × 60" in d.changes.text()
    assert ed.entries[0].machine.travel == (651.0, 1011.0, 100.0)                  # solo se muestra
    d.set_text("$130=651\n$131=1011\n$132=100")
    assert not d.btn_ok.isEnabled() and "no cambia ningún dato" in d.btn_ok.toolTip() and d.changes.text() == ""
    assert "no cambia ningún dato" in d.status.text()


# --- crear y actualizar desde el log, en el dialogo de fichas -----------------------------------------------
def answer_log(monkeypatch, text, ident=None, name=None, result=QDialog.Accepted):
    def run(self):
        self.set_text(text)
        if ident is not None:
            self.ident.setText(ident)
        if name is not None:
            self.name.setText(name)
        return result
    monkeypatch.setattr(LogImportDialog, "exec", run)


def test_new_from_a_log_adds_the_card_and_selects_it(dlg, monkeypatch):
    answer_log(monkeypatch, LOG, ident="chica", name="Chica")
    assert dlg.btn_log.isEnabled() and dlg.new_from_log() is True
    assert labels(dlg)[-1] == "chica — Chica (500 × 700 × 60 mm)" and dlg.row == 2
    m = dlg.editor.entries[2].machine
    assert (m.max_feed, m.max_feed_z, m.homing, m.firmware) == (3000.0, 1500.0, True, "Grbl 1.1h")
    assert m.source.startswith("log pegado el ") and dlg.changed() and dlg.btn_save.isEnabled()


def test_cancelling_the_log_dialog_adds_nothing(dlg, monkeypatch):
    answer_log(monkeypatch, LOG, result=QDialog.Rejected)
    assert dlg.new_from_log() is False and dlg.list.count() == 2


def test_import_log_can_be_driven_without_the_dialog(dlg):
    assert dlg.import_log(LOG, ident="x") == 2 and dlg.ident.text() == "x" and dlg.row == 2


def test_refreshing_the_chosen_card_from_a_log_changes_only_what_the_log_brings(dlg, monkeypatch):
    answer_log(monkeypatch, "$130=640\n$131=1000\n$132=100")
    dlg.select(0)
    assert dlg.refresh_from_log() is True
    m = dlg.editor.entries[0].machine
    assert m.travel == (640.0, 1000.0, 100.0) and m.homing is True and m.max_feed == 3600.0 and m.name == "Taller"
    assert dlg.travel[0].text() == "640" and dlg.changed()
    assert dlg.editor.entries[0].machine.source.startswith("log pegado el ")


def test_refreshing_with_nothing_selected_or_cancelling_does_nothing(dlg, monkeypatch):
    answer_log(monkeypatch, LOG, result=QDialog.Rejected)
    assert dlg.refresh_from_log() is False and not dlg.changed()
    dlg.btn_del.click()
    dlg.btn_del.click()
    assert dlg.refresh_from_log() is False


# --- el panel del trabajo ---------------------------------------------------------------------------------------
@pytest.fixture
def folder(tmp_path):
    (tmp_path / "herramientas.json").write_text(TOOLS, encoding="utf-8")
    save_machines(machines(), tmp_path / "maquinas.json")
    return tmp_path


@pytest.fixture
def win(qtbot, folder):
    w = MainWindow(folders=(folder, folder), use_settings=False)
    qtbot.addWidget(w)
    w.show()
    return w


def pick(win, ident="em4", thickness="19"):
    win.job.tool.setCurrentIndex(win.job.tool.findData(ident))
    win.job.thickness.setText(thickness)
    win.job.thickness.valueChanged.emit()


def test_the_panel_lists_the_machines_and_asks_to_choose_when_there_are_several(win):
    j = win.job
    assert [j.machine.itemText(i) for i in range(j.machine.count())] == [
        "taller — Taller (651 × 1011 × 100 mm)", "emu — Emulador (650 × 1017 × 100 mm)"]
    assert j.machine.currentIndex() == -1 and "Hay varias máquinas (taller, emu)" in j.machine_note.text()
    assert j.machine_value() == "" and win.project.general.machine == ""


def test_choosing_a_machine_sets_the_project_and_shows_its_travel_and_warnings(win, qtbot):
    j = win.job
    with qtbot.waitSignal(j.machineChosen) as sig:
        j.machine.setCurrentIndex(j.machine.findData("emu"))
    assert sig.args == ["emu"] and win.project.general.machine == "emu"
    assert j.machine_note.text() == "Recorrido 650 × 1017 × 100 mm · sin homing"
    j.machine.setCurrentIndex(j.machine.findData("taller"))
    assert j.machine_note.text() == "Recorrido 651 × 1011 × 100 mm" and win.project.general.machine == "taller"
    assert j.machine_note.styleSheet() == ""


def test_editing_another_field_never_changes_the_machine(win):
    j = win.job
    j.machine.setCurrentIndex(j.machine.findData("emu"))
    pick(win)
    assert win.project.general.machine == "emu" and j.machine_value() == "emu"


def test_a_machine_chosen_from_code_is_not_a_user_choice(win, qtbot):
    got = []
    win.job.machineChosen.connect(got.append)
    win.project.general = General(tool="em4", machine="emu")
    win.job.refresh()
    assert got == [] and win.job.machine_value() == "emu"


def test_the_only_card_is_shown_chosen_without_asking(qtbot, folder):
    save_machines({"solo": parse_machine("solo", {"recorrido": [700, 800, 100]})}, folder / "maquinas.json")
    w = MainWindow(folders=(folder, folder), use_settings=False)
    qtbot.addWidget(w)
    assert w.job.machine_value() == "solo" and w.job.machine_note.text() == "Recorrido 700 × 800 × 100 mm"


def test_a_machine_that_does_not_exist_is_shown_as_an_error(win):
    win.project.general = General(tool="em4", machine="tallr")
    win.job.refresh()
    assert win.job.machine.currentIndex() == -1 and "No hay ninguna máquina 'tallr'" in win.job.machine_note.text()


def test_machine_label_shows_the_name_only_when_it_adds_something():
    assert machine_label("a", parse_machine("a", {"recorrido": [1, 2, 3]})) == "a (1 × 2 × 3 mm)"
    assert machine_label("a", parse_machine("a", {"recorrido": [1, 2, 3], "nombre": "Uno"})) == "a — Uno (1 × 2 × 3 mm)"


def test_the_job_is_planned_against_the_chosen_machine_and_the_checklist_names_it(qtbot, folder, monkeypatch, tmp_path,
                                                                                    checklist_on):
    from cnc.gui.checklist_dialog import ChecklistDialog
    win = MainWindow(folders=(folder, folder), settings_file=str(tmp_path / "ajustes.ini"))   # la lista solo sale con ajustes
    qtbot.addWidget(win)
    win.open_svg(BOARDS19, apply_default_recipe=False)
    pick(win)
    win.job.machine.setCurrentIndex(win.job.machine.findData("emu"))
    win.wait_for_plan()
    assert win.last_plan.ok and win.last_plan.job.machine.name == "Emulador"
    shown = []
    monkeypatch.setattr(ChecklistDialog, "exec", lambda self: shown.append(self) or QDialog.Rejected)
    win.generate(base=tmp_path / "a.nc", confirm=True)
    (dialog,) = shown
    assert dialog.checklist.checks[0].text == ("La maquina conectada a UGS es «Emulador» (recorrido 650 x 1017 x 100 mm): "
                                               "este G-code es para ella.")
    assert dict(dialog.checklist.sections[0].rows)["Maquina"] == "Emulador, recorrido 650 x 1017 x 100 mm"


def test_without_a_machine_the_window_does_not_generate(win, folder):
    win.open_svg(BOARDS19, apply_default_recipe=False)
    pick(win)
    win.wait_for_plan()
    assert win.last_plan.error and "Hay varias máquinas" in win.last_plan.error and not win.last_plan.writable


def test_without_a_machines_file_the_startup_says_so_and_nothing_is_generated(qtbot, tmp_path):
    (tmp_path / "herramientas.json").write_text(TOOLS, encoding="utf-8")
    w = MainWindow(folders=(tmp_path, tmp_path), use_settings=False)
    qtbot.addWidget(w)
    assert any("No encuentro maquinas.json" in n.text for n in w.startup_notes if n.code == "machines")
    assert w.project.machines == {} and w.job.machine.count() == 0
    assert "No hay ninguna ficha de máquina" in w.job.machine_note.text()
    w.open_svg(BOARDS19, apply_default_recipe=False)
    pick(w)
    w.wait_for_plan()
    assert w.last_plan.error and not w.last_plan.writable


# --- enganche con la ventana --------------------------------------------------------------------------------
def drive(monkeypatch, steps, result=QDialog.Accepted):
    """Sustituye el exec() del dialogo por unos pasos que lo manejan como lo haria una persona."""
    seen = []

    def run(self):
        seen.append(self)
        steps(self)
        return result
    monkeypatch.setattr(MachinesDialog, "exec", run)
    return seen


def test_the_button_and_ctrl_m_open_the_editor(win, monkeypatch):
    opened = []
    monkeypatch.setattr(MachinesDialog, "exec", lambda self: opened.append(self.path) or QDialog.Rejected)
    win.job.edit_machines.click()
    action = next(a for a in win.menuBar().actions()[0].menu().actions() if a.text() == "Máquinas…")
    assert action.shortcut().toString() == "Ctrl+M"
    action.trigger()
    assert opened == [win.project.machines_path, win.project.machines_path]
    assert win.project.machines_path.name == "maquinas.json"


def test_saved_machines_replace_the_ones_in_use_and_the_chosen_one_follows_a_rename(win, path, monkeypatch, qtbot):
    win.job.machine.setCurrentIndex(win.job.machine.findData("taller"))

    def steps(d):
        type_into(qtbot, d.ident, "taller_pino")
        type_into(qtbot, d.travel[0], "640")
        d.btn_dup.click()
        type_into(qtbot, d.ident, "taller_2")
        assert d.save()
    seen = drive(monkeypatch, steps)
    assert win.open_machines_editor() is True
    assert list(win.project.machines) == ["taller_pino", "taller_2", "emu"]
    assert win.project.general.machine == "taller_pino" and win.job.machine_value() == "taller_pino"
    assert win.job.machine.count() == 3 and win.job.machine_note.text().startswith("Recorrido 640 × 1011")
    assert "taller_pino" in win.statusBar().currentMessage() and "pasa a llamarse" in win.statusBar().currentMessage()
    assert set(load_machines(path)) == set(win.project.machines) and path.with_name("maquinas.json.bak").exists()
    assert seen[0].parent() is win


def test_removing_the_chosen_machine_says_so_and_does_not_invent_another(win, monkeypatch):
    win.job.machine.setCurrentIndex(win.job.machine.findData("emu"))

    def steps(d):
        d.select(1)
        d.btn_del.click()
        assert d.save()
    drive(monkeypatch, steps)
    assert win.open_machines_editor()
    assert list(win.project.machines) == ["taller"] and win.project.general.machine == "emu"
    assert "ya no existe" in win.statusBar().currentMessage()


def test_cancelling_changes_nothing(win, path, monkeypatch):
    before = path.read_text(encoding="utf-8")
    drive(monkeypatch, lambda d: d.btn_del.click(), result=QDialog.Rejected)
    assert win.open_machines_editor() is False
    assert list(win.project.machines) == ["taller", "emu"] and path.read_text(encoding="utf-8") == before
    assert not path.with_name("maquinas.json.bak").exists()


def test_the_editor_starts_from_the_file_as_it_is_now_not_as_it_was_loaded(win, path, monkeypatch):
    save_machines({"nueva": parse_machine("nueva", {"recorrido": [1, 2, 3]})}, path)                 # editado a mano despues
    seen = []
    drive(monkeypatch, lambda d: seen.append([e.ident for e in d.editor.entries]), result=QDialog.Rejected)
    win.open_machines_editor()
    assert seen == [["nueva", "taller", "emu"]]       # lo del archivo, y lo que solo estaba en el trabajo abierto


def test_a_card_edited_by_hand_wins_over_the_stale_copy_in_memory(win, path, monkeypatch):
    save_machines({"taller": parse_machine("taller", {**CARDS["taller"], "recorrido": [700, 1011, 100]})}, path)
    seen = []
    drive(monkeypatch, lambda d: seen.append({e.ident: e.machine.travel for e in d.editor.entries}), result=QDialog.Rejected)
    win.open_machines_editor()
    assert seen[0]["taller"] == (700, 1011, 100)       # lo que hay en el archivo, no lo que se cargo al arrancar
    assert seen[0]["emu"] == machines()["emu"].travel   # la que solo estaba en el trabajo abierto se ofrece igualmente


def test_a_broken_file_is_not_overwritten_and_the_reason_is_shown(win, path, monkeypatch):
    path.write_text("{esto no es json", encoding="utf-8")
    seen = drive(monkeypatch, lambda d: None)
    assert win.open_machines_editor() is False
    assert seen == [] and path.read_text(encoding="utf-8") == "{esto no es json"
    assert "JSON" in win.messages.texts()[0].upper() and "a mano" in win.messages.texts()[0]
    assert set(win.project.machines) == {"taller", "emu"}


def test_with_no_file_the_editor_starts_empty_and_creates_the_file(qtbot, tmp_path, monkeypatch):
    (tmp_path / "herramientas.json").write_text(TOOLS, encoding="utf-8")
    w = MainWindow(folders=(tmp_path, tmp_path), use_settings=False)
    qtbot.addWidget(w)
    seen = []

    def steps(d):
        seen.append((d.path, d.list.count()))
        d.import_log(LOG, ident="chica")
        assert d.save()
    drive(monkeypatch, steps)
    assert w.open_machines_editor() is True
    assert seen == [(tmp_path / "maquinas.json", 0)]
    assert list(load_machines(tmp_path / "maquinas.json")) == ["chica"] and list(w.project.machines) == ["chica"]
    assert not (tmp_path / "maquinas.json.bak").exists() and w.job.machine_value() == "chica"
    assert w.project.machines_path == tmp_path / "maquinas.json"


def test_the_last_machine_chosen_is_remembered_on_this_pc(qtbot, folder, tmp_path):
    ini = tmp_path / "ajustes.ini"
    w = MainWindow(folders=(folder, folder), settings_file=str(ini))
    qtbot.addWidget(w)
    assert w.project.general.machine == ""
    w.job.machine.setCurrentIndex(w.job.machine.findData("emu"))
    assert w._pref("machine") == "emu"
    w2 = MainWindow(folders=(folder, folder), settings_file=str(ini))
    qtbot.addWidget(w2)
    assert w2.project.general.machine == "emu" and w2.job.machine_value() == "emu"


def test_a_remembered_machine_that_no_longer_exists_or_a_configured_one_is_not_replaced(qtbot, folder, tmp_path):
    ini = tmp_path / "ajustes.ini"
    first = MainWindow(folders=(folder, folder), settings_file=str(ini))
    qtbot.addWidget(first)
    first._set_pref("machine", "borrada")
    w = MainWindow(folders=(folder, folder), settings_file=str(ini))
    qtbot.addWidget(w)
    assert w.project.general.machine == "" and w.job.machine_value() == ""
    (folder / "cnc_app.toml").write_text('machine = "taller"\n', encoding="utf-8")
    first._set_pref("machine", "emu")
    w2 = MainWindow(folders=(folder, folder), settings_file=str(ini))
    qtbot.addWidget(w2)
    assert w2.project.general.machine == "taller" and w2.job.machine_value() == "taller"


def test_a_project_given_to_the_window_keeps_its_own_machine_over_the_remembered_one(qtbot, tmp_path):
    ini = tmp_path / "ajustes.ini"
    p = Project({"em4": Tool(diameter=4)}, machines())
    p.general = General(machine="taller")
    w = MainWindow(p, [], settings_file=str(ini))
    qtbot.addWidget(w)
    w._set_pref("machine", "emu")
    w2 = MainWindow(Project({"em4": Tool(diameter=4)}, machines()), [], settings_file=str(ini))
    qtbot.addWidget(w2)
    assert w2.project.general.machine == "emu"
    assert w.project.general.machine == "taller"


def test_reload_machines_reads_the_file_again(win, path, monkeypatch):
    save_machines({"otra": parse_machine("otra", {"recorrido": [1, 2, 3]})}, path)
    assert win.reload_machines() is True
    assert list(win.project.machines) == ["otra"] and win.job.machine.count() == 1
    assert "Maquinas recargadas: otra" in win.statusBar().currentMessage()
    assert win.job.machine_value() == "otra"


def test_reload_machines_with_a_broken_file_changes_nothing_and_says_why(win, path):
    path.write_text("{esto no es json", encoding="utf-8")
    assert win.reload_machines() is False
    assert list(win.project.machines) == ["taller", "emu"] and "JSON" in win.messages.texts()[0].upper()
    assert "No se recargaron las maquinas" in win.statusBar().currentMessage()
    path.unlink()
    assert win.reload_machines() is False and "No encuentro maquinas.json" in win.messages.texts()[0]


def test_a_saved_job_opens_with_its_machine_and_warns_when_the_card_changed(win, path, tmp_path):
    win.open_svg(BOARDS19, apply_default_recipe=False)
    pick(win)
    win.job.machine.setCurrentIndex(win.job.machine.findData("taller"))
    job = tmp_path / "t.cncjob"
    assert win.save_job(job)
    save_machines({"taller": parse_machine("taller", {**CARDS["taller"], "recorrido": [600, 1000, 100]}),
                   "emu": machines()["emu"]}, path)
    assert win.reload_machines()
    win.open_job(job)
    assert win.project.general.machine == "taller" and win.job.machine_value() == "taller"
    win.wait_for_plan()
    assert any("«taller» ha cambiado" in t for t in win.messages.texts())


def test_the_menu_has_the_machine_actions(win):
    texts = [a.text() for a in win.menuBar().actions()[0].menu().actions()]
    assert "Máquinas…" in texts and "Recargar máquinas" in texts and "Fichas de fresas…" in texts
