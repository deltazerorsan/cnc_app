"""Herramienta de desarrollo para los idiomas (no hace falta para usar el programa: el catalogo .mo ya va hecho).

    python i18n_tool.py extract     busca los _("...") del codigo y deja locales/cnc_app.pot
    python i18n_tool.py update      pasa los textos nuevos a locales/en/LC_MESSAGES/cnc_app.po (sin traducir)
    python i18n_tool.py compile     genera cnc_app.mo desde el .po (es lo que lee el programa)
    python i18n_tool.py missing     lista los textos sin traducir
    python i18n_tool.py all         extract + update + compile + missing

Necesita Babel (`pip install babel`, esta en requirements-dev.txt). Los textos estan en español; el unico idioma extra
es el ingles. Ver cnc/i18n.py para como se marcan los textos.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOCALES = ROOT / "locales"
POT = LOCALES / "cnc_app.pot"
PO_EN = LOCALES / "en" / "LC_MESSAGES" / "cnc_app.po"
MO_EN = LOCALES / "en" / "LC_MESSAGES" / "cnc_app.mo"
SOURCES = ["cnc", "cnc_gui.py"]
KEYWORDS = {"_": None, "N_": None, "_n": (1, 2)}


def collect():
    """Los textos marcados con _(), _n() y N_() en el codigo, como un catalogo de Babel (sin escribir nada)."""
    from babel.messages.catalog import Catalog
    from babel.messages.extract import extract_from_dir, extract_from_file

    cat = Catalog(project="cnc_app", charset="utf-8", fuzzy=False, locale=None)
    cat.header_comment = "# Textos de cnc_app (español, idioma base). Generado por i18n_tool.py extract."
    for src in SOURCES:
        path = ROOT / src
        found = (extract_from_dir(str(path), keywords=KEYWORDS, comment_tags=("i18n:",), strip_comment_tags=True)
                 if path.is_dir() else
                 ((src, *item) for item in extract_from_file("python", str(path), keywords=KEYWORDS)))
        for filename, lineno, message, comments, context in found:
            if not str(filename).endswith(".py"):
                continue
            cat.add(message, locations=[(str(filename).replace("\\", "/"), lineno)], auto_comments=comments, context=context)
    return cat


def extract() -> int:
    from babel.messages.pofile import write_po

    cat = collect()
    LOCALES.mkdir(exist_ok=True)
    with POT.open("wb") as f:
        write_po(f, cat, width=0, sort_by_file=True)
    print(f"{len(list(cat))-1} textos en {POT.relative_to(ROOT)}")
    return 0


def update() -> int:
    from babel.messages.catalog import Catalog
    from babel.messages.pofile import read_po, write_po

    with POT.open("rb") as f:
        template = read_po(f)
    if PO_EN.is_file():
        with PO_EN.open("rb") as f:
            cat = read_po(f, locale="en")
    else:
        cat = Catalog(locale="en", project="cnc_app", charset="utf-8")
        cat.header_comment = "# Ingles de cnc_app. Los msgid estan en español."
    cat.update(template, no_fuzzy_matching=True)
    cat.fuzzy = False
    gone = [m.id for m in cat.obsolete.values()]
    cat.obsolete.clear()                  # lo que ya no esta en el codigo se va tambien del catalogo (queda en el historial)
    PO_EN.parent.mkdir(parents=True, exist_ok=True)
    with PO_EN.open("wb") as f:
        write_po(f, cat, width=0, sort_by_file=True, include_previous=False)
    print(f"{PO_EN.relative_to(ROOT)}: {len(list(cat))-1} textos, {len(gone)} obsoletos quitados")
    return 0


def compile_mo() -> int:
    from babel.messages.mofile import write_mo
    from babel.messages.pofile import read_po

    with PO_EN.open("rb") as f:
        cat = read_po(f, locale="en")
    with MO_EN.open("wb") as f:
        write_mo(f, cat)
    done = sum(1 for m in cat if m.id and m.string and (m.string if isinstance(m.string, str) else any(m.string)))
    print(f"{MO_EN.relative_to(ROOT)}: {done} traducidos")
    return 0


def missing() -> int:
    from babel.messages.pofile import read_po

    with PO_EN.open("rb") as f:
        cat = read_po(f, locale="en")
    todo = [m for m in cat if m.id and not (m.string if isinstance(m.string, str) else all(m.string))]
    for m in todo:
        print(repr(m.id if isinstance(m.id, str) else m.id[0]))
    print(f"{len(todo)} sin traducir")
    return 0


def main(argv) -> int:
    cmd = argv[0] if argv else "all"
    steps = {"extract": extract, "update": update, "compile": compile_mo, "missing": missing}
    if cmd == "all":
        for name in ("extract", "update", "compile", "missing"):
            steps[name]()
        return 0
    if cmd not in steps:
        print(__doc__)
        return 2
    return steps[cmd]()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
