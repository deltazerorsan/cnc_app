"""Dialogos de las herramientas de dibujo: una lista de campos con medidas (mm, grados), opciones y casillas.

Los numeros admiten coma o punto decimal. El boton Aceptar queda apagado mientras falte algun dato o alguno este mal
escrito, asi lo que sale de `values()` siempre vale para construir la edicion.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
                               QPlainTextEdit, QVBoxLayout)

from .widgets import OptionalNumber, color_icon
from ..i18n import _

OTHER_COLOR = "__otro__"


@dataclass
class Num:
    key: str
    label: str
    default: Optional[float] = None
    integer: bool = False
    negative: bool = True
    tip: str = ""


@dataclass
class Choice:
    key: str
    label: str
    options: Sequence[tuple]                 # (valor, texto)
    default: Optional[object] = None
    tip: str = ""


@dataclass
class Check:
    key: str
    label: str
    default: bool = False
    tip: str = ""


@dataclass
class Colour:
    key: str
    label: str
    colors: Sequence[str] = ()               # los que ya hay en el dibujo
    default: str = "#000000"


@dataclass
class Points:
    key: str
    label: str
    default: str = ""
    tip: str = ""


def _fmt(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 3)


def parse_points(text: str) -> Optional[list]:
    """Los puntos de un texto: uno por linea, 'X Y' o 'X; Y' (con coma o punto decimal). None si alguna linea esta mal
    escrita; una lista vacia si no hay ninguna."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p for p in (line.split(";") if ";" in line else line.split()) if p.strip()]
        if len(parts) != 2:
            return None
        try:
            out.append((float(parts[0].strip().replace(",", ".")), float(parts[1].strip().replace(",", "."))))
        except ValueError:
            return None
    return out


class ToolDialog(QDialog):
    def __init__(self, title: str, fields: Sequence, intro: str = "", parent=None, ok_text: str = _("Aceptar")):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.fields = list(fields)
        self.widgets: dict = {}
        self._color_back: dict = {}
        self.hook: Optional[Callable[["ToolDialog", str], None]] = None     # se llama al cambiar un campo (con su clave)
        self._busy = False
        lay = QVBoxLayout(self)
        if intro:
            label = QLabel(intro)
            label.setWordWrap(True)
            label.setMinimumWidth(380)
            lay.addWidget(label)
        form = QFormLayout()
        for f in self.fields:
            w = self._build(f)
            self.widgets[f.key] = w
            if getattr(f, "tip", ""):
                w.setToolTip(f.tip)
            if isinstance(f, Check):
                form.addRow(w)
            else:
                form.addRow(f.label, w)
        lay.addLayout(form)
        self.problem = QLabel("")
        self.problem.setStyleSheet("color: #b3261e;")
        self.problem.setWordWrap(True)
        lay.addWidget(self.problem)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText(ok_text)
        self.buttons.button(QDialogButtonBox.Cancel).setText(_("Cancelar"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self._validate()

    # --- construccion de cada campo -------------------------------------------------------------------------------
    def _build(self, f):
        if isinstance(f, Num):
            w = OptionalNumber(negative=f.negative)
            w.setValue(_fmt(f.default))
            w.valueChanged.connect(lambda key=f.key: self._changed(key))
            return w
        if isinstance(f, Choice):
            w = QComboBox()
            for value, text in f.options:
                w.addItem(text, value)
            if f.default is not None:
                w.setCurrentIndex(max(0, w.findData(f.default)))
            w.currentIndexChanged.connect(lambda _i, key=f.key: self._changed(key))
            return w
        if isinstance(f, Check):
            w = QCheckBox(f.label)
            w.setChecked(f.default)
            w.toggled.connect(lambda _on, key=f.key: self._changed(key))
            return w
        if isinstance(f, Colour):
            return self._build_colour(f)
        if isinstance(f, Points):
            w = QPlainTextEdit()
            w.setPlainText(f.default)
            w.setMinimumHeight(110)
            w.setPlaceholderText("0 0\n50 0\n50 30")
            w.textChanged.connect(lambda key=f.key: self._changed(key))
            return w
        raise TypeError(_("campo desconocido: {f!r}", f=f))

    def _build_colour(self, f: Colour) -> QComboBox:
        w = QComboBox()
        seen = []
        for c in [f.default, *f.colors]:
            if c and c.lower() not in seen:
                seen.append(c.lower())
        for c in seen:
            w.addItem(color_icon(c), c, c)
        w.addItem(_("Otro color…"), OTHER_COLOR)
        w.setCurrentIndex(max(0, w.findData(f.default.lower())))
        self._color_back[f.key] = w.currentIndex()
        w.activated.connect(lambda _i, key=f.key: self._colour_activated(key))
        return w

    def _colour_activated(self, key: str) -> None:
        w: QComboBox = self.widgets[key]
        if w.currentData() == OTHER_COLOR:
            chosen = QColorDialog.getColor(QColor(w.itemData(self._color_back[key]) or "#000000"), self, _("Color del trazo"))
            if chosen.isValid():
                self.set_colour(key, chosen.name())
            else:
                w.setCurrentIndex(self._color_back[key])
        else:
            self._color_back[key] = w.currentIndex()
        self._changed(key)

    def set_colour(self, key: str, color: str) -> None:
        w: QComboBox = self.widgets[key]
        color = color.lower()
        i = w.findData(color)
        if i < 0:
            i = w.count() - 1                                    # antes de "Otro color"
            w.insertItem(i, color_icon(color), color, color)
        w.setCurrentIndex(i)
        self._color_back[key] = i

    # --- lectura -------------------------------------------------------------------------------------------------------
    def _changed(self, key: str = "") -> None:
        if self.hook is not None and not self._busy:
            self._busy = True
            try:
                self.hook(self, key)
            finally:
                self._busy = False
        self._validate()

    def set_value(self, key: str, value) -> None:
        w = self.widgets[key]
        f = next(f for f in self.fields if f.key == key)
        if isinstance(f, Num):
            w.setValue(_fmt(value))
        elif isinstance(f, Choice):
            w.setCurrentIndex(max(0, w.findData(value)))
        elif isinstance(f, Check):
            w.setChecked(bool(value))
        elif isinstance(f, Points):
            w.setPlainText("\n".join(f"{x:g} {y:g}" for x, y in value) if isinstance(value, (list, tuple)) else str(value))
        elif isinstance(f, Colour):
            self.set_colour(key, str(value))
        self._validate()

    def values(self) -> dict:
        out: dict = {}
        for f in self.fields:
            w = self.widgets[f.key]
            if isinstance(f, Num):
                v = w.value()
                out[f.key] = None if v is None else (int(round(v)) if f.integer else v)
            elif isinstance(f, Choice):
                out[f.key] = w.currentData()
            elif isinstance(f, Check):
                out[f.key] = w.isChecked()
            elif isinstance(f, Colour):
                out[f.key] = w.currentData() if w.currentData() != OTHER_COLOR else None
            elif isinstance(f, Points):
                out[f.key] = parse_points(w.toPlainText())
        return out

    def issue(self) -> str:
        """Lo que falta o esta mal ('' si se puede aceptar)."""
        for f in self.fields:
            w = self.widgets[f.key]
            if isinstance(f, Num):
                v = w.value()
                if v is None:
                    return _("Falta «{label}».", label=f.label)
                if f.integer and abs(v - round(v)) > 1e-9:
                    return _("«{label}» tiene que ser un número entero.", label=f.label)
            elif isinstance(f, Points):
                pts = parse_points(w.toPlainText())
                if pts is None:
                    return _("Hay una línea mal escrita: pon un punto por línea, «X Y» o «X; Y».")
                if not pts:
                    return _("Falta «{label}».", label=f.label)
        return ""

    def _validate(self) -> None:
        issue = self.issue()
        self.problem.setText(issue)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(not issue)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not self.buttons.button(QDialogButtonBox.Ok).isEnabled():
            return                                                # Intro no acepta un dialogo incompleto
        super().keyPressEvent(event)
