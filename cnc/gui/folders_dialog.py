"""Archivo → Carpetas…: las dos carpetas fijas (trabajos y G-code).

Cada ruta se comprueba mientras se escribe con las mismas reglas que usa la ventana al abrir y guardar
(`cnc.folders.problem`), y Aceptar queda apagado mientras alguna no valga. Vacia = sin configurar.
"""
from __future__ import annotations

from typing import Mapping

from PySide6.QtCore import QDir
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QVBoxLayout)

from .. import folders as fo
from . import theme
from ..i18n import _


class FoldersDialog(QDialog):
    def __init__(self, current: Mapping[str, str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Carpetas de trabajos y G-code"))
        self.setMinimumWidth(620)
        self.edits: dict = {}
        self.notes: dict = {}
        self._browse: dict = {}
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        for row, kind in enumerate(fo.KINDS):
            edit = QLineEdit(fo.clean(current.get(kind, "")))
            edit.setPlaceholderText(_("Sin configurar: la carpeta del dibujo"))
            edit.textChanged.connect(self._check)
            browse = QPushButton(_("Examinar…"))
            browse.clicked.connect(lambda _checked=False, k=kind: self._choose(k))
            note = QLabel("")
            note.setWordWrap(True)
            grid.addWidget(QLabel(fo.LABELS[kind]), row * 3, 0)
            grid.addWidget(edit, row * 3, 1)
            grid.addWidget(browse, row * 3, 2)
            help_ = QLabel(fo.HELP[kind])
            help_.setObjectName("muted")
            grid.addWidget(help_, row * 3 + 1, 1, 1, 2)
            grid.addWidget(note, row * 3 + 2, 1, 1, 2)
            self.edits[kind], self.notes[kind], self._browse[kind] = edit, note, browse
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        intro = QLabel(_("Las ventanas de abrir y guardar empiezan en estas carpetas. Escribe la ruta completa (por ejemplo "
                       "D:\\Trabajos) o búscala con Examinar. Una carpeta tiene que existir y poder escribirse en ella; "
                       "si no, la aplicación no la cambia por otra sin preguntar."))
        intro.setWordWrap(True)
        lay.addWidget(intro)
        lay.addLayout(grid)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.buttons)
        lay.addLayout(row)
        self._check()

    def _choose(self, kind: str) -> None:
        start = fo.clean(self.edits[kind].text())
        path = QFileDialog.getExistingDirectory(self, _("Carpeta de {LABELS}", LABELS=fo.LABELS[kind]), start)
        if path:
            self.edits[kind].setText(QDir.toNativeSeparators(path))      # D:\Trabajos en Windows, no D:/Trabajos

    def _check(self, *_args) -> None:
        all_good = True
        for kind in fo.KINDS:
            text = fo.clean(self.edits[kind].text())
            why = fo.problem(text)
            note = self.notes[kind]
            if why:
                all_good = False
                note.setText(f"«{text}» {why}")
                note.setStyleSheet(f"color: {theme.ERROR};")
            else:
                note.setText(_("Vale.") if text else "")
                note.setStyleSheet(f"color: {theme.MUTED};")
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(all_good)

    def values(self) -> dict:
        """kind → ruta limpia ("" = sin configurar)."""
        return {kind: fo.clean(self.edits[kind].text()) for kind in fo.KINDS}
