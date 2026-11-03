"""Una sola ventana: el canal local por el que un arranque nuevo le pasa el archivo a la ventana abierta (sin pantalla).

El arranque nuevo es otro PROCESO de verdad (como en el uso real): en una hebra del mismo proceso, sus esperas bloqueantes
no dejan al servidor contestar."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import socket
import subprocess
import sys
import types
import uuid
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtNetwork import QLocalServer, QLocalSocket

from cnc.gui import single_instance as si

ROOT = Path(__file__).resolve().parent.parent
CLIENT = """
import json, os, sys
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QCoreApplication
app = QCoreApplication([])
from cnc.gui import single_instance as si
a = json.loads(sys.argv[1])
kw = {k: a[k] for k in ('connect_ms', 'ack_ms') if k in a}
if a.get('fg'):
    kw['foreground'] = lambda pid: print('FG', pid, flush=True)
if a.get('forward'):
    ok = si.forward_to_running(a['paths'], a['name'], **kw)
else:
    ok = si.send_to_running(a['paths'], a['name'], **kw)
print('RESULT', ok, flush=True)
"""


@pytest.fixture
def name():
    return f"cnc_app-test-{uuid.uuid4().hex[:12]}"


@pytest.fixture
def server(qtbot, name):
    s = si.InstanceServer(name)
    assert s.start()
    s.got = []
    s.requested.connect(lambda paths: s.got.append(list(paths)))
    yield s
    s.stop()


def launch(name, paths=(), cwd=None, **options):
    """Un arranque nuevo: otro proceso que intenta pasar `paths` a la ventana abierta."""
    args = json.dumps({"name": name, "paths": [str(p) for p in paths], **options})
    env = dict(os.environ, PYTHONPATH=str(ROOT), QT_QPA_PLATFORM="offscreen")
    return subprocess.Popen([sys.executable, "-c", CLIENT, args], cwd=str(cwd or ROOT), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def result(qtbot, proc, timeout=30000):
    """(lo que devolvio send_to_running, lo que imprimio). Mientras espera, la ventana sigue atendiendo."""
    qtbot.waitUntil(lambda: proc.poll() is not None, timeout=timeout)
    out, err = proc.communicate()
    assert proc.returncode == 0, err
    lines = out.splitlines()
    return next(l for l in lines if l.startswith("RESULT")).split()[1] == "True", lines


def ask(qtbot, name, paths=(), **options):
    return result(qtbot, launch(name, paths, **options))[0]


# --- un cliente a mano, sin hebras ----------------------------------------------------------------------------------
def open_raw(qtbot, name):
    sock = QLocalSocket()
    sock.connectToServer(name)
    qtbot.waitUntil(lambda: sock.state() == QLocalSocket.ConnectedState, timeout=3000)
    return sock


def read_reply(qtbot, sock, timeout=5000):
    qtbot.waitUntil(lambda: sock.canReadLine(), timeout=timeout)
    return bytes(sock.readLine()).decode("utf-8").strip()


# --- sin ventana abierta ----------------------------------------------------------------------------------------------
def test_with_no_window_open_there_is_nobody_to_pass_the_file_to(qtbot, name):
    assert si.send_to_running(["x.cncjob"], name, connect_ms=200) is False
    assert si.forward_to_running(["x.cncjob"], name, connect_ms=200) is False
    assert si.forward_to_running([], name, connect_ms=200) is False


# --- con ventana abierta ----------------------------------------------------------------------------------------------
def test_the_open_window_receives_the_file_and_the_new_start_has_nothing_more_to_do(qtbot, server, name, tmp_path):
    job = tmp_path / "mi trabajo.cncjob"
    assert ask(qtbot, name, [job]) is True
    qtbot.waitUntil(lambda: bool(server.got), timeout=5000)
    assert server.got == [[str(job)]]


def test_the_command_line_helper_passes_the_first_argument_only(qtbot, server, name, tmp_path):
    assert ask(qtbot, name, [tmp_path / "a.svg", tmp_path / "b.svg"], forward=True) is True
    qtbot.waitUntil(lambda: bool(server.got), timeout=5000)
    assert server.got == [[str(tmp_path / "a.svg")]]


def test_a_relative_path_travels_as_an_absolute_one_because_the_open_window_is_in_another_folder(qtbot, server, name,
                                                                                               tmp_path):
    assert ask(qtbot, name, ["dibujo.svg"], cwd=tmp_path) is True
    qtbot.waitUntil(lambda: bool(server.got), timeout=5000)
    assert server.got == [[str(tmp_path / "dibujo.svg")]] and os.path.isabs(server.got[0][0])


def test_starting_again_with_no_file_only_asks_the_window_to_come_forward(qtbot, server, name):
    assert ask(qtbot, name, []) is True
    qtbot.waitUntil(lambda: bool(server.got), timeout=5000)
    assert server.got == [[]]


def test_unusual_names_arrive_untouched(qtbot, server, name):
    odd = "/Users/Román/Mis trabajos/tablero ñ 1 (copia) [v2].cncjob"
    assert ask(qtbot, name, [odd]) is True
    qtbot.waitUntil(lambda: bool(server.got), timeout=5000)
    assert server.got == [[odd]]


def test_the_window_says_which_process_it_is_so_the_new_start_can_give_it_the_foreground(qtbot, server, name):
    ok, lines = result(qtbot, launch(name, ["a.svg"], fg=True))
    assert ok and f"FG {os.getpid()}" in lines


def test_the_window_acts_only_when_the_new_start_has_gone_so_the_foreground_turn_is_already_given(qtbot, server, name):
    sock = open_raw(qtbot, name)
    sock.write(json.dumps({"paths": ["/tmp/a.svg"]}).encode() + b"\n")
    assert read_reply(qtbot, sock).startswith("ok ")
    qtbot.wait(300)
    assert server.got == []                                              # sigue conectado: todavia no
    sock.disconnectFromServer()
    qtbot.waitUntil(lambda: bool(server.got), timeout=5000)
    assert server.got == [["/tmp/a.svg"]]


def test_several_starts_one_after_another_each_get_through_once(qtbot, server, name):
    for i in range(3):
        assert ask(qtbot, name, [f"/tmp/{i}.svg"]) is True
    qtbot.waitUntil(lambda: len(server.got) == 3, timeout=5000)
    assert server.got == [[f"/tmp/{i}.svg"] for i in range(3)]


def test_a_burst_of_starts_at_the_same_moment_all_get_through(qtbot, server, name):
    procs = [launch(name, [f"/tmp/{i}.svg"]) for i in range(5)]
    assert all(result(qtbot, p)[0] for p in procs)
    qtbot.waitUntil(lambda: len(server.got) == 5, timeout=5000)
    assert sorted(g[0] for g in server.got) == [f"/tmp/{i}.svg" for i in range(5)]


def test_no_more_than_the_limit_of_files_goes_through(qtbot, server, name):
    assert ask(qtbot, name, [f"/tmp/{i}.svg" for i in range(si.MAX_PATHS + 5)]) is True
    qtbot.waitUntil(lambda: bool(server.got), timeout=5000)
    assert len(server.got[0]) == si.MAX_PATHS


def test_what_the_new_start_sends_is_one_line_of_json_with_at_most_the_allowed_files(qtbot, name):
    """El formato del aviso (lo que lee la ventana abierta) y que el cliente no manda mas archivos de los admitidos."""
    seen = []
    fake = QLocalServer()
    assert fake.listen(name)

    def answer():
        sock = fake.nextPendingConnection()
        buffer = bytearray()

        def read():
            buffer.extend(bytes(sock.readAll()))
            if b"\n" in buffer:
                seen.append(bytes(buffer))
                sock.write(b"ok 4242\n")
                sock.flush()
        sock.readyRead.connect(read)
        if sock.bytesAvailable():
            read()
    fake.newConnection.connect(answer)
    many = [f"/tmp/Román {i}.svg" for i in range(si.MAX_PATHS + 4)]
    ok, lines = result(qtbot, launch(name, many, fg=True))
    assert ok and "FG 4242" in lines
    raw = seen[0]
    assert raw.endswith(b"\n") and raw.count(b"\n") == 1
    data = json.loads(raw.decode("utf-8"))
    assert list(data) == ["paths"] and data["paths"] == [os.path.abspath(p) for p in many[:si.MAX_PATHS]]
    assert "Román".encode("utf-8") in raw                                  # sin escapar: viaja en UTF-8
    fake.close()


def test_what_arrives_before_the_window_accepts_the_connection_is_not_lost(qtbot, server, name):
    sock = QLocalSocket()
    sock.connectToServer(name)
    assert sock.waitForConnected(2000)                                   # la ventana aun no ha atendido la conexion
    sock.write(json.dumps({"paths": ["/tmp/a.svg"]}).encode() + b"\n")
    assert sock.waitForBytesWritten(2000)
    assert read_reply(qtbot, sock).startswith("ok ")
    sock.disconnectFromServer()
    qtbot.waitUntil(lambda: bool(server.got), timeout=5000)
    assert server.got == [["/tmp/a.svg"]]


def test_a_message_that_arrives_in_pieces_is_read_when_complete(qtbot, server, name):
    sock = open_raw(qtbot, name)
    whole = json.dumps({"paths": ["/tmp/a.svg"]}).encode() + b"\n"
    sock.write(whole[:8])
    sock.flush()
    qtbot.wait(150)
    assert not sock.canReadLine()                                        # incompleto: la ventana no contesta aun
    sock.write(whole[8:])
    assert read_reply(qtbot, sock).startswith("ok ")
    sock.disconnectFromServer()
    qtbot.waitUntil(lambda: bool(server.got), timeout=5000)
    assert server.got == [["/tmp/a.svg"]]


def test_a_second_message_on_the_same_connection_is_ignored(qtbot, server, name):
    sock = open_raw(qtbot, name)
    sock.write(json.dumps({"paths": ["/tmp/a.svg"]}).encode() + b"\n")
    assert read_reply(qtbot, sock).startswith("ok ")
    sock.write(json.dumps({"paths": ["/tmp/b.svg"]}).encode() + b"\n")
    sock.flush()
    qtbot.wait(200)
    assert not sock.canReadLine()                                        # no hay una segunda respuesta
    sock.disconnectFromServer()
    qtbot.waitUntil(lambda: bool(server.got), timeout=5000)
    qtbot.wait(100)
    assert server.got == [["/tmp/a.svg"]]


def test_the_channel_is_only_open_to_the_user_who_started_it(server):
    assert server._server.socketOptions() & QLocalServer.UserAccessOption


def test_on_windows_the_new_start_hands_the_foreground_turn_to_the_open_window(monkeypatch):
    turns = []
    fake = types.SimpleNamespace(windll=types.SimpleNamespace(user32=types.SimpleNamespace(
        AllowSetForegroundWindow=lambda pid: turns.append(pid))))
    monkeypatch.setitem(sys.modules, "ctypes", fake)
    monkeypatch.setattr(sys, "platform", "win32")
    si.allow_foreground(4321)
    assert turns == [4321]


def test_elsewhere_there_is_no_foreground_turn_to_hand_over(monkeypatch):
    turns = []
    fake = types.SimpleNamespace(windll=types.SimpleNamespace(user32=types.SimpleNamespace(
        AllowSetForegroundWindow=lambda pid: turns.append(pid))))
    monkeypatch.setitem(sys.modules, "ctypes", fake)
    monkeypatch.setattr(sys, "platform", "linux")
    si.allow_foreground(4321)
    assert turns == []


def test_if_windows_refuses_the_foreground_turn_nothing_breaks(monkeypatch):
    def refuse(pid):
        raise OSError("no")
    fake = types.SimpleNamespace(windll=types.SimpleNamespace(user32=types.SimpleNamespace(AllowSetForegroundWindow=refuse)))
    monkeypatch.setitem(sys.modules, "ctypes", fake)
    monkeypatch.setattr(sys, "platform", "win32")
    si.allow_foreground(4321)


# --- avisos raros -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("payload", [b"esto no es json\n", b"[1, 2, 3]\n", b'{"paths": "no es una lista"}\n',
                                     b'{"paths": [1, null, "", "  "]}\n', b"\n", b'{"otra": 1}\n'])
def test_a_message_that_makes_no_sense_only_brings_the_window_forward(qtbot, server, name, payload):
    sock = open_raw(qtbot, name)
    sock.write(payload)
    assert read_reply(qtbot, sock).startswith("ok ")
    sock.disconnectFromServer()
    qtbot.waitUntil(lambda: bool(server.got), timeout=5000)
    assert server.got == [[]]


def test_a_message_that_is_far_too_long_is_not_obeyed(qtbot, server, name):
    sock = open_raw(qtbot, name)
    sock.write(b'{"paths": ["' + b"x" * (si.MAX_MESSAGE + 100) + b'.svg"]}\n')
    assert read_reply(qtbot, sock).startswith("ok ")
    sock.disconnectFromServer()
    qtbot.waitUntil(lambda: bool(server.got), timeout=5000)
    assert server.got == [[]]


def test_a_client_that_connects_and_says_nothing_does_not_hold_the_window_up_for_ever(qtbot, server, name, monkeypatch):
    monkeypatch.setattr(si, "ACK_MS", 100)
    monkeypatch.setattr(si, "LINGER_MS", 100)
    sock = open_raw(qtbot, name)
    qtbot.waitUntil(lambda: bool(server.got), timeout=5000)
    assert server.got == [[]]
    qtbot.wait(400)
    assert server.got == [[]]                                            # y se actua una sola vez
    sock.abort()
    qtbot.wait(100)
    assert server.got == [[]]


def test_a_window_that_never_answers_counts_as_no_window(qtbot, name):
    """Un servidor mudo (una ventana colgada): el arranque nuevo no se queda esperando, abre la suya."""
    mute = QLocalServer()
    assert mute.listen(name)
    assert result(qtbot, launch(name, ["a.svg"], connect_ms=500, ack_ms=300))[0] is False
    mute.close()


def test_a_reply_that_is_not_ok_counts_as_no_window(qtbot, name):
    liar = QLocalServer()
    assert liar.listen(name)

    def answer():
        sock = liar.nextPendingConnection()
        sock.readyRead.connect(lambda: (sock.write(b"no\n"), sock.flush()))
    liar.newConnection.connect(answer)
    assert result(qtbot, launch(name, ["a.svg"], connect_ms=500, ack_ms=2000))[0] is False
    liar.close()


# --- arranque del servidor --------------------------------------------------------------------------------------------
def test_a_second_server_with_the_same_name_does_not_steal_it_from_a_live_window(qtbot, server, name):
    other = si.InstanceServer(name)
    assert other.start() is False
    assert ask(qtbot, name, ["/tmp/a.svg"]) is True                      # la primera sigue siendo la que contesta
    qtbot.waitUntil(lambda: ["/tmp/a.svg"] in server.got, timeout=5000)


@pytest.mark.skipif(sys.platform == "win32", reason="en Windows la tuberia desaparece sola con el proceso")
def test_the_leftover_of_a_window_that_closed_badly_does_not_stop_the_next_one(qtbot, name):
    probe = si.InstanceServer(name)
    assert probe.start()
    path = probe._server.fullServerName()
    probe.stop()
    leftover = socket.socket(socket.AF_UNIX)
    leftover.bind(path)                                                  # un archivo de socket sin nadie detras
    leftover.close()
    assert os.path.exists(path)
    s = si.InstanceServer(name)
    assert s.start() is True
    s.got = []
    s.requested.connect(lambda paths: s.got.append(paths))
    assert ask(qtbot, name, ["/tmp/a.svg"]) is True
    qtbot.waitUntil(lambda: bool(s.got), timeout=5000)
    assert s.got == [["/tmp/a.svg"]]
    s.stop()


class FakeListener:
    """Un QLocalServer que contesta lo que se le dice a cada listen()."""
    def __init__(self, answers):
        self.answers = list(answers)
        self.tried = 0

    def listen(self, name):
        self.tried += 1
        return self.answers.pop(0)


def test_a_leftover_name_is_cleared_and_taken(qtbot, name, monkeypatch):
    cleared = []
    s = si.InstanceServer(name)
    monkeypatch.setattr(si, "QLocalServer", types.SimpleNamespace(removeServer=lambda n: cleared.append(n)))
    s._server = FakeListener([False, True])
    assert s.start() is True and cleared == [name] and s._server.tried == 2


def test_if_the_name_cannot_be_taken_even_after_clearing_it_the_start_says_so(qtbot, name, monkeypatch):
    cleared = []
    s = si.InstanceServer(name)
    monkeypatch.setattr(si, "QLocalServer", types.SimpleNamespace(removeServer=lambda n: cleared.append(n)))
    s._server = FakeListener([False, False])
    assert s.start() is False and cleared == [name]


def test_a_free_name_is_taken_at_once_without_clearing_anything(qtbot, name, monkeypatch):
    cleared = []
    s = si.InstanceServer(name)
    monkeypatch.setattr(si, "QLocalServer", types.SimpleNamespace(removeServer=lambda n: cleared.append(n)))
    s._server = FakeListener([True])
    assert s.start() is True and cleared == [] and s._server.tried == 1


def test_a_message_already_waiting_when_the_connection_is_accepted_is_answered(qtbot, name):
    """Si Qt ya trajo el mensaje antes de que se enganche readyRead (pasa con las tuberias de Windows), no se queda sin leer."""
    class Signal:
        def connect(self, slot):
            pass

    class Sock:
        readyRead = Signal()
        disconnected = Signal()

        def __init__(self):
            self.sent = b""

        def bytesAvailable(self):
            return 30

        def readAll(self):
            return b'{"paths": ["/tmp/a.svg"]}\n'

        def write(self, data):
            self.sent += bytes(data)

        def flush(self):
            pass

        def abort(self):
            pass

        def deleteLater(self):
            pass

    sock = Sock()

    class Listener:
        def __init__(self):
            self.pending = [sock]

        def hasPendingConnections(self):
            return bool(self.pending)

        def nextPendingConnection(self):
            return self.pending.pop()

        def close(self):
            pass

    s = si.InstanceServer(name)
    s._server = Listener()
    s._accept()
    assert sock.sent == f"ok {os.getpid()}\n".encode("ascii")
    assert s._clients[0]["paths"] == ["/tmp/a.svg"]
    s.stop()                                                            # y el temporizador pendiente ya no hace nada


def test_stopping_the_server_leaves_no_pending_work_for_the_connections_that_were_open(qtbot, name, monkeypatch):
    monkeypatch.setattr(si, "ACK_MS", 50)
    monkeypatch.setattr(si, "LINGER_MS", 50)
    s = si.InstanceServer(name)
    assert s.start()
    s.got = []
    s.requested.connect(lambda paths: s.got.append(paths))
    sock = open_raw(qtbot, name)                                         # conectado y callado
    qtbot.waitUntil(lambda: len(s._clients) == 1, timeout=3000)
    s.stop()
    qtbot.wait(300)                                                      # el temporizador de ese cliente vence ya parado
    assert s.got == []
    sock.abort()


def test_stopping_the_server_frees_the_name(qtbot, name):
    s = si.InstanceServer(name)
    assert s.start()
    s.stop()
    assert si.send_to_running(["a.svg"], name, connect_ms=200) is False


# --- el nombre del canal ----------------------------------------------------------------------------------------------
def test_each_user_of_the_pc_has_their_own_channel(monkeypatch):
    monkeypatch.delenv(si.ENV_NAME, raising=False)
    assert si.instance_name("santo") == "cnc_app-santo"
    assert si.instance_name("santo") != si.instance_name("otro")
    assert si.instance_name("Román Pérez/..\\x") == "cnc_app-Rom_n_P_rez_.._x"
    assert si.instance_name("") == "cnc_app-usuario"


def test_the_channel_name_can_be_forced_for_tests(monkeypatch):
    monkeypatch.setenv(si.ENV_NAME, "  prueba-123  ")
    assert si.instance_name("santo") == "prueba-123"


@pytest.mark.parametrize("line, expected", [
    ('{"paths": ["a.svg", "b.cncjob"]}', ["a.svg", "b.cncjob"]), ('{"paths": []}', []), ("{}", []), ("", []), ("[]", []),
    ('{"paths": ["a.svg", 3, null, " "]}', ["a.svg"]), ("null", []), ('{"paths": {"a": 1}}', [])])
def test_how_a_message_is_read(line, expected):
    assert si.InstanceServer.parse(line) == expected


def test_a_message_with_too_many_files_keeps_the_first_ones():
    many = [f"/tmp/{i}.svg" for i in range(si.MAX_PATHS + 5)]
    assert si.InstanceServer.parse(json.dumps({"paths": many})) == many[:si.MAX_PATHS]
