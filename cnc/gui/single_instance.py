"""Una sola ventana de cnc_app por usuario.

Dos ventanas abiertas a la vez pueden pisarse lo que guardan (las fichas de fresas y de maquinas se reescriben enteras, el
mismo trabajo se puede guardar desde las dos, y lo que el PC recuerda lo escribe la que cierra la ultima). Por eso, al
arrancar otra vez (por ejemplo con un doble clic en un `.cncjob` con la ventana abierta), el arranque nuevo NO abre otra
ventana: le pasa el archivo a la que ya esta y se va.

El aviso va por un canal local (tuberia con nombre en Windows, socket local en Linux) que solo ve el mismo usuario:
- el cliente (arranque nuevo) manda una linea JSON `{"paths": [...]}` y espera la respuesta `ok PID`;
- el servidor (la ventana abierta) contesta enseguida, y cuando el cliente se desconecta emite `requested(paths)`;
- si nadie contesta a tiempo (la ventana esta colgada o no hay ninguna), el cliente dice que no la hay y arranca normal.
"""
from __future__ import annotations

import getpass
import json
import os
import re
import sys
from pathlib import Path
from typing import Callable, Optional, Sequence

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

ENV_NAME = "CNC_APP_INSTANCE"            # para las pruebas: otro nombre de canal, que no choque con una ventana de verdad
MAX_MESSAGE = 64 * 1024                  # un aviso mas largo se descarta
MAX_PATHS = 8
CONNECT_MS = 600                         # esperar a conectar con una ventana ya abierta
ACK_MS = 5000                            # esperar su respuesta (si esta ocupada calculando, tarda)
LINGER_MS = 3000                         # si el cliente no se desconecta, se actua igualmente a los 3 s


def instance_name(user: Optional[str] = None) -> str:
    """El nombre del canal: uno por usuario del PC (o el de la variable de entorno, para las pruebas)."""
    forced = os.environ.get(ENV_NAME, "").strip()
    if forced:
        return forced
    try:
        who = user if user is not None else getpass.getuser()
    except Exception:                    # noqa: BLE001 - sin usuario conocido, un nombre comun sirve igual
        who = ""
    return "cnc_app-" + (re.sub(r"[^A-Za-z0-9_.-]", "_", who) or "usuario")


def allow_foreground(pid: int) -> None:
    """En Windows, un programa solo puede ponerse delante si quien lo lanzo tiene turno: el arranque nuevo (que acaba de
    recibir el doble clic) se lo cede a la ventana abierta."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(int(pid))
    except Exception:                    # noqa: BLE001 - si no se puede, la ventana solo parpadea en la barra de tareas
        pass


def _read_line(sock: QLocalSocket, wait_ms: int) -> Optional[str]:
    """Una linea completa del socket, o None si no llega a tiempo."""
    buffer = bytearray()
    while b"\n" not in buffer:
        if sock.bytesAvailable() == 0 and not sock.waitForReadyRead(wait_ms):
            return None
        buffer += bytes(sock.readAll())
        if len(buffer) > MAX_MESSAGE:
            return None
    return buffer.split(b"\n", 1)[0].decode("utf-8", "replace")


def send_to_running(paths: Sequence = (), name: Optional[str] = None, connect_ms: int = CONNECT_MS, ack_ms: int = ACK_MS,
                    foreground: Optional[Callable[[int], None]] = allow_foreground) -> bool:
    """Avisa a la ventana ya abierta (si la hay) de que abra estos archivos (o solo de que se ponga delante, sin archivos).
    Devuelve True si la ventana lo ha recibido: el arranque nuevo no tiene que hacer nada mas."""
    sock = QLocalSocket()
    sock.connectToServer(name or instance_name())
    if not sock.waitForConnected(connect_ms):
        sock.abort()
        return False
    try:
        absolute = [os.path.abspath(str(p)) for p in paths][:MAX_PATHS]      # la ventana abierta tiene otra carpeta actual
        sock.write(json.dumps({"paths": absolute}, ensure_ascii=False).encode("utf-8") + b"\n")
        if not sock.waitForBytesWritten(ack_ms):
            return False
        reply = _read_line(sock, ack_ms)
        if reply is None or not reply.startswith("ok"):
            return False
        if foreground is not None:
            try:
                foreground(int(reply.split()[1]))
            except (IndexError, ValueError):
                pass
        sock.disconnectFromServer()
        if sock.state() != QLocalSocket.UnconnectedState:
            sock.waitForDisconnected(1000)
        return True
    finally:
        sock.abort()


class InstanceServer(QObject):
    """La ventana abierta escucha avisos de otros arranques. `requested` lleva la lista de archivos (vacia: solo ponerse
    delante)."""

    requested = Signal(list)

    def __init__(self, name: Optional[str] = None, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.name = name or instance_name()
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.UserAccessOption)        # solo el mismo usuario
        self._server.newConnection.connect(self._accept)
        self._clients: list = []

    def start(self) -> bool:
        """Empieza a escuchar. False si el nombre ya esta cogido por una ventana viva (entonces hay que avisarla a ella)."""
        if self._alive():                                                   # en Linux un segundo servidor le quitaria el nombre
            return False
        if self._server.listen(self.name):
            return True
        QLocalServer.removeServer(self.name)                                 # un resto de una ventana que se cerro mal
        return self._server.listen(self.name)                               # si sigue fallando, otra ventana lo ocupa ya

    def _alive(self) -> bool:
        probe = QLocalSocket()
        probe.connectToServer(self.name)
        found = probe.waitForConnected(CONNECT_MS)                          # contesta alguien: hay una ventana viva
        probe.abort()
        return found

    def stop(self) -> None:
        self._server.close()
        for state in list(self._clients):
            state["done"] = True                                            # el temporizador pendiente ya no hace nada
            state["socket"].abort()
        self._clients.clear()

    def _accept(self) -> None:
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            state = {"socket": sock, "buffer": bytearray(), "paths": None, "done": False}
            self._clients.append(state)
            sock.readyRead.connect(lambda s=state: self._read(s))
            sock.disconnected.connect(lambda s=state: self._finish(s))
            QTimer.singleShot(ACK_MS + LINGER_MS, lambda s=state: self._finish(s))      # un cliente que se queda mudo
            if sock.bytesAvailable():                                       # lo que llego antes de enganchar readyRead
                self._read(state)

    def _read(self, state: dict) -> None:
        if state["paths"] is not None:
            return
        state["buffer"] += bytes(state["socket"].readAll())
        if len(state["buffer"]) > MAX_MESSAGE:
            state["paths"] = []                                             # demasiado largo: no se hace caso
            self._reply(state)
            return
        if b"\n" not in state["buffer"]:
            return
        line = bytes(state["buffer"]).split(b"\n", 1)[0].decode("utf-8", "replace")
        state["paths"] = self.parse(line)
        self._reply(state)

    @staticmethod
    def parse(line: str) -> list:
        """La lista de archivos del aviso; algo que no se entiende es una lista vacia (la ventana solo se pone delante)."""
        try:
            data = json.loads(line)
            raw = data.get("paths", []) if isinstance(data, dict) else []
        except (ValueError, AttributeError):
            return []
        return [p for p in raw if isinstance(p, str) and p.strip()][:MAX_PATHS] if isinstance(raw, list) else []

    def _reply(self, state: dict) -> None:
        sock = state["socket"]
        sock.write(f"ok {os.getpid()}\n".encode("ascii"))
        sock.flush()

    def _finish(self, state: dict) -> None:
        if state["done"]:
            return
        state["done"] = True
        paths = state["paths"] if state["paths"] is not None else []
        if state in self._clients:
            self._clients.remove(state)
        state["socket"].abort()
        state["socket"].deleteLater()
        self.requested.emit(paths)


def forward_to_running(argv: Sequence, name: Optional[str] = None, **kwargs) -> bool:
    """Para `main`: True si ya hay una ventana y se le ha pasado el archivo de la linea de comandos (si lo hay)."""
    paths = [Path(argv[0])] if argv else []
    return send_to_running(paths, name, **kwargs)
