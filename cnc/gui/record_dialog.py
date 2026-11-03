"""La ventana de edicion de fichas que comparten fresas y maquinas: lista a la izquierda, formulario a la derecha.

Esta clase pone todo lo comun (lista, botones, identificador, nombre, notas, errores, avisos, pie con Guardar/Cancelar y
el paso de la ficha en edicion a las casillas y de vuelta). Cada ventana concreta pone sus casillas: `_make_fields` las crea,
`_add_rows` las coloca, `_fill_fields` y `_collect_fields` las llenan y las leen.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QRegularExpression, Qt
from PySide6.QtGui import QBrush, QColor, QRegularExpressionValidator
from PySide6.QtWidgets import (QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from ..records import BACKUP_SUFFIX, Editor
from . import theme
from ..i18n import _

IDENT_RE = QRegularExpression(r"[A-Za-z][A-Za-z0-9_.\-]*")


class RecordDialog(QDialog):
    """Crea, duplica, edita y borra fichas. Nada se escribe hasta pulsar Guardar, y mientras haya una ficha que no vale no
    se puede guardar. Al guardar, el archivo anterior queda con el sufijo `.bak`."""

    title = ""                          # nombre de la ventana (y de sus avisos)
    size = (860, 560)
    empty_text = ""                     # lo que se ve cuando no hay ninguna ficha
    suggestions = staticmethod(lambda record: [])       # `advice` de la ficha: lo que falta o limita

    def __init__(self, editor: Editor, path=None, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.path = Path(path) if path is not None else None
        self.saved_path: Optional[Path] = None
        self._loading = False
        self._initial = self._state()
        self.setWindowTitle(self.title)
        self.resize(*self.size)
        self.setModal(True)

        # --- lista
        self.list = QListWidget()
        self.list.setMinimumWidth(270)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.ElideRight)
        self.btn_new = QPushButton(_("Nueva"))
        self.btn_dup = QPushButton(_("Duplicar"))
        self.btn_del = QPushButton(_("Eliminar"))

        # --- lo comun del formulario
        self.ident = QLineEdit()
        self.ident.setValidator(QRegularExpressionValidator(IDENT_RE, self))
        self.name = QLineEdit()
        self.name.setPlaceholderText(_("opcional (si no, el identificador)"))
        self.notes = QPlainTextEdit()
        self.notes.setFixedHeight(54)
        self.notes.setPlaceholderText(_("opcional"))
        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {theme.ERROR};")
        self.advice = QLabel("")
        self.advice.setWordWrap(True)
        self.advice.setObjectName("muted")
        self.empty = QLabel(self.empty_text)
        self.empty.setObjectName("muted")
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignCenter)

        # --- pie
        where = str(self.path) if self.path else _("(sin ruta: no se podrá guardar)")
        self.where = QLabel(_("Se guarda en {where}; la copia anterior queda como «{BACKUP_SUFFIX}».", where=where, BACKUP_SUFFIX=BACKUP_SUFFIX))
        self.where.setObjectName("muted")
        self.where.setWordWrap(True)
        self.where.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.btn_save = QPushButton(_("Guardar"))
        self.btn_save.setObjectName("primary")
        self.btn_cancel = QPushButton(_("Cancelar"))

        self._make_fields()

        buttons = QHBoxLayout()
        for b in (self.btn_new, self.btn_dup, self.btn_del):
            buttons.addWidget(b)
        left = QVBoxLayout()
        left.addWidget(self.list, 1)
        for w in self._left_widgets():
            left.addWidget(w)
        left.addLayout(buttons)

        self.form_box = QWidget()
        form = QFormLayout(self.form_box)
        form.setContentsMargins(0, 0, 0, 0)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.addRow(_("Identificador"), self.ident)
        form.addRow(_("Nombre"), self.name)
        self._add_rows(form)
        form.addRow(_("Notas"), self.notes)
        self._add_last_rows(form)

        right = QVBoxLayout()
        right.addWidget(self.empty)
        right.addWidget(self.form_box)
        right.addWidget(self.error)
        right.addWidget(self.advice)
        right.addStretch(1)
        body = QHBoxLayout()
        body.addLayout(left, 0)
        body.addLayout(right, 1)
        foot = QHBoxLayout()
        foot.addWidget(self.where, 1)
        foot.addWidget(self.btn_save)
        foot.addWidget(self.btn_cancel)
        lay = QVBoxLayout(self)
        lay.addLayout(body, 1)
        lay.addLayout(foot)

        self.list.currentRowChanged.connect(self._row_changed)
        self.btn_new.clicked.connect(self.new)
        self.btn_dup.clicked.connect(self.duplicate)
        self.btn_del.clicked.connect(self.remove)
        self.btn_save.clicked.connect(self.save)
        self.btn_cancel.clicked.connect(self.reject)
        for signal in (self.ident.textEdited, self.name.textEdited, self.notes.textChanged, *self._field_signals()):
            signal.connect(self._edited)

        self._fill_list()
        self._goto(0 if self.editor.entries else -1)

    # --- lo que pone cada ventana ---------------------------------------------------------------------------
    def _make_fields(self) -> None:
        """Crea las casillas propias (y ajusta las comunes: textos de ayuda, botones)."""

    def _left_widgets(self) -> list:
        """Widgets propios entre la lista y los botones Nueva/Duplicar/Eliminar."""
        return []

    def _add_rows(self, form: QFormLayout) -> None:
        """Las filas propias del formulario, entre Nombre y Notas."""

    def _add_last_rows(self, form: QFormLayout) -> None:
        """Las filas propias que van despues de Notas."""

    def _field_signals(self) -> list:
        """Las senales de las casillas propias que cambian la ficha."""
        return []

    def _fill_fields(self, form: dict) -> None:
        """Pone en las casillas propias lo que dice el formulario de la ficha."""

    def _collect_fields(self) -> dict:
        """Lo que dicen las casillas propias, como formulario."""
        return {}

    def _entry_changed(self, entry) -> None:
        """Lo que depende de la ficha elegida (o de ninguna: `entry` es None) ademas de errores y avisos."""

    def _focus_new(self) -> None:
        self.ident.setFocus()
        self.ident.selectAll()

    # --- estado ---------------------------------------------------------------------------------------------
    def _state(self) -> list:
        return [(e.ident, dict(e.form)) for e in self.editor.entries]

    def changed(self) -> bool:
        """Hay algo distinto de lo que se abrió."""
        return self._state() != self._initial

    @property
    def row(self) -> int:
        return self.list.currentRow()

    def select(self, index: int) -> None:
        self.list.setCurrentRow(index)

    def _goto(self, index: int) -> None:
        """Elige una ficha y rellena el formulario aunque sea la misma fila de antes."""
        self.list.blockSignals(True)
        self.list.setCurrentRow(index)
        self.list.blockSignals(False)
        self._row_changed(index)

    # --- lista ----------------------------------------------------------------------------------------------
    def _fill_list(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for __ in self.editor.entries:
            self.list.addItem(QListWidgetItem())
        self.list.blockSignals(False)
        self._refresh_labels()

    def _refresh_labels(self) -> None:
        for i, e in enumerate(self.editor.entries):
            item = self.list.item(i)
            text = e.label if e.ident else _("(sin identificador)")
            item.setToolTip(e.error or text)
            if e.valid:
                item.setText(text)
                item.setForeground(QBrush(QColor(theme.TEXT)))
            else:
                item.setText("⚠ " + text)
                item.setForeground(QBrush(QColor(theme.ERROR)))

    # --- formulario <-> ficha en edicion --------------------------------------------------------------------
    def _row_changed(self, index: int) -> None:
        has = 0 <= index < len(self.editor.entries)
        self.form_box.setVisible(has)
        self.empty.setVisible(not has)
        self.btn_dup.setEnabled(has)
        self.btn_del.setEnabled(has)
        if not has:
            self._after_edit()
            return
        e = self.editor.entries[index]
        self._loading = True
        try:
            self.ident.setText(e.ident)
            self.name.setText(e.form.get("nombre", ""))
            self.name.setPlaceholderText(e.ident or _("opcional (si no, el identificador)"))
            self._fill_fields(e.form)
            self.notes.setPlainText(e.form.get("notas", ""))
        finally:
            self._loading = False
        self._after_edit()

    def _collect(self) -> tuple[str, dict]:
        form = {"nombre": self.name.text().strip(), "notas": self.notes.toPlainText().strip(), **self._collect_fields()}
        return self.ident.text().strip(), form

    def _edited(self, *_args) -> None:
        if self._loading or not 0 <= self.row < len(self.editor.entries):
            return
        ident, form = self._collect()
        self.editor.update(self.row, ident=ident, form=form)
        self.name.setPlaceholderText(ident or _("opcional (si no, el identificador)"))
        self._after_edit()

    def _after_edit(self) -> None:
        """Pone lo que depende de la ficha elegida: errores, avisos, lista y botón de guardar."""
        e = self.editor.entries[self.row] if 0 <= self.row < len(self.editor.entries) else None
        self.error.setText(e.error if e is not None and not e.valid else "")
        self.advice.setText("\n".join("• " + n for n in self.suggestions(e.record)) if e is not None and e.valid else "")
        self._entry_changed(e)
        self._refresh_labels()
        self.btn_save.setEnabled(self.editor.valid and self.path is not None and bool(self.editor.entries))
        if not self.editor.valid:
            self.btn_save.setToolTip(_("Hay fichas que no valen:\n") + "\n".join(self.editor.errors()))
        elif not self.editor.entries:
            self.btn_save.setToolTip(_("No hay ninguna ficha que guardar."))
        elif self.path is None:
            self.btn_save.setToolTip(_("No hay ruta donde guardar el archivo."))
        else:
            self.btn_save.setToolTip("")

    # --- acciones -----------------------------------------------------------------------------------------
    def new(self) -> None:
        self.editor.new()
        self._fill_list()
        self._goto(len(self.editor.entries) - 1)
        self._focus_new()

    def duplicate(self) -> None:
        if not 0 <= self.row < len(self.editor.entries):
            return
        new = self.editor.duplicate(self.row)
        self._fill_list()
        self._goto(new)
        self.ident.setFocus()
        self.ident.selectAll()

    def remove(self) -> None:
        i = self.row
        if not 0 <= i < len(self.editor.entries):
            return
        self.editor.remove(i)
        self._fill_list()
        self._goto(min(i, len(self.editor.entries) - 1) if self.editor.entries else -1)

    def save(self) -> bool:
        """Escribe el archivo y cierra. Si no se puede, avisa y deja la ventana abierta."""
        if self.path is None:
            return False
        try:
            self.saved_path = self.editor.save(self.path)
        except self.editor.error_class as e:
            QMessageBox.warning(self, self.title, str(e))
            return False
        except OSError as e:
            QMessageBox.critical(self, _("No se pudo guardar"), f"{self.path.name}: {e}")
            return False
        self.accept()
        return True

    def reject(self) -> None:
        if self.saved_path is None and self.changed():
            answer = QMessageBox.question(self, self.title, _("Hay cambios sin guardar. ¿Cerrar y descartarlos?"),
                                          QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        super().reject()
