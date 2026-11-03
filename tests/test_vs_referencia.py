"""Nuestra salida frente a un G-code de referencia de la misma placa (6 mm)."""
from pathlib import Path

import pytest
from gcodecmp import assert_same_gcode

import svg_a_gcode
from comparar_nc import compare
from verificar_nc import analyze

DATA = Path(__file__).parent / "data"
SVG, REF = DATA / "placa_doble.svg", DATA / "ref_placa_doble.nc"
ARGS = [str(SVG), "--tool", "4", "--thickness", "6", "--margin", "0.4", "--feed", "300", "--plunge", "200",
        "--stock-color", "#2b2a29", "--rotate", "90"]


@pytest.fixture(scope="module")
def ours(tmp_path_factory):
    out = tmp_path_factory.mktemp("ref") / "v2.nc"
    assert svg_a_gcode.main([*ARGS, "-o", str(out)]) == 0
    return out.read_text()


def test_reference_file_is_understood():
    r = analyze(REF.read_text(), travel=(651, 1011, 100))
    assert r.ok, (r.issues, r.unknown)
    assert r.pauses == [5] and r.ended                     # M0 de cambio de herramienta y M02 final
    assert r.cut_feeds == {300.0} and r.cut_z_levels == {-6.4} and r.spindle_s == {20000.0}
    assert len(r.contours) == 14
    assert r.bounds == pytest.approx((13.27, 7.42, 286.55, 1006.12), abs=0.01)


def test_same_coordinate_frame_as_reference(ours):
    # con el cero en la esquina del tablero y el dibujo girado 90 grados, no hace falta alinear nada
    c = compare(REF.read_text(), ours, 4.0)
    assert c["rotation"] == 0 and c["shift"] == pytest.approx((0.0, 0.0), abs=0.05)
    b = analyze(ours).bounds
    assert b == pytest.approx((13.27, 7.42, 286.55, 1006.12), abs=0.03)


def test_toolpaths_agree_with_reference(ours):
    c = compare(REF.read_text(), ours, 4.0)
    assert c["contours"] == (14, 14)
    assert c["a"].cut_length == pytest.approx(c["b"].cut_length, rel=1e-3)      # 8574 mm los dos
    assert c["diff_area"] < 0.002 * c["footprint_a"]                              # < 0,2 % de huella
    assert c["max_dev"] < 0.05                                                    # < 50 micras
    assert (c["same_direction"], c["opposite_direction"]) == (14, 0)              # misma concordancia
    a, b = c["a"], c["b"]                                                         # y los mismos parametros de corte
    assert a.cut_z_levels == b.cut_z_levels == {-6.4}
    assert a.cut_feeds == b.cut_feeds == {300.0}
    assert a.plunge_targets == b.plunge_targets == {-6.4}


def test_holes_first_then_pieces_like_reference(ours):
    # Referencia: 4 contornos interiores en el primer trazado y 10 exteriores en el segundo; igual que nosotros
    ref, mine = analyze(REF.read_text()), analyze(ours)
    def n_ccw(contours):
        return [sum(0.5 * (x0 * y1 - x1 * y0) for (x0, y0), (x1, y1) in zip(c, c[1:] + c[:1])) > 0 for c in contours]
    assert n_ccw(ref.contours)[:4] == [False] * 4 and all(n_ccw(ref.contours)[4:])
    assert n_ccw(mine.contours)[:4] == [False] * 4 and all(n_ccw(mine.contours)[4:])


def test_compare_finds_rotation_between_two_orientations(tmp_path):
    a, b = tmp_path / "a.nc", tmp_path / "b.nc"
    base = [str(SVG), "--tool", "4", "--thickness", "6", "--margin", "0.4", "--feed", "300", "--plunge", "200",
            "--stock-color", "#2b2a29"]
    svg_a_gcode.main([*base, "--rotate", "90", "-o", str(a)])
    svg_a_gcode.main([*base, "--rotate", "-90", "-o", str(b)])
    c = compare(a.read_text(), b.read_text(), 4.0)
    assert c["rotation"] == 180 and c["diff_area"] < 0.002 * c["footprint_a"]


def test_compare_identical_files_has_no_difference():
    t = REF.read_text()
    c = compare(t, t, 4.0)
    assert c["diff_area"] < 1e-6 and c["max_dev"] < 1e-9 and c["rotation"] == 0


def test_we_waste_far_less_time_at_feed_in_the_air_than_reference(ours):
    # La referencia baja de Z5 a F200 y sube a F300 en cada uno de los 14 contornos; nosotros, rapido hasta Z1
    ref, mine = analyze(REF.read_text()), analyze(ours)
    assert ref.air_mm == pytest.approx(14 * 10) and ref.air_seconds == pytest.approx(35.0, abs=0.5)
    assert mine.air_mm == pytest.approx(14 * 1.0) and mine.air_seconds == pytest.approx(4.2, abs=0.1)


def test_clearance_option_controls_the_air_gap(tmp_path):
    out = tmp_path / "c.nc"
    svg_a_gcode.main([*ARGS, "--clearance-z", "0.5", "-o", str(out)])
    assert analyze(out.read_text()).air_mm == pytest.approx(14 * 0.5)
    with pytest.raises(SystemExit):
        svg_a_gcode.main([*ARGS, "--clearance-z", "8", "-o", str(out)])


def centroid(contour):
    from shapely.geometry import Polygon
    p = Polygon(contour).centroid
    return p.x, p.y


def test_contour_order_matches_reference_one_by_one(ours):
    # mismo marco de coordenadas: el contorno i de la referencia y el contorno i nuestro son la misma pieza
    ref, mine = analyze(REF.read_text()).contours, analyze(ours).contours
    assert len(ref) == len(mine) == 14
    for i, c in enumerate(ref):
        cx, cy = centroid(c)
        nearest = min(range(len(mine)), key=lambda j: (centroid(mine[j])[0] - cx) ** 2 + (centroid(mine[j])[1] - cy) ** 2)
        assert nearest == i, f"el contorno {i} de la referencia es el {nearest} en nuestra salida"


def test_every_contour_enters_where_reference_enters(ours):
    # La referencia arranca cada contorno en el vertice mas cercano a la esquina inferior izquierda de su caja
    import math
    ref, mine = analyze(REF.read_text()).contours, analyze(ours).contours
    assert max(math.dist(a[0], b[0]) for a, b in zip(ref, mine)) < 1.0


def test_file_order_option_keeps_drawing_order(tmp_path):
    out = tmp_path / "f.nc"
    svg_a_gcode.main([*ARGS, "--order", "file", "-o", str(out)])
    ys = [centroid(c)[1] for c in analyze(out.read_text()).contours]
    assert ys[:4] != sorted(ys[:4], reverse=True) or ys[4:] != sorted(ys[4:], reverse=True)   # ya no esta ordenado por Y


def test_portrait_equals_the_manual_ninety_degree_rotation(tmp_path):
    base = [str(SVG), "--tool", "4", "--thickness", "6", "--margin", "0.4", "--feed", "300", "--plunge", "200",
            "--stock-color", "#2b2a29"]
    a, b = tmp_path / "a.nc", tmp_path / "b.nc"
    svg_a_gcode.main([*base, "--portrait", "-o", str(a)])
    svg_a_gcode.main([*base, "--rotate", "90", "-o", str(b)])
    assert_same_gcode(a.read_text(), b.read_text())
    with pytest.raises(SystemExit):
        svg_a_gcode.main([*base, "--portrait", "--rotate", "90", "-o", str(a)])


def test_portrait_leaves_an_already_vertical_drawing_alone(tmp_path):
    tall = tmp_path / "alto.svg"
    tall.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="200mm" viewBox="0 0 100 200">'
                    '<rect x="10" y="10" width="40" height="150" fill="none" stroke="#000"/></svg>')
    base = [str(tall), "--tool", "4", "--thickness", "6", "--margin", "0.4", "--feed", "300", "--plunge", "200"]
    a, b = tmp_path / "a.nc", tmp_path / "b.nc"
    svg_a_gcode.main([*base, "--portrait", "-o", str(a)])
    svg_a_gcode.main([*base, "-o", str(b)])
    assert_same_gcode(a.read_text(), b.read_text())
