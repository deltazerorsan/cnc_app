# -*- mode: python ; coding: utf-8 -*-
# Empaquetado con PyInstaller en UN SOLO archivo autocontenido (en Windows: build_windows_onefile.bat; en Linux: build_linux.sh):
#   pyinstaller --noconfirm --clean cnc_gui.spec
# En macOS (build_macos.sh) sale una aplicacion cnc_app.app (carpeta de PyInstaller dentro de un paquete .app): un solo archivo con
# ventana no es lo recomendado alli, y el .app se abre con doble clic.
# El catalogo del ingles (locales/en/LC_MESSAGES/cnc_app.mo) va dentro del programa; el español es el texto del propio codigo.
# Los archivos que se editan a mano (herramientas.json, maquinas.json, cnc_app.toml, recetas) NO van dentro del programa: se copian
# junto a cnc_app.exe (en macOS, junto a cnc_app.app), que es donde los busca `app_dir()`.
import os
import re
import sys

if sys.platform == "win32":          # el recurso de version solo existe en Windows (en Linux ni siquiera esta instalado `pefile`)
    from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo,
                                                     VarStruct, VSVersionInfo)


def _version() -> str:
    """El numero de version de cnc/__init__.py (por ejemplo 0.30.1)."""
    text = open(os.path.join(SPECPATH, "cnc", "__init__.py"), encoding="utf-8").read()
    return re.search(r'__version__\s*=\s*"(\d+(?:\.\d+){0,3})"', text).group(1)


def _version_resource():
    """La version de cnc/__init__.py como recurso de Windows: se ve en Propiedades -> Detalles del exe (clic derecho).
    En otros sistemas no hay recurso (None)."""
    if sys.platform != "win32":
        return None
    numbers = (list(map(int, _version().split("."))) + [0, 0, 0, 0])[:4]
    full = ".".join(map(str, numbers[:3]))
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=tuple(numbers), prodvers=tuple(numbers), mask=0x3F, flags=0x0, OS=0x40004, fileType=0x1,
                          subtype=0x0, date=(0, 0)),
        kids=[StringFileInfo([StringTable("040904B0", [
            StringStruct("FileDescription", "cnc_app - dibujo SVG a G-code para CNC (GRBL)"),
            StringStruct("FileVersion", full),
            StringStruct("InternalName", "cnc_app"),
            StringStruct("OriginalFilename", "cnc_app.exe"),
            StringStruct("ProductName", "cnc_app"),
            StringStruct("ProductVersion", full)])]),
            VarFileInfo([VarStruct("Translation", [0x409, 1200])])])


# Lo que PyInstaller recoge por si acaso y este programa no usa. Quitarlo deja el exe de Windows en unos 36 MB en vez de
# 57, y son unos 50 MB menos que descomprimir en cada arranque. Si algun dia hace falta algo de esto (abrir imagenes, pedir
# algo por https...), se quita de aqui; `cnc_app --autotest` dice si el programa compilado sigue entero.
EXCLUDES = [
    "matplotlib", "scipy", "tkinter", "pytest", "IPython", "PySide6.QtQml", "PySide6.QtQuick",
    "PIL",                                              # lo importa svgelements solo para las imagenes de un SVG: no se usan
    "PySide6.QtSvg", "PySide6.QtOpenGL",
    "setuptools", "pkg_resources", "yaml", "psutil", "charset_normalizer",
    "ssl", "_ssl", "_hashlib",                          # OpenSSL: no hay red; hashlib usa sin el sus propios algoritmos
]
UNUSED_PLUGINS = ("/plugins/imageformats/", "/plugins/tls/", "/plugins/iconengines/")   # no se abren imagenes ni hay red
# OpenGL por software, otra forma de pintar la ventana, el SVG de Qt y el OpenSSL que usaria el plugin de red cifrada
UNUSED_FILES = ("opengl32sw", "qdirect2d", "qt6svg", "libqt6svg", "libcrypto-3-x64", "libssl-3-x64")


def _unused(dest: str) -> bool:
    """Si sobra un archivo de los que recoge PyInstaller (`dest` es su ruta dentro del programa)."""
    path = dest.replace("\\", "/").lower()
    name = path.rsplit("/", 1)[-1]
    if "/translations/" in path:                        # catalogos de Qt: solo se carga el español (cnc/gui/app.py)
        return not name.startswith("qtbase_es.")
    return any(p in path for p in UNUSED_PLUGINS) or name.startswith(UNUSED_FILES)


a = Analysis(
    ["cnc_gui.py"],
    pathex=[],
    binaries=[],
    datas=[("locales/en/LC_MESSAGES/cnc_app.mo", "locales/en/LC_MESSAGES")],      # el ingles (cnc/i18n.py lo busca en locales/ junto a los modulos)
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)
a.binaries = [entry for entry in a.binaries if not _unused(entry[0])]
a.datas = [entry for entry in a.datas if not _unused(entry[0])]
pyz = PYZ(a.pure)
if sys.platform == "darwin":
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,                            # carpeta + .app: las librerias van junto al ejecutable, no dentro de el
        name="cnc_app",
        debug=False,
        strip=False,
        upx=False,
        console=False,                                    # ventana sin terminal
    )
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="cnc_app")
    app = BUNDLE(
        coll,
        name="cnc_app.app",
        icon=None,
        bundle_identifier="io.github.deltazerorsan.cnc_app",
        version=_version(),
        info_plist={
            "CFBundleName": "cnc_app",
            "CFBundleDisplayName": "cnc_app",
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "GPL-3.0-or-later",
        },
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,                                       # un solo archivo: todo va dentro del exe y se descomprime al arrancar
        a.datas,
        [],
        name="cnc_app",
        debug=False,
        strip=False,
        upx=False,
        console=False,          # ventana sin consola
        version=_version_resource(),
    )
