"""Tabla "color -> operacion": que se hace con las formas de cada color del dibujo, y los grupos de vectores que se
eligen a mano (cada grupo es una fila mas de la tabla)."""
from __future__ import annotations

from functools import partial
from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QLabel, QMenu,
                               QPushButton, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

from ..oprows import ACTION_LABEL, ACTIONS, SIDES, OpRow
from .widgets import OptionalNumber, color_icon
from ..i18n import _

SIDE_LABEL = {"auto": _("Auto"), "fuera": _("Por fuera"), "dentro": _("Por dentro"), "linea": _("Sobre la línea")}
COLUMNS = (_("Color"), _("Formas"), _("Operación"), _("Lado"), _("Pasante"), _("Puentes"), _("Prof. (mm)"), _("Pasada (mm)"), _("Avance"),
           _("Bajada"), _("Stepover %"))
WIDTHS = (210, 60, 104, 124, 70, 74, 92, 100, 84, 84, 96)
TABS_TIP = (_("Puentes: deja sin cortar unos trozos de cada pieza o hueco (corte pasante) para que no se mueva al soltarse del "
              "tablero. El ancho, el alto y la separación se ponen en el panel «Puentes» de los ajustes. Los puentes puestos a mano "
              "con la herramienta Puente mandan sobre esta casilla."))
TABS_OFF_TIP = _("Los puentes están desactivados: marca «Poner puentes» en los ajustes del trabajo para usar esta casilla.")
SELECTION_HINT = _("Elige formas en el dibujo: clic; Ctrl o Mayús para varios; Mayús + arrastrar, un recuadro.")
NUMBER_FIELDS = (("depth", 6, _("Profundidad total en mm (solo si no es pasante), desde la cara superior")),
                 ("stepdown", 7, _("Profundidad de cada pasada en mm (en un taladro, el picoteo)")),
                 ("feed", 8, _("Avance de corte en mm/min")),
                 ("plunge", 9, _("Avance de bajada en mm/min")),
                 ("stepover", 10, _("Solape entre pasadas del vaciado, en % del diámetro de la fresa")))


class OpsPanel(QWidget):
    changed = Signal()                  # un valor cambio: hay que recalcular
    structureChanged = Signal()         # cambio el orden de las filas
    rowSelected = Signal(object)        # (trazo, relleno) de la fila elegida, o None
    loadRecipeRequested = Signal()
    saveRecipeRequested = Signal()
    resetRequested = Signal()
    pickAllRequested = Signal()
    pickNoneRequested = Signal()
    pickLevelRequested = Signal(int, bool)   # nivel de anidamiento (-1: lineas abiertas) y si se elige o se quita
    newGroupRequested = Signal()
    moveToGroupRequested = Signal(str)       # color-etiqueta del grupo al que se pasa lo elegido
    ungroupRequested = Signal()
    splitRequested = Signal()                # separar la fila elegida en interiores y exteriores
    sortRequested = Signal()                 # poner las filas en el orden correcto

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[OpRow] = []
        self._widgets: list[dict] = []
        self._hints: dict[str, str] = {}
        self._building = False
        self.sorter: Optional[Callable] = None      # lo pone la ventana: ordena las filas tras cambiar el tipo o el lado de una
        self.bridges_allowed: Callable[[], bool] = lambda: True      # lo pone la ventana: el interruptor general de los puentes

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.horizontalHeader().setStretchLastSection(False)
        for i, w in enumerate(WIDTHS):
            self.table.setColumnWidth(i, w)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.itemChanged.connect(self._item_edited)

        self.sel_label = QLabel(SELECTION_HINT)
        self.sel_label.setObjectName("muted")
        self.pick_btn = QToolButton()
        self.pick_btn.setText(_("Elegir"))
        self.pick_btn.setPopupMode(QToolButton.InstantPopup)
        self.pick_btn.setToolTip(_("Elegir formas por su nivel de anidamiento (0 es el exterior, 1 el que va dentro...)"))
        self.pick_menu = QMenu(self.pick_btn)
        self.pick_menu.aboutToShow.connect(self._build_pick_menu)
        self.pick_btn.setMenu(self.pick_menu)
        self._levels = []
        self.move_btn = QToolButton()
        self.move_btn.setText(_("Mover a grupo"))
        self.move_btn.setPopupMode(QToolButton.InstantPopup)
        self.move_btn.setToolTip(_("Lo elegido pasa a una fila propia de la tabla, con su operación, profundidad y avances"))
        self.move_btn.setEnabled(False)
        self.move_menu = QMenu(self.move_btn)
        self.move_menu.aboutToShow.connect(self._build_move_menu)
        self.move_btn.setMenu(self.move_menu)
        self.split_btn = QPushButton(_("Separar interior/exterior"))
        self.split_btn.setToolTip(_("La fila elegida (de Corte en Auto) pasa a una fila por cada clase de forma: interiores (por dentro), "
                                  "líneas abiertas y exteriores (por fuera), en el orden en que se cortan y con los mismos ajustes. "
                                  "Así se ven y se ajustan por separado."))
        self.split_btn.clicked.connect(self.splitRequested)
        self.set_selection("", 0, [])

        self.note = QLabel("")
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        self.warning = QLabel("")                    # el orden de las filas no es el correcto (en rojo, solo si pasa)
        self.warning.setObjectName("problem")
        self.warning.setWordWrap(True)
        self.warning.setVisible(False)
        self.sort_btn = QPushButton(_("Ordenar"))
        self.sort_btn.setToolTip(_("Pone las filas en el orden correcto: taladros, pozos, interiores, líneas y exteriores"))
        self.sort_btn.clicked.connect(self.sortRequested)
        self.up = QPushButton("▲")
        self.down = QPushButton("▼")
        for b, tip in ((self.up, _("Subir: se corta antes")), (self.down, _("Bajar: se corta después"))):
            b.setToolTip(tip)
            b.setFixedWidth(34)
        self.load_btn = QPushButton(_("Abrir receta…"))
        self.save_btn = QPushButton(_("Guardar receta…"))
        self.reset_btn = QPushButton(_("Restablecer"))
        self.reset_btn.setToolTip(_("Todas las filas vuelven a corte pasante normal (las formas siguen en sus grupos)"))
        self.up.clicked.connect(lambda: self._move(-1))
        self.down.clicked.connect(lambda: self._move(1))
        self.load_btn.clicked.connect(self.loadRecipeRequested)
        self.save_btn.clicked.connect(self.saveRecipeRequested)
        self.reset_btn.clicked.connect(self.resetRequested)

        sel = QHBoxLayout()
        sel.addWidget(self.pick_btn)
        sel.addWidget(self.move_btn)
        sel.addWidget(self.split_btn)
        sel.addSpacing(8)
        sel.addWidget(self.sel_label, 1)
        bar = QHBoxLayout()
        bar.addWidget(self.up)
        bar.addWidget(self.down)
        bar.addWidget(self.sort_btn)
        bar.addSpacing(8)
        bar.addWidget(self.note, 1)
        bar.addWidget(self.load_btn)
        bar.addWidget(self.save_btn)
        bar.addWidget(self.reset_btn)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(sel)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.warning)
        lay.addLayout(bar)

    # --- datos ------------------------------------------------------------------------------------------
    def bind(self, rows: list[OpRow], keep_key=None) -> None:
        """Muestra estas filas (la lista es la del proyecto: las ediciones la modifican en el sitio)."""
        self._rows = rows
        self.rebuild(keep_key)

    def set_hints(self, hints: dict[str, str]) -> None:
        """Texto gris de las casillas vacias: que valor se usara (el del trabajo o el de la ficha)."""
        self._hints = dict(hints)
        for w in self._widgets:
            self._apply_hints(w)

    def set_note(self, text: str) -> None:
        self.note.setText(text)

    def set_order_warning(self, text: str) -> None:
        """El aviso en rojo de que las filas no van en el orden correcto (vacio: se esconde)."""
        self.warning.setText(("⚠ " + text) if text else "")
        self.warning.setVisible(bool(text))

    # --- eleccion de vectores -----------------------------------------------------------------------------
    def set_selection(self, text: str, count: int, levels: list) -> None:
        """Lo que se muestra de la eleccion: el texto, si hay algo que mover y el menu Elegir (`levels` es una lista de
        (nivel, texto, marcado): marcado si todos los vectores de ese nivel estan elegidos). El menu se rehace al abrirlo."""
        self.sel_label.setText(text or SELECTION_HINT)
        self.move_btn.setEnabled(count > 0)
        self._levels = list(levels)

    def _build_pick_menu(self) -> None:
        self.pick_menu.clear()
        for label, signal in ((_("Todos"), self.pickAllRequested), (_("Ninguno"), self.pickNoneRequested)):
            self.pick_menu.addAction(label, signal.emit)
        if self._levels:
            self.pick_menu.addSeparator()
        for level, label, checked in self._levels:
            act = QAction(label, self.pick_menu)
            act.setCheckable(True)
            act.setChecked(checked)
            act.triggered.connect(partial(self.pickLevelRequested.emit, level, not checked))
            self.pick_menu.addAction(act)

    def _build_move_menu(self) -> None:
        self.move_menu.clear()
        groups = [r for r in self._rows if r.group]
        for r in groups:
            act = self.move_menu.addAction(color_icon(r.display_color), r.name or _("Grupo"))
            act.triggered.connect(partial(self.moveToGroupRequested.emit, r.stroke))
        if groups:
            self.move_menu.addSeparator()
        self.move_menu.addAction(_("Grupo nuevo…"), self.newGroupRequested.emit)
        self.move_menu.addSeparator()
        self.move_menu.addAction(_("Quitar del grupo (vuelve a su color)"), self.ungroupRequested.emit)

    def current_key(self) -> Optional[tuple]:
        i = self.table.currentRow()
        return self._rows[i].key if 0 <= i < len(self._rows) else None

    def select_key(self, key) -> None:
        for i, r in enumerate(self._rows):
            if r.key == key:
                self.table.selectRow(i)
                return
        self.table.clearSelection()

    # --- construccion -----------------------------------------------------------------------------------
    def rebuild(self, keep_key=None) -> None:
        self._building = True
        self.table.blockSignals(True)
        self.table.clearContents()
        self.table.setRowCount(len(self._rows))
        self._widgets = []
        for i, row in enumerate(self._rows):
            self._widgets.append(self._fill_row(i, row))
        self.table.blockSignals(False)
        self._building = False
        if keep_key is not None:
            self.select_key(keep_key)
        self.up.setEnabled(len(self._rows) > 1)
        self.down.setEnabled(len(self._rows) > 1)
        self.sort_btn.setEnabled(len(self._rows) > 1)
        self.split_btn.setEnabled(bool(self._rows))

    def _fill_row(self, i: int, row: OpRow) -> dict:
        t = self.table
        color = QTableWidgetItem(color_icon(row.display_color), row.title)
        if row.group:
            color.setToolTip(_("Grupo de formas «{name}» (color de trabajo {stroke}). Doble clic para cambiar el nombre.", name=row.name, stroke=row.stroke))
        else:
            color.setToolTip(row.color_label)
            color.setFlags(color.flags() & ~Qt.ItemIsEditable)
        count = QTableWidgetItem(str(row.count))
        count.setTextAlignment(Qt.AlignCenter)
        count.setFlags(count.flags() & ~Qt.ItemIsEditable)
        t.setItem(i, 0, color)
        t.setItem(i, 1, count)
        w: dict = {}
        action = QComboBox()
        for a in ACTIONS:
            action.addItem(ACTION_LABEL[a], a)
        action.setCurrentIndex(ACTIONS.index(row.action))
        side = QComboBox()
        for s in SIDES:
            side.addItem(SIDE_LABEL[s], s)
        side.setCurrentIndex(SIDES.index(row.side))
        through = QCheckBox()
        through.setChecked(row.through)
        through.setToolTip(_("Pasante: corta todo el grosor del material más el margen del sufridor"))
        holder = QWidget()
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setAlignment(Qt.AlignCenter)
        hl.addWidget(through)
        tabs = QCheckBox()
        tabs.setChecked(row.tabs)
        tabs.setToolTip(TABS_TIP)
        tabs_holder = QWidget()
        tl = QHBoxLayout(tabs_holder)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setAlignment(Qt.AlignCenter)
        tl.addWidget(tabs)
        t.setCellWidget(i, 2, action)
        t.setCellWidget(i, 3, side)
        t.setCellWidget(i, 4, holder)
        t.setCellWidget(i, 5, tabs_holder)
        w.update(action=action, side=side, through=through, tabs=tabs)
        for name, col, tip in NUMBER_FIELDS:
            edit = OptionalNumber()
            edit.setToolTip(tip)
            edit.setValue(getattr(row, name))
            edit.valueChanged.connect(partial(self._number_edited, row, name, edit))
            t.setCellWidget(i, col, edit)
            w[name] = edit
        action.currentIndexChanged.connect(partial(self._action_changed, row, w))
        side.currentIndexChanged.connect(partial(self._side_changed, row, w))
        through.toggled.connect(partial(self._through_toggled, row, w))
        tabs.toggled.connect(partial(self._tabs_toggled, row))
        self._apply_enabled(row, w)
        self._apply_hints(w)
        return w

    # --- estado de cada celda -----------------------------------------------------------------------------
    def refresh_enabled(self) -> None:
        """Vuelve a poner qué casillas se pueden tocar (el interruptor general de los puentes las apaga todas las de puentes)."""
        for row, w in zip(self._rows, self._widgets):
            self._apply_enabled(row, w)

    def _apply_enabled(self, row: OpRow, w: dict) -> None:
        active = row.active
        allowed = bool(self.bridges_allowed())
        w["side"].setEnabled(row.action == "corte")
        w["through"].setEnabled(active)
        w["tabs"].setEnabled(allowed and row.action == "corte" and row.through and row.side in ("auto", "fuera", "dentro"))
        w["tabs"].setToolTip(TABS_TIP if allowed else TABS_OFF_TIP)
        w["depth"].setEnabled(active and not row.through)
        for name in ("stepdown", "feed", "plunge"):
            w[name].setEnabled(active)
        w["stepover"].setEnabled(row.action == "vaciado")

    def _apply_hints(self, w: dict) -> None:
        for name in ("stepdown", "feed", "plunge", "stepover"):
            w[name].setPlaceholderText(self._hints.get(name, ""))
        w["depth"].setPlaceholderText("obligatoria" if not w["through"].isChecked() else "")

    # --- ediciones ------------------------------------------------------------------------------------------
    def _action_changed(self, row: OpRow, w: dict, _index: int) -> None:
        row.action = w["action"].currentData()
        self._apply_enabled(row, w)
        self._resort(row)
        self._emit_changed()

    def _side_changed(self, row: OpRow, w: dict, _index: int) -> None:
        row.side = w["side"].currentData()
        self._apply_enabled(row, w)
        self._resort(row)
        self._emit_changed()

    def _resort(self, row: OpRow) -> None:
        """Cambiar el tipo o el lado de una fila cambia cuando se corta: si el resto de la tabla iba bien, la fila se coloca
        sola donde toca (y si alguien movio las demas a mano, no se toca nada y el aviso de arriba lo dice)."""
        if self.sorter is not None and not self._building and self.sorter(row):
            self.rebuild(row.key)

    def _through_toggled(self, row: OpRow, w: dict, on: bool) -> None:
        row.through = bool(on)
        self._apply_enabled(row, w)
        self._apply_hints(w)
        if not on:
            w["depth"].setFocus()
        self._emit_changed()

    def _tabs_toggled(self, row: OpRow, on: bool) -> None:
        row.tabs = bool(on)
        self._emit_changed()

    def _number_edited(self, row: OpRow, name: str, edit: OptionalNumber) -> None:
        setattr(row, name, edit.value())
        self._emit_changed()

    def _item_edited(self, item: QTableWidgetItem) -> None:
        """El nombre de un grupo se cambia en su celda (doble clic); uno vacio no vale y vuelve el de antes."""
        i = item.row()
        if self._building or item.column() != 0 or not 0 <= i < len(self._rows) or not self._rows[i].group:
            return
        row = self._rows[i]
        name = item.text().strip()
        self.table.blockSignals(True)
        item.setText(name or row.name)
        self.table.blockSignals(False)
        if name and name != row.name:
            row.name = name
            item.setToolTip(_("Grupo de formas «{name}» (color de trabajo {stroke}). Doble clic para cambiar el nombre.", name=name, stroke=row.stroke))
            self._emit_changed()

    def _emit_changed(self) -> None:
        if not self._building:
            self.changed.emit()

    def _selection_changed(self) -> None:
        if not self._building:
            self.rowSelected.emit(self.current_key())

    def _move(self, step: int) -> None:
        i = self.table.currentRow()
        j = i + step
        if i < 0:
            i, j = (0, 1) if step > 0 else (len(self._rows) - 1, len(self._rows) - 2)
        if not (0 <= i < len(self._rows) and 0 <= j < len(self._rows)):
            return
        key = self._rows[i].key
        self._rows[i], self._rows[j] = self._rows[j], self._rows[i]
        self.rebuild(key)
        self.structureChanged.emit()
        self.changed.emit()
