"""cnc.toolbook: edicion de las fichas de fresas sin Qt."""
import json

import pytest

from cnc.model import Tool
from cnc.records import BACKUP_SUFFIX
from cnc.toolbook import ToolEditError, ToolEditor, advice, build_tool, check_ident, form_to_raw, tool_to_form
from cnc.tools import load_tools, parse_tools, save_tools

FILE = ('{"herramientas": {'
        '"em4": {"nombre": "End mill 4 mm", "diametro": 4, "feed": 300, "entrada": 200, "flautas": 2, "stepover": 40},'
        '"em3175": {"diametro": 3.175, "notas": "pendiente"},'
        '"v90": {"tipo": "v", "diametro": 6, "angulo": 90}}}')


def tools():
    return parse_tools(FILE)


def form(**kw):
    base = {"nombre": "", "tipo": "endmill", "diametro": "4", "flautas": "", "longitud_util": "", "angulo": "", "rpm": "",
            "profundidad_pasada": "", "feed": "", "entrada": "", "stepover": "", "notas": ""}
    base.update(kw)
    return base


# --- formulario <-> ficha ----------------------------------------------------------------------------------------
def test_a_tool_shows_in_the_form_as_the_texts_you_would_type():
    f = tool_to_form(tools()["em4"], "em4")
    assert (f["nombre"], f["tipo"], f["diametro"], f["feed"], f["entrada"], f["flautas"], f["stepover"]) == (
        "End mill 4 mm", "endmill", "4", "300", "200", "2", "40")
    assert f["rpm"] == "" and f["angulo"] == ""
    assert tool_to_form(tools()["v90"], "v90")["angulo"] == "90" and tool_to_form(tools()["v90"], "v90")["tipo"] == "v"
    assert tool_to_form(tools()["em3175"], "em3175")["nombre"] == ""            # el nombre que se pone solo no se repite
    assert tool_to_form(tools()["em3175"], "em3175")["diametro"] == "3.175"


def test_the_form_gives_back_the_same_tool_it_came_from():
    for ident, tool in tools().items():
        again = build_tool(ident, tool_to_form(tool, ident))
        assert again == tool, ident


def test_numbers_accept_comma_and_empty_boxes_are_not_written():
    raw = form_to_raw(form(diametro="3,175", feed="1.200", entrada=" 300 ", flautas="2", nombre=""))
    assert raw == {"tipo": "endmill", "diametro": 3.175, "flautas": 2, "feed": 1.2, "entrada": 300}
    assert "nombre" not in raw and "rpm" not in raw


def test_the_angle_only_travels_with_a_v_bit():
    assert "angulo" not in form_to_raw(form(angulo="90"))                         # una plana con el angulo de antes
    assert form_to_raw(form(tipo="v", angulo="90"))["angulo"] == 90


@pytest.mark.parametrize("change, message", [
    (dict(diametro=""), "diametro"),
    (dict(diametro="0"), "mayor que 0"),
    (dict(diametro="abc"), "debe ser un número"),
    (dict(feed="-5"), "'feed'"),
    (dict(flautas="2,5"), "número entero"),
    (dict(flautas="0"), "'flautas'"),
    (dict(stepover="0,4"), "porcentaje"),
    (dict(stepover="120"), "entre 0 y 100"),
    (dict(tipo="v"), "necesita 'angulo'"),
    (dict(tipo="v", angulo="200"), "'angulo'"),
    (dict(tipo="raro"), "'tipo'"),
])
def test_bad_values_say_which_field_and_why(change, message):
    with pytest.raises(ToolEditError, match=message):
        build_tool("x", form(**change))


def test_the_message_does_not_repeat_the_file_name_prefix():
    with pytest.raises(ToolEditError) as e:
        build_tool("em4", form(diametro="0"))
    assert not str(e.value).startswith("ficha")


@pytest.mark.parametrize("ident", ["", "4", "1/8", "con espacio", "ñu", "-x", "a/b"])
def test_identifiers_must_start_with_a_letter_and_use_simple_characters(ident):
    with pytest.raises(ToolEditError):
        check_ident(ident)


@pytest.mark.parametrize("ident", ["em4", "em4_mdf", "V90", "a.b-c_1"])
def test_good_identifiers(ident):
    check_ident(ident)


def test_advice_lists_what_is_missing_without_blocking_anything():
    em3175 = advice(tools()["em3175"])
    assert any("feed ni entrada" in n for n in em3175) and any("longitud útil" in n for n in em3175)
    assert any("profundidad de pasada" in n for n in em3175)
    em4 = advice(tools()["em4"])
    assert not any("feed" in n for n in em4)
    assert any("solo las fresas planas" in n for n in advice(tools()["v90"]))
    full = Tool(diameter=4, feed_xy=300, feed_z=200, flute_length=22, stepdown=2.0)
    assert advice(full) == []
    assert any("solo entrada" not in n and "Sin entrada" in n for n in advice(Tool(diameter=4, feed_xy=300)))


# --- lista de fichas ---------------------------------------------------------------------------------------------------
def test_the_editor_starts_valid_with_the_file_in_order():
    ed = ToolEditor(tools())
    assert ed.valid and [e.ident for e in ed.entries] == ["em4", "em3175", "v90"] and ed.errors() == []
    assert [e.label for e in ed.entries] == ["em4 — End mill 4 mm (Ø4 mm)", "em3175 (Ø3.175 mm)", "v90 (Ø6 mm)"]
    assert ed.tools() == tools()


def test_editing_a_field_revalidates_and_keeps_the_rest():
    ed = ToolEditor(tools())
    f = dict(ed.entries[1].form, feed="450", entrada="150")
    e = ed.update(1, form=f)
    assert e.valid and e.tool.feed_xy == 450 and e.tool.feed_z == 150 and e.tool.diameter == 3.175
    assert ed.tools()["em3175"].feed_xy == 450 and ed.tools()["em4"] == tools()["em4"]
    ed.update(1, form=dict(f, diametro="-1"))
    assert not ed.valid and "mayor que 0" in ed.entries[1].error and ed.entries[1].tool is None
    with pytest.raises(ToolEditError, match="em3175"):
        ed.tools()
    assert ed.errors()[0].startswith("em3175: ")


def test_renaming_checks_duplicates_on_both_cards_and_reports_the_rename():
    ed = ToolEditor(tools())
    ed.update(1, ident="em4")
    assert not ed.valid and ed.entries[0].error and ed.entries[1].error                  # las dos señalan el duplicado
    ed.update(1, ident="em3175_mdf")
    assert ed.valid and list(ed.tools()) == ["em4", "em3175_mdf", "v90"]                  # mismo orden
    assert ed.renamed() == {"em3175": "em3175_mdf"} and ed.removed() == []
    ed.update(2, ident="")
    assert not ed.valid and "falta el identificador" in ed.entries[2].error


def test_new_duplicate_and_remove():
    ed = ToolEditor(tools())
    i = ed.new()
    assert ed.entries[i].ident == "fresa" and not ed.valid and "diametro" in ed.entries[i].error     # no vale sin diametro
    assert ed.new() == i + 1 and ed.entries[i + 1].ident == "fresa_2"
    ed.remove(i + 1)
    ed.update(i, ident="v60", form=form(diametro="6", tipo="v", angulo="60"))
    assert ed.valid and ed.entries[i].tool.v_angle == 60
    j = ed.duplicate(0)
    assert j == 1 and ed.entries[1].ident == "em4_copia" and ed.entries[1].original is None
    assert ed.entries[1].tool == ed.entries[0].tool.__class__(**{**ed.entries[0].tool.__dict__, "name": "End mill 4 mm"})
    assert ed.duplicate(0) == 1 and ed.entries[1].ident == "em4_copia_2"
    ed.remove(0)
    assert ed.removed() == ["em4"] and "em4" not in ed.tools()


# --- guardar -------------------------------------------------------------------------------------------------------------
def test_saving_writes_a_file_the_program_reads_back_and_keeps_a_backup(tmp_path):
    path = tmp_path / "herramientas.json"
    path.write_text(FILE, encoding="utf-8")
    ed = ToolEditor(load_tools(path))
    ed.update(1, form=dict(ed.entries[1].form, feed="450", entrada="150", flautas="2", rpm="18000"))
    j = ed.duplicate(0)
    ed.update(j, ident="em4_mdf", form=dict(ed.entries[j].form, feed="500", profundidad_pasada="2"))
    assert ed.save(path) == path
    back = load_tools(path)
    assert list(back) == ["em4", "em4_mdf", "em3175", "v90"]
    assert (back["em3175"].feed_xy, back["em3175"].feed_z, back["em3175"].flutes, back["em3175"].rpm) == (450, 150, 2, 18000)
    assert back["em4_mdf"].feed_xy == 500 and back["em4_mdf"].stepdown == 2 and back["em4_mdf"].diameter == 4
    assert back["em4"] == tools()["em4"] and back["v90"] == tools()["v90"]
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    assert backup.read_text(encoding="utf-8") == FILE                                    # el original queda a salvo
    assert "_ayuda" in json.loads(path.read_text(encoding="utf-8"))


def test_nothing_is_written_while_a_card_is_invalid(tmp_path):
    path = tmp_path / "herramientas.json"
    path.write_text(FILE, encoding="utf-8")
    ed = ToolEditor(load_tools(path))
    ed.new()
    with pytest.raises(ToolEditError):
        ed.save(path)
    assert path.read_text(encoding="utf-8") == FILE and not path.with_name(path.name + BACKUP_SUFFIX).exists()


def test_saving_a_new_file_makes_no_backup(tmp_path):
    ed = ToolEditor({})
    ed.update(ed.new(), ident="em4", form=form(diametro="4", feed="300", entrada="200"))
    path = tmp_path / "nuevo.json"
    ed.save(path)
    assert load_tools(path)["em4"].feed_xy == 300 and not (tmp_path / "nuevo.json.bak").exists()


def test_the_saved_text_equals_what_save_tools_writes_for_the_same_tools(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    ToolEditor(tools()).save(a)
    save_tools(tools(), b)
    assert a.read_text(encoding="utf-8") == b.read_text(encoding="utf-8")
