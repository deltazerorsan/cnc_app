"""Pregunta el nombre y la operacion de un grupo nuevo de vectores."""
from __future__ import annotations

from typing import Sequence

from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout

from ..oprows import ACTIONS
from ..i18n import _

# lo que se ofrece al crear un grupo: (operacion de la tabla, texto, nombre que se propone)
CHOICES = (("vaciado", _("Vaciado (pozo)"), _("Pozo")), ("corte", _("Corte"), _("Corte")), ("taladro", _("Taladro"), _("Taladros")),
           ("ignorar", _("Ignorar"), _("Ignorados")))
assert {c[0] for c in CHOICES} == set(ACTIONS)


def free_name(base: str, taken: Sequence[str]) -> str:
    """`base`, o `base 2`, `base 3`... si ya hay un grupo con ese nombre."""
    if base not in taken:
        return base
    n = 2
    while f"{base} {n}" in taken:
        n += 1
    return f"{base} {n}"


class GroupDialog(QDialog):
    def __init__(self, count: int, taken: Sequence[str] = (), parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Grupo nuevo"))
        self._taken = list(taken)
        self._touched = False
        self.kind = QComboBox()
        for action, label, _name in CHOICES:
            self.kind.addItem(label, action)
        self.name_edit = QLineEdit()
        self.name_edit.setText(free_name(CHOICES[0][2], self._taken))
        self.name_edit.textEdited.connect(self._edited)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        form = QFormLayout()
        form.addRow(_("Qué es"), self.kind)
        form.addRow(_("Nombre"), self.name_edit)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(_("{count} forma(s) pasan a una fila propia de la tabla, donde se pone su profundidad y sus "
                               "avances.", count=count)))
        lay.addLayout(form)
        lay.addWidget(self.buttons)

    def _edited(self, _text: str) -> None:
        self._touched = True

    def _kind_changed(self, index: int) -> None:
        if not self._touched:
            self.name_edit.setText(free_name(CHOICES[index][2], self._taken))

    def action(self) -> str:
        return self.kind.currentData()

    def name(self) -> str:
        return self.name_edit.text().strip()
