"""La lista de comprobacion antes de generar (el contenido lo arma `cnc.checklist`).

Arriba, los datos que calcula el programa; debajo, una casilla por cada cosa que solo puede saber la persona (y las alarmas
del trabajo, en rojo). Generar no se activa hasta que estan todas marcadas, y no hay un «marcar todo» a proposito: la gracia
es mirar cada una.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFrame, QGridLayout, QLabel, QScrollArea, QVBoxLayout,
                               QWidget)

from ..checklist import Checklist
from . import theme
from ..i18n import _


class ChecklistDialog(QDialog):
    def __init__(self, checklist: Checklist, parent=None):
        super().__init__(parent)
        self.checklist = checklist
        self.setWindowTitle(_("Lista de comprobación antes de generar"))
        self.setMinimumWidth(680)
        lay = QVBoxLayout(self)
        intro = QLabel(_("Este G-code da por hecho lo que sigue. Mira los datos y marca cada casilla cuando lo hayas "
                       "comprobado: Generar se activa con todas marcadas."))
        intro.setWordWrap(True)
        lay.addWidget(intro)

        data = QWidget()
        grid = QGridLayout(data)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setColumnStretch(1, 1)
        row = 0
        self.values: list[QLabel] = []
        for section in checklist.sections:
            if section.title:
                title = QLabel(section.title)
                title.setStyleSheet("font-weight: bold;")
                grid.addWidget(title, row, 0, 1, 2)
                row += 1
            for name, value in section.rows:
                key = QLabel(name)
                key.setObjectName("muted")
                text = QLabel(value)
                text.setWordWrap(True)
                grid.addWidget(key, row, 0, Qt.AlignTop)
                grid.addWidget(text, row, 1)
                self.values.append(text)
                row += 1
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(data)
        scroll.setMaximumHeight(260)
        lay.addWidget(scroll)

        self.boxes: list[QCheckBox] = []
        for check in checklist.checks:
            box = QCheckBox(("⚠ " if check.alarm else "") + check.text)
            if check.alarm:
                box.setStyleSheet(f"color: {theme.ERROR}; font-weight: bold;")
            box.toggled.connect(self._update)
            lay.addWidget(box)
            self.boxes.append(box)
        self.count = QLabel("")
        self.count.setObjectName("muted")
        lay.addWidget(self.count)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText(_("Generar"))
        self.buttons.button(QDialogButtonBox.Cancel).setText(_("Cancelar"))
        self.buttons.button(QDialogButtonBox.Ok).setAutoDefault(False)     # si no, Qt lo hace el predeterminado al mostrarla
        self.buttons.button(QDialogButtonBox.Cancel).setDefault(True)      # un Intro despistado cancela, no genera
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self._update()

    @property
    def all_checked(self) -> bool:
        return all(box.isChecked() for box in self.boxes)

    def _update(self, *_args) -> None:
        done = sum(1 for box in self.boxes if box.isChecked())
        self.count.setText(_("Marcadas {done} de {count}", done=done, count=len(self.boxes)))
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(self.all_checked)
