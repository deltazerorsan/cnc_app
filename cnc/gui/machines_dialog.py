"""Editor de las fichas de maquina (maquinas.json): lista a la izquierda, formulario a la derecha, y el dialogo para crear o
actualizar una ficha pegando lo que UGS muestra al conectar (el log de arranque: `$$` y `$I`)."""
from __future__ import annotations

from datetime import date
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase, QRegularExpressionValidator
from PySide6.QtWidgets import (QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from ..machinebook import FLAG_CHOICES, MachineEditError, MachineEditor, advice, check_ident
from ..machines import parse_log, understood
from . import theme
from .record_dialog import IDENT_RE, RecordDialog
from .widgets import OptionalNumber
from ..i18n import _
FLAG_ROWS = (("limites_blandos", _("Límites blandos ($20)"), _("Con ellos, GRBL da alarma si el G-code sale del recorrido.")),
             ("limites_duros", _("Límites duros ($21)"), _("Finales de carrera: paran la máquina al tocarlos.")),
             ("homing", _("Homing ($22)"), _("Sin homing, la orden $H da error: no se puede terminar un trabajo con Home.")),
             ("laser", _("Modo láser ($32)"), _("Con $32=1, GRBL apaga el husillo en cada G0: no vale para una fresadora.")))


def today_source() -> str:
    return _("log pegado el {strftime}", strftime=date.today().strftime('%d/%m/%Y'))


def _flag_combo() -> QComboBox:
    c = QComboBox()
    for value, label in FLAG_CHOICES:
        c.addItem(label, value)
    return c


def _row(*widgets) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setContentsMargins(0, 0, 0, 0)
    for w in widgets:
        lay.addWidget(w, 1 if isinstance(w, (OptionalNumber, QLineEdit)) else 0)
    return lay


class LogImportDialog(QDialog):
    """Pega aqui el log de arranque de la maquina (lo que UGS muestra al conectar, o lo que contesta a `$$`), o escribe los
    valores uno por linea (`$130=651`). Ensena lo que ha entendido para que se compruebe contra la pantalla antes de aceptar.

    Con `update_index` se actualiza esa ficha (solo cambia lo que el log trae) y se enseña lo que cambiaria."""

    def __init__(self, editor: MachineEditor, update_index: Optional[int] = None, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.update_index = update_index
        updating = update_index is not None
        self.setWindowTitle(_("Actualizar la ficha desde un log") if updating else _("Máquina nueva desde un log"))
        self.resize(640, 640)
        self.setModal(True)

        self.help = QLabel(
            (_("Pega el log de la máquina para actualizar la ficha «%s».") % editor.entries[update_index].ident if updating
             else _("Pega el log de arranque de la máquina.")) +
            _(" Es lo que UGS escribe en su consola al conectar, y lo que contesta a la orden $$ (y $I). "
            "Si no lo tienes copiado, escribe los valores que ves, uno por línea (por ejemplo $130=651)."))
        self.help.setWordWrap(True)
        self.log = QPlainTextEdit()
        self.log.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.log.setPlaceholderText("Grbl 1.1h ['$' for help]\n$$\n$110=3600.000\n…\n$130=651.000\n$131=1011.000\n"
                                    "$132=100.000\nok")
        self.ident = QLineEdit(editor.unique("maquina"))
        self.ident.setValidator(QRegularExpressionValidator(IDENT_RE, self))
        self.ident.setToolTip(_("El nombre corto de la ficha: el que se elige en el trabajo."))
        self.name = QLineEdit()
        self.name.setPlaceholderText(_("opcional (si no, el identificador)"))
        form = QFormLayout()
        form.addRow(_("Identificador"), self.ident)
        form.addRow(_("Nombre"), self.name)
        self.form_box = QWidget()
        self.form_box.setLayout(form)
        form.setContentsMargins(0, 0, 0, 0)
        self.form_box.setVisible(not updating)

        self.title_read = QLabel(_("Lo que se ha entendido"))
        self.title_read.setObjectName("muted")
        self.read = QLabel("")
        self.read.setWordWrap(True)
        self.read.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.problems = QLabel("")
        self.problems.setWordWrap(True)
        self.problems.setStyleSheet(f"color: {theme.ERROR};")
        self.notes = QLabel("")
        self.notes.setWordWrap(True)
        self.notes.setObjectName("muted")
        self.changes = QLabel("")
        self.changes.setWordWrap(True)
        self.changes.setTextFormat(Qt.PlainText)
        self.status = QLabel("")                         # por que todavia no se puede aceptar (si no lo dice ya lo de arriba)
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color: {theme.ERROR};")

        self.btn_ok = QPushButton(_("Actualizar la ficha") if updating else _("Añadir la máquina"))
        self.btn_ok.setObjectName("primary")
        self.btn_ok.setDefault(True)
        self.btn_cancel = QPushButton(_("Cancelar"))
        foot = QHBoxLayout()
        foot.addStretch(1)
        foot.addWidget(self.btn_ok)
        foot.addWidget(self.btn_cancel)

        lay = QVBoxLayout(self)
        lay.addWidget(self.help)
        lay.addWidget(self.log, 1)
        lay.addWidget(self.form_box)
        lay.addWidget(self.title_read)
        lay.addWidget(self.read)
        lay.addWidget(self.problems)
        lay.addWidget(self.notes)
        lay.addWidget(self.changes)
        lay.addWidget(self.status)
        lay.addLayout(foot)

        self.log.textChanged.connect(self._update)
        self.ident.textChanged.connect(self._update)
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        self._update()

    # --- lo que se ha escrito ---------------------------------------------------------------------------------
    def text(self) -> str:
        return self.log.toPlainText()

    def machine_ident(self) -> str:
        return self.ident.text().strip()

    def machine_name(self) -> str:
        return self.name.text().strip()

    def set_text(self, text: str) -> None:
        self.log.setPlainText(text)

    # --- comprobacion en vivo ---------------------------------------------------------------------------------
    def _update(self) -> None:
        text = self.text()
        parsed = parse_log(text)
        lines = understood(parsed)
        self.read.setText("\n".join("• " + line for line in lines) if lines else _("(nada todavía)"))
        self.problems.setText("\n".join("• " + p for p in parsed.problems))
        self.notes.setText("\n".join("• " + n for n in parsed.notes) if text.strip() else "")
        self.changes.setText("")
        self.status.setText("")
        why = ""
        shown = False                                   # el motivo ya sale arriba (problemas o notas del log)
        if not text.strip():
            why = _("Pega el log o escribe los valores.")
            shown = True
        elif not parsed.usable:
            why = parsed.problems[0] if parsed.problems else parsed.notes[0]
            shown = True
        elif self.update_index is None:
            try:
                check_ident(self.machine_ident())
                if any(e.ident == self.machine_ident() for e in self.editor.entries):
                    raise MachineEditError(_("ya hay una ficha con el identificador '{machine_ident}'.", machine_ident=self.machine_ident()))
            except MachineEditError as e:
                why = str(e)
        else:
            try:
                changes = self.editor.preview_refresh(self.update_index, text)
            except MachineEditError as e:
                why = str(e)
            else:
                if changes:
                    self.changes.setText(_("Cambiaría:\n") + "\n".join("• " + c for c in changes))
                else:
                    why = _("El log no cambia ningún dato de la ficha.")
        self.btn_ok.setEnabled(not why)
        self.btn_ok.setToolTip(why)
        if why and not shown:
            self.status.setText(why)


class MachinesDialog(RecordDialog):
    """Crea, duplica, edita y borra fichas de maquina. Nada se escribe hasta pulsar Guardar, y mientras haya una ficha que no
    vale no se puede guardar. Al guardar, el archivo anterior queda como maquinas.json.bak."""

    title = _("Máquinas")
    size = (900, 640)
    empty_text = _("No hay máquinas. Pulsa «Nueva desde un log…» y pega lo que UGS muestra al conectar.")
    suggestions = staticmethod(advice)

    def __init__(self, machines: Optional[dict] = None, path=None, parent=None):
        super().__init__(MachineEditor(machines), path, parent)

    def _make_fields(self) -> None:
        self.btn_log = QPushButton(_("Nueva desde un log…"))
        self.btn_log.setObjectName("primary")
        self.btn_log.setToolTip(_("Pega lo que UGS muestra al conectar con la máquina (el $$ y el $I): la ficha se rellena sola"))
        self.btn_new.setToolTip(_("Una ficha en blanco, para escribir los valores a mano"))
        self.ident.setPlaceholderText(_("p. ej. taller"))
        self.ident.setToolTip(_("El nombre corto de la ficha: el que se elige en el trabajo. "
                              "Empieza por una letra; letras, números, _ - ."))
        self.name.setToolTip(_("Sale en la lista de comprobación y en la cabecera del .nc"))
        self.travel = [OptionalNumber(_("obligatorio")) for __ in range(3)]
        for edit, axis in zip(self.travel, "XYZ"):
            edit.setToolTip(_("Recorrido en {axis} en mm (${v}): lo que cabe en la máquina", axis=axis, v=130 + 'XYZ'.index(axis)))
        self.accel = [OptionalNumber("") for __ in range(3)]
        for edit, axis in zip(self.accel, "XYZ"):
            edit.setToolTip(_("Aceleración en {axis} en mm/s² (${v}): informativo", axis=axis, v=120 + 'XYZ'.index(axis)))
        self.feed_xy = OptionalNumber(_("sin dato"))
        self.feed_xy.setToolTip(_("Avance máximo en XY en mm/min ($110 y $111: el menor de los dos)"))
        self.feed_z = OptionalNumber(_("sin dato"))
        self.feed_z.setToolTip(_("Avance máximo en Z en mm/min ($112)"))
        self.spindle = OptionalNumber(_("informativo"))
        self.spindle.setToolTip(_("RPM máximas ($30): informativo, el husillo de aquí es on/off"))
        self.flags: dict[str, QComboBox] = {}
        for key, __, tip in FLAG_ROWS:
            self.flags[key] = _flag_combo()
            self.flags[key].setToolTip(tip)
        self.firmware = QLineEdit()
        self.firmware.setPlaceholderText(_("p. ej. Grbl 1.1h"))
        self.source = QLineEdit()
        self.source.setPlaceholderText(_("de dónde salieron los datos"))
        self.btn_refresh = QPushButton(_("Actualizar esta ficha desde un log…"))
        self.btn_refresh.setToolTip(_("Cambia solo lo que el log trae (p. ej. si has cambiado $130 en la máquina)"))
        self.btn_log.clicked.connect(self.new_from_log)
        self.btn_refresh.clicked.connect(self.refresh_from_log)

    def _left_widgets(self) -> list:
        return [self.btn_log]

    def _add_rows(self, form: QFormLayout) -> None:
        form.addRow(_("Recorrido X Y Z (mm)"), _row(*self.travel))
        form.addRow(_("Avance máx. XY (mm/min)"), self.feed_xy)
        form.addRow(_("Avance máx. Z (mm/min)"), self.feed_z)
        form.addRow(_("Aceleración X Y Z (mm/s²)"), _row(*self.accel))
        for key, label, __ in FLAG_ROWS:
            form.addRow(label, self.flags[key])
        form.addRow(_("Husillo máx. (RPM)"), self.spindle)
        form.addRow(_("Firmware"), self.firmware)
        form.addRow(_("Origen de los datos"), self.source)

    def _add_last_rows(self, form: QFormLayout) -> None:
        form.addRow(self.btn_refresh)

    def _field_signals(self) -> list:
        return [self.firmware.textEdited, self.source.textEdited,
                *(edit.valueChanged for edit in (*self.travel, *self.accel, self.feed_xy, self.feed_z, self.spindle)),
                *(combo.currentIndexChanged for combo in self.flags.values())]

    def _fill_fields(self, f: dict) -> None:
        for edit, key in zip(self.travel, ("x", "y", "z")):
            edit.setText(f.get(key, ""))
        for edit, key in zip(self.accel, ("acel_x", "acel_y", "acel_z")):
            edit.setText(f.get(key, ""))
        self.feed_xy.setText(f.get("avance_max", ""))
        self.feed_z.setText(f.get("avance_max_z", ""))
        self.spindle.setText(f.get("husillo_max", ""))
        for key, combo in self.flags.items():
            combo.setCurrentIndex(max(combo.findData(f.get(key, "")), 0))
        self.firmware.setText(f.get("firmware", ""))
        self.source.setText(f.get("origen", ""))

    def _collect_fields(self) -> dict:
        form = {key: edit.text().strip() for key, edit in zip(("x", "y", "z"), self.travel)}
        form.update({key: edit.text().strip() for key, edit in zip(("acel_x", "acel_y", "acel_z"), self.accel)})
        form.update(avance_max=self.feed_xy.text().strip(), avance_max_z=self.feed_z.text().strip(),
                    husillo_max=self.spindle.text().strip(), firmware=self.firmware.text().strip(),
                    origen=self.source.text().strip())
        form.update({key: combo.currentData() or "" for key, combo in self.flags.items()})
        return form

    def _entry_changed(self, entry) -> None:
        self.btn_refresh.setEnabled(entry is not None and entry.valid)

    # --- acciones propias ---------------------------------------------------------------------------------
    def import_log(self, text: str, ident: str = "", name: str = "") -> int:
        """Anade una ficha leida de un log y la deja elegida. Lanza MachineEditError si el log no sirve."""
        index = self.editor.import_log(text, ident=ident, name=name, source=today_source())
        self._fill_list()
        self._goto(index)
        return index

    def new_from_log(self) -> bool:
        dialog = LogImportDialog(self.editor, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return False
        try:
            self.import_log(dialog.text(), dialog.machine_ident(), dialog.machine_name())
        except MachineEditError as e:
            QMessageBox.warning(self, _("Máquinas"), str(e))
            return False
        return True

    def update_from_log(self, text: str) -> list[str]:
        """Actualiza la ficha elegida con un log y devuelve lo que cambió. Lanza MachineEditError si el log no sirve."""
        changes = self.editor.refresh_from_log(self.row, text, source=today_source())
        self._goto(self.row)
        return changes

    def refresh_from_log(self) -> bool:
        if not 0 <= self.row < len(self.editor.entries):
            return False
        dialog = LogImportDialog(self.editor, update_index=self.row, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return False
        try:
            self.update_from_log(dialog.text())
        except MachineEditError as e:
            QMessageBox.warning(self, _("Máquinas"), str(e))
            return False
        return True
