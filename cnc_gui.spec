# -*- mode: python ; coding: utf-8 -*-
# Empaquetado con PyInstaller en UN SOLO archivo autocontenido (en Windows: build_windows_onefile.bat):
#   pyinstaller --noconfirm --clean cnc_gui.spec
# El catalogo del ingles (locales/en/LC_MESSAGES/cnc_app.mo) va dentro del programa; el español es el texto del propio codigo.
# Los archivos que se editan a mano (herramientas.json, maquinas.json, cnc_app.toml, recetas) NO van dentro del programa: se copian
# junto a cnc_app.exe, que es donde los busca `app_dir()`.
import os
import re


from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo,
                                                 VarStruct, VSVersionInfo)


def _version_resource():
    """La version de cnc/__init__.py como recurso de Windows: se ve en Propiedades -> Detalles del exe (clic derecho)."""
    text = open(os.path.join(SPECPATH, "cnc", "__init__.py"), encoding="utf-8").read()
    version = re.search(r'__version__\s*=\s*"(\d+(?:\.\d+){0,3})"', text).group(1)
    numbers = (list(map(int, version.split("."))) + [0, 0, 0, 0])[:4]
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


a = Analysis(
    ["cnc_gui.py"],
    pathex=[],
    binaries=[],
    datas=[("locales/en/LC_MESSAGES/cnc_app.mo", "locales/en/LC_MESSAGES")],      # el ingles (cnc/i18n.py lo busca en locales/ junto a los modulos)
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "scipy", "tkinter", "pytest", "IPython", "PySide6.QtQml", "PySide6.QtQuick"],
    noarchive=False,
)
pyz = PYZ(a.pure)
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
