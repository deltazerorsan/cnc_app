"""Base de datos de fresas (herramientas.json): lectura estricta, ida y vuelta, y uso en el G-code."""
import json
from pathlib import Path

import pytest

from cnc.generate import generate_gcode, tool_label
from cnc.model import ContourOp, Job, Stock, Tool, resolved
import svg_a_gcode
from cnc.tools import (ToolsFileError, get_tool, load_tools, parse_tool, parse_tools,
                       save_tools, tool_to_dict)
from shapely.geometry import box

ROOT = Path(__file__).resolve().parent.parent


def tools_text(**cards):
    return json.dumps({"herramientas": cards})


# --- el archivo que se entrega -------------------------------------------------------------
def test_shipped_file_loads_and_has_the_two_usual_endmills():
    tools = load_tools(ROOT / "herramientas.json")
    assert tools["em4"].diameter == 4 and tools["em4"].kind == "endmill"
    assert tools["em4"].feed_xy == 300 and tools["em4"].feed_z == 200
    assert tools["em3175"].diameter == pytest.approx(3.175)


def test_save_then_load_is_lossless(tmp_path):
    seed = {
        "a": Tool(4, name="A", flutes=2, flute_length=12, rpm=18000, stepdown=2, feed_xy=300, feed_z=200,
                  stepover=0.45, notes="n"),
        "v90": Tool(12.7, name="V 90", kind="vbit", v_angle=90, rpm=16000),
        "b": Tool(3.175, name="b", kind="ball"),
    }
    f = tmp_path / "t.json"
    save_tools(seed, f)
    assert load_tools(f) == seed


def test_saved_file_is_valid_json_with_help_and_utf8(tmp_path):
    f = tmp_path / "t.json"
    save_tools({"x": Tool(3.175, name="Fresa 1/8 “", notes="acentos: ñ á")}, f)
    data = json.loads(f.read_text(encoding="utf-8"))
    assert "_ayuda" in data and data["herramientas"]["x"]["notas"].startswith("acentos")


def test_bom_from_windows_editors_is_accepted(tmp_path):
    f = tmp_path / "t.json"
    f.write_bytes(b"\xef\xbb\xbf" + tools_text(a={"diametro": 4}).encode())
    assert load_tools(f)["a"].diameter == 4


# --- campos y conversiones -----------------------------------------------------------------
def test_field_mapping_and_units():
    t = parse_tool("x", {"nombre": "N", "tipo": "endmill", "diametro": 4, "flautas": 2, "longitud_util": 12,
                         "rpm": 18000, "profundidad_pasada": 1.5, "feed": 300, "entrada": 200, "stepover": 40,
                         "notas": "hola"})
    assert (t.name, t.kind, t.diameter, t.flutes, t.flute_length, t.rpm, t.stepdown, t.feed_xy, t.feed_z,
            t.stepover, t.notes) == ("N", "endmill", 4, 2, 12, 18000, 1.5, 300, 200, 0.4, "hola")


def test_only_diameter_is_required_and_name_defaults_to_the_id():
    t = parse_tools(tools_text(solo={"diametro": 3.175}))["solo"]
    assert t.name == "solo" and t.feed_xy is None and t.stepdown is None and t.kind == "endmill"


def test_v_bit_keeps_its_angle_and_prints_it_in_the_table():
    tools = parse_tools(tools_text(v90={"tipo": "v", "diametro": 12.7, "angulo": 90}))
    assert tools["v90"].kind == "vbit" and tools["v90"].v_angle == 90
    assert "v 90" in svg_a_gcode.tools_table(tools)


@pytest.mark.parametrize("card, expected", [
    ({"diametro": 4, "feed": 300, "stepoverr": 40}, "stepoverr"),            # clave mal escrita
    ({"feed": 300}, "falta el campo obligatorio 'diametro'"),
    ({"diametro": 0}, "'diametro' debe ser un número mayor que 0"),
    ({"diametro": -4}, "'diametro' debe ser un número mayor que 0"),
    ({"diametro": "4"}, "'diametro' debe ser un número mayor que 0"),
    ({"diametro": True}, "'diametro' debe ser un número mayor que 0"),
    ({"diametro": float("inf")}, "'diametro' debe ser un número mayor que 0"),
    ({"diametro": 4, "feed": 0}, "'feed' debe ser un número mayor que 0"),
    ({"diametro": 4, "entrada": "200"}, "'entrada' debe ser un número mayor que 0"),
    ({"diametro": 4, "flautas": 2.5}, "'flautas' debe ser un numero entero"),
    ({"diametro": 4, "flautas": 0}, "'flautas' debe ser un numero entero"),
    ({"diametro": 4, "tipo": "cono"}, "'tipo' debe ser uno de"),
    ({"diametro": 4, "tipo": "v"}, "necesita 'angulo'"),
    ({"diametro": 4, "tipo": "v", "angulo": 180}, "'angulo' debe estar entre 0 y 180"),
    ({"diametro": 4, "angulo": 90}, "'angulo' solo tiene sentido en fresas de tipo v"),
    ({"diametro": 4, "stepover": 0.4}, "porcentaje"),                         # 0,4 en vez de 40
    ({"diametro": 4, "stepover": 1}, "porcentaje"),
    ({"diametro": 4, "stepover": 150}, "'stepover' debe estar entre 0 y 100"),
    ({"diametro": 4, "nombre": 4}, "'nombre' debe ser texto"),
])
def test_bad_cards_give_a_precise_error(card, expected):
    with pytest.raises(ToolsFileError, match=expected) as e:
        parse_tools(tools_text(f1=card))
    assert "f1" in str(e.value)        # el mensaje dice en que ficha esta el problema


def test_typo_suggestion_names_the_right_field():
    with pytest.raises(ToolsFileError, match="Quisiste decir 'stepover'"):
        parse_tools(tools_text(a={"diametro": 4, "stepoverr": 40}))


@pytest.mark.parametrize("text, expected", [
    ("{", "JSON inválido en la línea"),
    ("[]", "debe contener un objeto"),
    ("{}", "falta la clave 'herramientas'"),
    ('{"herramientas": []}', "falta la clave 'herramientas'"),
    ('{"herramientas": {}, "otras": 1}', "desconocida"),
    ('{"herramientas": {"a": {"diametro": 4}, "a": {"diametro": 3}}}', "repetida"),
    ('{"herramientas": {"4": {"diametro": 4}}}', "identificador"),              # un numero seria un diametro
    ('{"herramientas": {"a b": {"diametro": 4}}}', "identificador"),
    ('{"herramientas": {"a": 5}}', "debe ser un objeto"),
])
def test_bad_files_give_a_precise_error(text, expected):
    with pytest.raises(ToolsFileError, match=expected):
        parse_tools(text, "x.json")


def test_underscore_keys_are_comments_and_an_empty_database_is_valid():
    assert parse_tools('{"_ayuda": "texto", "_nota": 1, "herramientas": {}}') == {}


def test_missing_file_and_unknown_tool_messages(tmp_path):
    with pytest.raises(ToolsFileError, match="No existe"):
        load_tools(tmp_path / "nada.json")
    tools = parse_tools(tools_text(em4={"diametro": 4}, em3={"diametro": 3}))
    with pytest.raises(ToolsFileError, match="Fichas: em4, em3") as e:
        get_tool(tools, "em5")
    assert "Quisiste decir" in str(e.value)


def test_stepover_survives_the_percentage_round_trip():
    t = parse_tool("x", {"diametro": 4, "stepover": 45})
    assert tool_to_dict(t)["stepover"] == 45


# --- como entra la ficha en el G-code --------------------------------------------------------
def plate(**op):
    return [ContourOp(box(0, 0, 30, 20), side="outside", through=True, **op)]


def gcode(tool, **op):
    return generate_gcode(Job(Stock(6, spoilboard_extra=0.4), tool, plate(**op))).text


def test_tool_values_are_used_when_the_operation_has_none():
    text = gcode(Tool(4, feed_xy=300, feed_z=200, stepdown=3.2))
    assert "F300" in text and "F200" in text
    assert "Z-3.2" in text and "Z-6.4" in text                  # 6,4 mm en dos pasadas de 3,2


def test_operation_values_win_over_the_tool():
    text = gcode(Tool(4, feed_xy=300, feed_z=200, stepdown=3.2), feed_xy=500, feed_z=100, stepdown=6.4)
    assert "F500" in text and "F100" in text and "F300" not in text and "F200" not in text
    assert "Z-3.2" not in text


def test_resolved_falls_back_to_library_defaults_when_nothing_is_set():
    op = resolved(ContourOp(box(0, 0, 5, 5), side="outside"), Tool(4))
    assert (op.stepdown, op.feed_xy, op.feed_z) == (2.0, 1200.0, 300.0)


def test_tool_flute_length_is_checked_against_the_total_depth():
    with pytest.raises(ValueError, match="longitud util"):
        gcode(Tool(4, flute_length=5, feed_xy=300, feed_z=200))      # 6,4 mm > 5 mm de filo
    assert "G1" in gcode(Tool(4, flute_length=6.4, feed_xy=300, feed_z=200))  # justo cabe


@pytest.mark.parametrize("tool", [Tool(12.7, kind="vbit", v_angle=90), Tool(3.175, kind="ball")])
def test_v_and_ball_are_stored_as_data_but_do_not_generate(tool):
    with pytest.raises(ValueError, match="solo las end mill"):
        gcode(tool, feed_xy=300, feed_z=200)


def test_header_carries_the_tool_and_the_advised_rpm_without_breaking_ascii():
    text = gcode(Tool(4, name="End mill 4 mm (ñandu)", rpm=18000, feed_xy=300, feed_z=200))
    text.encode("ascii")
    assert "(Fresa End mill 4 mm [nandu]" in text and "RPM aconsejadas [variador]: 18000" in text
    assert all(len(line) <= 79 for line in text.splitlines())


def test_tool_label_does_not_repeat_the_diameter():
    assert tool_label(Tool(4, name="End mill 4 mm")) == "End mill 4 mm"
    assert tool_label(Tool(3.175, name="End mill 3,175 mm")) == "End mill 3,175 mm"
    assert tool_label(Tool(6, name="Fresa gorda")) == "Fresa gorda (6 mm)"
    assert tool_label(Tool(4)) == "de 4 mm"
