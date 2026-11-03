"""La tira de puentes, sobre el lienzo mientras se usa la herramienta Puente: los números del puente elegido (posición, ancho y alto)
para verlos y escribirlos, y los botones de quitarlo o de devolver la forma a los puentes automáticos."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from .props_bar import FIELD_WIDTH, fmt_mm
from .widgets import OptionalNumber
from ..i18n import _

HINT = _("Clic en un contorno para poner un puente (los nuevos miden {width:g} × {height:g} mm, como dicen los ajustes del "
         "trabajo). Clic en un puente para elegirlo: arrástralo para deslizarlo o tira de un extremo para cambiar su ancho.")


class BridgeBar(QWidget):
    applied = Signal(float, float, float)          # posicion (mm desde el primer vertice), ancho y alto del puente elegido
    removeRequested = Signal()
    autoRequested = Signal()                       # la forma del puente elegido vuelve a los puentes automaticos
    problem = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self._shown = ["", "", ""]
        self._hint = HINT.format(width=6.0, height=2.0)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 0, 8, 0)
        row.setSpacing(4)
        self.title = QLabel(_("Sin puente"))
        self.title.setMinimumWidth(84)
        self.title.setStyleSheet("font-weight: 600;")
        row.addWidget(self.title)
        tips = (_("Dónde está el centro del puente: mm a lo largo del contorno desde su primer vértice."),
                _("Ancho del puente que queda (mm): lo que se deja sin cortar a lo largo del contorno."),
                _("Material que queda sobre el fondo del tablero en el puente (mm)."))
        self.names, self.edits = [], []
        for label, tip in zip((_("Posición"), _("Ancho"), _("Alto")), tips):
            name = QLabel(label)
            name.setObjectName("muted")
            edit = OptionalNumber()
            edit.setFixedWidth(FIELD_WIDTH)
            edit.setAlignment(Qt.AlignRight)
            edit.setToolTip(tip)
            edit.editingFinished.connect(self._commit)
            self.names.append(name)
            self.edits.append(edit)
            row.addWidget(name)
            row.addWidget(edit)
            row.addSpacing(6)
        unit = QLabel("mm")
        unit.setObjectName("muted")
        row.addWidget(unit)
        row.addSpacing(8)
        self.remove = QPushButton(_("Quitar puente"))
        self.remove.setToolTip(_("Quita el puente elegido (Supr)."))
        self.remove.clicked.connect(self.removeRequested.emit)
        row.addWidget(self.remove)
        self.auto = QPushButton(_("Forma en automático"))
        self.auto.setToolTip(_("Deshace los puentes puestos a mano en la forma del puente elegido: vuelve a llevar los que propone la aplicación."))
        self.auto.clicked.connect(self.autoRequested.emit)
        row.addWidget(self.auto)
        row.addSpacing(8)
        self.info = QLabel("")
        self.info.setObjectName("muted")
        self.info.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)      # un texto largo no ensancha la ventana
        row.addWidget(self.info, 1)
        self.show_bridge(None)

    def set_defaults(self, width: float, height: float) -> None:
        self._hint = HINT.format(width=width, height=height)
        if self.title.text() == _("Sin puente"):
            self.info.setText(self._hint)
            self.info.setToolTip(self._hint)

    def show_bridge(self, info: Optional[dict], manual: bool = False) -> None:
        """Pone los números del puente elegido (`info`: lo que da `bridge_tools.describe`); None: no hay ninguno elegido."""
        if info is None:
            self.title.setText(_("Sin puente"))
            for edit in self.edits:
                edit.setEnabled(False)
                edit.setText("")
            self.remove.setEnabled(False)
            self.auto.setEnabled(False)
            self.info.setText(self._hint)
            self.info.setToolTip(self._hint)
            self._shown = ["", "", ""]
            return
        self.title.setText(_("Puente"))
        values = [info["position"], info["width"], info["height"]]
        self._shown = [fmt_mm(v) for v in values]
        for edit, text in zip(self.edits, self._shown):
            edit.setEnabled(True)
            if edit.text() != text and not (edit.hasFocus() and edit.isModified()):
                edit.setText(text)
        self.remove.setEnabled(True)
        self.auto.setEnabled(manual)
        text = _("Centro en X {x}, Y {y} · el contorno mide {length} mm", x=fmt_mm(info["x"]), y=fmt_mm(info["y"]),
                 length=fmt_mm(info["length"])) + ("" if manual else _(" · propuesto por la aplicación: al tocarlo pasa a mano"))
        self.info.setText(text)
        self.info.setToolTip(text)

    def _commit(self) -> None:
        if self._busy or not self.edits[0].isEnabled():
            return
        self._busy = True
        try:
            for edit in self.edits:
                edit.setModified(False)
            typed = [e.value() for e in self.edits]
            if any(v is None for v in typed):
                self.problem.emit(_("Falta una medida: escribe un número en cada casilla."))
                self._restore()
                return
            words = [e.text().strip().replace(",", ".") for e in self.edits]
            if words == self._shown:
                self._restore()
                return
            self.applied.emit(*typed)
        finally:
            self._busy = False

    def _restore(self) -> None:
        for edit, text in zip(self.edits, self._shown):
            edit.setText(text)
