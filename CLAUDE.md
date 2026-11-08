# cnc_app: guía para Claude Code

Programa en Python (PySide6, shapely, svgelements) que convierte dibujos SVG en G-code para GRBL 1.1 (fresadora CNC con UGS). El idioma base del código, los mensajes y los comentarios es el español; el inglés va en `locales/`. Licencia GPL-3.0-or-later.

## Por dónde empezar

Lee `docs/TECHNICAL_ES.md`: la sección *Contenido* es el mapa de módulos y *Convenciones* explica los ejes y el cero. En resumen: `cnc/` es la lógica sin Qt (importador SVG, `pipeline.py`, `generate.py`, `post_grbl.py`, `project.py`), `cnc/gui/` es la ventana y `tests/` lo comprueba todo, con SVG reales y seis `.nc` de referencia en `tests/data/`.

## Comandos

- Pruebas sin pantalla: `QT_QPA_PLATFORM=offscreen python -m pytest -q`. La suite entera tarda de 20 a 30 minutos con 2 núcleos; para iterar, ejecuta solo los ficheros afectados, y antes de dar algo por bueno, la suite completa. `conftest.py` aísla las pruebas del `cnc_app.toml` real del usuario.
- No uses `pytest.approx` con listas de tuplas (el pytest reciente no lo admite): compara las listas aplanadas.
- Idiomas: los textos nuevos van con `_("...")`. Después, `python i18n_tool.py all`, traduce lo que salga en `missing` y sube `.pot`, `.po` y `.mo`.
- Compilar un solo archivo (PyInstaller no compila entre sistemas): en Windows, `build_windows_onefile.bat`; en Linux, `bash build_linux.sh` (con `--tests` pasa antes las pruebas); en macOS, `bash build_macos.sh` (una vez en cada tipo de Mac, Intel y Apple Silicon; necesita las Command Line Tools de Apple). Comprobación sin ventana del programa compilado: `cnc_app --autotest dibujo.svg salida.txt`.
- La versión está en `cnc/__init__.py` (el exe la lleva en sus propiedades). Súbela cuando cambie algo que el usuario vea.

## Convenciones del proyecto

- Documentación bilingüe: todo cambio en `docs/TECHNICAL_ES.md` va igual en `TECHNICAL_EN.md`, y el README lleva cada párrafo en español y en inglés.
- Los archivos que se editan a mano (`herramientas.json`, `maquinas.json`, `cnc_app.toml`, recetas) van junto al programa, no dentro. El programa empaquetado lee `herramientas.json` y `maquinas.json` siempre de su carpeta; desde el código fuente manda la carpeta actual.
- `herramientas.json` y `maquinas.json` de serie intervienen en pruebas: `test_cli_config` valida con la ficha `em4` el trabajo de referencia en una sola pasada, así que no le pongas `profundidad_pasada` sin actualizar esa referencia. Una prueba que necesite una ficha sin feed debe usar una fresa escrita por su diámetro (por ejemplo `"3.175"`), no una ficha de serie.
- El `maquinas.json` de serie lleva solo el emulador; nunca la máquina real de nadie.
- Un cambio de comportamiento lleva su prueba, y una prueba nueva se comprueba rompiendo a propósito el código que protege (que falle de verdad).

## Releases

El paquete sale de los archivos del commit, nunca de una carpeta con ediciones del usuario. Windows: `cnc_app_<ver>_windows.zip` con `cnc_app.exe`, `LICENSE`, `QUICK_START_*`, `README.md`, `THIRD_PARTY_NOTICES.md`, `cnc_app.toml`, `herramientas.json`, `maquinas.json`, `receta_ejemplo.toml` y `docs/` (manuales PDF, `TECHNICAL_*`, capturas), todo dentro de la carpeta `cnc_app_<ver>_windows/`. Linux: `cnc_app_<ver>_linux.tar.gz` igual, con el binario `cnc_app` en modo 0755. Notas bilingües en `RELEASE_NOTES_<ver>.md` con los SHA-256. La release se crea en la web de GitHub con *Create new tag on publish*, y después se comprueba con `gh api repos/deltazerorsan/cnc_app/releases/tags/<tag>` (campo `digest` de cada archivo).

## Limitaciones conocidas

- Los mensajes de `cnc/folders.py` hablan de Windows y de letras de unidad; en Linux es solo un detalle de texto.
- Fresas de bola y en V: se guardan como datos, pero solo la plana (end mill) genera trayectorias.
