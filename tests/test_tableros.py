"""Convencion de tableros (rectangulo = un trabajo) y orden de corte, contra G-code de referencia.

Datos reales: un SVG real con cinco tableros de PVC y el G-code de referencia de tres de
ellos (fresa de 3,175 mm, 5 mm de grosor + 0,2 de margen, todo pasante, sin girar).
"""
import math
from pathlib import Path

import pytest
from shapely.geometry import Polygon, box

import svg_a_gcode
from cnc.boards import detect_boards, is_rectangle
from svg_a_gcode import parse_board_selection
from cnc.model import Tool
from cnc.ordering import order_cuts
from cnc.pipeline import JobSettings, load_drawing, plan_job
from cnc.svg_import import ImportedShape, import_svg
from cnc.toolpath import bottom_left_start
from comparar_nc import compare
from verificar_nc import analyze

DATA = Path(__file__).parent / "data"
SVG = DATA / "pvc_negro_3_y_5mm.svg"
REF = {1: DATA / "ref_pvc_350x1000.nc", 2: DATA / "ref_pvc_705x630.nc", 5: DATA / "ref_pvc_570x630.nc"}
ARGS = ["--no-config", "--tool", "3.175", "--thickness", "5", "--margin", "0.2", "--feed", "900", "--plunge", "300"]


def shp(geom, kind="path", closed=True):
    return ImportedShape(shape=geom, closed=closed, kind=kind)


def make(n):
    """Imita la referencia: nuestra salida del tablero n sin girar, para compararla con la suya.

    Se pide al planificador y no a la linea de comandos: la maquina de la referencia es mas grande y el tablero 2 (705 mm) no cabe
    sin girar en los 651 mm de la nuestra, asi que el programa no escribiria ese .nc; para comparar la trayectoria da igual.
    """
    def _make(tmp_path_factory):
        drawing = load_drawing(SVG)
        board = next(b for b in drawing.layout.boards if b.number == n)
        settings = JobSettings(tool=Tool(diameter=3.175), thickness=5, margin=0.2, feed=900, plunge=300, rotate=0.0)
        plan = plan_job(drawing.board_shapes(board), drawing.board_stock(board), settings, None, n)
        assert plan.ok, plan.error
        return plan.text
    return _make


@pytest.fixture(scope="module")
def ours(tmp_path_factory):
    return {n: make(n)(tmp_path_factory) for n in REF}


@pytest.fixture(scope="module")
def layout():
    shapes = import_svg(SVG).shapes
    return shapes, detect_boards(shapes)


def area(c):
    return 0.5 * sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(c, c[1:] + c[:1]))


# --- deteccion -------------------------------------------------------------------------------------
def test_real_svg_has_five_boards_in_reading_order(layout):
    shapes, lay = layout
    got = [(b.number, round(b.width), round(b.height)) for b in lay.boards]
    assert got == [(1, 350, 1000), (2, 705, 630), (3, 705, 230), (4, 705, 230), (5, 570, 630)]
    assert len(lay.groupings) == 2 and all(shapes[i].kind in ("ellipse", "circle") for i in lay.groupings)
    assert lay.loose == []                                   # nada queda fuera de un tablero


def test_boards_contain_the_expected_pieces_and_holes(layout):
    from cnc.svg_import import suggest_sides
    shapes, lay = layout
    counts = []
    for b in lay.boards:
        sides = suggest_sides([shapes[j] for j in b.contents])
        counts.append((sides.count("outside"), sides.count("inside")))
    assert counts == [(7, 0), (1, 20), (1, 2), (1, 2), (5, 3)]


def test_rectangle_pieces_inside_a_board_are_not_boards():
    board = shp(box(0, 0, 100, 100))
    piece = shp(box(10, 10, 40, 40))                         # rectangulo con algo dentro, pero dentro del tablero
    hole = shp(box(20, 20, 30, 30))
    lay = detect_boards([board, piece, hole])
    assert [b.index for b in lay.boards] == [0]
    assert sorted(lay.boards[0].contents) == [1, 2]


def test_empty_rectangle_and_loose_shapes_are_not_boards():
    lay = detect_boards([shp(box(0, 0, 10, 10)), shp(Polygon([(50, 0), (60, 0), (55, 8)]))])
    assert lay.boards == [] and lay.loose == [0, 1]


def test_ellipse_around_boards_is_a_grouping_and_is_discarded():
    ring = shp(Polygon([(math.cos(t / 20 * 2 * math.pi) * 200 + 50, math.sin(t / 20 * 2 * math.pi) * 200 + 50)
                        for t in range(20)]), kind="ellipse")
    lay = detect_boards([shp(box(0, 0, 100, 100)), shp(box(10, 10, 20, 20)), ring])
    assert lay.groupings == [2] and len(lay.boards) == 1 and lay.loose == []


def test_a_piece_that_sticks_out_a_little_still_belongs_to_the_board_but_one_mostly_outside_does_not():
    board = shp(box(0, 0, 100, 100))
    hole = shp(box(10, 10, 20, 20))
    mostly_in = shp(Polygon([(60, 40), (106, 40), (83, 60)]))           # sobresale por la derecha, ~87% dentro
    mostly_out = shp(Polygon([(95, 40), (140, 40), (118, 60)]))        # ~11% dentro
    lay = detect_boards([board, hole, mostly_in, mostly_out])
    assert lay.boards[0].contents == [1, 2] and lay.loose == [3]


def test_rectangle_with_a_hole_is_indistinguishable_from_a_board():
    """Limite documentado de la convencion: se resuelve con --no-boards."""
    assert is_rectangle(shp(box(0, 0, 50, 50)))
    lay = detect_boards([shp(box(0, 0, 50, 50)), shp(box(10, 10, 20, 20))])
    assert len(lay.boards) == 1


def test_board_selection_errors_are_clear(layout):
    _, lay = layout
    assert [b.number for b in parse_board_selection("all", lay)] == [1, 2, 3, 4, 5]
    assert [b.number for b in parse_board_selection("5, 2,5", lay)] == [5, 2]
    with pytest.raises(ValueError, match="No existe el tablero 9"):
        parse_board_selection("9", lay)
    with pytest.raises(ValueError, match="lista de numeros"):
        parse_board_selection("uno", lay)


# --- salida frente a la referencia ---------------------------------------------------------------
@pytest.mark.parametrize("n,contours", [(1, 7), (2, 21), (5, 8)])
def test_board_toolpaths_agree_with_reference(ours, n, contours):
    c = compare(REF[n].read_text(), ours[n], 3.175)
    assert c["rotation"] == 0 and c["shift"] == pytest.approx((0.0, 0.0), abs=0.05)      # mismo cero: la esquina del tablero
    assert c["contours"] == (contours, contours)
    assert c["a"].cut_length == pytest.approx(c["b"].cut_length, rel=2e-3)
    assert c["max_dev"] < 0.05
    assert (c["same_direction"], c["opposite_direction"]) == (contours, 0)
    a, b = c["a"], c["b"]
    assert a.cut_z_levels == b.cut_z_levels == {-5.2} and a.plunge_targets == b.plunge_targets == {-5.2}
    assert a.cut_feeds == b.cut_feeds == {900.0} and a.spindle_s == b.spindle_s == {20000.0}


@pytest.mark.parametrize("n", [1, 2, 5])
def test_contour_order_and_entry_points_match_reference_one_by_one(ours, n):
    ref, mine = analyze(REF[n].read_text()).contours, analyze(ours[n]).contours
    assert len(ref) == len(mine)
    for i, (a, b) in enumerate(zip(ref, mine)):
        assert math.dist(a[0], b[0]) < 1.0, f"el contorno {i + 1} entra en otro sitio que en la referencia"


def test_holes_are_cut_first_and_letters_keep_their_inner_strokes(ours):
    """Las R y las A del tablero de letras tienen trazo interior: 3 huecos (sentido horario) antes de las piezas."""
    mine = analyze(ours[5]).contours
    assert [area(c) < 0 for c in mine] == [True] * 3 + [False] * 5
    assert [area(c) < 0 for c in analyze(REF[5].read_text()).contours] == [True] * 3 + [False] * 5


def test_board_one_has_no_holes_and_reference_agrees(ours):
    assert all(area(c) > 0 for c in analyze(ours[1]).contours)
    assert all(area(c) > 0 for c in analyze(REF[1].read_text()).contours)


# --- giro automatico y archivos de salida ----------------------------------------------------------
def test_auto_rotation_only_turns_the_boards_that_do_not_fit(tmp_path, capsys):
    out = tmp_path / "pvc.nc"
    assert svg_a_gcode.main([str(SVG), *ARGS, "-o", str(out)]) == 0
    text = capsys.readouterr().out
    assert [p.name for p in sorted(tmp_path.glob("*.nc"))] == [f"pvc_tablero{n}.nc" for n in range(1, 6)]
    assert text.count("se gira 90 grados") == 3                     # los tres de 705 mm de ancho
    assert text.count("cabe en el recorrido: no se gira") == 2
    assert "NO CABE" not in text
    for p in tmp_path.glob("*.nc"):
        r = analyze(p.read_text(), travel=(651, 1011, 100))
        assert r.ok, (p.name, r.issues)
        assert r.bounds[0] > 0 and r.bounds[1] > 0                  # el cero es la esquina del tablero


def test_rotated_board_is_the_manual_ninety_degrees_of_reference(tmp_path):
    """Girar el tablero de 705 x 630 da una trayectoria de 630 x 705 en el mismo cero que sin girar."""
    out = tmp_path / "r.nc"
    assert svg_a_gcode.main([str(SVG), *ARGS, "--board", "2", "-o", str(out)]) == 0
    c = compare(REF[2].read_text(), out.read_text(), 3.175)
    assert c["rotation"] in (90, 270) and c["max_dev"] < 0.05      # el mismo dibujo, girado un cuarto de vuelta
    b = analyze(out.read_text()).bounds
    assert (b[2] - b[0], b[3] - b[1]) == pytest.approx((593.5, 666.9), abs=0.1)


def test_single_board_uses_the_exact_output_name_and_no_boards_restores_one_job(tmp_path, capsys):
    one = tmp_path / "solo.nc"
    assert svg_a_gcode.main([str(SVG), *ARGS, "--board", "3", "-o", str(one)]) == 0
    assert one.exists() and len(list(tmp_path.glob("*.nc"))) == 1
    capsys.readouterr()
    all_in_one = tmp_path / "todo.nc"
    # sin tableros todo el dibujo es un solo trabajo, y entero no cabe en la maquina: no se escribe
    assert svg_a_gcode.main([str(SVG), *ARGS, "--no-boards", "--rotate", "0", "-o", str(all_in_one)]) == 1
    out, err = capsys.readouterr()
    assert "Tableros" not in out and "No cabe en la maquina" in err
    assert not all_in_one.exists()


def test_list_boards_prints_the_table_and_needs_no_tool(capsys):
    assert svg_a_gcode.main([str(SVG), "--list-boards", "--no-config"]) == 0
    out = capsys.readouterr().out
    assert "350 x 1000" in out and "705 x 630" in out and "570 x 630" in out
    assert "2 elipse(s) de agrupacion descartada(s)" in out


def test_unknown_board_is_a_one_line_error(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        svg_a_gcode.main([str(SVG), *ARGS, "--board", "9", "-o", str(tmp_path / "x.nc")])
    err = capsys.readouterr().err
    assert e.value.code == 2 and err.startswith("ERROR:") and "No existe el tablero 9" in err
    assert len(err.strip().splitlines()) == 2                       # el error y una pista, no todo el uso


# --- piezas sueltas del orden -----------------------------------------------------------------------
def test_bottom_left_start_rotates_the_ring_without_changing_direction():
    ring = [(5, 5), (10, 5), (10, 10), (5, 10), (5, 5)]            # antihorario, empieza arriba a la derecha de (0,0)
    ring = [(10, 10), (5, 10), (5, 5), (10, 5), (10, 10)]
    out = bottom_left_start(ring)
    assert out[0] == out[-1] == (5, 5) and len(out) == len(ring)
    assert Polygon(out).exterior.is_ccw == Polygon(ring).exterior.is_ccw


def test_order_pieces_by_top_edge_then_left_and_holes_by_nearest():
    sq = lambda x, y, s=10: box(x, y, x + s, y + s)
    pieces = [sq(100, 0), sq(0, 50), sq(60, 50), sq(30, 80)]       # bordes superiores 10, 60, 60, 90
    assert order_cuts(pieces, ["outside"] * 4, 1.0) == [3, 1, 2, 0]
    # huecos: se empieza por el de arriba a la izquierda y se sigue al mas cercano, no fila a fila
    holes = [sq(0, 100), sq(200, 100), sq(0, 0), sq(200, 0)]
    assert order_cuts(holes, ["inside"] * 4, 1.0) == [0, 2, 3, 1]
    assert order_cuts(holes, ["inside"] * 4, 1.0, "file") == [0, 1, 2, 3]
    mixed = order_cuts([pieces[0], holes[2], pieces[1]], ["outside", "inside", "outside"], 1.0)
    assert mixed == [1, 2, 0]                                       # primero los huecos


def test_pieces_with_almost_the_same_top_edge_count_as_one_row():
    # El exportador redondea: dos letras de la misma fila pueden diferir unas decimas en su borde superior
    pieces = [box(50, 0, 60, 10.3), box(0, 0, 10, 10.0)]
    assert order_cuts(pieces, ["outside"] * 2, 1.0) == [1, 0]
    assert order_cuts([box(50, 0, 60, 12.0), box(0, 0, 10, 10.0)], ["outside"] * 2, 1.0) == [0, 1]


def test_order_cuts_rejects_mismatched_lists():
    with pytest.raises(ValueError):
        order_cuts([box(0, 0, 1, 1)], [], 1.0)
