"""Una sola ventana en la aplicacion: lo que hace la ventana al recibir un archivo de otro arranque, y el arranque
(`main`) de verdad como proceso (PySide6 sin pantalla)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import subprocess
import sys
import types
import uuid
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from cnc.gui import main_window as mw
from cnc.gui import single_instance as si
from cnc.gui.main_window import MainWindow
from cnc.model import GRBL_MAQUINA_REAL, Tool
from cnc.project import Project

DATA = Path(__file__).parent / "data"
BOARDS19 = DATA / "tableros_19mm.svg"
BIG = DATA / "dibujo_grande.svg"
ROOT = Path(__file__).resolve().parent.parent
TOOLS = {"em4": Tool(diameter=4, name="Fresa 4", feed_xy=300, feed_z=200, stepdown=6)}


@pytest.fixture(autouse=True)
def no_boxes(monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)


def make(qtbot, drawing=BOARDS19):
    w = MainWindow(Project(dict(TOOLS), {"real": GRBL_MAQUINA_REAL}), [], use_settings=False)
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    if drawing is not None:
        assert w.open_svg(drawing, apply_default_recipe=False)
        w.wait_for_plan()
    return w


@pytest.fixture
def win(qtbot):
    return make(qtbot)


@pytest.fixture
def spy(win, monkeypatch):
    """Cuenta lo que hace la ventana: ponerse delante, preguntar por cambios sin guardar y abrir."""
    calls = {"front": 0, "asked": 0, "opened": []}
    front = win.bring_to_front
    monkeypatch.setattr(win, "bring_to_front", lambda: (calls.__setitem__("front", calls["front"] + 1), front())[1])
    monkeypatch.setattr(win, "_confirm_discard", lambda: calls.__setitem__("asked", calls["asked"] + 1) or True)
    return calls


def name_of(w):
    return w.project.drawing.path.name


@pytest.fixture
def job_file(qtbot, tmp_path):
    other = make(qtbot)
    other.job.tool.setCurrentIndex(other.job.tool.findData("em4"))
    other.job.thickness.setText("19")
    other.job.thickness.valueChanged.emit()
    other.wait_for_plan()
    path = tmp_path / "uno.cncjob"
    assert other.save_job(path) and path.exists()
    return path


# --- la ventana recibe un archivo --------------------------------------------------------------------------------------
def test_a_received_drawing_is_opened_and_the_window_comes_forward(win, spy):
    win.receive_external([str(BIG)])
    assert name_of(win) == "dibujo_grande.svg" and spy["front"] == 1 and spy["asked"] == 1


def test_a_received_job_is_opened(qtbot, job_file):
    other = make(qtbot, drawing=BIG)
    other.receive_external([str(job_file)])
    assert name_of(other) == "tableros_19mm.svg" and other.project.general.thickness == 19.0


def test_with_no_file_the_window_only_comes_forward(win, spy):
    name = name_of(win)
    win.receive_external([])
    assert spy == {"front": 1, "asked": 0, "opened": []} and name_of(win) == name


def test_a_file_that_is_not_a_drawing_or_a_job_is_not_opened_and_says_so(win, spy, tmp_path):
    name = name_of(win)
    win.receive_external([str(tmp_path / "notas.txt")])
    assert spy["front"] == 1 and spy["asked"] == 0 and name_of(win) == name
    assert "notas.txt" in win.statusBar().currentMessage() and ".svg" in win.statusBar().currentMessage()


def test_the_first_file_that_can_be_opened_is_the_one_opened(win, spy, tmp_path):
    win.receive_external([str(tmp_path / "notas.txt"), str(BIG), str(BOARDS19)])
    assert name_of(win) == "dibujo_grande.svg"


def test_coming_forward_means_showing_raising_and_activating(win, monkeypatch):
    done = []
    monkeypatch.setattr(win, "show", lambda: done.append("show"))
    monkeypatch.setattr(win, "raise_", lambda: done.append("raise"))
    monkeypatch.setattr(win, "activateWindow", lambda: done.append("activate"))
    win.receive_external([])
    assert done == ["show", "raise", "activate"]


def test_a_minimized_window_is_restored(win):
    win.showMinimized()
    assert win.isMinimized()
    win.receive_external([])
    assert not win.isMinimized() and win.isVisible()


def test_with_changes_not_saved_it_asks_first_like_opening_from_the_menu(win, monkeypatch, tmp_path):
    win.settings = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)                                       # en las pruebas no hay nadie para contestar: aqui si
    win._dirty = True
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[1]) or QMessageBox.No)
    name = name_of(win)
    win.receive_external([str(BIG)])
    assert asked == ["Cambios sin guardar"] and name_of(win) == name and win._dirty


def test_and_if_they_are_discarded_the_new_file_replaces_the_work(win, monkeypatch, tmp_path):
    win.settings = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    win._dirty = True
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    win.receive_external([str(BIG)])
    assert name_of(win) == "dibujo_grande.svg" and not win._dirty


# --- con un cuadro abierto ---------------------------------------------------------------------------------------------
@pytest.fixture
def modal(monkeypatch):
    """Un cuadro de dialogo abierto hasta que la prueba lo 'cierre'."""
    state = {"open": True, "box": QWidget()}
    monkeypatch.setattr(QApplication, "activeModalWidget", staticmethod(lambda: state["box"] if state["open"] else None))
    monkeypatch.setattr(mw, "PENDING_EXTERNAL_MS", 20)
    return state


def test_with_a_dialog_open_the_file_waits_and_opens_when_it_closes(qtbot, win, spy, modal):
    name = name_of(win)
    win.receive_external([str(BIG)])
    assert spy["front"] == 1 and name_of(win) == name and spy["asked"] == 0
    qtbot.wait(150)
    assert name_of(win) == name                                           # sigue esperando
    modal["open"] = False
    qtbot.waitUntil(lambda: name_of(win) == "dibujo_grande.svg", timeout=3000)
    assert spy["asked"] == 1 and win._pending_external is None
    qtbot.wait(150)
    assert spy["asked"] == 1                                              # y no lo vuelve a abrir


def test_while_waiting_the_last_request_wins(qtbot, win, spy, modal):
    win.receive_external([str(BIG)])
    win.receive_external([str(BOARDS19)])
    modal["open"] = False
    qtbot.waitUntil(lambda: spy["asked"] >= 1, timeout=3000)
    qtbot.wait(150)
    assert name_of(win) == "tableros_19mm.svg" and spy["asked"] == 1


def test_a_request_with_no_file_while_waiting_does_not_cancel_the_wait(qtbot, win, spy, modal):
    win.receive_external([str(BIG)])
    win.receive_external([])
    modal["open"] = False
    qtbot.waitUntil(lambda: name_of(win) == "dibujo_grande.svg", timeout=3000)


def test_if_nobody_closes_the_dialog_for_a_long_time_the_file_is_dropped(qtbot, win, spy, modal, monkeypatch):
    monkeypatch.setattr(mw, "PENDING_EXTERNAL_TRIES", 5)
    name = name_of(win)
    win.receive_external([str(BIG)])
    qtbot.wait(400)
    modal["open"] = False
    qtbot.wait(200)
    assert name_of(win) == name and spy["asked"] == 0 and win._pending_external is None


def test_a_new_wait_starts_counting_from_zero(qtbot, win, spy, modal, monkeypatch):
    monkeypatch.setattr(mw, "PENDING_EXTERNAL_TRIES", 8)
    win.receive_external([str(BOARDS19)])
    qtbot.waitUntil(lambda: win._pending_external is None, timeout=3000)       # se rindio: el cuadro no se cerro
    win.receive_external([str(BIG)])                                      # otro aviso, con el cuadro aun abierto
    qtbot.wait(60)
    assert win._pending_external is not None and win._pending_tries <= 4
    modal["open"] = False
    qtbot.waitUntil(lambda: name_of(win) == "dibujo_grande.svg", timeout=3000)


# --- el arranque de verdad ---------------------------------------------------------------------------------------------
DRIVER_ASK = """
import os, sys
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from cnc.gui import app as A
sys.exit(A.main(json_args))
"""
DRIVER_WINDOW = """
import json, os, sys
from cnc.gui import app as A, main_window as M
original = M.MainWindow.receive_external
def spy(self, paths):
    original(self, paths)
    shown = self.project.drawing.path.name if self.project.drawing else None
    print('GOT', json.dumps(paths), shown, flush=True)
    if paths:
        A.QApplication.instance().quit()
M.MainWindow.receive_external = spy
sys.exit(A.main(json.loads(sys.argv[1])))
"""


SPAWNED = []


@pytest.fixture(autouse=True)
def kill_leftovers():
    """Un arranque que no hace lo debido se quedaria con su ventana abierta para siempre: se mata al acabar la prueba."""
    yield
    while SPAWNED:
        proc = SPAWNED.pop()
        if proc.poll() is None:
            proc.kill()
        proc.communicate()


def spawn(driver, instance, args, tmp_path):
    env = dict(os.environ, PYTHONPATH=str(ROOT), QT_QPA_PLATFORM="offscreen", CNC_APP_INSTANCE=instance,
               HOME=str(tmp_path), USERPROFILE=str(tmp_path), APPDATA=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path))
    proc = subprocess.Popen([sys.executable, "-c", driver, json.dumps(args)], cwd=str(tmp_path), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    SPAWNED.append(proc)
    return proc


@pytest.fixture
def instance():
    return f"cnc_app-test-{uuid.uuid4().hex[:12]}"


def finish(qtbot, proc, timeout=60000):
    qtbot.waitUntil(lambda: proc.poll() is not None, timeout=timeout)
    out, err = proc.communicate()
    SPAWNED.remove(proc)
    return proc.returncode, out, err


def test_starting_the_app_with_a_window_open_passes_the_file_and_exits_without_a_window(qtbot, instance, tmp_path):
    server = si.InstanceServer(instance)
    assert server.start()
    got = []
    server.requested.connect(lambda paths: got.append(list(paths)))
    driver = DRIVER_ASK.replace("json_args", "json.loads(sys.argv[1])").replace("import os, sys", "import json, os, sys")
    code, out, err = finish(qtbot, spawn(driver, instance, ["dibujo.svg"], tmp_path))
    assert code == 0, err
    qtbot.waitUntil(lambda: bool(got), timeout=5000)
    assert got == [[str(tmp_path / "dibujo.svg")]]
    server.stop()


def test_the_checks_of_the_installed_program_never_pass_the_file_to_a_window(qtbot, instance, tmp_path):
    server = si.InstanceServer(instance)
    assert server.start()
    got = []
    server.requested.connect(lambda paths: got.append(list(paths)))
    driver = DRIVER_ASK.replace("json_args", "json.loads(sys.argv[1])").replace("import os, sys", "import json, os, sys")
    report = tmp_path / "resultado.txt"
    code, out, err = finish(qtbot, spawn(driver, instance, ["--autotest", str(BOARDS19), str(report)], tmp_path))
    assert code == 0, (out, err)
    assert "RESULTADO: CORRECTO" in report.read_text(encoding="utf-8")
    qtbot.wait(300)
    assert got == []
    server.stop()


def test_two_starts_one_window_the_second_hands_the_file_over_to_the_first(qtbot, instance, tmp_path):
    first = spawn(DRIVER_WINDOW, instance, [], tmp_path)
    qtbot.waitUntil(lambda: si.send_to_running([], instance, connect_ms=200, ack_ms=2000) or first.poll() is not None,
                    timeout=60000)                                        # hasta que la primera ventana escucha
    assert first.poll() is None
    driver = DRIVER_ASK.replace("json_args", "json.loads(sys.argv[1])").replace("import os, sys", "import json, os, sys")
    second = spawn(driver, instance, [str(BIG)], tmp_path)
    code, out, err = finish(qtbot, second, timeout=30000)
    assert code == 0 and "Traceback" not in err, err                     # el segundo se fue sin abrir ventana
    code, out, err = finish(qtbot, first, timeout=30000)
    assert code == 0, err
    got = [line for line in out.splitlines() if line.startswith("GOT")]
    assert got[0] == "GOT [] None"                                       # el aviso de prueba: solo ponerse delante
    assert got[-1] == f"GOT {json.dumps([str(BIG)])} dibujo_grande.svg"    # y el archivo del segundo, abierto en la primera


# --- main: el orden de las decisiones ----------------------------------------------------------------------------------
class FakeServer:
    """Una ventana que ya hay (o no): lo que hace `main` antes de abrir la suya."""
    created = []
    answer = True

    def __init__(self, name=None):
        FakeServer.created.append(name)
        self.requested = types.SimpleNamespace(connect=lambda slot: None)

    def start(self):
        return FakeServer.answer


@pytest.fixture
def entry(monkeypatch):
    from cnc.gui import app as A
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)               # main() lo cambia
    FakeServer.created, FakeServer.answer = [], True
    calls = []
    monkeypatch.setattr(A, "InstanceServer", FakeServer)
    monkeypatch.setattr(A, "instance_name", lambda: "canal-de-prueba")

    class NoWindow:
        def __init__(self, *a, **k):
            raise AssertionError("no debia abrirse otra ventana")
    monkeypatch.setattr(A, "MainWindow", NoWindow)
    monkeypatch.setattr(A.theme, "apply_theme", lambda app: None)         # el tema es global: no debe quedar puesto en las pruebas siguientes
    return A, calls


def test_if_there_is_a_window_the_new_start_does_not_even_listen(entry, monkeypatch):
    A, calls = entry
    monkeypatch.setattr(A, "forward_to_running", lambda argv, name: calls.append((list(argv), name)) or True)
    assert A.main(["dibujo.svg"]) == 0
    assert calls == [(["dibujo.svg"], "canal-de-prueba")] and FakeServer.created == []


def test_if_another_start_got_the_channel_first_the_file_is_passed_to_it(entry, monkeypatch):
    A, calls = entry
    answers = iter([False, True])
    monkeypatch.setattr(A, "forward_to_running", lambda argv, name: calls.append(list(argv)) or next(answers))
    FakeServer.answer = False
    assert A.main(["dibujo.svg"]) == 0
    assert calls == [["dibujo.svg"], ["dibujo.svg"]] and FakeServer.created == ["canal-de-prueba"]


def test_if_nobody_answers_the_app_opens_its_own_window(entry, monkeypatch):
    A, calls = entry
    monkeypatch.setattr(A, "forward_to_running", lambda argv, name: False)
    with pytest.raises(AssertionError, match="no debia abrirse"):                # llega a crear su ventana
        A.main([])
    assert FakeServer.created == ["canal-de-prueba"]
