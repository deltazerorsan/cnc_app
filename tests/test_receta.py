"""Receta de trabajo (color -> operacion): lectura, errores, clasificacion y construccion de operaciones."""
import copy
import math
from pathlib import Path

import pytest
from shapely.geometry import LineString, Point, box

from cnc.model import ContourOp, DrillOp, PocketOp, Stock, Tool
from cnc.recipe import (
    RecipeError,
    RecipeOp,
    build_operations,
    classify,
    count_by_operation,
    cut_shapes,
    load_recipe,
    parse_recipe,
)
from cnc.svg_import import ImportedShape

DATA = Path(__file__).parent / "data"
EXAMPLE = DATA / "receta_ejemplo.toml"
RED, ORANGE, YELLOW, BLACK = "#ff0000", "#e63c24", "#ffcc00", "#000000"

STOCK = Stock(thickness=19, spoilboard_extra=0.4)
TOOL = Tool(diameter=4)


def shape(geom, stroke=None, fill=None, closed=True):
    return ImportedShape(shape=geom, closed=closed, stroke=stroke, fill=fill)


def square(x, y, size, **kw):
    return shape(box(x, y, x + size, y + size), **kw)


def circle(x, y, diameter, **kw):
    return shape(Point(x, y).buffer(diameter / 2, quad_segs=16), **kw)


def op(**kw):
    base = {"nombre": "op", "tipo": "corte", "trazo": RED}
    base.update(kw)
    return {k: v for k, v in base.items() if v is not None}                     # trazo=None quita el trazo


def recipe(*ops, **top):
    return parse_recipe({"operacion": list(ops), **top})


def close(points, expected, tol=1e-6):
    return len(points) == len(expected) and all(math.dist(p, q) < tol for p, q in zip(points, expected))


def build(shapes, rec, tool=TOOL, stock=STOCK, **kw):
    kw.setdefault("feed", 900)
    kw.setdefault("plunge", 300)
    return build_operations(shapes, rec, tool, stock, **kw)


# --- lectura ----------------------------------------------------------------------------------------
def test_shipped_example_parses_as_documented():
    r = load_recipe(EXAMPLE)
    assert r.ignore == [YELLOW] and r.source == "receta_ejemplo.toml"
    assert [(o.name, o.kind) for o in r.operations] == [
        ("Taladros de 5 mm", "vaciado"), ("Pozos de 3 mm", "vaciado"), ("Cortes pasantes", "corte")]
    drills, wells, cuts = r.operations
    assert (drills.fill, drills.stroke, drills.depth, drills.stepdown) == (BLACK, None, 10, 5.1)
    assert (drills.feed_xy, drills.feed_z, drills.stepover) == (600, 400, 0.6)       # el % pasa a fraccion
    assert (wells.stroke, wells.depth, wells.feed_xy, wells.feed_z) == (ORANGE, 3, 1200, 300)
    assert cuts.through and cuts.depth is None and cuts.side == "auto" and cuts.stepdown == 6.5


def test_a_cut_without_depth_is_through_but_a_pocket_needs_one():
    assert recipe(op()).operations[0].through
    with pytest.raises(RecipeError, match="falta 'profundidad'"):
        recipe(op(tipo="vaciado"))
    with pytest.raises(RecipeError, match="falta 'profundidad'"):
        recipe(op(tipo="taladro"))


def test_colors_are_normalised_and_matched_by_value_not_by_spelling():
    r = recipe(op(trazo="#FF0000", relleno="Black"))
    o = r.operations[0]
    assert (o.stroke, o.fill) == (RED, BLACK)
    assert o.matches(shape(box(0, 0, 1, 1), stroke="red", fill="rgb(0,0,0)"))
    assert not o.matches(shape(box(0, 0, 1, 1), stroke="red"))                    # con los dos, deben cumplirse ambos
    assert not o.matches(shape(box(0, 0, 1, 1), stroke="#fe0000", fill=BLACK))


@pytest.mark.parametrize("text", ["rojo", "#12345", "#12", "ff0000", "#GGGGGG", "rgb(1,2)", "255", "colorin"])
def test_a_misspelled_color_is_an_error_not_black(text):
    """svgelements convierte en negro lo que no entiende; en una receta eso cortaria todo lo negro del dibujo."""
    for key in ("trazo", "relleno"):
        with pytest.raises(RecipeError, match="no entiendo el color"):
            recipe(op(**{key: text}))
    with pytest.raises(RecipeError, match="no entiendo el color"):
        recipe(op(), ignorar=[text])


@pytest.mark.parametrize("text", ["black", "BLACK", "#000", "#000000", "rgb(0,0,0)", "rgb(0%, 0%, 0%)"])
def test_real_black_is_accepted_in_any_spelling(text):
    assert recipe(op(trazo=None, relleno=text)).operations[0].fill == BLACK


@pytest.mark.parametrize("data,message", [
    ({}, "hace falta al menos una operacion"),
    ({"operacion": []}, "hace falta al menos una operacion"),
    ({"operacion": [op()], "colores": 1}, "clave desconocida 'colores'"),
    ({"operacion": [op(profundidd=3)]}, "Quisiste decir 'profundidad'"),
    ({"operacion": [op(tipo="fresado")]}, "'tipo' debe ser uno de corte, vaciado, taladro"),
    ({"operacion": [{"nombre": "x", "tipo": "corte"}]}, "indica que formas son"),
    ({"operacion": [op(trazo="rojo")]}, "no entiendo el color"),
    ({"operacion": [op(trazo=5)]}, "debe ser un color"),
    ({"operacion": [op()], "ignorar": [RED]}, "esta en 'ignorar'"),
    ({"operacion": [op()], "ignorar": 5}, "'ignorar' debe ser una lista"),
    ({"operacion": [op(lado="derecha")]}, "'lado' debe ser uno de"),
    ({"operacion": [op(tipo="vaciado", profundidad=3, lado="fuera")]}, "'lado' solo vale en operaciones de corte"),
    ({"operacion": [op(stepover=40)]}, "'stepover' solo vale en vaciados"),
    ({"operacion": [op(tipo="vaciado", profundidad=3, stepover=140)]}, "hasta 100"),
    ({"operacion": [op(pasante=True, profundidad=3)]}, "se excluyen"),
    ({"operacion": [op(pasante="si")]}, "'pasante' debe ser true o false"),
    ({"operacion": [op(profundidad=0)]}, "mayor que 0"),
    ({"operacion": [op(profundidad="3")]}, "debe ser un numero"),
    ({"operacion": [op(avance=True)]}, "debe ser un numero"),
    ({"operacion": [op(avance=float("inf"))]}, "debe ser un numero"),
    ({"operacion": [op(), op(trazo=ORANGE)]}, "dos operaciones con el nombre 'op'"),
    ({"operacion": [op(nombre=5)]}, "'nombre' debe ser texto"),
    ({"operacion": [op()], "titulo": 3}, "'titulo' debe ser texto"),
])
def test_recipe_errors_say_what_and_where(data, message):
    with pytest.raises(RecipeError, match=message):
        parse_recipe(data, "mi_receta.toml")


def test_errors_name_the_recipe_and_the_operation():
    with pytest.raises(RecipeError) as e:
        parse_recipe({"operacion": [op(), op(nombre="Pozos", tipo="x", trazo=ORANGE)]}, "mi_receta.toml")
    assert "mi_receta.toml: operacion 2 ('Pozos')" in str(e.value)


def test_file_errors_become_recipe_errors(tmp_path):
    with pytest.raises(RecipeError, match="No existe el archivo de receta"):
        load_recipe(tmp_path / "no_existe.toml")
    bad = tmp_path / "mala.toml"
    bad.write_text("[[operacion]\nnombre = 1", encoding="utf-8")
    with pytest.raises(RecipeError, match="mala.toml: TOML invalido"):
        load_recipe(bad)


def test_parse_does_not_modify_its_input():
    data = {"operacion": [op(tipo="vaciado", profundidad=3, stepover=40)], "ignorar": [YELLOW]}
    before = copy.deepcopy(data)
    parse_recipe(data)
    assert data == before


# --- clasificacion ----------------------------------------------------------------------------------
def test_first_matching_operation_wins_and_the_rest_is_reported():
    r = recipe(op(nombre="negro", tipo="vaciado", relleno=BLACK, profundidad=5),
               op(nombre="rojo", trazo=RED),
               ignorar=[YELLOW])
    shapes = [
        circle(10, 10, 5, fill=BLACK, stroke=RED),      # encaja en las dos: gana la primera
        square(0, 0, 20, stroke=RED),
        square(0, 0, 5, stroke=YELLOW),                  # ignorada
        square(0, 0, 6, stroke="#00ff00"),               # sin operacion
        square(0, 0, 7, stroke=YELLOW, fill=BLACK),      # ignorar manda sobre cualquier operacion
    ]
    per_op, ignored, unmatched = classify(r, shapes)
    assert [len(g) for g in per_op] == [1, 1]
    assert [shapes.index(s) for s in ignored] == [2, 4]
    assert [shapes.index(s) for s in unmatched] == [3]
    assert count_by_operation(r, shapes) == {"negro": 1, "rojo": 1}
    assert [shapes.index(s) for s in cut_shapes(r, shapes)] == [0, 1]


# --- construccion de operaciones --------------------------------------------------------------------
def test_operations_come_out_in_recipe_order_not_drawing_order():
    r = recipe(op(nombre="taladros", tipo="taladro", trazo=None, relleno=BLACK, profundidad=10),
               op(nombre="pozos", tipo="vaciado", trazo=ORANGE, profundidad=3),
               op(nombre="cortes", trazo=RED))
    shapes = [square(0, 0, 50, stroke=RED), square(100, 0, 30, stroke=ORANGE), circle(200, 0, 4, fill=BLACK)]
    built = build(shapes, r)
    assert [type(o) for o in built.operations] == [DrillOp, PocketOp, ContourOp]
    assert built.counts == {"taladros": 1, "pozos": 1, "cortes": 1}


def test_values_come_from_the_operation_then_the_command_line_then_the_tool_card():
    card = Tool(diameter=4, feed_xy=1200, feed_z=250, stepdown=3, stepover=0.4)
    shapes = [square(0, 0, 50, stroke=RED)]

    own = build(shapes, recipe(op(avance=500, entrada=100, pasada=2)), tool=card, feed=900, plunge=300, stepdown=6).operations[0]
    assert (own.feed_xy, own.feed_z, own.stepdown) == (500, 100, 2)
    cli = build(shapes, recipe(op()), tool=card, feed=900, plunge=300, stepdown=6).operations[0]
    assert (cli.feed_xy, cli.feed_z, cli.stepdown) == (900, 300, 6)
    ficha = build(shapes, recipe(op()), tool=card, feed=None, plunge=None).operations[0]
    assert (ficha.feed_xy, ficha.feed_z, ficha.stepdown) == (1200, 250, 3)


def test_missing_feed_names_the_operation_and_the_ways_to_set_it():
    with pytest.raises(RecipeError, match=r"'Cortes' no tiene avance de corte.*'avance'.*en el trabajo.*ficha"):
        build([square(0, 0, 50, stroke=RED)], recipe(op(nombre="Cortes")), feed=None)
    with pytest.raises(RecipeError, match=r"'Cortes' no tiene avance de bajada.*en el trabajo"):
        build([square(0, 0, 50, stroke=RED)], recipe(op(nombre="Cortes")), plunge=None)


def test_without_any_stepdown_each_operation_is_a_single_pass():
    o = build([square(0, 0, 50, stroke=RED)], recipe(op())).operations[0]
    assert o.stepdown == pytest.approx(19.4)                                        # grosor + margen
    p = build([square(0, 0, 50, stroke=ORANGE)], recipe(op(tipo="vaciado", trazo=ORANGE, profundidad=3))).operations[0]
    assert p.stepdown == 3 and not p.through and p.depth == 3


def test_through_operations_use_thickness_plus_the_spoilboard_extra():
    o = build([square(0, 0, 50, stroke=RED)], recipe(op(pasante=True))).operations[0]
    assert o.through and o.depth is None


def test_depth_errors_are_attributed_to_the_operation():
    with pytest.raises(RecipeError, match="Operacion 'cortes': .*grosor"):
        build([square(0, 0, 50, stroke=RED)], recipe(op(nombre="cortes")), stock=Stock(thickness=0))


def test_auto_side_decides_piece_or_hole_by_nesting():
    r = recipe(op(lado="auto"))
    shapes = [square(0, 0, 100, stroke=RED), square(20, 20, 20, stroke=RED), square(60, 60, 10, stroke=RED)]
    sides = {o.shape.bounds[2] - o.shape.bounds[0]: o.side for o in build(shapes, r).operations}
    assert sides == {100: "outside", 20: "inside", 10: "inside"}
    forced = build(shapes, recipe(op(lado="fuera"))).operations
    assert {o.side for o in forced} == {"outside"}


def test_holes_are_cut_before_pieces_whatever_the_drawing_order():
    r = recipe(op())
    shapes = [square(0, 0, 100, stroke=RED), square(20, 20, 20, stroke=RED), square(200, 0, 50, stroke=RED)]
    assert [o.side for o in build(shapes, r).operations] == ["inside", "outside", "outside"]


def test_a_line_cut_accepts_open_paths_and_inside_outside_do_not():
    line = shape(LineString([(0, 0), (50, 0), (50, 30)]), stroke=RED, closed=False)
    ok = build([line], recipe(op(lado="linea"))).operations
    assert [o.side for o in ok] == ["on"]
    for side in ("fuera", "dentro"):
        with pytest.raises(RecipeError, match=rf"corta 'por {side}' una forma abierta.*lado = \"linea\""):
            build([line], recipe(op(lado=side)))


def test_open_shapes_are_rejected_in_pockets_and_drills():
    line = shape(LineString([(0, 0), (50, 0), (50, 30)]), stroke=ORANGE, closed=False)
    with pytest.raises(RecipeError, match=r"\(vaciado\) tiene una forma abierta.*cerca de X"):
        build([line], recipe(op(tipo="vaciado", trazo=ORANGE, profundidad=3)))
    with pytest.raises(RecipeError, match=r"\(taladro\) tiene una forma abierta"):
        build([line], recipe(op(tipo="taladro", trazo=ORANGE, profundidad=3)))


def test_pocket_islands_are_the_same_color_shapes_nested_inside():
    r = recipe(op(tipo="vaciado", trazo=ORANGE, profundidad=3))
    shapes = [square(0, 0, 100, stroke=ORANGE), square(20, 20, 30, stroke=ORANGE), square(30, 30, 10, stroke=ORANGE),
              square(200, 0, 40, stroke=ORANGE)]
    pockets = [o.shape for o in build(shapes, r).operations]
    by_size = {round(p.bounds[2] - p.bounds[0]): p for p in pockets}
    assert sorted(by_size) == [10, 40, 100]                      # la isla de 30 no se vacia: el cuadrado de 10 de su interior si
    assert len(by_size[100].interiors) == 1 and abs(by_size[100].area - (100 * 100 - 30 * 30)) < 1e-6
    assert len(by_size[10].interiors) == 0 and len(by_size[40].interiors) == 0


def test_pocket_uses_its_stepover_or_the_tool_card_one():
    shapes = [square(0, 0, 50, stroke=ORANGE)]
    mine = build(shapes, recipe(op(tipo="vaciado", trazo=ORANGE, profundidad=3, stepover=60)),
                 tool=Tool(diameter=4, stepover=0.4)).operations[0]
    card = build(shapes, recipe(op(tipo="vaciado", trazo=ORANGE, profundidad=3)), tool=Tool(diameter=4, stepover=0.4)).operations[0]
    assert mine.stepover == pytest.approx(0.6) and card.stepover == pytest.approx(0.4)


def test_drills_are_one_plunge_at_the_center_of_each_circle():
    r = recipe({"nombre": "t", "tipo": "taladro", "relleno": BLACK, "profundidad": 10, "pasada": 5})
    shapes = [circle(10, 10, 4, fill=BLACK), circle(50, 12, 4, fill=BLACK), circle(30, 11, 4, fill=BLACK)]
    built = build(shapes, r)
    (drill,) = built.operations
    assert isinstance(drill, DrillOp) and drill.peck == 5 and drill.depth == 10 and not drill.through
    assert close(sorted(drill.points), [(10, 10), (30, 11), (50, 12)])
    assert built.notes == []


def test_a_drill_circle_that_is_not_the_tool_diameter_is_flagged():
    r = recipe({"nombre": "t", "tipo": "taladro", "relleno": BLACK, "profundidad": 10})
    built = build([circle(10, 10, 4, fill=BLACK), circle(50, 10, 8, fill=BLACK)], r)
    assert len(built.notes) == 1
    assert "8.00 mm" in built.notes[0] and "4 mm" in built.notes[0] and 'tipo = "vaciado"' in built.notes[0]


def test_file_order_keeps_the_drawing_order_of_drills_and_pockets():
    r = recipe({"nombre": "t", "tipo": "taladro", "relleno": BLACK, "profundidad": 10})
    shapes = [circle(10, 10, 4, fill=BLACK), circle(50, 90, 4, fill=BLACK), circle(30, 11, 4, fill=BLACK)]
    ordered = build(shapes, r, order="far-to-near").operations[0]
    as_drawn = build(shapes, r, order="file").operations[0]
    assert close(as_drawn.points, [(10, 10), (50, 90), (30, 11)]) and not as_drawn.optimize_order
    assert close(ordered.points[:1], [(50, 90)])                                  # empieza por el mas alto
    assert ordered.optimize_order


def test_a_recipe_that_finds_nothing_to_cut_says_so():
    with pytest.raises(RecipeError, match="no encuentra ninguna forma que cortar"):
        build([square(0, 0, 10, stroke="#00ff00")], recipe(op()))


def test_unmatched_and_ignored_shapes_do_not_stop_the_job():
    shapes = [square(0, 0, 50, stroke=RED), square(0, 0, 5, stroke="#00ff00"), square(0, 0, 5, stroke=YELLOW)]
    built = build(shapes, recipe(op(), ignorar=[YELLOW]))
    assert (len(built.operations), built.ignored, len(built.unmatched)) == (1, 1, 1)


def test_empty_operations_are_skipped_but_counted_as_zero():
    r = recipe(op(nombre="pozos", tipo="vaciado", trazo=ORANGE, profundidad=3), op(nombre="cortes", trazo=RED))
    built = build([square(0, 0, 50, stroke=RED)], r)
    assert built.counts == {"pozos": 0, "cortes": 1} and len(built.operations) == 1


def test_recipe_op_dataclass_defaults():
    o = RecipeOp(name="a", kind="corte", stroke=RED)
    assert o.side == "auto" and not o.through and o.depth is None and o.matches(shape(box(0, 0, 1, 1), stroke="red"))
