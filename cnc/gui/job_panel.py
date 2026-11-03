"""Ajustes del trabajo (fresa, material, avances, G-code) y los propios de cada tablero."""
from __future__ import annotations

from dataclasses import replace
from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPlainTextEdit,
                               QPushButton, QVBoxLayout, QWidget)

from ..machines import travel_text
from ..project import ROTATIONS, BoardOverrides, General, Project, ProjectError
from . import theme
from .widgets import OptionalNumber
from ..i18n import _

ROTATION_LABEL = {"auto": _("Automático"), "0": _("Sin girar"), "90": _("90° antihorario"), "180": "180°",
                  "270": _("270° (90° horario)"), "vertical": _("Vertical")}
ROTATION_TIP = (_("Automático: gira 90° antihorario solo si no cabe tal cual en la máquina y sí girado.\n"
                "Vertical: pone el lado largo del dibujo a lo largo de Y."))
ORIGIN_MARGIN_TIP = (_("Solo si el dibujo no tiene tablero: el cero de la máquina queda a estos mm, por fuera, de la esquina\n"
                     "inferior izquierda de lo que se corta (se estima ahí el borde del material). 0 la pone justo en el cero.\n"
                     "Con el mismo margen arriba y a la derecha se supone el tablero que usa la pestaña Sujeción."))
BRIDGES_TIP = (_("Interruptor general de los puentes. Apagado, el G-code sale sin ninguno: ni los que proponen las filas ni los "
                 "puestos a mano (quedan guardados y vuelven al encenderlo). Se recuerda en este PC."))


def tool_label(ident: str, tool) -> str:
    """Como se ve una ficha en las listas: `em4 — End mill 4 mm (Ø4 mm)`."""
    return _("{ident} — {name} (Ø{diameter:g} mm)", ident=ident, name=tool.name, diameter=tool.diameter) if tool.name and tool.name != ident else _("{ident} (Ø{diameter:g} mm)", ident=ident, diameter=tool.diameter)


def machine_label(ident: str, machine) -> str:
    """Como se ve una maquina en las listas: `real — Taller (651 × 1011 × 100 mm)`."""
    size = travel_text(machine)
    return (f"{ident} — {machine.name} ({size})" if machine.name and machine.name != ident else f"{ident} ({size})")


def _shrinkable(combo: QComboBox) -> QComboBox:
    """Que un texto largo no ensanche el panel: el desplegable se acorta y el texto completo sale al abrirlo."""
    combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(10)
    return combo


def _combo(options: dict[str, str]) -> QComboBox:
    c = _shrinkable(QComboBox())
    for value, label in options.items():
        c.addItem(label, value)
    return c


def _set_combo(combo: QComboBox, value) -> None:
    i = combo.findData(value)
    combo.setCurrentIndex(max(i, 0))


class JobPanel(QWidget):
    changed = Signal()
    editToolsRequested = Signal()
    editMachinesRequested = Signal()
    machineChosen = Signal(str)             # el identificador que la persona acaba de elegir
    bridgesToggled = Signal(bool)           # la persona acaba de encender o apagar el interruptor de los puentes

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project: Optional[Project] = None
        self.board: Optional[int] = None
        self._loading = False

        # --- fresa y material
        self.tool = _shrinkable(QComboBox())
        self.tool.setEditable(True)
        self.tool.lineEdit().setPlaceholderText(_("elige una ficha o escribe el diámetro en mm"))
        self.edit_tools = QPushButton(_("Fichas…"))
        self.edit_tools.setToolTip(_("Crear, duplicar y editar las fichas de fresas (herramientas.json)  Ctrl+T"))
        tool_row = QHBoxLayout()
        tool_row.setContentsMargins(0, 0, 0, 0)
        tool_row.addWidget(self.tool, 1)
        tool_row.addWidget(self.edit_tools)
        self.machine = _shrinkable(QComboBox())
        self.machine.setToolTip(_("La máquina para la que es este trabajo: de su ficha salen el recorrido y los topes"))
        self.machine.setPlaceholderText(_("elige la máquina"))
        self.edit_machines = QPushButton(_("Máquinas…"))
        self.edit_machines.setToolTip(_("Crear y editar las fichas de máquina; se rellenan pegando el log de UGS  Ctrl+M"))
        machine_row = QHBoxLayout()
        machine_row.setContentsMargins(0, 0, 0, 0)
        machine_row.addWidget(self.machine, 1)
        machine_row.addWidget(self.edit_machines)
        self.machine_note = QLabel("")
        self.machine_note.setObjectName("muted")
        self.machine_note.setWordWrap(True)
        self.thickness = OptionalNumber(_("obligatorio"))
        self.margin = OptionalNumber("0")
        self.origin_margin = OptionalNumber("0")
        self.origin_margin.setToolTip(ORIGIN_MARGIN_TIP)
        self.rotate = _combo({r: ROTATION_LABEL[r] for r in ROTATIONS})
        self.rotate.setToolTip(ROTATION_TIP)
        mat = QGroupBox(_("Fresa y material"))
        f = QFormLayout(mat)
        f.addRow(_("Máquina"), machine_row)
        f.addRow(self.machine_note)
        f.addRow(_("Fresa"), tool_row)
        f.addRow(_("Grosor (mm)"), self.thickness)
        f.addRow(_("Margen sufridor (mm)"), self.margin)
        self.origin_margin_label = QLabel(_("Cero a (mm) del dibujo"))
        self.origin_margin_label.setToolTip(ORIGIN_MARGIN_TIP)
        f.addRow(self.origin_margin_label, self.origin_margin)
        f.addRow(_("Giro"), self.rotate)

        # --- avances
        self.feed = OptionalNumber()
        self.plunge = OptionalNumber()
        self.stepdown = OptionalNumber()
        cut = QGroupBox(_("Avances (vacío = el de la ficha)"))
        f = QFormLayout(cut)
        f.addRow(_("Corte (mm/min)"), self.feed)
        f.addRow(_("Bajada (mm/min)"), self.plunge)
        f.addRow(_("Pasada (mm)"), self.stepdown)

        # --- puentes
        defaults = General()
        self.bridges_on = QCheckBox(_("Poner puentes"))
        self.bridges_on.setToolTip(BRIDGES_TIP)
        self.tab_width = OptionalNumber(f"{defaults.tab_width:g}")
        self.tab_height = OptionalNumber(f"{defaults.tab_height:g}")
        self.tab_spacing = OptionalNumber(f"{defaults.tab_spacing:g}")
        self.tab_width.setToolTip(_("Ancho del puente que queda, a lo largo del contorno. La fresa se levanta además su diámetro."))
        self.tab_height.setToolTip(_("Material que queda del puente, sobre el fondo del tablero."))
        self.tab_spacing.setToolTip(_("Un puente por cada tantos mm de perímetro de la pieza (mínimo dos si caben)."))
        bridges = QGroupBox(_("Puentes (las filas con «Puentes» marcado)"))
        f = QFormLayout(bridges)
        f.addRow(self.bridges_on)
        f.addRow(_("Ancho (mm)"), self.tab_width)
        f.addRow(_("Alto (mm)"), self.tab_height)
        f.addRow(_("Uno cada (mm de perímetro)"), self.tab_spacing)

        # --- g-code
        self.safe_z = OptionalNumber("5")
        self.clearance_z = OptionalNumber("1")
        self.spindle = QCheckBox(_("Encender y apagar el husillo (M3 / M5)"))
        self.pause = QCheckBox(_("Pausa (M0) antes de arrancar el husillo"))
        self.home = QCheckBox(_("Home ($H) al terminar"))
        self.back = QCheckBox(_("Volver al origen al terminar"))
        self.end_code = QPlainTextEdit()
        self.end_code.setPlaceholderText(_("G-code propio para el final, una orden por línea"))
        self.end_code.setFixedHeight(54)
        gc = QGroupBox(_("G-code"))
        f = QFormLayout(gc)
        f.addRow(_("Z segura (mm)"), self.safe_z)
        f.addRow(_("Z de aproximación (mm)"), self.clearance_z)
        f.addRow(self.spindle)
        f.addRow(self.pause)
        f.addRow(self.home)
        f.addRow(self.back)
        f.addRow(_("Código final"), self.end_code)

        # --- tablero
        self.board_box = QGroupBox(_("Tablero"))
        self.b_selected = QCheckBox(_("Generar este tablero"))
        self.b_thickness = OptionalNumber()
        self.b_margin = OptionalNumber()
        self.b_dx = OptionalNumber("0", negative=True)
        self.b_dy = OptionalNumber("0", negative=True)
        self.b_rotate = _combo({"": _("(el general)"), **{r: ROTATION_LABEL[r] for r in ROTATIONS}})
        off = QHBoxLayout()
        off.addWidget(QLabel("X"))
        off.addWidget(self.b_dx)
        off.addWidget(QLabel("Y"))
        off.addWidget(self.b_dy)
        f = QFormLayout(self.board_box)
        f.addRow(self.b_selected)
        f.addRow(_("Grosor (mm)"), self.b_thickness)
        f.addRow(_("Margen (mm)"), self.b_margin)
        f.addRow(_("Desplazar dibujo (mm)"), off)
        f.addRow(_("Giro"), self.b_rotate)
        self.b_note = QLabel(_("Solo si este tablero es distinto de los demás (otro material, o hay que mover el "
                             "dibujo para que quepa en el tablero)."))
        self.b_note.setObjectName("muted")
        self.b_note.setWordWrap(True)
        f.addRow(self.b_note)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        for box in (mat, cut, bridges, self.board_box, gc):
            form = box.layout()
            form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            lay.addWidget(box)
        lay.addStretch(1)

        for w in (self.thickness, self.margin, self.origin_margin, self.feed, self.plunge, self.stepdown, self.safe_z, self.clearance_z,
                  self.tab_width, self.tab_height, self.tab_spacing):
            w.valueChanged.connect(self._edited)
        for w in (self.b_thickness, self.b_margin, self.b_dx, self.b_dy):
            w.valueChanged.connect(self._edited)
        for w in (self.rotate, self.b_rotate):
            w.currentIndexChanged.connect(self._edited)
        self.edit_tools.clicked.connect(self.editToolsRequested)
        self.edit_machines.clicked.connect(self.editMachinesRequested)
        self.machine.currentIndexChanged.connect(self._machine_chosen)
        self.tool.currentIndexChanged.connect(self._edited)
        self.tool.lineEdit().textEdited.connect(self._edited)
        for w in (self.spindle, self.pause, self.home, self.back, self.b_selected, self.bridges_on):
            w.toggled.connect(self._edited)
        self.bridges_on.toggled.connect(self._bridges_switched)
        self.end_code.textChanged.connect(self._edited)

    # --- proyecto <-> controles -----------------------------------------------------------------------------
    def bind(self, project: Project) -> None:
        self.project = project
        self.tool.blockSignals(True)
        self.tool.clear()
        for ident, t in project.tools.items():
            self.tool.addItem(tool_label(ident, t), ident)
        self.tool.blockSignals(False)
        self.machine.blockSignals(True)
        self.machine.clear()
        for ident, m in project.machines.items():
            self.machine.addItem(machine_label(ident, m), ident)
        self.machine.blockSignals(False)
        self.refresh()

    def set_board(self, number: Optional[int]) -> None:
        self.board = number
        self.refresh()

    def tool_value(self) -> str:
        i = self.tool.currentIndex()
        if i >= 0 and self.tool.currentText() == self.tool.itemText(i):
            return str(self.tool.itemData(i))
        return self.tool.currentText().strip()

    def refresh(self) -> None:
        """Pone en los controles lo que hay en el proyecto."""
        if self.project is None:
            return
        g = self.project.general
        self._loading = True
        try:
            ident = g.tool
            i = self.tool.findData(ident)
            if i >= 0:
                self.tool.setCurrentIndex(i)
            else:
                self.tool.setCurrentIndex(-1)
                self.tool.setEditText(ident)
            self.tool.lineEdit().setCursorPosition(0)
            self._show_machine()
            self.thickness.setValue(g.thickness)
            self.margin.setValue(g.margin)
            self.origin_margin.setValue(g.origin_margin)
            _set_combo(self.rotate, g.rotate)
            self.feed.setValue(g.feed)
            self.plunge.setValue(g.plunge)
            self.stepdown.setValue(g.stepdown)
            self.tab_width.setValue(g.tab_width)
            self.tab_height.setValue(g.tab_height)
            self.tab_spacing.setValue(g.tab_spacing)
            self.bridges_on.setChecked(g.bridges)
            self._sync_bridge_fields()
            self.safe_z.setValue(g.safe_z)
            self.clearance_z.setValue(g.clearance_z)
            self.spindle.setChecked(g.emit_spindle)
            self.pause.setChecked(g.pause_at_start)
            self.home.setChecked(g.home_at_end)
            self.back.setChecked(g.return_to_origin)
            if self.end_code.toPlainText() != g.end_code:
                self.end_code.setPlainText(g.end_code)
            n = self.board
            has_board = n is not None and n in self.project.overrides
            self.board_box.setEnabled(has_board)
            for w in (self.origin_margin, self.origin_margin_label):
                w.setEnabled(not has_board)
            self.board_box.setTitle(_("Tablero {n}", n=n) if has_board else _("Tablero (el dibujo no tiene tableros)"))
            o = self.project.overrides.get(n, BoardOverrides()) if has_board else BoardOverrides()
            self.b_selected.setChecked(o.selected)
            self.b_thickness.setValue(o.thickness)
            self.b_margin.setValue(o.margin)
            self.b_dx.setValue(o.offset[0] if o.offset else None)
            self.b_dy.setValue(o.offset[1] if o.offset else None)
            _set_combo(self.b_rotate, o.rotate or "")
        finally:
            self._loading = False
        self.refresh_hints()

    def _sync_bridge_fields(self) -> None:
        for w in (self.tab_width, self.tab_height, self.tab_spacing):
            w.setEnabled(self.bridges_on.isChecked())

    def _bridges_switched(self, on: bool) -> None:
        self._sync_bridge_fields()
        if not self._loading:
            self.bridgesToggled.emit(bool(on))

    def _show_machine(self) -> None:
        """Pone la maquina del proyecto en el desplegable (la unica ficha que haya, si no se ha elegido ninguna) y, debajo,
        lo que hay que saber de ella o por que no se puede generar."""
        p = self.project
        try:
            ident = p.machine_id()
        except ProjectError as e:
            self.machine.setCurrentIndex(-1)
            self.machine_note.setText(str(e))
            self.machine_note.setStyleSheet(f"color: {theme.ERROR};")
            return
        self.machine.setCurrentIndex(max(self.machine.findData(ident), 0))
        m = p.machines[ident]
        flags = [text for text, on in ((_("sin homing"), m.homing is False), (_("modo láser"), m.laser_mode is True)) if on]
        self.machine_note.setText(_("Recorrido {travel_text}", travel_text=travel_text(m)) + (f" · {', '.join(flags)}" if flags else ""))
        self.machine_note.setStyleSheet("")

    def machine_value(self) -> str:
        i = self.machine.currentIndex()
        return str(self.machine.itemData(i)) if i >= 0 else ""

    def _machine_chosen(self, _index: int) -> None:
        if self._loading or self.project is None or self.machine.currentIndex() < 0:
            return
        ident = self.machine_value()
        self.project.general = replace(self.project.general, machine=ident)
        self._show_machine()
        self.machineChosen.emit(ident)
        self.changed.emit()

    def refresh_hints(self) -> None:
        """Texto gris: lo que valdra cada casilla vacía."""
        if self.project is None:
            return
        hints = self.project.tool_hints()
        for name, edit in (("feed", self.feed), ("plunge", self.plunge), ("stepdown", self.stepdown)):
            edit.setPlaceholderText(hints.get(name, ""))
        g = self.project.general
        self.b_thickness.setHint(g.thickness)
        self.b_margin.setHint(g.margin)

    def _edited(self, *_args) -> None:
        if self._loading or self.project is None:
            return
        p = self.project
        old = p.general
        defaults = General()
        p.general = replace(
            old, tool=self.tool_value(), thickness=self.thickness.value(),
            margin=self.margin.value() if self.margin.value() is not None else 0.0, rotate=self.rotate.currentData(),
            origin_margin=self.origin_margin.value() if self.origin_margin.value() is not None else 0.0,
            feed=self.feed.value(), plunge=self.plunge.value(), stepdown=self.stepdown.value(),
            bridges=self.bridges_on.isChecked(),
            tab_width=self.tab_width.value() if self.tab_width.value() is not None else defaults.tab_width,
            tab_height=self.tab_height.value() if self.tab_height.value() is not None else defaults.tab_height,
            tab_spacing=self.tab_spacing.value() if self.tab_spacing.value() is not None else defaults.tab_spacing,
            safe_z=self.safe_z.value() if self.safe_z.value() is not None else old.safe_z,
            clearance_z=self.clearance_z.value() if self.clearance_z.value() is not None else old.clearance_z,
            emit_spindle=self.spindle.isChecked(), pause_at_start=self.pause.isChecked(),
            home_at_end=self.home.isChecked(), return_to_origin=self.back.isChecked(),
            end_code=self.end_code.toPlainText())
        n = self.board
        if n is not None and n in p.overrides:
            dx, dy = self.b_dx.value(), self.b_dy.value()
            p.overrides[n] = BoardOverrides(
                thickness=self.b_thickness.value(), margin=self.b_margin.value(),
                offset=None if dx is None and dy is None else (dx or 0.0, dy or 0.0),
                rotate=self.b_rotate.currentData() or None, selected=self.b_selected.isChecked())
        self.refresh_hints()
        self.changed.emit()
