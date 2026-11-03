"""Sin tablero (sin recuadro) el cero se estima a unos mm por fuera de la esquina inferior izquierda de lo que se corta."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import re
import sys
from pathlib import Path

import pytest
from gcodecmp import assert_same_gcode
from shapely.geometry import box

from cnc.model import Tool
from cnc.pipeline import JobSettings, plan_job
from cnc.project import General, Project, ProjectError
from cnc.recipe import parse_recipe
from cnc.svg_import import ImportedShape

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import svg_a_gcode  # noqa: E402

DATA = Path(__file__).parent / "data"
BOARDS19 = DATA / "tableros_19mm.svg"
TOOL = Tool(diameter=4)
SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="60mm" viewBox="0 0 100 60">'
       '<rect x="10" y="10" width="40" height="30" stroke="#ff0000" fill="none"/>'
       '<circle cx="30" cy="25" r="5" stroke="#ff0000" fill="none"/></svg>')


def shp(geom, stroke="#ff0000"):
    return ImportedShape(shape=geom, closed=True, stroke=stroke, fill=None)


def settings(**kw):
    base = dict(tool=TOOL, thickness=6.0, margin=0.4, feed=300.0, plunge=200.0)
    base.update(kw)
    return JobSettings(**base)


def pieces():
    return [shp(box(10, 10, 110, 70)), shp(box(30, 30, 50, 50))]


def lower_left(plan):
    return (min(s.shape.bounds[0] for s in plan.shapes), min(s.shape.bounds[1] for s in plan.shapes))


def texts(plan):
    return [m.text for m in plan.messages]


NOTE = "Sin tablero: el cero queda 4 mm por fuera de la esquina inferior izquierda de lo que se corta"


# --- el plan -------------------------------------------------------------------------------------------------
def test_without_margin_the_corner_of_the_drawing_is_the_zero_as_always():
    plan = plan_job(pieces(), [], settings())
    assert lower_left(plan) == pytest.approx((0, 0)) and not any(t.startswith("Sin tablero") for t in texts(plan))


def test_the_zero_is_margin_mm_outside_the_lower_left_corner_on_both_axes():
    plan = plan_job(pieces(), [], settings(origin_margin=4.0))
    assert lower_left(plan) == pytest.approx((4, 4)) and NOTE in texts(plan)
    assert plan.result.bounds[:2] == pytest.approx((2, 2), abs=0.01)             # y la fresa (radio 2) no se sale del cero
    plan = plan_job(pieces(), [], settings(origin_margin=6.5))
    assert lower_left(plan) == pytest.approx((6.5, 6.5)) and "Sin tablero: el cero queda 6.5 mm por fuera" in " ".join(texts(plan))


def test_the_margin_adds_to_a_manual_offset():
    plan = plan_job(pieces(), [], settings(origin_margin=4.0, offset=(5, 7)))
    assert lower_left(plan) == pytest.approx((9, 11))


def test_the_corner_is_estimated_from_what_is_cut_not_from_what_the_recipe_ignores():
    recipe = parse_recipe({"ignorar": ["#00ff00"], "operacion": [{"tipo": "corte", "trazo": "#ff0000"}]})
    shapes = [shp(box(-30, -30, -10, -10), "#00ff00"), shp(box(50, 50, 80, 70))]
    plan = plan_job(shapes, [], settings(origin_margin=4.0), recipe)
    assert plan.ok and lower_left(plan) == pytest.approx((4, 4))


@pytest.mark.parametrize("origin", ["center", "page_bottom_left"])
def test_other_origins_ignore_the_margin(origin):
    a = plan_job(pieces(), [], settings(origin=origin, origin_margin=4.0))
    b = plan_job(pieces(), [], settings(origin=origin))
    assert lower_left(a) == pytest.approx(lower_left(b)) and not any(t.startswith("Sin tablero") for t in texts(a))


def test_with_a_board_the_zero_is_the_boards_corner_and_the_margin_does_not_apply():
    board = shp(box(0, 0, 300, 200), "#000000")
    inside = [shp(box(20, 30, 120, 90))]
    a = plan_job(inside, [board], settings(origin_margin=4.0))
    b = plan_job(inside, [board], settings())
    assert lower_left(a) == pytest.approx((20, 30)) and not any(t.startswith("Sin tablero") for t in texts(a))
    assert_same_gcode(a.text, b.text, "con tablero el margen no cuenta")


# --- el proyecto ------------------------------------------------------------------------------------------------
def tools():
    return {"em4": Tool(diameter=4, name="End mill 4 mm", feed_xy=300, feed_z=200)}


def project(tmp_path, **general):
    svg = tmp_path / "d.svg"
    svg.write_text(SVG)
    p = Project(tools())
    p.use_boards = False
    p.load_svg(svg)
    p.general = General(tool="em4", thickness=6.0, **general)
    return p


def test_the_default_is_4_mm_and_the_project_plan_uses_it(tmp_path):
    assert General().origin_margin == 4.0
    p = project(tmp_path)
    plan = p.plan(None)
    assert plan.ok and lower_left(plan) == pytest.approx((4, 4)) and NOTE in texts(plan)
    zero = project(tmp_path, origin_margin=0.0).plan(None)
    assert lower_left(zero) == pytest.approx((0, 0))


def test_a_negative_or_missing_margin_is_a_plan_error_that_says_what_is_wrong(tmp_path):
    for bad in (-1.0, float("nan")):
        plan = project(tmp_path, origin_margin=bad).plan(None)
        assert not plan.ok and "el margen del cero no puede ser negativo" in plan.error


def test_the_margin_is_saved_with_the_job_and_an_old_job_gets_the_default(tmp_path):
    p = project(tmp_path, origin_margin=2.5)
    data = p.to_dict()
    assert data["general"]["origin_margin"] == 2.5
    q = project(tmp_path)
    q.apply_dict(data)
    assert q.general.origin_margin == 2.5
    del data["general"]["origin_margin"]
    q.apply_dict(data)
    assert q.general.origin_margin == 4.0


def test_the_config_file_can_set_it():
    assert General.from_config({"origin_margin": 1.5}).origin_margin == 1.5
    assert General.from_config({"origin-margin": 0}).origin_margin == 0


def test_a_drawing_with_boards_ignores_the_margin_in_the_project(tmp_path):
    a, b = Project(tools()), Project(tools())
    for p, m in ((a, 4.0), (b, 0.0)):
        p.load_svg(BOARDS19)
        p.general = General(tool="em4", thickness=19.0, rotate="0", origin_margin=m)
    assert_same_gcode(a.plan(1).text, b.plan(1).text, "tablero 1 igual con y sin margen")


# --- la linea de comandos --------------------------------------------------------------------------------------
def run_cli(tmp_path, *extra):
    svg = tmp_path / "d.svg"
    svg.write_text(SVG)
    out = tmp_path / "o.nc"
    argv = [str(svg), "--no-config", "--no-boards", "--tool", "4", "--thickness", "6", "--feed", "300", "--plunge", "200",
            "-o", str(out), *extra]
    return svg_a_gcode.main(argv), out


def min_xy(path):
    xs, ys = [], []
    for line in path.read_text().splitlines():
        if line.startswith("("):
            continue
        xs += [float(v) for v in re.findall(r"X(-?\d+\.?\d*)", line)]
        ys += [float(v) for v in re.findall(r"Y(-?\d+\.?\d*)", line)]
    return min(xs), min(ys)


def test_the_command_line_has_the_same_default_and_the_option(tmp_path, capsys):
    code, out = run_cli(tmp_path)
    assert code == 0 and min_xy(out) == pytest.approx((2, 2), abs=0.01)               # 4 mm de margen - radio 2
    assert "el cero queda 4 mm por fuera" in capsys.readouterr().out
    code, out = run_cli(tmp_path, "--origin-margin", "0")
    assert code == 0 and min_xy(out) == pytest.approx((-2, -2), abs=0.01)
    code, out = run_cli(tmp_path, "--origin-margin", "7")
    assert code == 0 and min_xy(out) == pytest.approx((5, 5), abs=0.01)


def test_the_command_line_refuses_a_negative_margin(tmp_path, capsys):
    with pytest.raises(SystemExit):
        run_cli(tmp_path, "--origin-margin", "-1")
    assert "--origin-margin no puede ser negativo" in capsys.readouterr().err
    assert not (tmp_path / "o.nc").exists()


# --- la ventana ---------------------------------------------------------------------------------------------------
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from cnc.gui.main_window import MainWindow  # noqa: E402


@pytest.fixture
def win(qtbot):
    w = MainWindow(Project(tools()), [], use_settings=False)
    qtbot.addWidget(w)
    w.show()
    return w


def set_text(edit, text):
    edit.setText(text)
    edit.valueChanged.emit()


def test_the_panel_shows_the_margin_and_editing_it_moves_the_drawing_in_the_plan(win, tmp_path):
    svg = tmp_path / "d.svg"
    svg.write_text(SVG)
    win.detect_boards.setChecked(False)
    assert win.open_svg(svg, apply_default_recipe=False)
    win.job.tool.setCurrentIndex(win.job.tool.findData("em4"))
    win.job.thickness.setText("6")
    win.job.thickness.valueChanged.emit()
    jp = win.job
    assert jp.origin_margin.text() == "4" and jp.origin_margin.isEnabled() and jp.origin_margin_label.isEnabled()
    win.plan_now()
    assert lower_left(win.last_plan) == pytest.approx((4, 4))
    set_text(jp.origin_margin, "7,5")
    assert win.project.general.origin_margin == 7.5
    win.plan_now()
    assert lower_left(win.last_plan) == pytest.approx((7.5, 7.5))
    set_text(jp.origin_margin, "")                                             # vacio: sin margen
    assert win.project.general.origin_margin == 0.0
    win.plan_now()
    assert lower_left(win.last_plan) == pytest.approx((0, 0))


def test_the_field_is_only_available_when_the_drawing_has_no_boards(win):
    assert win.open_svg(BOARDS19, apply_default_recipe=False)
    assert win.job.origin_margin.text() == "4"
    assert not win.job.origin_margin.isEnabled() and not win.job.origin_margin_label.isEnabled()
    assert win.job.origin_margin.toolTip().startswith("Solo si el dibujo no tiene tablero")
    win.detect_boards.setChecked(False)
    assert win.job.origin_margin.isEnabled() and win.job.origin_margin_label.isEnabled()


def test_a_saved_job_brings_the_margin_back(win, tmp_path, qtbot):
    svg = tmp_path / "d.svg"
    svg.write_text(SVG)
    win.detect_boards.setChecked(False)
    assert win.open_svg(svg, apply_default_recipe=False)
    set_text(win.job.origin_margin, "9")
    job = tmp_path / "t.cncjob"
    assert win.save_job(job)
    other = MainWindow(Project(tools()), [], use_settings=False)
    qtbot.addWidget(other)
    assert other.open_job(job)
    assert other.job.origin_margin.text() == "9" and other.project.general.origin_margin == 9.0
