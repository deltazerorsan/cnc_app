"""Donde cae el recorrido de la maquina sobre el dibujo: las cuatro esquinas (de 0,0 a X,Y de la maquina) contadas con
las coordenadas del SVG, con el mismo giro, cero y desplazamiento que tendra el G-code."""
import math
from dataclasses import replace
from pathlib import Path

import pytest
from shapely.geometry import box

from cnc.model import GRBL_MAQUINA_REAL, MachineProfile, Tool
from cnc.pipeline import lay_out, machine_frame, plan_job, JobSettings
from cnc.project import BoardOverrides, General, Project
from cnc.svg_import import ImportedShape

DATA = Path(__file__).parent / "data"
BOARDS19 = DATA / "tableros_19mm.svg"
TX, TY = 300.0, 500.0
MACHINE = MachineProfile(name="Prueba", max_feed=3000.0, travel=(TX, TY, 80.0))
TOOL = Tool(diameter=4, feed_xy=300, feed_z=200, stepdown=6)


def shp(geom):
    return ImportedShape(geom, True, stroke="#ff0000", kind="rect")


# el tablero, y lo que se corta dentro
STOCK = [shp(box(100, 50, 700, 450))]
KEPT = [shp(box(150, 100, 300, 200)), shp(box(400, 250, 650, 400))]


def lay(degrees, stock=STOCK, kept=KEPT, offset=None, margin=4.0):
    return lay_out(kept, stock, rotate_spec=float(degrees), portrait=False, tool=TOOL, machine=MACHINE, boards_mode=True,
                   origin="bbox_bottom_left", origin_margin=margin, offset=offset)


def close(frame, expected):
    assert len(frame) == len(expected)
    for got, want in zip(frame, expected):
        assert got == pytest.approx(want, abs=1e-4)


# --- el movimiento deshecho, con el tablero como referencia -----------------------------------------------------------
def test_without_turning_the_machine_starts_at_the_lower_left_corner_of_the_board():
    close(lay(0).frame, [(100, 50), (100 + TX, 50), (100 + TX, 50 + TY), (100, 50 + TY)])


def test_turned_a_quarter_the_machine_starts_at_the_upper_left_corner_and_its_x_runs_down():
    close(lay(90).frame, [(100, 450), (100, 450 - TX), (100 + TY, 450 - TX), (100 + TY, 450)])


def test_turned_half_the_machine_starts_at_the_upper_right_corner():
    close(lay(180).frame, [(700, 450), (700 - TX, 450), (700 - TX, 450 - TY), (700, 450 - TY)])


def test_turned_three_quarters_the_machine_starts_at_the_lower_right_corner_and_its_x_runs_up():
    close(lay(270).frame, [(700, 50), (700, 50 + TX), (700 - TY, 50 + TX), (700 - TY, 50)])


def test_a_full_turn_is_no_turn():
    close(lay(360).frame, lay(0).frame)


@pytest.mark.parametrize("degrees", [0, 90, 180, 270])
def test_the_frame_is_a_rectangle_of_the_travel_size_whatever_the_turn(degrees):
    f = lay(degrees).frame
    sides = [math.dist(f[i], f[(i + 1) % 4]) for i in range(4)]
    assert sides == pytest.approx([TX, TY, TX, TY])
    assert (f[1][0] - f[0][0]) * (f[3][0] - f[0][0]) + (f[1][1] - f[0][1]) * (f[3][1] - f[0][1]) == pytest.approx(0, abs=1e-6)


def test_a_turn_that_is_not_a_quarter_gives_a_turned_rectangle():
    f = lay(30).frame
    sides = [math.dist(f[i], f[(i + 1) % 4]) for i in range(4)]
    assert sides == pytest.approx([TX, TY, TX, TY])
    ux, uy = f[1][0] - f[0][0], f[1][1] - f[0][1]
    assert math.degrees(math.atan2(uy, ux)) == pytest.approx(-30)              # el eje X de la maquina, girado al reves


def test_the_offset_moves_the_machine_the_other_way_over_the_drawing():
    close(lay(0, offset=(5, 7)).frame[0:1], [(100 - 5, 50 - 7)])
    assert len(lay(0, offset=(5, 7)).frame) == 4
    # girado 90 grados, un desplazamiento (dx, dy) de la maquina es (dy, -dx) del dibujo, asi que el cero se corre al reves
    assert lay(90, offset=(5, 7)).frame[0] == pytest.approx((100 - 7, 450 + 5), abs=1e-4)


def test_without_a_board_the_zero_is_the_margin_below_and_left_of_what_is_cut():
    f = lay(0, stock=[], margin=4.0).frame
    close(f[0:1], [(150 - 4, 100 - 4)])
    assert lay(0, stock=[], margin=0.0).frame[0] == pytest.approx((150, 100))
    assert lay(0, stock=[], margin=10.0).frame[0] == pytest.approx((140, 90))


def test_the_assumed_board_is_not_used_as_the_reference_of_the_frame():
    result = lay(0, stock=[], margin=4.0)
    assert result.virtual and result.stock and result.frame[0] == pytest.approx((146, 96))


def test_what_the_frame_says_matches_where_the_job_really_lands():
    """El cero que sale en el dibujo es la esquina del tablero que de verdad pasa a ser 0,0 en la maquina."""
    for degrees in (0, 90, 180, 270):
        r = lay(degrees)
        assert min(s.shape.bounds[0] for s in r.stock) == pytest.approx(0, abs=1e-9)
        assert min(s.shape.bounds[1] for s in r.stock) == pytest.approx(0, abs=1e-9)
        corners = {(100, 50): 0, (100, 450): 90, (700, 450): 180, (700, 50): 270}
        assert tuple(round(v) for v in r.frame[0]) in corners and corners[tuple(round(v) for v in r.frame[0])] == degrees


# --- entradas raras ---------------------------------------------------------------------------------------------------
def test_without_shapes_there_is_no_frame():
    assert machine_frame([], [], 0.0, MACHINE) == ()
    assert machine_frame(KEPT, [], 0.0, MACHINE) == ()
    assert machine_frame(KEPT, KEPT[:1], 0.0, MACHINE) == ()                # no son las mismas formas: no se fia


def test_an_empty_shape_gives_no_frame():
    from shapely.geometry import Polygon
    empty = ImportedShape(Polygon(), True)
    assert machine_frame([empty], [empty], 0.0, MACHINE) == ()


def test_lines_work_as_well_as_polygons():
    from shapely.geometry import LineString
    line = ImportedShape(LineString([(10, 10), (60, 40)]), False, stroke="#000000", kind="line")
    r = lay_out([line], [], rotate_spec=90.0, portrait=False, tool=TOOL, machine=MACHINE, boards_mode=False,
                origin="bbox_bottom_left", origin_margin=0.0, offset=None)
    assert len(r.frame) == 4 and r.frame[0] == pytest.approx((10, 40), abs=1e-4)


# --- el trabajo de la ventana ---------------------------------------------------------------------------------------
@pytest.fixture
def project():
    p = Project({"em4": TOOL}, {"real": GRBL_MAQUINA_REAL, "chica": MACHINE})
    p.general = replace(p.general, machine="real")
    p.load_svg(BOARDS19)
    return p


def test_each_board_has_its_own_frame_starting_at_its_own_corner(project):
    one, two = project.units()
    assert one.number == 1 and two.number == 2
    f1, f2 = project.machine_frame(1), project.machine_frame(2)
    assert f1[0] == pytest.approx((one.bounds[0], one.bounds[1]), abs=0.01)
    assert f2[0] == pytest.approx((two.bounds[0], two.bounds[1]), abs=0.01)
    assert f2[0][0] > f1[0][0] + 600
    assert f1[2][0] - f1[0][0] == pytest.approx(GRBL_MAQUINA_REAL.travel[0])
    assert f1[2][1] - f1[0][1] == pytest.approx(GRBL_MAQUINA_REAL.travel[1])


def test_the_frame_is_there_before_the_tool_and_the_thickness_are_chosen(project):
    assert project.general.tool == "" and project.general.thickness is None
    plan = project.plan(1)
    assert not plan.ok and "fresa" in plan.error
    assert len(plan.frame) == 4 and plan.frame == project.machine_frame(1)


def test_a_plan_that_works_gives_the_same_frame_as_the_one_asked_alone(project):
    project.general = replace(project.general, tool="em4", thickness=19.0)
    for n in (1, 2):
        plan = project.plan(n)
        assert plan.ok and plan.frame == project.machine_frame(n)


def test_the_frame_follows_the_machine_chosen(project):
    project.general = replace(project.general, machine="chica")
    f = project.machine_frame(1)
    assert math.dist(f[0], f[1]) == pytest.approx(TX) or math.dist(f[0], f[1]) == pytest.approx(TY)   # la ficha chica, girada o no
    assert sorted((round(math.dist(f[0], f[1])), round(math.dist(f[1], f[2])))) == [300, 500]
    project.general = replace(project.general, machine="real")
    g = project.machine_frame(1)
    assert sorted((round(math.dist(g[0], g[1])), round(math.dist(g[1], g[2])))) == [651, 1011]


def test_a_board_that_does_not_fit_unturned_is_shown_turned_when_it_fits_that_way(project):
    project.general = replace(project.general, machine="chica", tool="em4", thickness=19.0)
    plan = project.plan(1)
    f = project.machine_frame(1)
    assert plan.degrees in (0.0, 90.0)
    expected_start = (project.unit(1).bounds[0], project.unit(1).bounds[1] if plan.degrees == 0 else project.unit(1).bounds[3])
    assert f[0] == pytest.approx(expected_start, abs=0.01)


def test_the_turn_chosen_in_the_panel_is_the_one_drawn(project):
    b = project.unit(1).bounds
    project.general = replace(project.general, rotate="90")
    f = project.machine_frame(1)
    assert f[0] == pytest.approx((b[0], b[3]), abs=0.01)                    # girado 90: el cero es la esquina de arriba a la izquierda
    project.general = replace(project.general, rotate="0")
    assert project.machine_frame(1)[0] == pytest.approx((b[0], b[1]), abs=0.01)


def test_no_frame_without_a_machine_or_shapes_or_with_settings_that_do_not_hold(project):
    assert project.machine_frame(1)
    project.machines = {}
    assert project.machine_frame(1) == () and project.plan(1).frame == ()
    project.machines = {"real": GRBL_MAQUINA_REAL, "chica": MACHINE}
    assert project.machine_frame(1)
    project.general = replace(project.general, machine="")
    assert project.machine_frame(1) == ()                                    # dos fichas y ninguna elegida: no se sabe cual
    project.general = replace(project.general, machine="real")
    assert project.machine_frame(1)
    assert project.machine_frame(99) == ()                                   # un tablero que no existe
    project.general = replace(project.general, origin_margin=-1.0)
    assert project.machine_frame(1) == ()
    project.general = replace(project.general, origin_margin=4.0, rotate="giro raro")
    assert project.machine_frame(1) == ()
    project.general = replace(project.general, rotate="auto")
    for row in project.rows:
        row.action = "ignorar"
    assert project.machine_frame(1) == ()                                    # todo en Ignorar: nada que cortar


def test_the_tool_only_matters_to_the_automatic_turn(project):
    a = project.machine_frame(1)
    project.general = replace(project.general, tool="em4")
    assert project.machine_frame(1) == a


def test_plan_job_still_reports_the_same_frame_as_lay_out():
    settings = JobSettings(tool=TOOL, thickness=19.0, margin=0.4, feed=300, plunge=200, stepdown=6, rotate=90.0,
                           portrait=False, origin="bbox_bottom_left", origin_margin=4.0, offset=(5.0, 7.0), machine=MACHINE)
    plan = plan_job(KEPT, STOCK, settings, None, 1)
    assert plan.frame == lay(90, offset=(5, 7)).frame


def test_the_frame_follows_the_automatic_turn_of_the_gcode():
    """Una maquina que solo da para el tablero girado: el contorno sale girado igual que el G-code."""
    wide = MachineProfile(name="Ancha", max_feed=3000.0, travel=(1100.0, 650.0, 80.0))
    p = Project({"em4": TOOL}, {"ancha": wide})
    p.load_svg(BOARDS19)
    p.general = replace(p.general, machine="ancha", tool="em4", thickness=19.0)
    plan = p.plan(1)
    assert plan.ok and plan.degrees == 90.0
    f = p.machine_frame(1)
    assert f == plan.frame
    b = p.unit(1).bounds
    assert f[0] == pytest.approx((b[0], b[3]), abs=0.01)                      # girado: el cero esta arriba a la izquierda


def test_what_the_table_ignores_does_not_count_for_the_zero_of_a_drawing_without_a_board(tmp_path):
    svg = tmp_path / "sin_tablero.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="600mm" height="450mm" viewBox="0 0 600 450">'
                   '<rect x="10" y="10" width="40" height="30" stroke="#ff0000" fill="none"/>'
                   '<rect x="300" y="200" width="100" height="80" stroke="#0000ff" fill="none"/></svg>')
    p = Project({"em4": TOOL}, {"real": GRBL_MAQUINA_REAL})
    p.general = replace(p.general, machine="real")
    p.load_svg(svg)
    everything = p.machine_frame(None)
    boxes = {s.stroke: s.shape.bounds for s in p.drawing.shapes}
    assert everything[0] == pytest.approx((min(b[0] for b in boxes.values()) - 4, min(b[1] for b in boxes.values()) - 4), abs=0.01)
    for row in p.rows:
        if row.stroke == "#ff0000":
            row.action = "ignorar"
    only_blue = p.machine_frame(None)
    assert only_blue[0] == pytest.approx((boxes["#0000ff"][0] - 4, boxes["#0000ff"][1] - 4), abs=0.01)
    assert only_blue[0][0] > everything[0][0] + 100                              # el cero se fue con lo que sigue cortandose


def test_the_offset_of_a_board_moves_the_frame_the_same_way_it_moves_the_job(project):
    project.general = replace(project.general, tool="em4", thickness=19.0)
    plain = project.machine_frame(1)
    project.overrides[1] = BoardOverrides(offset=(7.0, -3.0))
    moved = project.machine_frame(1)
    assert moved != plain                                                        # el desplazamiento cuenta
    plan = project.plan(1)
    assert plan.ok and plan.frame == moved                                       # y es el que usa el G-code
    dx, dy = moved[0][0] - plain[0][0], moved[0][1] - plain[0][1]
    assert math.hypot(dx, dy) == pytest.approx(math.hypot(7.0, 3.0), abs=0.01)   # la misma distancia, girada o no


def test_shapes_with_no_row_in_the_table_are_not_cut_so_they_do_not_place_the_zero(tmp_path):
    svg = tmp_path / "dos_colores.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="600mm" height="450mm" viewBox="0 0 600 450">'
                   '<rect x="10" y="10" width="40" height="30" stroke="#ff0000" fill="none"/>'
                   '<rect x="300" y="200" width="100" height="80" stroke="#0000ff" fill="none"/></svg>')
    p = Project({"em4": TOOL}, {"real": GRBL_MAQUINA_REAL})
    p.general = replace(p.general, machine="real", tool="em4", thickness=19.0)
    p.load_svg(svg)
    p.rows[1].action = "vaciado"                                                 # con receta (no todo es corte normal)
    p.rows = [r for r in p.rows if r.stroke != "#ff0000"]                        # lo rojo se queda sin fila: no se corta
    plan = p.plan(None)
    assert plan.ok and plan.frame == p.machine_frame(None)
    blue = next(s for s in p.drawing.shapes if s.stroke == "#0000ff").shape.bounds
    assert plan.frame[0] == pytest.approx((blue[0] - 4, blue[1] - 4), abs=0.01)  # el cero va con lo azul, que es lo que se corta
