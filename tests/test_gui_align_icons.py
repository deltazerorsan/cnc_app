"""Los iconos y los botones de alinear: se pintan como vectores del tamano que se pida (con cualquier escala de pantalla), salen
grises si estan apagados y el boton de la tira no depende del estilo de Windows (PySide6 sin pantalla)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QEvent, QPoint, QSize, Qt
from PySide6.QtGui import QAction, QColor, QIcon
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from cnc.drawing_edit import ALIGN_SIDES
from cnc.gui import align_icons as ai
from cnc.gui.theme import ACCENT, TEXT

SIDES = ai.ORDER


def alpha_box(img, threshold=40):
    """Caja (x0, y0, x1, y1) de lo pintado (no transparente) y su centro de masas (cx, cy) como fraccion del lado."""
    xs, ys, n = [], [], 0
    sx = sy = 0.0
    for y in range(img.height()):
        for x in range(img.width()):
            if img.pixelColor(x, y).alpha() > threshold:
                xs.append(x)
                ys.append(y)
                sx += x
                sy += y
                n += 1
    assert n
    return (min(xs), min(ys), max(xs), max(ys)), (sx / n / img.width(), sy / n / img.height())


def has_colour(img, colour, tol=40):
    want = QColor(colour)
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.alpha() > 200 and abs(c.red() - want.red()) + abs(c.green() - want.green()) + abs(c.blue() - want.blue()) < tol:
                return True
    return False


def make_button(qtbot, side="izquierda", enabled=True):
    action = QAction("Alinear")
    action.setData(side)
    action.setIcon(ai.align_icon(side))
    action.setToolTip(ai.TIPS[side])
    action.setEnabled(enabled)
    button = ai.AlignButton(action)
    qtbot.addWidget(button)
    button._action = action                                # la accion tiene que seguir viva
    button.show()
    return button


def grab(button):
    return button.grab().toImage()


def glyph_box(img, inset=3):
    """Caja de lo que difiere del fondo, dentro del boton y sin su borde: la del dibujo."""
    bg = img.pixelColor(img.width() // 2, 2)
    xs, ys = [], []
    for y in range(inset, img.height() - inset):
        for x in range(inset, img.width() - inset):
            c = img.pixelColor(x, y)
            if abs(c.red() - bg.red()) + abs(c.green() - bg.green()) + abs(c.blue() - bg.blue()) > 45:
                xs.append(x)
                ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


# --- el icono -------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("side", SIDES)
@pytest.mark.parametrize("size", [16, 20, 22, 32, 48])
def test_the_icon_is_drawn_at_the_size_asked_and_fills_it(qtbot, side, size):
    pm = ai.align_icon(side).pixmap(QSize(size, size))
    assert pm.width() == size and pm.height() == size
    (x0, y0, x1, y1), _ = alpha_box(pm.toImage())
    w, h = x1 - x0 + 1, y1 - y0 + 1
    assert max(w, h) >= 0.85 * size and min(w, h) >= 0.55 * size          # la raya llena el cuadro; las barras, buena parte
    assert x0 >= 0 and y0 >= 0 and x1 < size and y1 < size


@pytest.mark.parametrize("side", SIDES)
def test_on_a_sharp_screen_the_icon_has_twice_the_pixels_and_the_same_look(qtbot, side):
    icon = ai.align_icon(side)
    pm = icon.pixmap(QSize(20, 20), 2.0)
    assert pm.devicePixelRatio() == 2.0 and pm.width() == 40 and pm.height() == 40
    (x0, y0, x1, y1), _ = alpha_box(pm.toImage())
    w, h = x1 - x0 + 1, y1 - y0 + 1
    assert max(w, h) >= 34 and min(w, h) >= 22


def test_a_pixmap_of_another_shape_keeps_the_icon_square_and_centred(qtbot):
    pm = ai.align_icon("centro_x").pixmap(QSize(60, 30))
    assert pm.width() == 60 and pm.height() == 30
    (x0, y0, x1, y1), (cx, _) = alpha_box(pm.toImage())
    assert y1 - y0 >= 24 and x1 - x0 < 30 and abs(cx - 0.5) < 0.03


@pytest.mark.parametrize("side", SIDES)
def test_a_disabled_icon_is_grey_and_an_enabled_one_is_blue(qtbot, side):
    icon = ai.align_icon(side)
    assert has_colour(icon.pixmap(QSize(40, 40), QIcon.Normal).toImage(), ACCENT)
    off = icon.pixmap(QSize(40, 40), QIcon.Disabled).toImage()
    assert not has_colour(off, ACCENT) and not has_colour(off, "#23221f")
    assert alpha_box(off)[0] == alpha_box(icon.pixmap(QSize(40, 40), QIcon.Normal).toImage())[0]      # y del mismo tamano


@pytest.mark.parametrize("side, axis, low, high", [
    ("izquierda", 0, 0.0, 0.47), ("derecha", 0, 0.53, 1.0), ("centro_x", 0, 0.47, 0.53),
    ("arriba", 1, 0.0, 0.47), ("abajo", 1, 0.53, 1.0), ("centro_y", 1, 0.47, 0.53)])
def test_each_icon_means_what_it_says(qtbot, side, axis, low, high):
    """La mayor parte de lo pintado esta del lado por el que se alinea (la imagen tiene el 0 de Y arriba)."""
    _, centre = alpha_box(ai.align_icon(side).pixmap(QSize(80, 80)).toImage())
    assert low <= centre[axis] <= high, (side, centre)
    assert abs(centre[1 - axis] - 0.5) < 0.1


def test_the_six_icons_are_all_different(qtbot):
    images = [ai.align_icon(s).pixmap(QSize(40, 40)).toImage() for s in SIDES]
    for i, a in enumerate(images):
        for b in images[i + 1:]:
            assert a != b


def test_the_engine_can_be_cloned_and_keeps_its_side(qtbot):
    engine = ai.AlignIconEngine("abajo")
    clone = engine.clone()
    assert isinstance(clone, ai.AlignIconEngine) and clone.side == "abajo"
    icon = QIcon(clone)
    assert not icon.isNull() and icon.pixmap(QSize(30, 30)).toImage() == ai.align_icon("abajo").pixmap(QSize(30, 30)).toImage()


def test_the_order_names_and_tips_are_those_of_the_core(qtbot):
    assert set(SIDES) == set(ALIGN_SIDES) == set(ai.LABELS) == set(ai.TIPS) and len(SIDES) == 6
    assert SIDES == ("izquierda", "centro_x", "derecha", "arriba", "centro_y", "abajo")


# --- el boton -------------------------------------------------------------------------------------------------------------
def test_the_button_is_big_enough_to_hit_and_to_read(qtbot):
    b = make_button(qtbot)
    assert (b.width(), b.height()) == (ai.BUTTON, ai.BUTTON) and ai.BUTTON >= 28 and ai.GLYPH >= 20
    assert b.side == "izquierda" and b.defaultAction() is b._action


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("side", SIDES)
def test_the_drawing_inside_the_button_is_as_big_as_it_should_be_even_when_it_is_off(qtbot, side, enabled):
    """El fallo de la v0.21.0 en Windows: el dibujo salia de unos 5 px. Ahora ocupa casi todo el boton."""
    box = glyph_box(grab(make_button(qtbot, side, enabled)))
    assert box is not None
    w, h = box[2] - box[0] + 1, box[3] - box[1] + 1
    assert max(w, h) >= 18 and min(w, h) >= 12, (side, enabled, w, h)
    assert w <= ai.GLYPH + 1 and h <= ai.GLYPH + 1


def test_the_button_follows_its_action(qtbot):
    b = make_button(qtbot, enabled=False)
    assert not b.isEnabled()
    b._action.setEnabled(True)
    assert b.isEnabled()
    assert b.toolTip() == ai.TIPS["izquierda"]
    seen = []
    b._action.triggered.connect(lambda: seen.append(1))
    b.click()
    assert seen == [1]
    b._action.setEnabled(False)
    b.click()
    assert seen == [1]


def test_the_button_looks_different_normal_hovered_pressed_off_and_focused(qtbot):
    b = make_button(qtbot)
    normal = grab(b)
    QTest.mouseMove(b, QPoint(15, 15))
    qtbot.wait(20)
    hovered = grab(b)
    assert b.underMouse() and hovered != normal
    QTest.mousePress(b, Qt.LeftButton, pos=QPoint(15, 15))
    pressed = grab(b)
    QTest.mouseRelease(b, Qt.LeftButton, pos=QPoint(15, 15))
    assert pressed != hovered
    b._action.setEnabled(False)
    assert grab(b) != normal
    b._action.setEnabled(True)
    b.setFocus()
    assert b.hasFocus() and grab(b) != normal


def test_leaving_the_button_takes_the_highlight_away(qtbot):
    holder = QWidget()
    holder.resize(200, 100)
    qtbot.addWidget(holder)
    action = QAction("Alinear")
    action.setData("derecha")
    action.setIcon(ai.align_icon("derecha"))
    button = ai.AlignButton(action, holder)
    button.move(60, 30)
    holder.show()
    qtbot.waitExposed(holder)
    qtbot.wait(50)                                                  # (que la ventana ya haya dado el foco)
    normal = grab(button)
    QTest.mouseMove(holder, QPoint(75, 45))
    qtbot.waitUntil(lambda: button.underMouse(), timeout=2000)
    assert grab(button) != normal
    button.setAttribute(Qt.WA_UnderMouse, False)                    # lo que hace Qt cuando el raton sale...
    QApplication.sendEvent(button, QEvent(QEvent.Leave))            # ...y el aviso que manda
    assert not button.underMouse() and grab(button) == normal


# --- mas exigencias (salidas de la mutacion) -------------------------------------------------------------------------------
def dark_mean(img, x_range, y_range, colour=TEXT, tol=90):
    """Posicion media (x, y), como fraccion del lado, de los puntos casi del color `colour` dentro de esa zona (fracciones)."""
    want = QColor(colour)
    w, h = img.width(), img.height()
    pts = []
    for y in range(int(y_range[0] * h), int(y_range[1] * h)):
        for x in range(int(x_range[0] * w), int(x_range[1] * w)):
            c = img.pixelColor(x, y)
            if c.alpha() > 200 and abs(c.red() - want.red()) + abs(c.green() - want.green()) + abs(c.blue() - want.blue()) < tol:
                pts.append((x + 0.5, y + 0.5))
    assert pts, "no hay raya en esa zona"
    return sum(p[0] for p in pts) / len(pts) / w, sum(p[1] for p in pts) / len(pts) / h


@pytest.mark.parametrize("side, axis, where", [
    ("izquierda", 0, 2.75 / 20), ("derecha", 0, 17.25 / 20), ("centro_x", 0, 0.5),
    ("arriba", 1, 2.75 / 20), ("abajo", 1, 17.25 / 20), ("centro_y", 1, 0.5)])
def test_the_line_of_each_icon_is_exactly_where_the_faces_meet(qtbot, side, axis, where):
    """La raya contra la que se alinean las barras: pegada a un lado o justo en el centro (donde van los centros)."""
    img = ai.align_icon(side).pixmap(QSize(160, 160)).toImage()
    # la zona donde solo esta la raya: el extremo del cuadro, mas alla de las barras
    zone = ((0.0, 1.0), (0.05, 0.12)) if axis == 0 else ((0.05, 0.12), (0.0, 1.0))
    x, y = dark_mean(img, zone[0], zone[1])
    assert abs((x, y)[axis] - where) < 0.012, (side, x, y)


def test_the_edges_of_the_icon_are_smooth_at_a_size_that_is_not_a_multiple_of_the_grid(qtbot):
    img = ai.align_icon("centro_x").pixmap(QSize(22, 22)).toImage()
    partial = [1 for y in range(22) for x in range(22) if 0 < img.pixelColor(x, y).alpha() < 255]
    assert len(partial) > 10


@pytest.mark.parametrize("side", SIDES)
def test_the_button_says_its_side(qtbot, side):
    assert make_button(qtbot, side).side == side
    assert grab(make_button(qtbot, side)) != grab(make_button(qtbot, "izquierda" if side != "izquierda" else "derecha"))


@pytest.mark.parametrize("side", SIDES)
def test_a_button_that_is_off_shows_no_blue_at_all_and_one_that_is_on_does(qtbot, side):
    on, off = grab(make_button(qtbot, side, True)), grab(make_button(qtbot, side, False))
    assert has_colour(on, ACCENT) and not has_colour(off, ACCENT)
    assert on != off


def test_the_focus_ring_shows_when_the_button_has_the_focus_and_only_then(qtbot):
    holder = QWidget()
    holder.resize(120, 60)
    qtbot.addWidget(holder)
    action = QAction("Alinear")
    action.setData("abajo")
    button = ai.AlignButton(action, holder)
    button.move(30, 15)
    holder.show()
    qtbot.waitExposed(holder)
    holder.activateWindow()
    button.setFocus()
    qtbot.waitUntil(button.hasFocus, timeout=2000)
    button.setAttribute(Qt.WA_UnderMouse, False)
    focused = grab(button)
    button.clearFocus()
    assert not button.hasFocus()
    plain = grab(button)
    differing = [1 for y in range(30) for x in range(30) if focused.pixel(x, y) != plain.pixel(x, y)]
    assert len(differing) >= 20                                                  # el anillo de puntos del foco


def test_entering_and_leaving_ask_for_a_repaint(qtbot):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QEnterEvent
    button = make_button(qtbot)
    calls = []
    button.update = lambda *a: calls.append("update")
    button.enterEvent(QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
    assert calls
    calls.clear()
    button.leaveEvent(QEvent(QEvent.Leave))
    assert calls
