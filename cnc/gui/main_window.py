"""Ventana principal: abrir el SVG, decidir que se hace con cada color, ver la trayectoria y generar el G-code."""
from __future__ import annotations

import json
import sys
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QRect, QRunnable, QSettings, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
                               QSplitter, QToolButton, QVBoxLayout, QWidget)

from .. import __version__
from .. import i18n
from .. import folders as fo
from .. import checklist as cl
from ..checklist import build_checklist
from ..holddown import HoldAnalysis, HoldError, analyse_plan, board_points
from ..oprows import ignored_colors, plain, shape_key
from ..machines import DEFAULT_MACHINES_FILE, MachinesFileError, load_machines
from ..pipeline import JobPlan, Message, app_dir
from ..preview import preview_ops
from ..project import Project, ProjectError, gui_messages, startup_project
from ..timing import describe as describe_time
from ..tools import ToolsFileError, load_tools
from . import theme
from .canvas import Canvas
from .draw_actions import DrawActions
from .checklist_dialog import ChecklistDialog
from .folder_actions import FolderActions
from .group_dialog import GroupDialog
from .hold_panel import HoldPanel
from .job_panel import JobPanel
from .machines_dialog import MachinesDialog
from .messages_panel import MessagesPanel
from .ops_panel import SIDE_LABEL, OpsPanel
from .bridge_bar import BridgeBar
from .props_bar import PropertiesBar
from .tools_dialog import ToolsDialog
from .widgets import CurrentPageTabs
from ..i18n import _, _n

PLAN_DELAY_MS = 220
JOB_SUFFIX = ".cncjob"
PENDING_EXTERNAL_MS = 400              # cada cuanto mira si ya se cerro el cuadro que impedia abrir el archivo recibido
PENDING_EXTERNAL_TRIES = 900           # unos 6 minutos
TOOLS_FILE = "herramientas.json"


class PlanSignals(QObject):
    done = Signal(int, object, object, object)        # token, numero de tablero, plan, previsualizacion
    failed = Signal(int, str)


class PlanTask(QRunnable):
    """Calcula un plan en un hilo con una copia del proyecto, para que la ventana siga respondiendo."""

    def __init__(self, token: int, latest, project: Project, number, signals: PlanSignals):
        super().__init__()
        self.token, self.latest, self.project, self.number, self.signals = token, latest, project, number, signals

    def run(self) -> None:
        if self.latest() != self.token:                 # ya hay una peticion mas nueva
            return
        try:
            plan = self.project.plan(self.number)
            previews = preview_ops(plan)
            plan.time                                   # lo caro se hace aqui, en el hilo, y la ventana lo encuentra hecho
        except Exception:                               # un fallo del programa no debe dejar la ventana colgada
            self.signals.failed.emit(self.token, traceback.format_exc())
            return
        self.signals.done.emit(self.token, self.number, plan, previews)


HOLD_PANE_HEIGHT = 340                                    # alto del panel de abajo con la pestaña Sujecion
PANE_MIN_HEIGHT = 90                                        # lo mas bajo que se deja el panel de abajo (pestañas + algo)
CANVAS_MIN_HEIGHT = 300                                      # el dibujo conserva al menos esto al abrir Sujecion
DEFAULT_SIZE = (1400, 880)                                  # tamaño de la ventana si no hay otro recordado

class MainWindow(FolderActions, DrawActions, QMainWindow):
    def __init__(self, project: Optional[Project] = None, notes=(), use_settings: bool = True, folders=None,
                 settings_file: Optional[str] = None):
        super().__init__()
        self.folders = tuple(folders) if folders else (Path.cwd(), app_dir())    # donde se buscan los archivos de ajustes
        if project is None:
            project, notes = startup_project(*self.folders)
        self.project = project
        self.startup_notes = list(notes)
        if settings_file:                               # lo que recuerda este PC, en un archivo concreto (pruebas)
            self.settings = QSettings(str(settings_file), QSettings.IniFormat)
        else:
            self.settings = QSettings("cnc_app", "cnc_app") if use_settings else None
        self._memory_prefs: dict = {}                   # lo que se recuerda cuando no hay QSettings (pruebas)
        self.startup_notes.extend(self.folder_notes())  # una carpeta fija que ya no vale se avisa al arrancar
        self.current: Optional[int] = None              # numero del tablero que se esta viendo
        self.last_plan: Optional[JobPlan] = None
        self.last_previews: list = []
        self.last_number: Optional[int] = None
        self.job_path: Optional[Path] = None
        self._dirty = False                             # hay cambios de dibujo que no estan en el trabajo guardado
        self._token = 0
        self._fit_next = True
        self._ignored_sig: tuple = ()
        self._extra_notes: list = []
        self.selection: set = set()                     # vectores elegidos en el dibujo (indices en drawing.shapes)
        self._quiet_rows = False                        # elegir una fila desde el dibujo no repinta dos veces
        self.setWindowTitle("cnc_app")
        self.setAcceptDrops(True)
        self.resize(*DEFAULT_SIZE)

        self.canvas = Canvas()
        self.ops = OpsPanel()
        self.job = JobPanel()
        self.messages = MessagesPanel()
        self.hold_panel = HoldPanel()
        self._hold_cache: Optional[tuple] = None         # (plan, analisis) del ultimo calculo de sujecion
        self._hold_view: Optional[tuple] = None          # (analisis, puntos, comprobaciones) de lo que se ve
        self._pane_before: Optional[int] = None          # altura del panel de abajo antes de abrir Sujecion
        self._pane_set = 0
        self._pending_external: Optional[str] = None       # archivo pasado por otro arranque, esperando a que no haya cuadros
        self._pending_tries = 0
        self._pending_timer = QTimer(self)
        self._pending_timer.setSingleShot(True)
        self._pending_timer.timeout.connect(self._open_external_when_free)
        self._build_ui()
        self._build_menus()
        self._connect()
        self._only_the_window_takes_drops()

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(PLAN_DELAY_MS)
        self._timer.timeout.connect(self._start_plan)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._signals = PlanSignals()
        self._signals.done.connect(self._plan_done)
        self._signals.failed.connect(self._plan_failed)

        self._apply_remembered_machine()
        self._apply_remembered_bridges()
        self.job.bind(self.project)
        self.hold_panel.bind(self.project.hold)
        self.ops.bind(self.project.rows)
        self._update_selection_ui()
        self.refresh_all(fit=True)
        if self.startup_notes:
            self.messages.show_messages(self.startup_notes)
            self._update_messages_tab()
        if self.settings is not None:
            geo = self.settings.value("geometry")
            if geo is not None:
                self.restoreGeometry(geo)

    # --- construccion ---------------------------------------------------------------------------------------
    def _build_ui(self) -> None:
        # columna izquierda: tableros y ajustes
        self.boards = QListWidget()
        self.boards.setMaximumHeight(92)
        self.detect_boards = QCheckBox(_("Detectar tableros"))
        self.detect_boards.setToolTip(_("Un rectángulo con dibujo dentro es un tablero y sale en su propio .nc; las elipses agrupan y se descartan"))
        self.detect_boards.setChecked(self.project.use_boards)
        self.boards_title = QLabel(_("Tableros"))
        self.boards_title.setObjectName("muted")
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(8, 8, 4, 8)
        ll.addWidget(self.boards_title)
        ll.addWidget(self.boards)
        ll.addWidget(self.detect_boards)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)           # lo que no quepa se alcanza, nunca queda cortado
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 6, 0)
        hl.addWidget(self.job)
        scroll.setWidget(holder)
        ll.addWidget(scroll, 1)
        # El ancho del panel es el que necesita el panel del trabajo (con su barra vertical y los margenes): con menos, los
        # botones de al lado de la fresa y la maquina salian cortados.
        margins = ll.contentsMargins()
        self._left_width = max(380, self.job.minimumSizeHint().width() + 6 + scroll.verticalScrollBar().sizeHint().width()
                               + margins.left() + margins.right() + 2)
        left.setMinimumWidth(340)
        left.setMaximumWidth(max(480, self._left_width))

        # centro: lienzo con sus botones de vista
        self.btn_drawing = QToolButton(text=_("Dibujo"))
        self.btn_machine = QToolButton(text=_("Máquina"))
        for b in (self.btn_drawing, self.btn_machine):
            b.setCheckable(True)
            b.setAutoExclusive(True)
        self.btn_drawing.setChecked(True)
        self.btn_rapids = QToolButton(text=_("Rápidos"))
        self.btn_rapids.setCheckable(True)
        self.btn_rapids.setToolTip(_("Mostrar los desplazamientos en rápido (G0) entre cortes"))
        self.btn_fit = QToolButton(text=_("Ajustar"))
        self.btn_fit.setToolTip(_("Ajustar la vista (doble clic en el dibujo)"))
        self.act_grid = QAction(_("Rejilla"), self)
        self.act_grid.setCheckable(True)
        self.act_grid.setToolTip(_("Rejilla con medidas en mm; su paso se adapta al zoom o es el de la cuadrícula del ratón "
                                 "(Dibujo → Dibujar con el ratón → Opciones)"))
        self.act_frame = QAction(_("Recorrido"), self)
        self.act_frame.setCheckable(True)
        self.act_frame.setToolTip(_("En el dibujo, el contorno de puntos del recorrido de la máquina elegida, colocado sobre "
                                  "el tablero que se ve con el giro y el cero que tendrá el G-code"))
        self.btn_grid, self.btn_frame = QToolButton(), QToolButton()
        self.btn_grid.setDefaultAction(self.act_grid)
        self.btn_frame.setDefaultAction(self.act_frame)
        self.act_wheel = QAction(_("Rueda del ratón invertida"), self)
        self.act_wheel.setCheckable(True)
        self.act_wheel.setToolTip(_("Marcada: girar la rueda hacia delante aleja el dibujo y hacia atrás lo acerca. "
                                  "Sin marcar: al revés."))
        self.act_wheel.setChecked(self._pref("wheel") != "0")                # sin dato, invertida
        self.canvas.wheel_inverted = self.act_wheel.isChecked()
        self.act_wheel.toggled.connect(self._wheel_toggled)
        self.act_grid.setChecked(self._pref("grid") != "0")                # sin dato, encendidos
        self.act_frame.setChecked(self._pref("frame") != "0")
        self.canvas.set_grid_visible(self.act_grid.isChecked())
        self.canvas.set_frame_visible(self.act_frame.isChecked())
        self.act_grid.toggled.connect(self._grid_toggled)
        self.act_frame.toggled.connect(self._frame_toggled)
        self.summary = QLabel("")
        self.summary.setTextFormat(Qt.RichText)
        self.legend = QLabel("")
        self.legend.setTextFormat(Qt.RichText)
        bar = QHBoxLayout()
        bar.setContentsMargins(8, 6, 8, 0)
        for w in (self.btn_drawing, self.btn_machine, self.btn_rapids, self.btn_fit, self.btn_grid, self.btn_frame):
            bar.addWidget(w)
        bar.addSpacing(12)
        bar.addWidget(self.legend, 1)
        self.generate_btn = QPushButton(_("Generar G-code…"))
        self.generate_btn.setObjectName("primary")
        self.generate_btn.setEnabled(False)
        bar.addWidget(self.generate_btn)
        self.summary.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)     # un texto largo no ensancha la ventana
        self.summary.setContentsMargins(10, 0, 10, 0)
        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(4)
        cl.addLayout(bar)
        cl.addLayout(self._build_tool_row())
        self.props = PropertiesBar()                     # las medidas de la figura elegida, para verlas y escribirlas
        cl.addWidget(self.props)
        self.bridge_bar = BridgeBar()                    # los puentes: lo que hay en lugar de las medidas con la herramienta Puente
        self.bridge_bar.hide()
        cl.addWidget(self.bridge_bar)
        cl.addWidget(self.summary)
        cl.addWidget(self.canvas, 1)

        top = QSplitter(Qt.Horizontal)
        top.addWidget(left)
        top.addWidget(center)
        top.setStretchFactor(0, 0)
        top.setStretchFactor(1, 1)
        top.setSizes([self._left_width, 1000])
        top.setChildrenCollapsible(False)                  # arrastrar el divisor nunca hace desaparecer un panel de golpe
        top.setCollapsible(0, False)
        top.setCollapsible(1, False)

        self.tabs = CurrentPageTabs()
        self.tabs.addTab(self.ops, _("Operaciones"))
        self.tabs.addTab(self.messages, _("Mensajes"))
        self.tabs.addTab(self.hold_panel, _("Sujeción"))
        self.tabs.setDocumentMode(True)
        main = QSplitter(Qt.Vertical)
        main.addWidget(top)
        # El panel de abajo cabe en lo que haya: las pestañas tienen su altura minima (la de Sujecion, unos 275 px), y
        # dentro de un area con barras de desplazamiento el divisor baja sin tope hasta PANE_MIN_HEIGHT (en una pantalla
        # de 768 px de alto el dibujo necesita ese sitio) y lo que no cabe se alcanza con la barra.
        self.pane = QScrollArea()
        self.pane.setWidget(self.tabs)
        self.pane.setWidgetResizable(True)
        self.pane.setFrameShape(QScrollArea.NoFrame)
        self.pane.setMinimumHeight(PANE_MIN_HEIGHT)
        main.addWidget(self.pane)
        main.setStretchFactor(0, 1)
        main.setStretchFactor(1, 0)
        main.setSizes([600, 250])
        main.setChildrenCollapsible(False)
        main.setCollapsible(0, False)
        main.setCollapsible(1, False)
        self.splitter = main
        self.setCentralWidget(main)
        self.statusBar().showMessage(_("Abre un dibujo SVG para empezar (Archivo → Abrir, o arrástralo aquí)."))

    def _build_menus(self) -> None:
        def act(menu, text, slot, shortcut=None, checkable=False, checked=False):
            a = QAction(text, self)
            a.setCheckable(checkable)
            a.setChecked(checked)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.triggered.connect(slot)
            menu.addAction(a)
            return a

        m = self.menuBar().addMenu(_("&Archivo"))
        act(m, _("Abrir dibujo SVG…"), self.open_svg_dialog, "Ctrl+O")
        act(m, _("Abrir trabajo guardado…"), self.open_job_dialog, "Ctrl+Shift+O")
        self.act_save = act(m, _("Guardar trabajo…"), self.save_job_dialog, "Ctrl+S")
        act(m, _("Fichas de fresas…"), self.open_tools_editor, "Ctrl+T")
        act(m, _("Recargar fichas de fresas"), self.reload_tools, "F5")
        act(m, _("Máquinas…"), self.open_machines_editor, "Ctrl+M")
        act(m, _("Recargar máquinas"), self.reload_machines)
        act(m, _("Carpetas de trabajos y G-code…"), self.open_folders_dialog)
        m.addSeparator()
        self.act_generate = act(m, _("Generar G-code…"), self.generate, "Ctrl+G")
        if sys.platform != "darwin":                      # en macOS «Salir» pasa al menú de la aplicación y no queda nada debajo
            m.addSeparator()
        quit_action = act(m, _("Salir"), self.close, "Ctrl+Q")
        # Sin el papel de «Salir», macOS pone el suyo en el menú de la aplicación, desactivado, con el atajo ⌘Q: ni el menú ni
        # ⌘Q cerraban la ventana. Con el papel, esta acción ocupa su sitio (en los demás sistemas no cambia nada).
        quit_action.setMenuRole(QAction.MenuRole.QuitRole)
        m = self.menuBar().addMenu(_("&Edición"))
        self.act_undo = act(m, _("Deshacer"), self.undo, "Ctrl+Z")
        self.act_redo = act(m, _("Rehacer"), self.redo, "Ctrl+Y")
        m.addSeparator()
        act(m, _("Elegir todos"), self.pick_all, "Ctrl+A")
        self.act_pick_board = act(m, _("Elegir el tablero actual (con su recuadro)"), self.pick_current_board)
        act(m, _("Quitar la elección"), self.escape, "Esc")
        m.addSeparator()
        self.act_delete = act(m, _("Borrar lo elegido"), self.draw_delete, QKeySequence.Delete)
        m = self.menuBar().addMenu(_("&Receta"))
        act(m, _("Abrir receta…"), self.load_recipe_dialog)
        act(m, _("Guardar receta como…"), self.save_recipe_dialog)
        act(m, _("Restablecer tabla"), self.reset_rows)
        m = self.menuBar().addMenu(_("&Formas"))
        self.act_new_group = act(m, _("Grupo nuevo con lo elegido…"), self.new_group, "Ctrl+Shift+N")
        self.act_ungroup = act(m, _("Quitar del grupo"), self.ungroup, "Ctrl+Shift+U")
        m.addSeparator()
        self.act_split = act(m, _("Separar interiores y exteriores"), self.split_cuts, "Ctrl+Shift+I")
        self.act_sort = act(m, _("Ordenar las filas (taladros, pozos, interiores, exteriores)"), self.sort_rows)
        self._build_draw_menu()
        m = self.menuBar().addMenu(_("&Ver"))
        group = QActionGroup(self)
        self.act_view_drawing = act(m, _("Dibujo"), lambda: self.set_view("drawing"), "Ctrl+1", True, True)
        self.act_view_machine = act(m, _("Máquina"), lambda: self.set_view("machine"), "Ctrl+2", True)
        group.addAction(self.act_view_drawing)
        group.addAction(self.act_view_machine)
        m.addSeparator()
        act(m, _("Ajustar vista"), self.canvas.fit, "Ctrl+0")
        m.addSeparator()
        m.addAction(self.act_grid)
        m.addAction(self.act_frame)
        m.addSeparator()
        m.addAction(self.act_wheel)
        m = self.menuBar().addMenu(_("A&yuda"))
        self._build_language_menu(m)
        act(m, _("Acerca de…"), self.about)

    def _build_language_menu(self, menu) -> None:
        """Ayuda → Idioma: Automático (el del sistema), Español o English. Vale desde el siguiente arranque."""
        sub = menu.addMenu(_("Idioma"))
        group = QActionGroup(self)
        current = i18n.normalize(self._pref(i18n.SETTING_KEY)) or "auto"
        self.language_actions = {}
        for code, text in (("auto", _("Automático (el del sistema)")), ("es", "Español"), ("en", "English")):
            action = QAction(text, self)
            action.setCheckable(True)
            action.setChecked(code == current)
            action.triggered.connect(lambda _checked=False, c=code: self.choose_language(c))
            group.addAction(action)
            sub.addAction(action)
            self.language_actions[code] = action

    def choose_language(self, code: str) -> None:
        """Guarda el idioma elegido en el menú; se aplica al abrir el programa de nuevo (los textos se fijan al arrancar)."""
        self._set_pref(i18n.SETTING_KEY, code)
        target = i18n.system_language() if code == "auto" else code
        if target == i18n.current_language():
            self.statusBar().showMessage(_("Idioma guardado."))
            return
        # El mensaje va en los dos idiomas (sin traducir a propósito): quien lo lee aún tiene el antiguo y puede no entender el nuevo.
        es = "Idioma guardado. Cierra la aplicación y ábrelo de nuevo para aplicarlo."
        en = "Language saved. Close the application and open it again to apply it."
        first, second = (en, es) if i18n.current_language() == "en" else (es, en)
        self.statusBar().showMessage(first)
        if self.settings is not None:
            QMessageBox.information(self, "cnc_app", first + "\n\n" + second)

    def _connect(self) -> None:
        self.btn_drawing.clicked.connect(lambda: self.set_view("drawing"))
        self.btn_machine.clicked.connect(lambda: self.set_view("machine"))
        self.btn_rapids.toggled.connect(lambda _on: self.refresh_canvas(keep=True))
        self.btn_fit.clicked.connect(self.canvas.fit)
        self.props.applied.connect(self._props_applied)
        self.props.problem.connect(lambda text: self.statusBar().showMessage(text))
        self.bridge_bar.applied.connect(self._bridge_numbers_applied)
        self.bridge_bar.problem.connect(lambda text: self.statusBar().showMessage(text))
        self.bridge_bar.removeRequested.connect(lambda: self.canvas.tools.bridge.delete_current())
        self.bridge_bar.autoRequested.connect(self._bridge_current_to_auto)
        self.generate_btn.clicked.connect(lambda: self.generate())
        self.boards.currentRowChanged.connect(self._board_chosen)
        self.boards.itemChanged.connect(self._board_checked)
        self.detect_boards.toggled.connect(self._detect_toggled)
        self.job.changed.connect(self._settings_changed)
        self.job.editToolsRequested.connect(self.open_tools_editor)
        self.job.editMachinesRequested.connect(self.open_machines_editor)
        self.job.machineChosen.connect(self._remember_machine)
        self.job.bridgesToggled.connect(self._remember_bridges)
        self.ops.changed.connect(self._rows_changed)
        self.ops.rowSelected.connect(
            lambda _key: self.refresh_canvas(keep=True) if self.view == "drawing" and not self._quiet_rows else None)
        self.ops.pickAllRequested.connect(self.pick_all)
        self.ops.pickNoneRequested.connect(self.pick_none)
        self.ops.pickLevelRequested.connect(self.pick_level)
        self.ops.newGroupRequested.connect(self.new_group)
        self.ops.moveToGroupRequested.connect(self.move_to_group)
        self.ops.ungroupRequested.connect(self.ungroup)
        self.ops.splitRequested.connect(self.split_cuts)
        self.ops.sortRequested.connect(self.sort_rows)
        self.ops.sorter = lambda row: self.project.sort_rows(row)
        self.ops.bridges_allowed = lambda: self.project.general.bridges
        self.ops.loadRecipeRequested.connect(self.load_recipe_dialog)
        self.ops.saveRecipeRequested.connect(self.save_recipe_dialog)
        self.ops.resetRequested.connect(self.reset_rows)
        self.canvas.shapeClicked.connect(self._shape_clicked)
        self.canvas.shapesSelected.connect(self._shapes_selected)
        self.canvas.boardClicked.connect(self._board_clicked)
        self.messages.applyOffsetRequested.connect(self.apply_suggested_offset)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.hold_panel.settingsChanged.connect(self._hold_settings_changed)
        self.hold_panel.resetRequested.connect(self.hold_reset)
        self.hold_panel.removeRequested.connect(self.hold_remove)
        self.hold_panel.pointEdited.connect(self.hold_edit)
        self.hold_panel.marksRequested.connect(lambda: self.generate_marks())
        self.hold_panel.rowSelected.connect(lambda _i: self._hold_draw_layer())
        self.canvas.holdAddRequested.connect(self.hold_add)
        self.canvas.holdRemoveRequested.connect(self.hold_remove)
        self.canvas.holdHovered.connect(self._hold_hover)
        self.canvas.holdLeft.connect(lambda: self.statusBar().clearMessage())

    # --- estado de la vista ---------------------------------------------------------------------------------
    @property
    def view(self) -> str:
        return "machine" if self.btn_machine.isChecked() else "drawing"

    def set_view(self, which: str) -> None:
        self.btn_drawing.setChecked(which == "drawing")
        self.btn_machine.setChecked(which == "machine")
        self.act_view_drawing.setChecked(which == "drawing")
        self.act_view_machine.setChecked(which == "machine")
        self.canvas.carry_view_to(which)                   # el zoom y la zona que se mira pasan de una vista a la otra
        self.refresh_canvas(keep=True)
        self._update_draw_actions()
        self._update_props()

    # --- abrir ---------------------------------------------------------------------------------------------
    def open_svg_dialog(self) -> None:
        if not self._confirm_discard():
            return
        path, __ = QFileDialog.getOpenFileName(self, _("Abrir dibujo"), self._folder(), _("Dibujo SVG (*.svg);;Todos (*)"))
        if path:
            self.open_svg(path)

    def open_svg(self, path, apply_default_recipe: bool = True) -> bool:
        try:
            self.project.load_svg(path)
        except Exception as e:                                  # archivo ilegible o SVG que no se entiende
            self._error(_("No se pudo abrir el dibujo"), f"{Path(path).name}: {e}")
            return False
        self._remember_folder(path)
        self.job_path = None
        note = self.project.apply_default_recipe() if apply_default_recipe else None
        self._after_drawing_loaded(note)
        return True

    def _drawing_name(self) -> str:
        """Como se llama lo que hay abierto: el SVG, o el trabajo de un dibujo nuevo."""
        if self.project.scratch:
            return self.job_path.name if self.job_path else _("dibujo nuevo")
        return self.project.drawing.path.name

    def _after_drawing_loaded(self, note: Optional[Message] = None) -> None:
        self._dirty = False
        self.canvas.tools.reset()                               # una herramienta a medias no vale para otro dibujo
        self.setWindowTitle(_("cnc_app — {drawing_name}", drawing_name=self._drawing_name()))
        self.job.bind(self.project)
        self.hold_panel.bind(self.project.hold)
        self._hold_cache = None
        self.ops.bind(self.project.rows)
        self._fill_boards()
        self.last_plan, self.last_previews = None, []
        self._extra_notes = ([note] if note else []) + list(self.project.load_notes)
        self.selection = set()
        self._update_selection_ui()
        self.refresh_all(fit=True)
        self.statusBar().showMessage(_("{drawing_name}: {count} combinacion(es) de color, {count2} unidad(es) de trabajo.", drawing_name=self._drawing_name(), count=len(self.project.rows), count2=len(self.project.units())))

    def open_job_dialog(self) -> None:
        if not self._confirm_discard():
            return
        folder = self.folder_for(fo.JOBS)
        if folder is None:
            return
        path, __ = QFileDialog.getOpenFileName(self, _("Abrir trabajo"), folder,
                                              _("Trabajo cnc_app (*{JOB_SUFFIX});;Todos (*)", JOB_SUFFIX=JOB_SUFFIX))
        if path:
            self.open_job(path)

    def open_job(self, path) -> bool:
        path = Path(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ProjectError(_("el archivo de trabajo no tiene el formato esperado."))
        except (OSError, ValueError, ProjectError) as e:
            self._error(_("No se pudo abrir el trabajo"), f"{path.name}: {e}")
            return False
        edicion = data.get("edicion")
        scratch = isinstance(edicion, dict) and bool(edicion.get("nuevo"))
        svg = None if scratch else self._find_svg(data.get("svg", ""), path)
        if svg is None and not scratch:
            return False
        use_boards = bool(data.get("detectar_tableros", True))              # antes de leer el dibujo: cambia sus tableros
        self.project.use_boards = use_boards
        self.detect_boards.blockSignals(True)
        self.detect_boards.setChecked(use_boards)
        self.detect_boards.blockSignals(False)
        if scratch:
            self.project.new_drawing(path.parent, path.with_suffix(".svg").name)         # sin SVG: el dibujo sale de las ediciones
            self.job_path = None
            self._after_drawing_loaded()
        elif not self.open_svg(svg, apply_default_recipe=False):
            return False
        try:
            self.project.apply_dict(data)
        except (ProjectError, TypeError, ValueError) as e:
            self._error(_("No se pudo aplicar el trabajo"), f"{path.name}: {e}")
            return False
        self.job_path = path
        self._after_drawing_loaded()
        self.statusBar().showMessage(_("Trabajo {name} abierto.", name=path.name))
        return True

    def _find_svg(self, stored: str, job_file: Path) -> Optional[Path]:
        for candidate in (Path(stored) if stored else None, job_file.with_name(Path(stored).name) if stored else None):
            if candidate is not None and candidate.is_file():
                return candidate
        path, __ = QFileDialog.getOpenFileName(
            self, _("¿Dónde está el dibujo {v}?", v=Path(stored).name or _('del trabajo')), str(job_file.parent),
            _("Dibujo SVG (*.svg);;Todos (*)"))
        return Path(path) if path else None

    def save_job_dialog(self) -> None:
        if self.project.drawing is None:
            return
        start = self.job_path
        if start is None:
            folder = self.folder_for(fo.JOBS)
            if folder is None:
                return
            start = Path(folder) / (self.project.drawing.path.stem + JOB_SUFFIX)
        path, __ = QFileDialog.getSaveFileName(self, _("Guardar trabajo"), str(start), _("Trabajo cnc_app (*{JOB_SUFFIX})", JOB_SUFFIX=JOB_SUFFIX))
        if path:
            self.save_job(path)

    def save_job(self, path) -> bool:
        path = Path(path)
        if path.suffix == "":
            path = path.with_suffix(JOB_SUFFIX)
        try:
            self.project.save(path)
        except OSError as e:
            self._error(_("No se pudo guardar el trabajo"), f"{path.name}: {e}")
            return False
        self.job_path = path
        self._dirty = False
        if self.project.scratch:                                # un dibujo nuevo se llama como su trabajo (los .nc salen asi)
            self.project.rename(path.with_suffix(".svg"))
            self.setWindowTitle(_("cnc_app — {name}", name=path.name))
        self.statusBar().showMessage(_("Trabajo guardado en {path}", path=path))
        return True

    def reload_tools(self) -> bool:
        """Vuelve a leer herramientas.json (se edita a mano para completar avances y pasadas de cada fresa)."""
        fresh, notes = startup_project(*self.folders)
        notes = [n for n in notes if n.code != "machines"]          # las maquinas se recargan aparte
        if any(n.level == "error" for n in notes) or not fresh.tools:
            self.messages.show_messages(notes or [Message(_("No hay fichas de fresas que cargar."), "warning")])
            self._update_messages_tab()
            self.statusBar().showMessage(_("No se recargaron las fichas: {why}", why=notes[0].text if notes else ""))
            return False
        self.project.tools = fresh.tools
        self.project.tools_path = fresh.tools_path
        self.job.bind(self.project)
        self.ops.set_hints(self.project.tool_hints(for_rows=True))
        self.statusBar().showMessage(_("Fichas recargadas: {join}", join=', '.join(fresh.tools)))
        self.request_plan()
        return True

    def open_tools_editor(self) -> bool:
        """Abre el editor de fichas. Antes vuelve a leer el archivo, para no pisar lo que se haya cambiado a mano
        desde la ultima vez; si esta roto, hay que arreglarlo primero (no se sobrescribe un archivo que no se lee)."""
        path = startup_project(*self.folders)[0].tools_path
        tools = self.project.tools
        if path is not None:
            try:
                tools = load_tools(path)
            except ToolsFileError as e:
                self._error(_("Fichas de fresas"), _("{e}\n\nArregla el archivo a mano (o bórralo) antes de abrir el editor: así no se pisa lo que "
                                                  "contiene.", e=e))
                return False
        else:
            path = self.project.tools_path or Path(self.folders[-1]) / TOOLS_FILE
        dialog = ToolsDialog(tools, path, self)
        if dialog.exec() != QDialog.Accepted or dialog.saved_path is None:
            return False
        self.apply_tools(dialog.editor, dialog.saved_path)
        return True

    def apply_tools(self, editor, path) -> None:
        """Pone en el proyecto las fichas recien guardadas y sigue con la fresa elegida si solo cambio de nombre."""
        p = self.project
        p.tools = editor.tools()
        p.tools_path = Path(path)
        g = p.general
        renames = editor.renamed()
        note = ""
        if g.tool in renames:
            p.general = replace(g, tool=renames[g.tool])
            note = " " + _("La fresa elegida pasa a llamarse {renames}.", renames=renames[g.tool])
        elif g.tool in editor.removed():
            note = " " + _("La ficha {tool} ya no existe: elige otra fresa.", tool=g.tool)
        self.job.bind(p)
        self.ops.set_hints(p.tool_hints(for_rows=True))
        self.statusBar().showMessage(_("Fichas guardadas en {path} ({count}).{note}", path=path, count=len(p.tools), note=note))
        self.request_plan()

    # --- maquinas ---------------------------------------------------------------------------------------------
    def _remember_machine(self, ident: str) -> None:
        """La ultima maquina elegida se recuerda en este PC: es la que sale al abrir un dibujo nuevo."""
        self._set_pref("machine", ident)

    def _apply_remembered_machine(self) -> None:
        """Al arrancar, si el proyecto no trae maquina (el archivo de configuracion no la nombra) y este PC recuerda una
        que sigue existiendo, se usa esa."""
        p = self.project
        remembered = self._pref("machine")
        if not (p.general.machine or "").strip() and remembered in p.machines:
            p.general = replace(p.general, machine=remembered)

    def _remember_bridges(self, on: bool) -> None:
        """El interruptor de los puentes se recuerda en este PC: es como queda al arrancar."""
        self._set_pref("bridges", "1" if on else "0")

    def _apply_remembered_bridges(self) -> None:
        remembered = self._pref("bridges")
        if remembered in ("0", "1"):
            self.project.general = replace(self.project.general, bridges=remembered == "1")

    def reload_machines(self) -> bool:
        """Vuelve a leer maquinas.json (se puede editar a mano)."""
        fresh, notes = startup_project(*self.folders)
        problems = [n for n in notes if n.code == "machines"]
        if problems or not fresh.machines:
            self.messages.show_messages(problems or [Message(_("No hay fichas de maquina que cargar."), "warning")])
            self._update_messages_tab()
            self.statusBar().showMessage(_("No se recargaron las maquinas: {why}", why=problems[0].text if problems else ""))
            return False
        self.project.machines = fresh.machines
        self.project.machines_path = fresh.machines_path
        self.job.bind(self.project)
        self.statusBar().showMessage(_("Maquinas recargadas: {join}", join=', '.join(fresh.machines)))
        self.request_plan()
        return True

    def open_machines_editor(self) -> bool:
        """Abre el editor de maquinas. Antes vuelve a leer el archivo, para no pisar lo que se haya cambiado a mano desde
        la ultima vez; si esta roto, hay que arreglarlo primero (no se sobrescribe un archivo que no se lee)."""
        path = startup_project(*self.folders)[0].machines_path
        machines: dict = {}
        if path is not None:
            try:
                machines = load_machines(path)
            except MachinesFileError as e:
                self._error(_("Maquinas"), _("{e}\n\nArregla el archivo a mano (o borralo) antes de abrir el editor: asi no se pisa lo que "
                                          "contiene.", e=e))
                return False
        else:
            path = self.project.machines_path or Path(self.folders[-1]) / DEFAULT_MACHINES_FILE
        for ident, machine in self.project.machines.items():     # las que solo estan en el trabajo abierto tambien se ofrecen
            machines.setdefault(ident, machine)
        dialog = MachinesDialog(machines, path, self)
        if dialog.exec() != QDialog.Accepted or dialog.saved_path is None:
            return False
        self.apply_machines(dialog.editor, dialog.saved_path)
        return True

    def apply_machines(self, editor, path) -> None:
        """Pone en el proyecto las fichas recien guardadas y sigue con la maquina elegida si solo cambio de nombre."""
        p = self.project
        p.machines = editor.machines()
        p.machines_path = Path(path)
        g = p.general
        renames = editor.renamed()
        note = ""
        if g.machine in renames:
            p.general = replace(g, machine=renames[g.machine])
            note = " " + _("La máquina elegida pasa a llamarse {renames}.", renames=renames[g.machine])
        elif g.machine in editor.removed():
            note = " " + _("La máquina {machine} ya no existe: elige otra.", machine=g.machine)
        self.job.bind(p)
        self.statusBar().showMessage(_("Maquinas guardadas en {path} ({count}).{note}", path=path, count=len(p.machines), note=note))
        self.request_plan()

    # --- recetas ----------------------------------------------------------------------------------------------
    def load_recipe_dialog(self) -> None:
        if self.project.drawing is None:
            self._error(_("Receta"), _("Abre primero un dibujo."))
            return
        path, __ = QFileDialog.getOpenFileName(self, _("Abrir receta"), self._folder(), _("Receta (*.toml);;Todos (*)"))
        if path:
            self.load_recipe(path)

    def load_recipe(self, path) -> bool:
        try:
            self.project.load_recipe_file(path)
        except ProjectError as e:
            self._error(_("No se pudo abrir la receta"), str(e))
            return False
        self.ops.bind(self.project.rows)
        self.refresh_all()
        self.statusBar().showMessage(_("Receta {name} aplicada a la tabla.", name=Path(path).name))
        return True

    def save_recipe_dialog(self) -> None:
        if self.project.drawing is None:
            return
        path, __ = QFileDialog.getSaveFileName(self, _("Guardar receta"), self._folder() + "/receta.toml",
                                              _("Receta (*.toml)"))
        if path:
            self.save_recipe(path)

    def save_recipe(self, path) -> bool:
        try:
            text = self.project.recipe_text()
            Path(path).write_text(text, encoding="utf-8")
        except (ProjectError, OSError) as e:
            self._error(_("No se pudo guardar la receta"), str(e))
            return False
        self.statusBar().showMessage(_("Receta guardada en {path}.", path=path))
        return True

    def reset_rows(self) -> None:
        if self.project.drawing is None:
            return
        self.project.reset_rows()
        self.ops.bind(self.project.rows)
        self.refresh_all()

    # --- tableros -----------------------------------------------------------------------------------------------
    def _fill_boards(self) -> None:
        self.boards.blockSignals(True)
        self.boards.clear()
        units = self.project.units()
        for u in units:
            item = QListWidgetItem(u.label)
            item.setData(Qt.UserRole, u.number)
            if u.number is not None:
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if self.project.overrides[u.number].selected else Qt.Unchecked)
            else:
                item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
            self.boards.addItem(item)
        self.boards.blockSignals(False)
        multi = any(u.number is not None for u in units)
        self.boards_title.setText(_("Tableros (marca los que se generan)") if multi else _("Dibujo sin tableros"))
        if units:
            self.boards.setCurrentRow(0)
            self.current = units[0].number
        else:
            self.current = None
        self.job.set_board(self.current)

    def _sync_board_checks(self) -> None:
        self.boards.blockSignals(True)
        for i in range(self.boards.count()):
            item = self.boards.item(i)
            n = item.data(Qt.UserRole)
            if n is not None and n in self.project.overrides:
                item.setCheckState(Qt.Checked if self.project.overrides[n].selected else Qt.Unchecked)
        self.boards.blockSignals(False)

    def _board_chosen(self, row: int) -> None:
        if row < 0:
            return
        self.current = self.boards.item(row).data(Qt.UserRole)
        self.job.set_board(self.current)
        self._fit_next = True
        self.last_plan = None
        self.refresh_canvas(keep=True)
        self._update_hold()                       # sin trayectoria del tablero nuevo: no se enseñan los puntos del anterior
        self.request_plan()

    def _board_checked(self, item: QListWidgetItem) -> None:
        n = item.data(Qt.UserRole)
        if n is None or n not in self.project.overrides:
            return
        self.project.overrides[n].selected = item.checkState() == Qt.Checked
        self.job.refresh()
        self._update_generate_enabled()

    def _board_clicked(self, number: int) -> None:
        for i in range(self.boards.count()):
            if self.boards.item(i).data(Qt.UserRole) == number:
                self.boards.setCurrentRow(i)
                return

    def _shape_clicked(self, index: int) -> None:
        """Clic en un vector: queda elegido (solo el) y su fila se marca en la tabla."""
        d = self.project.drawing
        if d is not None and 0 <= index < len(d.shapes):
            self.selection = {index}
            self._quiet_rows = True
            self.ops.select_key(shape_key(self.project.shapes()[index]))
            self._quiet_rows = False
            self._selection_changed()

    # --- vectores elegidos y grupos --------------------------------------------------------------------------------
    def _shapes_selected(self, ids, mode: str) -> None:
        ids = {int(i) for i in ids}
        if mode == "add":
            self.selection |= ids
        elif mode == "toggle":
            self.selection ^= ids
        else:
            self.selection = ids
        self._selection_changed()

    def pick_all(self) -> None:
        self.selection = set(self.project.selectable())
        self._selection_changed()

    def pick_none(self) -> None:
        self.selection = set()
        self._selection_changed()

    def _level_indices(self, level: int) -> set:
        """Los vectores de un nivel de anidamiento (-1: los abiertos, que no tienen nivel)."""
        levels = self.project.levels()
        if level < 0:
            return {i for i in self.project.selectable() if i not in levels}
        return {i for i, lv in levels.items() if lv == level}

    def pick_level(self, level: int, on: bool) -> None:
        picked = self._level_indices(level)
        self.selection = (self.selection | picked) if on else (self.selection - picked)
        self._selection_changed()

    def _selection_levels(self) -> list:
        """Las entradas del menu Elegir: un nivel de anidamiento cada una, con su cantidad y si esta toda elegida."""
        p = self.project
        if p.drawing is None:
            return []
        levels = p.levels()
        found = sorted(set(levels.values()))
        if any(i not in levels for i in p.selectable()):
            found.append(-1)
        out = []
        for level in found:
            idx = self._level_indices(level)
            if level < 0:
                text = _("Líneas abiertas ({count})", count=len(idx))
            elif level == 0:
                text = _("Nivel 0: exteriores ({count})", count=len(idx))
            else:
                text = _("Nivel {level}: dentro de otro ({count})", level=level, count=len(idx)) if level == 1 else _("Nivel {level} ({count})", level=level, count=len(idx))
            out.append((level, text, bool(idx) and idx <= self.selection))
        return out

    def _selection_text(self) -> str:
        """Que hay elegido: cuantos vectores, de que niveles y en que filas estan ahora."""
        p = self.project
        if not self.selection or p.drawing is None:
            return ""
        picked = self._picked_vectors()
        frames = len(self.selection) - len(picked)
        n = len(picked)
        levels = p.levels()
        nests = sorted({levels[i] for i in picked if i in levels})
        parts = [_n("{n} forma", "{n} formas", n)]
        if frames:
            parts.append(_n("{n} recuadro de tablero", "{n} recuadros de tablero", frames))
        if nests:
            levels_text = ", ".join(str(v) for v in nests)
            parts.append(_n("nivel {levels}", "niveles {levels}", len(nests), levels=levels_text))
        if any(i not in levels for i in picked):
            parts.append(_("con líneas abiertas"))
        shapes = p.shapes()
        titles = {r.key: r.title for r in p.rows}
        where: dict = {}
        for i in picked:
            key = shape_key(shapes[i])
            label = titles.get(key) or key[0] or key[1] or _("sin color")
            where[label] = where.get(label, 0) + 1
        parts.append(_("ahora en: {where}", where=", ".join(f"{k} ({v})" for k, v in where.items())))
        return " · ".join(parts)

    def _picked_vectors(self) -> list[int]:
        """Lo elegido que se puede meter en un grupo (el recuadro de un tablero, si esta elegido, no)."""
        allowed = set(self.project.selectable())
        return sorted(i for i in self.selection if i in allowed)

    def _update_selection_ui(self) -> None:
        picked = self._picked_vectors()
        self.ops.set_selection(self._selection_text(), len(picked), self._selection_levels())
        self.act_new_group.setEnabled(bool(picked))
        self.act_ungroup.setEnabled(bool(picked))
        self._update_draw_actions()
        self._update_props()

    def _selection_changed(self) -> None:
        self._update_selection_ui()
        if self.view == "drawing":
            self.refresh_canvas(keep=True)

    def _ask_group(self) -> Optional[tuple]:
        """(nombre, operacion) del grupo nuevo, o None si se cancela."""
        dialog = GroupDialog(len(self.selection), [r.name for r in self.project.groups()], self)
        if dialog.exec() != QDialog.Accepted:
            return None
        return dialog.name(), dialog.action()

    def new_group(self) -> None:
        picked = self._picked_vectors()
        if not picked or self.project.drawing is None:
            return
        asked = self._ask_group()
        if asked is None:
            return
        name, action = asked
        try:
            color = self.project.create_group(picked, name, action)
        except ProjectError as e:
            self._error(_("Grupo nuevo"), str(e))
            return
        self._groups_changed(color, _("{count} forma(s) en el grupo «{name}»: pon su operación y su profundidad en la tabla.", count=len(picked), name=name))

    def move_to_group(self, color: str) -> None:
        picked = self._picked_vectors()
        if not picked or self.project.drawing is None:
            return
        try:
            self.project.move_to_group(picked, color)
        except ProjectError as e:
            self._error(_("Mover a grupo"), str(e))
            return
        name = next((r.name for r in self.project.groups() if r.stroke == color), "")
        self._groups_changed(color, _("{count} forma(s) movidas al grupo «{name}».", count=len(picked), name=name))

    def ungroup(self) -> None:
        picked = self._picked_vectors()
        if not picked or self.project.drawing is None:
            return
        try:
            self.project.ungroup(picked)
        except ProjectError as e:
            self._error(_("Quitar del grupo"), str(e))
            return
        self._groups_changed(None, _("{count} forma(s) vuelven a su color.", count=len(picked)))

    def split_cuts(self) -> None:
        """Separa la fila elegida (o la unica que se puede separar) en interiores, lineas y exteriores, cada uno con su fila."""
        p = self.project
        if p.drawing is None:
            return
        key = self.ops.current_key()
        candidates = p.splittable_rows()
        if (key is None or key not in {r.key for r in candidates}) and len(candidates) == 1:
            key = candidates[0].key
        try:
            colors = p.split_cuts(key)
        except ProjectError as e:
            self._error(_("Separar interiores y exteriores"), str(e))
            return
        rows = {r.stroke: r for r in p.rows}
        parts = ", ".join(f"{rows[c].name} ({rows[c].count}, {SIDE_LABEL[rows[c].side].lower()})" for c in colors)
        self._groups_changed(colors[0], _("Separado en una fila por clase, en el orden de corte: {parts}. Al elegir una fila se marcan sus "
                                          "formas en el dibujo.", parts=parts))

    def sort_rows(self) -> None:
        """Pone las filas en el orden correcto: taladros, pozos, interiores, lineas y exteriores."""
        p = self.project
        if p.drawing is None:
            return
        key = self.ops.current_key()                      # antes de ordenar: la lista es la misma que muestra la tabla
        changed = p.sort_rows()
        self._quiet_rows = True
        self.ops.bind(p.rows, keep_key=key)
        self._quiet_rows = False
        self.refresh_all()
        problem = p.order_problem()
        self.statusBar().showMessage(
            (_("Filas ordenadas: taladros, pozos, interiores, líneas y exteriores.") if changed
             else _("Las filas ya estaban en el orden correcto.")) if not problem else problem)

    def _groups_changed(self, color, message: str) -> None:
        """Tras mover vectores: la tabla se rehace, la fila del grupo queda marcada y se recalcula."""
        self._quiet_rows = True
        self.ops.bind(self.project.rows, keep_key=(color, None) if color else None)
        self._quiet_rows = False
        self._update_selection_ui()
        self.refresh_all()
        self.statusBar().showMessage(message)

    def _detect_toggled(self, on: bool) -> None:
        if self.project.drawing is None:
            self.project.use_boards = on
            return
        data = self.project.to_dict()
        path = self.project.drawing.path
        dirty = self._dirty
        self.project.use_boards = on
        try:
            if self.project.scratch:
                self.project.new_drawing(path.parent, path.name)
            else:
                self.project.load_svg(path)
            self.project.apply_dict(data)
        except Exception as e:
            self._error(_("No se pudo volver a leer el dibujo"), str(e))
            return
        self._after_drawing_loaded()
        self._dirty = dirty

    # --- reacciones a los cambios -------------------------------------------------------------------------------
    def _settings_changed(self) -> None:
        self.ops.set_hints(self.project.tool_hints(for_rows=True))
        self.ops.refresh_enabled()
        self._sync_board_checks()
        self._update_generate_enabled()
        self._refresh_bridges()
        self.request_plan()

    def _rows_changed(self) -> None:
        self._update_ops_note()
        sig = tuple(ignored_colors(self.project.rows))
        if sig != self._ignored_sig:
            self._ignored_sig = sig
            if self.view == "drawing":
                self.refresh_canvas(keep=True)
        else:
            self._refresh_bridges()
        self.request_plan()

    def _refresh_bridges(self) -> None:
        """En la vista del dibujo, repinta si los puentes que se ven ya no son los que toca (una casilla de la tabla, la
        anchura o la separación, la fresa o el lado de una fila los cambian sin tocar las formas)."""
        if self.view == "drawing" and self.project.drawing is not None and self.project.bridge_map() != self.canvas.bridges_shown():
            self.refresh_canvas(keep=True)

    def apply_suggested_offset(self) -> None:
        plan = self.last_plan
        if plan is None or self.current not in self.project.overrides:
            return
        offset = self.project.suggest_offset(plan, self.current)
        if offset is None:
            return
        self.project.overrides[self.current].offset = tuple(float(v) for v in offset)
        self.job.refresh()
        self.request_plan()

    def _update_ops_note(self) -> None:
        rows = self.project.rows
        if not rows:
            self.ops.set_note("")
        elif plain(rows):
            self.ops.set_note(_("Todo son cortes pasantes normales: se corta todo junto, como sin receta."))
        else:
            n = sum(1 for r in rows if r.active)
            self.ops.set_note(_("Receta de {n} operación(es), en el orden de la tabla. Lo que está en Ignorar no se corta.", n=n))
        # en el orden de la tabla solo corta lo que tiene receta; con todo en corte normal el programa ya ordena por su cuenta
        self.ops.set_order_warning(self.project.order_problem() or "")

    def _update_generate_enabled(self) -> None:
        has = self.project.drawing is not None and bool(self.project.selected_numbers())
        self.generate_btn.setEnabled(has)
        self.act_generate.setEnabled(has)
        self.act_save.setEnabled(self.project.drawing is not None)

    # --- calculo en segundo plano -------------------------------------------------------------------------------
    def request_plan(self) -> None:
        if self.project.drawing is None:
            return
        self._token += 1
        self.summary.setText(_("<span style='color:{MUTED}'>Calculando…</span>", MUTED=theme.MUTED))
        self._timer.start()

    def _start_plan(self) -> None:
        if self.project.drawing is None:
            return
        token = self._token
        task = PlanTask(token, lambda: self._token, self.project.snapshot(), self.current, self._signals)
        self._pool.start(task)

    def plan_now(self) -> JobPlan:
        """Calcula ahora mismo, sin esperar ni usar el hilo (para las pruebas y para exportar)."""
        self._timer.stop()
        self._token += 1
        plan = self.project.plan(self.current)
        self._apply_plan(self._token, self.current, plan, preview_ops(plan))
        return plan

    def wait_for_plan(self, timeout_ms: int = 20000) -> None:
        """Espera a que termine el calculo pendiente (para las pruebas)."""
        import time
        end = time.monotonic() + timeout_ms / 1000
        while (self._timer.isActive() or self._pool.activeThreadCount()) and time.monotonic() < end:
            QApplication.processEvents()
            time.sleep(0.01)
        QApplication.processEvents()

    def _plan_done(self, token: int, number, plan: JobPlan, previews) -> None:
        self._apply_plan(token, number, plan, previews)

    def _plan_failed(self, token: int, detail: str) -> None:
        if token != self._token:
            return
        self.last_plan = None
        self._show_machine_frame(None)
        self.summary.setText(_("<span style='color:{ERROR}'>✖ Fallo interno al calcular</span>", ERROR=theme.ERROR))
        self.messages.show_text(_("Fallo interno de la aplicación al calcular (copia este texto si hay que arreglarlo):\n")
                                + detail, "error")
        self._update_messages_tab()

    def _apply_plan(self, token: int, number, plan: JobPlan, previews) -> None:
        if token != self._token or number != self.current:
            return
        self.last_plan, self.last_previews, self.last_number = plan, previews, number
        self._show_machine_frame(plan)
        offset = self.project.suggest_offset(plan, number) if number in self.project.overrides else None
        msgs = list(self._extra_notes) + list(self.project.drawing.messages) + gui_messages(plan)
        self.messages.show_messages(msgs, offset)
        self._update_messages_tab()
        self._update_summary(plan)
        if self.view == "machine":
            self.refresh_canvas(keep=not self._fit_next)
        self._update_hold()
        self._fit_next = False

    def _wheel_toggled(self, on: bool) -> None:
        self.canvas.wheel_inverted = on
        self._set_pref("wheel", "1" if on else "0")

    def _grid_toggled(self, on: bool) -> None:
        self.canvas.set_grid_visible(on)
        self._set_pref("grid", "1" if on else "0")

    def _frame_toggled(self, on: bool) -> None:
        self.canvas.set_frame_visible(on)
        self._update_legend()
        self._set_pref("frame", "1" if on else "0")

    def _show_machine_frame(self, plan: Optional[JobPlan]) -> None:
        """El recorrido de la maquina elegida, en puntos sobre el dibujo del tablero que se ve (sin maquina, nada)."""
        frame, caption = (plan.frame if plan is not None else ()), ""
        if frame:
            try:
                caption = Canvas._frame_caption(self.project.resolve_machine())
            except ProjectError:
                frame = ()
        self.canvas.set_machine_frame(frame, caption)
        self._update_legend()

    def _update_summary(self, plan: JobPlan) -> None:
        label = next((u.label for u in self.project.units() if u.number == self.current), "")
        self.summary.setToolTip("")
        if not plan.ok:
            self.summary.setText(_("<span style='color:{ERROR}'>✖ {error}</span>", ERROR=theme.ERROR, error=plan.error))
            return
        minx, miny, maxx, maxy = plan.result.bounds
        warn = sum(1 for m in plan.messages if m.level == "warning" and not m.severe)
        parts = [_("{label}: {count} líneas", label=label, count=len(plan.text.splitlines())), _("Z mín {min_z:.1f} mm", min_z=plan.result.min_z),
                 _("trayectoria {v:.0f} × {v2:.0f} mm", v=maxx - minx, v2=maxy - miny)]
        if plan.time is not None:
            parts.append(_("≈ {text} de máquina", text=plan.time.text))
            self.summary.setToolTip(describe_time(plan.time))
        html = f"<span style='color:{theme.OK}'>✔</span> " + " · ".join(parts)
        if plan.blockers:
            more = " " + _("(y {count} más)", count=len(plan.blockers) - 1) if len(plan.blockers) > 1 else ""
            html += f" · <b style='color:{theme.ERROR}'>✖ " + _("No se puede generar: {blockers}", blockers=plan.blockers[0] + more) + "</b>"
        if plan.alarms and not plan.blockers:
            what = _("se confirman al generar") if cl.ENABLED else _("mira Mensajes")
            html += f" · <b style='color:{theme.ERROR}'>⚠ " + _("{count} alarma(s): {what}", count=len(plan.alarms), what=what) + "</b>"
        if warn:
            html += f" · <span style='color:{theme.WARN}'>⚠ " + _("{warn} aviso(s)", warn=warn) + "</span>"
        self.summary.setText(html)

    def _update_messages_tab(self) -> None:
        w, e = self.messages.warnings, self.messages.errors
        text = _("Mensajes") + (f"  ✖ {e}" if e else "") + (f"  ⚠ {w}" if w else "")
        self.tabs.setTabText(1, text)

    # --- pintar -----------------------------------------------------------------------------------------------------
    def refresh_all(self, fit: bool = False) -> None:
        self.ops.set_hints(self.project.tool_hints(for_rows=True))
        self._update_ops_note()
        self._ignored_sig = tuple(ignored_colors(self.project.rows))
        self._update_generate_enabled()
        if self.project.drawing is None:
            self.canvas.show_message(_("Abre un dibujo SVG\n(Archivo → Abrir, o arrástralo aquí)"))
            return
        self._fit_next = fit or self._fit_next
        if fit:
            self.canvas.set_machine_frame((), "")                     # el de un dibujo anterior no vale; llega con el plan
        self.refresh_canvas(keep=not fit)
        self.request_plan()

    def _update_legend(self) -> None:
        parts = []
        if self.view == "machine":
            kinds = [k for k in theme.KIND_COLORS if any(op.kind == k for op in self.last_previews)]
            parts = [_("<span style='color:{KIND_COLORS}'>■</span> {KIND_LABELS}", KIND_COLORS=theme.KIND_COLORS[k], KIND_LABELS=theme.KIND_LABELS[k]) for k in kinds]
            if getattr(getattr(self.last_plan, "result", None), "marks", None):
                parts.append(_("<span style='color:{ERROR}'>◯</span> falta de espacio o punto agudo", ERROR=theme.ERROR))
        elif self.canvas.frame_text():
            parts = [_("<span style='color:{MACHINE}'>● ● ●</span> {frame_text} <span style='color:{ERROR}'>+</span> "
                       "cero", MACHINE=theme.MACHINE, frame_text=self.canvas.frame_text(), ERROR=theme.ERROR)]
        self.legend.setText("  ".join(parts))

    def refresh_canvas(self, keep: bool = False) -> None:
        d = self.project.drawing
        if d is None:
            return
        self._update_legend()
        if self.view == "drawing":
            self.canvas.show_drawing(self.project.shapes(), d.layout.boards, ignored_keys=ignored_colors_keys(self.project),
                                     selected_board=self.current, highlight_key=self.ops.current_key(), keep_view=keep,
                                     selected=self.selection, titles={r.key: r.title for r in self.project.rows if r.group},
                                     bridges=self.project.bridge_map())
            return
        plan = self.last_plan
        if plan is None:
            self.canvas.show_message(_("Calculando la trayectoria…"))
        elif not plan.ok:
            self.canvas.show_message(_("Sin trayectoria:\n{error}", error=plan.error or ""))
        else:
            self.canvas.show_machine(plan, self.last_previews, plan.job.machine, plan.job.tool.diameter,
                                     show_rapids=self.btn_rapids.isChecked(), keep_view=keep)
            self._update_hold()

    # --- sujecion ----------------------------------------------------------------------------------------------------
    def _hold_active(self) -> bool:
        return self.tabs.currentWidget() is self.hold_panel

    def _tab_changed(self, _index: int) -> None:
        if self._hold_active():
            self.canvas.set_hold_mode(True)
            self._grow_bottom_pane()
            if self.view != "machine":
                self.set_view("machine")              # los puntos se ven sobre la trayectoria
            self._update_hold()
        else:
            self.canvas.set_hold_mode(False)
            self._hold_view = None
            self._restore_bottom_pane()

    def _grow_bottom_pane(self) -> None:
        """La tabla de puntos necesita sitio: si el panel de abajo es bajito se hace mas alto mientras dure la pestaña."""
        top, bottom = self.splitter.sizes()
        want = min(HOLD_PANE_HEIGHT, top + bottom - CANVAS_MIN_HEIGHT)     # sin dejar el dibujo por debajo de un minimo
        if bottom < want:
            self._pane_before = bottom
            self.splitter.setSizes([top + bottom - want, want])
            self._pane_set = self.splitter.sizes()[1]

    def _restore_bottom_pane(self) -> None:
        """Al salir se devuelve su altura al panel, salvo que se haya movido a mano mientras tanto."""
        before, self._pane_before = self._pane_before, None
        top, bottom = self.splitter.sizes()
        if before is not None and abs(bottom - self._pane_set) <= 3:
            self.splitter.setSizes([top + bottom - before, before])

    def _hold_analysis(self, plan: JobPlan) -> HoldAnalysis:
        """El analisis de sujecion del plan actual; si lo unico que cambio fue algo que no mueve la geometria (un
        avance, una operacion de otro color), se reutiliza el anterior con la propuesta que ya tenia calculada."""
        cache = self._hold_cache
        if cache is not None and cache[0] is plan and cache[1].settings == self.project.hold:
            return cache[1]
        if cache is not None and cache[0] is plan:
            fresh = HoldAnalysis(cache[1].zones, self.project.hold)      # mismo plan, mismas zonas: solo cambian los ajustes
        else:
            fresh = analyse_plan(plan, self.last_previews, self.project.hold)
        if cache is not None and cache[1].key == fresh.key:
            fresh = cache[1]
        self._hold_cache = (plan, fresh)
        return fresh

    def _update_hold(self) -> None:
        """Calcula la sujecion del tablero que se ve y la pone en la pestaña y sobre el lienzo."""
        if not self._hold_active():
            return
        panel, p, plan = self.hold_panel, self.project, self.last_plan
        self._hold_view = None
        if p.drawing is None:
            panel.set_available(False, _("Abre un dibujo con tableros para calcular la sujeción."))
        elif plan is None:
            panel.set_available(False, _("Calculando la trayectoria…"))
        elif not plan.ok:
            panel.set_available(False, _("No hay trayectoria que respetar: {error}", error=plan.error or _("sin detalle")))
        elif not plan.stock:
            panel.set_available(False, _("Este dibujo no tiene tablero (un rectángulo con el dibujo dentro) y el cero no está "
                                       "en la esquina inferior izquierda de lo que se corta: sin eso no se sabe dónde está "
                                       "el borde de la plancha."))
        else:
            try:
                p.hold.validate()
            except HoldError as e:
                panel.set_available(False, _("Ajuste de sujeción que no vale: {e}", e=e))
                return
            analysis = self._hold_analysis(plan)
            edited = p.hold_points.get(self.current)
            points = board_points(analysis, edited)
            checks = [analysis.check(x, y) for x, y in points]
            self._hold_view = (analysis, points, checks)
            panel.show_points(points, checks)
            info = self._hold_info(analysis, points, checks, edited is not None)
            if plan.virtual_stock:
                info = self._virtual_board_note(plan) + "<br>" + info
            panel.set_info(info)
        self._hold_draw_layer()

    def _virtual_board_note(self, plan: JobPlan) -> str:
        b = plan.stock[0].shape.bounds
        return (_("<span style='color:{WARN}'>ℹ Sin recuadro: se supone una plancha de {b:.0f} x {b2:.0f} mm, del "
                  "cero a lo que se corta más {origin_margin:g} mm. Si la tuya es mayor, sube «Cero a (mm) del "
                  "dibujo» (en Fresa y material).</span>", WARN=theme.WARN, b=b[2], b2=b[3], origin_margin=self.project.general.origin_margin))

    def _hold_info(self, analysis: HoldAnalysis, points, checks, edited: bool) -> str:
        n, bad = len(points), sum(1 for c in checks if not c.ok)
        first = (_("<b>{n} punto(s)</b> editados a mano", n=n) if edited else _("<b>{n} punto(s)</b> propuestos", n=n))
        if bad:
            first += f" · <span style='color:{theme.ERROR}'><b>✖ " + _("{bad} no es(son) seguro(s)", bad=bad) + "</b></span>"
        lines = [first]
        reach = analysis.settings.reach
        if analysis.free.is_empty:
            lines.append(_("<span style='color:{ERROR}'>✖ Con estos márgenes no queda sitio libre en la plancha.</span>", ERROR=theme.ERROR))
        elif not n:
            lines.append(_("<span style='color:{WARN}'>⚠ Sin puntos: la plancha queda sin sujetar.</span>", WARN=theme.WARN))
        else:
            good = [pt for pt, c in zip(points, checks) if c.ok]          # un punto en rojo no sujeta nada
            gap, _where = analysis.worst_gap(good)
            if analysis.gap_ok(good):
                lines.append(_("<span style='color:{OK}'>✔ Ningún sitio de la plancha a más de {reach:g} mm de un punto (el más "
                               "lejos: {gap:.0f} mm).</span>", OK=theme.OK, reach=reach, gap=gap))
            else:
                lines.append(_("<span style='color:{WARN}'>⚠ Hay sitios a {gap:.0f} mm de un punto (alcance {reach:g} "
                               "mm).</span>", WARN=theme.WARN, gap=gap, reach=reach))
            close = [i + 1 for i, c in enumerate(checks) if c.ok and c.warn]
            if close:
                lines.append(f"<span style='color:{theme.WARN}'>⚠ " + _n(
                    "Punto {join} más cerca del corte de lo cómodo ({cut_clear:g} mm): un error pequeño al clavar podría dar en el corte.",
                    "Puntos {join} más cerca del corte de lo cómodo ({cut_clear:g} mm): un error pequeño al clavar podría dar en el corte.",
                    len(close), join=', '.join(map(str, close)), cut_clear=analysis.settings.cut_clear) + "</span>")
            need = analysis.required_points()
            if len(good) < need:
                if edited:
                    lines.append(_("<span style='color:{WARN}'>⚠ Hay {count} punto(s) seguro(s) y el mínimo para una plancha de este "
                                   "tamaño es {need}.</span>", WARN=theme.WARN, count=len(good), need=need))
                else:
                    lines.append(_("<span style='color:{WARN}'>⚠ Solo se han podido proponer {count} punto(s) (el mínimo para una "
                                   "plancha de este tamaño es {need}): no cabe otro a {min_gap:g} mm o más de los demás.</span>", WARN=theme.WARN, count=len(good), need=need, min_gap=analysis.settings.min_gap))
        loose = analysis.unreachable_fragments()
        if loose:
            lines.append(_("<span style='color:{WARN}'>⚠ Hay {count} trozo(s) de desecho grande(s) ({join} cm²) donde no cabe "
                           "ningún punto con estos márgenes.</span>", WARN=theme.WARN, count=len(loose), join=', '.join(str(a) for a in loose)))
        return "<br>".join(lines)

    def _hold_draw_layer(self) -> None:
        if self._hold_view is None or not self._hold_active():
            self.canvas.clear_hold_layer()
            return
        analysis, points, checks = self._hold_view
        current = self.hold_panel.current()
        self.canvas.set_hold_layer(analysis.free, [(x, y, c.ok) for (x, y), c in zip(points, checks)],
                                   current if current >= 0 else None)

    def _set_hold_points(self, points) -> None:
        self.project.hold_points[self.current] = [(float(x), float(y)) for x, y in points]
        self._update_hold()

    def hold_add(self, x: float, y: float) -> bool:
        """Anade un punto si ahi se puede sujetar; si no, dice por que (en la barra de estado)."""
        if self._hold_view is None:
            return False
        analysis, points, _checks = self._hold_view
        x, y = round(x, 1), round(y, 1)
        c = analysis.check(x, y)
        if not c.ok:
            self.statusBar().showMessage(_("X{x:g} Y{y:g}: no se puede sujetar ahí, {reason}.", x=x, y=y, reason=c.reason))
            return False
        self._set_hold_points(list(points) + [(x, y)])
        self.hold_panel.select(len(points))
        self.statusBar().showMessage(_("Punto {v} añadido en X{x:g} Y{y:g}.", v=len(points) + 1, x=x, y=y))
        return True

    def hold_remove(self, index: int) -> None:
        if self._hold_view is None:
            return
        points = list(self._hold_view[1])
        if 0 <= index < len(points):
            gone = points.pop(index)
            self._set_hold_points(points)
            self.statusBar().showMessage(_("Punto quitado (X{gone:g} Y{gone2:g}).", gone=gone[0], gone2=gone[1]))

    def hold_edit(self, index: int, x: float, y: float) -> None:
        """Una coordenada escrita en la tabla: se admite aunque no sea segura (sale en rojo y avisa al generar)."""
        if self._hold_view is None:
            return
        points = list(self._hold_view[1])
        if not 0 <= index < len(points):
            self._update_hold()                    # lo escrito no era un numero: se repinta la tabla como estaba
            return
        points[index] = (round(x, 1), round(y, 1))
        self._set_hold_points(points)

    def hold_reset(self) -> None:
        self.project.hold_points.pop(self.current, None)
        self._update_hold()
        self.statusBar().showMessage(_("Vuelven los puntos propuestos para este tablero."))

    def _hold_settings_changed(self) -> None:
        try:
            new = self.hold_panel.settings()
            new.validate()
        except HoldError as e:
            self.hold_panel.set_info(_("<span style='color:{ERROR}'>✖ {e}</span>", ERROR=theme.ERROR, e=e))
            return
        self.project.hold = new
        self._update_hold()

    def _hold_hover(self, x: float, y: float) -> None:
        if self._hold_view is None:
            return
        c = self._hold_view[0].check(x, y)
        self.canvas.set_hold_ghost(x, y, c.ok)
        if c.ok:
            text = (_("se puede sujetar aquí, pero {warn} (clic para añadir un punto)", warn=c.warn) if c.warn
                    else _("se puede sujetar aquí (clic para añadir un punto)"))
        else:
            text = c.reason
        self.statusBar().showMessage(_("X{x:.1f} Y{y:.1f}: {text}", x=x, y=y, text=text))

    def generate_marks(self, path=None, confirm: bool = True) -> Optional[Path]:
        """Escribe el programa de marcas del tablero que se ve. `path` y `confirm=False` sirven para probarlo sin
        cuadros de dialogo."""
        if self._hold_view is None:
            self._error(_("Marcas de sujeción"), _("Abre la pestaña Sujeción con un dibujo calculado."))
            return None
        analysis, points, checks = self._hold_view
        if not points:
            self._error(_("Marcas de sujeción"), _("No hay puntos que marcar."))
            return None
        bad = [i + 1 for i, c in enumerate(checks) if not c.ok]
        if bad and confirm:
            if QMessageBox.question(self, _("Puntos que no son seguros"),
                                    _("Estos puntos caen donde no se debe sujetar: {points}.\n\n¿Generar las marcas igualmente?",
                                      points=", ".join(map(str, bad))), QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No) != QMessageBox.Yes:
                return None
        if path is None:
            folder = self.folder_for(fo.GCODE)
            if folder is None:
                return None
            suggested = self.project.marks_path(Path(folder) / f"{self.project.drawing.path.stem}.nc", self.current)
            chosen, __ = QFileDialog.getSaveFileName(self, _("Guardar G-code de marcas"), str(suggested),
                                                    _("G-code (*.nc *.gcode *.ngc);;Todos (*)"),
                                                    options=QFileDialog.DontConfirmOverwrite)
            if not chosen:
                return None
            path = Path(chosen) if Path(chosen).suffix else Path(chosen).with_suffix(".nc")
        path = Path(path)
        if path.exists() and confirm:
            if QMessageBox.question(self, _("Sustituir archivo"), _("Ya existe:\n{path}\n\n¿Sustituirlo?", path=path),
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return None
        try:
            text = self.project.marks_text(self.current, points)
            path.write_text(text, encoding="ascii")
        except (ProjectError, OSError) as e:
            self._error(_("No se pudo escribir las marcas"), str(e))
            return None
        self._remember_folder(path)
        self.messages.show_messages([
            Message(_("Marcas guardadas en {path}: {count} punto(s), de lejos a cerca (de Y mayor a menor).", path=path, count=len(points))),
            Message(_("En UGS: abre el archivo con el husillo PARADO y ejecútalo; en cada pausa (M0) marca el punto bajo "
                    "la punta y pulsa Reanudar. Al acabar, retira el puente y clava o atornilla en las marcas.")),
            *([Message(_("Los puntos {join} no son seguros.", join=', '.join(map(str, bad))), "warning")] if bad else [])])
        self._update_messages_tab()
        self.statusBar().showMessage(_("Marcas guardadas en {path}", path=path))
        return path

    # --- generar -----------------------------------------------------------------------------------------------------
    def generate(self, base=None, confirm: bool = True) -> list[Path]:
        """Calcula los tableros marcados y escribe un .nc de cada uno. `base` y `confirm=False` sirven para
        probarlo sin cuadros de dialogo."""
        if self.project.drawing is None:
            return []
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            try:
                plans = self.project.plan_selected()
            except ProjectError as e:
                QApplication.restoreOverrideCursor()
                self._error(_("No se puede generar"), str(e))
                return []
        finally:
            QApplication.restoreOverrideCursor()
        failed = [(n, p) for n, p in plans if not p.ok]
        if failed:
            self._error(_("No se puede generar"), "\n".join(
                (_("Tablero {n}: ", n=n) if n is not None else "") + (p.error or "") for n, p in failed))
            return []
        blocked = [(n, why) for n, p in plans for why in p.blockers]
        if blocked:                                       # un tope no se salta: se corrige el dato
            self._error(_("No se puede generar"), "\n".join((_("Tablero {n}: ", n=n) if n is not None else "") + why
                                                         for n, why in blocked))
            return []
        many = [(n, p.result.excess_text) for n, p in plans if p.result.excess]
        if many and confirm:                              # un dibujo que no es para esta fresa: no se genera sin decirlo claro
            what = "\n\n".join((_("Tablero {n}: ", n=n) if n is not None else "") + text for n, text in many)
            if QMessageBox.warning(self, _("Demasiadas zonas con problema"), what + "\n\n" + _("¿Generar de todos modos?"),
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return []
        if confirm and cl.ENABLED:                        # desconectada de momento: ver cnc/checklist.py
            if not self._run_checklist(build_checklist(plans)):
                return []
        if base is None:
            base = self._ask_base(plans)
            if base is None:
                return []
        targets = [Project.output_path(base, n, len(plans)) for n, __ in plans]
        existing = [t for t in targets if t.exists()]
        if existing and confirm:
            if QMessageBox.question(self, _("Sustituir archivos"),
                                    _("Ya existe:\n{files}\n\n¿Sustituirlo?", files="\n".join(str(t) for t in existing)),
                                    QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No) != QMessageBox.Yes:
                return []
        try:
            written = self.project.write(base, plans)
        except (ProjectError, OSError) as e:
            self._error(_("No se pudo escribir el G-code"), str(e))
            return []
        self._remember_folder(base)
        lines = [_("{path}: {count} líneas, Z mínima {min_z:.3f} mm, ≈ {text} de máquina", path=path, count=len(p.text.splitlines()), min_z=p.result.min_z, text=p.time.text)
                 for path, (__, p) in zip(written, plans)]
        warnings = [Message(gm.text if n is None else _("Tablero {n}: {text}", n=n, text=gm.text), "warning")
                    for n, p in plans for gm in gui_messages(p) if gm.level == "warning" or gm.severe]
        self.messages.show_messages([Message(_("G-code generado:"))] + [Message("  " + t) for t in lines] + warnings)
        self._update_messages_tab()
        self.statusBar().showMessage(_("G-code generado: {join}", join=', '.join(p.name for p in written)))
        if confirm:
            body = "\n".join(lines)
            if warnings:
                body += "\n\n" + _("Avisos:") + "\n" + "\n".join(m.text for m in warnings)
            QMessageBox.information(self, _("G-code generado"), body + "\n\n" + _("Abre el archivo en UGS para enviarlo."))
        return written

    def _run_checklist(self, checklist) -> bool:
        """La lista de comprobacion antes de generar: True si se han marcado todas las casillas y se pulsa Generar. Sin
        ventana de verdad (pruebas) no se genera, que es lo que no estropea nada."""
        if self.settings is None:
            return False
        return ChecklistDialog(checklist, self).exec() == QDialog.Accepted

    def _ask_base(self, plans) -> Optional[Path]:
        stem = self.project.drawing.path.stem
        folder = self.folder_for(fo.GCODE)
        if folder is None:
            return None
        suggested = Path(folder) / f"{stem}.nc"
        title = (_("Guardar G-code (se añade _tablero1, _tablero2… a cada uno)") if len(plans) > 1 else _("Guardar G-code"))
        path, __ = QFileDialog.getSaveFileName(self, title, str(suggested), _("G-code (*.nc *.gcode *.ngc);;Todos (*)"),
                                              options=QFileDialog.DontConfirmOverwrite)
        if not path:
            return None
        p = Path(path)
        return p if p.suffix else p.with_suffix(".nc")

    # --- varios -------------------------------------------------------------------------------------------------------
    def _folder(self) -> str:
        if self.project.drawing is not None:
            return str(self.project.drawing.path.parent)
        if self.settings is not None:
            return str(self.settings.value("folder", str(Path.home())))
        return str(Path.home())

    def _remember_folder(self, path) -> None:
        if self.settings is not None:
            self.settings.setValue("folder", str(Path(path).parent))

    def _error(self, title: str, text: str) -> None:
        self.messages.show_text(text, "error")
        self._update_messages_tab()
        self.statusBar().showMessage(f"{title}: {text.splitlines()[0] if text else ''}")
        if self.settings is not None:                           # en las pruebas no hay nadie para pulsar Aceptar
            QMessageBox.warning(self, title, text)

    def about(self) -> None:
        QMessageBox.about(self, "cnc_app", _("cnc_app {version}\n\nDe SVG a G-code para GRBL 1.1 (fresadora de 2D).\nLos envíos a la máquina se "
                                             "hacen con UGS.", version=__version__))

    def _only_the_window_takes_drops(self) -> None:
        """Qt entrega una suelta al primer widget (de dentro afuera) que acepte soltar cosas; el lienzo y los campos de
        texto lo aceptan por defecto, la rechazan y la ventana nunca llega a verla. Solo la ventana acepta soltar."""
        for w in self.findChildren(QWidget):
            w.setAcceptDrops(False)

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if urls and urls[0].toLocalFile().lower().endswith((".svg", JOB_SUFFIX)):
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if not urls:
            return
        path = urls[0].toLocalFile()
        if not path.lower().endswith((".svg", JOB_SUFFIX)):
            return
        event.acceptProposedAction()
        self.open_path_asking(path)

    def open_path_asking(self, path) -> None:
        """Abre un dibujo o un trabajo guardado que viene de fuera (arrastrado, o pasado por otro arranque): antes pregunta
        si hay cambios sin guardar, igual que Archivo -> Abrir."""
        if not self._confirm_discard():
            return
        self.open_job(path) if str(path).lower().endswith(JOB_SUFFIX) else self.open_svg(path)

    def fit_to_screen(self) -> None:
        """En una pantalla pequeña (un portatil de 1366 x 768) la ventana de 1400 x 880 no cabe: se ajusta al espacio libre
        (sin barra de tareas) y se mete entera en la pantalla. Una ventana maximizada ya ocupa lo que hay."""
        screen = self.screen()
        if screen is None or self.isMaximized() or self.isFullScreen():
            return
        frame = self.frameGeometry()
        fitted = fit_rect(frame, screen.availableGeometry())
        if fitted == frame:
            return
        self.resize(self.width() - (frame.width() - fitted.width()), self.height() - (frame.height() - fitted.height()))
        self.move(fitted.topLeft())

    # --- una sola ventana ------------------------------------------------------------------------------------------
    def bring_to_front(self) -> None:
        if self.isMinimized():
            self.setWindowState((self.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        self.show()
        self.raise_()
        self.activateWindow()

    def receive_external(self, paths) -> None:
        """Otro arranque de la aplicacion (por ejemplo un doble clic en un .cncjob con la ventana abierta) nos pasa un
        archivo: en vez de abrir otra ventana, esta se pone delante y lo abre. Con un cuadro abierto (guardar, lista de
        comprobacion, fichas...) espera a que se cierre para no cambiar el trabajo por debajo de el."""
        self.bring_to_front()
        path = next((p for p in paths if str(p).lower().endswith((".svg", JOB_SUFFIX))), None)
        if path is None:
            if paths:
                self.statusBar().showMessage(_("No se abrió {name}: solo se abren dibujos .svg y trabajos {JOB_SUFFIX}.", name=Path(paths[0]).name, JOB_SUFFIX=JOB_SUFFIX))
            return
        self._pending_external, self._pending_tries = path, 0
        self._open_external_when_free()

    def _open_external_when_free(self) -> None:
        if self._pending_external is None:
            return
        if QApplication.activeModalWidget() is not None:
            self._pending_tries += 1
            if self._pending_tries <= PENDING_EXTERNAL_TRIES:
                self._pending_timer.start(PENDING_EXTERNAL_MS)
            else:
                self._pending_external = None                  # nadie cerro el cuadro en mucho rato: se deja
            return
        path, self._pending_external = self._pending_external, None
        self.open_path_asking(path)

    def closeEvent(self, event):
        if not self._can_close():
            event.ignore()
            return
        self._timer.stop()
        self._token += 1
        self._pool.waitForDone(5000)
        if self.settings is not None:
            self.settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(event)


def fit_rect(frame: QRect, available: QRect) -> QRect:
    """El marco de la ventana (con barra de titulo) recortado y movido para que quepa en el espacio libre de la pantalla."""
    w, h = min(frame.width(), available.width()), min(frame.height(), available.height())
    x = min(max(frame.x(), available.left()), available.left() + available.width() - w)
    y = min(max(frame.y(), available.top()), available.top() + available.height() - h)
    return QRect(x, y, w, h)


def ignored_colors_keys(project: Project) -> set:
    """Claves (trazo, relleno) de las filas en Ignorar, para atenuarlas en el dibujo."""
    return {r.key for r in project.rows if not r.active}
