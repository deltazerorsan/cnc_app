"""Genera el G-code de una placa de ejemplo con la ventana y escribe (en JSON) lo que se ve: las primeras lineas del
programa, el resumen y los avisos. Se lanza en un subproceso con CNC_APP_LANG=es|en: `python tests/i18n_generate.py CARPETA`.
Lo usa tests/test_i18n.py para ver que el G-code y los mensajes salen en el idioma elegido."""
import hashlib
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication, QMessageBox              # noqa: E402

from cnc.gui import theme                                            # noqa: E402
from cnc.gui.main_window import MainWindow                           # noqa: E402
from cnc.i18n import current_language                                # noqa: E402
from cnc.model import Tool                                           # noqa: E402
from cnc.project import Project                                      # noqa: E402

PLACA = ROOT / "tests" / "data" / "placa_doble.svg"


def main(folder: Path) -> int:
    app = QApplication.instance() or QApplication(["generate"])
    theme.apply_theme(app)
    for name in ("warning", "critical", "information", "question"):
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Yes))
    tools = {"em4": Tool(diameter=4, name="End mill 4 mm", feed_xy=1200, feed_z=300, stepdown=6.5, stepover=0.6)}
    window = MainWindow(Project(tools), [], use_settings=False)
    window.open_svg(PLACA, apply_default_recipe=False)
    window.job.tool.setCurrentIndex(window.job.tool.findData("em4"))
    window.job.thickness.setText("12")
    window.job.thickness.valueChanged.emit()
    window.wait_for_plan()
    paths = window.generate(base=folder / "placa.nc", confirm=False)
    text = paths[0].read_text(encoding="utf-8") if paths else ""
    body = "\n".join(line for line in text.splitlines() if not line.startswith("("))      # el G-code sin sus comentarios
    print(json.dumps({"language": current_language(), "paths": [p.name for p in paths], "gcode_head": text.splitlines()[:14],
                      "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(), "body_lines": len(body.splitlines()),
                      "summary": window.summary.text(), "status": window.statusBar().currentMessage()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
