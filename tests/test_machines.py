"""Las fichas de maquina: el archivo maquinas.json, el log de arranque de GRBL y el editor de fichas (sin Qt)."""
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

import svg_a_gcode
from cnc.machinebook import (MachineEditError, MachineEditor, advice, build_machine, form_to_raw, machine_to_form)
from cnc.machines import (DEFAULT_MACHINE_ID, DEFAULT_MACHINES_FILE, MachinesFileError, choose_machine, differences,
                          get_machine, load_machines, machine_from_log, machine_to_dict, n, parse_log,
                          parse_machine, parse_machines, save_machines, travel_text, understood)
from cnc.model import GRBL_MAQUINA_REAL, GRBL_MKS_DLC32, MachineProfile

ROOT = Path(__file__).resolve().parent.parent

# lo que UGS escribe al conectar y al contestar a $$ en una maquina real (con los comentarios de la pantalla)
UGS_LOG = """\
Grbl 1.1h ['$' for help]
[MSG:'$H'|'$X' to unlock]
>>> $$
$0=10
$1=25
$2=0
$20=1
$21=1
$22=1
$30=10000
$32=0
$100=250.000
$110=3600.000
$111=3600.000
$112=3600.000
$120=200.000
$121=200.000
$122=100.000
$130=651.000
$131=1011.000
$132=100.000
ok
>>> $I
[VER:1.1h.20190825:]
[OPT:V,15,128]
ok
"""

MIN = {"recorrido": [100, 200, 30]}


def machines_json(**cards):
    return json.dumps({"maquinas": cards})


# --- el archivo ------------------------------------------------------------------------------------------------
def test_the_shipped_file_has_exactly_the_emulator():
    machines = load_machines(ROOT / DEFAULT_MACHINES_FILE)
    assert list(machines) == ["emulador"]
    m = machines["emulador"]
    assert m.name == "Emulador GRBL (ESP32)" and m.travel == (700.0, 1017.0, 100.0)
    assert (m.max_feed, m.max_feed_z, m.homing, m.soft_limits, m.hard_limits, m.laser_mode) == (
        3600.0, 3600.0, False, False, False, False)


def test_a_minimal_card_only_needs_the_travel():
    m = parse_machine("x", MIN)
    assert m == MachineProfile(name="x", max_feed=None, travel=(100.0, 200.0, 30.0))


def test_a_full_card_is_read_field_by_field():
    m = parse_machine("x", {"nombre": "Taller", "recorrido": [651, 1011, 100], "avance_max": 3600, "avance_max_z": 1800,
                            "aceleracion": [200, 200, 100], "limites_blandos": True, "limites_duros": False,
                            "homing": True, "husillo_max": 10000, "laser": False, "firmware": "Grbl 1.1h",
                            "origen": "a mano", "notas": "la del taller"})
    assert m == MachineProfile(name="Taller", max_feed=3600.0, travel=(651.0, 1011.0, 100.0), max_feed_z=1800.0,
                               accel=(200.0, 200.0, 100.0), soft_limits=True, hard_limits=False, homing=True,
                               spindle_max=10000.0, laser_mode=False, firmware="Grbl 1.1h", source="a mano",
                               notes="la del taller")


def test_without_name_the_id_is_the_name():
    assert parse_machine("taller", MIN).name == "taller"


@pytest.mark.parametrize("raw, text", [
    ({}, "falta el campo obligatorio 'recorrido'"),
    ({"recorrido": [1, 2]}, "lista de tres números"),
    ({"recorrido": [1, 2, 3, 4]}, "lista de tres números"),
    ({"recorrido": "100x200x30"}, "lista de tres números"),
    ({"recorrido": [100, 0, 30]}, "mayor que 0"),
    ({"recorrido": [100, -5, 30]}, "mayor que 0"),
    ({"recorrido": [100, "200", 30]}, "mayor que 0"),
    ({"recorrido": [100, True, 30]}, "mayor que 0"),
    ({"recorrido": [100, float("inf"), 30]}, "mayor que 0"),
    ({**MIN, "avance_max": 0}, "'avance_max' debe ser un número mayor que 0"),
    ({**MIN, "avance_max_z": "rapido"}, "'avance_max_z'"),
    ({**MIN, "husillo_max": -1}, "'husillo_max'"),
    ({**MIN, "aceleracion": [1, 2]}, "'aceleracion'"),
    ({**MIN, "homing": "si"}, "'homing' debe ser true o false"),
    ({**MIN, "laser": 0}, "'laser' debe ser true o false"),
    ({**MIN, "limites_blandos": None}, "'limites_blandos' debe ser true o false"),
    ({**MIN, "nombre": 5}, "'nombre' debe ser texto"),
    ({**MIN, "notas": ["a"]}, "'notas' debe ser texto"),
    ({**MIN, "firmware": 1.1}, "'firmware' debe ser texto"),
    ({**MIN, "origen": False}, "'origen' debe ser texto"),
])
def test_a_card_that_does_not_make_sense_is_rejected_with_where_and_why(raw, text):
    with pytest.raises(MachinesFileError, match=r"ficha 'x'") as e:
        parse_machine("x", raw)
    assert text in str(e.value)


def test_an_unknown_field_is_rejected_with_a_suggestion():
    with pytest.raises(MachinesFileError) as e:
        parse_machine("x", {**MIN, "recorido": [1, 2, 3], "homming": True})
    text = str(e.value)
    assert "'recorido'" in text and "'homming'" in text
    assert "¿Quisiste decir 'recorrido'?" in text and "¿Quisiste decir 'homing'?" in text
    assert "Campos válidos" in text


def test_a_card_must_be_an_object():
    with pytest.raises(MachinesFileError, match="debe ser un objeto"):
        parse_machine("x", [1, 2, 3])


def test_the_file_needs_the_maquinas_key_and_nothing_else_unknown():
    for text, msg in [("[]", "debe contener un objeto"), ("{}", "falta la clave 'maquinas'"),
                      ('{"maquinas": []}', "falta la clave 'maquinas'"),
                      ('{"maquinas": {}, "otra": 1}', "clave(s) desconocida(s) 'otra'")]:
        with pytest.raises(MachinesFileError, match=re.escape(msg)):
            parse_machines(text)


def test_underscore_keys_are_comments():
    machines = parse_machines('{"_ayuda": "hola", "maquinas": {"a": {"recorrido": [1, 2, 3]}}}')
    assert list(machines) == ["a"]


def test_broken_json_says_where():
    with pytest.raises(MachinesFileError, match=r"maquinas\.json: JSON inválido en la línea 2, columna"):
        parse_machines('{"maquinas":\n  {"a": }}')


def test_a_repeated_key_is_an_error_not_a_silent_overwrite():
    text = '{"maquinas": {"a": {"recorrido": [1,2,3]}, "a": {"recorrido": [4,5,6]}}}'
    with pytest.raises(MachinesFileError, match="'a' está repetida"):
        parse_machines(text)
    with pytest.raises(MachinesFileError, match="'homing' está repetida"):
        parse_machines('{"maquinas": {"a": {"recorrido": [1,2,3], "homing": true, "homing": false}}}')


@pytest.mark.parametrize("ident", ["1abc", "a b", "-x", "", "á", "a/b"])
def test_the_id_must_be_a_word(ident):
    with pytest.raises(MachinesFileError, match="no vale"):
        parse_machines(json.dumps({"maquinas": {ident: MIN}}))


def test_load_reads_utf8_with_bom_and_missing_file_is_an_error(tmp_path):
    p = tmp_path / "m.json"
    p.write_bytes(b"\xef\xbb\xbf" + '{"maquinas": {"a": {"nombre": "Máquina", "recorrido": [1, 2, 3]}}}'.encode())
    assert load_machines(p)["a"].name == "Máquina"
    with pytest.raises(MachinesFileError, match="No existe el archivo de máquinas"):
        load_machines(tmp_path / "no.json")


def test_save_and_load_round_trip(tmp_path):
    original = {"real": replace(GRBL_MAQUINA_REAL, name="Taller", notes="la buena"), "emu": GRBL_MKS_DLC32}
    p = tmp_path / "m.json"
    save_machines(original, p)
    assert load_machines(p) == original
    text = p.read_text(encoding="utf-8")
    assert '"recorrido": [651, 1011, 100],' in text and text.endswith("}\n")
    assert '"_ayuda"' in text


def test_save_writes_utf8_and_keeps_accents(tmp_path):
    p = tmp_path / "m.json"
    save_machines({"a": parse_machine("a", {**MIN, "nombre": "Máquina ñ"})}, p)
    assert "Máquina ñ" in p.read_text(encoding="utf-8")
    assert load_machines(p)["a"].name == "Máquina ñ"


def test_a_name_equal_to_the_id_is_not_written():
    assert "nombre" not in machine_to_dict(parse_machine("a", MIN), "a")
    assert machine_to_dict(replace(parse_machine("a", MIN), name="Taller"), "a")["nombre"] == "Taller"


def test_empty_data_is_not_written():
    assert machine_to_dict(parse_machine("a", MIN), "a") == {"recorrido": [100, 200, 30]}
    assert machine_to_dict(parse_machine("a", {**MIN, "recorrido": [100.5, 200, 30]}), "a")["recorrido"] == [100.5, 200, 30]


def test_unknown_values_stay_none_not_false():
    m = parse_machine("a", MIN)
    assert (m.homing, m.soft_limits, m.hard_limits, m.laser_mode, m.max_feed, m.max_feed_z) == (None,) * 6


# --- elegir la maquina ---------------------------------------------------------------------------------------
def test_the_only_card_is_the_default_one():
    machines = {"a": parse_machine("a", MIN)}
    assert choose_machine(machines, None) == ("a", None)
    assert choose_machine(machines, "") == ("a", None)
    assert choose_machine(machines, "a") == ("a", None)


def test_a_chosen_card_wins_and_a_wrong_one_is_not_replaced_by_the_default():
    machines = {"a": parse_machine("a", MIN), "b": parse_machine("b", MIN)}
    assert choose_machine(machines, "b") == ("b", None)
    ident, why = choose_machine({"a": machines["a"]}, "tallr")
    assert ident is None and "No hay ninguna máquina 'tallr'" in why and "Máquinas: a" in why


def test_several_cards_without_a_choice_choose_nothing():
    machines = {"a": parse_machine("a", MIN), "b": parse_machine("b", MIN)}
    ident, why = choose_machine(machines, None)
    assert ident is None and "Hay varias máquinas (a, b)" in why


def test_no_cards_say_how_to_get_one():
    ident, why = choose_machine({}, None)
    assert ident is None and "No hay ninguna ficha de máquina" in why and "Máquinas…" in why
    ident, why = choose_machine({}, "a")
    assert ident is None and "No hay ninguna máquina 'a'" in why


def test_get_machine_suggests_a_close_name():
    with pytest.raises(MachinesFileError, match="¿Quisiste decir 'taller'"):
        get_machine({"taller": parse_machine("taller", MIN)}, "tallr")


# --- numeros y tablas -------------------------------------------------------------------------------------
def test_numbers_use_a_decimal_comma_and_no_trailing_zeros():
    assert (n(651.0), n(0.5), n(10.25), n(3600)) == ("651", "0,5", "10,25", "3600")
    assert travel_text(GRBL_MAQUINA_REAL) == "651 × 1011 × 100 mm"


def test_the_table_shows_dashes_for_what_the_card_does_not_know():
    table = svg_a_gcode.machines_table({"real": GRBL_MAQUINA_REAL, "min": parse_machine("min", MIN)})
    lines = table.splitlines()
    assert lines[0].split() == ["id", "recorrido", "(mm)", "avance", "XY", "avance", "Z", "homing", "limites", "laser"]
    assert "651 x 1011 x 100" in lines[2] and "blandos sí, duros sí" in lines[2]
    min_row = lines[3].split()
    assert min_row[:5] == ["min", "100", "x", "200", "x"] and "-" in min_row
    assert "sin dato" in lines[-1]


def test_differences_list_only_what_changes_and_ignore_names():
    old = replace(GRBL_MAQUINA_REAL, name="uno", firmware="a", source="x", notes="y")
    new = replace(old, name="dos", firmware="b", source="z", notes="w")
    assert differences(old, new) == []
    changed = replace(old, travel=(650.0, 1017.0, 100.0), homing=False, max_feed=None)
    assert differences(old, changed) == [
        "recorrido (mm): 651 × 1011 × 100 → 650 × 1017 × 100", "avance máx. XY (mm/min): 3600 → sin dato",
        "homing: sí → no"]


# --- el log de arranque -------------------------------------------------------------------------------------
def test_a_real_ugs_log_is_understood():
    parsed = parse_log(UGS_LOG)
    assert parsed.usable and parsed.problems == []
    assert parsed.firmware == "Grbl 1.1h"
    s = parsed.settings
    assert (s[130], s[131], s[132], s[110], s[112], s[22], s[32], s[30]) == (651, 1011, 100, 3600, 3600, 1, 0, 10000)
    m = machine_from_log(UGS_LOG, "Taller", "log del dia")
    assert m == MachineProfile(name="Taller", max_feed=3600.0, travel=(651.0, 1011.0, 100.0), max_feed_z=3600.0,
                               accel=(200.0, 200.0, 100.0), soft_limits=True, hard_limits=True, homing=True,
                               spindle_max=10000.0, laser_mode=False, firmware="Grbl 1.1h", source="log del dia")


def test_values_typed_by_hand_are_understood_in_every_style():
    text = "$130=651\n$131 1011\n  $132 = 100 (z max travel)\n$22=1; homing\n$110=3.600,5\n"
    parsed = parse_log(text)
    assert parsed.settings[130] == 651 and parsed.settings[131] == 1011 and parsed.settings[132] == 100
    assert parsed.settings[22] == 1
    comma = parse_log("$130=651,5\n$131=1011,25\n$132=100\n")
    assert (comma.settings[130], comma.settings[131]) == (651.5, 1011.25)


def test_only_the_travel_is_required():
    m = machine_from_log("$130=100\n$131=200\n$132=30")
    assert m.travel == (100.0, 200.0, 30.0)
    assert (m.max_feed, m.max_feed_z, m.accel, m.homing, m.soft_limits, m.hard_limits, m.laser_mode, m.spindle_max,
            m.firmware) == (None, None, None, None, None, None, None, None, "")


def test_without_the_travel_there_is_no_card_and_the_log_says_what_is_missing():
    parsed = parse_log("$130=651\n$131=1011\n$110=3600")
    assert not parsed.usable and not parsed.has_travel
    assert any("$132 (recorrido Z)" in note for note in parsed.notes)
    assert machine_from_log("$130=651\n$131=1011") is None
    assert machine_from_log("hola, esto no es un log") is None
    assert parse_log("").settings == {} and not parse_log("").usable


def test_garbage_lines_are_ignored():
    parsed = parse_log("G0 X10 Y10\n[GC:G0 G54 G17 G21 G90 G94 M5 M9 T0 F0 S0]\n$130=100\nbasura $\n$131=200\n$132=30\n")
    assert parsed.usable and set(parsed.settings) == {130, 131, 132}


def test_the_same_value_twice_is_fine_but_two_different_values_are_a_problem():
    assert parse_log(UGS_LOG + "\n$130=651.000\n").usable
    mixed = parse_log("$130=651\n$131=1011\n$132=100\n$130=650\n")
    assert not mixed.usable and "$130 (recorrido X) sale dos veces con valores distintos (651 y 650)" in mixed.problems[0]
    assert machine_from_log("$130=651\n$131=1011\n$132=100\n$130=650\n") is None


@pytest.mark.parametrize("line, text", [("$22=2", "$22 (homing) debe ser 0 o 1"), ("$20=-1", "$20 (límites blandos) debe ser 0 o 1"),
                                        ("$21=0.5", "$21 (límites duros) debe ser 0 o 1"),
                                        ("$32=7", "$32 (modo láser) debe ser 0 o 1"),
                                        ("$110=0", "$110 (avance máx. X) debe ser mayor que 0"),
                                        ("$112=-5", "$112 (avance máx. Z) debe ser mayor que 0"),
                                        ("$121=0", "$121 (aceleración Y) debe ser mayor que 0"),
                                        ("$30=-1", "$30 (husillo máx.) debe ser mayor que 0")])
def test_values_that_make_no_sense_make_the_log_unusable(line, text):
    parsed = parse_log("$130=100\n$131=200\n$132=30\n" + line)
    assert not parsed.usable and text in parsed.problems[0]


@pytest.mark.parametrize("axis", ["$130=0", "$131=-1", "$132=0"])
def test_a_travel_of_zero_or_less_makes_the_log_unusable(axis):
    base = {"$130": "100", "$131": "200", "$132": "30"}
    key, value = axis.split("=")
    base[key] = value
    parsed = parse_log("\n".join(f"{k}={v}" for k, v in base.items()))
    assert not parsed.usable and "debe ser mayor que 0" in parsed.problems[0]


def test_a_zero_spindle_max_is_allowed_and_means_no_data():
    m = machine_from_log("$130=100\n$131=200\n$132=30\n$30=0")
    assert m is not None and m.spindle_max is None


def test_the_smaller_xy_feed_is_used_and_the_difference_is_noted():
    log = "$130=100\n$131=200\n$132=30\n$110=3600\n$111=3000\n$112=1000"
    m = machine_from_log(log)
    assert (m.max_feed, m.max_feed_z) == (3000.0, 1000.0)
    assert any("difieren" in note for note in parse_log(log).notes)
    assert machine_from_log("$130=100\n$131=200\n$132=30\n$111=3000").max_feed == 3000.0


def test_what_is_missing_is_listed_as_a_note_so_nobody_assumes_it():
    parsed = parse_log("$130=100\n$131=200\n$132=30\n$22=1")
    note = next(note for note in parsed.notes if note.startswith("No aparece"))
    assert "$20 (límites blandos)" in note and "$22" not in note and "$110" in note


def test_the_firmware_comes_from_the_banner_or_from_i():
    assert parse_log("Grbl 1.1f ['$' for help]\n$130=1\n$131=1\n$132=1").firmware == "Grbl 1.1f"
    assert parse_log("[VER:1.1h.20190825:]\n$130=1\n$131=1\n$132=1").firmware == "Grbl 1.1h"
    assert parse_log("[VER:]\n$130=1\n$131=1\n$132=1").firmware == ""
    assert parse_log("$130=1\n$131=1\n$132=1").firmware == ""
    both = parse_log("Grbl 1.1h ['$' for help]\n[VER:1.0x.2019:]\n$130=1\n$131=1\n$132=1")
    assert both.firmware == "Grbl 1.1h"


def test_what_was_understood_is_shown_in_words():
    lines = understood(parse_log(UGS_LOG))
    assert lines[0] == "Firmware: Grbl 1.1h"
    assert "Recorrido: 651 × 1011 × 100 mm" in lines
    assert "Avances máximos (mm/min): X 3600, Y 3600, Z 3600" in lines
    assert "Aceleración (mm/s²): X 200, Y 200, Z 100" in lines
    assert {"Límites blandos: sí", "Límites duros: sí", "Homing: sí", "Modo láser: no", "Husillo máx.: 10000 RPM"} <= set(lines)
    assert understood(parse_log("nada")) == []


# --- el editor de fichas ------------------------------------------------------------------------------------
def test_a_card_goes_through_the_form_and_comes_back_equal():
    for m in (GRBL_MAQUINA_REAL, GRBL_MKS_DLC32, parse_machine("a", MIN)):
        form = machine_to_form(m, "x")
        assert build_machine("x", form) == replace(m, name=m.name or "x")


def test_the_form_uses_empty_for_no_data_and_si_no_for_flags():
    form = machine_to_form(parse_machine("a", {**MIN, "homing": True, "laser": False}), "a")
    assert form["homing"] == "si" and form["laser"] == "no" and form["limites_blandos"] == "" and form["avance_max"] == ""
    assert form["x"] == "100" and form["nombre"] == ""


def test_form_errors_are_in_the_words_of_the_window():
    base = machine_to_form(parse_machine("a", MIN), "a")
    for change, text in [({"x": ""}, "el recorrido tiene que tener las tres casillas (X, Y y Z); falta X"),
                         ({"x": "", "y": "", "z": ""}, "falta el recorrido"),
                         ({"y": "mucho"}, "debe ser un número"),
                         ({"z": "0"}, "mayor que 0"),
                         ({"acel_x": "5"}, "la aceleración tiene que tener las tres casillas (X, Y y Z); falta Y, Z"),
                         ({"avance_max": "-3"}, "mayor que 0"),
                         ({"homing": "quizas"}, "'homing' debe ser sí, no o sin dato")]:
        with pytest.raises(MachineEditError, match=re.escape(text)):
            build_machine("a", {**base, **change})


def test_the_form_accepts_commas_and_spanish_si():
    base = machine_to_form(parse_machine("a", MIN), "a")
    m = build_machine("a", {**base, "x": "651,5", "homing": "sí", "laser": "NO"})
    assert m.travel[0] == 651.5 and m.homing is True and m.laser_mode is False
    assert form_to_raw({**base, "homing": " si "})["homing"] is True


def test_the_id_is_checked():
    base = machine_to_form(parse_machine("a", MIN), "a")
    for bad in ("", "1x", "a b"):
        with pytest.raises(MachineEditError):
            build_machine(bad, base)


def test_advice_says_what_the_card_does_not_check():
    notes = advice(parse_machine("a", MIN))
    assert any("Sin avance máximo" in x for x in notes) and any("homing" in x for x in notes)
    assert any("láser" in x for x in notes)
    assert advice(GRBL_MAQUINA_REAL) == []
    no_homing = advice(GRBL_MKS_DLC32)
    assert any("Sin homing" in x for x in no_homing)


def test_the_editor_starts_valid_and_marks_a_broken_card():
    ed = MachineEditor({"real": GRBL_MAQUINA_REAL})
    assert ed.valid and ed.entries[0].label == "real — Taller (651 × 1011 × 100 mm)"
    assert MachineEditor({"a": parse_machine("a", MIN)}).entries[0].label == "a (100 × 200 × 30 mm)"
    ed.update(0, form={**ed.entries[0].form, "x": ""})
    assert not ed.valid and ed.errors()[0].startswith("real: ") and ed.entries[0].label == "real"
    with pytest.raises(MachineEditError):
        ed.machines()


def test_new_duplicate_and_remove():
    ed = MachineEditor({"real": GRBL_MAQUINA_REAL})
    i = ed.new()
    assert ed.entries[i].ident == "maquina" and not ed.entries[i].valid and "falta el recorrido" in ed.errors()[0]
    j = ed.duplicate(0)
    assert j == 1 and ed.entries[1].ident == "real_copia" and ed.entries[1].form == ed.entries[0].form
    ed.duplicate(0)
    assert [e.ident for e in ed.entries] == ["real", "real_copia_2", "real_copia", "maquina"]
    ed.remove(3)
    ed.remove(1)
    assert [e.ident for e in ed.entries] == ["real", "real_copia"] and ed.valid


def test_two_cards_with_the_same_id_are_both_errors():
    ed = MachineEditor({"a": parse_machine("a", MIN), "b": parse_machine("b", MIN)})
    ed.update(1, ident="a")
    assert not ed.valid and all("hay otra ficha con el identificador 'a'" in e.error for e in ed.entries)


def test_renamed_and_removed_are_reported():
    ed = MachineEditor({"a": parse_machine("a", MIN), "b": parse_machine("b", MIN), "c": parse_machine("c", MIN)})
    ed.update(0, ident="z")
    ed.remove(1)
    assert ed.renamed() == {"a": "z"} and ed.removed() == ["b"]
    assert list(ed.machines()) == ["z", "c"]
    assert MachineEditor().renamed() == {} and MachineEditor().removed() == []


def test_import_log_adds_a_card_and_refuses_what_is_not_usable():
    ed = MachineEditor()
    i = ed.import_log(UGS_LOG, ident="taller", name="Taller")
    assert i == 0 and ed.entries[0].ident == "taller" and ed.valid
    assert ed.machines()["taller"].travel == (651.0, 1011.0, 100.0) and ed.machines()["taller"].name == "Taller"
    assert ed.machines()["taller"].source == "log pegado"
    assert ed.import_log(UGS_LOG, source="log del dia") == 1 and ed.entries[1].ident == "maquina"
    assert ed.machines()["maquina"].source == "log del dia"
    before = [e.ident for e in ed.entries]
    for text, msg in [("", "No se ha encontrado ningún ajuste"), ("$130=1\n$131=2", r"Falta \$132"),
                      ("$130=1\n$131=2\n$132=3\n$130=4", "dos veces con valores distintos")]:
        with pytest.raises(MachineEditError, match=msg):
            ed.import_log(text)
    with pytest.raises(MachineEditError, match="ya hay una ficha con el identificador 'taller'"):
        ed.import_log(UGS_LOG, ident="taller")
    with pytest.raises(MachineEditError):
        ed.import_log(UGS_LOG, ident="1mala")
    assert [e.ident for e in ed.entries] == before


def test_refreshing_changes_only_what_the_log_brings():
    ed = MachineEditor({"real": replace(GRBL_MAQUINA_REAL, name="Taller", notes="mi nota")})
    changes = ed.refresh_from_log(0, "$130=650\n$131=1017\n$132=100\nGrbl 1.1h ['$' for help]", source="log nuevo")
    assert changes == ["recorrido (mm): 651 × 1011 × 100 → 650 × 1017 × 100"]
    m = ed.machines()["real"]
    assert m.travel == (650.0, 1017.0, 100.0) and m.homing is True and m.max_feed == 3600.0     # lo que no vino se queda
    assert m.name == "Taller" and m.notes == "mi nota" and m.firmware == "Grbl 1.1h" and m.source == "log nuevo"


def test_preview_refresh_touches_nothing_and_a_bad_log_touches_nothing():
    ed = MachineEditor({"real": GRBL_MAQUINA_REAL})
    before = dict(ed.entries[0].form)
    assert ed.preview_refresh(0, "$130=1\n$131=2\n$132=3") == ["recorrido (mm): 651 × 1011 × 100 → 1 × 2 × 3"]
    assert ed.entries[0].form == before
    with pytest.raises(MachineEditError):
        ed.refresh_from_log(0, "$130=1")
    assert ed.entries[0].form == before


def test_a_log_that_only_repeats_what_is_there_changes_nothing():
    ed = MachineEditor({"real": GRBL_MAQUINA_REAL})
    assert ed.refresh_from_log(0, "$130=651\n$131=1011\n$132=100\n$22=1") == []


def test_a_broken_card_cannot_be_refreshed():
    ed = MachineEditor({"real": GRBL_MAQUINA_REAL})
    ed.update(0, form={**ed.entries[0].form, "x": ""})
    with pytest.raises(MachineEditError, match="corrígelos"):
        ed.refresh_from_log(0, UGS_LOG)


def test_save_keeps_a_backup_and_refuses_when_something_is_wrong(tmp_path):
    p = tmp_path / "maquinas.json"
    ed = MachineEditor({"real": GRBL_MAQUINA_REAL})
    ed.save(p)
    assert p.is_file() and not (tmp_path / "maquinas.json.bak").exists()
    first = p.read_text(encoding="utf-8")
    ed.update(0, form={**ed.entries[0].form, "x": "700"})
    ed.save(p)
    assert (tmp_path / "maquinas.json.bak").read_text(encoding="utf-8") == first
    assert load_machines(p)["real"].travel[0] == 700.0
    ed.update(0, form={**ed.entries[0].form, "x": ""})
    with pytest.raises(MachineEditError):
        ed.save(p)
    assert load_machines(p)["real"].travel[0] == 700.0
