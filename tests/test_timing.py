"""El tiempo de maquina estimado (cnc.timing, sin Qt): el modelo del planificador de GRBL con cuentas hechas a mano, lo que
cuenta y lo que no (pausas, esperas), la ficha de la maquina, el plan, la lista de comprobacion y la linea de comandos."""
import math
from pathlib import Path

import pytest

from cnc.model import GRBL_MAQUINA_REAL, MachineProfile
from cnc.timing import TimeEstimate, describe, estimate_for_machine, estimate_time, format_duration

ACC = (200.0, 200.0, 100.0)
KW = dict(accel=ACC, max_feed=3600.0, max_feed_z=3600.0)
HEAD = "G21 G90 G17 G94 G54\nG0 X0 Y0 Z5\n"          # el primer movimiento solo fija donde esta la maquina


def est(body, **kw):
    return estimate_time(HEAD + body, **{**KW, **kw})


# --- un movimiento: trapecio y triangulo ---------------------------------------------------------------------------
def test_a_long_move_accelerates_cruises_and_brakes():
    # 100 mm a 3600 mm/min (60 mm/s) con 200 mm/s2: 0,3 s acelerando (9 mm), 0,3 s frenando (9 mm) y 82 mm a 60 mm/s
    t = est("G1 X100 F3600\n")
    assert t.seconds == pytest.approx(0.3 + 0.3 + 82 / 60)
    assert t.cut_seconds == pytest.approx(t.seconds) and t.rapid_seconds == 0 and t.plunge_seconds == 0


def test_a_short_move_never_reaches_its_feed():
    # 1 mm: acelera hasta la mitad y frena (triangulo): v max = raiz(a * L) = raiz(200); t = 2 * v / a
    t = est("G1 X1 F3600\n")
    assert t.seconds == pytest.approx(2 * math.sqrt(200) / 200)


def test_a_slow_feed_is_a_plain_cruise_with_short_ramps():
    # 100 mm a 300 mm/min (5 mm/s): rampa de 0,025 s (0,0625 mm) cada lado
    t = est("G1 X100 F300\n")
    assert t.seconds == pytest.approx(0.025 * 2 + (100 - 2 * 0.0625) / 5)


def test_the_feed_is_capped_by_the_machines_maximum():
    capped = est("G1 X100 F99999\n")
    assert capped.seconds == pytest.approx(est("G1 X100 F3600\n").seconds)
    assert est("G1 X100 F99999\n", max_feed=1200.0).seconds == pytest.approx(est("G1 X100 F1200\n").seconds)


def test_rapids_go_at_the_machines_maximum_and_are_counted_apart():
    t = est("G0 X100\n")
    assert t.rapid_seconds == pytest.approx(0.3 + 0.3 + 82 / 60) and t.cut_seconds == 0


def test_z_moves_use_the_z_axis_acceleration_and_are_plunges():
    # 10 mm en Z con 100 mm/s2: no llega a 60 mm/s: v max = raiz(100 * 10); t = 2 * v / a
    t = est("G1 Z-5 F3600\n")                      # de Z5 a Z-5
    assert t.seconds == pytest.approx(2 * math.sqrt(100 * 10) / 100)
    assert t.plunge_seconds == pytest.approx(t.seconds) and t.cut_seconds == 0


def test_a_diagonal_move_is_limited_by_its_most_demanding_axis():
    # X30 Y40 (50 mm, vector 0,6 / 0,8): la aceleracion a lo largo es min(200/0,6; 200/0,8) = 250 mm/s2 y la velocidad
    # maxima, min(60/0,6; 60/0,8) = 75 mm/s: con 600 mm/min (10 mm/s) es un trapecio normal
    t = est("G1 X30 Y40 F600\n")
    a, v = 250.0, 10.0
    assert t.seconds == pytest.approx(2 * v / a + (50 - v * v / a) / v)


# --- uniones: GRBL no frena del todo en una curva --------------------------------------------------------------------
def test_two_collinear_moves_cost_the_same_as_one():
    one = est("G1 X100 F3600\n").seconds
    two = est("G1 X40 F3600\nG1 X100\n").seconds
    assert two == pytest.approx(one)


def test_a_sharp_corner_nearly_stops_the_machine():
    # 90 grados: v2 = a * d * s / (1 - s) con s = raiz(0,5) y la aceleracion a lo largo de la diagonal de la union
    # (el vector (-1, 1) / raiz(2): 200 / 0,7071 = 282,8 mm/s2) -> ~2,61 mm/s en la esquina
    corner = math.sqrt(200 * math.sqrt(2) * 0.010 * math.sqrt(0.5) / (1 - math.sqrt(0.5)))
    t = est("G1 X100 F3600\nG1 Y100\n")
    first = 0.3 + (60 - corner) / 200 + (100 - 9 - (3600 - corner ** 2) / 400) / 60     # parado -> esquina
    assert t.seconds == pytest.approx(2 * first)                                           # y la segunda, al reves


def test_a_gentle_bend_barely_slows_down():
    # 2 grados de giro entre dos tramos de 50 mm: la union deja pasar a mas de 60 mm/s, no frena
    c, s = math.cos(math.radians(2)), math.sin(math.radians(2))
    bent = est(f"G1 X50 F3600\nG1 X{50 + 50 * c:.4f} Y{50 * s:.4f}\n").seconds
    straight = est("G1 X100 F3600\n").seconds
    assert abs(bent - straight) < 0.01          # (algo menos: en la diagonal el eje X aporta algo mas de aceleracion a lo largo)


def test_going_back_over_the_same_line_stops_the_machine_completely():
    back = est("G1 X50 F3600\nG1 X0\n").seconds
    assert back == pytest.approx(2 * est("G1 X50 F3600\n").seconds)


# --- sincronizacion: M3, M5, G4, M0 -----------------------------------------------------------------------------------
@pytest.mark.parametrize("sync", ["M3 S20000", "M5", "G4 P0", "M0"])
def test_the_machine_stops_at_each_sync_so_the_planner_does_not_blend_across_it(sync):
    cut = est("G1 X50 F3600\n" + sync + "\nG1 X100\n").seconds
    assert cut == pytest.approx(2 * est("G1 X50 F3600\n").seconds)


def test_dwells_add_their_seconds_and_pauses_are_counted_but_do_not_add():
    t = est("G4 P2\nG0 Z5\nM0\nG0 Z5\nM0\n")
    assert t.dwell_seconds == 2 and t.pauses == 2
    assert t.seconds == pytest.approx(2.0)          # no hay movimientos: solo la espera; las pausas no suman


def test_comments_and_system_lines_are_ignored():
    plain = est("G1 X100 F3600\n").seconds
    noisy = est("(un comentario G1 X5000)\n$H\n; G1 X999\nG1 X100 F3600 (otro)\n").seconds
    assert noisy == pytest.approx(plain)


def test_words_without_spaces_and_lowercase_also_work():
    assert est("g1x100f3600\n").seconds == pytest.approx(est("G1 X100 F3600\n").seconds)


# --- sin aceleraciones, supuestos y limites -------------------------------------------------------------------------------
def test_without_accelerations_it_is_the_flat_time_and_says_so():
    t = est("G1 X100 F3600\n", accel=None)
    assert t.seconds == pytest.approx(100 / 60) and not t.accelerations
    assert any("aceleraciones" in n for n in t.notes)
    assert est("G1 X100 F3600\n").accelerations


def test_the_flat_time_is_always_a_lower_bound():
    body = "".join(f"G1 X{x} Y{y} F1200\n" for x, y in [(5, 0), (5, 5), (0, 5), (0, 0)] * 3)
    t = est(body)
    assert t.flat_seconds < t.seconds


def test_without_a_maximum_feed_it_assumes_the_usual_one_and_says_so():
    t = estimate_time(HEAD + "G0 X100\n", accel=ACC)
    assert t.seconds == pytest.approx(est("G0 X100\n").seconds) and any("avance maximo" in n for n in t.notes)


def test_a_cut_without_feed_is_not_counted_and_is_reported():
    t = est("G1 X100\n")
    assert t.seconds == 0 and any("sin avance" in n for n in t.notes)


def test_arcs_and_relative_or_inch_modes_are_flagged():
    assert any("arcos" in n for n in est("G2 X10 Y10 I5 J0 F600\n").notes)
    assert any("relativas" in n for n in est("G91\nG1 X10 F600\n").notes)


def test_empty_and_move_free_code_costs_nothing():
    t = estimate_time("", **KW)
    assert t.seconds == 0 and t.blocks == 0
    assert estimate_time("M3 S1000\nM5\nM30\n", **KW).seconds == 0


def test_a_shape_made_of_many_small_chords_flows_at_its_feed():
    # un circulo de 20 mm de radio en cuerdas de 0,2 mm a 1200 mm/min: las uniones son muy suaves y casi no se frena
    n = 628
    pts = [(20 * math.cos(2 * math.pi * i / n), 20 * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]
    t = est("".join(f"G1 X{x:.4f} Y{y:.4f} F1200\n" for x, y in pts[1:]))
    assert t.seconds < t.flat_seconds * 1.03


# --- formato ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("seconds, text", [(0, "0 s"), (45, "45 s"), (59.6, "1 min"), (60, "1 min"), (720, "12 min"),
                                           (3600, "1 h 00 min"), (5700, "1 h 35 min"), (3 * 3600 + 5 * 60, "3 h 05 min")])
def test_durations_read_in_hours_and_minutes(seconds, text):
    assert format_duration(seconds) == text


def test_a_negative_duration_is_a_bug_not_a_text():
    with pytest.raises(ValueError):
        format_duration(-1)


def test_the_description_names_the_parts_and_the_pauses_it_leaves_out():
    t = est("G1 Z-5 F600\nG1 X100 F600\nM0\nG0 X0\nG4 P3\n")
    text = describe(t)
    assert text.startswith(f"unos {t.text} de maquina") and "corte" in text and "bajadas" in text
    assert "rapidos" in text and "esperas 3 s" in text and "sin contar 1 pausa(s) M0" in text


def test_the_description_warns_when_accelerations_are_missing():
    assert "aceleraciones" in describe(est("G1 X100 F600\n", accel=None))


# --- la ficha de la maquina -------------------------------------------------------------------------------------------------
def test_the_machine_card_gives_the_accelerations_and_feeds():
    nc = HEAD + "G1 X100 F3600\n"
    assert estimate_for_machine(nc, GRBL_MAQUINA_REAL).seconds == pytest.approx(est("G1 X100 F3600\n").seconds)
    slow = MachineProfile(name="lenta", max_feed=1800.0, travel=(651, 1011, 100), accel=(100, 100, 50))
    assert estimate_for_machine(nc, slow).seconds > estimate_for_machine(nc, GRBL_MAQUINA_REAL).seconds
    bare = MachineProfile(name="sin datos", max_feed=None, travel=(651, 1011, 100))
    t = estimate_for_machine(nc, bare)
    assert not t.accelerations and any("avance maximo" in n for n in t.notes)


# --- un trabajo de verdad -----------------------------------------------------------------------------------------------------
DATA = Path(__file__).parent / "data"


def test_the_estimate_of_a_real_plan_matches_the_flat_time_of_the_verifier_within_a_few_percent():
    from cnc.verify_nc import analyze
    from test_split_cuts import big_drawing

    plan = big_drawing().plan(None)
    t = plan.time
    assert isinstance(t, TimeEstimate) and plan.time is t                 # se calcula una vez y se guarda
    assert t.seconds == pytest.approx(estimate_for_machine(plan.text, plan.job.machine).seconds)
    cut = analyze(plan.text)
    assert t.flat_seconds <= t.seconds <= t.flat_seconds * 1.15
    assert t.cut_seconds + t.plunge_seconds == pytest.approx(cut.cut_minutes * 60, rel=0.15)
    assert t.pauses == 0 and t.blocks > 1000


def test_a_plan_without_gcode_has_no_time():
    from cnc.pipeline import JobPlan

    assert JobPlan().time is None
