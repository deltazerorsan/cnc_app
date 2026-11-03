"""Aislamiento de las pruebas: ninguna debe depender del cnc_app.toml real del usuario."""
import os

import pytest

# Las pruebas comprueban textos en español: el idioma se fija antes de que nada importe cnc (los subprocesos lo heredan).
# Las pruebas de otro idioma lo cambian en un subproceso propio.
os.environ.setdefault("CNC_APP_LANG", "es")


@pytest.fixture(autouse=True, scope="session")
def _sin_configuracion_ambiente():
    """Las pruebas de la linea de comandos no deben leer el cnc_app.toml de la carpeta actual ni del
    programa; las que quieran configuracion la pasan con --config. (Sesion, no funcion: varias
    pruebas generan el G-code en fixtures de modulo.)"""
    import svg_a_gcode

    mp = pytest.MonkeyPatch()
    mp.setattr(svg_a_gcode, "find_config", lambda cwd, script_dir: None)
    yield
    mp.undo()


@pytest.fixture(autouse=True, scope="session")
def _maquina_del_taller_para_la_linea_de_comandos():
    """El maquinas.json que se entrega trae solo el emulador (700 x 1017, sin homing). Las pruebas de la linea de comandos
    que comparan G-code necesitan una maquina de taller conocida (651 x 1011, con homing): cuando la carpeta del programa
    es la de verdad, sin --machines-file se busca `maquinas.json` en la carpeta actual (si no es la del programa) y despues en
    tests/data/taller.
    Si la prueba mueve la carpeta del programa (cnc.pipeline.app_dir) manda la suya, y la que quiera el archivo que se
    entrega usa cnc.pipeline.find_machines_file, que no se toca."""
    from pathlib import Path

    import svg_a_gcode
    from cnc import pipeline

    original = svg_a_gcode.find_machines_file
    root = Path(__file__).resolve().parent
    taller = root / "tests" / "data" / "taller"

    def find_machines_file(explicit=None, folders=None):
        if folders is None:
            program, cwd = Path(pipeline.app_dir()), Path.cwd()
            folders = ((cwd,) if cwd != root else ()) + ((taller,) if program == root else (program,))
        return original(explicit, folders)

    mp = pytest.MonkeyPatch()
    mp.setattr(svg_a_gcode, "find_machines_file", find_machines_file)
    yield
    mp.undo()


@pytest.fixture(autouse=True, scope="session")
def _los_archivos_que_se_entregan_no_se_tocan():
    """maquinas.json y herramientas.json de la carpeta del programa son los que se entregan: una prueba que escriba
    en ellos (p. ej. una importacion de maquina sin carpeta aislada) los estropearia en silencio. Se restauran y la
    sesion falla."""
    from pathlib import Path
    root = Path(__file__).resolve().parent
    watched = {p: (p.read_bytes() if p.is_file() else None) for p in (root / "maquinas.json", root / "herramientas.json",
                                                                         root / "maquinas.json.bak",
                                                                         root / "herramientas.json.bak",
                                                                         root / "tests" / "data" / "taller" / "maquinas.json",
                                                                         root / "tests" / "data" / "taller" / "maquinas.json.bak")}
    yield
    changed = []
    for path, data in watched.items():
        now = path.read_bytes() if path.is_file() else None
        if now != data:
            changed.append(path.name)
            if data is None:
                path.unlink()
            else:
                path.write_bytes(data)
    assert not changed, f"las pruebas han tocado {', '.join(changed)} (restaurado)"


@pytest.fixture
def checklist_on(monkeypatch):
    """La lista de comprobacion esta desconectada de serie (`cnc.checklist.ENABLED`): las pruebas de la lista la conectan."""
    import cnc.checklist

    monkeypatch.setattr(cnc.checklist, "ENABLED", True)
