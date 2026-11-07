#!/usr/bin/env bash
# Genera dist_linux/cnc_app: UN SOLO archivo autocontenido (se descomprime en /tmp en cada arranque), igual que el .exe de Windows.
# Los archivos que se editan a mano (herramientas.json, maquinas.json, cnc_app.toml, receta_ejemplo.toml) NO van dentro: se copian
# junto al programa, que es donde los busca. Si ya existen ahi no se pisan (puede que los hayas editado).
#
# Uso (desde la carpeta del programa):   bash build_linux.sh            compila
#                                        bash build_linux.sh --tests    ademas pasa las pruebas antes de compilar
# Hace falta Python 3.11+ con venv (en Ubuntu/Mint: sudo apt install python3-venv python3-pip). No hace falta ser root:
# todo se instala en la carpeta .venv-linux. El binario sirve para la misma version de Ubuntu/Mint con la que se compila
# o una posterior (usa la glibc del sistema): para llegar a mas distribuciones, compilar en la mas antigua.
set -euo pipefail
cd "$(dirname "$0")"

RUN_TESTS=0
[ "${1:-}" = "--tests" ] && RUN_TESTS=1

# Librerias del sistema que Qt necesita al ARRANCAR (no al compilar). En Mint/Ubuntu de escritorio casi todas ya estan.
SYSTEM_LIBS=(libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-shape0
             libxcb-xinerama0 libxcb-xkb1 libxkbcommon-x11-0 libgl1 libegl1 libfontconfig1 libdbus-1-3)

echo "[1/5] Comprobando Python y las librerias del sistema..."
if ! command -v python3 >/dev/null || ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    echo "Hace falta Python 3.11 o superior (python3 --version)." >&2
    exit 1
fi
if ! python3 -c 'import venv, ensurepip' 2>/dev/null; then
    echo "Falta el modulo venv: sudo apt install python3-venv python3-pip" >&2
    exit 1
fi
if command -v dpkg >/dev/null; then
    missing=()
    for lib in "${SYSTEM_LIBS[@]}"; do
        dpkg -s "$lib" >/dev/null 2>&1 || missing+=("$lib")
    done
    if [ "${#missing[@]}" -gt 0 ]; then
        echo "Faltan librerias que el programa necesita para abrir su ventana: ${missing[*]}"
        if [ -t 0 ] && command -v sudo >/dev/null; then
            read -r -p "¿Instalarlas ahora con sudo apt? [s/N] " answer
            if [ "$answer" = "s" ] || [ "$answer" = "S" ]; then
                sudo apt install -y "${missing[@]}"
            fi
        else
            echo "Instalalas con: sudo apt install ${missing[*]}"
        fi
    fi
fi

echo "[2/5] Preparando el entorno de Python (.venv-linux) e instalando dependencias..."
[ -d .venv-linux ] || python3 -m venv .venv-linux
# shellcheck disable=SC1091
source .venv-linux/bin/activate
python -m pip install --quiet --upgrade pip
if [ "$RUN_TESTS" -eq 1 ]; then
    python -m pip install --quiet -r requirements-dev.txt
else
    python -m pip install --quiet -r requirements.txt "pyinstaller>=6.0"
fi

if [ "$RUN_TESTS" -eq 1 ]; then
    echo "[3/5] Pasando las pruebas (sin ventana)..."
    QT_QPA_PLATFORM=offscreen python -m pytest -q -x
else
    echo "[3/5] Pruebas omitidas (usa bash build_linux.sh --tests para pasarlas)."
fi

echo "[4/5] Compilando con PyInstaller en un solo archivo (tarda unos minutos)..."
python -m PyInstaller --noconfirm --clean --distpath dist_linux --workpath build_linux cnc_gui.spec
if [ ! -x dist_linux/cnc_app ]; then
    echo "PyInstaller no ha dejado dist_linux/cnc_app." >&2
    exit 1
fi
for f in herramientas.json maquinas.json cnc_app.toml receta_ejemplo.toml; do
    [ -e "dist_linux/$f" ] || cp "$f" dist_linux/
done
rm -rf build_linux

echo "[5/5] Comprobando el programa compilado (sin ventana)..."
if [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then export QT_QPA_PLATFORM=offscreen; fi
check_out="$(mktemp)"
trap 'rm -f "$check_out"' EXIT
if dist_linux/cnc_app --autotest ejemplo_placa.svg "$check_out" && tail -n 1 "$check_out" | grep -q "RESULTADO: CORRECTO"; then
    cat "$check_out"
else
    echo "La comprobacion ha fallado:" >&2
    cat "$check_out" >&2 || true
    exit 1
fi

echo
echo "Listo: $PWD/dist_linux/cnc_app ($(du -h dist_linux/cnc_app | cut -f1))"
echo "Para abrirlo:  ./dist_linux/cnc_app"
