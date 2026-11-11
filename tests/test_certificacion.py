"""Certificacion de la salida .nc: verificador independiente + simulacion geometrica."""
import re
from pathlib import Path

import pytest
from shapely.geometry import LineString, Point
from shapely.ops import unary_union

import svg_a_gcode
from cnc.model import Tool
from cnc.pipeline import JobSettings, load_drawing, plan_job
from cnc.svg_import import import_svg, place, suggest_sides
from verificar_nc import analyze, main as verify_main

SVG = Path(__file__).parent / "data" / "placa_doble.svg"
BOARD = "#2b2a29"
REF = ["--tool", "4", "--thickness", "6", "--margin", "0.4", "--feed", "300", "--plunge", "200",
       "--exclude-color", BOARD, "--origin-margin", "0"]        # la esquina del dibujo en el cero: asi se compara con la geometria


@pytest.fixture(scope="module")
def nc(tmp_path_factory):
    """El G-code de referencia sin girar. Mide 998,7 mm en X y el recorrido es de 651: por la linea de comandos el programa
    no lo escribe (lo bloquea), asi que se pide al planificador, que lo genera igualmente para poder certificarlo."""
    drawing = load_drawing(SVG, exclude_colors=[BOARD])
    settings = JobSettings(tool=Tool(diameter=4.0), thickness=6, margin=0.4, feed=300, plunge=200, origin_margin=0.0)
    plan = plan_job(drawing.shapes, drawing.stock, settings)
    assert plan.ok and any("No cabe en la maquina" in b for b in plan.blockers)
    out = tmp_path_factory.mktemp("nc") / "salida.nc"
    out.write_text(plan.text, encoding="ascii")
    return out


def test_unrotated_reference_is_refused_by_the_command_line(tmp_path, capsys):
    out = tmp_path / "salida.nc"
    assert svg_a_gcode.main([str(SVG), *REF, "-o", str(out)]) == 1
    assert not out.exists() and "No cabe en la maquina" in capsys.readouterr().err


def test_reference_parameters_end_up_in_the_gcode(nc):
    r = analyze(nc.read_text(), travel=(651, 1011, 100))
    assert r.cut_feeds == {300.0} and r.plunge_feeds == {200.0}
    assert r.cut_z_levels == {-6.4} and r.plunge_targets == {-6.4}      # 6 + 0,4 en una sola pasada
    assert r.z_min == -6.4 and r.z_max == 5.0
    assert r.spindle_s == {20000.0} and [c for _, c, _ in r.spindle] == ["M3 S20000", "M5"]
    assert r.units_mm and r.absolute and r.wcs == 54 and r.ended
    assert r.unknown == []
    # el unico problema es el recorrido: 998,7 mm no caben en X (651) si no se gira el dibujo
    assert len(r.issues) == 1 and "recorrido" in r.issues[0]


def test_rotated_job_fits_and_verifies_clean(tmp_path):
    out = tmp_path / "girado.nc"
    svg_a_gcode.main([str(SVG), *REF, "--rotate", "90", "-o", str(out)])
    r = analyze(out.read_text(), travel=(651, 1011, 100))
    assert r.ok, r.issues


def test_tool_footprint_does_not_gouge_the_parts(nc):
    rep = analyze(nc.read_text())
    radius = 2.0
    footprint = unary_union([LineString(s).buffer(radius, quad_segs=32) for s in rep.cut_segments])
    shapes = place([s for s in import_svg(SVG).shapes if s.stroke != BOARD])
    sides = suggest_sides(shapes)
    pieces = unary_union([s.shape for s, sd in zip(shapes, sides) if sd == "outside"])
    holes = unary_union([s.shape for s, sd in zip(shapes, sides) if sd == "inside"])
    part = pieces.difference(holes)

    # 1) sin sobrecorte
    assert footprint.intersection(part.buffer(-0.02)).area < 1e-3
    # 2) el centro de la fresa va siempre a un radio del contorno de la pieza
    devs = [abs(Point(p).distance(part) - radius) for a, b in rep.cut_segments for p in (a, b)]
    assert max(devs) < 0.002
    # 3) casi todo el contorno queda recorrido; lo que no, son esquinas interiores
    untouched = part.boundary.difference(footprint.buffer(0.02))
    assert untouched.length < 0.03 * part.boundary.length
    assert max(g.length for g in getattr(untouched, "geoms", [untouched])) < 12.0


def test_holes_are_cut_before_pieces(nc):
    # posicion XY de cada bajada (G1 Z-6.4 F200): una por contorno, en el orden del archivo
    plunges, x, y = [], None, None
    for line in nc.read_text().splitlines():
        if line.startswith("(") or not line:
            continue
        for axis in "XY":
            m = re.search(axis + r"(-?\d+\.?\d*)", line)
            if m:
                if axis == "X":
                    x = float(m.group(1))
                else:
                    y = float(m.group(1))
        if line == "G1 Z-6.4 F200":
            plunges.append(Point(x, y))
    assert len(plunges) == 14

    shapes = place([s for s in import_svg(SVG).shapes if s.stroke != BOARD])
    sides = suggest_sides(shapes)
    holes = unary_union([s.shape for s, sd in zip(shapes, sides) if sd == "inside"])
    pieces = unary_union([s.shape for s, sd in zip(shapes, sides) if sd == "outside"])
    # la fresa de un contorno interior arranca dentro de un hueco; la de uno exterior, fuera de toda pieza
    assert all(holes.contains(p) for p in plunges[:4])
    assert not any(pieces.buffer(1e-6).contains(p) for p in plunges[4:])
    assert not any(holes.contains(p) for p in plunges[4:])


# ---------- el verificador detecta problemas ----------

GOOD = "G21 G90 G17 G94 G54\nG0 Z5\nM3 S20000\nG4 P2\nG0 X0 Y0\nG1 Z-3 F200\nG1 X10 F300\nG0 Z5\nM5\nM30\n"


def test_verifier_accepts_a_clean_file():
    assert analyze(GOOD).ok


@pytest.mark.parametrize("mutation, expected", [
    (lambda t: t.replace("M5\n", ""), "M5"),
    (lambda t: t.replace("M30\n", ""), "M30"),
    (lambda t: t.replace("G1 Z-3 F200", "G1 Z-3"), "sin avance"),
    (lambda t: t.replace("G0 X0 Y0\n", "G0 X0 Y0\nG1 Z-3 F200\nG0 X5 Y5\n"), "G0 con desplazamiento XY"),
    (lambda t: t.replace("G1 X10 F300", "G1 X10 Z4 F300"), "rampa"),
    (lambda t: t.replace("G0 Z5\nM3", "G0 Z0\nM3"), "husillo arranca"),
    (lambda t: t.replace("G21 ", ""), "G21"),
])
def test_verifier_flags_problems(mutation, expected):
    text = mutation(GOOD)
    r = analyze(text)
    assert any(expected in m for m in r.issues), r.issues


def test_verifier_flags_unknown_codes_and_cutting_in_air():
    r = analyze(GOOD.replace("G0 X0 Y0", "G83 X0 Y0 Z-5 R1"))
    assert any("G83" in m for m in r.unknown)
    r = analyze("G21 G90\nG0 Z5\nG0 X0 Y0\nG1 X10 F300\nM30\n")
    assert any("por encima de la superficie" in m for m in r.issues)


def test_verifier_flags_a_cut_with_the_spindle_off_only_if_the_program_starts_it():
    # el husillo se para a medio corte: el aviso sale una vez, con su linea y en su sitio entre los demas
    long_line = "(" + "x" * 80 + ")\n"
    early = long_line + GOOD.replace("G1 X10 F300\n", "G1 X10 F300\nM5\nG1 X20\nG1 X30 Z-2\nM3 S20000\n")
    issues = analyze(early).issues
    assert len(issues) == 3 and "caracteres" in issues[0] and "rampa" in issues[2]
    assert issues[1] == "linea 10: corte con el husillo apagado"
    # el M3 llega despues del corte: cuenta igual (el programa si arranca el husillo)
    late = "G21 G90\nG0 Z5\nG0 X0 Y0\nG1 Z-3 F200\nG1 X10 F300\nG0 Z5\nM3 S20000\nM5\nM30\n"
    assert analyze(late).issues == ["linea 5: corte con el husillo apagado"]
    # sin ningun M3 el husillo se maneja a mano: no hay nada que avisar
    assert analyze(late.replace("M3 S20000\nM5\n", "")).ok


def test_verifier_reads_the_program_format_and_any_other_the_same():
    """Las lineas como las escribe el programa («G1 X10 Y5») van por un camino rapido: da lo mismo que el general."""
    plain = GOOD.replace("G1 X10 F300", "G1 X10 Y-.5 F300\nG1 X12. Y3")
    loose = plain.replace("G1 X10 Y-.5 F300", "g1x10 y-.5  F300").replace("G1 X12. Y3", "G1  X12.Y3")
    a, b = analyze(plain), analyze(loose)
    assert a.ok and b.ok and vars(a) == vars(b) and a.cut_segments[-1] == ((10.0, -0.5), (12.0, 3.0))
    for bad in ("G1 X1e3 F300", "G1 X10 Y F300", "G1 X10 W3 F300", "G1 X1_0 F300"):       # lo que no es letra + numero, se dice
        assert analyze(GOOD.replace("G1 X10 F300", bad)).unknown, bad


def test_verifier_cli_exit_code(tmp_path, capsys):
    f = tmp_path / "a.nc"
    f.write_text(GOOD)
    assert verify_main([str(f)]) == 0
    f.write_text(GOOD.replace("M5\n", ""))
    assert verify_main([str(f)]) == 1
    assert "PROBLEMAS" in capsys.readouterr().out


def test_verifier_understands_compact_reference_syntax():
    compact = "G90\nG21\nM05\nM0 ;T1004\nM03S20000\nG0X10Y10Z5.000\nG1Z-6.400F200.0\nX20Y10F300.0\nZ5.000\nM05\nM02\n"
    r = analyze(compact)
    assert r.ok, (r.issues, r.unknown)
    assert r.pauses == [4] and r.ended and r.spindle_s == {20000.0}
    assert [c for _, c, _ in r.spindle] == ["M5", "M3 S20000", "M5"]
    assert len(r.contours) == 1 and r.contours[0][-1] == (20.0, 10.0)
    # y detecta el husillo sin apagar con esa sintaxis compacta
    assert any("M5" in m for m in analyze(compact.replace("M05\nM02", "M02")).issues)


def test_verifier_measures_feed_moves_in_the_air():
    # G1 de Z5 a Z-3: los primeros 5 mm (de Z5 a Z0) son en el aire, a F200 = 1,5 s
    r = analyze("G21 G90\nG0 X0 Y0 Z5\nG1 Z-3 F200\nG0 Z5\nM30\n")
    assert r.air_mm == pytest.approx(5.0) and r.air_seconds == pytest.approx(1.5)
    assert analyze("G21 G90\nG0 X0 Y0 Z5\nG0 Z-3\nG1 X5 F200\nG0 Z5\nM30\n").air_mm == 0.0   # todo en rapido o dentro del material
