"""Sin tablero (sin recuadro) se supone uno: del cero a la cota maxima de lo que se corta mas el mismo margen.

Con el, la pestaña Sujecion tiene zona libre (verde) y puntos de anclaje tambien en un dibujo sin recuadro. El tablero
supuesto solo sirve para eso: no cambia el G-code ni da avisos de que la trayectoria se sale.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
from dataclasses import replace
from pathlib import Path

import pytest
from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union

from cnc import holddown
from cnc.holddown import MANY_PARTS, HoldSettings, analyse_plan, build_zones
from cnc.model import Tool
from cnc.pipeline import JobSettings, plan_job, virtual_stock
from cnc.preview import preview_ops
from cnc.project import HOLD_WHOLE, General, Project
from cnc.svg_import import ImportedShape

DATA = Path(__file__).parent / "data"
BOARDS19 = DATA / "tableros_19mm.svg"
BIG = DATA / "dibujo_grande.svg"
TOOL = Tool(diameter=4)
# dos piezas lejos una de otra (la de la izquierda con un agujero): entre ellas queda mucho desecho donde sujetar
SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="600mm" height="450mm" viewBox="0 0 600 450">'
       '<rect x="10" y="10" width="140" height="100" stroke="#ff0000" fill="none"/>'
       '<circle cx="80" cy="60" r="10" stroke="#ff0000" fill="none"/>'
       '<rect x="400" y="300" width="150" height="110" stroke="#ff0000" fill="none"/></svg>')


def shp(geom, stroke="#ff0000"):
    return ImportedShape(shape=geom, closed=True, stroke=stroke, fill=None)


def settings(**kw):
    base = dict(tool=TOOL, thickness=6.0, margin=0.4, feed=300.0, plunge=200.0)
    base.update(kw)
    return JobSettings(**base)


def pieces():
    return [shp(box(10, 10, 110, 70)), shp(box(30, 30, 50, 50))]               # 100 x 60 mm


def texts(plan):
    return [m.text for m in plan.messages]


def bounds(plan):
    return plan.stock[0].shape.bounds


# --- el plan ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("margin", [0.0, 4.0, 10.0])
def test_the_assumed_board_runs_from_the_zero_to_the_far_corner_plus_the_same_margin(margin):
    plan = plan_job(pieces(), [], settings(origin_margin=margin))             # el dibujo mide 100 x 60 mm
    assert plan.ok and plan.virtual_stock and len(plan.stock) == 1
    cut = max(s.shape.bounds[2] for s in plan.shapes), max(s.shape.bounds[3] for s in plan.shapes)
    assert cut == pytest.approx((100 + margin, 60 + margin))                    # el margen del cero ya esta puesto abajo/izquierda
    assert bounds(plan) == pytest.approx((0, 0, cut[0] + margin, cut[1] + margin))
    assert bounds(plan) == pytest.approx((0, 0, 100 + 2 * margin, 60 + 2 * margin))
    assert plan.stock[0].closed and plan.stock[0].shape.area == pytest.approx((100 + 2 * margin) * (60 + 2 * margin))


def test_the_board_is_said_in_the_messages():
    plan = plan_job(pieces(), [], settings(origin_margin=4.0))
    assert "Tablero supuesto: 108.0 x 68.0 mm (del cero a lo que se corta mas 4 mm); sirve para calcular la sujecion" \
        in texts(plan)
    assert not any(t.startswith("Tablero:") for t in texts(plan))


def test_the_board_follows_a_manual_offset():
    plan = plan_job(pieces(), [], settings(origin_margin=4.0, offset=(20.0, 5.0)))
    assert bounds(plan) == pytest.approx((0, 0, 128, 73))                       # (100 + 4 + 20) + 4 ; (60 + 4 + 5) + 4


def test_the_board_is_computed_after_turning():
    plan = plan_job(pieces(), [], settings(origin_margin=4.0, rotate=90.0))
    assert bounds(plan) == pytest.approx((0, 0, 68, 108))                       # el dibujo de 100 x 60 queda de 60 x 100


@pytest.mark.parametrize("origin", ["center", "page_bottom_left"])
def test_other_origins_have_no_assumed_board(origin):
    plan = plan_job(pieces(), [], settings(origin=origin, origin_margin=4.0))
    assert plan.ok and plan.stock == [] and not plan.virtual_stock
    assert not any(t.startswith("Tablero supuesto") for t in texts(plan))


def test_a_real_board_is_used_as_it_is():
    board = shp(box(0, 0, 300, 200), "#000000")
    plan = plan_job([shp(box(20, 30, 120, 90))], [board], settings(origin_margin=4.0))
    assert plan.ok and not plan.virtual_stock and bounds(plan) == pytest.approx((0, 0, 300, 200))
    assert any(t.startswith("Tablero: 300.0 x 200.0 mm") for t in texts(plan))
    assert not any(t.startswith("Tablero supuesto") for t in texts(plan))


def test_the_assumed_board_never_warns_that_the_path_sticks_out():
    plan = plan_job(pieces(), [], settings(origin_margin=4.0, offset=(-30.0, -30.0)))
    assert plan.ok and plan.overhang == {} and not any(m.code == "overhang" for m in plan.messages)


def test_a_real_board_still_warns_when_the_path_sticks_out():
    board = shp(box(0, 0, 300, 200), "#000000")
    plan = plan_job([shp(box(0, 0, 100, 60))], [board], settings())
    assert plan.overhang and any(m.code == "overhang" for m in plan.messages)


def test_a_drawing_that_is_pushed_off_the_zero_has_no_board_to_assume():
    plan = plan_job(pieces(), [], settings(origin_margin=4.0, offset=(-120.0, -80.0)))
    assert plan.ok and plan.stock == [] and not plan.virtual_stock


def test_the_helper_needs_a_rectangle_with_area():
    assert virtual_stock([], 4.0) is None
    assert virtual_stock([shp(box(-10, -10, -4, 5))], 4.0) is None              # llega justo a 0 en X
    assert virtual_stock([shp(box(-10, -10, 5, -4))], 4.0) is None              # y en Y
    v = virtual_stock([shp(box(-10, -10, -3.9, 5))], 4.0)
    assert v is not None and v.shape.bounds == pytest.approx((0, 0, 0.1, 9))
    assert virtual_stock([shp(box(0, 0, 10, 20)), shp(box(5, 5, 30, 8))], 2.0).shape.bounds == pytest.approx((0, 0, 32, 22))


# --- la sujecion --------------------------------------------------------------------------------------------------
def project_for(tmp_path, **general):
    svg = tmp_path / "d.svg"
    svg.write_text(SVG)
    p = Project({"em4": Tool(diameter=4, name="End mill 4 mm", feed_xy=300, feed_z=200)})
    p.use_boards = False
    p.load_svg(svg)
    p.general = General(tool="em4", thickness=6.0, **general)
    return p


def test_the_assumed_board_has_free_zones_and_a_proposal_of_points(tmp_path):
    p = project_for(tmp_path)
    plan = p.plan(None)
    assert plan.ok and plan.virtual_stock
    analysis = analyse_plan(plan, preview_ops(plan), HoldSettings())
    assert analysis is not None
    assert analysis.zones.board.bounds == pytest.approx(bounds(plan))
    assert not analysis.free.is_empty and analysis.free.area > 10_000
    points = analysis.suggest()
    assert points and all(analysis.check(x, y).ok for x, y in points)
    assert all(0 < x < bounds(plan)[2] and 0 < y < bounds(plan)[3] for x, y in points)


def test_without_a_board_and_without_the_assumed_one_there_is_nothing_to_hold(tmp_path):
    p = project_for(tmp_path, origin="center")
    plan = p.plan(None)
    assert plan.ok and plan.stock == []
    assert analyse_plan(plan, preview_ops(plan), HoldSettings()) is None


def test_a_margin_too_thin_to_hold_anything_gives_no_free_zone_but_still_a_board():
    """Un dibujo que llena la plancha (una sola pieza): el margen es todo el desecho y no basta para clavar."""
    plan = plan_job([shp(box(0, 0, 200, 100))], [], settings(origin_margin=4.0))
    analysis = analyse_plan(plan, preview_ops(plan), HoldSettings())
    assert analysis is not None and analysis.free.is_empty and analysis.suggest() == []
    assert not analysis.check(100, 50).ok                                       # dentro de la pieza: no se clava


# --- trazados enormes (un dibujo grande: 36.000 segmentos sueltos) --------------------------------------------------------
def circle_lines(n_circles=40, vertices=120):
    """Circulos de 100 mm de radio con los centros a 12 mm: se cruzan todos con todos y la union los parte en miles de
    tramos sueltos, como las curvas de un dibujo de verdad."""
    return [LineString(Point(100 + 12 * i, 350).buffer(100, quad_segs=vertices // 4).exterior.coords)
            for i in range(n_circles)]


def test_buffering_a_huge_path_gives_the_same_shape_as_buffering_it_whole(monkeypatch):
    lines = circle_lines()
    drills = [Point(20, 20), Point(680, 680)]                                  # fuera de los circulos
    axis = unary_union(lines + drills)                                          # los segmentos quedan sueltos
    assert len(axis.geoms) > 1000                                                # miles de tramos sueltos
    exact = axis.buffer(2.0)
    merges = []
    real_merge = holddown.shapely.line_merge
    monkeypatch.setattr(holddown.shapely, "line_merge", lambda g: merges.append(len(g.geoms)) or real_merge(g))
    monkeypatch.setattr(holddown, "MANY_PARTS", 100_000)
    holddown._buffer(axis, 2.0)
    assert merges == []                                                          # con pocos trozos no se junta nada
    monkeypatch.setattr(holddown, "MANY_PARTS", 100)
    merged = holddown._buffer(axis, 2.0)
    assert len(merges) == 1 and merges[0] > 1000                                 # primero se juntan los tramos sueltos
    assert merged.area == pytest.approx(exact.area, rel=1e-4)
    assert merged.symmetric_difference(exact).area < 0.001 * exact.area
    assert merged.contains(Point(20, 20)) and merged.contains(Point(680, 680))   # los taladros no se pierden


def op(kind, cuts):
    class Op:
        pass
    o = Op()
    o.kind, o.cuts, o.drills, o.rapids = kind, cuts, [], []
    return o


def test_a_path_with_many_segments_is_buffered_in_long_pieces_and_a_small_one_whole(monkeypatch):
    previews = [op("pieza", [list(l.coords) for l in circle_lines()])]
    calls = []
    real = holddown._buffer
    monkeypatch.setattr(holddown, "_buffer", lambda g, d: calls.append(len(g.geoms)) or real(g, d))
    board = box(0, 0, 700, 700)
    monkeypatch.setattr(holddown, "MANY_PARTS", 100_000)
    small = build_zones(board, previews, 2.0)
    assert calls == []                                                           # lo corriente: un solo ensanchado
    monkeypatch.setattr(holddown, "MANY_PARTS", 100)
    big = build_zones(board, previews, 2.0)
    assert len(calls) == 1 and calls[0] > 100
    assert len(small.pieces) == len(big.pieces) > 0 and len(small.frame) == len(big.frame) > 0
    assert len(small.islands) == len(big.islands)
    assert big.frame_union.area == pytest.approx(small.frame_union.area, rel=1e-4)


# --- el trabajo guardado ----------------------------------------------------------------------------------------------
def test_the_points_of_a_drawing_without_boards_are_saved_and_come_back(tmp_path):
    p = project_for(tmp_path)
    p.hold_points[None] = [(100.0, 200.0), (300.5, 250.0)]
    data = p.to_dict()
    assert data["sujecion"]["puntos"] == {HOLD_WHOLE: [[100.0, 200.0], [300.5, 250.0]]}
    other = Project(p.tools)
    other.apply_dict(json.loads(json.dumps(data)))
    assert other.hold_points == {None: [(100.0, 200.0), (300.5, 250.0)]}


def test_board_numbers_still_work_next_to_the_whole_drawing(tmp_path):
    p = project_for(tmp_path)
    p.hold_points.update({None: [(1.0, 2.0)], 2: [(3.0, 4.0)]})
    data = json.loads(json.dumps(p.to_dict()))
    assert data["sujecion"]["puntos"] == {HOLD_WHOLE: [[1.0, 2.0]], "2": [[3.0, 4.0]]}
    other = Project(p.tools)
    other.apply_dict(data)
    assert other.hold_points == {None: [(1.0, 2.0)], 2: [(3.0, 4.0)]}


def test_a_badly_written_key_or_point_is_skipped_and_the_rest_comes_back(tmp_path):
    p = project_for(tmp_path)
    data = json.loads(json.dumps(p.to_dict()))
    data["sujecion"]["puntos"] = {"dibujo": [[5, 6]], "None": [[7, 8]], "xx": [[9, 9]], "3": [["a", 1]]}
    other = Project(p.tools)
    other.apply_dict(data)
    assert other.hold_points == {None: [(5.0, 6.0)]}


def test_the_marks_file_of_a_whole_drawing_is_named_after_the_drawing(tmp_path):
    p = project_for(tmp_path)
    assert p.marks_path(tmp_path / "pieza.nc", None).name == "pieza_marcas.nc"
    lines = p.marks_text(None, [(100.0, 200.0), (300.0, 250.0)]).splitlines()
    assert lines.count("M0") == 2 and "G0 X100 Y200" in lines and "G0 X300 Y250" in lines


# --- la ventana ------------------------------------------------------------------------------------------------------
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QGraphicsRectItem, QMessageBox  # noqa: E402

from cnc.gui.hold_panel import COL_STATE  # noqa: E402
from cnc.gui.main_window import MainWindow  # noqa: E402


@pytest.fixture(autouse=True)
def no_boxes(monkeypatch):
    for name in ("question", "warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.No if name == "question" else None))


def open_window(qtbot, svg, tmp_path=None, thickness="6"):
    w = MainWindow(Project({"em4": Tool(diameter=4, name="End mill 4 mm", feed_xy=300, feed_z=200, stepdown=6.5)}), [],
                   use_settings=False)
    qtbot.addWidget(w)
    w.resize(1300, 800)
    w.show()
    w.detect_boards.setChecked(False)
    assert w.open_svg(svg, apply_default_recipe=False)
    w.job.tool.setCurrentIndex(w.job.tool.findData("em4"))
    w.job.thickness.setText(thickness)
    w.job.thickness.valueChanged.emit()
    w.wait_for_plan()
    w.tabs.setCurrentWidget(w.hold_panel)
    return w


@pytest.fixture
def nb(qtbot, tmp_path):
    svg = tmp_path / "d.svg"
    svg.write_text(SVG)
    return open_window(qtbot, svg)


def points(w):
    return list(w._hold_view[1])


def free_spot(w, far_from=()):
    analysis = w._hold_view[0]
    geoms = list(analysis.free.geoms) if hasattr(analysis.free, "geoms") else [analysis.free]
    for g in sorted(geoms, key=lambda g: -g.area):
        p = g.representative_point()
        if all(abs(p.x - x) + abs(p.y - y) > 5 for x, y in far_from):
            return round(p.x, 1), round(p.y, 1)
    raise AssertionError("no hay sitio libre")


def board_items(w):
    return [i for i in w.canvas._scene.items() if isinstance(i, QGraphicsRectItem) and i.zValue() == -10]


def test_a_drawing_without_boards_shows_free_zones_and_proposed_points(nb):
    w = nb
    assert w.last_plan.ok and w.last_plan.virtual_stock
    assert w.hold_panel.count() >= 3 and w.canvas.hold_item_count() == w.hold_panel.count()
    assert not w._hold_view[0].free.is_empty and any(i.toolTip() == "Zona donde se puede sujetar" for i in w.canvas._hold_items)
    assert all(w.hold_panel.table.item(i, COL_STATE).text().startswith("✔") for i in range(w.hold_panel.count()))
    assert "propuestos" in w.hold_panel.info.text()


def test_the_margin_field_explains_that_it_also_sizes_the_assumed_board(nb):
    assert "pestaña Sujeción" in nb.job.origin_margin.toolTip() and "arriba y a la derecha" in nb.job.origin_margin.toolTip()


def test_the_panel_says_the_board_is_assumed_and_how_big(nb):
    text = nb.hold_panel.info.text()
    b = nb.last_plan.stock[0].shape.bounds
    assert "Sin recuadro" in text and f"{b[2]:.0f} x {b[3]:.0f} mm" in text and "más 4 mm" in text
    assert "Cero a (mm) del dibujo" in text


def test_the_assumed_board_grows_with_the_margin_and_the_note_follows(nb):
    def far_corner(w):
        return (max(sh.shape.bounds[2] for sh in w.last_plan.shapes), max(sh.shape.bounds[3] for sh in w.last_plan.shapes))
    nb.job.origin_margin.setText("10")
    nb.job.origin_margin.valueChanged.emit()
    nb.wait_for_plan()
    b = nb.last_plan.stock[0].shape.bounds
    assert b[:2] == pytest.approx((0, 0)) and b[2:] == pytest.approx((far_corner(nb)[0] + 10, far_corner(nb)[1] + 10))
    assert (b[2], b[3]) == pytest.approx((560, 420), abs=0.01)                    # 540 x 400 mm de dibujo + 10 mm por cada lado
    assert "más 10 mm" in nb.hold_panel.info.text() and "560 x 420 mm" in nb.hold_panel.info.text()


def test_the_assumed_board_is_drawn_dashed_and_a_real_one_solid(nb, qtbot):
    rects = board_items(nb)
    assert len(rects) == 1 and rects[0].pen().style() == Qt.DashLine
    assert rects[0].toolTip().startswith("Tablero supuesto")
    b = nb.last_plan.stock[0].shape.bounds
    r = rects[0].rect()
    assert (r.x(), r.y(), r.right(), r.bottom()) == pytest.approx(b)
    real = MainWindow(Project({"em4": Tool(diameter=4, name="End mill 4 mm", feed_xy=300, feed_z=200, stepdown=6.5)}), [],
                      use_settings=False)
    qtbot.addWidget(real)
    real.show()
    assert real.open_svg(BOARDS19, apply_default_recipe=False)
    real.job.tool.setCurrentIndex(real.job.tool.findData("em4"))
    real.job.thickness.setText("19")
    real.job.thickness.valueChanged.emit()
    real.wait_for_plan()
    real.tabs.setCurrentWidget(real.hold_panel)
    solid = board_items(real)
    assert solid and all(i.pen().style() == Qt.SolidLine and not i.toolTip() for i in solid)


def test_a_point_can_be_added_on_a_free_spot_and_is_kept_for_the_whole_drawing(nb):
    w = nb
    before = points(w)
    spot = free_spot(w, far_from=before)
    assert w.hold_add(*spot) is True
    assert points(w) == before + [spot] and w.project.hold_points[None] == points(w)
    assert "editados a mano" in w.hold_panel.info.text() and "Sin recuadro" in w.hold_panel.info.text()
    assert w.hold_panel.count() == len(points(w))


def test_a_point_on_a_piece_is_refused_and_says_why(nb):
    w = nb
    before = points(w)
    inside = w._hold_view[0].zones.pieces[0].representative_point()
    assert w.hold_add(inside.x, inside.y) is False
    assert points(w) == before and None not in w.project.hold_points
    assert "no se puede sujetar ahí" in w.statusBar().currentMessage()


def test_points_can_be_moved_removed_and_reset(nb):
    w = nb
    first = points(w)
    spot = free_spot(w, far_from=first)
    w.hold_edit(0, *spot)
    assert points(w)[0] == spot and w.project.hold_points[None][0] == spot
    w.hold_remove(1)
    assert len(points(w)) == len(first) - 1
    w.hold_reset()
    assert None not in w.project.hold_points and points(w) == first


def test_edited_points_come_back_after_saving_and_reopening(nb, qtbot, tmp_path):
    w = nb
    spot = free_spot(w, far_from=points(w))
    assert w.hold_add(*spot)
    mine = points(w)
    job = tmp_path / "t.cncjob"
    assert w.save_job(job)
    other = MainWindow(Project({"em4": Tool(diameter=4, name="End mill 4 mm", feed_xy=300, feed_z=200, stepdown=6.5)}), [],
                       use_settings=False)
    qtbot.addWidget(other)
    other.show()
    assert other.open_job(job)
    other.wait_for_plan()
    other.tabs.setCurrentWidget(other.hold_panel)
    assert other.project.hold_points == {None: mine} and points(other) == mine
    assert "editados a mano" in other.hold_panel.info.text()


def test_the_marks_of_a_whole_drawing_can_be_written(nb, tmp_path):
    w = nb
    out = tmp_path / "marcas.nc"
    assert w.generate_marks(out, confirm=False) == out
    lines = out.read_text().splitlines()
    assert lines.count("M0") == len(points(w)) > 0
    assert w.project.marks_path(tmp_path / "d.nc", w.current).name == "d_marcas.nc"


def test_without_the_assumed_board_the_tab_says_why(nb):
    w = nb
    w.project.general = replace(w.project.general, origin="center")
    w.job.thickness.valueChanged.emit()
    w.wait_for_plan()
    assert not w.last_plan.stock
    text = w.hold_panel.info.text()
    assert "no tiene tablero" in text and "esquina inferior izquierda" in text and w.hold_panel.count() == 0
    assert w.canvas.hold_item_count() == 0 and not w.hold_panel.btn_marks.isEnabled()


# --- Un dibujo grande: el caso real (689 x 986 mm, 36.000 segmentos de trayectoria) --------------------------------------------
def test_the_big_drawing_gets_a_board_and_points_without_running_out_of_memory(qtbot):
    w = open_window(qtbot, BIG, thickness="19")
    assert w.last_plan.ok and w.last_plan.virtual_stock
    assert w.hold_panel.count() >= 4 and w.canvas.hold_item_count() == w.hold_panel.count()
    analysis, pts, checks = w._hold_view
    assert all(c.ok for c in checks) and not analysis.free.is_empty
    assert "Sin recuadro" in w.hold_panel.info.text()
