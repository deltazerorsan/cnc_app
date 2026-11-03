"""Idiomas del programa.

Los textos del codigo estan escritos en español (idioma base): `_("Abrir dibujo")`. Con el idioma `es` el texto sale
tal cual; con `en` se busca en `locales/en/LC_MESSAGES/cnc_app.mo` (gettext) y, si falta, sale el español. El idioma
se fija al arrancar y no cambia mientras el programa esta abierto: los textos que se definen al importar un modulo
(listas de etiquetas, ayudas) usan el idioma que ya habia entonces.

    _("texto")                       texto fijo
    _("{n} figuras", n=3)            con datos: SIEMPRE marcadores con nombre, nunca f-strings ni +
    _n("{n} figura", "{n} figuras", n)   singular / plural (n entra solo como `n`)
    N_("texto")                      marca un texto para el catalogo sin traducirlo aun (se traduce al usarlo)

Idioma, por orden: variable de entorno CNC_APP_LANG (es | en | pseudo | auto), lo que se eligio en el menu
Idioma (guardado con QSettings) y, si es `auto`, el del sistema (español si empieza por "es"; si no, ingles).
`pseudo` solo es para pruebas: pone acentos a todo texto traducido para delatar los que se escaparon sin marcar.

Numeros: las entradas aceptan siempre coma y punto; `number()` da el texto de un numero con el separador decimal del
idioma (coma en español, punto en ingles).
"""
from __future__ import annotations

import gettext
import locale
import os
import sys
from pathlib import Path
from typing import Optional

DOMAIN = "cnc_app"
SOURCE = "es"                                   # idioma en que estan escritos los textos del codigo
LANGUAGES = ("es", "en")                        # los que se pueden elegir
ENV_VAR = "CNC_APP_LANG"
SETTING_KEY = "language"
PSEUDO = "pseudo"                               # solo pruebas

_catalog: gettext.NullTranslations = gettext.NullTranslations()
_language = SOURCE


def locales_dir() -> Path:
    """Carpeta `locales`: junto al codigo, o dentro de la carpeta de datos de PyInstaller si va empaquetado."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "locales"
    return Path(__file__).resolve().parent.parent / "locales"


def normalize(code: Optional[str]) -> Optional[str]:
    """'es_ES', 'es-ES', 'ES' -> 'es'; 'en_GB' -> 'en'; 'auto' y 'pseudo' tal cual; otra cosa -> None."""
    if not code:
        return None
    text = str(code).strip().lower().replace("-", "_")
    if text in ("auto", PSEUDO):
        return text
    head = text.split("_")[0].split(".")[0]
    return head if head in LANGUAGES else None


def system_language() -> str:
    """Idioma del sistema reducido a los que hay: 'es' si empieza por es, si no 'en'."""
    name = ""
    try:                                        # con Qt, el del sistema operativo (en Windows, el de la interfaz)
        from PySide6.QtCore import QLocale
        name = QLocale.system().name()
    except Exception:                           # noqa: BLE001 - sin Qt se usa locale
        try:
            name = locale.getlocale()[0] or os.environ.get("LANG", "")
        except Exception:                       # noqa: BLE001
            name = ""
    return "es" if str(name).lower().startswith("es") else "en"


def saved_language() -> Optional[str]:
    """Lo elegido en el menu Idioma (QSettings), o None si no hay nada o no hay Qt."""
    try:
        from PySide6.QtCore import QSettings
        return normalize(str(QSettings("cnc_app", "cnc_app").value(SETTING_KEY, "") or ""))
    except Exception:                           # noqa: BLE001
        return None


def resolve(setting: Optional[str] = None) -> str:
    """El idioma que toca: entorno > `setting` > auto (sistema). Devuelve es, en o pseudo."""
    for candidate in (os.environ.get(ENV_VAR), setting):
        code = normalize(candidate)
        if code in LANGUAGES or code == PSEUDO:
            return code
    return system_language()


def _pseudo(text: str) -> str:
    """Texto de pruebas: letras con acento y entre corchetes; los {marcadores} y el % se dejan intactos."""
    table = str.maketrans("aeiouAEIOUcnyCNY", "àéîõüÀÉÎÕÜçñýÇÑÝ")
    out, depth = [], 0
    for ch in text:
        if ch == "{":
            depth += 1
        if depth:
            out.append(ch)
        else:
            out.append(ch.translate(table))
        if ch == "}" and depth:
            depth -= 1
    return "⟦" + "".join(out) + "⟧"


class _Pseudo(gettext.NullTranslations):
    def gettext(self, message):
        return _pseudo(message)

    def ngettext(self, singular, plural, n):
        return _pseudo(singular if n == 1 else plural)


def set_language(code: str) -> str:
    """Carga el catalogo del idioma. Devuelve el idioma que ha quedado (es si falta el catalogo)."""
    global _catalog, _language
    code = normalize(code) or SOURCE
    if code == "auto":
        code = system_language()
    if code == PSEUDO:
        _catalog, _language = _Pseudo(), PSEUDO
    elif code == SOURCE:
        _catalog, _language = gettext.NullTranslations(), SOURCE
    else:
        try:
            _catalog = gettext.translation(DOMAIN, str(locales_dir()), languages=[code])
            _language = code
        except OSError:
            _catalog, _language = gettext.NullTranslations(), SOURCE
    return _language


def current_language() -> str:
    return _language


def use_saved_language() -> str:
    """Para el arranque de la ventana: entorno, si no lo elegido en el menu, si no el del sistema."""
    return set_language(resolve(saved_language()))


def _(msgid: str, **kw) -> str:
    """Texto traducido; con `kw`, rellena los {marcadores}."""
    text = _catalog.gettext(msgid)
    return text.format(**kw) if kw else text


def _n(singular: str, plural: str, n: int, **kw) -> str:
    """Singular o plural segun `n` (que tambien es el marcador {n})."""
    text = _catalog.ngettext(singular, plural, n)
    return text.format(n=n, **kw)


def N_(msgid: str) -> str:
    """Marca un texto para el catalogo sin traducirlo en este punto."""
    return msgid


def number(value: float, decimals: int = 3, strip: bool = True) -> str:
    """Un numero como texto con el separador decimal del idioma. `strip`: sin ceros ni punto sobrantes (2,5 y no 2,500)."""
    text = f"{value:.{decimals}f}"
    if strip and "." in text:
        text = text.rstrip("0").rstrip(".")
    text = "0" if text in ("", "-0") else text
    return text.replace(".", ",") if _language == "es" else text


_env = normalize(os.environ.get(ENV_VAR))
if _env in LANGUAGES or _env == PSEUDO:
    set_language(_env)
