"""Lectura del archivo de configuracion (cnc_app.toml) con los valores por defecto del programa.

TOML en vez de JSON porque admite comentarios, que es lo que hace falta en un archivo que se
edita a mano. Necesita Python 3.11+ (modulo tomllib) o `pip install tomli`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional
from .i18n import _

CONFIG_NAME = "cnc_app.toml"

try:  # Python 3.11+
    import tomllib as _toml
except ImportError:  # pragma: no cover - depende de la version de Python
    try:
        import tomli as _toml  # type: ignore[no-redef]
    except ImportError:
        _toml = None


class ConfigError(ValueError):
    """Error al leer el archivo de configuracion (el mensaje dice cual y por que)."""


def find_config(cwd: Path, script_dir: Path) -> Optional[Path]:
    """cnc_app.toml en la carpeta actual y, si no esta, junto al programa."""
    for folder in (cwd, script_dir):
        candidate = Path(folder) / CONFIG_NAME
        if candidate.is_file():
            return candidate
    return None


def read_config(path: Path, what: str = _("archivo de configuracion")) -> dict:
    path = Path(path)
    if _toml is None:
        raise ConfigError(
            _("No se puede leer {name}: hace falta Python 3.11 o superior, o `pip install tomli`.", name=path.name)
        )
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise ConfigError(_("No existe el {what} {path}.", what=what, path=path)) from None
    except OSError as e:                      # p. ej. macOS sin permiso para leer un disco externo o una carpeta protegida
        raise ConfigError(_("No se puede leer {path}: {e}", path=path, e=e.strerror or e)) from None
    try:
        return _toml.loads(text)
    except _toml.TOMLDecodeError as e:
        raise ConfigError(_("{name}: TOML invalido: {e}", name=path.name, e=e)) from None
