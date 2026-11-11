import math

import pytest
from shapely.geometry import LineString, Point, Polygon

from cnc.svg_import import import_svg, nesting_levels, place, suggest_sides

HEAD = '<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '


def svg(body, w="200mm", h="100mm", vb="0 0 200 100"):
    return f'{HEAD}width="{w}" height="{h}" viewBox="{vb}">{body}</svg>'


def test_units_viewbox_and_y_flip():
    # rectangulo de 50 x 20 unidades en (10, 5) con viewBox 1 unidad = 1 mm
    r = import_svg(svg('<rect x="10" y="5" width="50" height="20" fill="none" stroke="#000"/>'))
    assert r.page_size == pytest.approx((200.0, 100.0))
    (s,) = r.shapes
    # Y hacia arriba: el borde superior del SVG (y=5) queda a 100-5 = 95 mm del fondo
    assert s.shape.bounds == pytest.approx((10, 100 - 25, 60, 100 - 5))


def test_viewbox_scale_in_hundredths_of_mm():
    r = import_svg(svg('<rect x="0" y="0" width="12000" height="8000"/>', "120mm", "80mm", "0 0 12000 8000"))
    assert r.shapes[0].shape.bounds == pytest.approx((0, 0, 120, 80))


def test_circle_area_within_tolerance():
    r = import_svg(svg('<circle cx="50" cy="50" r="10"/>'), tolerance=0.01)
    (s,) = r.shapes
    assert s.kind == "circle" and s.closed
    assert s.shape.area == pytest.approx(math.pi * 100, rel=0.002)
    # todos los vertices estan sobre la circunferencia (o ligeramente dentro)
    c = Point(50, 50)
    assert all(9.98 <= c.distance(Point(p)) <= 10.0001 for p in s.shape.exterior.coords)


def test_cubic_bezier_flattening_respects_tolerance():
    k = 0.5522847498  # cuarto de circunferencia de radio 30 con una cubica
    d = f"M 30 0 C 30 {30 * k} {30 * k} 30 0 30"
    tol = 0.01
    r = import_svg(svg(f'<path d="{d}" fill="none" stroke="#000"/>'), tolerance=tol)
    (s,) = r.shapes
    assert not s.closed and isinstance(s.shape, LineString)
    # el arco esta en un SVG con Y invertido: el centro (0, 0) del arco queda en (0, 100)
    c = Point(0, 100)
    devs = [abs(c.distance(Point(p)) - 30) for p in s.shape.coords]
    assert max(devs) < tol + 0.01  # tolerancia de aplanado + error propio de la cubica
    assert len(s.shape.coords) > 8


def test_bezier_points_are_the_ones_svgelements_gives():
    """Las curvas se calculan aqui (svgelements tarda diez veces mas en cada punto) y tiene que salir lo mismo hasta la
    ultima cifra: con una diferencia en la cifra 13 el desfase de la fresa ya pone vertices de mas o de menos y cambia el
    G-code de los dibujos con curvas."""
    import svgelements as se

    from cnc.svg_import import _bezier, _flatten_segment
    path = se.Path("M 3.1 4.7 C 30.3 -8.9 5.2 40.6 60.1 22.3 Q 80.7 -10.3 95.9 30.1 A 20 12 15 0 1 130 40 L 140 40 "
                   "C 141.37 52.811 163.09 47.3 170.513 61.07 Q 181.9 70.33 177.7 93.21")
    __, cubic, quadratic, arc, line, cubic2, quadratic2 = path
    for seg in (cubic, quadratic, cubic2, quadratic2):
        curve = _bezier(seg)
        for t in [k / 512 for k in range(513)] + [0.1, 0.3, 0.77]:
            assert curve(t) == (seg.point(t).x, seg.point(t).y), t
        assert curve(0.0) == (seg.start.x, seg.start.y) and curve(1.0) == (seg.end.x, seg.end.y)
    assert _bezier(arc) is None and _bezier(line) is None          # un arco lo sigue calculando svgelements
    out: list = []
    _flatten_segment(arc, 0.01, out)
    assert len(out) > 8 and out[-1] == pytest.approx((130, 40))


def test_arc_command():
    r = import_svg(svg('<path d="M 20 50 A 10 10 0 1 1 40 50 A 10 10 0 1 1 20 50 Z"/>'), tolerance=0.01)
    (s,) = r.shapes
    assert s.closed and s.shape.area == pytest.approx(math.pi * 100, rel=0.003)


def test_group_transform_is_applied():
    r = import_svg(svg('<g transform="translate(10 20) scale(2)"><rect x="0" y="0" width="5" height="5"/></g>'))
    # rect 5x5 -> 10x10 en (10, 20) -> en Y invertida: 100-30 .. 100-20
    assert r.shapes[0].shape.bounds == pytest.approx((10, 70, 20, 80))


def test_rotated_rect_becomes_polygon_with_same_area():
    r = import_svg(svg('<rect x="50" y="40" width="20" height="10" transform="rotate(30 50 40)"/>'))
    # svgelements convierte mm->px con una constante aproximada: error relativo ~5e-7 (0,00006 mm en 120 mm)
    assert r.shapes[0].shape.area == pytest.approx(200.0, rel=1e-5)


def test_css_classes_colors_and_layer():
    body = (
        '<defs><style type="text/css">.fil0{fill:none}.str0{stroke:#FF0000;stroke-width:1}</style></defs>'
        '<g id="Layer_1"><rect class="fil0 str0" x="0" y="0" width="10" height="10"/></g>'
        '<g inkscape:label="Corte"><circle cx="50" cy="50" r="5" fill="#00ff00"/></g>'
    )
    a, b = import_svg(svg(body)).shapes
    assert (a.stroke, a.fill, a.layer) == ("#ff0000", None, "Layer_1")
    assert (b.fill, b.layer) == ("#00ff00", "Corte")


def test_text_is_ignored_and_reported():
    r = import_svg(svg('<rect x="0" y="0" width="10" height="10"/><text x="5" y="5">nota</text>'))
    assert len(r.shapes) == 1 and r.ignored == {"texto": 1}
    assert "texto x1" in r.summary()


def test_hidden_elements_are_skipped():
    r = import_svg(svg('<rect x="0" y="0" width="10" height="10" style="display:none"/>'
                       '<rect x="20" y="0" width="10" height="10"/>'))
    assert len(r.shapes) == 1


def test_open_path_polyline_and_line():
    r = import_svg(svg('<polyline points="0,0 10,0 10,10" fill="none" stroke="#000"/>'
                       '<line x1="0" y1="50" x2="40" y2="50" stroke="#000"/>'))
    assert [s.closed for s in r.shapes] == [False, False]
    assert [s.kind for s in r.shapes] == ["polyline", "line"]
    assert all(isinstance(s.shape, LineString) for s in r.shapes)


def test_compound_path_splits_into_subpaths():
    d = "M 0 0 H 100 V 100 H 0 Z M 30 30 H 70 V 70 H 30 Z"
    r = import_svg(svg(f'<path id="marco" d="{d}"/>'))
    assert len(r.shapes) == 2
    assert [s.id for s in r.shapes] == ["marco-1", "marco-2"]
    assert nesting_levels(r.shapes) == [0, 1]


def test_duplicates_are_reported():
    r = import_svg(svg('<rect x="0" y="0" width="10" height="10"/><rect x="0" y="0" width="10" height="10"/>'))
    assert any("duplicada" in w for w in r.warnings)


def test_self_intersecting_shape_warns():
    r = import_svg(svg('<polygon points="0,0 10,10 10,0 0,10"/>'))
    assert any("se cruza" in w for w in r.warnings)


def test_accepts_bytes_and_file_path(tmp_path):
    text = svg('<rect x="0" y="0" width="10" height="10"/>')
    assert len(import_svg(text.encode()).shapes) == 1
    f = tmp_path / "a.svg"
    f.write_text(text)
    assert len(import_svg(f).shapes) == 1
    assert len(import_svg(str(f)).shapes) == 1


def test_place_origin_modes():
    r = import_svg(svg('<rect x="10" y="10" width="40" height="20"/>'))
    bl = place(r.shapes, "bbox_bottom_left")[0].shape.bounds
    assert bl == pytest.approx((0, 0, 40, 20))
    c = place(r.shapes, "center")[0].shape.bounds
    assert c == pytest.approx((-20, -10, 20, 10))
    assert place(r.shapes, "page_bottom_left")[0].shape.bounds == pytest.approx(r.shapes[0].shape.bounds)
    with pytest.raises(ValueError):
        place(r.shapes, "otro")


def test_nesting_and_suggested_sides():
    body = (
        '<rect x="0" y="0" width="100" height="100"/>'            # exterior
        '<circle cx="50" cy="50" r="30"/>'                        # hueco
        '<rect x="45" y="45" width="10" height="10"/>'            # isla dentro del hueco
        '<line x1="0" y1="0" x2="10" y2="0" stroke="#000"/>'      # abierta
    )
    shapes = import_svg(svg(body)).shapes
    assert nesting_levels(shapes) == [0, 1, 2, None]
    assert suggest_sides(shapes) == ["outside", "inside", "outside", "on"]


def test_duplicates_do_not_count_as_nesting():
    shapes = import_svg(svg('<rect x="0" y="0" width="10" height="10"/>' * 2)).shapes
    assert nesting_levels(shapes) == [0, 0]


def test_sample_file_summary():
    r = import_svg("ejemplo_placa.svg")
    assert r.page_size == pytest.approx((120.0, 80.0))
    assert len(r.shapes) == 7 and r.ignored == {"texto": 1}
    assert r.shapes[0].shape.bounds == pytest.approx((0, 0, 120, 80))


def test_unitless_size_uses_ppi():
    body = '<rect x="0" y="0" width="720" height="360"/>'
    text = f'{HEAD}width="720" height="360">{body}</svg>'
    assert import_svg(text).page_size == pytest.approx((190.5, 95.25))              # 96 ppi
    assert import_svg(text, ppi=72.0).page_size == pytest.approx((254.0, 127.0))    # 72 ppi
    assert import_svg(text, ppi=72.0).shapes[0].shape.bounds == pytest.approx((0, 0, 254.0, 127.0))


# ---------- limpieza y reparacion (ruido tipico de algunos exportadores) ----------

def test_closing_tail_below_tolerance_is_merged():
    # el trazado termina a 0,0004 mm del inicio: no debe crear un cruce
    r = import_svg(svg('<path d="M 0 0 L 10 0 L 10 10 L 0 10 L 0.0002 0.0003 Z"/>'))
    (s,) = r.shapes
    assert s.shape.is_valid and s.shape.area == pytest.approx(100.0, rel=1e-4)
    assert not r.warnings


def test_tiny_self_crossing_is_repaired_with_warning():
    r = import_svg(svg('<path d="M 0 0 L 10 0 L 10 10 L 0 10 L 0 0.01 L 0.02 -0.01 Z"/>'))
    (s,) = r.shapes
    assert s.shape.is_valid and s.shape.area == pytest.approx(100.0, rel=1e-3)
    assert any("reparado" in w for w in r.warnings)


def test_metadata_and_title_are_not_reported_as_ignored():
    r = import_svg(svg('<title>x</title><metadata id="m"/><rect x="0" y="0" width="10" height="10"/>'))
    assert r.ignored == {}


def test_rotate_swaps_dimensions_around_center():
    from cnc.svg_import import rotate

    r = import_svg(svg('<rect x="10" y="10" width="40" height="20"/>'))
    b0 = r.shapes[0].shape.bounds
    b = rotate(r.shapes, 90)[0].shape.bounds
    assert (b[2] - b[0], b[3] - b[1]) == pytest.approx((20, 40))
    assert ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) == pytest.approx(((b0[0] + b0[2]) / 2, (b0[1] + b0[3]) / 2))
    assert rotate(r.shapes, 360)[0].shape.bounds == pytest.approx(b0)


def test_place_uses_reference_corner_as_origin():
    # el tablero (referencia) empieza en (5, 5): el cero debe quedar en SU esquina, no en la de las piezas
    r = import_svg(svg('<rect id="tablero" x="5" y="5" width="90" height="60" stroke="#000"/>'
                       '<rect id="pieza" x="20" y="30" width="10" height="10"/>'))
    board, piece = r.shapes
    moved = place([piece], "bbox_bottom_left", reference=[board])[0].shape.bounds
    assert moved == pytest.approx((piece.shape.bounds[0] - board.shape.bounds[0],
                                   piece.shape.bounds[1] - board.shape.bounds[1],
                                   piece.shape.bounds[2] - board.shape.bounds[0],
                                   piece.shape.bounds[3] - board.shape.bounds[1]))
