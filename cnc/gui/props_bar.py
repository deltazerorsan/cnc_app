"""La tira de medidas de lo elegido, sobre el lienzo: se ven los numeros de la figura y se cambian escribiendolos.

Una figura (o varias, como una sola caja) se da por un punto de la caja que la rodea (el que se elige en el selector de nueve
puntos: por omision la esquina inferior izquierda, y el centro para un circulo) y por su ancho y alto; un circulo, ademas, por su
diametro o su radio. Cuando cambia el tamano, el punto elegido es el que no se mueve. Escribir un numero y pulsar Intro (o pasar a
otra casilla) hace el cambio como cualquier otra edicion del dibujo, con deshacer. Lo que no se toca se manda tal cual es, sin el
redondeo del texto. A la derecha estan los botones de alinear varias figuras (los pone la ventana, que los comparte con el menu).
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from ..drawing_edit import MEASURE_TOL, Measures, box_corner
from .align_icons import AlignButton
from .anchor_picker import AnchorPicker, point_name
from .widgets import OptionalNumber
from ..i18n import _

KIND_NAMES = {"rect": _("Rectángulo"), "circle": _("Círculo"), "ellipse": _("Elipse"), "polygon": _("Polígono"), "line": _("Línea"),
              "polyline": _("Polilínea"), "path": _("Forma")}
FIELD_WIDTH = 72
NO_SELECTION = _("Elige una forma (clic, Ctrl+clic o recuadro con Mayús) para ver y cambiar sus medidas.")

BOX_LABELS = ("X", "Y", _("Ancho"), _("Alto"))
CIRCLE_LABELS = ("X", "Y", _("Diámetro"), _("Radio"))
DEFAULT_BOX, DEFAULT_CIRCLE = (0.0, 0.0), (0.5, 0.5)      # el punto de partida: la esquina inferior izquierda, o el centro de un circulo


def tips_for(circle: bool, point: str) -> tuple:
    """Lo que dice cada casilla al pasar el raton, segun el punto elegido (`point`: su nombre, "el centro"...)."""
    keep = _("El punto elegido ({point}) no se mueve.", point=point)
    if circle:
        return (_("Posición X de {point} del círculo (mm)", point=point), _("Posición Y de {point} del círculo (mm)", point=point),
                _("Diámetro del círculo (mm): crece o se encoge sin mover {point}. ", point=point) + keep,
                _("Radio del círculo (mm): crece o se encoge sin mover {point}. ", point=point) + keep)
    return (_("Posición X de {point} de la forma (mm)", point=point), _("Posición Y de {point} de la forma (mm)", point=point),
            _("Ancho de la forma (mm). ") + keep + _(" Si es un rectángulo con esquinas redondas, conservan su radio."),
            _("Alto de la forma (mm). ") + keep + _(" Si es un rectángulo con esquinas redondas, conservan su radio."))


def fmt_mm(value: float) -> str:
    """Un numero con hasta tres decimales y sin ceros que sobran (el programa no corta mas fino que eso)."""
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


class PropertiesBar(QWidget):
    applied = Signal(list, float, float, float, float)        # formas, X, Y, ancho, alto (de la caja nueva)
    problem = Signal(str)                                     # algo mal escrito: el motivo, para la barra de estado

    def __init__(self, parent=None):
        super().__init__(parent)
        self._m: Optional[Measures] = None
        self._indices: list[int] = []
        self._exact: list[float] = [0.0, 0.0, 0.0, 0.0]       # lo que valen ahora las cuatro casillas, sin redondear
        self._shown: list[str] = ["", "", "", ""]             # y como se escriben (lo que hay en la casilla si no se toca)
        self._circle = False
        self._busy = False
        self._anchor: Optional[tuple] = None                   # el punto elegido con el selector (None: aun no se ha tocado)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 0, 8, 0)
        row.setSpacing(4)
        self.kind_label = QLabel("")
        self.kind_label.setMinimumWidth(84)
        self.kind_label.setStyleSheet("font-weight: 600;")
        row.addWidget(self.kind_label)
        self.picker = AnchorPicker()
        self.picker.anchorChanged.connect(self._anchor_chosen)
        row.addWidget(self.picker)
        row.addSpacing(6)
        self.names: list[QLabel] = []
        self.edits: list[OptionalNumber] = []
        for i in range(4):
            name = QLabel("")
            name.setObjectName("muted")
            edit = OptionalNumber(negative=i < 2)
            edit.setFixedWidth(FIELD_WIDTH)
            edit.setAlignment(Qt.AlignRight)
            edit.editingFinished.connect(self._commit)
            self.names.append(name)
            self.edits.append(edit)
            row.addWidget(name)
            row.addWidget(edit)
            row.addSpacing(6)
        unit = QLabel("mm")
        unit.setObjectName("muted")
        row.addWidget(unit)
        row.addSpacing(8)
        self.align_box = QWidget()                             # los botones de alinear (se llenan con `set_align_actions`)
        al = QHBoxLayout(self.align_box)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(1)
        self.align_buttons: list[AlignButton] = []
        row.addWidget(self.align_box)
        row.addSpacing(8)
        self.info = QLabel("")
        self.info.setObjectName("muted")
        self.info.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)      # un texto largo no ensancha la ventana
        row.addWidget(self.info, 1)
        self.show_measures(None, [], NO_SELECTION)

    # --- el punto de referencia ---------------------------------------------------------------------------------------
    def anchor(self) -> tuple:
        """El punto de la caja al que se refieren X e Y: el elegido con el selector o, mientras no se haya tocado, el de partida
        (el centro para un circulo, la esquina inferior izquierda para lo demas). (0, 0) es la esquina inferior izquierda."""
        if self._anchor is not None:
            return self._anchor
        return DEFAULT_CIRCLE if self._circle else DEFAULT_BOX

    def _anchor_chosen(self, fx: float, fy: float) -> None:
        """Se ha elegido un punto en el selector: desde ahora vale para todo lo que se elija; las casillas lo muestran."""
        self._anchor = (fx, fy)
        self._refill()

    # --- los botones de alinear -----------------------------------------------------------------------------------------
    def set_align_actions(self, actions) -> None:
        """Pone un boton por cada accion de alinear (cada una lleva en `data` su lado); su estado, activo o no, es el de la accion."""
        for button in self.align_buttons:
            button.deleteLater()
        self.align_buttons = []
        for action in actions:
            button = AlignButton(action)
            self.align_box.layout().addWidget(button)
            self.align_buttons.append(button)

    # --- lo que se ve ---------------------------------------------------------------------------------------------
    def show_measures(self, m: Optional[Measures], indices, hint: str = NO_SELECTION) -> None:
        """Pone las medidas de lo elegido en las casillas (`m` None: no hay nada que medir y `hint` dice por que)."""
        self._m = m
        self._indices = list(indices)
        self._circle = m is not None and m.kind == "circle"
        fx, fy = self.anchor()
        self.picker.set_anchor(fx, fy)
        self.picker.setEnabled(m is not None)
        labels = CIRCLE_LABELS if self._circle else BOX_LABELS
        for name, edit, label, tip in zip(self.names, self.edits, labels, tips_for(self._circle, point_name(fx, fy))):
            name.setText(label)
            edit.setToolTip(tip)
        if m is None:
            self.kind_label.setText(_("Sin forma"))
            self.info.setText(hint)
            self.info.setToolTip(hint)
            for edit in self.edits:
                edit.setEnabled(False)
                if edit.text():
                    edit.setText("")
            self._exact = [0.0, 0.0, 0.0, 0.0]
            return
        px, py = m.point(fx, fy)
        self._exact = [px, py, m.width, m.width / 2] if self._circle else [px, py, m.width, m.height]
        if (fx, fy) == (0.5, 0.5):                            # lo que no se esta mirando: el otro punto que se echaria en falta
            other = _("Esquina: {x}, {y}", x=fmt_mm(m.x), y=fmt_mm(m.y))
            tip = _("Esquina inferior izquierda: {x}, {y}", x=fmt_mm(m.x), y=fmt_mm(m.y))
        else:
            other = tip = _("Centro: {x}, {y}", x=fmt_mm(m.cx), y=fmt_mm(m.cy))
        self._shown = [fmt_mm(v) for v in self._exact]
        self.kind_label.setText(_("{count} formas", count=m.count) if m.count > 1 else KIND_NAMES.get(m.kind, _("Forma")))
        self.info.setText(other)
        self.info.setToolTip(tip)
        for edit, text in zip(self.edits, self._shown):
            edit.setEnabled(True)
            if edit.text() != text and not (edit.hasFocus() and edit.isModified()):       # lo que se esta tecleando no se pisa
                edit.setText(text)

    # --- lo que se escribe ------------------------------------------------------------------------------------------
    def _commit(self) -> None:
        if self._busy or self._m is None:
            return
        self._busy = True
        for edit in self.edits:
            edit.setModified(False)                           # lo escrito ya se entrega: la tira puede ordenar el texto
        try:
            self._apply()
        finally:
            self._busy = False

    def _apply(self) -> None:
        m, ex = self._m, self._exact
        typed = [e.value() for e in self.edits]
        if any(v is None for v in typed):
            self.problem.emit(_("Falta una medida: escribe un número en cada casilla."))
            self._refill()
            return
        # Una casilla que se deja con el texto que tenia no cambia nada (aunque el numero de verdad tenga mas decimales que
        # los tres que se ven: 1,5875 se ve 1,587); una que se reescribe cambia si difiere de verdad de lo que vale.
        words = [e.text().strip().replace(",", ".") for e in self.edits]
        changed = [w != s and abs(v - e) > MEASURE_TOL for w, s, v, e in zip(words, self._shown, typed, ex)]
        if not any(changed):
            self._refill()                                    # nada cambia: solo se ordena el texto
            return
        if self._circle:
            if changed[2]:
                d = typed[2]
            elif changed[3]:
                d = 2 * typed[3]
            else:
                d = None
            if d is not None and d <= 0:
                self.problem.emit(_("El diámetro y el radio tienen que ser mayores que cero."))
                self._refill()
                return
            w, h = (m.width, m.height) if d is None else (d, d)
        else:
            w, h = (typed[2] if changed[2] else m.width), (typed[3] if changed[3] else m.height)
            if (changed[2] and w <= 0) or (changed[3] and h <= 0):
                self.problem.emit(_("El ancho y el alto tienen que ser mayores que cero."))
                self._refill()
                return
        # El punto elegido queda donde se escribio (o donde estaba, si no se toco) y la caja se mide a partir de el.
        fx, fy = self.anchor()
        px, py = (typed[0] if changed[0] else ex[0]), (typed[1] if changed[1] else ex[1])
        x, y = box_corner(px, py, fx, fy, w, h)
        self.applied.emit(list(self._indices), x, y, w, h)

    def _refill(self) -> None:
        """Vuelve a escribir en las casillas lo que vale de verdad (un valor rechazado o sin cambios)."""
        for edit in self.edits:
            edit.setModified(False)
        self.show_measures(self._m, self._indices, NO_SELECTION)
