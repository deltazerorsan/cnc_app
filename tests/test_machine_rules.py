"""Lo que la ficha de la maquina permite o no: Z que cabe, Home sin homing, modo laser, avances, y la cabecera del .nc."""
from dataclasses import replace

import pytest
from shapely.geometry import box

from cnc import checklist as cl
from cnc import safety
from cnc.generate import generate_gcode, machine_comment
from cnc.machines import parse_machine
from cnc.model import GRBL_MAQUINA_REAL, ContourOp, Job, MachineProfile, Stock, Tool
from cnc.pipeline import JobSettings, plan_job
from cnc.post_grbl import PostOptions
from cnc.svg_import import ImportedShape
from cnc.verify_nc import analyze

EM4 = Tool(diameter=4, name="End mill 4 mm", feed_xy=300, feed_z=200, stepdown=5.0, rpm=10000)


def machine(**kw):
    base = dict(recorrido=[651, 1011, 100])
    base.update(kw)
    return parse_machine("m", base)


def shp(geom):
    return ImportedShape(shape=geom, closed=True, stroke="#ff0000", fill=None)


def plan(m=GRBL_MAQUINA_REAL, **kw):
    base = dict(tool=EM4, thickness=19.0, margin=0.4, feed=300.0, plunge=200.0, stepdown=5.0, machine=m)
    base.update(kw)
    return plan_job([shp(box(0, 0, 80, 60))], [], JobSettings(**base))


def job(m, feed_xy=300.0, feed_z=200.0):
    return Job(Stock(6.0, 0.2), EM4, [ContourOp(box(0, 0, 50, 50), side="outside", feed_xy=feed_xy, feed_z=feed_z)],
               machine=m)


def problems(m, depth=6.2, safe_z=5.0, home=False, spindle=True):
    return safety.machine_problems(m, depth=depth, safe_z=safe_z, home_at_end=home, emit_spindle=spindle)


# --- Z -------------------------------------------------------------------------------------------------------------
def test_the_tool_has_to_fit_in_z_going_down_and_up_to_the_safe_height():
    m = machine(recorrido=[100, 100, 30])
    assert problems(m, depth=25.0, safe_z=5.0) == []                            # 30 de 30: justo
    assert problems(m, depth=25.0 + 1e-10, safe_z=5.0) == []                    # el redondeo no bloquea
    (why,) = problems(m, depth=25.1, safe_z=5.0)
    assert why == ("No cabe en Z: la fresa baja 25,1 mm y sube hasta 5 mm sobre la superficie (30,1 mm en total) y el "
                   "recorrido de Z de la maquina «m» es de 30 mm.")


def test_a_taller_safe_height_also_needs_room():
    m = machine(recorrido=[100, 100, 30])
    assert problems(m, depth=6.2, safe_z=23.8) == []
    assert "No cabe en Z" in problems(m, depth=6.2, safe_z=24.0)[0]


def test_the_real_machine_has_room_for_the_deepest_allowed_cut():
    assert problems(GRBL_MAQUINA_REAL, depth=safety.THICKNESS_MAX + safety.MARGIN_MAX, safe_z=5.0) == []


# --- Home ----------------------------------------------------------------------------------------------------------
def test_home_at_the_end_needs_a_machine_with_homing():
    assert "no tiene homing ($22=0)" in problems(machine(homing=False), home=True)[0]
    assert "«m»" in problems(machine(homing=False), home=True)[0]
    assert problems(machine(homing=True), home=True) == []
    assert problems(machine(), home=True) == []                                  # sin dato: no se sabe, no se bloquea
    assert problems(machine(homing=False), home=False) == []


def test_home_problem_is_the_same_text_used_for_the_marks():
    assert safety.home_problem(machine(homing=False), True) == problems(machine(homing=False), home=True)[0]
    assert safety.home_problem(machine(homing=False), False) is None
    assert safety.home_problem(MachineProfile(name="", max_feed=None, travel=(1.0, 1.0, 1.0), homing=False), True) \
        .startswith("La maquina «sin nombre»")


# --- modo laser ----------------------------------------------------------------------------------------------------
def test_laser_mode_with_a_spindle_started_by_the_program_is_refused():
    (why,) = problems(machine(laser=True), spindle=True)
    assert "modo laser ($32=1)" in why and "apaga el husillo en cada movimiento rapido (G0)" in why
    assert problems(machine(laser=True), spindle=False) == []                   # el husillo lo arranca la persona
    assert problems(machine(laser=False), spindle=True) == [] and problems(machine(), spindle=True) == []


def test_every_problem_is_listed():
    m = machine(recorrido=[100, 100, 10], homing=False, laser=True)
    found = problems(m, depth=8.0, home=True)
    assert len(found) == 3 and found[0].startswith("No cabe en Z") and "homing" in found[1] and "laser" in found[2]


# --- el plan -------------------------------------------------------------------------------------------------------
def test_a_machine_without_homing_blocks_a_job_that_ends_with_home_but_still_shows_it():
    p = plan(machine(homing=False), home_at_end=True)
    assert p.ok and not p.writable and any("no tiene homing" in b for b in p.blockers)
    ok = plan(machine(homing=False), home_at_end=False)
    assert ok.writable and ok.blockers == []
    assert plan(GRBL_MAQUINA_REAL, home_at_end=True).writable


def test_a_machine_too_short_in_z_blocks_the_job():
    p = plan(machine(recorrido=[651, 1011, 20]))
    assert p.ok and not p.writable and any(b.startswith("No cabe en Z: la fresa baja 19,4 mm") for b in p.blockers)


def test_a_machine_too_small_in_xy_still_blocks_with_its_own_message():
    p = plan(machine(recorrido=[60, 1011, 100]))
    assert not p.writable and any(b.startswith("No cabe en la maquina") for b in p.blockers)


def test_laser_mode_blocks_the_plan_unless_the_spindle_is_manual():
    assert not plan(machine(laser=True)).writable
    assert plan(machine(laser=True), emit_spindle=False).writable


# --- avances ---------------------------------------------------------------------------------------------------
def test_feeds_above_the_card_limits_are_warned_separately_for_xy_and_z():
    m = machine(avance_max=250, avance_max_z=150)
    warnings = generate_gcode(job(m, feed_xy=300, feed_z=200)).warnings
    assert any("feed_xy 300 mm/min supera el maximo de la maquina (250)" in w for w in warnings)
    assert any("feed_z 200 mm/min supera el maximo de la maquina (150)" in w for w in warnings)
    only_z = generate_gcode(job(m, feed_xy=250, feed_z=200)).warnings
    assert len([w for w in only_z if "supera el maximo" in w]) == 1 and "feed_z" in only_z[0]


def test_feeds_equal_to_the_limit_or_a_card_without_data_do_not_warn():
    assert not [w for w in generate_gcode(job(machine(avance_max=300, avance_max_z=200))).warnings if "supera" in w]
    assert not [w for w in generate_gcode(job(machine(), feed_xy=99999, feed_z=99999)).warnings if "supera" in w]
    xy_only = generate_gcode(job(machine(avance_max=100), feed_xy=99, feed_z=99999)).warnings
    assert not [w for w in xy_only if "supera" in w]                              # Z sin dato: no se compara con el de XY


# --- la cabecera del .nc ---------------------------------------------------------------------------------------
def test_the_nc_says_which_machine_it_is_for_in_the_second_line():
    text = generate_gcode(job(GRBL_MAQUINA_REAL)).text
    assert text.splitlines()[:2] == ["(Generado por cnc_app)", "(Maquina: Taller | recorrido 651 x 1011 x 100 mm)"]


def test_the_machine_line_is_ascii_and_never_longer_than_grbl_allows():
    odd = machine(nombre="Máquina (grande) ñandú", recorrido=[651.5, 1011, 100])
    assert machine_comment(odd) == "Maquina: Máquina (grande) ñandú | recorrido 651.5 x 1011 x 100 mm"
    text = generate_gcode(job(odd)).text
    line = text.splitlines()[1]
    assert line == "(Maquina: Maquina [grande] nandu | recorrido 651.5 x 1011 x 100 mm)" and line.isascii()
    long_name = machine(nombre="M" * 200)
    text = generate_gcode(job(long_name)).text
    header = text.splitlines()[1]
    assert len(header) <= 79 and header.endswith("| recorrido 651 x 1011 x 100 mm)") and header.startswith("(Maquina: MMM")
    assert all(len(line) <= 79 for line in text.splitlines())
    assert analyze(text).issues == []


def test_a_card_without_name_uses_its_id_in_the_header():
    assert machine_comment(machine()) == "Maquina: m | recorrido 651 x 1011 x 100 mm"
    assert machine_comment(MachineProfile(name="", max_feed=None, travel=(1.0, 2.0, 3.0))) == \
        "Maquina: sin nombre | recorrido 1 x 2 x 3 mm"


# --- la lista de comprobacion ---------------------------------------------------------------------------------
def test_the_checklist_names_the_machine_in_a_row_and_in_the_first_confirmation():
    c = cl.build_checklist([(None, plan(machine(nombre="Taller grande", recorrido=[651.5, 1011, 100])))])
    assert dict(c.sections[0].rows)["Maquina"] == "Taller grande, recorrido 651,5 x 1011 x 100 mm"
    assert list(dict(c.sections[0].rows))[0] == "Maquina"
    first = c.checks[0]
    assert not first.alarm
    assert first.text == ("La maquina conectada a UGS es «Taller grande» (recorrido 651,5 x 1011 x 100 mm): este G-code es "
                          "para ella.")


def test_the_trajectory_row_uses_the_travel_of_the_chosen_machine():
    c = cl.build_checklist([(None, plan(machine(recorrido=[700, 800, 100])))])
    assert dict(c.sections[0].rows)["Trayectoria"] == "84,0 x 64,0 mm, de 700 x 800 mm de recorrido"
