"""La ventana (PySide6) sin pantalla: abrir un dibujo, editar la tabla y los ajustes, calcular y generar."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from gcodecmp import assert_same_gcode

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from cnc.gui.canvas import ITEM_KIND
from cnc.gui.main_window import MainWindow
from cnc.model import Tool
from cnc.oprows import plain, shape_key
from cnc.project import General, Project
from cnc.recipe import load_recipe

DATA = Path(__file__).parent / "data"
BOARDS19 = DATA / "tableros_19mm.svg"
RECIPE = DATA / "receta_ejemplo.toml"


def tools():
    return {"em4": Tool(diameter=4, name="End mill 4 mm", feed_xy=300, feed_z=200, stepdown=6.5),
            "em3175": Tool(diameter=3.175, name="End mill 1/8")}


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


def pick_tool(win, ident="em4", thickness="19"):
    win.job.tool.setCurrentIndex(win.job.tool.findData(ident))
    win.job.thickness.setText(thickness)
    win.job.thickness.valueChanged.emit()


def items_of(canvas, kind):
    return [i for i in canvas.scene().items() if i.data(ITEM_KIND) == kind]


# --- abrir ------------------------------------------------------------------------------------------
def test_the_window_starts_empty_and_says_what_to_do(win):
    assert win.project.drawing is None and not win.generate_btn.isEnabled() and win.canvas.mode == "empty"
    assert win.boards.count() == 0 and win.ops.table.rowCount() == 0


def test_opening_the_drawing_fills_boards_rows_and_canvas(job19):
    assert [job19.boards.item(i).text() for i in range(2)] == ["Tablero 1 (620 x 1000 mm)", "Tablero 2 (620 x 1000 mm)"]
    assert all(job19.boards.item(i).checkState() == Qt.Checked for i in range(2))
    assert job19.windowTitle().endswith("tableros_19mm.svg")
    t = job19.ops.table
    assert [t.item(i, 1).text() for i in range(4)] == ["95", "13", "10", "8"]      # la tabla va en orden de corte: los huecos primero
    assert job19.canvas.mode == "drawing"
    assert len(items_of(job19.canvas, "shape")) == 126 and len(items_of(job19.canvas, "board")) == 2
    assert job19.current == 1 and job19.generate_btn.isEnabled()
    assert job19.ops.note.text().startswith("Todo son cortes pasantes normales")


def test_a_file_that_is_not_a_drawing_is_reported_without_crashing(win, tmp_path):
    bad = tmp_path / "mal.svg"
    bad.write_text("esto no es un svg")
    assert win.open_svg(bad) is False
    assert win.project.drawing is None and "mal.svg" in win.messages.texts()[0]
    assert win.open_svg(tmp_path / "no_existe.svg") is False


def test_startup_notes_are_shown(qtbot):
    from cnc.pipeline import Message
    w = MainWindow(Project(), [Message("no encuentro herramientas.json", "warning")], use_settings=False)
    qtbot.addWidget(w)
    assert "no encuentro herramientas.json" in w.messages.texts()[0] and "⚠ 1" in w.tabs.tabText(1)


# --- tabla ---------------------------------------------------------------------------------------------
def test_table_edits_change_the_project_rows(job19):
    w0 = job19.ops._widgets
    row = job19.project.rows[1]
    assert not w0[1]["depth"].isEnabled() and w0[1]["side"].isEnabled()           # pasante: no hay profundidad que dar
    w0[1]["action"].setCurrentIndex(w0[1]["action"].findData("vaciado"))
    assert row.action == "vaciado" and w0[1]["stepover"].isEnabled() and not w0[1]["side"].isEnabled()
    w0[1]["through"].setChecked(False)
    assert row.through is False and w0[1]["depth"].isEnabled()
    w0[1]["through"].setChecked(True)
    assert row.through is True and not w0[1]["depth"].isEnabled()
    w0[1]["through"].setChecked(False)
    w0[1]["depth"].setText("3,5")
    w0[1]["depth"].valueChanged.emit()
    w0[1]["stepover"].setText("60")
    w0[1]["stepover"].valueChanged.emit()
    w0[1]["feed"].setText("1200")
    w0[1]["feed"].valueChanged.emit()
    assert (row.depth, row.stepover, row.feed) == (3.5, 60.0, 1200.0)
    w0[1]["feed"].setText("")
    w0[1]["feed"].valueChanged.emit()
    assert row.feed is None                                                   # vacio = el del trabajo o la ficha
    assert not plain(job19.project.rows) and job19.ops.note.text().startswith("Receta de 4")
    w0[1]["action"].setCurrentIndex(w0[1]["action"].findData("ignorar"))
    assert not w0[1]["depth"].isEnabled() and not w0[1]["feed"].isEnabled() and not row.active


def test_moving_a_row_changes_the_order_of_the_recipe(job19):
    job19.ops.table.selectRow(0)
    first = job19.project.rows[0].key
    job19.ops._move(1)
    assert job19.project.rows[1].key == first and job19.ops.current_key() == first
    job19.ops._move(-1)
    assert job19.project.rows[0].key == first
    job19.ops._move(-1)                                                          # ya esta arriba: no pasa nada
    assert job19.project.rows[0].key == first


def test_clicking_a_shape_selects_its_row_and_clicking_a_board_selects_the_board(job19):
    shapes = job19.project.drawing.shapes
    index = next(i for i, s in enumerate(shapes) if s.fill == "#000000" and s.stroke is None)
    job19.canvas.shapeClicked.emit(index)
    assert job19.ops.current_key() == shape_key(shapes[index]) and job19.project.rows[job19.ops.table.currentRow()].fill == "#000000"
    job19.canvas.boardClicked.emit(2)
    assert job19.current == 2 and job19.boards.currentRow() == 1 and job19.job.board_box.title() == "Tablero 2"


def test_ignored_colors_are_dimmed_in_the_drawing(job19):
    def alphas():
        return {round(i.pen().color().alpha()) for i in items_of(job19.canvas, "shape")}
    assert alphas() == {255}
    w0 = job19.ops._widgets
    w0[2]["action"].setCurrentIndex(w0[2]["action"].findData("ignorar"))
    assert 70 in alphas()


# --- ajustes y calculo --------------------------------------------------------------------------------------
def test_without_a_tool_or_thickness_the_plan_is_an_error_the_window_explains(job19):
    job19.plan_now()
    assert job19.last_plan.error and "fresa" in job19.last_plan.error and "✖" in job19.summary.text()
    job19.set_view("machine")
    assert job19.canvas.mode == "empty"                                           # un mensaje en vez de la trayectoria
    assert job19.messages.errors == 1 and "✖" in job19.tabs.tabText(1)
    pick_tool(job19, thickness="")
    job19.plan_now()
    assert "grosor" in job19.last_plan.error


def test_settings_in_the_panel_reach_the_project(job19):
    pick_tool(job19, "em4", "19")
    j = job19.job
    j.margin.setText("0,5")
    j.margin.valueChanged.emit()
    j.rotate.setCurrentIndex(j.rotate.findData("90"))
    j.pause.setChecked(True)
    j.end_code.setPlainText("M9\nG0 X0 Y0")
    g = job19.project.general
    assert (g.tool, g.thickness, g.margin, g.rotate, g.pause_at_start, g.end_code) == (
        "em4", 19.0, 0.5, "90", True, "M9\nG0 X0 Y0")
    assert j.feed.placeholderText() == "300 (ficha)" and j.plunge.placeholderText() == "200 (ficha)"
    j.tool.setEditText("3,5")                                                    # un diametro escrito a mano
    j.tool.lineEdit().textEdited.emit("3,5")
    assert job19.project.general.tool == "3,5" and j.feed.placeholderText() == ""
    assert job19.project.resolve_tool().diameter == 3.5


def test_board_settings_only_touch_that_board(job19):
    pick_tool(job19)
    j = job19.job
    j.b_thickness.setText("6")
    j.b_thickness.valueChanged.emit()
    j.b_dy.setText("7")
    j.b_dy.valueChanged.emit()
    j.b_rotate.setCurrentIndex(j.b_rotate.findData("90"))
    assert job19.project.overrides[1].thickness == 6.0 and job19.project.overrides[1].offset == (0.0, 7.0)
    assert job19.project.overrides[1].rotate == "90"
    assert job19.project.overrides[2].thickness is None and job19.project.overrides[2].offset is None
    assert job19.project.overrides[2].rotate is None
    job19.boards.setCurrentRow(1)                                                  # al cambiar de tablero, sus propios valores
    assert j.b_thickness.value() is None and j.b_thickness.placeholderText() == "19"
    assert j.b_rotate.currentData() == ""
    job19.boards.setCurrentRow(0)
    assert j.b_thickness.value() == 6.0 and j.b_dy.value() == 7.0 and j.b_rotate.currentData() == "90"
    j.b_rotate.setCurrentIndex(j.b_rotate.findData(""))
    assert job19.project.overrides[1].rotate is None                               # "(el general)" quita el giro propio


def test_unchecking_a_board_removes_it_from_the_job(job19):
    job19.boards.item(1).setCheckState(Qt.Unchecked)
    assert job19.project.overrides[2].selected is False and job19.project.selected_numbers() == [1]
    job19.boards.setCurrentRow(1)
    assert job19.job.b_selected.isChecked() is False
    job19.job.b_selected.setChecked(True)
    assert job19.boards.item(1).checkState() == Qt.Checked and job19.project.selected_numbers() == [1, 2]
    job19.boards.item(0).setCheckState(Qt.Unchecked)
    job19.boards.item(1).setCheckState(Qt.Unchecked)
    assert not job19.generate_btn.isEnabled()


def test_the_plan_is_calculated_in_the_background_and_drawn_on_the_machine_view(job19, qtbot):
    pick_tool(job19)
    job19.wait_for_plan()
    plan = job19.last_plan
    assert plan is not None and plan.ok and "✔" in job19.summary.text() and "38094 líneas" in job19.summary.text()
    job19.set_view("machine")
    assert job19.canvas.mode == "machine" and job19.canvas.scene().items()
    assert job19.legend.text().count("■") == 3                                     # hueco, pieza y la linea central
    assert all(k in job19.legend.text() for k in ("Hueco", "Pieza", "Línea"))
    assert any("Giro:" in t for t in job19.messages.texts())
    # el calculo del hilo es el mismo que el del proyecto
    assert_same_gcode(plan.text, job19.project.plan(1).text)


def test_only_the_latest_request_is_applied(job19):
    pick_tool(job19)
    job19.plan_now()
    keep = job19.last_plan
    stale = job19.project.plan(1)
    job19._apply_plan(job19._token - 1, 1, stale, [])                                # una respuesta vieja
    assert job19.last_plan is keep
    job19._apply_plan(job19._token, 2, stale, [])                                    # de otro tablero
    assert job19.last_plan is keep


def test_switching_boards_replans_for_the_new_board(job19):
    pick_tool(job19)
    job19.plan_now()
    assert "Tablero 1" in job19.summary.text()
    job19.boards.setCurrentRow(1)
    assert job19.last_plan is None                                                 # el del otro tablero ya no vale
    job19.wait_for_plan()
    assert job19.last_plan.ok and "Tablero 2" in job19.summary.text() and job19.last_number == 2


def test_overhang_offers_the_suggested_offset_and_applying_it_fixes_it(job19):
    pick_tool(job19)
    job19.load_recipe(RECIPE)
    job19.plan_now()
    assert job19.last_plan.overhang and not job19.messages.apply.isHidden()
    assert "(X 0, Y 7 mm)" in job19.messages.apply.text()
    assert not any("--offset" in t for t in job19.messages.texts())               # en la ventana no se habla de la linea de comandos
    job19.messages.apply.click()
    assert job19.project.overrides[1].offset == (0.0, 7.0) and job19.job.b_dy.value() == 7.0
    job19.wait_for_plan()
    assert job19.last_plan.overhang == {} and job19.messages.apply.isHidden()


# --- recetas ---------------------------------------------------------------------------------------------------
def test_loading_and_saving_a_recipe_goes_through_the_table(job19, tmp_path):
    assert job19.load_recipe(RECIPE)
    assert [r.action for r in job19.project.rows] == ["vaciado", "vaciado", "corte", "ignorar"]
    assert [job19.ops.table.cellWidget(i, 2).currentData() for i in range(4)] == ["vaciado", "vaciado", "corte", "ignorar"]
    assert job19.ops.note.text().startswith("Receta de 3")
    out = tmp_path / "mia.toml"
    assert job19.save_recipe(out)
    again = load_recipe(out)
    assert [o.kind for o in again.operations] == ["vaciado", "vaciado", "corte"]
    job19.reset_rows()
    assert plain(job19.project.rows) and job19.ops.table.cellWidget(0, 2).currentData() == "corte"
    assert job19.load_recipe(tmp_path / "no_existe.toml") is False
    assert plain(job19.project.rows)                                              # un error no deja la tabla a medias


def test_a_recipe_with_a_missing_depth_is_an_error_in_the_plan(job19):
    pick_tool(job19)
    w0 = job19.ops._widgets
    w0[0]["through"].setChecked(False)                                           # no pasante y sin profundidad
    job19.plan_now()
    assert job19.last_plan.error and "profundidad" in job19.last_plan.error.lower()


# --- generar ----------------------------------------------------------------------------------------------------------
def test_generate_writes_one_file_per_checked_board_equal_to_the_project_output(job19, tmp_path):
    pick_tool(job19)
    job19.project.general.rotate = "0"
    job19.job.refresh()
    paths = job19.generate(base=tmp_path / "job19.nc", confirm=False)
    assert [p.name for p in paths] == ["job19_tablero1.nc", "job19_tablero2.nc"]
    for n, p in zip((1, 2), paths):
        assert_same_gcode(p.read_text(), job19.project.plan(n).text, f"tablero {n}")
    assert "G-code generado" in job19.messages.texts()[0] and "job19_tablero2.nc" in " ".join(job19.messages.texts())
    job19.boards.item(1).setCheckState(Qt.Unchecked)
    (one,) = job19.generate(base=tmp_path / "solo.nc", confirm=False)
    assert one.name == "solo.nc"


def test_generate_with_a_problem_writes_nothing_and_says_why(job19, tmp_path):
    assert job19.generate(base=tmp_path / "x.nc", confirm=False) == []             # sin fresa ni grosor
    assert list(tmp_path.iterdir()) == [] and "fresa" in job19.messages.texts()[0]
    pick_tool(job19)
    job19.project.overrides[2].thickness = -3.0
    assert job19.generate(base=tmp_path / "x.nc", confirm=False) == []
    assert list(tmp_path.iterdir()) == [] and "Tablero 2" in job19.messages.texts()[0]


def test_generate_in_a_job_that_does_not_fit_is_refused_without_asking(job19, tmp_path, monkeypatch):
    """Que no quepa en la maquina ya no se confirma: no se genera, ni se llega a abrir la lista de comprobacion."""
    opened = []
    monkeypatch.setattr(job19, "_run_checklist", lambda checklist: opened.append(checklist) or True)
    pick_tool(job19)
    job19.project.general.rotate = "90"                                            # 1000 mm de tablero a lo largo de X (651)
    job19.job.refresh()
    assert job19.generate(base=tmp_path / "x.nc", confirm=True) == []
    assert opened == [] and list(tmp_path.iterdir()) == []
    assert "No cabe en la maquina" in job19.messages.texts()[0] and "Tablero 1" in job19.messages.texts()[0]


def test_generate_asks_before_replacing_a_file(job19, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    asked, answer = [], [QMessageBox.No]
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda parent, title, *a, **k: (asked.append(title), answer[0])[1]))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(job19, "_run_checklist", lambda checklist: True)             # la lista se prueba en test_gui_checklist
    pick_tool(job19)
    job19.project.general.rotate = "0"
    job19.job.refresh()
    job19.boards.item(1).setCheckState(Qt.Unchecked)
    target = tmp_path / "v.nc"
    target.write_text("viejo")
    assert job19.generate(base=target, confirm=True) == [] and target.read_text() == "viejo"
    assert asked == ["Sustituir archivos"]
    answer[0] = QMessageBox.Yes
    assert job19.generate(base=target, confirm=True) == [target] and target.read_text().startswith("(Generado por cnc_app)")


# --- guardar y abrir el trabajo ----------------------------------------------------------------------------------
def test_a_saved_job_reopens_with_settings_boards_and_rows(job19, qtbot, tmp_path):
    pick_tool(job19)
    job19.load_recipe(RECIPE)
    job19.project.overrides[1].offset = (0.0, 7.0)
    job19.boards.item(1).setCheckState(Qt.Unchecked)
    job = tmp_path / "job19.cncjob"
    assert job19.save_job(job)
    other = MainWindow(Project(tools()), [], use_settings=False)
    qtbot.addWidget(other)
    assert other.open_job(job)
    p = other.project
    assert p.general.tool == "em4" and p.general.thickness == 19.0
    assert [r.action for r in p.rows] == ["vaciado", "vaciado", "corte", "ignorar"]
    assert p.overrides[1].offset == (0.0, 7.0) and p.selected_numbers() == [1]
    assert other.job.tool.currentData() == "em4" and other.boards.item(1).checkState() == Qt.Unchecked
    assert other.ops.table.cellWidget(0, 2).currentData() == "vaciado" and other.job_path == job
    assert other.open_job(tmp_path / "no.cncjob") is False


def test_a_job_whose_drawing_moved_is_found_next_to_the_job_file(job19, qtbot, tmp_path, monkeypatch):
    import shutil
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", "")))   # no preguntar: buscarlo solo
    pick_tool(job19)
    svg = tmp_path / "tableros_19mm.svg"
    shutil.copy(BOARDS19, svg)
    job19.open_svg(svg, apply_default_recipe=False)
    job = tmp_path / "t.cncjob"
    job19.save_job(job)
    data = job.read_text(encoding="utf-8").replace(str(svg).replace("\\", "\\\\"), "/ya/no/esta/tableros_19mm.svg")
    import json
    raw = json.loads(data)
    raw.pop("svg_incrustado")                                   # un trabajo de antes: no lleva el dibujo dentro
    job.write_text(json.dumps(raw), encoding="utf-8")
    other = MainWindow(Project(tools()), [], use_settings=False)
    qtbot.addWidget(other)
    assert other.open_job(job) and other.project.drawing.path == svg


def test_a_job_carries_its_drawing_and_opens_without_the_svg(job19, qtbot, tmp_path, monkeypatch):
    import shutil
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", "")))   # si preguntara, falla
    pick_tool(job19)
    svg = tmp_path / "tableros_19mm.svg"
    shutil.copy(BOARDS19, svg)
    job19.open_svg(svg, apply_default_recipe=False)
    job19.load_recipe(RECIPE)
    first = job19.project.plan(1).text
    job = tmp_path / "t.cncjob"
    assert job19.save_job(job)
    svg.unlink()                                                # el SVG ya no existe en ningun sitio
    other = MainWindow(Project(tools()), [], use_settings=False)
    qtbot.addWidget(other)
    assert other.open_job(job)                                  # y no pregunta dónde está: el trabajo lo lleva dentro
    assert other.project.drawing.path == svg and other.project.svg_source == BOARDS19.read_bytes()
    assert other.project.plan(1).text == first
    again = tmp_path / "otra" / "copia.cncjob"
    again.parent.mkdir()
    assert other.save_job(again)                                # al guardar de nuevo sigue llevándolo
    assert Project.embedded_svg(__import__("json").loads(again.read_text(encoding="utf-8")))[0] == BOARDS19.read_bytes()


def test_turning_board_detection_off_rereads_the_drawing_and_keeps_the_settings(tmp_path, win):
    svg = tmp_path / "d.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="60mm" viewBox="0 0 100 60">'
                   '<rect x="10" y="10" width="40" height="30" stroke="#ff0000" fill="none"/>'
                   '<circle cx="30" cy="25" r="5" stroke="#ff0000" fill="none"/></svg>')
    win.open_svg(svg)
    pick_tool(win, "em4", "6")
    assert win.boards.count() == 1 and win.boards.item(0).text().startswith("Tablero 1")
    w0 = win.ops._widgets[0]
    w0["through"].setChecked(False)
    w0["depth"].setText("2")
    w0["depth"].valueChanged.emit()                                              # una edicion de la tabla...
    win.detect_boards.setChecked(False)
    assert (win.project.rows[0].through, win.project.rows[0].depth) == (False, 2.0)   # ...sobrevive a la relectura
    assert win.ops.table.cellWidget(0, 6).value() == 2.0
    assert win.boards.item(0).text() == "Dibujo completo" and Qt.ItemIsUserCheckable not in win.boards.item(0).flags()
    assert win.project.general.thickness == 6.0 and win.job.board_box.isEnabled() is False
    win.plan_now()
    assert win.last_plan.ok and len(win.last_plan.operations) == 2 and win.last_plan.operations[0].depth == 2.0


def test_a_drawing_without_boards_generates_one_file_with_the_name_given(tmp_path, win):
    svg = tmp_path / "d.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="60mm" viewBox="0 0 100 60">'
                   '<polygon points="10,10 60,10 35,50" stroke="#ff0000" fill="none"/>'
                   '<circle cx="35" cy="25" r="5" stroke="#ff0000" fill="none"/></svg>')
    win.open_svg(svg)
    pick_tool(win, "em4", "6")
    (nc,) = win.generate(base=tmp_path / "pieza.nc", confirm=False)
    assert nc.name == "pieza.nc" and "M3 S20000" in nc.read_text()
    assert_same_gcode(nc.read_text(), win.project.plan(None).text)


def test_the_canvas_zoom_keeps_its_place_when_only_the_highlight_changes(job19):
    c = job19.canvas
    c.zoom_by(2.0)
    scale = c.scale_factor
    job19.ops.table.selectRow(2)
    assert c.scale_factor == pytest.approx(scale)
    job19.btn_fit.click()
    assert c.scale_factor < scale


def test_reloading_the_tool_cards_picks_up_the_edited_file(qtbot, tmp_path):
    (tmp_path / "herramientas.json").write_text('{"herramientas": {"em4": {"diametro": 4}}}', encoding="utf-8")
    w = MainWindow(folders=(tmp_path, tmp_path), use_settings=False)
    qtbot.addWidget(w)
    assert w.job.tool.count() == 1 and w.project.general.tool == ""
    pick_tool(w, "em4")
    assert w.job.feed.placeholderText() == ""
    (tmp_path / "herramientas.json").write_text(
        '{"herramientas": {"em4": {"diametro": 4, "feed": 450, "entrada": 120}, "v90": {"diametro": 6, "tipo": "v", '
        '"angulo": 90}}}', encoding="utf-8")
    assert w.reload_tools()
    assert w.job.tool.count() == 2 and w.job.tool_value() == "em4"            # sigue con la misma fresa
    assert w.job.feed.placeholderText() == "450 (ficha)" and w.job.plunge.placeholderText() == "120 (ficha)"
    (tmp_path / "herramientas.json").write_text("{esto no es json", encoding="utf-8")
    assert w.reload_tools() is False and w.job.tool.count() == 2             # un archivo roto no borra las fichas
    assert "JSON" in w.messages.texts()[0].upper()


def test_a_job_saved_without_board_detection_reopens_without_it(qtbot, tmp_path, win):
    svg = tmp_path / "d.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="60mm" viewBox="0 0 100 60">'
                   '<rect x="10" y="10" width="40" height="30" stroke="#ff0000" fill="none"/>'
                   '<circle cx="30" cy="25" r="5" stroke="#ff0000" fill="none"/></svg>')
    win.open_svg(svg)
    win.detect_boards.setChecked(False)
    pick_tool(win, "em4", "6")
    job = tmp_path / "d.cncjob"
    assert win.save_job(job)
    other = MainWindow(Project(tools()), [], use_settings=False)
    qtbot.addWidget(other)
    assert other.open_job(job)
    assert other.project.use_boards is False and other.detect_boards.isChecked() is False
    assert other.boards.item(0).text() == "Dibujo completo" and other.project.general.thickness == 6.0


def test_an_unexpected_error_is_logged_next_to_the_program_and_shown(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from cnc.gui.app import LOG_NAME, report_unexpected
    shown = []
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda parent, title, text: shown.append((title, text))))
    try:
        raise RuntimeError("fallo de prueba")
    except RuntimeError as e:
        text = report_unexpected(type(e), e, e.__traceback__, log_dir=tmp_path)
    log = (tmp_path / LOG_NAME).read_text(encoding="utf-8")
    assert "fallo de prueba" in text and "RuntimeError" in log and LOG_NAME in shown[0][1]
    report_unexpected(RuntimeError, RuntimeError("otro"), None, log_dir=tmp_path)
    assert log != (tmp_path / LOG_NAME).read_text(encoding="utf-8") and (tmp_path / LOG_NAME).read_text().count("---") == 2
    report_unexpected(RuntimeError, RuntimeError("x"), None, log_dir=tmp_path / "no" / "existe")   # sin carpeta: no revienta
