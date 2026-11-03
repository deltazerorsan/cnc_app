"""Editor de las fichas de fresas (herramientas.json): lista a la izquierda, formulario a la derecha."""
from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QComboBox, QFormLayout

from ..tools import FIELDS
from ..toolbook import KIND_LABEL, ToolEditor, advice
from .record_dialog import RecordDialog
from .widgets import OptionalNumber
from ..i18n import _

# (clave, texto de la casilla, texto gris) en el orden en que se rellenan
NUMBER_ROWS = (
    ("diametro", _("Diámetro (mm)"), _("obligatorio")),
    ("angulo", _("Ángulo de la V (°)"), _("solo en una V")),
    ("flautas", _("Flautas"), ""),
    ("longitud_util", _("Longitud útil (mm)"), _("mm de filo")),
    ("rpm", _("RPM"), _("informativo")),
    ("profundidad_pasada", _("Profundidad de pasada (mm)"), ""),
    ("feed", _("Avance de corte (mm/min)"), ""),
    ("entrada", _("Avance de bajada (mm/min)"), ""),
    ("stepover", _("Stepover (% del diámetro)"), _("p. ej. 40")),
)


class ToolsDialog(RecordDialog):
    """Crea, duplica, edita y borra fichas. Nada se escribe hasta pulsar Guardar, y mientras haya una ficha que no
    vale no se puede guardar. Al guardar, el archivo anterior queda como herramientas.json.bak."""

    title = _("Fichas de fresas")
    empty_text = _("No hay fichas. Pulsa Nueva para crear la primera.")
    suggestions = staticmethod(advice)

    def __init__(self, tools: Optional[dict] = None, path=None, parent=None):
        super().__init__(ToolEditor(tools), path, parent)

    def _make_fields(self) -> None:
        self.btn_dup.setToolTip(_("Copia la ficha elegida con otro identificador (p. ej. el mismo diámetro para otro material)"))
        self.btn_save.setDefault(True)
        self.ident.setPlaceholderText(_("p. ej. em4_mdf"))
        self.ident.setToolTip(_("El nombre corto de la ficha, el que se ve en la lista. Empieza por una letra; letras, números, _ - ."))
        self.kind = QComboBox()
        for value, label in KIND_LABEL.items():
            self.kind.addItem(label, value)
        self.numbers: dict[str, OptionalNumber] = {}
        for key, __, hint in NUMBER_ROWS:
            self.numbers[key] = OptionalNumber(hint)
            self.numbers[key].setToolTip(FIELDS[key])

    def _add_rows(self, form: QFormLayout) -> None:
        form.addRow(_("Tipo"), self.kind)
        for key, label, __ in NUMBER_ROWS:
            form.addRow(label, self.numbers[key])

    def _field_signals(self) -> list:
        return [self.kind.currentIndexChanged, *(edit.valueChanged for edit in self.numbers.values())]

    def _fill_fields(self, form: dict) -> None:
        self.kind.setCurrentIndex(max(self.kind.findData(form.get("tipo") or "endmill"), 0))
        for key, edit in self.numbers.items():
            edit.setText(form.get(key, ""))

    def _collect_fields(self) -> dict:
        return {"tipo": self.kind.currentData(), **{key: edit.text().strip() for key, edit in self.numbers.items()}}

    def _entry_changed(self, entry) -> None:
        self.numbers["angulo"].setEnabled(self.kind.currentData() == "v")

    def _focus_new(self) -> None:
        self.numbers["diametro"].setFocus()
