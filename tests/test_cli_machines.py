"""Linea de comandos: elegir la maquina, --list-machines, --import-machine y las claves del archivo de configuracion."""
import json
from pathlib import Path

import pytest

import svg_a_gcode
from cnc import pipeline
from cnc.machines import load_machines, parse_machine, save_machines
from verificar_nc import analyze

ROOT = Path(__file__).resolve().parent.parent

SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="60mm" height="40mm" viewBox="0 0 60 40">
<rect x="5" y="5" width="50" height="30" fill="none" stroke="#000000"/>
<circle cx="30" cy="20" r="6" fill="none" stroke="#000000"/>
</svg>"""

TOOLS = '{"herramientas": {"em4": {"diametro": 4, "feed": 300, "entrada": 200}}}'

LOG = """\
Grbl 1.1h ['$' for help]
>>> $$
$20=0
$21=0
$22=0
$30=1000
$32=0
$110=3600.000
$111=3000.000
$112=1500.000
$120=200.000
$121=200.000
$122=100.000
$130=650.000
$131=1017.000
$132=100.000
ok
"""


@pytest.fixture
def work(tmp_path, monkeypatch):
    """Una carpeta de trabajo que tambien hace de carpeta del programa: nada de lo que se busque o se escriba toca el
    maquinas.json que se entrega."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cnc.pipeline.app_dir", lambda: tmp_path)
    (tmp_path / "dib.svg").write_text(SVG)
    (tmp_path / "herramientas.json").write_text(TOOLS, encoding="utf-8")
    (tmp_path / "log.txt").write_text(LOG, encoding="utf-8")
    save_machines({"taller": parse_machine("taller", {"nombre": "Taller", "recorrido": [651, 1011, 100], "avance_max": 3600,
                                                      "homing": True}),
                   "emu": parse_machine("emu", {"nombre": "Emulador", "recorrido": [650, 1017, 100], "homing": False})},
                  tmp_path / "maquinas.json")
    return tmp_path


def main(work, *args, capsys, machines=True):
    argv = list(args)
    if machines:
        argv += ["--machines-file", str(work / "maquinas.json")]
    try:
        code = svg_a_gcode.main(argv)
    except SystemExit as e:
        code = e.code
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def job(work, *args, capsys, machines=True):
    out = work / "out.nc"
    if out.exists():
        out.unlink()
    code, so, se = main(work, str(work / "dib.svg"), "--tools-file", str(work / "herramientas.json"), "--tool", "em4",
                        "--thickness", "6", "--no-boards", "--no-config", "-o", str(out), *args, capsys=capsys,
                        machines=machines)
    return code, so, se, (out.read_text() if out.exists() else None)


# --- elegir la maquina ---------------------------------------------------------------------------------------
def test_the_job_needs_a_machine_when_there_are_several(work, capsys):
    code, out, err, nc = job(work, capsys=capsys)
    assert code == 2 and nc is None
    assert "Hay varias máquinas (taller, emu) y no se ha elegido ninguna. Usa --machine FICHA." in err


def test_the_chosen_machine_goes_to_the_header_the_summary_and_the_checklist(work, capsys, checklist_on):
    code, out, err, nc = job(work, "--machine", "taller", capsys=capsys)
    assert code == 0, err
    assert "Maquina: taller (Taller), recorrido 651 × 1011 × 100 mm" in out
    assert nc.splitlines()[1] == "(Maquina: Taller | recorrido 651 x 1011 x 100 mm)"
    assert "  - La maquina conectada a UGS es «Taller» (recorrido 651 x 1011 x 100 mm): este G-code es para ella." in out
    assert analyze(nc).issues == []


def test_the_only_card_is_used_when_there_is_just_one(work, capsys):
    save_machines({"solo": parse_machine("solo", {"recorrido": [700, 800, 100]})}, work / "maquinas.json")
    code, out, err, nc = job(work, capsys=capsys)
    assert code == 0 and "Maquina: solo, recorrido 700 × 800 × 100 mm" in out
    assert nc.splitlines()[1] == "(Maquina: solo | recorrido 700 x 800 x 100 mm)"


def test_the_trajectory_is_checked_against_the_chosen_machine(work, capsys):
    save_machines({"chica": parse_machine("chica", {"recorrido": [40, 1000, 100]})}, work / "maquinas.json")
    code, out, err, nc = job(work, capsys=capsys)
    assert code == 1 and nc is None and "No cabe en la maquina" in err and "recorrido es de 40 mm" in err


def test_a_machine_without_homing_refuses_home_at_the_end(work, capsys):
    code, out, err, nc = job(work, "--machine", "emu", "--home-at-end", capsys=capsys)
    assert code == 1 and nc is None and "no tiene homing ($22=0)" in err and "No se escribe" in err
    code, out, err, nc = job(work, "--machine", "taller", "--home-at-end", capsys=capsys)
    assert code == 0 and "G28" not in (nc or "") and "$H" in nc


def test_an_unknown_machine_says_the_known_ones(work, capsys):
    code, out, err, nc = job(work, "--machine", "tallr", capsys=capsys)
    assert code == 2 and "No hay ninguna máquina 'tallr'. Máquinas: taller, emu." in err and "¿Quisiste decir 'taller'?" in err


def test_without_a_machines_file_nothing_is_generated_and_it_says_how_to_create_one(work, capsys):
    (work / "maquinas.json").unlink()
    code, out, err, nc = job(work, capsys=capsys, machines=False)
    assert code == 2 and nc is None and "no encuentro maquinas.json" in err and "--import-machine LOG --machine NOMBRE" in err
    code, out, err, nc = job(work, capsys=capsys, machines=True)             # indicado a mano pero no existe
    assert code == 2 and nc is None and "No existe el archivo de máquinas" in err


def test_a_broken_machines_file_is_an_error_naming_the_card_and_the_field(work, capsys):
    (work / "maquinas.json").write_text('{"maquinas": {"a": {"recorrido": [1, 0, 3]}}}', encoding="utf-8")
    code, out, err, nc = job(work, capsys=capsys)
    assert code == 2 and nc is None and "ficha 'a'" in err and "mayor que 0" in err


def test_the_machine_and_the_file_can_come_from_the_config(work, capsys, tmp_path):
    cfg = work / "c.toml"
    cfg.write_text(f'tool = "em4"\nthickness = 6\nmachine = "emu"\nmachines_file = "{(work / "maquinas.json").as_posix()}"\n',
                   encoding="utf-8")
    out = work / "out.nc"
    code, so, se = main(work, str(work / "dib.svg"), "--tools-file", str(work / "herramientas.json"), "--no-boards",
                        "--config", str(cfg), "-o", str(out), capsys=capsys, machines=False)
    assert code == 0, se
    assert "Maquina: emu (Emulador), recorrido 650 × 1017 × 100 mm" in so
    assert out.read_text().splitlines()[1] == "(Maquina: Emulador | recorrido 650 x 1017 x 100 mm)"
    cfg.write_text('machine = 5\n', encoding="utf-8")
    code, so, se = main(work, str(work / "dib.svg"), "--config", str(cfg), capsys=capsys, machines=False)
    assert code == 2 and "'machine' debe ser texto" in se


def test_the_command_line_machine_wins_over_the_config(work, capsys):
    cfg = work / "c.toml"
    cfg.write_text('machine = "emu"\n', encoding="utf-8")
    out = work / "out.nc"
    code, so, se = main(work, str(work / "dib.svg"), "--tools-file", str(work / "herramientas.json"), "--tool", "em4",
                        "--thickness", "6", "--no-boards", "--config", str(cfg), "--machine", "taller", "-o", str(out),
                        capsys=capsys)
    assert code == 0 and "Maquina: taller (Taller)" in so


def test_the_shipped_machines_file_makes_the_default_job_work(tmp_path, capsys, monkeypatch):
    """Sin --machines-file se busca junto al programa: el maquinas.json que se entrega trae el emulador."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cnc.pipeline.app_dir", lambda: ROOT)
    monkeypatch.setattr(svg_a_gcode, "find_machines_file", pipeline.find_machines_file)      # el de verdad, no el de pruebas
    (tmp_path / "dib.svg").write_text(SVG)
    out = tmp_path / "out.nc"
    code = svg_a_gcode.main([str(tmp_path / "dib.svg"), "--tool", "4", "--thickness", "6", "--feed", "300", "--plunge", "200",
                             "--no-boards", "--no-config", "-o", str(out)])
    so = capsys.readouterr().out
    assert code == 0 and "Maquina: emulador (Emulador GRBL (ESP32)), recorrido 700 × 1017 × 100 mm" in so


# --- --list-machines ------------------------------------------------------------------------------------------
def test_list_machines_shows_the_table(work, capsys):
    code, out, err = main(work, "--list-machines", capsys=capsys)
    assert code == 0 and err == ""
    assert out.startswith("Maquinas de ") and "taller" in out and "651 x 1011 x 100" in out and "650 x 1017 x 100" in out
    assert "sin dato" in out


def test_list_machines_without_a_file_or_with_a_broken_one_is_an_error(work, capsys):
    (work / "maquinas.json").write_text("{", encoding="utf-8")
    code, out, err = main(work, "--list-machines", capsys=capsys)
    assert code == 2 and "JSON inválido" in err
    (work / "maquinas.json").unlink()
    code, out, err = main(work, "--list-machines", capsys=capsys, machines=False)
    assert code == 2 and "no encuentro maquinas.json" in err
    code, out, err = main(work, "--list-machines", capsys=capsys)
    assert code == 2 and "No existe el archivo de máquinas" in err


# --- --import-machine -------------------------------------------------------------------------------------------
def test_import_creates_a_new_card_and_shows_what_it_understood(work, capsys):
    code, out, err = main(work, "--import-machine", str(work / "log.txt"), "--machine", "nueva", capsys=capsys)
    assert code == 0 and err == ""
    assert "Recorrido: 650 × 1017 × 100 mm" in out and "Firmware: Grbl 1.1h" in out and "Homing: no" in out
    assert "Ficha «nueva» nueva: 650 × 1017 × 100 mm de recorrido." in out and "Guardado en" in out
    machines = load_machines(work / "maquinas.json")
    assert list(machines) == ["taller", "emu", "nueva"]
    m = machines["nueva"]
    assert (m.travel, m.max_feed, m.max_feed_z, m.homing, m.firmware, m.source) == (
        (650.0, 1017.0, 100.0), 3000.0, 1500.0, False, "Grbl 1.1h", "log log.txt")
    assert (work / "maquinas.json.bak").is_file()


def test_import_creates_the_file_when_there_is_none(work, capsys):
    (work / "maquinas.json").unlink()
    code, out, err = main(work, "--import-machine", "log.txt", "--machine", "taller", capsys=capsys, machines=False)
    assert code == 0, err
    assert list(load_machines(work / "maquinas.json")) == ["taller"] and not (work / "maquinas.json.bak").exists()


def test_import_needs_a_name_for_the_card(work, capsys):
    code, out, err = main(work, "--import-machine", str(work / "log.txt"), capsys=capsys)
    assert code == 2 and "falta el nombre de la ficha" in err
    assert list(load_machines(work / "maquinas.json")) == ["taller", "emu"]


def test_import_never_touches_an_existing_card_without_update(work, capsys):
    before = (work / "maquinas.json").read_text(encoding="utf-8")
    code, out, err = main(work, "--import-machine", str(work / "log.txt"), "--machine", "taller", capsys=capsys)
    assert code == 1
    assert "La ficha «taller» ya existe y no se toca. Cambiaria:" in out
    assert "recorrido (mm): 651 × 1011 × 100 → 650 × 1017 × 100" in out and "homing: sí → no" in out
    assert "repite el comando con --update-machine" in out
    assert (work / "maquinas.json").read_text(encoding="utf-8") == before and not (work / "maquinas.json.bak").exists()


def test_import_of_an_existing_card_that_would_not_change_is_not_an_error(work, capsys):
    log = work / "igual.txt"
    log.write_text("$130=651\n$131=1011\n$132=100\n$22=1\n$110=3600", encoding="utf-8")
    code, out, err = main(work, "--import-machine", str(log), "--machine", "taller", capsys=capsys)
    assert code == 0 and "El log no cambia ningun dato" in out


def test_update_changes_only_what_the_log_brings_and_keeps_a_backup(work, capsys):
    before = (work / "maquinas.json").read_text(encoding="utf-8")
    code, out, err = main(work, "--import-machine", str(work / "log.txt"), "--machine", "taller", "--update-machine",
                          capsys=capsys)
    assert code == 0 and "Ficha «taller» actualizada." in out and "homing: sí → no" in out
    m = load_machines(work / "maquinas.json")["taller"]
    assert m.travel == (650.0, 1017.0, 100.0) and m.homing is False and m.max_feed == 3000.0 and m.name == "Taller"
    assert (work / "maquinas.json.bak").read_text(encoding="utf-8") == before
    assert load_machines(work / "maquinas.json")["emu"].travel == (650.0, 1017.0, 100.0)       # la otra ficha, intacta


def test_a_log_without_the_travel_is_refused_and_says_what_is_missing(work, capsys):
    log = work / "malo.txt"
    log.write_text("$130=651\n$131=1011\n$22=1", encoding="utf-8")
    code, out, err = main(work, "--import-machine", str(log), "--machine", "x", capsys=capsys)
    assert code == 1 and "Falta $132 (recorrido Z)" in err
    assert list(load_machines(work / "maquinas.json")) == ["taller", "emu"]
    log.write_text("esto no es un log", encoding="utf-8")
    code, out, err = main(work, "--import-machine", str(log), "--machine", "x", capsys=capsys)
    assert code == 1 and "No se ha encontrado ningún ajuste" in err


def test_an_unreadable_log_is_a_usage_error(work, capsys):
    code, out, err = main(work, "--import-machine", str(work / "no_existe.txt"), "--machine", "x", capsys=capsys)
    assert code == 2 and "no puedo leer el log" in err


def test_import_refuses_to_overwrite_a_broken_machines_file(work, capsys):
    (work / "maquinas.json").write_text('{"maquinas": {"a": {"recorrido": [1, 2]}}}', encoding="utf-8")
    code, out, err = main(work, "--import-machine", str(work / "log.txt"), "--machine", "x", capsys=capsys)
    assert code == 1 and "lista de tres números" in err
    assert "recorrido\": [1, 2]" in (work / "maquinas.json").read_text(encoding="utf-8")


def test_import_that_cannot_write_says_so(work, capsys, monkeypatch):
    from cnc.machinebook import MachineEditor

    def boom(self, path):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(MachineEditor, "save", boom)
    code, out, err = main(work, "--import-machine", str(work / "log.txt"), "--machine", "nueva", capsys=capsys)
    assert code == 1 and "no se pudo guardar" in err and "Permission denied" in err


def test_import_and_list_are_not_config_keys(work, capsys):
    cfg = work / "c.toml"
    for key in ("list_machines", "import_machine", "update_machine"):
        cfg.write_text(f"{key} = true\n", encoding="utf-8")
        code, so, se = main(work, str(work / "dib.svg"), "--config", str(cfg), capsys=capsys, machines=False)
        assert code == 2 and f"opcion desconocida '{key}'" in se


# --- verificar_nc con --machine -----------------------------------------------------------------------------
def test_the_verifier_can_take_the_travel_from_a_card(work, capsys):
    from cnc import verify_nc
    nc = work / "x.nc"
    nc.write_text("G21 G90 G17 G94 G54\nG0 Z5\nM3 S20000\nG0 X0 Y0\nG1 Z-1 F200\nG1 X700 Y0 F300\nG0 Z5\nM5\nM30\n")
    code = verify_nc.main([str(nc), "--machine", "taller", "--machines-file", str(work / "maquinas.json")])
    out = capsys.readouterr().out
    assert code == 1 and "recorrido" in out.lower()
    code = verify_nc.main([str(nc), "--travel", "800", "800", "100"])
    assert code == 0 or "recorrido" not in capsys.readouterr().out.lower()
    with pytest.raises(SystemExit):
        verify_nc.main([str(nc), "--machine", "nada", "--machines-file", str(work / "maquinas.json")])
    assert "No hay ninguna máquina 'nada'" in capsys.readouterr().err
