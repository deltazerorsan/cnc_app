"""Arranque de la ventana: `python cnc_gui.py [dibujo.svg | trabajo.cncjob]`."""
from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from .. import i18n
from ..i18n import _
from . import theme
from .main_window import JOB_SUFFIX, MainWindow
from .single_instance import InstanceServer, forward_to_running, instance_name


LOG_NAME = "cnc_app_error.log"


def report_unexpected(exc_type, exc, tb, log_dir=None) -> str:
    """Un error del programa (no del usuario): sin consola nadie lo veria, asi que se apunta en un archivo junto al
    programa y se enseña en un cuadro. Devuelve el texto del error."""
    from ..pipeline import app_dir
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    try:
        with open(Path(log_dir or app_dir()) / LOG_NAME, "a", encoding="utf-8") as f:
            f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')}\n{text}\n")
    except OSError:
        pass
    try:
        QMessageBox.critical(None, _("Error inesperado de la aplicación"),
                             _("Ha ocurrido un error que no debería pasar. El trabajo abierto sigue en la ventana; "
                               "guarda el trabajo y envíame el archivo {log_name}.", log_name=LOG_NAME)
                             + "\n\n" + text[-1200:])
    except Exception:
        pass
    return text


def autotest(svg: str, out: str) -> int:
    """Comprobacion del programa instalado (sin ventana visible ni consola): abre el dibujo, calcula el primer
    tablero con la primera ficha de fresa, la primera ficha de maquina y 6 mm de grosor si no hay otros ajustes, y
    escribe el resultado en `out`."""
    from .. import __version__
    from ..pipeline import app_dir
    window = MainWindow(use_settings=False)
    lines = [f"cnc_app {__version__}", f"carpeta del programa: {app_dir()}",
             f"fichas de fresas: {', '.join(window.project.tools) or '(ninguna)'}",
             f"fichas de maquina: {', '.join(window.project.machines) or '(ninguna)'}"]
    lines += [f"aviso de arranque: {m.text}" for m in window.startup_notes]
    ok = window.open_svg(svg)
    lines.append(f"dibujo: {'abierto' if ok else 'NO SE PUDO ABRIR'} {svg}")
    if ok:
        g = window.project.general
        if not g.tool and window.project.tools:
            g.tool = next(iter(window.project.tools))
        if g.thickness is None:
            g.thickness = 6.0
        if not (g.machine or "").strip() and window.project.machines:          # con varias fichas, el programa no supone
            g.machine = next(iter(window.project.machines))                    # ninguna: para comprobar, la primera
        window.job.refresh()
        plan = window.plan_now()
        lines.append(f"fresa {g.tool or '(ninguna)'}, maquina {g.machine or '(ninguna)'}, grosor {g.thickness:g} mm, "
                     f"tableros: {len(window.project.units())}, filas de colores: {len(window.project.rows)}")
        if plan.ok:
            lines.append(f"plan: correcto, {len(plan.text.splitlines())} lineas de G-code, Z minima {plan.result.min_z:.2f} mm")
        else:
            lines.append(f"plan: ERROR: {plan.error}")
        ok = plan.ok
        if plan.ok and plan.stock:                              # el calculo de sujecion usa shapely a fondo: que se vea si falla
            try:
                from ..holddown import analyse_plan
                points = analyse_plan(plan, window.last_previews, window.project.hold).suggest()
                lines.append(f"sujecion: {len(points)} punto(s) propuesto(s)")
            except Exception as e:                              # noqa: BLE001 - se cuenta, no se oculta
                lines.append(f"sujecion: ERROR: {type(e).__name__}: {e}")
                ok = False
    lines.append("RESULTADO: " + ("CORRECTO" if ok else "CON ERRORES"))
    Path(out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    window.close()
    return 0 if ok else 1


def qt_translation_dirs() -> list:
    """Donde buscar los catalogos de Qt: la ruta que dice Qt y, por si el programa va empaquetado, la carpeta de PySide6."""
    from PySide6.QtCore import QLibraryInfo
    dirs = [QLibraryInfo.path(QLibraryInfo.TranslationsPath)]
    try:
        import PySide6
        base = Path(getattr(sys, "_MEIPASS", Path(PySide6.__file__).parent.parent))
        dirs += [str(base / "PySide6" / "translations"), str(base / "PySide6" / "Qt" / "translations")]
    except Exception:                                          # noqa: BLE001 - es solo una ayuda para encontrarlos
        pass
    return dirs


def install_qt_translation(app: QApplication):
    """Los textos de Qt (botones Sí / No / Cancelar, menu del texto...) en el idioma del programa: Qt los trae en ingles
    y trae el catalogo español. Devuelve el traductor (hay que conservarlo) o None si no hace falta o no hay catalogo.
    Nunca impide arrancar: sin esos textos el programa funciona igual, con los botones de Qt en ingles."""
    if i18n.current_language() != "es":
        return None
    try:
        from PySide6.QtCore import QTranslator
        translator = QTranslator()
        for folder in qt_translation_dirs():
            if translator.load("qtbase_es", folder):
                app.installTranslator(translator)
                return translator
    except Exception:                                          # noqa: BLE001
        pass
    return None


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    app = QApplication.instance() or QApplication(["cnc_app"])
    app.setApplicationName("cnc_app")
    app._qt_translator = install_qt_translation(app)           # type: ignore[attr-defined]
    theme.apply_theme(app)
    sys.excepthook = report_unexpected
    if len(argv) == 3 and argv[0] == "--autotest":
        return autotest(argv[1], argv[2])
    name = instance_name()
    if forward_to_running(argv, name):                      # ya hay una ventana: se le pasa el archivo y no se abre otra
        return 0
    server = InstanceServer(name)
    if not server.start() and forward_to_running(argv, name):   # otra ventana acaba de arrancar a la vez
        return 0
    window = MainWindow()
    server.requested.connect(window.receive_external)
    window.show()
    window.fit_to_screen()                                   # un portatil de 1366 x 768 no tiene sitio para 1400 x 880
    if argv:
        path = Path(argv[0])
        if path.suffix.lower() == JOB_SUFFIX:
            window.open_job(path)
        else:
            window.open_svg(path)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
