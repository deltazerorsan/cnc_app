"""app_dir(): la carpeta del programa junto al .exe, o en macOS la que contiene el .app (no el interior del paquete)."""
import sys
from pathlib import Path

from cnc import pipeline

ROOT = Path(__file__).resolve().parent.parent


def _frozen(monkeypatch, exe: str, platform: str):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", exe)
    monkeypatch.setattr(sys, "platform", platform)


def test_desde_el_codigo_fuente_es_la_carpeta_del_proyecto():
    assert pipeline.app_dir() == ROOT


def test_empaquetado_en_windows_o_linux_es_la_carpeta_del_ejecutable(monkeypatch):
    _frozen(monkeypatch, "/opt/cnc/cnc_app", "linux")
    assert pipeline.app_dir() == Path("/opt/cnc")


def test_empaquetado_en_macos_es_la_carpeta_que_contiene_el_app(monkeypatch):
    _frozen(monkeypatch, "/Users/ana/cnc/cnc_app.app/Contents/MacOS/cnc_app", "darwin")
    assert pipeline.app_dir() == Path("/Users/ana/cnc")


def test_en_macos_sin_paquete_app_es_la_carpeta_del_ejecutable(monkeypatch):
    """Una carpeta de PyInstaller sin BUNDLE (dist/cnc_app/cnc_app) no es un .app: manda la carpeta del ejecutable."""
    _frozen(monkeypatch, "/Users/ana/dist/cnc_app/cnc_app", "darwin")
    assert pipeline.app_dir() == Path("/Users/ana/dist/cnc_app")
    _frozen(monkeypatch, "/Users/ana/Contents/MacOS/cnc_app", "darwin")      # sin el .app arriba: tampoco
    assert pipeline.app_dir() == Path("/Users/ana/Contents/MacOS")


def test_la_ruta_del_app_solo_cuenta_en_macos(monkeypatch):
    _frozen(monkeypatch, "/x/cnc_app.app/Contents/MacOS/cnc_app", "linux")
    assert pipeline.app_dir() == Path("/x/cnc_app.app/Contents/MacOS")
