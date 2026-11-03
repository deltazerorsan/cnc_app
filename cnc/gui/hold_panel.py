"""Pestaña Sujeción: que se usa para sujetar la plancha (tipo, alcance, márgenes) y la tabla de puntos."""
from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..holddown import FASTENERS, HoldSettings
from . import theme
from .widgets import OptionalNumber
from ..i18n import _

COL_N, COL_X, COL_Y, COL_STATE = range(4)
KIND_SHORT = {"clavo": _("clavo"), "tirafondo": _("tirafondo")}     # como se nombra el tipo en el texto gris de las casillas


class HoldPanel(QWidget):
    settingsChanged = Signal()
    resetRequested = Signal()
    removeRequested = Signal(int)
    pointEdited = Signal(int, float, float)
    marksRequested = Signal()
    rowSelected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False

        # --- lo que se usa
        self.kind = QComboBox()
        for key, f in FASTENERS.items():
            self.kind.addItem(f.label, key)
        self.reach = OptionalNumber(f"{HoldSettings().reach:g}")
        self.reach.setToolTip(_("Ningún sitio de la plancha a más de esta distancia de un punto"))
        self.min_gap = OptionalNumber(f"{HoldSettings().min_gap:g}")
        self.min_gap.setToolTip(_("La propuesta no pone dos puntos a menos de esta distancia: una plancha rígida no necesita "
                                "sujeciones tan juntas"))
        self.corner_clear = OptionalNumber(f"{HoldSettings().corner_clear:g}")
        self.corner_clear.setToolTip(_("La propuesta no pone puntos a menos de esta distancia de una esquina de la plancha: "
                                     "en la bancada serían siempre el mismo sitio y acabaría machacado"))
        self.cut_clear = OptionalNumber(f"{HoldSettings().cut_clear:g}")
        self.cut_clear.setToolTip(_("La propuesta pone los puntos a esta distancia o más del borde de corte, si hay sitio: un "
                                  "error pequeño al clavar no debe dar en el corte. Solo cede si no hay otra manera de "
                                  "llegar a toda la plancha. El margen al corte es lo mínimo absoluto; esto, lo cómodo"))
        self.grid = QCheckBox(_("En filas y columnas"))
        self.grid.setToolTip(_("En una plancha grande, los puntos propuestos van en filas y columnas regulares en vez de "
                             "repartidos al tresbolillo"))
        self.bottom_row = OptionalNumber(f"{HoldSettings().bottom_row:g}")
        self.bottom_row.setToolTip(_("Distancia del borde inferior de la plancha a la fila de abajo. 0 = sin fila pegada al "
                                   "borde: las filas van en el centro de sus celdas"))
        self.min_points = OptionalNumber(f"{HoldSettings().min_points:g}")
        self.min_points.setToolTip(_("Lo mínimo que propone en una plancha grande (más de 4 veces el alcance al cuadrado: "
                                   "600 × 600 mm con alcance 300); en una pequeña, como mucho 3. Un número entero"))
        self.kerf = OptionalNumber()
        self.kerf.setToolTip(_("Distancia mínima del borde de corte hasta el punto (vacío = la del tipo)"))
        self.edge = OptionalNumber()
        self.edge.setToolTip(_("Distancia mínima del borde de la plancha hasta el punto (vacío = la del tipo)"))
        self.mark_z = OptionalNumber(f"{HoldSettings().mark_z:g}")
        self.mark_z.setToolTip(_("Altura de la punta sobre la plancha en la pausa en la que se marca cada punto"))
        box = QGroupBox(_("Sujeción"))
        f = QFormLayout(box)
        f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        f.addRow(_("Se usa"), self.kind)
        f.addRow(_("Margen al corte (mm)"), self.kerf)
        f.addRow(_("Margen al borde (mm)"), self.edge)
        f.addRow(_("Altura de marca (mm)"), self.mark_z)
        box.setMaximumWidth(330)
        suggest = QGroupBox(_("Propuesta de puntos"))
        g = QFormLayout(suggest)
        g.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        g.addRow(_("Alcance (mm)"), self.reach)
        g.addRow(_("Mínimo de puntos"), self.min_points)
        g.addRow(_("Separación mínima (mm)"), self.min_gap)
        g.addRow(_("Distancia a esquinas (mm)"), self.corner_clear)
        g.addRow(_("Distancia a los cortes (mm)"), self.cut_clear)
        g.addRow(self.grid)
        g.addRow(_("Fila de abajo a (mm)"), self.bottom_row)
        suggest.setMaximumWidth(310)

        # --- puntos
        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.RichText)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([_("N.º"), _("X (mm)"), _("Y (mm)"), _("Estado")])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(COL_N, 46)
        self.table.setColumnWidth(COL_X, 90)
        self.table.setColumnWidth(COL_Y, 90)
        self.btn_reset = QPushButton(_("Proponer de nuevo"))
        self.btn_reset.setToolTip(_("Descarta lo editado a mano en este tablero y vuelve a la propuesta"))
        self.btn_remove = QPushButton(_("Quitar"))
        self.btn_marks = QPushButton(_("Generar marcas…"))
        self.btn_marks.setObjectName("primary")
        self.btn_marks.setToolTip(_("G-code corto que lleva la máquina a cada punto, de lejos a cerca (Y mayor primero), con una pausa\n"
                                  "para marcarlo"))
        buttons = QHBoxLayout()
        buttons.addWidget(self.btn_reset)
        buttons.addWidget(self.btn_remove)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_marks)
        right = QVBoxLayout()
        right.addWidget(self.info)
        right.addWidget(self.table, 1)
        right.addLayout(buttons)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.addWidget(box, 0, Qt.AlignTop)
        lay.addWidget(suggest, 0, Qt.AlignTop)
        lay.addLayout(right, 1)

        self.kind.currentIndexChanged.connect(self._settings_edited)
        for w in (self.reach, self.min_gap, self.corner_clear, self.cut_clear, self.min_points, self.bottom_row, self.kerf,
                  self.edge, self.mark_z):
            w.valueChanged.connect(self._settings_edited)
        self.grid.toggled.connect(self._settings_edited)
        self.table.itemChanged.connect(self._cell_changed)
        self.table.itemSelectionChanged.connect(self._selected)
        self.btn_reset.clicked.connect(self.resetRequested)
        self.btn_remove.clicked.connect(self._remove_clicked)
        self.btn_marks.clicked.connect(self.marksRequested)
        self.set_available(False, _("Abre un dibujo con tableros para calcular la sujeción."))

    # --- ajustes <-> controles ----------------------------------------------------------------------------------
    def bind(self, hold: HoldSettings) -> None:
        self._loading = True
        try:
            i = self.kind.findData(hold.kind)
            self.kind.setCurrentIndex(max(i, 0))
            self.reach.setValue(hold.reach)
            self.min_gap.setValue(hold.min_gap)
            self.corner_clear.setValue(hold.corner_clear)
            self.cut_clear.setValue(hold.cut_clear)
            self.min_points.setValue(hold.min_points)
            self.grid.setChecked(hold.grid)
            self.bottom_row.setValue(hold.bottom_row)
            self.kerf.setValue(hold.kerf_margin)
            self.edge.setValue(hold.edge_margin)
            self.mark_z.setValue(hold.mark_z)
        finally:
            self._loading = False
        self._hints()

    def settings(self) -> HoldSettings:
        """Lo que dicen los controles. Una casilla vacía vale lo que se ve en gris: el margen del tipo de sujeción, o el
        alcance, la separación, las esquinas, los cortes, el mínimo, la fila de abajo y la altura de marca de siempre."""
        d = HoldSettings()

        def number(box: OptionalNumber, default):
            v = box.value()
            return default if v is None else v

        return HoldSettings(kind=self.kind.currentData(), reach=number(self.reach, d.reach),
                            min_gap=number(self.min_gap, d.min_gap), corner_clear=number(self.corner_clear, d.corner_clear),
                            cut_clear=number(self.cut_clear, d.cut_clear),
                            min_points=int(round(number(self.min_points, d.min_points))), grid=self.grid.isChecked(),
                            bottom_row=number(self.bottom_row, d.bottom_row),
                            kerf_margin=self.kerf.value(), edge_margin=self.edge.value(),
                            mark_z=number(self.mark_z, d.mark_z))

    def _hints(self) -> None:
        f = FASTENERS[self.kind.currentData()]
        self.kerf.setHint(f.kerf_margin, f" ({KIND_SHORT.get(f.key, f.key)})")
        self.edge.setHint(f.edge_margin, f" ({KIND_SHORT.get(f.key, f.key)})")

    def _settings_edited(self, *_args) -> None:
        if self._loading:
            return
        self._hints()
        self.settingsChanged.emit()

    # --- estado ------------------------------------------------------------------------------------------------
    def set_available(self, available: bool, reason: str = "") -> None:
        """Sin trayectoria o sin tablero no se puede calcular: se dice por qué y se vacía la tabla."""
        for w in (self.btn_reset, self.btn_remove, self.btn_marks, self.table):
            w.setEnabled(available)
        if not available:
            self._fill([], [])
            self.info.setText(_("<span style='color:{MUTED}'>{reason}</span>", MUTED=theme.MUTED, reason=reason))

    def set_info(self, html: str) -> None:
        self.info.setText(html)

    def show_points(self, points: Sequence[tuple], checks: Sequence) -> None:
        for w in (self.btn_reset, self.btn_remove, self.btn_marks, self.table):
            w.setEnabled(True)
        self.btn_marks.setEnabled(bool(points))
        self.btn_remove.setEnabled(bool(points))
        self._fill(points, checks)

    def _fill(self, points, checks) -> None:
        keep = self.table.currentRow()
        self._loading = True
        try:
            self.table.setRowCount(len(points))
            for i, ((x, y), c) in enumerate(zip(points, checks)):
                n = QTableWidgetItem(str(i + 1))
                n.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                n.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(i, COL_N, n)
                for col, v in ((COL_X, x), (COL_Y, y)):
                    it = QTableWidgetItem(f"{v:.1f}")
                    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    self.table.setItem(i, col, it)
                if c.ok and c.warn:
                    text, color = _("⚠ libre, {warn}", warn=c.warn), theme.WARN
                else:
                    text, color = (_("✔ libre"), theme.OK) if c.ok else (f"✖ {c.reason}", theme.ERROR)
                state = QTableWidgetItem(text)
                state.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                state.setForeground(QBrush(QColor(color)))
                self.table.setItem(i, COL_STATE, state)
            if 0 <= keep < len(points):
                self.table.selectRow(keep)
        finally:
            self._loading = False

    def count(self) -> int:
        return self.table.rowCount()

    def current(self) -> int:
        return self.table.currentRow() if self.table.selectedItems() else -1

    def select(self, index: int) -> None:
        if 0 <= index < self.table.rowCount():
            self.table.selectRow(index)

    # --- ediciones --------------------------------------------------------------------------------------------
    def _cell_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() not in (COL_X, COL_Y):
            return
        row = item.row()
        try:
            x = float(self.table.item(row, COL_X).text().replace(",", "."))
            y = float(self.table.item(row, COL_Y).text().replace(",", "."))
        except (TypeError, ValueError, AttributeError):
            self.pointEdited.emit(-1, 0.0, 0.0)          # lo escrito no era un número: que la ventana repinte la tabla
            return
        self.pointEdited.emit(row, x, y)

    def _selected(self) -> None:
        if not self._loading:
            self.rowSelected.emit(self.current())

    def _remove_clicked(self) -> None:
        if self.current() >= 0:
            self.removeRequested.emit(self.current())
