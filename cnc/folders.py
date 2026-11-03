"""Las dos carpetas fijas de trabajo: donde se guardan los trabajos (.cncjob) y donde se guardan los G-code (.nc).

Sin Qt. Aqui estan las reglas para decir si una ruta vale y por donde empieza una ventana de abrir o guardar; la
ventana (`cnc/gui/folder_actions.py`) solo las recuerda en el PC y pregunta. La regla es no adivinar: una ruta que no
es completa, que no existe (una unidad desconectada, por ejemplo) o donde no se puede escribir se rechaza con el
motivo, y nunca se cambia por otra en silencio.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path, PureWindowsPath
from typing import Optional
from .i18n import _

JOBS = "trabajos"
GCODE = "gcode"
KINDS = (JOBS, GCODE)
LABELS = {JOBS: _("Trabajos (.cncjob)"), GCODE: _("G-code (.nc)")}
HELP = {JOBS: _("Donde se abren y se guardan los trabajos."),
        GCODE: _("Donde se guardan los G-code generados y los G-code de marcas de la sujeción.")}
FORBIDDEN = '<>"|?*'                    # caracteres que Windows no admite en una ruta


def clean(text) -> str:
    """Lo que se escribe o se pega, sin espacios ni comillas de alrededor (el Explorador da «Copiar como ruta»
    entre comillas)."""
    s = str(text or "").strip()
    while len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    return s


def problem(text, posix: Optional[bool] = None) -> Optional[str]:
    """Por que `text` no vale como carpeta, en una frase que sigue al nombre de la ruta; None si vale o esta vacia
    (vacia = sin configurar). `posix` dice si tambien se aceptan rutas que empiezan por «/» (en Windows no; solo
    cambia en las pruebas)."""
    s = clean(text)
    if not s:
        return None
    posix = os.name != "nt" if posix is None else posix
    rooted = posix and s.startswith("/")
    if any(c in FORBIDDEN or ord(c) < 32 for c in s) or (not rooted and ":" in s[2:]):
        return _("tiene caracteres que Windows no admite en una ruta.")
    if not (rooted or PureWindowsPath(s).is_absolute()):
        return _("no es una ruta completa: tiene que empezar por la unidad, por ejemplo D:\\Trabajos.")
    folder = Path(s)
    if not folder.exists():
        return _("no existe (¿está conectada la unidad?).")
    if not folder.is_dir():
        return _("no es una carpeta.")
    try:
        with tempfile.TemporaryFile(dir=folder):        # el archivo de prueba desaparece al cerrarse
            pass
    except OSError:
        return _("no se puede escribir en ella.")
    return None


def start_folder(configured, fallback: str) -> tuple[str, Optional[str]]:
    """(carpeta por la que empieza una ventana de abrir o guardar, problema de la configurada).

    Sin configurar sale `fallback` y ningun problema. Configurada y buena sale ella. Configurada y mala sale `fallback`
    junto al motivo: quien llama decide si pregunta, no se sustituye en silencio."""
    s = clean(configured)
    if not s:
        return fallback, None
    why = problem(s)
    return (fallback, why) if why else (s, None)


def describe(kind: str, text) -> str:
    """El motivo por el que la carpeta configurada no vale, listo para un mensaje («La carpeta de G-code (D:\\nc) no
    existe...»); vacio si vale o no esta configurada."""
    s = clean(text)
    why = problem(s)
    if not why:
        return ""
    if kind == JOBS:
        return _("La carpeta de trabajos ({s}) {why}", s=s, why=why)
    return _("La carpeta de G-code ({s}) {why}", s=s, why=why)
