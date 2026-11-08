"""Un archivo de ajustes que el sistema no deja leer (macOS sin permiso para un disco externo, p. ej.) sale como aviso
claro y no como un error inesperado: el programa abre igualmente."""
import errno
from pathlib import Path

import pytest

from cnc.config import ConfigError, read_config
from cnc.machines import MachinesFileError, load_machines
from cnc.project import startup_project
from cnc.tools import ToolsFileError, load_tools


@pytest.fixture
def denegar(monkeypatch):
    """Path.read_text falla con 'Operation not permitted' para los archivos cuyo nombre se indique."""
    original = Path.read_text

    def _denegar(*nombres):
        def read_text(self, *args, **kwargs):
            if self.name in nombres:
                raise PermissionError(errno.EPERM, "Operation not permitted", str(self))
            return original(self, *args, **kwargs)
        monkeypatch.setattr(Path, "read_text", read_text)
    return _denegar


def test_cada_lector_da_un_aviso_con_el_motivo(tmp_path, denegar):
    for name in ("cnc_app.toml", "herramientas.json", "maquinas.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    denegar("cnc_app.toml", "herramientas.json", "maquinas.json")
    with pytest.raises(ConfigError, match="No se puede leer .*cnc_app.toml: Operation not permitted"):
        read_config(tmp_path / "cnc_app.toml")
    with pytest.raises(ToolsFileError, match="No se puede leer .*herramientas.json: Operation not permitted"):
        load_tools(tmp_path / "herramientas.json")
    with pytest.raises(MachinesFileError, match="No se puede leer .*maquinas.json: Operation not permitted"):
        load_machines(tmp_path / "maquinas.json")


def test_el_arranque_no_se_cae_si_no_puede_leer_los_ajustes(tmp_path, denegar):
    for name in ("cnc_app.toml", "herramientas.json", "maquinas.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    denegar("cnc_app.toml", "herramientas.json", "maquinas.json")
    project, notes = startup_project(tmp_path, tmp_path)
    texts = [m.text for m in notes]
    assert any("cnc_app.toml" in t and "Operation not permitted" in t for t in texts)
    assert any("herramientas.json" in t and "Operation not permitted" in t for t in texts)
    assert any("maquinas.json" in t and "Operation not permitted" in t for t in texts)
    assert project is not None
