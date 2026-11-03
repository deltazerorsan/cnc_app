"""Menu Dibujo de la ventana: formas basicas, mover/girar/copiar, desfase y unir/restar, con deshacer y rehacer.

Es parte de MainWindow (se mezcla con ella): aqui estan los dialogos con medidas y lo que hace la ventana tras cada
edicion; lo que de verdad cambia el dibujo esta en `cnc.drawing_edit` y `Project.edit` (sin Qt, con sus pruebas).
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QMessageBox, QToolButton

from .. import bridge_tools as bt
from .. import draw_tools as dt
from .. import drawing_edit as de
from ..pipeline import Message
from ..project import ProjectError
from . import align_icons
from .draw_dialogs import Check, Choice, Colour, Num, Points, ToolDialog
from .props_bar import NO_SELECTION
from ..i18n import _

ZERO_TOLERANCE = 0.005                  # mm: un tablero con la esquina a menos de esto del 0,0 ya esta en el cero
DEFAULT_CENTER = (60.0, 40.0)          # donde se proponen las formas nuevas si el dibujo esta vacio
TOOL_TIPS = {
    "elegir": _("Elegir formas: clic, Ctrl+clic o recuadro con Mayús. Arrastrar desplaza la vista."),
    "rectangulo": _("Dibujar un rectángulo con el ratón (dos esquinas). Mayús: cuadrado."),
    "circulo": _("Dibujar un círculo con el ratón (centro y radio)."),
    "elipse": _("Dibujar una elipse con el ratón (las dos esquinas de su caja). Mayús: círculo."),
    "poligono": _("Dibujar un polígono regular con el ratón (centro y un vértice). Los lados se dan en las opciones."),
    "ranura": _("Dibujar una ranura con el ratón (los centros de sus dos extremos). El ancho se da en las opciones."),
    "linea": _("Dibujar una línea o polilínea con el ratón. Doble clic o Intro acaba; clic en el inicio cierra el contorno."),
    "mover": _("Mover lo elegido con el ratón: punto de partida y punto de llegada (se pega a esquinas y centros)."),
    "puente": _("Poner puentes con el ratón: clic en un contorno; arrastra un puente para deslizarlo o tira de un extremo para "
                "cambiar su ancho; Supr lo quita."),
}


class DrawActions:
    """Mezcla para MainWindow: necesita `project`, `selection`, `current`, `canvas`, `statusBar()`, `_error` y las
    funciones de repintado de la ventana."""

    # --- herramientas del raton ---------------------------------------------------------------------------------------
    def _build_tool_row(self) -> QHBoxLayout:
        """La tira de herramientas sobre el lienzo; las mismas acciones salen en el menu Dibujo."""
        tools = self.canvas.tools
        tools.stroke_of = lambda: self.project.draw_color
        tools.changed.connect(self._tool_changed)
        tools.status.connect(lambda text: self.statusBar().showMessage(text))
        tools.finished.connect(self._tool_finished)
        tools.bridge.problem_of = lambda i: self.project.bridge_problem(i)
        tools.bridge.size_of = lambda: (self.project.general.tab_width or 6.0, self.project.general.tab_height or 2.0)
        tools.bridge.edited.connect(self._bridges_edited)
        tools.bridge.selected.connect(self._bridge_selected)
        group = QActionGroup(self)
        group.setExclusive(True)
        self._tool_actions = {}
        row = QHBoxLayout()
        row.setContentsMargins(8, 0, 8, 0)
        label = QLabel(_("Con el ratón:"))
        label.setObjectName("muted")
        row.addWidget(label)
        for name in dt.TOOLS:
            a = QAction(dt.LABELS[name], self)
            a.setCheckable(True)
            a.triggered.connect(lambda _checked=False, n=name: self.choose_tool(n))
            key = next(k for k, v in dt.KEYS.items() if v == name)
            a.setToolTip(f"{dt.LABELS[name]} ({key.upper()}): {TOOL_TIPS[name]}")
            group.addAction(a)
            self._tool_actions[name] = a
            button = QToolButton()
            button.setDefaultAction(a)
            row.addWidget(button)
        self._tool_actions["elegir"].setChecked(True)
        options = QToolButton(text=_("Opciones…"))
        options.setToolTip(_("Radio de las esquinas, lados del polígono, ancho de la ranura, cuadrícula, imán y color"))
        options.clicked.connect(self.draw_mouse_options)
        row.addWidget(options)
        row.addStretch(1)
        return row

    def choose_tool(self, name: str) -> None:
        self.canvas.tools.set_tool(name)
        self._tool_actions[self.canvas.tools.name].setChecked(True)          # si no se pudo, vuelve a marcarse la de antes

    def _tool_changed(self, name: str) -> None:
        self._tool_actions[name].setChecked(True)
        bridges = name == "puente"                           # con la herramienta Puente, su tira ocupa el sitio de las medidas
        self.props.setVisible(not bridges)
        self.bridge_bar.setVisible(bridges)
        if bridges:
            g = self.project.general
            self.bridge_bar.set_defaults(g.tab_width or 6.0, g.tab_height or 2.0)

    # --- puentes ---------------------------------------------------------------------------------------------------------
    def _bridges_edited(self, index: int, tabs, at: float) -> None:
        """El raton (o la tira) ha dejado esta lista de puentes en una forma: se hace como cualquier edicion, con deshacer."""
        if self.apply_edit(de.tabs_edit([index], tabs), fit=False):
            self.canvas.tools.bridge.select(index, at)

    def _bridge_selected(self, found) -> None:
        if found is None:
            self.bridge_bar.show_bridge(None)
            return
        index, __, tab, manual = found
        self.bridge_bar.show_bridge(bt.describe(self.project.drawing.shapes[index].shape, tab), manual)

    def _bridge_numbers_applied(self, position: float, width: float, height: float) -> None:
        cur = self.canvas.tools.bridge.current()
        if cur is None:
            return
        index, k, __, __ = cur
        try:
            tab = bt.from_numbers(self.project.drawing.shapes[index].shape, position, width, height)
            tabs, new = bt.replaced(self.canvas.tools.bridge.tabs_of(index), k, tab)
        except ValueError as e:
            self.statusBar().showMessage(str(e)[:1].upper() + str(e)[1:])
            self._bridge_selected(cur)
            return
        self._bridges_edited(index, tabs, tabs[new].at)

    def _bridge_current_to_auto(self) -> None:
        cur = self.canvas.tools.bridge.current()
        if cur is not None:
            self.bridges_automatic([cur[0]])

    def bridges_automatic(self, indices=None) -> None:
        """Las formas (las elegidas, por omisión) vuelven a llevar los puentes que propone el programa."""
        picked = self._edit_selection() if indices is None else list(indices)
        shapes = self.project.shapes()
        by_hand = [i for i in picked if 0 <= i < len(shapes) and shapes[i].tabs is not None]
        if not by_hand:
            self.statusBar().showMessage(_("Ninguna de las formas tiene puentes puestos a mano: ya llevan los automáticos."))
            return
        if self.apply_edit(de.tabs_edit(by_hand, None), fit=False):
            self.canvas.tools.bridge.select(-1, -1.0)
            self.statusBar().showMessage(_("{count} forma(s) vuelven a los puentes automáticos.", count=len(by_hand)))

    def bridges_none(self) -> None:
        """Quita todos los puentes de las formas elegidas (se queda así: sin puentes, aunque la fila los pida)."""
        shapes = self.project.shapes()
        picked = [i for i in self._edit_selection() if not self.project.bridge_problem(i)]
        if not picked:
            self.statusBar().showMessage(_("Elige formas cerradas de una fila de corte pasante para quitarles los puentes."))
            return
        if self.apply_edit(de.tabs_edit(picked, ()), fit=False):
            self.statusBar().showMessage(_("{count} forma(s) se cortan sin puentes.", count=len(picked)))

    def _tool_finished(self, done) -> None:
        """El raton ha terminado una forma o un movimiento: se hace como cualquier otra edicion (con deshacer)."""
        if done.kind == "mover":
            self.apply_edit(de.move_edit(done.indices, done.dx, done.dy), fit=False)
        else:
            self.apply_edit(de.add_edit([done.shape]), fit=False)

    def escape(self) -> None:
        """Esc: primero cancela lo que se dibuja con el raton, luego deja la herramienta y por fin quita la eleccion."""
        if not self.canvas.tools.escape():
            self.pick_none()

    def draw_mouse_options(self) -> None:
        o = self.canvas.tools.options
        v = self._ask(_("Opciones del ratón"), [
            Num("radius", _("Radio de las esquinas del rectángulo (mm)"), o.radius, negative=False),
            Num("sides", _("Lados del polígono"), o.sides, integer=True, negative=False),
            Num("width", _("Ancho de la ranura (mm)"), o.slot_width, negative=False),
            Num("grid", _("Cuadrícula (mm; 0 = sin cuadrícula)"), o.grid, negative=False),
            Check("snap", _("Imán a esquinas, centros y puntos medios"), o.snap), self._color_field()],
            _("Lo que no se da con el ratón. La cuadrícula redondea los puntos libres; el imán pega el cursor a las esquinas, "
            "centros y puntos medios de las formas (Ctrl lo apaga un momento)."))
        if not v:
            return
        try:
            options = dt.Options(v["radius"], v["sides"], v["width"], v["grid"], v["snap"])
        except ValueError as e:
            self._error(_("Opciones del ratón"), str(e))
            return
        self.project.draw_color = v["color"]
        self.canvas.tools.configure(options)

    # --- menu -------------------------------------------------------------------------------------------------------
    def _build_draw_menu(self) -> None:
        menu = self.menuBar().addMenu(_("&Dibujo"))

        def act(m, text, slot, shortcut=None):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.triggered.connect(lambda _checked=False, s=slot: s())
            m.addAction(a)
            return a

        self.act_new_drawing = act(menu, _("Nuevo dibujo vacío"), self.new_drawing, "Ctrl+N")
        mouse = menu.addMenu(_("Dibujar con el ratón"))
        for name in dt.TOOLS:
            mouse.addAction(self._tool_actions[name])
        mouse.addSeparator()
        act(mouse, _("Opciones del ratón…"), self.draw_mouse_options)
        menu.addSeparator()
        shapes = menu.addMenu(_("Dibujar forma"))
        self.act_rectangle = act(shapes, _("Rectángulo…"), self.draw_rectangle)
        self.act_circle = act(shapes, _("Círculo…"), self.draw_circle)
        self.act_ellipse = act(shapes, _("Elipse…"), self.draw_ellipse)
        self.act_polygon = act(shapes, _("Polígono regular…"), self.draw_polygon)
        self.act_slot = act(shapes, _("Ranura…"), self.draw_slot)
        self.act_polyline = act(shapes, _("Línea o polilínea…"), self.draw_polyline)
        menu.addSeparator()
        self.act_move = act(menu, _("Mover…"), self.draw_move)
        self.act_place = act(menu, _("Colocar en…"), self.draw_place)
        self.act_rotate = act(menu, _("Girar…"), self.draw_rotate)
        self.act_scale = act(menu, _("Cambiar tamaño…"), self.draw_scale)
        self.act_mirror = act(menu, _("Reflejar…"), self.draw_mirror)
        self.act_array = act(menu, _("Copiar en matriz…"), self.draw_array)
        self.act_polar = act(menu, _("Copiar en círculo…"), self.draw_polar)
        align = menu.addMenu(_("Alinear"))
        self._align_actions = []
        for side in align_icons.ORDER:
            a = QAction(align_icons.LABELS[side], self)
            a.setIcon(align_icons.align_icon(side))
            a.setData(side)
            a.setIconVisibleInMenu(True)
            a.setToolTip(align_icons.TIPS[side])
            a.triggered.connect(lambda _checked=False, sd=side: self.draw_align(sd))
            align.addAction(a)
            self._align_actions.append(a)
        self.props.set_align_actions(self._align_actions)                  # los mismos botones, en la tira de medidas
        menu.addSeparator()
        self.act_offset = act(menu, _("Desfase (offset)…"), self.draw_offset)
        self.act_union = act(menu, _("Unir formas"), self.draw_union)
        self.act_subtract = act(menu, _("Restar formas…"), self.draw_subtract)
        self.act_intersect = act(menu, _("Intersección de formas"), self.draw_intersect)
        self.act_close = act(menu, _("Cerrar contorno"), self.draw_close)
        menu.addSeparator()
        bridges = menu.addMenu(_("Puentes de lo elegido"))
        self.act_bridges_auto = act(bridges, _("Automáticos (los propone la aplicación)"), self.bridges_automatic)
        self.act_bridges_none = act(bridges, _("Quitar todos (cortar sin puentes)"), self.bridges_none)
        self._selection_actions = [self.act_move, self.act_place, self.act_rotate, self.act_scale, self.act_mirror,
                                   self.act_array, self.act_polar, self.act_offset, self.act_close, self.act_delete,
                                   self.act_bridges_auto, self.act_bridges_none]
        self._pair_actions = [self.act_union, self.act_subtract, self.act_intersect, *self._align_actions]
        self._update_draw_actions()

    def _update_draw_actions(self) -> None:
        p = self.project
        has = p.drawing is not None
        some = has and bool(self._edit_selection())
        pair = has and len(self._edit_selection()) >= 2
        for a in self._selection_actions:
            a.setEnabled(some)
        for a in self._pair_actions:
            a.setEnabled(pair)
        for a in (self.act_rectangle, self.act_circle, self.act_ellipse, self.act_polygon, self.act_slot, self.act_polyline):
            a.setEnabled(has)
        self.act_pick_board.setEnabled(has and self._current_board() is not None)
        for a in self._tool_actions.values():
            a.setEnabled(has and self.view == "drawing")
        self.act_undo.setEnabled(bool(p.undo_label))
        self.act_redo.setEnabled(bool(p.redo_label))
        self.act_undo.setText(_("Deshacer: {undo_label}", undo_label=p.undo_label) if p.undo_label else _("Deshacer"))
        self.act_redo.setText(_("Rehacer: {redo_label}", redo_label=p.redo_label) if p.redo_label else _("Rehacer"))

    def draw_align(self, side: str) -> None:
        """Alinea las formas elegidas (dos o mas) con la caja de todas ellas, por una cara o por el centro."""
        picked = self._need_selection(2)
        if picked is None:
            return
        self.apply_edit(de.align_edit(picked, side), fit=False)

    # --- la tira de medidas ----------------------------------------------------------------------------------------------
    def _update_props(self) -> None:
        """Las medidas de lo elegido en la tira de sobre el lienzo (o por que no hay)."""
        d = self.project.drawing
        picked = self._edit_selection() if d is not None and self.view == "drawing" else []
        m = de.measures_of(d.shapes, picked) if picked else None
        if d is None:
            hint = ""
        elif self.view != "drawing":
            hint = _("Las medidas de las formas se ven y se cambian en la vista Dibujo.")
        else:
            hint = NO_SELECTION
        self.props.show_measures(m, picked if m else [], hint)

    def _props_applied(self, indices, x: float, y: float, width: float, height: float) -> None:
        """Se ha escrito una medida en la tira: se hace como cualquier edicion (con deshacer); si no se puede, la tira
        vuelve a mostrar lo que vale de verdad."""
        if not self.apply_edit(de.measures_edit(indices, x, y, width, height), fit=False):
            self._update_props()

    # --- que hay elegido y donde esta ----------------------------------------------------------------------------------
    def _edit_selection(self) -> list[int]:
        n = len(self.project.drawing.shapes) if self.project.drawing else 0
        return sorted(i for i in self.selection if 0 <= i < n)

    def _current_board(self):
        d = self.project.drawing
        if d is None or self.current is None:
            return None
        return next((b for b in d.layout.boards if b.number == self.current), None)

    def _selection_bounds(self) -> Optional[tuple]:
        d = self.project.drawing
        picked = self._edit_selection()
        if d is None or not picked:
            return None
        bs = [d.shapes[i].shape.bounds for i in picked]
        return min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)

    def _center(self) -> tuple:
        """Donde se proponen las formas nuevas: el centro del tablero que se ve (o del dibujo)."""
        try:
            b = self.project.unit(self.current).bounds
        except ProjectError:
            return DEFAULT_CENTER
        if b[2] - b[0] <= 0 or b[3] - b[1] <= 0:
            return DEFAULT_CENTER
        return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2

    def _colors(self) -> list[str]:
        """Los colores de trazo que ya usa el dibujo (sin las etiquetas de grupo), para elegir el de la forma nueva."""
        seen: list[str] = []
        for r in self.project.rows:
            c = None if r.group else (r.stroke or r.fill)
            if c and c not in seen:
                seen.append(c)
        return seen

    def _ask(self, title: str, fields, intro: str = "", ok: str = _("Aceptar"), hook=None) -> Optional[dict]:
        dialog = ToolDialog(title, fields, intro, self, ok)
        dialog.hook = hook
        if dialog.exec() != QDialog.Accepted:
            return None
        return dialog.values()

    def _need_selection(self, minimum: int = 1) -> Optional[list[int]]:
        picked = self._edit_selection()
        if len(picked) < minimum:
            self.statusBar().showMessage(
                _("Elige primero alguna forma (clic, Ctrl+clic o recuadro con Mayús).") if minimum == 1 else
                _("Elige primero al menos {minimum} formas (clic, Ctrl+clic o recuadro con Mayús).", minimum=minimum))
            return None
        return picked

    def _size_text(self) -> str:
        b = self._selection_bounds()
        if b is None:
            return ""
        return _("Lo elegido mide {v:.1f} x {v2:.1f} mm y empieza en X {b:.1f}, Y {b2:.1f}.", v=b[2] - b[0], v2=b[3] - b[1], b=b[0], b2=b[1])

    # --- aplicar una edicion --------------------------------------------------------------------------------------------
    def apply_edit(self, edit: dict, fit: Optional[bool] = None) -> bool:
        """Hace la edicion y deja la ventana al dia. False (con el motivo a la vista) si no se pudo. `fit`: ajustar la vista
        a lo que hay despues (por omision, solo si el dibujo estaba vacio); con el raton no, porque ya se esta mirando."""
        p = self.project
        if p.drawing is None:
            return False
        before = len(p.drawing.shapes)
        old_boards = {b.index for b in p.drawing.layout.boards}
        try:
            result = p.edit(edit)
        except ProjectError as e:
            self._error(de.edit_label(edit), str(e))
            return False
        self._dirty = True
        became = self._new_boards(result, old_boards)
        notes = [Message(n, "warning") for n in result.notes]
        notes += self._edit_notes(result, became)
        self.selection = set(result.selected)
        self._drawing_changed(notes, fit=before == 0 if fit is None else fit)
        text = de.edit_label(edit) + (_(": {count} forma(s)", count=len(result.selected)) if result.selected else "")
        self.statusBar().showMessage(text + (" · " + notes[0].text if notes else ""))
        if not old_boards and len(became) == 1 and len(p.drawing.layout.boards) == 1:
            self._offer_board_at_zero(became[0])
        return True

    def _new_boards(self, result, old_boards: set) -> list:
        """Los tableros que la edicion hace aparecer (rectangulos que antes no lo eran)."""
        mapped = {result.index_map().get(o) for o in old_boards}
        return [b for b in self.project.drawing.layout.boards if b.index not in mapped]

    def _offer_board_at_zero(self, board) -> None:
        """El primer tablero de un dibujo que no lo traia (se ha dibujado): propone llevarlo al 0,0, como queda el cero de la
        maquina. Si se acepta se mueve todo el dibujo con el, para que nada cambie de sitio respecto a el; si no, se queda."""
        x, y = board.bounds[0], board.bounds[1]
        if abs(x) < ZERO_TOLERANCE and abs(y) < ZERO_TOLERANCE:
            return
        if not self._ask_board_at_zero(x, y):
            return
        everything = list(range(len(self.project.drawing.shapes)))
        if self.apply_edit(de.move_edit(everything, -x, -y), fit=True):
            self.statusBar().showMessage(_("Tablero llevado al 0,0: Deshacer lo devuelve a donde estaba."))

    def _ask_board_at_zero(self, x: float, y: float) -> bool:
        if self.settings is None:                               # en las pruebas no hay nadie para contestar
            return False
        return QMessageBox.question(
            self, _("Tablero nuevo"),
            _("Ese rectángulo se toma como tablero (el material) y su esquina inferior izquierda está en X {x:.1f}, Y {y:.1f}. "
              "¿Lo llevo al 0,0? Todo el dibujo se mueve con él; si no, se queda donde lo dibujaste.", x=x, y=y),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes) == QMessageBox.Yes

    def _edit_notes(self, result, became: list) -> list:
        """Avisos de lo que la edicion provoca en los tableros: una forma nueva que queda fuera de todos, o un rectangulo
        que de pronto es un tablero."""
        d = self.project.drawing
        lay = d.layout
        notes = []
        fresh = [i for i, src in enumerate(result.origin) if src is None]
        if lay.boards:
            outside = [i for i in fresh if i in lay.loose]
            if outside:
                notes.append(Message(_("{count} forma(s) nueva(s) quedan fuera de todo tablero: no se cortan hasta que estén dentro de "
                                       "uno.", count=len(outside)), "warning"))
        if became:
            notes.append(Message(_("Un rectángulo con formas dentro se toma como tablero (el material): no se corta y el cero "
                                 "va a su esquina inferior izquierda. Si es una pieza, desmarca «Detectar tableros»."),
                                 "info"))
        return notes

    def _drawing_changed(self, notes=(), fit: bool = False) -> None:
        """La ventana al dia tras cambiar las formas: tabla, tableros (el que se veia sigue si existe), lienzo y calculo."""
        keep = self.current
        self._extra_notes = list(notes)
        self.ops.bind(self.project.rows)
        self._fill_boards()
        if keep is not None and keep != self.current and any(u.number == keep for u in self.project.units()):
            self._select_board_number(keep)
        self._hold_cache = None
        self._update_selection_ui()
        self.refresh_all(fit=fit)

    def _select_board_number(self, number: int) -> None:
        for i in range(self.boards.count()):
            if self.boards.item(i).data(Qt.UserRole) == number:
                self.boards.blockSignals(True)
                self.boards.setCurrentRow(i)
                self.boards.blockSignals(False)
                self.current = number
                self.job.set_board(number)
                return

    def undo(self) -> None:
        self._history_step(self.project.undo, _("Deshecho"))

    def redo(self) -> None:
        self._history_step(self.project.redo, _("Rehecho"))

    def _history_step(self, step, word: str) -> None:
        if self.project.drawing is None:
            return
        before = len(self.project.drawing.shapes)
        label = step()
        if not label:
            return
        self._dirty = True
        self.selection = set()
        self._drawing_changed(fit=before == 0 and bool(self.project.drawing.shapes))
        self.statusBar().showMessage(f"{word}: {label}")

    # --- cambios sin guardar -------------------------------------------------------------------------------------------
    def _confirm_discard(self) -> bool:
        """True si se puede abrir otra cosa encima: no hay cambios de dibujo sin guardar, o el usuario los descarta."""
        return True if not self._dirty else self._ask_discard()

    def _ask_discard(self) -> bool:
        if self.settings is None:                               # en las pruebas no hay nadie para contestar
            return True
        return QMessageBox.question(
            self, _("Cambios sin guardar"), _("Hay cambios de dibujo que no están en el trabajo guardado. ¿Descartarlos?"),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def _ask_save_before_close(self) -> str:
        """'save', 'discard' o 'cancel'."""
        if self.settings is None:
            return "discard"
        answer = QMessageBox.question(
            self, _("Cambios sin guardar"), _("Hay cambios de dibujo que no están en el trabajo guardado. ¿Guardarlos antes de salir?"),
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        return {QMessageBox.Save: "save", QMessageBox.Discard: "discard"}.get(answer, "cancel")

    def _can_close(self) -> bool:
        if not self._dirty:
            return True
        answer = self._ask_save_before_close()
        if answer == "save":
            self.save_job_dialog()
            return not self._dirty                              # si se cancelo el guardado, no se sale
        return answer == "discard"

    # --- dibujo nuevo y borrar -----------------------------------------------------------------------------------------
    def new_drawing(self) -> None:
        if not self._confirm_discard():
            return
        self.project.new_drawing(self._folder())
        self.job_path = None
        self._after_drawing_loaded()
        self.statusBar().showMessage(_("Dibujo nuevo y vacío: usa el menú Dibujo para añadir formas."))

    def draw_delete(self) -> None:
        picked = self._need_selection()
        if picked:
            self.apply_edit(de.delete_edit(picked))

    def pick_current_board(self) -> None:
        b = self._current_board()
        if b is None:
            return
        self.selection = {b.index, *b.contents}
        self._selection_changed()
        self.statusBar().showMessage(_("Tablero {number} elegido con su recuadro: {count} forma(s).", number=b.number, count=len(b.contents)))

    # --- formas nuevas ---------------------------------------------------------------------------------------------------
    def _color_field(self) -> Colour:
        return Colour("color", _("Color del trazo"), self._colors(), self.project.draw_color)

    def _add_shape(self, make, color: Optional[str]) -> None:
        try:
            shape = make()
        except de.EditError as e:
            self._error(_("Dibujar forma"), str(e))
            return
        if color:
            self.project.draw_color = color
        self.apply_edit(de.add_edit([shape]))

    def draw_rectangle(self) -> None:
        cx, cy = self._center()
        v = self._ask(_("Rectángulo"), [
            Num("x", _("Esquina inferior izquierda, X (mm)"), cx - 25), Num("y", _("Esquina inferior izquierda, Y (mm)"), cy - 15),
            Num("w", _("Ancho (mm)"), 50, negative=False), Num("h", _("Alto (mm)"), 30, negative=False),
            Num("r", _("Radio de las esquinas (mm)"), 0, negative=False), self._color_field()],
            _("Medidas en mm sobre el dibujo (X hacia la derecha, Y hacia arriba)."))
        if v:
            self._add_shape(lambda: de.make_rectangle(v["x"], v["y"], v["w"], v["h"], v["r"], stroke=v["color"]), v["color"])

    def draw_circle(self) -> None:
        cx, cy = self._center()
        v = self._ask(_("Círculo"), [Num("x", _("Centro, X (mm)"), cx), Num("y", _("Centro, Y (mm)"), cy),
                                  Num("d", _("Diámetro (mm)"), 20, negative=False), self._color_field()],
                      _("Medidas en mm sobre el dibujo (X hacia la derecha, Y hacia arriba)."))
        if v:
            self._add_shape(lambda: de.make_circle(v["x"], v["y"], v["d"] / 2, stroke=v["color"]), v["color"])

    def draw_ellipse(self) -> None:
        cx, cy = self._center()
        v = self._ask(_("Elipse"), [Num("x", _("Centro, X (mm)"), cx), Num("y", _("Centro, Y (mm)"), cy),
                                 Num("w", _("Ancho total (mm)"), 40, negative=False), Num("h", _("Alto total (mm)"), 20, negative=False),
                                 Num("a", _("Giro (grados, antihorario)"), 0), self._color_field()])
        if v:
            self._add_shape(lambda: de.make_ellipse(v["x"], v["y"], v["w"] / 2, v["h"] / 2, v["a"], stroke=v["color"]),
                            v["color"])

    def draw_polygon(self) -> None:
        cx, cy = self._center()
        v = self._ask(_("Polígono regular"), [
            Num("x", _("Centro, X (mm)"), cx), Num("y", _("Centro, Y (mm)"), cy), Num("n", _("Número de lados"), 6, integer=True, negative=False),
            Num("size", _("Tamaño (mm)"), 30, negative=False),
            Choice("measure", _("El tamaño es"), [("radio", _("el radio hasta los vértices")),
                                               ("caras", _("la medida entre caras opuestas"))], "radio",
                   _("Para una tuerca o un hueco hexagonal suele darse la medida entre caras (la de la llave).")),
            Num("a", _("Giro (grados): el primer vértice a…"), 0), self._color_field()])
        if not v:
            return
        by_faces = v["measure"] == "caras"
        self._add_shape(lambda: de.make_polygon(v["x"], v["y"], v["n"], v["size"] / 2 if by_faces else v["size"], v["a"],
                                                "apotema" if by_faces else "radio", stroke=v["color"]), v["color"])

    def draw_slot(self) -> None:
        cx, cy = self._center()
        v = self._ask(_("Ranura"), [
            Num("x1", _("Centro del extremo 1, X (mm)"), cx - 20), Num("y1", _("Centro del extremo 1, Y (mm)"), cy),
            Num("x2", _("Centro del extremo 2, X (mm)"), cx + 20), Num("y2", _("Centro del extremo 2, Y (mm)"), cy),
            Num("w", _("Ancho de la ranura (mm)"), 8, negative=False), self._color_field()],
            _("Una ranura es un rectángulo con los extremos redondos. Los puntos son los centros de los dos extremos."))
        if v:
            self._add_shape(lambda: de.make_slot(v["x1"], v["y1"], v["x2"], v["y2"], v["w"], stroke=v["color"]), v["color"])

    def draw_polyline(self) -> None:
        v = self._ask(_("Línea o polilínea"), [
            Points("pts", _("Puntos"), "", _("Un punto por línea.")), Check("closed", _("Cerrar el contorno (es una pieza o un hueco)")),
            self._color_field()],
            _("Un punto por línea, «X Y» o «X; Y» (mm). Dos puntos son una línea; con «Cerrar» el último se une con el "
            "primero y sale un contorno que se puede cortar por fuera o por dentro."))
        if v:
            self._add_shape(lambda: de.make_polyline(v["pts"], v["closed"], stroke=v["color"]), v["color"])

    # --- mover, girar, cambiar tamano, reflejar, copiar ---------------------------------------------------------------------
    def draw_move(self) -> None:
        picked = self._need_selection()
        if not picked:
            return
        v = self._ask(_("Mover"), [Num("dx", _("Hacia la derecha (mm)"), 0), Num("dy", _("Hacia arriba (mm)"), 0)],
                      _("Se mueven {count} forma(s). Negativo: hacia la izquierda / hacia abajo.\n", count=len(picked)) + self._size_text())
        if v:
            self.apply_edit(de.move_edit(picked, v["dx"], v["dy"]))

    def draw_place(self) -> None:
        picked = self._need_selection()
        b = self._selection_bounds()
        if not picked or b is None:
            return
        v = self._ask(_("Colocar en…"), [Num("x", _("Esquina inferior izquierda, X (mm)"), b[0]),
                                      Num("y", _("Esquina inferior izquierda, Y (mm)"), b[1])],
                      _("Pone la esquina inferior izquierda de lo elegido en ese punto.\n") + self._size_text())
        if v:
            self.apply_edit(de.move_edit(picked, v["x"] - b[0], v["y"] - b[1]))

    def draw_rotate(self) -> None:
        picked = self._need_selection()
        b = self._selection_bounds()
        if not picked or b is None:
            return
        v = self._ask(_("Girar"), [Num("a", _("Ángulo (grados)"), 90), Num("x", _("Girar alrededor de X (mm)"), (b[0] + b[2]) / 2),
                                Num("y", _("Girar alrededor de Y (mm)"), (b[1] + b[3]) / 2)],
                      _("Positivo gira en sentido antihorario; negativo, horario. Por defecto gira sobre el centro de lo elegido."))
        if v:
            self.apply_edit(de.rotate_edit(picked, v["a"], v["x"], v["y"]))

    def draw_scale(self) -> None:
        picked = self._need_selection()
        b = self._selection_bounds()
        if not picked or b is None:
            return
        w0, h0 = b[2] - b[0], b[3] - b[1]

        def hook(dialog: ToolDialog, key: str) -> None:
            vals = dialog.values()
            if not vals["keep"]:
                return
            if key == "w" and vals["w"] and w0 > 0:
                dialog.set_value("h", vals["w"] * h0 / w0)
            elif key == "h" and vals["h"] and h0 > 0:
                dialog.set_value("w", vals["h"] * w0 / h0)
            elif key == "keep" and vals["w"] and w0 > 0:
                dialog.set_value("h", vals["w"] * h0 / w0)

        v = self._ask(_("Cambiar tamaño"), [
            Num("w", _("Ancho final (mm)"), w0, negative=False), Num("h", _("Alto final (mm)"), h0, negative=False),
            Check("keep", _("Mantener las proporciones"), True),
            Choice("from", _("Cambiar desde"), [("centro", _("el centro de lo elegido")),
                                             ("esquina", _("su esquina inferior izquierda"))], "esquina")],
            _("Ahora mide {w0:.2f} x {h0:.2f} mm. Escribe la medida final.", w0=w0, h0=h0), hook=hook)
        if not v:
            return
        if (w0 <= 0 and abs(v["w"] - w0) > 1e-9) or (h0 <= 0 and abs(v["h"] - h0) > 1e-9):
            self._error(_("Cambiar tamaño"), _("Lo elegido no tiene ancho o alto (es una línea recta): no se puede dar esa medida."))
            return
        sx = v["w"] / w0 if w0 > 0 else 1.0
        sy = v["h"] / h0 if h0 > 0 else 1.0
        cx, cy = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) if v["from"] == "centro" else (b[0], b[1])
        self.apply_edit(de.scale_edit(picked, sx, sy, cx, cy))

    def draw_mirror(self) -> None:
        picked = self._need_selection()
        b = self._selection_bounds()
        if not picked or b is None:
            return
        centre = {"vertical": (b[0] + b[2]) / 2, "horizontal": (b[1] + b[3]) / 2}

        def hook(dialog: ToolDialog, key: str) -> None:
            if key == "axis":
                dialog.set_value("pos", centre[dialog.values()["axis"]])

        v = self._ask(_("Reflejar"), [
            Choice("axis", _("Voltear"), [("vertical", _("de izquierda a derecha (eje vertical)")),
                                       ("horizontal", _("de arriba abajo (eje horizontal)"))], "vertical"),
            Num("pos", _("Posición del eje (mm)"), centre["vertical"])],
            _("El eje pasa por el centro de lo elegido (la forma se da la vuelta en su sitio). Cambia la posición para "
            "reflejarla al otro lado de una línea."), hook=hook)
        if v:
            self.apply_edit(de.mirror_edit(picked, v["axis"], v["pos"]))

    def draw_array(self) -> None:
        picked = self._need_selection()
        b = self._selection_bounds()
        if not picked or b is None:
            return
        v = self._ask(_("Copiar en matriz"), [
            Num("nx", _("Columnas (con la original)"), 2, integer=True, negative=False),
            Num("ny", _("Filas (con la original)"), 1, integer=True, negative=False),
            Num("dx", _("Separación entre columnas (mm)"), round(b[2] - b[0] + 10, 3)),
            Num("dy", _("Separación entre filas (mm)"), round(b[3] - b[1] + 10, 3))],
            _("La separación va de una copia a la siguiente, medida entre puntos equivalentes: positiva hacia la derecha y "
            "hacia arriba. Con una separación mayor que lo elegido las copias no se tocan.\n") + self._size_text())
        if v:
            self.apply_edit(de.array_edit(picked, v["nx"], v["ny"], v["dx"], v["dy"]))

    def draw_polar(self) -> None:
        picked = self._need_selection()
        if not picked:
            return
        cx, cy = self._center()
        v = self._ask(_("Copiar en círculo"), [
            Num("n", _("Número de formas (con la original)"), 6, integer=True, negative=False),
            Num("a", _("Ángulo total (grados)"), 360), Num("x", _("Centro, X (mm)"), cx), Num("y", _("Centro, Y (mm)"), cy)],
            _("Reparte las copias alrededor del centro y las gira con ellas. Con 360 grados quedan repartidas por todo el "
            "círculo; con menos, la última queda a ese ángulo de la original (negativo: en sentido horario)."))
        if v:
            self.apply_edit(de.polar_edit(picked, v["n"], v["a"], v["x"], v["y"]))

    # --- desfase, unir, restar ------------------------------------------------------------------------------------------------
    def draw_offset(self) -> None:
        picked = self._need_selection()
        if not picked:
            return
        v = self._ask(_("Desfase (offset)"), [
            Choice("dir", _("Hacia"), [("fuera", _("fuera (la forma crece)")), ("dentro", _("dentro (la forma se encoge)"))], "fuera"),
            Num("d", _("Distancia (mm)"), 1, negative=False),
            Choice("corners", _("Esquinas"), [("redondas", _("redondeadas")), ("vivas", _("en punta"))], "redondas"),
            Check("keep", _("Conservar las originales"), False)],
            _("Crea el contorno paralelo de {count} forma(s) a esa distancia. Una línea abierta sale rodeada. "
              "Elige solo lo que quieras desfasar: un hueco dentro de una pieza crece si lo desfasas «hacia "
              "fuera».", count=len(picked)))
        if v:
            self.apply_edit(de.offset_edit(picked, v["d"] if v["dir"] == "fuera" else -v["d"], v["keep"], v["corners"]))

    def draw_union(self) -> None:
        picked = self._need_selection(2)
        if picked:
            self.apply_edit(de.union_edit(picked))

    def draw_intersect(self) -> None:
        picked = self._need_selection(2)
        if picked:
            self.apply_edit(de.intersect_edit(picked))

    def draw_subtract(self) -> None:
        picked = self._need_selection(2)
        if not picked:
            return
        v = self._ask(_("Restar formas"), [
            Choice("base", _("Se resta de"), [("primera", _("la primera (la más antigua del dibujo): se le quitan las demás")),
                                          ("ultima", _("la última (la más reciente): se le quitan las demás"))], "primera"),
            Check("keep", _("Conservar las originales"), False)],
            _("De {count} formas elegidas, una es el material y las demás son lo que se le quita (un agujero, un "
              "corte). Las formas más antiguas son las del SVG; las que dibujas ahora son las más recientes.", count=len(picked)))
        if v:
            self.apply_edit(de.subtract_edit(picked, v["base"], v["keep"]))

    def draw_close(self) -> None:
        picked = self._need_selection()
        if picked:
            self.apply_edit(de.close_edit(picked))
