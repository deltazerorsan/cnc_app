import re

import pytest
from shapely.geometry import LinearRing, LineString, box

from cnc.generate import generate_gcode
from cnc.model import ContourOp, Job, Stock, Tool
from cnc.post_grbl import PostOptions
from cnc.toolpath import pass_depths, tool_path_rings


def make_job(**op_kwargs):
    shape = op_kwargs.pop("shape", box(0, 0, 100, 60))
    op = ContourOp(shape=shape, **op_kwargs)
    return Job(
        stock=Stock(thickness=18.0, spoilboard_extra=0.2),
        tool=Tool(diameter=6.0),
        operations=[op],
    )


def body(text):
    return [ln for ln in text.splitlines() if not ln.startswith("(")]


def test_pass_depths_are_equal_and_end_exactly_at_total():
    d = pass_depths(18.2, 2.0)
    assert len(d) == 10
    assert d[-1] == -18.2
    steps = [d[0]] + [d[i] - d[i - 1] for i in range(1, len(d))]
    assert all(abs(s - steps[0]) < 1e-9 for s in steps)
    assert all(abs(s) <= 2.0 + 1e-9 for s in steps)


def test_through_cut_goes_to_thickness_plus_extra():
    r = generate_gcode(make_job())
    assert r.min_z == pytest.approx(-18.2)
    assert "Z min -18.200" in r.text
    zs = [float(z) for z in re.findall(r"Z(-?\d+\.?\d*)", "\n".join(body(r.text)))]
    assert min(zs) == pytest.approx(-18.2)


def test_outside_is_ccw_and_inside_is_cw_with_climb():
    (pts, closed), = tool_path_rings(box(0, 0, 100, 60), "outside", 3.0, climb=True)
    assert closed and LinearRing(pts).is_ccw
    (pts, _), = tool_path_rings(box(0, 0, 100, 60), "inside", 3.0, climb=True)
    assert not LinearRing(pts).is_ccw
    (pts, _), = tool_path_rings(box(0, 0, 100, 60), "outside", 3.0, climb=False)
    assert not LinearRing(pts).is_ccw


def test_offset_bounds():
    (pts, _), = tool_path_rings(box(0, 0, 100, 60), "outside", 3.0)
    xs, ys = zip(*pts)
    assert (min(xs), min(ys), max(xs), max(ys)) == pytest.approx((-3, -3, 103, 63))
    (pts, _), = tool_path_rings(box(0, 0, 100, 60), "inside", 3.0)
    xs, ys = zip(*pts)
    assert (min(xs), min(ys), max(xs), max(ys)) == pytest.approx((3, 3, 97, 57))


def test_gcode_xy_stays_within_expected_bounds():
    r = generate_gcode(make_job())
    lines = body(r.text)
    xs = [float(v) for v in re.findall(r"X(-?\d+\.?\d*)", "\n".join(lines))]
    ys = [float(v) for v in re.findall(r"Y(-?\d+\.?\d*)", "\n".join(lines))]
    assert min(xs) >= -3 - 1e-6 and max(xs) <= 103 + 1e-6
    assert min(ys) >= -3 - 1e-6 and max(ys) <= 63 + 1e-6


def test_extra_above_limit_is_rejected():
    job = make_job()
    job.stock.spoilboard_extra = 2.0  # el limite por defecto es 1.0
    with pytest.raises(ValueError):
        generate_gcode(job)


def test_blind_op_deeper_than_stock_is_rejected():
    with pytest.raises(ValueError):
        generate_gcode(make_job(through=False, depth=20.0))


def test_tool_too_big_for_inside_contour_is_rejected():
    with pytest.raises(ValueError):
        generate_gcode(make_job(shape=box(0, 0, 4, 4), side="inside"))


def test_feed_above_machine_limit_warns():
    r = generate_gcode(make_job(feed_xy=5000.0))
    assert any("feed_xy" in w for w in r.warnings)


def test_spindle_is_on_off_with_fixed_s_by_default():
    lines = body(generate_gcode(make_job()).text)
    assert "M3 S20000" in lines and "G4 P2" in lines and "M5" in lines
    first_cut = next(i for i, ln in enumerate(lines) if ln.startswith("G1"))
    last_cut = max(i for i, ln in enumerate(lines) if ln.startswith("G1"))
    # el husillo arranca con la fresa ya subida (G0 Z5) y antes de cualquier corte
    assert lines.index("G0 Z5") < lines.index("M3 S20000") < first_cut
    assert lines.index("M5") > last_cut


def test_no_spindle_commands_when_disabled():
    r = generate_gcode(make_job(), PostOptions(emit_spindle=False))
    assert not re.search(r"\bM3\b", r.text) and not re.search(r"\bM5\b", r.text)
    assert "G4" not in r.text


def test_spindle_s_is_configurable():
    r = generate_gcode(make_job(), PostOptions(spindle_s=18000.0))
    assert "M3 S18000" in r.text


def test_default_ends_where_it_finishes_like_reference():
    lines = body(generate_gcode(make_job()).text)
    assert lines[-3:] == ["G0 Z5", "M5", "M30"]      # sube Z, para el husillo y se queda donde acaba
    assert "G0 X0 Y0" not in lines and "$H" not in lines


def test_return_to_origin_is_optional():
    lines = body(generate_gcode(make_job(), PostOptions(return_to_origin=True)).text)
    assert lines[-3:] == ["M5", "G0 X0 Y0", "M30"]


def test_pause_at_start_goes_between_lift_and_spindle():
    lines = body(generate_gcode(make_job(), PostOptions(pause_at_start=True)).text)
    assert lines.index("G0 Z5") < lines.index("M0") < lines.index("M3 S20000")
    assert "M0" not in body(generate_gcode(make_job()).text)


def test_home_at_end_replaces_return_to_origin():
    lines = body(generate_gcode(make_job(), PostOptions(home_at_end=True, return_to_origin=True)).text)
    assert lines[-4:] == ["M5", "G4 P0.1", "$H", "M30"]
    assert "G0 X0 Y0" not in lines


def test_end_code_goes_after_home_and_before_program_end():
    text = generate_gcode(
        make_job(),
        PostOptions(home_at_end=True, end_code="\n  G0 X10 Y10  \n(aparcar en posici\u00f3n)\n\nM9\n"),
    ).text
    tail = text.splitlines()[-6:]
    assert tail == [
        "$H",
        "(Codigo final definido por el usuario)",
        "G0 X10 Y10",
        "(aparcar en posicion)",
        "M9",
        "M30",
    ]
    assert all(ord(ch) < 128 for ch in text)


def test_end_code_long_line_warns():
    r = generate_gcode(make_job(), PostOptions(end_code="G1 X1 " + "Y1 " * 30))
    assert any("Codigo final" in w for w in r.warnings)


def test_open_path_retracts_between_passes():
    job = make_job(
        shape=LineString([(0, 0), (50, 0)]),
        side="on",
        through=False,
        depth=4.0,
        stepdown=2.0,
    )
    r = generate_gcode(job)
    assert r.min_z == pytest.approx(-4.0)
    # dos pasadas -> el inicio (X0 Y0) se visita dos veces con la fresa levantada
    assert sum(1 for ln in body(r.text) if ln.startswith("G0 Z1")) >= 2


def test_default_machine_is_the_real_one():
    from cnc.model import GRBL_MAQUINA_REAL

    assert make_job().machine is GRBL_MAQUINA_REAL
    assert GRBL_MAQUINA_REAL.travel == (651.0, 1011.0, 100.0)


def test_result_reports_trajectory_bounds_in_header():
    r = generate_gcode(make_job())
    assert r.bounds == pytest.approx((-3.0, -3.0, 103.0, 63.0))
    assert "(XY de la trayectoria: X[-3.000, 103.000] Y[-3.000, 63.000] mm)" in r.text
