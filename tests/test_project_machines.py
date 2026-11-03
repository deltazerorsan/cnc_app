"""Cada trabajo lleva su maquina: elegirla, guardarla con el trabajo, abrirlo en otro PC y arrancar con maquinas.json."""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from cnc.machines import parse_machine, save_machines
from cnc.model import GRBL_MAQUINA_REAL, Tool
from cnc.project import General, Project, ProjectError, startup_project

DATA = Path(__file__).parent / "data"
BOARDS19 = DATA / "tableros_19mm.svg"
TOOLS = {"em4": Tool(diameter=4, name="End mill 4 mm", feed_xy=300, feed_z=200)}


def card(**kw):
    base = dict(recorrido=[651, 1011, 100], nombre="Taller")
    base.update(kw)
    return parse_machine("x", base)


def project(machines=None, **general):
    p = Project(TOOLS, machines)
    p.general = General(tool="em4", thickness=19.0, margin=0.4, rotate="0", stepdown=5.0, **general)
    return p


# --- elegir la maquina --------------------------------------------------------------------------------------
def test_a_project_without_a_file_has_the_built_in_real_machine():
    p = Project(TOOLS)
    assert list(p.machines) == ["real"] and p.machines["real"] is GRBL_MAQUINA_REAL
    assert p.machine_id() == "real" and p.resolve_machine() is GRBL_MAQUINA_REAL


def test_the_only_card_is_used_without_choosing():
    a = card()
    p = project({"a": a})
    assert p.machine_id() == "a" and p.resolve_machine() is a


def test_a_chosen_machine_is_used():
    a, b = card(), card(recorrido=[500, 700, 60])
    p = project({"a": a, "b": b}, machine="b")
    assert p.machine_id() == "b" and p.job_settings(None).machine is b


def test_several_cards_and_no_choice_is_an_error_not_a_guess():
    p = project({"a": card(), "b": card()})
    with pytest.raises(ProjectError, match=r"Hay varias máquinas \(a, b\)"):
        p.machine_id()
    p.load_svg(BOARDS19)
    plan = p.plan(1)
    assert plan.error and "Hay varias máquinas (a, b)" in plan.error and not plan.writable


def test_a_wrong_machine_is_an_error_with_a_suggestion():
    p = project({"taller": card()}, machine="tallr")
    with pytest.raises(ProjectError, match="¿Quisiste decir 'taller'"):
        p.resolve_machine()


def test_no_cards_is_an_error_that_says_how_to_get_one():
    p = project({})
    with pytest.raises(ProjectError, match="No hay ninguna ficha de máquina"):
        p.resolve_machine()


def test_planning_without_a_machine_is_a_plan_with_error_and_nothing_to_write():
    p = project({}, machine="")
    p.load_svg(BOARDS19)
    plan = p.plan(1)
    assert plan.error and "No hay ninguna ficha de máquina" in plan.error and not plan.writable
    with pytest.raises(ProjectError):
        p.write("/tmp/no_se_escribe.nc", [(1, plan)])


def test_the_job_is_planned_against_the_chosen_machine():
    p = project({"a": card(), "chica": card(recorrido=[300, 400, 100], nombre="Chica")}, machine="chica")
    p.load_svg(BOARDS19)
    plan = p.plan(1)
    assert plan.ok and plan.job.machine.name == "Chica" and not plan.writable
    assert any(b.startswith("No cabe en la maquina") for b in plan.blockers)
    p.general = replace(p.general, machine="a")
    assert p.plan(1).job.machine.name == "Taller"


def test_the_nc_carries_the_machine_header():
    p = project({"a": card()})
    p.load_svg(BOARDS19)
    assert p.plan(1).text.splitlines()[1] == "(Maquina: Taller | recorrido 651 x 1011 x 100 mm)"


def test_the_snapshot_for_the_background_thread_has_its_own_machines():
    p = project({"a": card()})
    q = p.snapshot()
    assert q.machines == p.machines and q.machines is not p.machines and q.resolve_machine() is p.resolve_machine()
    q.machines["b"] = card()
    assert list(p.machines) == ["a"]


def test_the_marks_program_does_not_end_with_home_on_a_machine_without_homing():
    p = project({"a": card(homing=False)}, home_at_end=True)
    p.load_svg(BOARDS19)
    with pytest.raises(ProjectError, match="no tiene homing"):
        p.marks_text(1, [(10.0, 10.0)])
    p.general = replace(p.general, home_at_end=False)
    assert "G0" in p.marks_text(1, [(10.0, 10.0)])


# --- guardar y abrir el trabajo -----------------------------------------------------------------------------
def saved(p, tmp_path):
    path = tmp_path / "t.cncjob"
    p.save(path)
    return json.loads(path.read_text(encoding="utf-8"))


def reopened(data, machines):
    q = project(machines)
    q.load_svg(BOARDS19)
    q.apply_dict(data)
    return q


def test_the_job_file_stores_the_machine_id_and_its_card(tmp_path):
    p = project({"a": card(homing=True)})
    p.load_svg(BOARDS19)
    data = saved(p, tmp_path)
    assert data["general"]["machine"] == "a" and data["maquina"]["id"] == "a"
    assert data["maquina"]["ficha"] == {"nombre": "Taller", "recorrido": [651, 1011, 100], "homing": True}
    assert data["version"] == 1                                              # sigue siendo la misma version del archivo


def test_a_job_without_a_resolvable_machine_stores_none(tmp_path):
    p = project({"a": card(), "b": card()})
    p.load_svg(BOARDS19)
    data = saved(p, tmp_path)
    assert "maquina" not in data and data["general"]["machine"] == ""


def test_reopening_with_the_same_card_is_silent(tmp_path):
    p = project({"a": card(), "b": card(recorrido=[1, 2, 3])}, machine="b")
    p.load_svg(BOARDS19)
    q = reopened(saved(p, tmp_path), p.machines)
    assert q.general.machine == "b" and q.load_notes == []


def test_reopening_after_the_card_changed_says_what_changed_and_uses_the_new_one(tmp_path):
    p = project({"a": card()})
    p.load_svg(BOARDS19)
    data = saved(p, tmp_path)
    q = reopened(data, {"a": card(recorrido=[650, 1017, 100], homing=False)})
    (note,) = q.load_notes
    assert note.level == "warning" and "«a» ha cambiado" in note.text
    assert "recorrido (mm): 651 × 1011 × 100 → 650 × 1017 × 100" in note.text and "homing: sin dato → no" in note.text
    assert note.text.endswith("se usa la de ahora.") and q.resolve_machine().travel == (650.0, 1017.0, 100.0)


def test_reopening_where_the_machine_does_not_exist_uses_the_saved_card(tmp_path):
    p = project({"taller": card(homing=True)})
    p.load_svg(BOARDS19)
    data = saved(p, tmp_path)
    q = reopened(data, {"otra": card(recorrido=[1, 2, 3])})
    (note,) = q.load_notes
    assert note.level == "warning" and "«taller» del trabajo no esta en maquinas.json" in note.text
    assert "recorrido 651 × 1011 × 100 mm" in note.text and "Guardala en Maquinas" in note.text
    assert q.general.machine == "taller" and q.machines["taller"].homing is True and "otra" in q.machines
    assert q.plan(1).job.machine.travel == (651.0, 1011.0, 100.0)


def test_reopening_with_no_machines_at_all_still_works_from_the_saved_card(tmp_path):
    p = project({"taller": card()})
    p.load_svg(BOARDS19)
    q = reopened(saved(p, tmp_path), {})
    assert q.resolve_machine().name == "Taller" and len(q.load_notes) == 1


def test_an_unreadable_saved_card_is_a_warning_and_never_stops_the_job_from_opening(tmp_path):
    p = project({"a": card()})
    p.load_svg(BOARDS19)
    for bad in ({"id": "a", "ficha": {"recorrido": "mucho"}}, {"id": 5, "ficha": {}}, {"id": "1x", "ficha": {}},
                {"id": "a"}, {"ficha": {"recorrido": [1, 2, 3]}}):
        data = saved(p, tmp_path)
        data["maquina"] = bad
        q = reopened(data, p.machines)
        assert [n.level for n in q.load_notes] == ["warning"] and "no se puede leer" in q.load_notes[0].text
        assert q.resolve_machine() is p.machines["a"]
    data = saved(p, tmp_path)
    data["maquina"] = "basura"
    assert reopened(data, p.machines).load_notes == []


def test_an_old_job_without_machine_keeps_working_with_the_default_one(tmp_path):
    p = project({"a": card()})
    p.load_svg(BOARDS19)
    data = saved(p, tmp_path)
    del data["maquina"]
    del data["general"]["machine"]
    q = reopened(data, p.machines)
    assert q.general.machine == "" and q.load_notes == [] and q.resolve_machine() is p.machines["a"]


def test_the_job_machine_wins_over_a_card_copy_of_another_machine(tmp_path):
    p = project({"a": card(), "b": card(recorrido=[1, 2, 3])}, machine="a")
    p.load_svg(BOARDS19)
    data = saved(p, tmp_path)
    data["general"]["machine"] = "b"
    q = reopened(data, p.machines)
    assert q.general.machine == "b" and q.load_notes == []


def test_the_chosen_machine_survives_a_round_trip_through_the_job_dict(tmp_path):
    p = project({"a": card(), "b": card(recorrido=[1, 2, 3])}, machine="b")
    p.load_svg(BOARDS19)
    q = reopened(saved(p, tmp_path), p.machines)
    assert q.general == p.general and q.plan(1).job.machine is p.machines["b"]


# --- arranque ------------------------------------------------------------------------------------------------
def write_machines(folder, **cards):
    save_machines({k: parse_machine(k, v) for k, v in cards.items()}, folder / "maquinas.json")


def test_startup_reads_maquinas_json_from_the_folders_and_the_current_one_wins(tmp_path):
    app, cwd = tmp_path / "app", tmp_path / "cwd"
    app.mkdir(), cwd.mkdir()
    write_machines(app, taller={"recorrido": [651, 1011, 100]})
    project_, notes = startup_project(cwd, app)
    assert [n for n in notes if n.code == "machines"] == [] and list(project_.machines) == ["taller"]
    assert project_.machines_path == app / "maquinas.json"
    write_machines(cwd, otra={"recorrido": [1, 2, 3]})
    assert list(startup_project(cwd, app)[0].machines) == ["otra"]


def test_startup_without_a_file_has_no_machines_and_an_error_that_says_how_to_create_one(tmp_path):
    project_, notes = startup_project(tmp_path, tmp_path)
    (note,) = [n for n in notes if n.code == "machines"]
    assert note.level == "error" and "No encuentro maquinas.json" in note.text and "Nueva desde un log" in note.text
    assert project_.machines == {} and project_.machines_path is None
    with pytest.raises(ProjectError):
        project_.resolve_machine()


def test_startup_with_a_broken_file_has_no_machines_and_the_file_error(tmp_path):
    (tmp_path / "maquinas.json").write_text('{"maquinas": {"a": {"recorrido": [1, 2]}}}', encoding="utf-8")
    project_, notes = startup_project(tmp_path, tmp_path)
    (note,) = [n for n in notes if n.code == "machines"]
    assert note.level == "error" and "lista de tres números" in note.text
    assert project_.machines == {} and project_.machines_path == tmp_path / "maquinas.json"


def test_startup_takes_machine_and_machines_file_from_the_config(tmp_path):
    other = tmp_path / "otro"
    other.mkdir()
    write_machines(other, a={"recorrido": [1, 2, 3]}, b={"recorrido": [4, 5, 6]})
    (tmp_path / "cnc_app.toml").write_text(f'machine = "b"\nmachines_file = "{(other / "maquinas.json").as_posix()}"\n',
                                           encoding="utf-8")
    project_, notes = startup_project(tmp_path, tmp_path)
    assert [n for n in notes if n.code == "machines"] == []
    assert project_.general.machine == "b" and project_.resolve_machine().travel == (4.0, 5.0, 6.0)


def test_general_reads_the_machine_key_as_text():
    assert General.from_config({"machine": "taller"}).machine == "taller"
    assert General.from_config({"machine": 5}).machine == "5"
    assert General().machine == ""
