"""Recorre la ventana y escribe (en JSON) todos los textos visibles. Se lanza en un subproceso con CNC_APP_LANG=pseudo|en:
`python tests/i18n_probe.py`. Lo usa tests/test_i18n.py para comprobar que no queda ningun texto sin traducir."""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QAction                                    # noqa: E402
from PySide6.QtWidgets import (QAbstractButton, QApplication, QComboBox, QDialog, QFileDialog, QGroupBox, QLabel,   # noqa: E402
                               QLineEdit, QMenu, QMessageBox, QTabBar, QTableWidget, QWidget)

from cnc.gui import theme                                            # noqa: E402
from cnc.gui.main_window import MainWindow                           # noqa: E402


def widget_texts(root: QWidget) -> list:
    out = []

    def add(where, text):
        if text and text.strip():
            out.append((where, text))

    for w in [root, *root.findChildren(QWidget)]:
        name = f"{type(w).__name__}:{w.objectName() or ''}"
        add(name + ".tooltip", w.toolTip())
        if isinstance(w, (QAbstractButton, QLabel)):
            add(name + ".text", w.text())
        if isinstance(w, QGroupBox):
            add(name + ".title", w.title())
        if isinstance(w, QLineEdit):
            add(name + ".placeholder", w.placeholderText())
        if isinstance(w, QComboBox):
            for i in range(w.count()):
                add(name + f".item{i}", w.itemText(i))
        if isinstance(w, QTabBar):
            for i in range(w.count()):
                add(name + f".tab{i}", w.tabText(i))
        if isinstance(w, QTableWidget):
            for c in range(w.columnCount()):
                item = w.horizontalHeaderItem(c)
                add(name + f".col{c}", item.text() if item else "")
        if hasattr(w, "windowTitle") and w.isWindow():
            add(name + ".windowTitle", w.windowTitle())
    for action in root.findChildren(QAction):
        add("QAction.text", action.text())
        add("QAction.tooltip", action.toolTip())
        add("QAction.statustip", action.statusTip())
    for menu in root.findChildren(QMenu):
        add("QMenu.title", menu.title())
    return out


def dialog_texts(window: MainWindow) -> list:
    """Abre (sin dejarlos esperando) los dialogos de la ventana y recoge sus textos: cada `exec()` se cambia por una
    funcion que apunta lo que se veria y cierra el dialogo como cancelado."""
    out: list = []

    def fake_exec(self):
        out.extend((f"{type(self).__name__}/{where}", text) for where, text in widget_texts(self))
        if isinstance(self, QMessageBox):
            out.append((f"{type(self).__name__}/title", self.windowTitle()))
        return int(QDialog.Rejected)

    def fake_box(parent, title, text, *args, **kwargs):
        out.append(("QMessageBox/title", title))
        out.append(("QMessageBox/text", text))
        return QMessageBox.No

    QDialog.exec = fake_exec
    QMessageBox.exec = fake_exec
    for name in ("question", "information", "warning", "critical"):
        setattr(QMessageBox, name, staticmethod(fake_box))
    for name in ("getOpenFileName", "getSaveFileName"):
        setattr(QFileDialog, name, staticmethod(lambda *a, **k: ("", "")))
    QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: "")

    window.selection = set(window.project.selectable())            # los de dibujo piden algo elegido
    calls = [window.open_tools_editor, window.open_machines_editor, window.open_folders_dialog, window._ask_group]
    for name in ("draw_rectangle", "draw_circle", "draw_ellipse", "draw_polygon", "draw_slot", "draw_move", "draw_place",
                 "draw_rotate", "draw_scale", "draw_mirror", "draw_array", "draw_polar", "draw_offset"):
        calls.append(getattr(window, name))
    for call in calls:
        try:
            call()
        except Exception as e:                                       # un dialogo que no se abre se ve en el informe
            out.append(("ERROR " + getattr(call, "__name__", "?"), f"{type(e).__name__}: {e}"))
    from cnc.gui.machines_dialog import LogImportDialog
    from cnc.machinebook import MachineEditor
    dialog = LogImportDialog(MachineEditor(), parent=window)
    out.extend((f"LogImportDialog/{where}", text) for where, text in widget_texts(dialog))
    from cnc.checklist import Check, Checklist, Section
    from cnc.gui.checklist_dialog import ChecklistDialog
    sample = Checklist([Section("", [("a", "b")])], [Check("c", alarm=True)])
    dialog = ChecklistDialog(sample, window)
    out.extend((f"ChecklistDialog/{where}", text) for where, text in widget_texts(dialog))
    return out


def main() -> int:
    app = QApplication.instance() or QApplication(["probe"])
    theme.apply_theme(app)
    window = MainWindow(use_settings=False)
    window.open_svg(str(ROOT / "ejemplo_placa.svg"))
    window.show()
    app.processEvents()
    texts = widget_texts(window)
    texts.extend(dialog_texts(window))
    texts.append(("statusbar", window.statusBar().currentMessage()))
    texts.append(("summary", window.summary.text()))
    from cnc.i18n import current_language
    print(json.dumps({"language": current_language(), "texts": texts}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
