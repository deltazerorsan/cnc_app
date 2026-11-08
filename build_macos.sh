#!/usr/bin/env bash
# Genera la aplicacion de macOS (cnc_app.app) y un .zip listo para repartir, para el Mac en el que se ejecuta:
#   Mac con Intel          ->  dist_macos/intel/cnc_app_<version>_macos_intel.zip
#   Mac con Apple Silicon  ->  dist_macos/apple-silicon/cnc_app_<version>_macos_apple-silicon.zip
# PyInstaller no compila entre arquitecturas: hay que lanzar el script una vez en cada tipo de Mac.
#
# El script NO instala nada en el Mac: Python (el autonomo de uv), uv, las dependencias, la cache y los temporales viven en
# la carpeta .macos-build/<arquitectura> junto a este script. Para llevarse todo a un disco externo basta con dejar ahi esta
# carpeta. Lo unico que necesita del sistema son las "Command Line Tools" de Apple (lipo e install_name_tool, que PyInstaller
# usa en macOS): si faltan, el script lo dice y para (instalalas con `xcode-select --install`).
#
# Uso (desde la carpeta del programa):   bash build_macos.sh            compila
#                                        bash build_macos.sh --tests    ademas pasa las pruebas antes (tardan mucho)
# Hace falta conexion a internet la primera vez (uv, Python y las librerias) y unos 3 GB libres.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"

RUN_TESTS=0
[ "${1:-}" = "--tests" ] && RUN_TESTS=1

if [ "$(uname -s)" != "Darwin" ]; then
    echo "Este script es para macOS (en Windows: build_windows_onefile.bat; en Linux: build_linux.sh)." >&2
    exit 1
fi
case "$(uname -m)" in
    x86_64) ARCH="intel";         UV_TRIPLE="x86_64-apple-darwin" ;;
    arm64)  ARCH="apple-silicon"; UV_TRIPLE="aarch64-apple-darwin" ;;
    *) echo "Arquitectura no reconocida: $(uname -m)" >&2; exit 1 ;;
esac
if [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" = "1" ]; then
    echo "AVISO: esta Terminal funciona con Rosetta; saldra la version para Intel, no la de Apple Silicon." >&2
fi
echo "Mac detectado: $ARCH ($(sw_vers -productName) $(sw_vers -productVersion))"

echo "[1/6] Comprobando las herramientas de Apple (Command Line Tools)..."
# Sin ellas, lipo e install_name_tool (que estan en /usr/bin) abririan una ventana pidiendo instalarlas: se comprueba antes
# sin abrirla. Si hay una copia de las herramientas en el disco (carpeta CommandLineTools junto a esta), se usa esa.
if ! xcode-select -p >/dev/null 2>&1 && [ -x "$ROOT/../CommandLineTools/usr/bin/lipo" ]; then
    export DEVELOPER_DIR="$(cd "$ROOT/../CommandLineTools" && pwd)"
    echo "  usando las herramientas de $DEVELOPER_DIR"
fi
if ! xcode-select -p >/dev/null 2>&1 || ! xcrun --find lipo >/dev/null 2>&1 || ! xcrun --find install_name_tool >/dev/null 2>&1; then
    cat >&2 <<'EOF'
Faltan las herramientas de linea de comandos de Apple (Command Line Tools): PyInstaller necesita lipo e install_name_tool.
Instalalas una sola vez con:

    xcode-select --install

(sale una ventana de Apple, pide la contrasena del Mac y tarda unos minutos) y vuelve a lanzar este script.
Si ya tienes una copia de esas herramientas en otra carpeta:  export DEVELOPER_DIR=/ruta/CommandLineTools
EOF
    exit 1
fi

echo "[2/6] Preparando uv y Python en .macos-build/$ARCH (no se toca el resto del Mac)..."
WORK="$ROOT/.macos-build/$ARCH"
mkdir -p "$WORK/uv" "$WORK/cache" "$WORK/python" "$WORK/python-bin" "$WORK/tmp" "$WORK/pyinstaller"
export UV_CACHE_DIR="$WORK/cache/uv"
export UV_PYTHON_INSTALL_DIR="$WORK/python"
export UV_PYTHON_BIN_DIR="$WORK/python-bin"
export UV_PYTHON_PREFERENCE="only-managed"
export XDG_CACHE_HOME="$WORK/cache"
export PYINSTALLER_CONFIG_DIR="$WORK/pyinstaller"
export TMPDIR="$WORK/tmp"
export PYTHONNOUSERSITE=1
UV="$WORK/uv/uv"
if [ ! -x "$UV" ]; then
    curl -fL --retry 3 -o "$WORK/uv/uv.tar.gz" "https://github.com/astral-sh/uv/releases/latest/download/uv-$UV_TRIPLE.tar.gz"
    tar xzf "$WORK/uv/uv.tar.gz" -C "$WORK/uv" --strip-components=1
    rm -f "$WORK/uv/uv.tar.gz"
fi
VENV="$WORK/venv"
# Python 3.12: hay ruedas de shapely, numpy y PySide6 para las dos arquitecturas.
[ -x "$VENV/bin/python" ] || "$UV" venv --python 3.12 "$VENV"
PY="$VENV/bin/python"

echo "[3/6] Instalando las dependencias..."
# PySide6 reciente exige macOS 13 o 14; uv elige la ultima que admite el macOS de este Mac (en Monterey, la 6.9).
if [ "$RUN_TESTS" -eq 1 ]; then
    "$UV" pip install --quiet --python "$PY" -r requirements-dev.txt
else
    "$UV" pip install --quiet --python "$PY" -r requirements.txt "pyinstaller>=6.0"
fi
"$PY" -c "import sys, PySide6, shapely, PyInstaller; print('  Python', sys.version.split()[0], '| PySide6', PySide6.__version__, '| shapely', shapely.__version__, '| PyInstaller', PyInstaller.__version__)"

if [ "$RUN_TESTS" -eq 1 ]; then
    echo "[4/6] Pasando las pruebas (sin ventana)..."
    QT_QPA_PLATFORM=offscreen "$PY" -m pytest -q -x
else
    echo "[4/6] Pruebas omitidas (usa bash build_macos.sh --tests para pasarlas)."
fi

echo "[5/6] Compilando con PyInstaller (tarda unos minutos)..."
OUT="$ROOT/dist_macos/$ARCH"
rm -rf "$OUT"
mkdir -p "$OUT"
"$PY" -m PyInstaller --noconfirm --clean --distpath "$OUT/dist" --workpath "$OUT/work" cnc_gui.spec
if [ ! -x "$OUT/dist/cnc_app.app/Contents/MacOS/cnc_app" ]; then
    echo "PyInstaller no ha dejado $OUT/dist/cnc_app.app." >&2
    exit 1
fi
rm -rf "$OUT/work"

echo "[6/6] Empaquetando y comprobando el programa compilado (sin ventana)..."
VERSION="$("$PY" -c 'from cnc import __version__; print(__version__)')"
NAME="cnc_app_${VERSION}_macos_${ARCH}"
PKG="$OUT/$NAME"
mkdir -p "$PKG/docs"
ditto "$OUT/dist/cnc_app.app" "$PKG/cnc_app.app"           # ditto conserva enlaces, permisos y la firma
# Los archivos que se editan a mano van junto al .app (no dentro), que es donde los busca el programa.
cp LICENSE QUICK_START_EN.md QUICK_START_ES.md README.md THIRD_PARTY_NOTICES.md \
   herramientas.json maquinas.json cnc_app.toml receta_ejemplo.toml "$PKG/"
cp docs/Manual_cnc_app_EN.pdf docs/Manual_cnc_app_ES.pdf docs/TECHNICAL_EN.md docs/TECHNICAL_ES.md \
   docs/captura_ES.png docs/screenshot_EN.png "$PKG/docs/"
cat > "$PKG/PRIMERA_VEZ_macOS.txt" <<'EOF'
cnc_app para macOS / cnc_app for macOS
======================================

ES  El programa no esta firmado con un certificado de Apple, asi que macOS lo bloquea la primera vez que se descarga.
    Abre la Terminal y quita la marca de descarga de toda esta carpeta (cambia la ruta por la tuya; basta con arrastrar
    la carpeta a la Terminal despues de escribir "xattr -dr com.apple.quarantine "):

        xattr -dr com.apple.quarantine /ruta/a/esta/carpeta

    Despues se abre con doble clic en cnc_app.app. Haz esto antes de abrirlo por primera vez: con la marca puesta, macOS
    puede ejecutar el programa desde una copia temporal y entonces no encuentra herramientas.json ni maquinas.json,
    que deben estar junto a cnc_app.app.
    Alternativa sin Terminal: clic derecho en cnc_app.app -> Abrir (macOS 14 o anterior), o intentar abrirlo y despues
    Ajustes del Sistema -> Privacidad y seguridad -> "Abrir igualmente" (macOS 15 o posterior).

EN  The program is not signed with an Apple certificate, so macOS blocks it the first time after a download.
    Open Terminal and remove the download flag from this whole folder (replace the path with yours):

        xattr -dr com.apple.quarantine /path/to/this/folder

    Then double-click cnc_app.app. Do this before the first launch: with the flag set, macOS may run the program from a
    temporary copy, and then it cannot find herramientas.json and maquinas.json, which must sit next to cnc_app.app.
    Without Terminal: right-click cnc_app.app -> Open (macOS 14 or earlier), or try to open it and then
    System Settings -> Privacy & Security -> "Open Anyway" (macOS 15 or later).
EOF
check_out="$(mktemp)"
trap 'rm -f "$check_out"' EXIT
if QT_QPA_PLATFORM=offscreen "$PKG/cnc_app.app/Contents/MacOS/cnc_app" --autotest ejemplo_placa.svg "$check_out" \
        && tail -n 1 "$check_out" | grep -q "RESULTADO: CORRECTO"; then
    cat "$check_out"
else
    echo "La comprobacion ha fallado:" >&2
    cat "$check_out" >&2 || true
    exit 1
fi
if grep -q "carpeta del programa: .*\.app" "$check_out"; then
    echo "La carpeta del programa apunta al interior del .app: los archivos editables no se encontrarian." >&2
    exit 1
fi
if ! grep -q "fichas de maquina: [^(]" "$check_out" || ! grep -q "fichas de fresas: [^(]" "$check_out"; then
    echo "El programa compilado no ha encontrado las fichas junto al .app." >&2
    exit 1
fi
codesign --verify --deep --strict "$PKG/cnc_app.app" && echo "Firma del .app: correcta (ad hoc, sin certificado de Apple)"

(cd "$OUT" && ditto -c -k --keepParent "$NAME" "$NAME.zip")
echo
echo "Listo: $OUT/$NAME.zip ($(du -h "$OUT/$NAME.zip" | cut -f1))"
echo "SHA-256: $(shasum -a 256 "$OUT/$NAME.zip" | cut -d' ' -f1)"
echo "Para probarlo aqui:  open \"$PKG/cnc_app.app\""
