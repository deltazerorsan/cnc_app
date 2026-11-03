"""Trabajo completo con receta (taladros, pozos y cortes pasantes) contra G-code de referencia.

Datos reales: un SVG real con dos tableros de 620 x 1000 mm y el G-code de referencia de cada
uno (fresa de 4 mm, 19 mm de grosor + 0,4 de margen). Colores del dibujo: circulos negros rellenos = taladros
de 5 mm, #e63c24 = pozos de 3 mm, #ff0000 = cortes pasantes, #ffcc00 = ignorado.

La referencia corto el tablero 1 con el dibujo subido 6,97 mm (la pieza 60 sobresale 1 mm del borde): aqui se
reproduce con --offset 0,6.97. El tablero 2 va sin mover.
"""
import math
from pathlib import Path

import pytest

import svg_a_gcode
from cnc.boards import detect_boards
from cnc.recipe import _islands, classify, load_recipe
from cnc.svg_import import import_svg, is_degenerate
from comparar_nc import compare
from verificar_nc import analyze

DATA = Path(__file__).parent / "data"
SVG = DATA / "tableros_19mm.svg"
RECIPE = DATA / "receta_ejemplo.toml"
REF = {1: DATA / "ref_19mm_tablero1.nc", 2: DATA / "ref_19mm_tablero2.nc"}
ARGS = ["--no-config", "--tool", "em4", "--thickness", "19", "--margin", "0.4", "--receta", str(RECIPE),
        "--rotate", "0", "--pause-at-start"]
OFFSET = {1: ["--offset", "0,6.97"], 2: []}
# taladros, pozos, cortes por dentro, cortes por fuera (contornos que ve el analizador en nuestra salida)
OURS_SECTIONS = {1: (54, 8, 3, 4), 2: (41, 4, 3, 3)}


def length(c):
    return sum(math.dist(a, b) for a, b in zip(c, c[1:]))


def generate(tmp_path_factory, n, *extra):
    out = tmp_path_factory.mktemp(f"job19{n}") / "t.nc"
    assert svg_a_gcode.main([str(SVG), *ARGS, "--board", str(n), *extra, "-o", str(out)]) == 0
    return out.read_text()


@pytest.fixture(scope="module")
def ours(tmp_path_factory):
    return {n: generate(tmp_path_factory, n, *OFFSET[n]) for n in (1, 2)}


@pytest.fixture(scope="module")
def ref():
    return {n: REF[n].read_text() for n in (1, 2)}


@pytest.fixture(scope="module")
def sections(ours, ref):
    """Contornos de nuestra salida y de la referencia por seccion. La referencia baja cada taladro dos veces (-5,1 y -10) con
    subida entre medias, asi que tiene dos contornos por taladro; nosotros bajamos de nivel sin subir."""
    out = {}
    for n in (1, 2):
        a, b = analyze(ref[n]).contours, analyze(ours[n]).contours
        holes, wells, inside, outside = OURS_SECTIONS[n]
        assert len(b) == holes + wells + inside + outside
        i, j = 2 * holes, holes
        out[n] = dict(
            holes=(a[1:i:2], b[:holes]),                     # el segundo nivel (-10) de la referencia frente al nuestro
            wells=(a[i:i + wells], b[j:j + wells]),
            inside=(a[i + wells:i + wells + inside], b[j + wells:j + wells + inside]),
            outside=(a[i + wells + inside:], b[j + wells + inside:]),
        )
        assert len(a) == i + wells + inside + outside
    return out


# --- lectura del dibujo con la receta ---------------------------------------------------------------
@pytest.fixture(scope="module")
def counts():
    shapes = [s for s in import_svg(SVG).shapes if not is_degenerate(s)]
    layout = detect_boards(shapes)
    recipe = load_recipe(RECIPE)
    out = {}
    for b in layout.boards:
        per_op, ignored, unmatched = classify(recipe, [shapes[j] for j in b.contents])
        out[b.number] = (per_op, ignored, unmatched)
    return layout, out


def test_two_boards_and_every_shape_belongs_to_one(counts):
    layout, _ = counts
    assert [(b.number, round(b.width), round(b.height)) for b in layout.boards] == [(1, 620, 1000), (2, 620, 1000)]
    assert layout.loose == [] and layout.groupings == []        # la pieza 60, que sobresale 1 mm, sigue siendo del tablero 1


def test_recipe_finds_the_expected_shapes_on_each_board(counts):
    _, by_board = counts
    for n, expected in {1: (54, 6, 7), 2: (41, 4, 6)}.items():
        per_op, ignored, unmatched = by_board[n]
        assert tuple(len(g) for g in per_op) == expected
        assert len(ignored) == 4 and unmatched == []             # los trazos amarillos son guias


def test_wells_with_islands_keep_the_islands(counts):
    _, by_board = counts
    pockets = _islands(by_board[1][0][1])
    assert sorted(len(p.interiors) for p in pockets) == [0, 0, 0, 2]      # un pozo grande rodea dos islas
    assert all(len(p.interiors) == 0 for p in _islands(by_board[2][0][1]))


# --- el trabajo entero frente a la referencia -------------------------------------------------------
@pytest.mark.parametrize("n", [1, 2])
def test_whole_job_agrees_with_reference(ours, ref, n):
    c = compare(ref[n], ours[n], 4.0)
    a, b = c["a"], c["b"]
    assert c["rotation"] == 0 and c["shift"] == pytest.approx((0.0, 0.0), abs=0.1)       # mismo cero
    assert c["diff_area"] / c["footprint_a"] < 0.003                                        # la huella de la fresa casi coincide
    assert c["max_dev"] < 1.0 and c["mean_dev"] < 0.05
    assert (c["same_direction"], c["opposite_direction"]) == (c["contours"][0], 0)
    assert b.cut_length == pytest.approx(a.cut_length, rel=2e-3)
    assert b.minutes == pytest.approx(a.minutes, rel=0.03)
    assert (len(a.pauses), len(b.pauses)) == (1, 1)
    assert a.spindle_s == b.spindle_s == {20000.0} and a.z_min == b.z_min == -19.4
    for ours_xy, ref_xy in zip(b.bounds, a.bounds):
        assert ours_xy == pytest.approx(ref_xy, abs=0.2)
    assert b.ok, b.issues


@pytest.mark.parametrize("n", [1, 2])
def test_depths_and_feeds_of_each_operation(ours, n):
    r = analyze(ours[n])
    assert r.cut_feeds == {600.0, 900.0, 1200.0}                  # taladros, cortes, pozos (de la receta)
    assert r.plunge_feeds == {300.0, 400.0}
    levels = sorted(r.cut_z_levels, reverse=True)
    third = 19.4 / 3
    expected = [-3.0, -5.0, -third, -10.0, -2 * third, -19.4]    # pozos, taladros (2 pasadas), cortes (3 pasadas iguales)
    assert levels == pytest.approx(sorted(expected, reverse=True), abs=1e-3)
    assert max(levels) < 0                                         # nada corta por encima del material


@pytest.mark.parametrize("n", [1, 2])
def test_operations_are_cut_in_recipe_order(ours, n):
    """Taladros (Z -5/-10), luego pozos (Z -3), luego cortes pasantes (Z hasta -19,4)."""
    first_z = []
    for line in ours[n].splitlines():
        if line.startswith("G1 Z-") and line.split()[1] not in first_z:
            first_z.append(line.split()[1])
    assert first_z[:2] == ["Z-5", "Z-10"]
    assert first_z.index("Z-3") > first_z.index("Z-10")
    assert first_z.index("Z-19.4") > first_z.index("Z-3")


# --- seccion a seccion ---------------------------------------------------------------------------------
@pytest.mark.parametrize("n", [1, 2])
def test_drills_follow_the_same_order_and_are_cut_by_the_ring_only(sections, n):
    a, b = sections[n]["holes"]
    assert all(math.dist(p[0], q[0]) < 0.2 for p, q in zip(a, b))        # mismo agujero en el mismo puesto de la cadena
    assert all(3.0 < length(q) < 3.2 for q in b)                          # circulo de 1 mm de diametro (5 - 4): sin vaciar el centro
    assert all(length(p) == pytest.approx(length(q), rel=0.03) for p, q in zip(a, b))
    assert all(area_sign(q) == area_sign(p) for p, q in zip(a, b))


@pytest.mark.parametrize("n", [1, 2])
def test_reference_cuts_each_drill_in_two_levels_and_so_do_we(ref, ours, n):
    holes = OURS_SECTIONS[n][0]
    assert {-5.1, -10.0} <= analyze(ref[n]).plunge_targets
    text = ours[n].splitlines()
    plunges = [l for l in text if l.startswith("G1 Z-5 ") or l.startswith("G1 Z-10 ")]
    assert plunges == ["G1 Z-5 F400", "G1 Z-10 F400"] * holes


def test_wells_without_islands_match_one_to_one_on_board_two(sections):
    a, b = sections[2]["wells"]
    for p, q in zip(a, b):
        assert math.dist(p[0], q[0]) < 1.5
        assert length(q) == pytest.approx(length(p), rel=2e-3)


def test_wells_on_board_one_match_in_length_and_the_plain_ones_one_to_one(sections):
    """La referencia y nosotros dividen distinto el pozo con islas en trazos continuos; el largo total y los otros tres coinciden."""
    a, b = sections[1]["wells"]
    assert sum(map(length, b)) == pytest.approx(sum(map(length, a)), rel=2e-3)
    for p, q in zip(a[-3:], b[-3:]):
        assert math.dist(p[0], q[0]) < 1.5 and length(q) == pytest.approx(length(p), rel=2e-3)


@pytest.mark.parametrize("n,tol", [(1, 6.0), (2, 1.5)])
def test_inside_cuts_start_close_to_reference_and_are_as_long(sections, n, tol):
    a, b = sections[n]["inside"]
    for p, q in zip(a, b):
        assert math.dist(p[0], q[0]) < tol
        assert length(q) == pytest.approx(length(p), rel=1e-3)
        assert area_sign(p) == area_sign(q) < 0                              # huecos: sentido horario


@pytest.mark.parametrize("n", [1, 2])
def test_outside_cuts_are_the_same_pieces_in_the_same_order(sections, n):
    a, b = sections[n]["outside"]
    for p, q in zip(a, b):
        assert math.dist(p[0], q[0]) < 2.0
        assert length(q) == pytest.approx(length(p), rel=1e-3)
        assert area_sign(p) == area_sign(q) > 0                              # piezas: sentido antihorario


def area_sign(c):
    a = 0.5 * sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(c, c[1:] + c[:1]))
    return -1 if a < 0 else 1


# --- colocacion y avisos -----------------------------------------------------------------------------------
def test_board_one_without_offset_sticks_out_below_and_the_warning_gives_the_fix(tmp_path_factory, capsys):
    text = generate(tmp_path_factory, 1)
    out = capsys.readouterr().out
    assert "se sale del tablero por abajo 3.0 mm" in out and "--offset 0,7" in out
    assert analyze(text).bounds[1] < 0
    fixed = generate(tmp_path_factory, 1, "--offset", "0,7")
    assert "se sale del tablero" not in capsys.readouterr().out
    assert analyze(fixed).bounds[1] == pytest.approx(analyze(text).bounds[1] + 7, abs=1e-3)


def test_board_two_needs_no_offset(ours):
    assert analyze(ours[2]).bounds[0] > 0 and analyze(ours[2]).bounds[1] > 0


def test_offset_moves_every_cut_by_exactly_that_much(tmp_path_factory):
    base = analyze(generate(tmp_path_factory, 2))
    moved = analyze(generate(tmp_path_factory, 2, "--offset", "3,-2.5"))
    assert [m - b for m, b in zip(moved.bounds, base.bounds)] == pytest.approx([3, -2.5, 3, -2.5], abs=1e-3)
    assert moved.cut_length == pytest.approx(base.cut_length, rel=1e-9)


@pytest.mark.parametrize("value", ["7", "a,b", "1,2,3", "nan,0", "inf,0"])
def test_a_bad_offset_is_a_one_line_error(tmp_path, capsys, value):
    with pytest.raises(SystemExit) as e:
        svg_a_gcode.main([str(SVG), *ARGS, "--offset", value, "-o", str(tmp_path / "x.nc")])
    assert e.value.code == 2 and "DX,DY" in capsys.readouterr().err


def test_two_senseless_dots_in_the_drawing_are_dropped_with_a_notice(tmp_path_factory, capsys):
    generate(tmp_path_factory, 2)
    out = capsys.readouterr().out
    assert "2 resto(s) sin sentido descartado(s)" in out and "128 utiles de 130" in out
    assert "Ignoradas por la receta: 4 forma(s)" in out


def test_list_boards_with_a_recipe_shows_what_each_board_will_cut(capsys):
    assert svg_a_gcode.main([str(SVG), "--list-boards", "--no-config", "--receta", str(RECIPE)]) == 0
    out = capsys.readouterr().out
    assert "Taladros de 5 mm" in out and "Pozos de 3 mm" in out and "Cortes pasantes" in out
    rows = [l.split() for l in out.splitlines() if l[:1].isdigit()]
    assert [r[-3:] for r in rows] == [["54", "6", "7"], ["41", "4", "6"]]


def test_missing_or_broken_recipe_is_a_clean_error(tmp_path, capsys):
    for name, text, message in [("falta.toml", None, "No existe el archivo de receta"),
                                ("mala.toml", "[[operacion]]\ntipo = 'x'\ntrazo = '#ff0000'\n", "'tipo' debe ser uno de")]:
        path = tmp_path / name
        if text is not None:
            path.write_text(text, encoding="utf-8")
        with pytest.raises(SystemExit) as e:
            svg_a_gcode.main([str(SVG), "--no-config", "--tool", "em4", "--thickness", "19", "--receta", str(path),
                              "-o", str(tmp_path / "x.nc")])
        err = capsys.readouterr().err
        assert e.value.code == 2 and message in err and "Traceback" not in err
