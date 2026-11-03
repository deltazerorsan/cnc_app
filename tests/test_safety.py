"""Los topes (cnc.safety), como los aplica el plan, el proyecto y la linea de comandos, y la lista de comprobacion (cnc.checklist)."""
from pathlib import Path

import pytest
from shapely.geometry import box

import svg_a_gcode
from cnc import checklist as cl
from cnc import safety
from cnc.model import ContourOp, DrillOp, PocketOp, Stock, Tool
from cnc.pipeline import JobSettings, plan_job
from cnc.project import General, Project, ProjectError
from cnc.svg_import import ImportedShape

DATA = Path(__file__).parent / "data"
BOARDS19 = DATA / "tableros_19mm.svg"
EM4 = Tool(diameter=4, name="End mill 4 mm", feed_xy=300, feed_z=200, stepdown=5.0, rpm=10000)


def shp(geom, stroke="#ff0000"):
    return ImportedShape(shape=geom, closed=True, stroke=stroke, fill=None)


def settings(**kw):
    base = dict(tool=EM4, thickness=19.0, margin=0.4, feed=300.0, plunge=200.0, stepdown=5.0)
    base.update(kw)
    return JobSettings(**base)


def plan(shapes=None, stock=(), board=None, **kw):
    return plan_job(shapes or [shp(box(0, 0, 80, 60))], list(stock), settings(**kw), board_number=board)


# --- los numeros ---------------------------------------------------------------------------------------------
def test_the_numbers_are_the_ones_roman_chose():
    assert (safety.MARGIN_MAX, safety.THICKNESS_ALARM, safety.THICKNESS_MAX, safety.STEP_FACTOR) == (0.5, 22.0, 30.0, 2.0)
    assert safety.max_step(4) == 8 and safety.max_step(3.175) == pytest.approx(6.35)


@pytest.mark.parametrize("margin, bad", [(0.0, False), (0.2, False), (0.4, False), (0.5, False), (0.5000000001, False),
                                         (0.5011, True), (0.51, True), (1.0, True), (10.0, True)])
def test_margin_over_half_a_millimetre_is_refused(margin, bad):
    why = safety.margin_problem(margin)
    assert bool(why) is bad
    if bad:
        assert "0,5 mm" in why and "0,2" in why and "0,4" in why and f"({safety._mm(margin)} mm)" in why


@pytest.mark.parametrize("thickness, bad", [(0.5, False), (19.0, False), (22.0, False), (30.0, False), (30.0000000001, False),
                                            (30.0011, True), (30.01, True), (190.0, True)])
def test_thickness_over_thirty_is_refused(thickness, bad):
    why = safety.thickness_problem(thickness)
    assert bool(why) is bad
    if bad:
        assert "30 mm" in why and f"({safety._mm(thickness)} mm)" in why


@pytest.mark.parametrize("thickness, alarm", [(6.0, False), (19.0, False), (22.0, False), (22.0000000001, False), (22.0005, True),
                                              (22.01, True), (24.0, True), (30.0, True), (30.0005, False), (30.01, False)])
def test_thickness_between_22_and_30_is_an_alarm(thickness, alarm):
    why = safety.thickness_alarm(thickness)
    assert bool(why) is alarm
    if alarm:
        assert "22 mm" in why and "30 mm" in why


def test_settings_problems_lists_both():
    assert safety.settings_problems(19.0, 0.4) == []
    both = safety.settings_problems(190.0, 10.0)
    assert len(both) == 2 and "grosor" in both[0] and "margen" in both[1]                       # siempre en este orden
    assert len(safety.settings_problems(19.0, 0.6)) == 1 and len(safety.settings_problems(31.0, 0.4)) == 1


def test_millimetres_are_written_with_a_comma_and_no_extra_zeros():
    assert safety._mm(0.5) == "0,5" and safety._mm(8.0) == "8" and safety._mm(19.4) == "19,4" and safety._mm(4.875) == "4,875"


# --- las pasadas -----------------------------------------------------------------------------------------------
def test_each_pass_is_the_equal_share_of_the_depth():
    stock = Stock(19.0, spoilboard_extra=0.4)
    contour = ContourOp(box(0, 0, 50, 50).exterior, side="on", stepdown=5.0)
    assert safety.operation_steps([contour], stock, EM4) == pytest.approx([19.4 / 4])


def test_a_missing_stepdown_comes_from_the_card_and_then_from_the_fallback():
    stock = Stock(19.0, spoilboard_extra=0.4)
    op = ContourOp(box(0, 0, 50, 50).exterior, side="on")
    assert safety.operation_steps([op], stock, EM4) == pytest.approx([19.4 / 4])               # ficha: 5 mm
    assert safety.operation_steps([op], stock, Tool(diameter=4)) == pytest.approx([19.4 / 10])  # respaldo: 2 mm


def test_a_drill_without_peck_is_one_plunge_and_with_peck_equal_shares():
    stock = Stock(19.0, spoilboard_extra=0.4)
    assert safety.operation_steps([DrillOp([(5, 5)])], stock, EM4) == pytest.approx([19.4])
    assert safety.operation_steps([DrillOp([(5, 5)], peck=6.0)], stock, EM4) == pytest.approx([19.4 / 4])


def test_a_pocket_uses_its_own_depth():
    stock = Stock(19.0, spoilboard_extra=0.4)
    assert safety.operation_steps([PocketOp(box(0, 0, 40, 40), depth=5.0, stepdown=2.0)], stock, EM4) == pytest.approx([5 / 3])


def test_several_operations_give_one_step_each_in_order():
    stock = Stock(19.0, spoilboard_extra=0.4)
    ops = [DrillOp([(5, 5)], peck=10.0), PocketOp(box(0, 0, 40, 40), depth=3.0, stepdown=3.0)]
    assert safety.operation_steps(ops, stock, EM4) == pytest.approx([19.4 / 2, 3.0])


def test_a_step_up_to_twice_the_diameter_passes_and_one_more_does_not():
    ok = [ContourOp(box(0, 0, 50, 50).exterior, side="on", stepdown=20.0)]                       # sin dividir la profundidad
    assert safety.operation_problems(ok, Stock(7.6, spoilboard_extra=0.4), EM4) == []           # 8,0 de una vez: justo
    over = safety.operation_problems(ok, Stock(7.7, spoilboard_extra=0.4), EM4)                  # 8,1 de una vez
    assert len(over) == 1 and "Operacion 1" in over[0] and "8,1 mm" in over[0] and "8 mm" in over[0]
    assert "2 x el diametro de 4 mm" in over[0] and "sin pasada, se corta de una sola vez" in over[0]


def test_a_hair_over_the_limit_is_already_too_much():
    """La tolerancia es solo para el ruido de los decimales: 8,001 mm con una fresa de 4 ya no vale."""
    op = [ContourOp(box(0, 0, 50, 50).exterior, side="on", stepdown=20.0)]
    assert safety.operation_problems(op, Stock(7.6, spoilboard_extra=0.4), EM4) == []              # 8,000 de una vez
    assert len(safety.operation_problems(op, Stock(7.6, spoilboard_extra=0.401), EM4)) == 1        # 8,001
    assert len(safety.operation_problems(op, Stock(7.6, spoilboard_extra=0.4005), EM4)) == 1       # 8,0005


def test_the_limit_follows_the_tool_diameter():
    op = [ContourOp(box(0, 0, 50, 50).exterior, side="on", stepdown=7.0)]
    stock = Stock(6.6, spoilboard_extra=0.4)                                                    # 7 mm de una vez
    assert safety.operation_problems(op, stock, Tool(diameter=4)) == []
    assert len(safety.operation_problems(op, stock, Tool(diameter=3.175))) == 1                  # 3,175 x 2 = 6,35


def test_every_operation_over_the_limit_is_named():
    stock = Stock(19.0, spoilboard_extra=0.4)
    ops = [ContourOp(box(0, 0, 50, 50).exterior, side="on", stepdown=19.4),
           ContourOp(box(0, 0, 50, 50).exterior, side="on", stepdown=4.0),
           DrillOp([(5, 5)])]
    out = safety.operation_problems(ops, stock, EM4)
    assert len(out) == 2 and "Operacion 1" in out[0] and "Operacion 3" in out[1]


def test_an_operation_with_no_valid_depth_raises_instead_of_hiding_it():
    with pytest.raises(ValueError, match="mayor que el grosor"):
        safety.operation_problems([PocketOp(box(0, 0, 40, 40), depth=25.0)], Stock(19.0), EM4)


# --- el G-code escrito ---------------------------------------------------------------------------------------
GOOD = "G21\nG90\nG54\nG0 Z5\nM3 S20000\nG0 X1 Y1\nG1 Z-6 F200\nG1 X10 Y1 F300\nG0 Z5\nM5\nM30\n"


def test_a_clean_program_has_no_problems():
    assert safety.gcode_problems(GOOD) == []


def test_a_cut_above_the_surface_or_without_feed_is_a_problem():
    assert any("por encima de la superficie" in p for p in safety.gcode_problems(GOOD.replace("G1 X10 Y1 F300", "G1 X10 Y1 Z1 F300")))
    assert any("sin avance" in p for p in safety.gcode_problems(GOOD.replace("G1 Z-6 F200\nG1 X10 Y1 F300", "G1 Z-6\nG1 X10 Y1")))


def test_codes_it_does_not_know_are_not_a_problem_because_the_end_code_is_the_users():
    assert safety.gcode_problems(GOOD.replace("M5\n", "M5\nM9\n")) == []


def test_a_line_over_79_characters_is_a_problem():
    assert any("79" in p for p in safety.gcode_problems(GOOD + "G0 X1 " + "(x)" * 30 + "Y" + "1" * 40 + "\n"))


# --- el plan ---------------------------------------------------------------------------------------------------
def test_a_good_job_has_nothing_in_the_way():
    p = plan()
    assert p.ok and p.writable and p.blockers == [] and p.alarms == [] and p.settings.thickness == 19.0


@pytest.mark.parametrize("margin, ok", [(0.2, True), (0.5, True), (0.6, False), (1.0, False), (10.0, False)])
def test_the_plan_refuses_a_margin_over_half_a_millimetre(margin, ok):
    p = plan(margin=margin)
    assert p.ok is ok
    if not ok:
        assert p.text is None and "margen en el sufridor" in p.error and not p.writable


@pytest.mark.parametrize("thickness, ok", [(19.0, True), (30.0, True), (30.5, False), (190.0, False)])
def test_the_plan_refuses_a_thickness_over_thirty(thickness, ok):
    p = plan(thickness=thickness, stepdown=8.0)
    assert p.ok is ok
    if not ok:
        assert "pasa de 30 mm" in p.error and p.text is None


def test_a_bad_thickness_and_a_bad_margin_are_both_reported_in_one_error():
    p = plan(thickness=31.0, margin=0.8)
    assert not p.ok and "grosor del material (31 mm)" in p.error and "margen en el sufridor (0,8 mm)" in p.error
    assert p.error.index("grosor") < p.error.index("margen")


def test_the_core_limit_for_the_margin_no_longer_follows_the_margin():
    p = plan(margin=0.5)
    assert p.ok and p.job.stock.max_spoilboard_extra == 0.5


def test_a_thickness_over_22_is_generated_with_an_alarm_that_has_to_be_confirmed():
    p = plan(thickness=24.0, stepdown=8.0)
    assert p.ok and p.writable and p.blockers == []
    assert any("Grosor de 24 mm" in a for a in p.alarms)
    assert plan(thickness=22.0, stepdown=8.0).alarms == []


def test_without_a_stepdown_the_whole_depth_in_one_pass_is_refused_when_it_is_too_much():
    p = plan(thickness=19.0, stepdown=None)
    assert not p.ok and "la pasada seria de 19,4 mm" in p.error and "8 mm" in p.error and p.text is None
    assert "operacion(es) mas" not in p.error                                  # una sola operacion: sin "y 0 mas"


def test_without_a_stepdown_a_thin_board_is_still_one_pass():
    p = plan(thickness=6.0, stepdown=None)
    assert p.ok and p.writable


def test_a_stepdown_over_twice_the_diameter_is_refused_even_when_it_splits_the_depth():
    p = plan(thickness=19.0, stepdown=19.0)                              # 19,4 / 2 = 9,7 mm por pasada
    assert not p.ok and "9,7 mm" in p.error


def test_many_operations_over_the_limit_give_one_message_with_the_count():
    shapes = [shp(box(0, 0, 20, 20)), shp(box(100, 0, 120, 20)), shp(box(200, 0, 220, 20))]
    p = plan(shapes, thickness=19.0, stepdown=None)
    assert not p.ok and "(y 2 operacion(es) mas)" in p.error and p.error.startswith("Operacion 1:")
    assert len([m for m in p.messages if m.level == "error"]) == 1


def test_an_operation_deeper_than_the_board_is_an_error_not_a_crash(tmp_path):
    p = project(tmp_path, SMALL)
    p.rows[0].through, p.rows[0].depth = False, 25.0                     # 25 mm en un grosor de 19
    result = p.plan(None)
    assert not result.ok and "mayor que el grosor" in result.error and not result.writable


def test_a_job_that_does_not_fit_the_machine_is_shown_but_not_writable():
    p = plan([shp(box(0, 0, 800, 50))], thickness=6.0, stepdown=None, rotate=0.0)
    assert p.ok and not p.writable
    assert len(p.blockers) == 1 and p.blockers[0].startswith("No cabe en la maquina")
    assert "en X" in p.blockers[0] and "651 mm" in p.blockers[0] and "faltan 153.0 mm" in p.blockers[0]
    assert any(m.severe for m in p.messages)                             # el aviso de siempre sigue


@pytest.mark.parametrize("width, fits", [(646.9, True), (647.1, False), (700.0, False)])
def test_the_machine_limit_has_no_tolerance_in_x(width, fits):
    """La fresa de 4 mm hace la trayectoria 4 mm mas ancha que la pieza: 647 + 4 = 651, el recorrido justo."""
    p = plan([shp(box(0, 0, width, 50))], thickness=6.0, stepdown=None, rotate=0.0)
    assert p.ok and p.writable is fits
    if not fits:
        assert len(p.blockers) == 1 and "en X" in p.blockers[0] and "en Y" not in p.blockers[0]


@pytest.mark.parametrize("height, fits", [(1006.9, True), (1007.1, False)])
def test_the_machine_limit_has_no_tolerance_in_y(height, fits):
    p = plan([shp(box(0, 0, 100, height))], thickness=6.0, stepdown=None, rotate=0.0)
    assert p.ok and p.writable is fits
    if not fits:
        assert len(p.blockers) == 1 and "en Y" in p.blockers[0] and "1011 mm" in p.blockers[0]
        assert "faltan 0.1 mm" in p.blockers[0]


def test_it_can_be_too_big_in_both_directions_and_each_one_is_named():
    p = plan([shp(box(0, 0, 800, 1100))], thickness=6.0, stepdown=None, rotate=0.0)
    assert len(p.blockers) == 2 and "en X" in p.blockers[0] and "en Y" in p.blockers[1]


def test_a_trajectory_that_sticks_out_of_the_board_is_an_alarm_not_a_block():
    board = [shp(box(0, 0, 200, 100), stroke="#2b2a29")]
    p = plan([shp(box(50, -1, 90, 40))], board, board=1, rotate=0.0)
    assert p.ok and p.writable and p.blockers == []
    assert any("se sale del tablero por abajo" in a and "fuera del material" in a for a in p.alarms)


def test_pieces_closer_than_the_tool_are_an_alarm_to_confirm():
    close = plan([shp(box(0, 0, 30, 30)), shp(box(31, 0, 61, 30))])
    assert close.writable and any("no cabe entre ellas" in a for a in close.alarms)
    apart = plan([shp(box(0, 0, 30, 30)), shp(box(40, 0, 70, 30))])
    assert apart.alarms == []


def test_a_zone_the_tool_cannot_reach_is_an_alarm_to_confirm():
    dumbbell = box(0, 0, 30, 30).union(box(30, 13, 50, 17)).union(box(50, 0, 80, 30))
    from shapely import affinity
    hole = affinity.translate(dumbbell, 30, 15)                          # un cuello de 4 mm, mas estrecho que la fresa de 6
    six = Tool(diameter=6.0, feed_xy=300, feed_z=200, stepdown=5.0)
    p = plan([shp(box(0, 0, 150, 60)), shp(hole)], tool=six)
    assert p.writable and any("no se pueden cortar" in a for a in p.alarms)
    three = Tool(diameter=3.0, feed_xy=300, feed_z=200, stepdown=5.0)
    assert plan([shp(box(0, 0, 150, 60)), shp(hole)], tool=three).alarms == []


def test_the_alarms_of_the_generator_are_the_spacing_and_reach_warnings():
    p = plan([shp(box(0, 0, 30, 30)), shp(box(31, 0, 61, 30))])
    assert [a for a in p.alarms if "no cabe entre ellas" in a] == [w for w in p.result.warnings if "no cabe entre ellas" in w]


def test_a_program_that_fails_the_verification_is_not_writable():
    p = plan(end_code="G0 X1 " + "Y1" * 50)                               # una linea de mas de 79 caracteres
    assert p.ok and not p.writable and any(b.startswith("El G-code no pasa la verificacion") and "79" in b for b in p.blockers)


def test_a_custom_end_code_with_codes_the_verifier_does_not_know_is_fine():
    p = plan(end_code="G0 X10 Y10\nM9")
    assert p.writable and p.blockers == []


def test_a_failed_plan_is_not_writable_and_a_plan_without_blockers_is_when_it_has_text():
    bad = plan(thickness=190.0)
    assert not bad.ok and not bad.writable
    good = plan()
    assert good.writable
    good.blockers.append("x")
    assert not good.writable


# --- el proyecto -----------------------------------------------------------------------------------------------
def project(tmp_path, body, **gen):
    svg = tmp_path / "d.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="900mm" height="1200mm" viewBox="0 0 900 1200">'
                   f"{body}</svg>")
    p = Project({"em4": EM4})
    p.load_svg(svg)
    base = dict(tool="em4", thickness=19.0)
    base.update(gen)
    p.general = General(**base)
    return p


BIG = '<rect x="0" y="0" width="800" height="1100" stroke="#ff0000" fill="none"/>'
SMALL = '<rect x="0" y="0" width="80" height="60" stroke="#ff0000" fill="none"/>'


def test_the_project_refuses_to_write_a_plan_that_does_not_fit_and_writes_nothing(tmp_path):
    p = project(tmp_path, BIG)
    plans = p.plan_selected()
    assert plans[0][1].ok and not plans[0][1].writable
    with pytest.raises(ProjectError, match="No cabe en la maquina"):
        p.write(tmp_path / "salida.nc", plans)
    assert not (tmp_path / "salida.nc").exists()


def test_the_project_writes_a_plan_that_is_fine(tmp_path):
    p = project(tmp_path, SMALL)
    out = p.write(tmp_path / "salida.nc", p.plan_selected())
    assert out == [tmp_path / "salida.nc"] and out[0].read_text().startswith("(")


def test_the_project_refuses_a_failed_plan_with_its_error(tmp_path):
    p = project(tmp_path, SMALL, thickness=190.0)
    with pytest.raises(ProjectError, match="pasa de 30 mm"):
        p.write(tmp_path / "salida.nc", p.plan_selected())


def test_with_several_boards_one_blocked_plan_blocks_all_of_them(tmp_path):
    p = project(tmp_path, BIG)
    good = project(tmp_path, SMALL).plan_selected()[0][1]
    bad = p.plan_selected()[0][1]
    with pytest.raises(ProjectError, match="Tablero 2"):
        p.write(tmp_path / "salida.nc", [(1, good), (2, bad)])
    assert not list(tmp_path.glob("*.nc"))


# --- la linea de comandos ------------------------------------------------------------------------------------
def svg_file(tmp_path, body, name="d.svg"):
    f = tmp_path / name
    f.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="900mm" height="1200mm" viewBox="0 0 900 1200">'
                 f"{body}</svg>")
    return f


def cli(tmp_path, body, *extra):
    out = tmp_path / "o.nc"
    code = svg_a_gcode.main([str(svg_file(tmp_path, body)), "--no-config", "--tool", "4", "--thickness", "6",
                             "--feed", "300", "--plunge", "200", "--rotate", "0", *extra, "-o", str(out)])
    return code, out


def test_the_command_line_writes_a_good_job_and_lists_what_to_check(tmp_path, capsys, checklist_on):
    code, out = cli(tmp_path, SMALL)
    text = capsys.readouterr().out
    assert code == 0 and out.exists()
    assert "Antes de ejecutarlo, comprueba:" in text and "  - La fresa montada es" in text and "  - El material mide 6 mm" in text


def test_the_command_line_does_not_write_a_job_that_does_not_fit(tmp_path, capsys):
    code, out = cli(tmp_path, BIG)
    err = capsys.readouterr().err
    assert code == 1 and not out.exists()
    assert "ERROR: No cabe en la maquina" in err and "No se escribe" in err


def test_the_command_line_refuses_a_margin_and_a_pass_over_the_limit(tmp_path, capsys):
    code, out = cli(tmp_path, SMALL, "--margin", "0.8")
    assert code == 1 and not out.exists() and "margen en el sufridor" in capsys.readouterr().err
    code, out = cli(tmp_path, SMALL, "--thickness", "19")
    assert code == 1 and not out.exists() and "la pasada seria" in capsys.readouterr().err


def test_the_command_line_marks_alarms_with_an_exclamation_mark(tmp_path, capsys, checklist_on):
    code, out = cli(tmp_path, SMALL, "--thickness", "24", "--stepdown", "8")
    text = capsys.readouterr().out
    assert code == 0 and out.exists() and "  ! Grosor de 24 mm" in text


# --- la lista de comprobacion ----------------------------------------------------------------------------------
def rows(section):
    return dict(section.rows)


def test_the_checklist_shows_what_the_program_knows():
    c = cl.build_checklist([(None, plan(thickness=19.0))])
    assert len(c.sections) == 1 and c.sections[0].title == ""
    r = rows(c.sections[0])
    assert list(r) == ["Maquina", "Fresa", "Material", "Pasadas", "Avances", "Cortes", "Trayectoria", "Tiempo", "Giro", "Cero",
                       "Z segura", "Husillo", "Al terminar"]
    assert r["Tiempo"].startswith("unos ") and "de maquina" in r["Tiempo"]
    assert r["Fresa"] == "End mill 4 mm, diametro 4 mm · 10000 RPM aconsejadas"
    assert r["Material"] == "19 mm de grosor + 0,4 mm en el sufridor = 19,4 mm de profundidad"
    assert r["Pasadas"] == "hasta 4,85 mm cada una (maximo 8 mm)"
    assert r["Avances"] == "corte 300, bajada 200 mm/min"
    assert r["Cortes"] == "1 exteriores"
    assert r["Trayectoria"] == "84,0 x 64,0 mm, de 651 x 1011 mm de recorrido"
    assert r["Giro"] == "sin giro" and r["Z segura"] == "5 mm sobre la superficie"
    assert r["Husillo"] == "lo arranca la aplicación (M3 S20000)"
    assert r["Al terminar"] == "sube a la Z segura y se queda donde acaba"


def test_the_checklist_asks_for_six_confirmations_when_nothing_is_wrong():
    c = cl.build_checklist([(None, plan())])
    assert [k.alarm for k in c.checks] == [False] * 6
    t = [k.text for k in c.checks]
    assert t[0] == ("La maquina conectada a UGS es «Taller» (recorrido 651 x 1011 x 100 mm): este G-code es para ella.")
    assert t[1] == "La fresa montada es End mill 4 mm (diametro 4 mm) y esta bien apretada."
    assert t[2] == "El material mide 19 mm de grosor."
    assert t[3].startswith("El cero (X0 Y0) esta en la esquina inferior izquierda de lo que se corta") and "Z0, en la cara superior" in t[3]
    assert t[4].startswith("El material esta sujeto") and "Z segura (5 mm)" in t[4]
    assert t[5] == ("El husillo esta parado (lo arranca la aplicación) y el variador esta ajustado para esta fresa y este "
                    "material (10000 RPM aconsejadas).")


def test_without_rpm_in_the_card_the_spindle_check_has_no_number():
    tool = Tool(diameter=4, feed_xy=300, feed_z=200, stepdown=5.0)
    c = cl.build_checklist([(None, plan(tool=tool))])
    assert c.checks[5].text.endswith("este material.") and "RPM" not in c.checks[5].text
    assert rows(c.sections[0])["Fresa"] == "de 4 mm, diametro 4 mm"


def test_when_the_program_does_not_start_the_spindle_it_says_so_twice():
    c = cl.build_checklist([(None, plan(emit_spindle=False))])
    assert rows(c.sections[0])["Husillo"] == "NO lo arranca la aplicación: se arranca a mano"
    assert c.checks[5].text.startswith("El husillo NO lo arranca la aplicación: lo arranco yo antes de empezar")


@pytest.mark.parametrize("kw, text", [(dict(home_at_end=True), "hace Home ($H, hace falta $22=1)"),
                                      (dict(return_to_origin=True), "vuelve al cero (X0 Y0) a la Z segura"),
                                      (dict(home_at_end=True, return_to_origin=True), "hace Home ($H, hace falta $22=1)")])
def test_the_ending_is_named(kw, text):
    assert rows(cl.build_checklist([(None, plan(**kw))]).sections[0])["Al terminar"] == text


def test_the_zero_is_named_for_each_way_of_placing_it():
    board = [shp(box(0, 0, 200, 100), stroke="#2b2a29")]
    piece = [shp(box(50, 20, 90, 40))]
    assert rows(cl.build_checklist([(1, plan(piece, board, board=1, rotate=0.0))]).sections[0])["Cero"] == \
        "la esquina inferior izquierda del tablero"
    assert rows(cl.build_checklist([(None, plan(origin_margin=4.0))]).sections[0])["Cero"] == \
        "la esquina inferior izquierda de lo que se corta, 4 mm por fuera"
    assert rows(cl.build_checklist([(None, plan(origin="center"))]).sections[0])["Cero"] == "el centro de lo que se corta"
    assert rows(cl.build_checklist([(None, plan(origin="page_bottom_left"))]).sections[0])["Cero"] == \
        "la esquina inferior izquierda de la pagina del dibujo"


def test_a_zero_margin_is_not_written_as_zero_mm_outside():
    assert rows(cl.build_checklist([(None, plan(origin_margin=0.0))]).sections[0])["Cero"] == \
        "la esquina inferior izquierda de lo que se corta"


def test_rotation_is_shown_only_when_there_is_one():
    assert rows(cl.build_checklist([(None, plan(rotate=90.0))]).sections[0])["Giro"] == "90 grados antihorario"
    assert rows(cl.build_checklist([(None, plan(rotate=0.0))]).sections[0])["Giro"] == "sin giro"
    assert rows(cl.build_checklist([(None, plan(rotate=360.0))]).sections[0])["Giro"] == "sin giro"


def test_cuts_are_counted_by_kind():
    shapes = [shp(box(0, 0, 100, 100)), shp(box(20, 20, 40, 40))]
    assert rows(cl.build_checklist([(None, plan(shapes))]).sections[0])["Cortes"] == "1 interiores, 1 exteriores"
    ops = [ContourOp(box(0, 0, 50, 50).exterior, side="on"), ContourOp(box(0, 0, 50, 50), side="outside"),
           PocketOp(box(0, 0, 40, 40), depth=3.0), DrillOp([(1, 1), (2, 2)]), DrillOp([(3, 3)])]
    assert cl._cuts_text(ops) == "1 exteriores, 1 sobre la linea, 1 vaciado(s), 3 taladro(s)" or \
        cl._cuts_text(ops) == "1 sobre la linea, 1 exteriores, 1 vaciado(s), 3 taladro(s)"
    assert cl._cuts_text([]) == "ninguno"


def test_feeds_that_differ_between_operations_are_shown_as_a_range():
    ops = [ContourOp(box(0, 0, 50, 50), side="outside", feed_xy=300, feed_z=200),
           ContourOp(box(0, 0, 50, 50), side="outside", feed_xy=1200, feed_z=200), DrillOp([(1, 1)], feed_z=100)]
    assert cl._feeds_text(ops, EM4) == "corte 300 a 1200, bajada 100 a 200 mm/min"
    assert cl._feeds_text([DrillOp([(1, 1)], feed_z=100)], EM4) == "bajada 100 mm/min"


def test_the_alarms_become_red_checks_that_say_how_to_confirm():
    p = plan(thickness=24.0, stepdown=8.0)
    c = cl.build_checklist([(None, p)])
    alarms = [k for k in c.checks if k.alarm]
    assert len(c.checks) == 7 and len(alarms) == 1
    assert alarms[0].text == "Grosor de 24 mm: pasa de 22 mm (el maximo es 30 mm). Lo he mirado y sigo adelante."
    assert c.checks[-1] is alarms[0]                                      # las alarmas, al final


def test_several_boards_get_a_section_each_and_labelled_alarms():
    close = [shp(box(0, 0, 30, 30)), shp(box(31, 0, 61, 30))]
    c = cl.build_checklist([(1, plan(close)), (2, plan(close, thickness=12.0))])
    assert [s.title for s in c.sections] == ["Tablero 1", "Tablero 2", "Todos los tableros"]
    assert c.checks[2].text == "El grosor del material es el indicado (Tablero 1: 19 mm; Tablero 2: 12 mm)."
    alarms = [k.text for k in c.checks if k.alarm]
    assert len(alarms) == 2 and alarms[0].startswith("Tablero 1: Operaciones 1 y 2") and alarms[1].startswith("Tablero 2: ")


def test_boards_with_the_same_thickness_share_one_confirmation():
    c = cl.build_checklist([(1, plan()), (2, plan())])
    assert c.checks[2].text == "El material mide 19 mm de grosor."


def test_the_same_alarm_text_is_not_asked_twice():
    p = plan(thickness=24.0, stepdown=8.0)
    c = cl.build_checklist([(None, p), (None, p)])
    assert len([k for k in c.checks if k.alarm]) == 1


def test_the_numbers_in_the_checklist_use_a_comma():
    assert cl._n(0.4) == "0,4" and cl._n(19.0) == "19" and cl._n(3.175) == "3,175" and cl._n1(74.0) == "74,0"


def test_a_recipe_job_shows_the_biggest_pass_and_the_deepest_cut():
    """La receta de ejemplo mezcla pasadas de 3, 5 y 6,467 mm y profundidades de 3, 10 y 19,4 mm: la fila dice la mayor."""
    p = Project({"em4": EM4})
    p.load_svg(BOARDS19)
    p.load_recipe_file(DATA / "receta_ejemplo.toml")
    p.general = General(tool="em4", thickness=19.0, margin=0.4, rotate="0")
    one = p.plan(1)
    assert one.ok
    rows = dict(cl.build_checklist([(1, one)]).sections[0].rows)
    assert rows["Pasadas"] == "hasta 6,467 mm cada una (maximo 8 mm)"
    assert rows["Material"] == "19 mm de grosor + 0,4 mm en el sufridor = 19,4 mm de profundidad"
    assert rows["Avances"] == "corte 600 a 1200, bajada 300 a 400 mm/min"


def test_feeds_left_empty_in_an_operation_come_from_the_tool_card():
    stock_ops = [ContourOp(box(0, 0, 50, 50).exterior, side="on")]
    assert cl._feeds_text(stock_ops, EM4) == "corte 300, bajada 200 mm/min"


def test_with_no_operations_the_feeds_row_says_there_is_no_data():
    assert cl._feeds_text([], EM4) == "sin dato"


def test_with_no_feed_in_the_operation_or_the_card_the_fallback_feeds_are_shown():
    op = ContourOp(box(0, 0, 50, 50).exterior, side="on")
    assert cl._feeds_text([op], Tool(diameter=4)) == "corte 1200, bajada 300 mm/min"
