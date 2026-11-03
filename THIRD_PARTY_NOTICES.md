# Third-party components / Componentes de terceros

The `LICENSE` of cnc_app (PolyForm Noncommercial 1.0.0) covers only cnc_app's own code. The components below keep their own licenses, which are not changed by it; the noncommercial restriction does not apply to them.

*La `LICENSE` de cnc_app (PolyForm Noncommercial 1.0.0) cubre solo el código propio de cnc_app. Los componentes de abajo conservan sus propias licencias, que ella no modifica; la restricción de uso no comercial no se aplica a ellos.*

| Component | License | Where it is used |
|---|---|---|
| [PySide6-Essentials / Shiboken6](https://pypi.org/project/PySide6-Essentials/) (Qt for Python, Qt) | LGPL-3.0 (alternatively GPL-2.0 / GPL-3.0) | The window. Included in `cnc_app.exe` |
| [Shapely](https://pypi.org/project/shapely/) | BSD-3-Clause | Geometry. Included in `cnc_app.exe` |
| [GEOS](https://libgeos.org/) (shipped inside Shapely) | LGPL-2.1 | Geometry engine. Included in `cnc_app.exe` |
| [NumPy](https://pypi.org/project/numpy/) | BSD-3-Clause (and the licenses of the libraries it ships) | Required by Shapely. Included in `cnc_app.exe` |
| [svgelements](https://pypi.org/project/svgelements/) | MIT | SVG reading. Included in `cnc_app.exe` |
| [Python](https://www.python.org/) | PSF License | Interpreter. Included in `cnc_app.exe` |
| [PyInstaller](https://pyinstaller.org/) | GPL-2.0-or-later with an exception that allows bundling it into applications under any license | Builds `cnc_app.exe`; not distributed with the program |
| pytest, pytest-qt, Babel, matplotlib | MIT / BSD / matplotlib license | Development and tests only; not distributed with the program |

The full license text of each package is in its distribution (the `*.dist-info` folders of the installed packages) and on its page above.

**LGPL components (Qt for Python, GEOS).** `cnc_app.exe` is built from the sources in this repository with `build_windows_onefile.bat` (PyInstaller) and the unmodified libraries from PyPI. You can rebuild it with a different version of those libraries by following the same steps (`requirements.txt`). The source code of Qt for Python and Qt is available from <https://code.qt.io/> and that of GEOS from <https://libgeos.org/>.

*Componentes LGPL (Qt for Python, GEOS). `cnc_app.exe` se genera a partir de los fuentes de este repositorio con `build_windows_onefile.bat` (PyInstaller) y las bibliotecas sin modificar de PyPI. Se puede recompilar con otra versión de esas bibliotecas siguiendo los mismos pasos (`requirements.txt`). El código fuente de Qt for Python y de Qt está en <https://code.qt.io/> y el de GEOS en <https://libgeos.org/>.*
