"""Las dos carpetas fijas en la ventana: configurarlas, recordarlas en el PC y que abrir/guardar/generar partan de ellas."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QDir, QSettings, Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QMessageBox

from cnc import folders as fo
from cnc.gui import folder_actions
from cnc.gui.folders_dialog import FoldersDialog
from cnc.gui.main_window import MainWindow
from cnc.gui import theme
from cnc.model import Tool
from cnc.project import Project

DATA = Path(__file__).parent / "data"
BOARDS19 = DATA / "tableros_19mm.svg"


def tools():
    return {"em4": Tool(diameter=4, name="End mill 4 mm", feed_xy=300, feed_z=200, stepdown=6.5)}


@pytest.fixture
def win(qtbot):
    w = MainWindow(Project(tools()), [], use_settings=False)
    qtbot.addWidget(w)
    w.show()
    return w


@pytest.fixture
def job19(win):
    assert win.open_svg(BOARDS19, apply_default_recipe=False)
    return win


@pytest.fixture
def boxes(monkeypatch):
    """Ningun cuadro de dialogo real."""
    for name in ("warning", "critical", "information", "question"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.No))


def folder(tmp_path, name):
    d = tmp_path / name
    d.mkdir()
    return d


def dialogs(monkeypatch, open_=("", ""), save=("", "")):
    """Anota con que carpeta/nombre empiezan las ventanas de abrir y guardar, y contesta lo que se diga."""
    seen = {"open": [], "save": []}

    def get_open(*a, **k):
        seen["open"].append(a[2])
        return open_

    def get_save(*a, **k):
        seen["save"].append(a[2])
        return save
    monkeypatch.setattr("cnc.gui.main_window.QFileDialog.getOpenFileName", staticmethod(get_open))
    monkeypatch.setattr("cnc.gui.main_window.QFileDialog.getSaveFileName", staticmethod(get_save))
    return seen


def choose(monkeypatch, answer):
    """Contesta a la pregunta de la carpeta que no vale y anota con que texto se hizo."""
    asked = []

    def choice(text):
        asked.append(text)
        return answer
    monkeypatch.setattr("cnc.gui.main_window.MainWindow._folder_problem_choice", lambda self, text: choice(text))
    return asked


# --- configurarlas ----------------------------------------------------------------------------------------------
def test_at_first_no_folder_is_configured_and_dialogs_start_where_they_always_did(win, job19):
    assert win.configured_folder(fo.JOBS) == "" and win.configured_folder(fo.GCODE) == ""
    assert job19.folder_for(fo.JOBS) == job19._folder() == str(BOARDS19.parent)
    assert job19.folder_for(fo.GCODE) == str(BOARDS19.parent)


def test_the_two_folders_are_kept_apart(win, tmp_path):
    jobs, nc = folder(tmp_path, "trabajos"), folder(tmp_path, "nc")
    win.set_configured_folders({fo.JOBS: str(jobs), fo.GCODE: str(nc)})
    assert win.configured_folder(fo.JOBS) == str(jobs) and win.configured_folder(fo.GCODE) == str(nc)
    assert win.folder_for(fo.JOBS) == str(jobs) and win.folder_for(fo.GCODE) == str(nc)


def test_a_path_pasted_with_quotes_and_spaces_is_stored_clean(win, tmp_path):
    jobs = folder(tmp_path, "trabajos")
    win.set_configured_folders({fo.JOBS: f'  "{jobs}"  '})
    assert win.configured_folder(fo.JOBS) == str(jobs)
    assert win._pref(folder_actions.PREF_KEYS[fo.JOBS]) == str(jobs)           # lo guardado ya va limpio


def test_a_value_edited_by_hand_with_quotes_and_spaces_is_read_clean(win, tmp_path):
    jobs = folder(tmp_path, "trabajos")
    win._set_pref(folder_actions.PREF_KEYS[fo.JOBS], f'  "{jobs}"  ')
    assert win.configured_folder(fo.JOBS) == str(jobs)
    assert win.folder_for(fo.JOBS) == str(jobs)


def test_setting_one_folder_leaves_the_other_alone(win, tmp_path):
    jobs, nc = folder(tmp_path, "trabajos"), folder(tmp_path, "nc")
    win.set_configured_folders({fo.JOBS: str(jobs)})
    win.set_configured_folders({fo.GCODE: str(nc)})
    assert win.configured_folder(fo.JOBS) == str(jobs) and win.configured_folder(fo.GCODE) == str(nc)


def test_an_empty_path_clears_the_folder(win, tmp_path):
    jobs = folder(tmp_path, "trabajos")
    win.set_configured_folders({fo.JOBS: str(jobs)})
    win.set_configured_folders({fo.JOBS: ""})
    assert win.configured_folder(fo.JOBS) == ""


def test_a_path_that_does_not_work_is_refused_and_nothing_at_all_is_stored(win, tmp_path):
    jobs = folder(tmp_path, "trabajos")
    with pytest.raises(ValueError, match="La carpeta de G-code .* no existe"):
        win.set_configured_folders({fo.JOBS: str(jobs), fo.GCODE: str(tmp_path / "nada")})
    assert win.configured_folder(fo.JOBS) == "" and win.configured_folder(fo.GCODE) == ""


def test_a_relative_path_is_refused(win):
    with pytest.raises(ValueError, match="no es una ruta completa"):
        win.set_configured_folders({fo.JOBS: "trabajos"})


# --- recordarlas en el PC -------------------------------------------------------------------------------------
@pytest.fixture
def ini(tmp_path):
    """Donde recuerda este PC las cosas, pero en un archivo de la prueba: ni el registro ni el HOME."""
    return tmp_path / "config" / "cnc_app.ini"


def program(qtbot, ini):
    w = MainWindow(Project(tools()), [], settings_file=str(ini))
    qtbot.addWidget(w)
    return w


def test_the_folders_are_still_there_after_closing_and_opening_the_program(qtbot, tmp_path, ini, boxes):
    jobs, nc = folder(tmp_path, "trabajos"), folder(tmp_path, "nc")
    first = program(qtbot, ini)
    first.set_configured_folders({fo.JOBS: str(jobs), fo.GCODE: str(nc)})
    first.close()
    second = program(qtbot, ini)
    assert second.configured_folder(fo.JOBS) == str(jobs) and second.configured_folder(fo.GCODE) == str(nc)
    assert second.messages.texts() == [] and second.startup_notes == []


def test_the_folders_reach_the_disk_without_closing_the_program(qtbot, tmp_path, ini, boxes):
    jobs = folder(tmp_path, "trabajos")
    w = program(qtbot, ini)
    w.set_configured_folders({fo.JOBS: str(jobs)})
    assert "folder_trabajos" in ini.read_text(encoding="utf-8")
    assert QSettings(str(ini), QSettings.IniFormat).value("folder_trabajos") == str(jobs)


def test_a_stored_folder_that_no_longer_works_is_announced_when_the_program_starts(qtbot, tmp_path, ini, boxes):
    gone = tmp_path / "unidad_desconectada"
    ini.parent.mkdir()
    store = QSettings(str(ini), QSettings.IniFormat)
    store.setValue("folder_gcode", str(gone))
    store.sync()
    w = program(qtbot, ini)
    texts = w.messages.texts()
    assert len(texts) == 1 and f"La carpeta de G-code ({gone}) no existe" in texts[0]
    assert "Archivo → Carpetas de trabajos y G-code…" in texts[0] and "preguntará" in texts[0]
    assert w.configured_folder(fo.GCODE) == str(gone)                      # no se borra: se avisa y se pregunta


def test_the_startup_notes_only_say_what_is_wrong(win, tmp_path):
    jobs = folder(tmp_path, "trabajos")
    win._memory_prefs.update({"folder_trabajos": str(jobs)})
    assert win.folder_notes() == []
    win._memory_prefs["folder_gcode"] = str(tmp_path / "nada")
    (note,) = win.folder_notes()
    assert note.level == "warning" and "La carpeta de G-code" in note.text and "trabajos" not in note.text.split("(")[0]
    win._memory_prefs["folder_trabajos"] = str(tmp_path / "otra")
    assert [n.text.split(" (")[0] for n in win.folder_notes()] == ["La carpeta de trabajos", "La carpeta de G-code"]


# --- abrir y guardar parten de ellas -----------------------------------------------------------------------------
def test_open_job_starts_in_the_jobs_folder_and_not_in_the_gcode_one(job19, tmp_path, monkeypatch):
    jobs, nc = folder(tmp_path, "trabajos"), folder(tmp_path, "nc")
    job19.set_configured_folders({fo.JOBS: str(jobs), fo.GCODE: str(nc)})
    seen = dialogs(monkeypatch)
    job19.open_job_dialog()
    assert seen["open"] == [str(jobs)]


def test_open_job_without_a_configured_folder_starts_in_the_drawing_folder(job19, monkeypatch):
    seen = dialogs(monkeypatch)
    job19.open_job_dialog()
    assert seen["open"] == [str(BOARDS19.parent)]


def test_a_new_job_is_proposed_in_the_jobs_folder_with_the_drawing_name(job19, tmp_path, monkeypatch):
    jobs = folder(tmp_path, "trabajos")
    job19.set_configured_folders({fo.JOBS: str(jobs)})
    seen = dialogs(monkeypatch)
    job19.save_job_dialog()
    assert seen["save"] == [str(jobs / "tableros_19mm.cncjob")]


def test_a_job_that_already_has_a_file_is_saved_over_that_file_not_in_the_folder(job19, tmp_path, monkeypatch):
    jobs = folder(tmp_path, "trabajos")
    job19.set_configured_folders({fo.JOBS: str(jobs)})
    job19.job_path = tmp_path / "ya_guardado.cncjob"
    seen = dialogs(monkeypatch)
    job19.save_job_dialog()
    assert seen["save"] == [str(tmp_path / "ya_guardado.cncjob")]


def test_saving_in_another_folder_from_the_dialog_does_not_change_the_configured_one(job19, tmp_path, monkeypatch):
    jobs, other = folder(tmp_path, "trabajos"), folder(tmp_path, "otra")
    job19.set_configured_folders({fo.JOBS: str(jobs)})
    dialogs(monkeypatch, save=(str(other / "pieza.cncjob"), ""))
    job19.save_job_dialog()
    assert (other / "pieza.cncjob").is_file() and job19.configured_folder(fo.JOBS) == str(jobs)


def test_generate_proposes_the_gcode_folder_with_the_drawing_name(job19, tmp_path, monkeypatch):
    jobs, nc = folder(tmp_path, "trabajos"), folder(tmp_path, "nc")
    job19.set_configured_folders({fo.JOBS: str(jobs), fo.GCODE: str(nc)})
    seen = dialogs(monkeypatch)
    assert job19._ask_base([(1, None), (2, None)]) is None
    assert seen["save"] == [str(nc / "tableros_19mm.nc")]


def test_the_name_chosen_in_the_dialog_is_used_with_the_nc_suffix(job19, tmp_path, monkeypatch):
    nc = folder(tmp_path, "nc")
    job19.set_configured_folders({fo.GCODE: str(nc)})
    dialogs(monkeypatch, save=(str(nc / "pieza"), ""))
    assert job19._ask_base([(1, None)]) == nc / "pieza.nc"


def test_generate_without_a_configured_folder_proposes_the_drawing_folder(job19, monkeypatch):
    seen = dialogs(monkeypatch)
    job19._ask_base([(1, None)])
    assert seen["save"] == [str(BOARDS19.parent / "tableros_19mm.nc")]


@pytest.fixture
def hold(job19, boxes):
    job19.job.tool.setCurrentIndex(job19.job.tool.findData("em4"))
    job19.job.thickness.setText("19")
    job19.job.thickness.valueChanged.emit()
    job19.wait_for_plan()
    job19.tabs.setCurrentWidget(job19.hold_panel)
    return job19


def test_the_marks_program_is_proposed_in_the_gcode_folder(hold, tmp_path, monkeypatch):
    nc = folder(tmp_path, "nc")
    hold.set_configured_folders({fo.GCODE: str(nc), fo.JOBS: str(folder(tmp_path, "trabajos"))})
    seen = dialogs(monkeypatch)
    assert hold.generate_marks() is None
    assert seen["save"] == [str(nc / "tableros_19mm_tablero1_marcas.nc")]


def test_the_marks_program_without_a_configured_folder_starts_in_the_drawing_folder(hold, monkeypatch):
    seen = dialogs(monkeypatch)
    assert hold.generate_marks() is None
    assert seen["save"] == [str(BOARDS19.parent / "tableros_19mm_tablero1_marcas.nc")]


# --- una carpeta configurada que ya no vale -------------------------------------------------------------------------
def test_cancelling_the_question_opens_no_dialog_and_does_nothing(hold, tmp_path, monkeypatch):
    gone = tmp_path / "desconectada"
    hold._memory_prefs.update({"folder_trabajos": str(gone), "folder_gcode": str(gone)})
    asked = choose(monkeypatch, "cancel")
    seen = dialogs(monkeypatch)
    hold.open_job_dialog()
    hold.save_job_dialog()
    assert hold._ask_base([(1, None)]) is None and hold.generate_marks() is None
    assert seen == {"open": [], "save": []} and len(asked) == 4
    assert asked[0] == fo.describe(fo.JOBS, str(gone)) and asked[2] == fo.describe(fo.GCODE, str(gone))


def test_using_the_drawing_folder_this_once_does_not_change_what_is_configured(hold, tmp_path, monkeypatch):
    gone = tmp_path / "desconectada"
    hold._memory_prefs["folder_gcode"] = str(gone)
    choose(monkeypatch, "last")
    seen = dialogs(monkeypatch)
    hold._ask_base([(1, None)])
    assert seen["save"] == [str(BOARDS19.parent / "tableros_19mm.nc")]
    assert hold.configured_folder(fo.GCODE) == str(gone)


def test_a_folder_that_is_fine_never_asks(job19, tmp_path, monkeypatch):
    nc = folder(tmp_path, "nc")
    job19.set_configured_folders({fo.GCODE: str(nc)})
    choose(monkeypatch, "cancel")
    monkeypatch.setattr("cnc.gui.main_window.MainWindow._folder_problem_choice",
                        lambda self, text: (_ for _ in ()).throw(AssertionError("no tenia que preguntar")))
    assert job19.folder_for(fo.GCODE) == str(nc)
    assert job19.folder_for(fo.JOBS) == str(BOARDS19.parent)


def test_configuring_again_continues_with_the_new_folder(job19, tmp_path, monkeypatch):
    gone, new = tmp_path / "desconectada", folder(tmp_path, "nueva")
    job19._memory_prefs["folder_gcode"] = str(gone)
    choose(monkeypatch, "configure")

    def fix():
        job19.set_configured_folders({fo.GCODE: str(new)})
        return True
    monkeypatch.setattr(job19, "open_folders_dialog", fix)
    assert job19.folder_for(fo.GCODE) == str(new)


def test_configuring_and_cancelling_the_dialog_cancels(job19, tmp_path, monkeypatch):
    job19._memory_prefs["folder_gcode"] = str(tmp_path / "desconectada")
    choose(monkeypatch, "configure")
    monkeypatch.setattr(job19, "open_folders_dialog", lambda: False)
    assert job19.folder_for(fo.GCODE) is None


def test_cancelling_the_dialog_cancels_even_if_the_drive_came_back_meanwhile(job19, tmp_path, monkeypatch):
    back = folder(tmp_path, "vuelta")
    job19._memory_prefs["folder_gcode"] = str(back / "desconectada")
    choose(monkeypatch, "configure")

    def reconnected_but_cancelled():
        job19._memory_prefs["folder_gcode"] = str(back)            # ahora vale, pero se ha pulsado Cancelar
        return False
    monkeypatch.setattr(job19, "open_folders_dialog", reconnected_but_cancelled)
    assert job19.folder_for(fo.GCODE) is None


def test_configuring_but_leaving_it_broken_cancels_instead_of_asking_forever(job19, tmp_path, monkeypatch):
    job19._memory_prefs["folder_gcode"] = str(tmp_path / "desconectada")
    asked = choose(monkeypatch, "configure")
    monkeypatch.setattr(job19, "open_folders_dialog", lambda: True)
    assert job19.folder_for(fo.GCODE) is None and len(asked) == 1


def test_configuring_and_clearing_the_folder_goes_back_to_the_drawing_folder(job19, tmp_path, monkeypatch):
    job19._memory_prefs["folder_gcode"] = str(tmp_path / "desconectada")
    choose(monkeypatch, "configure")

    def clear():
        job19.set_configured_folders({fo.GCODE: ""})
        return True
    monkeypatch.setattr(job19, "open_folders_dialog", clear)
    assert job19.folder_for(fo.GCODE) == str(BOARDS19.parent)


def test_without_a_window_to_answer_the_question_is_cancelled(win):
    assert win.settings is None and win._folder_problem_choice("lo que sea") == "cancel"


@pytest.fixture
def real_box(win, tmp_path, monkeypatch):
    """La pregunta de verdad (con QSettings en un archivo), sin esperar a nadie: se pulsa el boton que se diga."""
    win.settings = QSettings(str(tmp_path / "x.ini"), QSettings.IniFormat)
    seen = {}

    def run(text, press):
        def exec_(self):
            seen.update(title=self.windowTitle(), text=self.text(), icon=self.icon(),
                        buttons=[b.text() for b in self.buttons()], default=self.defaultButton().text())
            return 0
        monkeypatch.setattr(QMessageBox, "exec", exec_)
        monkeypatch.setattr(QMessageBox, "clickedButton",
                            lambda self: next((b for b in self.buttons() if b.text() == press), None))
        return win._folder_problem_choice(text), seen
    return run


@pytest.mark.parametrize("press, expected", [("Configurar…", "configure"), ("Usar la carpeta del dibujo esta vez", "last"),
                                            ("Cancelar", "cancel"), (None, "cancel")])
def test_the_question_has_three_ways_out_and_cancel_is_the_default(real_box, press, expected):
    answer, seen = real_box("La carpeta de G-code (D:\\nc) no existe.", press)
    assert answer == expected
    assert seen["title"] == "Carpeta no disponible" and seen["icon"] == QMessageBox.Warning
    assert seen["text"] == "La carpeta de G-code (D:\\nc) no existe.\n\n¿Qué quieres hacer?"
    assert sorted(seen["buttons"]) == sorted(["Configurar…", "Usar la carpeta del dibujo esta vez", "Cancelar"])
    assert seen["default"] == "Cancelar"


# --- el dialogo y el menu ----------------------------------------------------------------------------------------
def fake_exec(monkeypatch, values=None, result=QDialog.Accepted):
    made = []

    def exec_(self):
        made.append(self)
        for kind, text in (values or {}).items():
            self.edits[kind].setText(text)
        return result
    monkeypatch.setattr(FoldersDialog, "exec", exec_)
    return made


def test_the_dialog_saves_what_was_accepted_and_says_so(win, tmp_path, monkeypatch):
    jobs = folder(tmp_path, "trabajos")
    made = fake_exec(monkeypatch, {fo.JOBS: str(jobs)})
    assert win.open_folders_dialog() is True
    assert win.configured_folder(fo.JOBS) == str(jobs) and win.configured_folder(fo.GCODE) == ""
    assert win.statusBar().currentMessage() == (f"Carpetas guardadas. Trabajos (.cncjob): {jobs}, "
                                                "G-code (.nc): sin configurar")
    assert made[0].parent() is win


def test_the_dialog_starts_with_what_is_configured(win, tmp_path, monkeypatch):
    nc = folder(tmp_path, "nc")
    win.set_configured_folders({fo.GCODE: str(nc)})
    made = fake_exec(monkeypatch)
    win.open_folders_dialog()
    assert made[0].values() == {fo.JOBS: "", fo.GCODE: str(nc)}


def test_cancelling_the_dialog_changes_nothing(win, tmp_path, monkeypatch):
    jobs = folder(tmp_path, "trabajos")
    fake_exec(monkeypatch, {fo.JOBS: str(jobs)}, result=QDialog.Rejected)
    before = win.statusBar().currentMessage()
    assert win.open_folders_dialog() is False
    assert win.configured_folder(fo.JOBS) == "" and win.statusBar().currentMessage() == before


def test_the_file_menu_has_the_folders_entry(win, monkeypatch):
    menu = win.menuBar().actions()[0].menu()
    names = [a.text() for a in menu.actions()]
    assert "Carpetas de trabajos y G-code…" in names
    assert names.index("Carpetas de trabajos y G-code…") == names.index("Recargar máquinas") + 1
    called = []
    monkeypatch.setattr(FoldersDialog, "exec", lambda self: called.append(1) or QDialog.Rejected)
    menu.actions()[names.index("Carpetas de trabajos y G-code…")].trigger()
    assert called == [1]


# --- el dialogo solo -------------------------------------------------------------------------------------------------
@pytest.fixture
def dlg(qtbot):
    d = FoldersDialog({})
    qtbot.addWidget(d)
    d.show()
    return d


def ok(d):
    return d.buttons.button(QDialogButtonBox.Ok)


def test_the_dialog_shows_both_folders_with_their_help(dlg):
    texts = [label.text() for label in dlg.findChildren(QLabel)]
    for kind in fo.KINDS:
        assert fo.LABELS[kind] in texts and fo.HELP[kind] in texts
    assert dlg.windowTitle() == "Carpetas de trabajos y G-code"
    assert list(dlg.edits) == list(fo.KINDS)
    assert all(e.placeholderText() == "Sin configurar: la carpeta del dibujo" for e in dlg.edits.values())


def test_with_nothing_written_the_dialog_can_be_accepted(dlg):
    assert ok(dlg).isEnabled() and dlg.values() == {fo.JOBS: "", fo.GCODE: ""}
    assert [n.text() for n in dlg.notes.values()] == ["", ""]


def test_the_dialog_starts_with_the_current_folders_clean(qtbot, tmp_path):
    jobs = folder(tmp_path, "trabajos")
    d = FoldersDialog({fo.JOBS: f' "{jobs}" '})
    qtbot.addWidget(d)
    assert d.edits[fo.JOBS].text() == str(jobs) and d.edits[fo.GCODE].text() == ""
    assert ok(d).isEnabled() and d.notes[fo.JOBS].text() == "Vale."


def test_a_good_path_says_it_is_fine(dlg, tmp_path):
    dlg.edits[fo.GCODE].setText(str(tmp_path))
    assert dlg.notes[fo.GCODE].text() == "Vale." and dlg.notes[fo.JOBS].text() == ""
    assert theme.MUTED in dlg.notes[fo.GCODE].styleSheet() and ok(dlg).isEnabled()


def test_a_bad_path_says_why_in_red_and_blocks_accept_until_fixed(dlg, tmp_path):
    missing = str(tmp_path / "nada")
    dlg.edits[fo.JOBS].setText(missing)
    assert dlg.notes[fo.JOBS].text() == f"«{missing}» no existe (¿está conectada la unidad?)."
    assert theme.ERROR in dlg.notes[fo.JOBS].styleSheet() and not ok(dlg).isEnabled()
    dlg.edits[fo.JOBS].setText(str(tmp_path))
    assert ok(dlg).isEnabled() and dlg.notes[fo.JOBS].text() == "Vale." and theme.MUTED in dlg.notes[fo.JOBS].styleSheet()


def test_one_bad_path_blocks_accept_even_if_the_other_is_good(dlg, tmp_path):
    dlg.edits[fo.JOBS].setText(str(tmp_path))
    dlg.edits[fo.GCODE].setText("D:\\no_existe_nada_aqui" if os.name != "nt" else "Z:\\no_existe_nada_aqui")
    assert not ok(dlg).isEnabled() and dlg.notes[fo.JOBS].text() == "Vale."


def test_the_path_is_shown_clean_in_the_message_and_returned_clean(dlg, tmp_path):
    dlg.edits[fo.GCODE].setText(f'  "{tmp_path}"  ')
    assert dlg.values()[fo.GCODE] == str(tmp_path) and dlg.notes[fo.GCODE].text() == "Vale."
    dlg.edits[fo.GCODE].setText(f'"{tmp_path}/nada"')
    assert dlg.notes[fo.GCODE].text().startswith(f"«{tmp_path}/nada» ")


def test_ok_closes_the_dialog_as_accepted_and_cancel_as_rejected(dlg, qtbot):
    qtbot.mouseClick(ok(dlg), Qt.LeftButton)
    assert dlg.result() == QDialog.Accepted
    dlg.show()
    qtbot.mouseClick(dlg.buttons.button(QDialogButtonBox.Cancel), Qt.LeftButton)
    assert dlg.result() == QDialog.Rejected


def test_browse_fills_the_box_with_the_folder_chosen(dlg, tmp_path, monkeypatch):
    chosen = folder(tmp_path, "elegida")
    dlg.edits[fo.GCODE].setText(str(tmp_path))
    args = []
    monkeypatch.setattr("cnc.gui.folders_dialog.QFileDialog.getExistingDirectory",
                        staticmethod(lambda *a, **k: args.append(a) or str(chosen)))
    dlg._browse[fo.GCODE].click()
    assert dlg.edits[fo.GCODE].text() == QDir.toNativeSeparators(str(chosen)) and dlg.edits[fo.JOBS].text() == ""
    assert args[0][2] == str(tmp_path) and "G-code (.nc)" in args[0][1]


def test_browse_for_the_jobs_folder_asks_with_its_own_name_and_start(dlg, tmp_path, monkeypatch):
    args = []
    monkeypatch.setattr("cnc.gui.folders_dialog.QFileDialog.getExistingDirectory",
                        staticmethod(lambda *a, **k: args.append(a) or ""))
    dlg.edits[fo.JOBS].setText(str(tmp_path))
    dlg._browse[fo.JOBS].click()
    assert args[0][2] == str(tmp_path) and "Trabajos (.cncjob)" in args[0][1]
    assert dlg.edits[fo.JOBS].text() == str(tmp_path)                       # cancelar no borra lo escrito


def test_the_folder_actions_module_knows_both_preference_keys():
    assert folder_actions.PREF_KEYS == {fo.JOBS: "folder_trabajos", fo.GCODE: "folder_gcode"}
