"""cnc.preview: lineas de corte, rapidos y taladros de cada operacion, sin Qt."""
import pytest
from shapely.geometry import Point, box

from cnc.model import ContourOp, DrillOp, PocketOp, Tool
from cnc.pipeline import JobSettings, plan_job
from cnc.preview import OpPreview, op_kind, preview_moves, preview_ops
from cnc.svg_import import ImportedShape
from cnc.toolpath import Move


def shp(geom, stroke="#ff0000", fill=None):
    return ImportedShape(shape=geom, closed=True, stroke=stroke, fill=fill)


def settings(**kw):
    base = dict(tool=Tool(diameter=4), thickness=6.0, margin=0.4, feed=900.0, plunge=300.0, rotate=0.0)
    base.update(kw)
    return JobSettings(**base)


def test_cuts_rapids_and_plunges_are_separated():
    moves = [Move(x=0, y=0, rapid=True), Move(z=5, rapid=True), Move(x=10, y=0, rapid=True), Move(z=1, rapid=True),
             Move(z=-3, feed=300), Move(x=20, y=0, feed=900), Move(x=20, y=10, feed=900), Move(z=-6, feed=300),
             Move(x=0, y=10, feed=900), Move(z=5, rapid=True), Move(x=50, y=50, rapid=True)]
    p = preview_moves(moves, 4, "pieza")
    assert p.index == 4 and p.kind == "pieza" and p.z_min == -6
    assert p.cuts == [[(10, 0), (20, 0), (20, 10), (0, 10)]]                      # sigue por encima del cambio de nivel
    assert p.rapids == [[(0, 0), (10, 0)], [(0, 10), (50, 50)]] and p.drills == [] and p.points == 4


def test_a_rapid_ends_the_polyline_and_a_new_cut_starts_another():
    moves = [Move(x=0, y=0, z=-2, rapid=False, feed=1), Move(x=5, y=0, feed=1), Move(z=3, rapid=True),
             Move(x=9, y=9, rapid=True), Move(z=-2, feed=1), Move(x=12, y=9, feed=1)]
    p = preview_moves(moves, 1)
    assert p.cuts == [[(0, 0), (5, 0)], [(9, 9), (12, 9)]]                         # el primer movimiento no tiene origen


def test_cuts_above_the_surface_are_not_cuts():
    moves = [Move(x=0, y=0, z=5, rapid=True), Move(x=10, y=0, z=3, feed=900)]
    assert preview_moves(moves).cuts == []


def test_drill_plunges_are_recorded_only_for_drills():
    moves = [Move(x=7, y=8, z=5, rapid=True), Move(z=1, rapid=True), Move(z=-3, feed=200), Move(z=1, rapid=True),
             Move(z=-6.4, feed=200), Move(z=5, rapid=True)]
    assert preview_moves(moves, 1, "taladro").drills == [(7, 8, -3), (7, 8, -6.4)]
    assert preview_moves(moves, 1, "pieza").drills == []


def test_op_kind_names_each_operation_type():
    sq = box(0, 0, 10, 10)
    assert [op_kind(o) for o in (ContourOp(sq, side="inside"), ContourOp(sq, side="outside"), ContourOp(sq, side="on"),
                                 PocketOp(sq, depth=2), DrillOp([(1, 1)]))] == ["hueco", "pieza", "linea", "vaciado", "taladro"]
    with pytest.raises(TypeError):
        op_kind(object())


def test_preview_ops_follow_the_plan_operation_by_operation():
    pieces = [shp(box(0, 0, 100, 60)), shp(box(20, 20, 40, 40))]
    plan = plan_job(pieces, [], settings())
    ops = preview_ops(plan)
    assert [o.kind for o in ops] == ["hueco", "pieza"] and [o.index for o in ops] == [1, 2]
    hole, piece = ops
    xs = [x for x, _ in piece.cuts[0]]
    assert min(xs) == pytest.approx(-2) and max(xs) == pytest.approx(102)          # la pieza se corta por fuera: radio de la fresa
    assert all(18 <= x <= 42 for c in hole.cuts for x, _ in c)                      # el hueco, por dentro de su forma
    assert hole.z_min == pytest.approx(-6.4) and piece.rapids                       # hay desplazamientos en rapido entre operaciones
    assert hole.points > 4 and piece.points > 4


def test_preview_of_a_failed_plan_is_empty():
    assert preview_ops(plan_job([], [], settings())) == []


def test_preview_matches_the_gcode_cut_length():
    """Lo previsualizado y lo generado son la misma trayectoria: mismo largo de corte."""
    from verificar_nc import analyze
    from math import dist
    plan = plan_job([shp(box(0, 0, 80, 50)), shp(Point(40, 25).buffer(8, quad_segs=16))], [], settings(stepdown=3.0))
    total = sum(dist(a, b) for o in preview_ops(plan) for c in o.cuts for a, b in zip(c, c[1:]))
    assert total == pytest.approx(analyze(plan.text).cut_length, rel=1e-6)


def test_drills_of_a_recipe_job_show_their_plunge_points():
    from cnc.recipe import parse_recipe
    rec = parse_recipe({"operacion": [{"nombre": "t", "tipo": "taladro", "relleno": "#000000", "profundidad": 4}]})
    plan = plan_job([shp(Point(10, 10).buffer(2, quad_segs=8), stroke=None, fill="#000000"),
                     shp(Point(40, 10).buffer(2, quad_segs=8), stroke=None, fill="#000000")], [], settings(), rec)
    (op,) = preview_ops(plan)
    assert op.kind == "taladro" and len(op.drills) == 2 and op.cuts == [] and isinstance(op, OpPreview)
    assert {round(x) for x, _, _ in op.drills} == {2, 32}                             # ya colocado: el borde del dibujo en el cero
    assert all(z == pytest.approx(-4) for _, _, z in op.drills)


def test_pocket_preview_has_rings_at_its_depth():
    from cnc.recipe import parse_recipe
    rec = parse_recipe({"operacion": [{"nombre": "p", "tipo": "vaciado", "trazo": "#ff0000", "profundidad": 3}]})
    (op,) = preview_ops(plan_job([shp(box(0, 0, 60, 40))], [], settings(), rec))
    assert op.kind == "vaciado" and len(op.cuts) >= 1 and op.z_min == pytest.approx(-3)
