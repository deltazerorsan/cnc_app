"""Las dos carpetas fijas (trabajos y G-code): cuando una ruta vale, por donde empieza una ventana y los mensajes."""
from pathlib import Path

import pytest

from cnc import folders as fo


# --- clean ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    ("D:\\Trabajos", "D:\\Trabajos"),
    ("  D:\\Trabajos  ", "D:\\Trabajos"),
    ('"D:\\Trabajos"', "D:\\Trabajos"),                 # «Copiar como ruta» del Explorador
    ("'D:\\Trabajos'", "D:\\Trabajos"),
    ('  "  D:\\Trabajos  "  ', "D:\\Trabajos"),
    ('""D:\\Trabajos""', "D:\\Trabajos"),
    ('"D:\\Mis "trabajos""', 'D:\\Mis "trabajos"'),        # solo se quitan las comillas de alrededor
    ('"D:\\Trabajos', '"D:\\Trabajos'),                  # una comilla suelta no es un par
    ('D:\\Trabajos"', 'D:\\Trabajos"'),
    ("\"D:\\Trabajos'", "\"D:\\Trabajos'"),              # comillas distintas: no son un par
    ('"', '"'),
    ('""', ""),
    ("''", ""),
    ("", ""),
    ("   ", ""),
    (None, ""),
])
def test_clean_strips_spaces_and_a_pair_of_quotes(text, expected):
    assert fo.clean(text) == expected


def test_the_two_kinds_and_their_names():
    assert fo.KINDS == (fo.JOBS, fo.GCODE) == ("trabajos", "gcode")
    assert set(fo.LABELS) == set(fo.HELP) == set(fo.KINDS)
    assert ".cncjob" in fo.LABELS[fo.JOBS] and ".nc" in fo.LABELS[fo.GCODE]


# --- problem --------------------------------------------------------------------------------------------------
def test_an_empty_path_is_not_configured_and_is_not_a_problem():
    assert fo.problem("") is None and fo.problem(None) is None and fo.problem("   ") is None
    assert fo.problem('""') is None


def test_an_existing_writable_folder_is_good_even_with_spaces_and_quotes(tmp_path):
    assert fo.problem(str(tmp_path)) is None
    assert fo.problem(f'  "{tmp_path}"  ') is None
    assert fo.problem(f"{tmp_path}/") is None


def test_the_probe_file_is_removed(tmp_path):
    assert fo.problem(str(tmp_path)) is None
    assert list(tmp_path.iterdir()) == []


def test_a_folder_that_does_not_exist_says_so_and_mentions_the_drive(tmp_path):
    why = fo.problem(str(tmp_path / "nada"))
    assert why == "no existe (¿está conectada la unidad?)."


def test_a_file_is_not_a_folder(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    assert fo.problem(str(f)) == "no es una carpeta."


@pytest.mark.parametrize("text", ["relativa", "sub/carpeta", "..\\fuera", "D:Trabajos", "\\Trabajos", "Trabajos\\x", "."])
def test_a_path_that_is_not_complete_is_rejected_whatever_the_current_folder(text):
    assert fo.problem(text, posix=False) == ("no es una ruta completa: tiene que empezar por la unidad, "
                                             "por ejemplo D:\\Trabajos.")


def test_a_windows_path_is_complete_and_then_only_missing(tmp_path):
    assert fo.problem("D:\\Trabajos") == "no existe (¿está conectada la unidad?)."
    assert fo.problem("D:/Trabajos") == "no existe (¿está conectada la unidad?)."
    assert fo.problem("c:\\") == "no existe (¿está conectada la unidad?)."
    assert fo.problem("\\\\servidor\\compartida\\x") == "no existe (¿está conectada la unidad?)."


def test_a_rooted_path_only_counts_where_it_is_a_complete_path(tmp_path):
    assert fo.problem(str(tmp_path), posix=True) is None
    assert fo.problem(str(tmp_path), posix=False) == ("no es una ruta completa: tiene que empezar por la unidad, "
                                                      "por ejemplo D:\\Trabajos.")


@pytest.mark.parametrize("text", ["D:\\a<b", "D:\\a>b", 'D:\\a"b', "D:\\a|b", "D:\\a?b", "D:\\a*b", "D:\\a\tb", "D:\\a\x00b",
                                  "D:\\a\x1fb", "D:\\a:b", "ab:c", "D:\\Trabajos:"])
def test_characters_windows_does_not_allow_are_rejected(text):
    assert fo.problem(text, posix=False) == "tiene caracteres que Windows no admite en una ruta."


def test_a_control_character_just_over_the_limit_is_still_rejected_but_a_space_is_not():
    assert fo.problem("D:\\a\x1fb") == "tiene caracteres que Windows no admite en una ruta."
    assert fo.problem("D:\\a b") == "no existe (¿está conectada la unidad?)."


def test_the_drive_colon_is_fine_and_a_drive_alone_is_not_a_complete_path():
    assert fo.problem("D:\\x") == "no existe (¿está conectada la unidad?)."
    assert fo.problem("D:", posix=False).startswith("no es una ruta completa")


def test_a_rooted_path_may_have_a_colon_where_it_is_allowed(tmp_path):
    odd = tmp_path / "a:b"
    odd.mkdir()
    assert fo.problem(str(odd), posix=True) is None


def test_a_folder_where_nothing_can_be_written_says_so(tmp_path, monkeypatch):
    seen = []

    def refuse(**kw):
        seen.append(kw)
        raise PermissionError("no")
    monkeypatch.setattr(fo.tempfile, "TemporaryFile", refuse)
    assert fo.problem(str(tmp_path)) == "no se puede escribir en ella."
    assert seen == [{"dir": Path(str(tmp_path))}]


@pytest.mark.parametrize("error", [OSError(28, "No queda espacio en el dispositivo"), OSError(13, "Acceso denegado"),
                                   PermissionError("no"), OSError("unidad de solo lectura")])
def test_any_os_error_when_writing_the_probe_means_it_cannot_be_written(tmp_path, monkeypatch, error):
    def refuse(**kw):
        raise error
    monkeypatch.setattr(fo.tempfile, "TemporaryFile", refuse)
    assert fo.problem(str(tmp_path)) == "no se puede escribir en ella."


def test_which_systems_accept_rooted_paths_is_decided_by_the_system_not_assumed(tmp_path, monkeypatch):
    class System:
        def __init__(self, name):
            self.name = name
    monkeypatch.setattr(fo, "os", System("nt"))
    assert fo.problem(str(tmp_path)).startswith("no es una ruta completa")        # en Windows «/x» no es completa
    monkeypatch.setattr(fo, "os", System("posix"))
    assert fo.problem(str(tmp_path)) is None


# --- start_folder ---------------------------------------------------------------------------------------------
def test_without_a_configured_folder_the_window_starts_in_the_fallback():
    assert fo.start_folder("", "C:\\dibujos") == ("C:\\dibujos", None)
    assert fo.start_folder(None, "C:\\dibujos") == ("C:\\dibujos", None)
    assert fo.start_folder("  ", "C:\\dibujos") == ("C:\\dibujos", None)
    assert fo.start_folder('""', "C:\\dibujos") == ("C:\\dibujos", None)


def test_a_good_configured_folder_wins_over_the_fallback_and_comes_back_clean(tmp_path):
    assert fo.start_folder(str(tmp_path), "otra") == (str(tmp_path), None)
    assert fo.start_folder(f'  "{tmp_path}"  ', "otra") == (str(tmp_path), None)


def test_a_bad_configured_folder_gives_the_fallback_and_the_reason_not_a_silent_swap(tmp_path):
    missing = str(tmp_path / "nada")
    start, why = fo.start_folder(missing, "otra")
    assert start == "otra" and why == fo.problem(missing) and why


# --- describe -------------------------------------------------------------------------------------------------
def test_describe_is_empty_when_the_folder_is_fine_or_not_configured(tmp_path):
    assert fo.describe(fo.JOBS, str(tmp_path)) == "" and fo.describe(fo.GCODE, "") == "" and fo.describe(fo.GCODE, None) == ""


def test_describe_names_the_folder_the_kind_and_the_reason(tmp_path):
    missing = str(tmp_path / "nada")
    assert fo.describe(fo.JOBS, f'"{missing}"') == f"La carpeta de trabajos ({missing}) no existe (¿está conectada la unidad?)."
    assert fo.describe(fo.GCODE, missing) == f"La carpeta de G-code ({missing}) no existe (¿está conectada la unidad?)."
