"""Linea de comandos (--help, fichas, --list-tools) y archivo de configuracion cnc_app.toml."""
import re
import sys
from pathlib import Path

import pytest

import svg_a_gcode
from cnc.config import CONFIG_NAME, ConfigError, find_config, read_config
from cnc.tools import load_tools
from verificar_nc import analyze

ROOT = Path(__file__).resolve().parent.parent
REAL_SVG = Path(__file__).parent / "data" / "placa_doble.svg"

SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="60mm" height="40mm" viewBox="0 0 60 40">
<rect x="5" y="5" width="50" height="30" fill="none" stroke="#000000"/>
<circle cx="30" cy="20" r="6" fill="none" stroke="#000000"/>
<rect x="1" y="1" width="58" height="38" fill="none" stroke="#ff0000"/>
</svg>"""

TOOLS = """{"herramientas": {
  "em4": {"nombre": "End mill 4 mm", "diametro": 4, "feed": 300, "entrada": 200},
  "multi": {"diametro": 4, "feed": 300, "entrada": 200, "profundidad_pasada": 3.2, "rpm": 18000},
  "sinfeed": {"diametro": 3.175},
  "corta": {"diametro": 4, "feed": 300, "entrada": 200, "longitud_util": 5},
  "v90": {"tipo": "v", "diametro": 12.7, "angulo": 90, "feed": 300, "entrada": 200}
}}"""


@pytest.fixture
def work(tmp_path):
    (tmp_path / "dib.svg").write_text(SVG)
    (tmp_path / "herramientas.json").write_text(TOOLS, encoding="utf-8")
    return tmp_path


def run(work, *args, capsys=None, config=None):
    """Lanza el programa y devuelve (codigo, stdout, stderr, texto_nc)."""
    out = work / "out.nc"
    if out.exists():
        out.unlink()
    argv = [str(work / "dib.svg"), "--tools-file", str(work / "herramientas.json"), "-o", str(out), *args]
    if config is not None:
        (work / "c.toml").write_text(config, encoding="utf-8")
        argv += ["--config", str(work / "c.toml")]
    else:
        argv += ["--no-config"]
    if "--no-boards" not in argv and "--boards" not in argv and "--list-boards" not in argv and "--board" not in argv:
        argv += ["--no-boards"]       # el dibujo de prueba tiene un rectangulo con formas dentro: aqui no es un tablero
    try:
        code = svg_a_gcode.main(argv)
    except SystemExit as e:
        code = e.code
    cap = capsys.readouterr()
    return code, cap.out, cap.err, (out.read_text() if out.exists() else None)


def body(text):
    return [ln for ln in text.splitlines() if not ln.startswith("(")]


# --- --help -----------------------------------------------------------------------------------
def test_help_lists_every_option_in_groups_with_defaults(capsys):
    with pytest.raises(SystemExit) as e:
        svg_a_gcode.main(["--help"])
    assert e.value.code == 0
    h = " ".join(capsys.readouterr().out.split())      # el ajuste de linea parte "(por defecto: X)"
    for opt in ["--tool", "--tools-file", "--list-tools", "--thickness", "--margin", "--feed", "--plunge",
                "--stepdown", "--exclude-color", "--stock-color", "--portrait", "--rotate", "--origin",
                "--order", "--ppi", "--safe-z", "--clearance-z", "--no-spindle", "--pause-at-start",
                "--home-at-end", "--return-to-origin", "--out", "--config", "--no-config"]:
        assert opt in h, opt
    for group in ["fresa y material:", "parametros de corte", "que se corta", "g-code de salida:", "configuracion:"]:
        assert group in h
    assert "(por defecto: 0.2)" in h and "(por defecto: 5.0)" in h and "ejemplos:" in h and CONFIG_NAME in h


def test_help_shows_the_values_from_the_config_file_as_defaults(work, capsys):
    (work / "c.toml").write_text("safe_z = 9.0\n")
    with pytest.raises(SystemExit):
        svg_a_gcode.main(["--help", "--config", str(work / "c.toml")])
    assert "(por defecto: 9.0)" in " ".join(capsys.readouterr().out.split())


def test_help_still_works_when_the_config_file_is_broken(work, capsys):
    (work / "c.toml").write_text("esto no es toml [[[")
    with pytest.raises(SystemExit) as e:
        svg_a_gcode.main(["--help", "--config", str(work / "c.toml")])
    assert e.value.code == 0 and "usage:" in capsys.readouterr().out


def test_abbreviated_options_are_rejected(work, capsys):
    code, _, err, _ = run(work, "--tool", "em4", "--thick", "6", capsys=capsys)
    assert code == 2 and "--thick" in err


# --- --list-tools y eleccion de fresa ----------------------------------------------------------
def test_list_tools_shows_the_table_and_needs_no_svg(work, capsys):
    assert svg_a_gcode.main(["--list-tools", "--tools-file", str(work / "herramientas.json"), "--no-config"]) == 0
    out = capsys.readouterr().out
    assert "em4" in out and "v 90" in out and "sinfeed" in out and "stepover" in out


def test_tool_card_provides_feed_and_plunge_and_says_so(work, capsys):
    code, out, _, nc = run(work, "--tool", "em4", "--thickness", "6", capsys=capsys)
    assert code == 0
    assert "feed 300 (ficha)" in out and "entrada 200 (ficha)" in out and "una sola pasada" in out
    r = analyze(nc)
    assert r.cut_feeds == {300.0} and r.plunge_feeds == {200.0}


def test_numeric_tool_is_an_ad_hoc_diameter_and_equals_the_card(work, capsys):
    _, _, _, by_card = run(work, "--tool", "em4", "--thickness", "6", capsys=capsys)
    _, out, _, by_number = run(work, "--tool", "4", "--thickness", "6", "--feed", "300", "--plunge", "200",
                               capsys=capsys)
    assert body(by_card) == body(by_number) and "(linea de comandos)" in out


def test_command_line_beats_the_card(work, capsys):
    _, out, _, nc = run(work, "--tool", "multi", "--thickness", "6", "--feed", "450", "--stepdown", "6.4",
                        capsys=capsys)
    r = analyze(nc)
    assert r.cut_feeds == {450.0} and r.plunge_feeds == {200.0}
    assert "feed 450 (linea de comandos)" in out and "entrada 200 (ficha)" in out
    assert "pasadas de hasta 6.4 mm (linea de comandos)" in out


def test_stepdown_from_the_card_makes_several_passes_and_prints_the_rpm(work, capsys):
    _, out, _, nc = run(work, "--tool", "multi", "--thickness", "6", "--margin", "0.4", capsys=capsys)
    assert {"Z-3.2", "Z-6.4"} <= {m for m in re.findall(r"Z-[\d.]+", nc)}
    assert "pasadas de hasta 3.2 mm (ficha)" in out and "2 pasada(s)" in out
    assert "RPM aconsejadas (variador): 18000" in out
    assert "RPM aconsejadas [variador]: 18000" in nc


@pytest.mark.parametrize("args, fragment", [
    ((), "falta la fresa"),
    (("--tool", "em4"), "falta el grosor"),
    (("--tool", "sinfeed", "--thickness", "6"), "falta el avance de corte"),
    (("--tool", "sinfeed", "--thickness", "6", "--feed", "300"), "falta el avance de bajada"),
    (("--tool", "4", "--thickness", "6"), "falta el avance de corte"),
    (("--tool", "em5", "--thickness", "6"), "No hay ninguna ficha 'em5'"),
    (("--tool", "0", "--thickness", "6", "--feed", "1", "--plunge", "1"), "mayor que 0"),
    (("--tool", "em4", "--thickness", "0"), "--thickness debe ser mayor que 0"),
    (("--tool", "em4", "--thickness", "6", "--margin", "-1"), "--margin no puede ser negativo"),
    (("--tool", "em4", "--thickness", "6", "--feed", "0"), "--feed debe ser mayor que 0"),
    (("--tool", "em4", "--thickness", "6", "--stepdown", "0"), "--stepdown debe ser mayor que 0"),
    (("--tool", "em4", "--thickness", "6", "--safe-z", "1", "--clearance-z", "3"), "--clearance-z"),
    (("--tool", "em4", "--thickness", "6", "--portrait", "--rotate", "90"), "excluyentes"),
])
def test_missing_or_bad_values_stop_with_a_clear_message_and_no_file(work, capsys, args, fragment):
    code, _, err, nc = run(work, *args, capsys=capsys)
    assert code == 2 and fragment in err and nc is None


def test_missing_feed_message_points_to_the_card_field(work, capsys):
    _, _, err, _ = run(work, "--tool", "sinfeed", "--thickness", "6", capsys=capsys)
    assert "rellena 'feed' en la ficha" in err


def test_missing_svg_message(work, capsys):
    with pytest.raises(SystemExit) as e:
        svg_a_gcode.main(["--tool", "em4", "--no-config"])
    assert e.value.code == 2 and "falta el archivo SVG" in capsys.readouterr().err


def test_missing_tools_file_is_reported(work, capsys):
    (work / "herramientas.json").unlink()
    code, _, err, _ = run(work, "--tool", "em4", "--thickness", "6", capsys=capsys)
    assert code == 2 and "No existe" in err


def test_broken_tools_file_is_reported_with_its_card(work, capsys):
    (work / "herramientas.json").write_text('{"herramientas": {"em4": {"diametro": 4, "fed": 300}}}')
    code, _, err, _ = run(work, "--tool", "em4", "--thickness", "6", capsys=capsys)
    assert code == 2 and "ficha 'em4'" in err and "Quisiste decir 'feed'" in err


def test_v_bit_card_is_refused_with_a_readable_error_not_a_traceback(work, capsys):
    code, _, err, nc = run(work, "--tool", "v90", "--thickness", "6", capsys=capsys)
    assert code == 1 and err.startswith("ERROR:") and "solo las end mill" in err and nc is None


def test_flute_too_short_is_refused_with_a_readable_error(work, capsys):
    code, _, err, nc = run(work, "--tool", "corta", "--thickness", "6", capsys=capsys)
    assert code == 1 and err.startswith("ERROR:") and "longitud util" in err and nc is None
    code, _, _, nc = run(work, "--tool", "corta", "--thickness", "4", "--margin", "0.5", capsys=capsys)
    assert code == 0 and nc            # 4,5 mm <= 5 mm de filo: cabe


# --- archivo de configuracion ---------------------------------------------------------------------
def test_config_values_are_used_and_the_run_says_which_file(work, capsys):
    cfg = 'tool = "em4"\nthickness = 6\nsafe_z = 8.0\nclearance_z = 2.0\nhome_at_end = true\n'
    code, out, _, nc = run(work, config=cfg, capsys=capsys)
    assert code == 0 and "Configuracion:" in out and "c.toml" in out
    assert "G0 Z8" in nc and "G0 Z2" in nc and "$H" in nc


def test_command_line_beats_config_beats_default(work, capsys):
    base = ["--tool", "em4", "--thickness", "6"]
    _, _, _, default = run(work, *base, capsys=capsys)
    _, _, _, from_cfg = run(work, *base, config="safe_z = 8.0\n", capsys=capsys)
    _, _, _, from_cli = run(work, *base, "--safe-z", "7", config="safe_z = 8.0\n", capsys=capsys)
    assert "G0 Z5" in default and "G0 Z8" in from_cfg and "G0 Z7" in from_cli


def test_boolean_config_can_be_turned_off_from_the_command_line(work, capsys):
    base = ["--tool", "em4", "--thickness", "6"]
    _, _, _, on = run(work, *base, config="home_at_end = true\n", capsys=capsys)
    _, _, _, off = run(work, *base, "--no-home-at-end", config="home_at_end = true\n", capsys=capsys)
    assert "$H" in on and "$H" not in off


def test_no_config_ignores_the_file(work, capsys):
    (work / "c.toml").write_text("safe_z = 8.0\n")
    code, out, _, nc = run(work, "--tool", "em4", "--thickness", "6", "--config", str(work / "c.toml"),
                           "--no-config", capsys=capsys)
    assert code == 0 and "G0 Z5" in nc and "Configuracion:" not in out


def test_dashes_or_underscores_and_numbers_for_tool_are_accepted_in_the_config(work, capsys):
    cfg = 'tool = 4\nthickness = 6\nfeed = 300\nplunge = 200\nsafe-z = 6\n'
    code, _, _, nc = run(work, config=cfg, capsys=capsys)
    assert code == 0 and "G0 Z6" in nc


def test_exclude_colors_from_config_and_command_line_are_added(work, capsys):
    base = ["--tool", "em4", "--thickness", "6"]
    _, out, _, _ = run(work, *base, config='exclude_color = "#ff0000"\n', capsys=capsys)
    assert "Se cortan 2 de 3 formas" in out
    code, out, err, _ = run(work, *base, "--exclude-color", "#000000", config='exclude_color = ["#ff0000"]\n',
                            capsys=capsys)
    assert code == 1 and "ERROR" in err and "Se cortan 0 de 3 formas" in out   # nada que cortar: error legible
    assert "#000000" in out and "#ff0000" in out        # se excluyen los dos colores


@pytest.mark.parametrize("cfg, fragment", [
    ("safe_zz = 5\n", "Quisiste decir 'safe_z'"),
    ("colour = 1\n", "opcion desconocida"),
    ('safe_z = "alto"\n', "debe ser un numero"),
    ("safe_z = true\n", "debe ser un numero"),
    ('home_at_end = "si"\n', "true o false"),
    ("order = \"al-azar\"\n", "debe ser uno de"),
    ("thickness = [6]\n", "debe ser un numero"),
    ("exclude_color = 3\n", "texto o una lista"),
    ("tool = true\n", "ficha o un diametro"),
    ("stock_color = 3\n", "debe ser texto"),
    ("out = \"x.nc\"\n", "opcion desconocida"),        # --out, --config y similares no son configurables
    ("config = \"x\"\n", "opcion desconocida"),
    ("esto no es toml [[[\n", "TOML invalido"),
])
def test_bad_config_stops_with_a_clear_message(work, capsys, cfg, fragment):
    code, _, err, nc = run(work, "--tool", "em4", "--thickness", "6", config=cfg, capsys=capsys)
    assert code == 2 and fragment in err and "c.toml" in err and nc is None


def test_explicit_config_that_does_not_exist_is_an_error(work, capsys):
    with pytest.raises(SystemExit) as e:
        svg_a_gcode.main([str(work / "dib.svg"), "--tools-file", str(work / "herramientas.json"), "--tool", "em4",
                          "--thickness", "6", "--config", str(work / "no.toml")])
    assert e.value.code == 2 and "No existe" in capsys.readouterr().err


def test_portrait_from_config_rotates_but_explicit_rotate_wins(work, capsys):
    base = ["--tool", "em4", "--thickness", "6", "--exclude-color", "#ff0000"]
    cfg = "portrait = true\n"
    _, out, _, turned = run(work, *base, config=cfg, capsys=capsys)
    _, _, _, flat = run(work, *base, capsys=capsys)
    _, out2, _, forced = run(work, *base, "--rotate", "0", config=cfg, capsys=capsys)
    assert "apaisado: se gira 90" in out and turned != flat
    assert body(forced) == body(flat) and "se gira" not in out2


def test_read_config_errors_and_find_config_priority(tmp_path):
    with pytest.raises(ConfigError, match="No existe"):
        read_config(tmp_path / "x.toml")
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert find_config(tmp_path / "a", tmp_path / "b") is None
    (tmp_path / "b" / CONFIG_NAME).write_text("margin = 1\n")
    assert find_config(tmp_path / "a", tmp_path / "b") == tmp_path / "b" / CONFIG_NAME
    (tmp_path / "a" / CONFIG_NAME).write_text("margin = 2\n")
    assert find_config(tmp_path / "a", tmp_path / "b") == tmp_path / "a" / CONFIG_NAME     # la actual manda
    (tmp_path / "a" / CONFIG_NAME).write_bytes(b"\xef\xbb\xbfmargin = 3\n")                  # BOM de Windows
    assert read_config(tmp_path / "a" / CONFIG_NAME) == {"margin": 3}


def test_config_is_picked_up_from_the_current_folder(work, capsys, monkeypatch):
    """Sin --config ni --no-config, se busca cnc_app.toml (aqui se devuelve el de la carpeta de trabajo)."""
    (work / CONFIG_NAME).write_text("safe_z = 8.0\n")
    monkeypatch.setattr(svg_a_gcode, "find_config", lambda cwd, script_dir: work / CONFIG_NAME)
    out = work / "o.nc"
    assert svg_a_gcode.main([str(work / "dib.svg"), "--tools-file", str(work / "herramientas.json"),
                             "--tool", "em4", "--thickness", "6", "-o", str(out)]) == 0
    assert "G0 Z8" in out.read_text()


# --- los archivos que se entregan -------------------------------------------------------------------
def test_shipped_config_is_valid_and_only_turns_on_auto_rotation_and_margin():
    cfg = read_config(ROOT / CONFIG_NAME)
    assert cfg == {"rotate": "auto", "margin": 0.4}
    svg_a_gcode.config_defaults(svg_a_gcode.build_parser(), cfg, CONFIG_NAME)


def test_every_commented_example_in_the_shipped_config_is_a_valid_option():
    """Los ejemplos comentados (# clave = valor) deben seguir siendo opciones reales del programa."""
    import tomllib

    lines = [ln[1:].strip() for ln in (ROOT / CONFIG_NAME).read_text(encoding="utf-8").splitlines()
             if re.match(r"# [a-z_]+ = ", ln)]
    assert len(lines) >= 10
    cfg = tomllib.loads("\n".join(re.sub(r"\s+#.*$", "", ln) for ln in lines))
    parsed = svg_a_gcode.config_defaults(svg_a_gcode.build_parser(), cfg, CONFIG_NAME)
    assert set(parsed) == set(k.replace("-", "_") for k in cfg)


def test_shipped_tools_with_shipped_config_reproduce_the_validated_job(tmp_path, capsys):
    """El flujo real: em4 + cnc_app.toml (giro auto, margen 0,4) == la linea completa que se comparo con la referencia."""
    a, b = tmp_path / "a.nc", tmp_path / "b.nc"
    cfg = ["--config", str(ROOT / CONFIG_NAME), "--tools-file", str(ROOT / "herramientas.json")]
    assert svg_a_gcode.main([str(REAL_SVG), "--tool", "em4", "--thickness", "6", "--stock-color", "#2b2a29",
                             *cfg, "-o", str(a)]) == 0
    assert svg_a_gcode.main([str(REAL_SVG), "--tool", "4", "--thickness", "6", "--margin", "0.4", "--feed", "300",
                             "--plunge", "200", "--stock-color", "#2b2a29", "--rotate", "90", "--no-config",
                             "-o", str(b)]) == 0
    assert body(a.read_text()) == body(b.read_text())
    a.read_text().encode("ascii")


def test_bridges_from_the_recipe_use_the_tab_options_of_the_command_line(work, capsys):
    (work / "r.toml").write_text('[[operacion]]\nnombre = "Piezas"\ntipo = "corte"\ntrazo = "#000000"\npasante = true\npuentes = true\n',
                                 encoding="utf-8")
    code, out, _, nc = run(work, "--tool", "em4", "--thickness", "6", "--margin", "0.2", "--receta", str(work / "r.toml"),
                           "--tab-height", "3", "--tab-width", "8", capsys=capsys)
    assert code == 0 and "Puentes: 2 en 1 forma(s), de 8 mm de ancho y 3 mm de alto" in out
    assert "Z-3 " in nc + " " and analyze(nc).ok                    # los puentes se quedan a 3 mm del fondo (6 - 3)
    code, out, _, nc = run(work, "--tool", "em4", "--thickness", "6", "--receta", str(work / "r.toml"), capsys=capsys)
    assert "de 6 mm de ancho y 2 mm de alto" in out
    code, out, err, __ = run(work, "--tool", "em4", "--thickness", "6", "--receta", str(work / "r.toml"), "--tab-width", "0",
                             capsys=capsys)
    assert code == 2 and "puentes" in err


def test_the_tab_options_can_be_set_in_the_config_file(work, capsys):
    (work / "r.toml").write_text('[[operacion]]\nnombre = "Piezas"\ntipo = "corte"\ntrazo = "#000000"\npasante = true\npuentes = true\n',
                                 encoding="utf-8")
    code, out, _, nc = run(work, "--tool", "em4", "--thickness", "6", "--receta", str(work / "r.toml"), capsys=capsys,
                           config="tab_width = 10\ntab_height = 1.5\n")
    assert code == 0 and "de 10 mm de ancho y 1.5 mm de alto" in out
