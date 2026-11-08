# cnc_app (bilingual file)

Pasa dibujos vectoriales (SVG) a G-code GRBL 1.1 (MKS DLC32), con una ventana (PySide6). Cubre el trabajo habitual de taller: contornos, taladros, pozos con islas, tableros y su orden de corte. Enviar, hacer Home, poner el cero y previsualizar sigue siendo cosa de UGS (Universal G-code Sender).

*Turns vector drawings (SVG) into GRBL 1.1 G-code (MKS DLC32), through a window (PySide6). It covers the usual workshop jobs: contours, drilling, pockets with islands, boards and their cutting order. Sending, homing, setting zero and previewing are still done in UGS (Universal G-code Sender).*

## Por qué existe / Why it exists

cnc_app nace en un taller, de la necesidad de tener funciones concretas que el trabajo diario pedía y que no se encontraban juntas en otras herramientas.

*cnc_app was born in a workshop, out of the need for specific functions that daily work demanded and that could not be found together in other tools.*

- **Funcionalidades específicas.** Orden de corte correcto (taladros, pozos, interiores y exteriores), puntos de sujeción propuestos, puentes, y comprobaciones de que el trabajo cabe en la máquina y de que hay fresa asignada antes de generar.
  - *Specific functionality. Correct cutting order (drills, pockets, interiors and exteriors), suggested hold-down points, bridges, and checks that the job fits the machine and that a tool is assigned before generating.*
- **Experiencia de operador.** Las decisiones salen del trabajo con la máquina: qué se corta primero, qué se comprueba antes de empezar y qué avisos evitan estropear material caro.
  - *Operator experience. Decisions come from working with the machine: what is cut first, what is checked before starting, and which warnings prevent ruining expensive material.*
- **Corte 2D.** Contornos, taladros y vaciados sobre tablero. No es una herramienta 3D.
  - *2D cutting. Contours, drilling and pockets on a board. It is not a 3D tool.*
- **Hecha con IA, guiada por la funcionalidad.** Se ha construido con ayuda de Claude (Anthropic), un asistente de inteligencia artificial, y se ha dirigido por lo que el trabajo necesita más que por la técnica. Cada función se ha comprobado con pruebas automáticas y con un emulador de máquina y UGS antes de llegar a la máquina real.
  - *Built with AI, guided by functionality. It was built with the help of Claude (Anthropic), an artificial intelligence assistant, and steered by what the work needs rather than by technique. Every function has been checked with automated tests and with a machine emulator and UGS before reaching the real machine.*
- **Sin ánimo de ser exhaustiva.** Cubre la mayor parte del trabajo habitual, no todos los casos; lo excepcional se resuelve fuera.
  - *Not meant to be exhaustive. It covers most of the usual work, not every case; the exceptional is handled elsewhere.*

## Descarga y documentación / Download and documentation

**Windows, sin instalar Python:** descarga `cnc_app_<versión>_windows.zip` de [Releases](https://github.com/deltazerorsan/cnc_app/releases), descomprímelo y ejecuta `cnc_app.exe`. El código fuente corre con Python 3.11+ (`pip install -r requirements.txt`, `python cnc_gui.py`).

**Linux (Ubuntu 24.04 / Mint 22 o posterior), sin instalar Python:** descarga `cnc_app_<versión>_linux.tar.gz` de [Releases](https://github.com/deltazerorsan/cnc_app/releases), descomprímelo (`tar xzf cnc_app_<versión>_linux.tar.gz`) y ejecuta `./cnc_app`; si no abre, instala `libxcb-cursor0` (`sudo apt install libxcb-cursor0`). Para compilarlo tú: `bash build_linux.sh`.

*Windows, no Python needed: download `cnc_app_<version>_windows.zip` from [Releases](https://github.com/deltazerorsan/cnc_app/releases), unzip it and run `cnc_app.exe`. The source runs with Python 3.11+ (`pip install -r requirements.txt`, `python cnc_gui.py`).*

*Linux (Ubuntu 24.04 / Mint 22 or later), no Python needed: download `cnc_app_<version>_linux.tar.gz` from [Releases](https://github.com/deltazerorsan/cnc_app/releases), unpack it (`tar xzf cnc_app_<version>_linux.tar.gz`) and run `./cnc_app`; if it does not open, install `libxcb-cursor0` (`sudo apt install libxcb-cursor0`). To build it yourself: `bash build_linux.sh`.*

Manual de usuario / User manual: [ES](docs/Manual_cnc_app_ES.pdf) · [EN](docs/Manual_cnc_app_EN.pdf). Documentación técnica / Technical documentation: [ES](docs/TECHNICAL_ES.md) · [EN](docs/TECHNICAL_EN.md). Capturas de pantalla (con errores intencionados, que explican más que una captura "bonita") / Screenshots (with intentional errors, which explain more than a "pretty" one): [ES](docs/captura_ES.png) · [EN](docs/screenshot_EN.png).

## Licencia y aviso / License and disclaimer

cnc_app es **software libre y de código abierto**, publicado bajo la licencia **GNU GPL, versión 3 o posterior** ([LICENSE](LICENSE)). Copyright (C) 2026 deltazerorsan. Se puede **usar en trabajos comerciales** (el G-code que genera es tuyo, sin restricciones), estudiar, modificar y compartir. Si distribuyes copias del código o del programa compilado, con o sin cobrar, tienes que ofrecer el código fuente completo, con tus cambios, bajo esta misma licencia y conservar el aviso de copyright: no se puede convertir en software de código cerrado. Las bibliotecas que lleva conservan sus licencias ([THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)).

**Aviso.** Se entrega **tal cual, sin garantía de ningún tipo y a tu propio riesgo**. Una CNC puede dañar material, herramientas y máquina, y causar lesiones. Revisa siempre el G-code antes de ejecutarlo (aunque cnc_app lo compruebe), prueba primero sin husillo o con un emulador y no dejes la máquina sin vigilancia. El autor no se hace responsable de los daños derivados de su uso.

*cnc_app is **free and open-source software**, released under the **GNU GPL, version 3 or later** ([LICENSE](LICENSE)). Copyright (C) 2026 deltazerorsan. It may be **used for commercial work** (the G-code it generates is yours, without restrictions), studied, modified and shared. If you distribute copies of the code or of the compiled program, whether or not you charge for them, you must offer the complete source code, including your changes, under this same license and keep the copyright notice: it cannot be turned into closed-source software. The libraries it includes keep their own licenses ([THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)).*

***Disclaimer.** Provided **as is, without warranty of any kind and at your own risk**. A CNC machine can damage material, tools and the machine itself, and can cause injury. Always review the G-code before running it (even though cnc_app checks it), test first with the spindle off or with an emulator, and never leave the machine unattended. The author accepts no responsibility for damage arising from its use.*

Para empezar a usarla, ver [QUICK_START_ES.md](QUICK_START_ES.md).

*To get started, see [QUICK_START_EN.md](QUICK_START_EN.md).*
