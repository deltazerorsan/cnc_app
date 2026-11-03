"""El editor de fichas de fresas (PySide6 sin pantalla) y su enganche con la ventana principal."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QMessageBox

from cnc.gui.main_window import MainWindow
from cnc.gui.tools_dialog import ToolsDialog
from cnc.model import Tool
from cnc.project import Project
from cnc.tools import load_tools, parse_tools

FILE = ('{"herramientas": {'
        '"em4": {"nombre": "End mill 4 mm", "diametro": 4, "feed": 300, "entrada": 200},'
        '"em3175": {"diametro": 3.175},'
        '"v90": {"tipo": "v", "diametro": 6, "angulo": 90}}}')


@pytest.fixture
def path(tmp_path):
    p = tmp_path / "herramientas.json"
    p.write_text(FILE, encoding="utf-8")
    (tmp_path / "maquinas.json").write_text('{"maquinas": {"real": {"recorrido": [651, 1011, 100]}}}', encoding="utf-8")
    return p


@pytest.fixture
def dlg(qtbot, path):
    d = ToolsDialog(load_tools(path), path)
    qtbot.addWidget(d)
    d.show()
    return d


def type_into(qtbot, edit, text):
    edit.setFocus()
    edit.selectAll()
    if text:
        qtbot.keyClicks(edit, text)
    else:
        qtbot.keyClick(edit, Qt.Key_Backspace)


def labels(d):
    return [d.list.item(i).text() for i in range(d.list.count())]


@pytest.fixture(autouse=True)
def no_boxes(monkeypatch):
    """Que ninguna pregunta modal se quede esperando a alguien que pulse un boton (tampoco al cerrar la prueba)."""
    calls = {"question": [], "warning": [], "critical": []}
    answer = {"question": QMessageBox.Yes}
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: calls["question"].append(a[2]) or answer["question"])
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: calls["warning"].append(a[2]))
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: calls["critical"].append(a[2]))
    calls["answer"] = answer
    return calls


# --- el dialogo ---------------------------------------------------------------------------------------
def test_it_opens_on_the_first_card_with_every_field_filled(dlg):
    assert labels(dlg) == ["em4 — End mill 4 mm (Ø4 mm)", "em3175 (Ø3.175 mm)", "v90 (Ø6 mm)"]
    assert dlg.row == 0 and dlg.ident.text() == "em4" and dlg.name.text() == "End mill 4 mm"
    assert [dlg.numbers[k].text() for k in ("diametro", "feed", "entrada", "flautas")] == ["4", "300", "200", ""]
    assert dlg.kind.currentData() == "endmill" and not dlg.numbers["angulo"].isEnabled()
    assert dlg.btn_save.isEnabled() and not dlg.changed() and dlg.error.text() == ""


def test_the_angle_box_is_only_for_a_v_bit(dlg):
    dlg.select(2)
    assert dlg.kind.currentData() == "v" and dlg.numbers["angulo"].isEnabled() and dlg.numbers["angulo"].text() == "90"
    dlg.kind.setCurrentIndex(dlg.kind.findData("endmill"))
    assert not dlg.numbers["angulo"].isEnabled() and dlg.editor.entries[2].valid
    assert dlg.editor.entries[2].tool.v_angle is None                                 # una plana no lleva ángulo
    dlg.kind.setCurrentIndex(dlg.kind.findData("v"))
    assert dlg.numbers["angulo"].text() == "90" and dlg.editor.entries[2].tool.v_angle == 90      # el angulo no se pierde


def test_typing_updates_the_card_the_list_and_the_advice(dlg, qtbot):
    dlg.select(1)
    assert "feed ni entrada" in dlg.advice.text() and "longitud útil" in dlg.advice.text()
    type_into(qtbot, dlg.numbers["feed"], "450")
    type_into(qtbot, dlg.numbers["entrada"], "150,5")
    type_into(qtbot, dlg.name, "End mill 1/8")
    tool = dlg.editor.entries[1].tool
    assert (tool.feed_xy, tool.feed_z, tool.name) == (450, 150.5, "End mill 1/8")
    assert "feed" not in dlg.advice.text() and dlg.changed()
    assert labels(dlg)[1] == "em3175 — End mill 1/8 (Ø3.175 mm)"


def test_a_value_that_does_not_work_is_flagged_everywhere_and_blocks_saving(dlg, qtbot):
    type_into(qtbot, dlg.numbers["stepover"], "150")
    e = dlg.editor.entries[0]
    assert not e.valid and "entre 0 y 100" in dlg.error.text() and dlg.advice.text() == ""
    assert labels(dlg)[0].startswith("⚠ em4") and not dlg.btn_save.isEnabled()
    assert "em4" in dlg.btn_save.toolTip()
    dlg.select(1)                                    # se puede mirar otra ficha; la primera sigue marcada
    assert labels(dlg)[0].startswith("⚠") and not dlg.btn_save.isEnabled() and dlg.error.text() == ""
    dlg.select(0)
    assert dlg.numbers["stepover"].text() == "150"   # lo escrito no se pierde al cambiar de ficha
    type_into(qtbot, dlg.numbers["stepover"], "40")
    assert dlg.error.text() == "" and dlg.btn_save.isEnabled() and not labels(dlg)[0].startswith("⚠")


def test_the_identifier_box_does_not_accept_characters_that_the_file_rejects(dlg, qtbot):
    type_into(qtbot, dlg.ident, "4 e/m_mdf")
    assert dlg.ident.text() == "em_mdf" and dlg.editor.entries[0].ident == "em_mdf"
    assert labels(dlg)[0].startswith("em_mdf")


def test_a_repeated_identifier_flags_both_cards(dlg, qtbot):
    type_into(qtbot, dlg.ident, "v90")
    assert labels(dlg)[0].startswith("⚠") and labels(dlg)[2].startswith("⚠") and not dlg.btn_save.isEnabled()
    assert "otra ficha" in dlg.error.text()


def test_an_empty_identifier_is_flagged_with_a_readable_name(dlg, qtbot):
    type_into(qtbot, dlg.ident, "")
    assert labels(dlg)[0] == "⚠ (sin identificador)" and "falta el identificador" in dlg.error.text()
    assert not dlg.btn_save.isEnabled()


def test_new_card_is_selected_and_cannot_be_saved_until_it_has_a_diameter(dlg, qtbot):
    dlg.btn_new.click()
    assert dlg.list.count() == 4 and dlg.row == 3 and dlg.ident.text() == "fresa"
    assert not dlg.btn_save.isEnabled() and "diametro" in dlg.error.text() and labels(dlg)[3] == "⚠ fresa"
    type_into(qtbot, dlg.ident, "em6")
    type_into(qtbot, dlg.numbers["diametro"], "6")
    assert dlg.btn_save.isEnabled() and labels(dlg)[3] == "em6 (Ø6 mm)" and dlg.editor.entries[3].tool.diameter == 6


def test_duplicate_copies_the_form_and_puts_you_on_the_identifier(dlg):
    dlg.btn_dup.click()
    assert dlg.list.count() == 4 and dlg.row == 1 and dlg.ident.text() == "em4_copia"
    assert dlg.numbers["feed"].text() == "300" and dlg.name.text() == "End mill 4 mm"
    assert dlg.btn_save.isEnabled() and labels(dlg)[1] == "em4_copia — End mill 4 mm (Ø4 mm)"


def test_deleting_moves_the_selection_and_the_last_one_leaves_an_empty_editor(dlg):
    dlg.select(1)
    dlg.btn_del.click()
    assert labels(dlg) == ["em4 — End mill 4 mm (Ø4 mm)", "v90 (Ø6 mm)"] and dlg.row == 1 and dlg.ident.text() == "v90"
    dlg.btn_del.click()
    assert dlg.row == 0 and dlg.ident.text() == "em4"
    dlg.btn_del.click()
    assert dlg.list.count() == 0 and not dlg.form_box.isVisible() and dlg.empty.isVisible()
    assert not dlg.btn_save.isEnabled() and not dlg.btn_dup.isEnabled() and not dlg.btn_del.isEnabled()
    dlg.btn_new.click()                               # y se puede volver a empezar
    assert dlg.form_box.isVisible() and not dlg.empty.isVisible() and dlg.ident.text() == "fresa"


def test_notes_and_every_number_reach_the_saved_card(dlg, qtbot):
    dlg.select(1)
    for key, text in (("diametro", "3,175"), ("flautas", "2"), ("longitud_util", "12"), ("rpm", "18000"),
                      ("profundidad_pasada", "1,5"), ("feed", "700"), ("entrada", "250"), ("stepover", "45")):
        type_into(qtbot, dlg.numbers[key], text)
    dlg.notes.setPlainText("  para MDF  ")
    t = dlg.editor.entries[1].tool
    assert (t.diameter, t.flutes, t.flute_length, t.rpm, t.stepdown, t.feed_xy, t.feed_z, t.stepover, t.notes) == (
        3.175, 2, 12, 18000, 1.5, 700, 250, 0.45, "para MDF")
    assert dlg.advice.text() == ""


def test_save_writes_the_file_keeps_the_backup_and_closes(dlg, path, qtbot):
    type_into(qtbot, dlg.numbers["feed"], "500")
    dlg.btn_dup.click()
    type_into(qtbot, dlg.ident, "em4_mdf")
    with qtbot.waitSignal(dlg.accepted):
        dlg.btn_save.click()
    assert dlg.saved_path == path and dlg.result() == QDialog.Accepted
    back = load_tools(path)
    assert list(back) == ["em4", "em4_mdf", "em3175", "v90"] and back["em4"].feed_xy == 500 and back["em4_mdf"].feed_xy == 500
    assert path.with_name("herramientas.json.bak").read_text(encoding="utf-8") == FILE
    assert "_ayuda" in json.loads(path.read_text(encoding="utf-8"))


def test_a_write_error_is_reported_and_the_window_stays_open(dlg, path, qtbot, monkeypatch, no_boxes):
    type_into(qtbot, dlg.numbers["feed"], "500")

    def boom(_path):
        raise PermissionError("denegado")
    monkeypatch.setattr(dlg.editor, "save", boom)
    assert dlg.save() is False
    assert dlg.result() != QDialog.Accepted and dlg.saved_path is None and dlg.changed()
    assert "denegado" in no_boxes["critical"][0] and path.read_text(encoding="utf-8") == FILE


def test_cancel_with_changes_asks_and_only_discards_when_told(dlg, path, qtbot, no_boxes):
    type_into(qtbot, dlg.numbers["feed"], "500")
    no_boxes["answer"]["question"] = QMessageBox.No
    dlg.btn_cancel.click()
    assert len(no_boxes["question"]) == 1 and dlg.isVisible()
    no_boxes["answer"]["question"] = QMessageBox.Yes
    dlg.btn_cancel.click()
    assert len(no_boxes["question"]) == 2 and dlg.result() == QDialog.Rejected
    assert path.read_text(encoding="utf-8") == FILE


def test_cancel_without_changes_closes_without_asking(dlg, no_boxes):
    dlg.select(2)                                      # mirar fichas no es cambiarlas
    dlg.btn_cancel.click()
    assert no_boxes["question"] == [] and dlg.result() == QDialog.Rejected


def test_changing_a_value_and_changing_it_back_is_not_a_change(dlg, qtbot):
    type_into(qtbot, dlg.numbers["feed"], "500")
    assert dlg.changed()
    type_into(qtbot, dlg.numbers["feed"], "300")
    assert not dlg.changed()


def test_without_a_path_nothing_can_be_saved(qtbot):
    d = ToolsDialog({"em4": Tool(diameter=4)}, None)
    qtbot.addWidget(d)
    assert not d.btn_save.isEnabled() and "ruta" in d.btn_save.toolTip() and d.save() is False


def test_the_dialog_says_where_it_saves(dlg, path):
    assert str(path) in dlg.where.text() and ".bak" in dlg.where.text()


# --- enganche con la ventana ---------------------------------------------------------------------------------
@pytest.fixture
def win(qtbot, path):
    w = MainWindow(folders=(path.parent, path.parent), use_settings=False)
    qtbot.addWidget(w)
    w.show()
    return w


def drive(monkeypatch, steps, result=QDialog.Accepted):
    """Sustituye el exec() del dialogo por unos pasos que lo manejan como lo haria una persona."""
    seen = []

    def run(self):
        seen.append(self)
        steps(self)
        return result
    monkeypatch.setattr(ToolsDialog, "exec", run)
    return seen


def test_the_button_and_ctrl_t_open_the_editor(win, monkeypatch):
    opened = []
    monkeypatch.setattr(ToolsDialog, "exec", lambda self: opened.append(self.path) or QDialog.Rejected)
    win.job.edit_tools.click()
    action = next(a for a in win.menuBar().actions()[0].menu().actions() if a.text() == "Fichas de fresas…")
    assert action.shortcut().toString() == "Ctrl+T"
    action.trigger()
    assert opened == [win.project.tools_path, win.project.tools_path] and win.project.tools_path.name == "herramientas.json"


def test_saved_cards_replace_the_ones_in_use_and_the_chosen_tool_follows_a_rename(win, path, monkeypatch, qtbot):
    win.job.tool.setCurrentIndex(win.job.tool.findData("em4"))
    win.job.thickness.setText("19")
    win.job.thickness.valueChanged.emit()
    assert win.project.general.tool == "em4"

    def steps(d):
        type_into(qtbot, d.ident, "em4_pino")
        type_into(qtbot, d.numbers["feed"], "500")
        d.btn_dup.click()
        type_into(qtbot, d.ident, "em4_mdf")
        assert d.save()
    seen = drive(monkeypatch, steps)
    assert win.open_tools_editor() is True
    assert list(win.project.tools) == ["em4_pino", "em4_mdf", "em3175", "v90"] and win.project.tools_path == path
    assert win.project.general.tool == "em4_pino" and win.job.tool_value() == "em4_pino"
    assert win.job.tool.count() == 4 and win.job.tool.findData("em4_mdf") >= 0
    assert "em4_pino" in win.statusBar().currentMessage() and "pasa a llamarse" in win.statusBar().currentMessage()
    assert win.job.feed.placeholderText() == "500 (ficha)"
    assert set(load_tools(path)) == set(win.project.tools) and path.with_name("herramientas.json.bak").exists()
    assert seen[0].parent() is win


def test_removing_the_chosen_card_says_so_and_does_not_invent_another(win, monkeypatch):
    win.job.tool.setCurrentIndex(win.job.tool.findData("em3175"))
    win.job.thickness.setText("19")
    win.job.thickness.valueChanged.emit()

    def steps(d):
        d.select(1)
        d.btn_del.click()
        assert d.save()
    drive(monkeypatch, steps)
    assert win.open_tools_editor()
    assert "em3175" not in win.project.tools and win.project.general.tool == "em3175"
    assert "ya no existe" in win.statusBar().currentMessage()


def test_cancelling_changes_nothing(win, path, monkeypatch):
    before = dict(win.project.tools)
    drive(monkeypatch, lambda d: d.btn_del.click(), result=QDialog.Rejected)
    assert win.open_tools_editor() is False
    assert win.project.tools == before and path.read_text(encoding="utf-8") == FILE
    assert not path.with_name("herramientas.json.bak").exists()


def test_the_editor_starts_from_the_file_as_it_is_now_not_as_it_was_loaded(win, path, monkeypatch):
    path.write_text('{"herramientas": {"fresa_nueva": {"diametro": 8}}}', encoding="utf-8")      # editado a mano despues
    seen = []
    drive(monkeypatch, lambda d: seen.append([e.ident for e in d.editor.entries]), result=QDialog.Rejected)
    win.open_tools_editor()
    assert seen == [["fresa_nueva"]]


def test_a_broken_file_is_not_overwritten_and_the_reason_is_shown(win, path, monkeypatch):
    path.write_text("{esto no es json", encoding="utf-8")
    seen = drive(monkeypatch, lambda d: None)
    assert win.open_tools_editor() is False
    assert seen == [] and path.read_text(encoding="utf-8") == "{esto no es json"
    assert "JSON" in win.messages.texts()[0].upper() and "a mano" in win.messages.texts()[0]
    assert set(win.project.tools) == {"em4", "em3175", "v90"}


def test_with_no_file_the_editor_starts_from_the_tools_in_use_and_creates_it(qtbot, tmp_path, monkeypatch):
    w = MainWindow(Project({"em4": Tool(diameter=4, name="em4", feed_xy=300)}), [], use_settings=False,
                   folders=(tmp_path, tmp_path))
    qtbot.addWidget(w)
    seen = []

    def steps(d):
        seen.append((d.path, [e.ident for e in d.editor.entries]))
        assert d.save()
    drive(monkeypatch, steps)
    assert w.open_tools_editor()
    target = tmp_path / "herramientas.json"
    assert seen == [(target, ["em4"])] and target.is_file() and not (tmp_path / "herramientas.json.bak").exists()
    assert load_tools(target)["em4"].feed_xy == 300 and w.project.tools_path == target


def test_f5_after_saving_reads_the_new_file_and_remembers_where_it_is(win, path, monkeypatch):
    drive(monkeypatch, lambda d: (d.btn_dup.click(), d.save()))
    win.open_tools_editor()
    assert "em4_copia" in parse_tools(path.read_text(encoding="utf-8"))
    assert win.reload_tools() and "em4_copia" in win.project.tools and win.project.tools_path == path


# --- lo que quedaba por cubrir -----------------------------------------------------------------------------------
def test_notes_are_shown_and_survive_opening_and_saving_without_touching_anything(qtbot, tmp_path):
    p = tmp_path / "herramientas.json"
    p.write_text('{"herramientas": {"em4": {"diametro": 4, "notas": "comprada en una tienda, 2 filos"}}}', encoding="utf-8")
    d = ToolsDialog(load_tools(p), p)
    qtbot.addWidget(d)
    assert d.notes.toPlainText() == "comprada en una tienda, 2 filos" and not d.changed()
    assert d.save()
    assert load_tools(p)["em4"].notes == "comprada en una tienda, 2 filos"


BOARDS19 = os.path.join(os.path.dirname(__file__), "data", "tableros_19mm.svg")


def test_saving_recalculates_the_plan_and_refreshes_the_grey_hints_of_the_table(win, monkeypatch):
    assert win.open_svg(BOARDS19, apply_default_recipe=False)
    win.job.tool.setCurrentIndex(win.job.tool.findData("em4"))
    win.job.thickness.setText("19")
    win.job.thickness.valueChanged.emit()
    win.job.stepdown.setText("6,5")                                      # 19 mm con una fresa de 4 no se corta de una pasada
    win.job.stepdown.valueChanged.emit()
    win.wait_for_plan()
    assert win.ops._widgets[0]["plunge"].placeholderText() == "200 (ficha)"
    token = win._token

    def steps(d):
        d.numbers["entrada"].setText("120")
        d.numbers["entrada"].valueChanged.emit()
        assert d.save()
    drive(monkeypatch, steps)
    assert win.open_tools_editor()
    assert win._token > token and win._timer.isActive()                 # se vuelve a calcular con la ficha nueva
    assert win.ops._widgets[0]["plunge"].placeholderText() == "120 (ficha)"
    assert win.job.plunge.placeholderText() == "120 (ficha)"
    win.wait_for_plan()
    assert win.last_plan is not None and win.last_plan.ok


def test_a_file_created_by_hand_after_starting_is_found_by_f5_and_by_the_editor(qtbot, tmp_path, monkeypatch):
    w = MainWindow(Project({"em4": Tool(diameter=4, name="em4")}), [], use_settings=False, folders=(tmp_path, tmp_path))
    qtbot.addWidget(w)
    assert w.project.tools_path is None
    target = tmp_path / "herramientas.json"
    target.write_text('{"herramientas": {"de_archivo": {"diametro": 5}}}', encoding="utf-8")
    seen = []
    drive(monkeypatch, lambda d: seen.append((d.path, [e.ident for e in d.editor.entries])), result=QDialog.Rejected)
    w.open_tools_editor()
    assert seen == [(target, ["de_archivo"])]                            # parte del archivo, no de lo que habia en memoria
    assert w.reload_tools() and w.project.tools_path == target and list(w.project.tools) == ["de_archivo"]
