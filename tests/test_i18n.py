"""Idiomas (v0.25): el catalogo del ingles esta completo y bien formado, la ventana no deja textos sin traducir, el G-code sale
igual salvo sus comentarios y el español sigue siendo el de siempre.

Las pruebas de ventana y de G-code se lanzan en un subproceso por idioma (el idioma se fija al arrancar). El catalogo se
comprueba con Babel (solo de desarrollo: `pip install -r requirements-dev.txt`); sin Babel esas pruebas se saltan."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gettext
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "locales"
POT = LOCALES / "cnc_app.pot"
PO_EN = LOCALES / "en" / "LC_MESSAGES" / "cnc_app.po"
MO_EN = LOCALES / "en" / "LC_MESSAGES" / "cnc_app.mo"
PLACEHOLDER = re.compile(r"\{[^{}]*\}")
# Textos que se ven igual en todos los idiomas o que no son del programa (botones estandar de Qt, identificadores de fichas...).
SAME_IN_ALL = {"mm", "OK", "Cancel", "Save", "Español", "English", "em4", "real", "taller", "emulador"}


def run_python(script: str, *args: str, lang: str, timeout: int = 240) -> dict:
    env = dict(os.environ, CNC_APP_LANG=lang, QT_QPA_PLATFORM="offscreen")
    done = subprocess.run([sys.executable, str(ROOT / "tests" / script), *args], env=env, capture_output=True, text=True,
                          timeout=timeout, cwd=str(ROOT))
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def ids_of(message) -> tuple:
    return message.id if isinstance(message.id, tuple) else (message.id,)


def strings_of(message) -> tuple:
    return message.string if isinstance(message.string, tuple) else (message.string,)


@pytest.fixture(scope="module")
def catalog():
    pytest.importorskip("babel")
    from babel.messages.pofile import read_po
    with PO_EN.open("rb") as f:
        return read_po(f, locale="en")


# --- el catalogo ---------------------------------------------------------------------------------------------------------------
def test_the_template_and_the_catalog_have_every_text_of_the_code(catalog):
    pytest.importorskip("babel")
    from babel.messages.pofile import read_po
    import i18n_tool
    in_code = {ids_of(m)[0] for m in i18n_tool.collect() if m.id}
    with POT.open("rb") as f:
        in_pot = {ids_of(m)[0] for m in read_po(f) if m.id}
    in_po = {ids_of(m)[0] for m in catalog if m.id}
    assert in_code == in_pot, f"hace falta `python i18n_tool.py all`: {sorted(in_code ^ in_pot)[:5]}"
    assert in_code == in_po, f"hace falta `python i18n_tool.py all`: {sorted(in_code ^ in_po)[:5]}"


def test_no_text_is_left_untranslated_or_empty(catalog):
    todo = [ids_of(m)[0] for m in catalog if m.id and not all(strings_of(m))]
    assert not todo, f"{len(todo)} sin traducir, por ejemplo {todo[:3]}"
    plurals = [ids_of(m)[0] for m in catalog if m.id and isinstance(m.id, tuple) and len(strings_of(m)) != 2]
    assert not plurals, f"plurales sin sus dos formas: {plurals}"


def test_the_markers_of_every_translation_match_the_original(catalog):
    """Un {marcador} que falta o sobra rompe el texto al rellenarlo. Unico permitido: los dos mensajes de machinebook que en
    español llevan el articulo ({article}) y en ingles no lo necesitan."""
    allowed = {"{article}"}
    bad = []
    for m in catalog:
        if not m.id:
            continue
        original = sorted(PLACEHOLDER.findall(ids_of(m)[0]))
        for text in strings_of(m):
            found = sorted(PLACEHOLDER.findall(text))
            if found != original and sorted(set(original) - allowed) != found:
                bad.append((ids_of(m)[0][:60], original, found))
    assert not bad, bad[:3]


def test_every_translation_fills_in_with_the_markers_of_its_original(catalog):
    """Se rellena cada texto como lo hace el codigo (`format`), con numeros de prueba, para ver que ninguno falla."""
    class Anything(dict):
        def __missing__(self, key):
            return 1

    for m in catalog:
        if not m.id:
            continue
        for text in strings_of(m):
            names = {f.split(":")[0].split("!")[0].strip("{}") for f in PLACEHOLDER.findall(text)}
            kwargs = {n: 1 for n in names if n}
            positional = [1] if "{}" in text or any(f.startswith("{:") for f in PLACEHOLDER.findall(text)) else []
            try:
                text.format(*positional, **kwargs)
            except (KeyError, IndexError, ValueError) as e:
                pytest.fail(f"{text!r}: {e!r}")


def test_the_compiled_catalog_is_the_current_one(catalog):
    """El .mo es lo que lee el programa: tiene que decir lo mismo que el .po."""
    assert MO_EN.is_file()
    with MO_EN.open("rb") as f:
        compiled = gettext.GNUTranslations(f)
    for m in catalog:
        if not m.id:
            continue
        ids, strings = ids_of(m), strings_of(m)
        if isinstance(m.id, tuple):
            assert compiled.ngettext(ids[0], ids[1], 1) == strings[0], ids[0]
            assert compiled.ngettext(ids[0], ids[1], 2) == strings[1], ids[0]
        else:
            assert compiled.gettext(ids[0]) == strings[0], f"el .mo no esta al dia: `python i18n_tool.py compile` ({ids[0][:50]})"


# --- la biblioteca ---------------------------------------------------------------------------------------------------------------
@pytest.fixture
def language():
    """Cambia el idioma de este proceso para una prueba y lo deja como estaba."""
    from cnc import i18n
    before = i18n.current_language()
    yield i18n.set_language
    i18n.set_language(before)


def test_spanish_is_the_text_of_the_code_itself(language):
    from cnc import i18n
    language("es")
    assert i18n._("Hola") == "Hola"
    assert i18n._("{n} figuras", n=3) == "3 figuras"
    assert i18n._n("{n} forma", "{n} formas", 1) == "1 forma"
    assert i18n._n("{n} forma", "{n} formas", 2) == "2 formas"
    assert i18n.N_("sin tocar") == "sin tocar"


def test_english_translates_and_keeps_unknown_texts(language):
    from cnc import i18n
    language("en")
    assert i18n.current_language() == "en"
    assert i18n._("Abrir dibujo SVG…") == "Open SVG drawing…"
    assert i18n._("texto que no esta en el catalogo") == "texto que no esta en el catalogo"
    assert i18n._n("{n} forma", "{n} formas", 1) == "1 shape"
    assert i18n._n("{n} forma", "{n} formas", 3) == "3 shapes"


def test_numbers_use_the_decimal_separator_of_the_language(language):
    from cnc import i18n
    language("es")
    assert (i18n.number(2.5), i18n.number(2.0), i18n.number(0.1 + 0.2), i18n.number(-0.0001)) == ("2,5", "2", "0,3", "0")
    assert i18n.number(1.23456, 2, strip=False) == "1,23" and i18n.number(3.0, 1, strip=False) == "3,0"
    language("en")
    assert (i18n.number(2.5), i18n.number(2.0), i18n.number(0.1 + 0.2), i18n.number(-0.0001)) == ("2.5", "2", "0.3", "0")
    assert i18n.number(1.23456, 2, strip=False) == "1.23" and i18n.number(3.0, 1, strip=False) == "3.0"


def test_the_language_is_chosen_by_environment_then_setting_then_system(monkeypatch):
    from cnc import i18n
    monkeypatch.setenv(i18n.ENV_VAR, "es")
    assert i18n.resolve("en") == "es"                                   # el entorno manda
    monkeypatch.setenv(i18n.ENV_VAR, "en_GB")
    assert i18n.resolve("es") == "en"
    monkeypatch.delenv(i18n.ENV_VAR)
    assert i18n.resolve("en") == "en" and i18n.resolve("ES-es") == "es"  # luego lo elegido en el menu
    monkeypatch.setattr(i18n, "system_language", lambda: "en")
    assert i18n.resolve("auto") == "en" and i18n.resolve(None) == "en" and i18n.resolve("klingon") == "en"
    monkeypatch.setattr(i18n, "system_language", lambda: "es")
    assert i18n.resolve("auto") == "es" and i18n.resolve("") == "es"
    assert [i18n.normalize(c) for c in ("fr", "", None, "AUTO", "es_MX", "en-US")] == [None, None, None, "auto", "es", "en"]


# --- la ventana ------------------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def pseudo_texts():
    pytest.importorskip("PySide6")
    return run_python("i18n_probe.py", lang="pseudo")["texts"]


@pytest.fixture(scope="module")
def english_texts():
    pytest.importorskip("PySide6")
    return run_python("i18n_probe.py", lang="en")["texts"]


def test_the_probe_reaches_the_dialogs(pseudo_texts):
    places = {where.split("/")[0] for where, __ in pseudo_texts if "/" in where}
    assert {"ToolsDialog", "MachinesDialog", "FoldersDialog", "GroupDialog", "ToolDialog", "LogImportDialog",
            "ChecklistDialog"} <= places, places
    assert not [w for w, __ in pseudo_texts if w.startswith("ERROR")], [x for x in pseudo_texts if x[0].startswith("ERROR")]
    assert len(pseudo_texts) > 500


def test_every_text_of_the_window_goes_through_the_catalog(pseudo_texts):
    """En el idioma de pruebas cada texto traducido sale entre ⟦ ⟧: lo que no lleva esas marcas se escribio sin `_()`."""
    loose = []
    for where, text in pseudo_texts:
        if "⟦" in text or text in SAME_IN_ALL or re.fullmatch(r"#[0-9a-fA-F]{6}", text) \
                or not re.search(r"[A-Za-zÁÉÍÓÚáéíóúñÑ]{2,}", text):
            continue
        loose.append((where, text[:80]))
    assert not loose, loose[:10]


def test_the_english_window_has_no_spanish_left(english_texts):
    spanish = re.compile(r"[áéíóúñ¿¡]|\b(el|los|las|del|para|con|una|sin|por|tablero|fresa|dibujo|figura|archivo|trabajo)\b",
                         re.IGNORECASE)
    left = [(where, text[:80]) for where, text in english_texts if text not in SAME_IN_ALL and spanish.search(text)]
    assert not left, left[:10]
    assert not [x for x in english_texts if "{" in x[1] and "}" in x[1] and "color" not in x[1]]


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    pytest.importorskip("PySide6")
    out = {}
    for lang in ("es", "en"):
        folder = tmp_path_factory.mktemp(f"gen_{lang}")
        out[lang] = run_python("i18n_generate.py", str(folder), lang=lang)
    return out


def test_the_gcode_is_the_same_in_both_languages_except_for_its_comments(generated):
    es, en = generated["es"], generated["en"]
    assert es["language"] == "es" and en["language"] == "en"
    assert es["paths"] == en["paths"] == ["placa.nc"]
    assert es["body_lines"] > 1000
    assert es["body_sha256"] == en["body_sha256"]
    assert "Fresa" in "\n".join(es["gcode_head"]) and "Tool" in "\n".join(en["gcode_head"])
    for line in en["gcode_head"]:                                       # GRBL solo admite ASCII
        assert line.isascii(), line


def test_the_messages_come_out_in_the_language_chosen(generated):
    assert "líneas" in generated["es"]["summary"] and "lines" in generated["en"]["summary"]
    assert "G-code generado" in generated["es"]["status"] and "G-code generated" in generated["en"]["status"]


# --- el menu Idioma --------------------------------------------------------------------------------------------------------------
def test_the_language_menu_remembers_the_choice(qtbot, tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    pytest.importorskip("pytestqt")
    from PySide6.QtWidgets import QMessageBox
    from cnc.gui.main_window import MainWindow
    from cnc.model import Tool
    from cnc.project import Project
    from cnc import i18n

    shown = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a[2])))
    settings = str(tmp_path / "ajustes.ini")
    tools = {"em4": Tool(diameter=4, name="End mill 4 mm")}

    def window():
        w = MainWindow(Project(tools), [], use_settings=False, settings_file=settings)
        qtbot.addWidget(w)
        return w

    w = window()
    assert set(w.language_actions) == {"auto", "es", "en"} and w.language_actions["auto"].isChecked()
    other = "en" if i18n.current_language() == "es" else "es"
    w.language_actions[other].trigger()
    assert w._pref(i18n.SETTING_KEY) == other
    assert len(shown) == 1 and "Idioma guardado" in shown[0] and "Language saved" in shown[0]   # el mensaje va en los dos idiomas
    assert window().language_actions[other].isChecked()                 # y la siguiente ventana lo muestra
    same = i18n.current_language()
    w.language_actions[same].trigger()
    assert len(shown) == 1 and w._pref(i18n.SETTING_KEY) == same         # el idioma de ahora no pide reiniciar


def test_numbers_are_typed_with_comma_or_dot_in_any_language(qtbot):
    pytest.importorskip("PySide6")
    pytest.importorskip("pytestqt")
    from cnc.gui.widgets import OptionalNumber
    box = OptionalNumber()
    qtbot.addWidget(box)
    for text, value in (("1,5", 1.5), ("1.5", 1.5), ("-2,25", -2.25), ("7", 7.0)):
        box.setText(text)
        assert box.value() == value, text
