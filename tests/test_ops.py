"""Tests de taladrado y vaciado. El vaciado se verifica simulando el corte."""
import re

import pytest
from shapely.geometry import LinearRing, LineString, Point, Polygon, box
from shapely.ops import unary_union

from cnc.drill import drill_moves
from cnc.generate import generate_gcode
from cnc.model import ContourOp, DrillOp, Job, PocketOp, Stock, Tool
from cnc.pocket import pocket_area, plan_paths, pocket_moves
from cnc.toolpath import Move

R = 3.0  # radio de la fresa de 6 mm


def stock():
    return Stock(thickness=18.0, spoilboard_extra=0.2)


def cut_union(moves, level_z, radius=R):
    """Union de lo que corta la fresa moviendose en XY a la profundidad level_z."""
    x = y = z = None
    segs = []
    for m in moves:
        nx = m.x if m.x is not None else x
        ny = m.y if m.y is not None else y
        nz = m.z if m.z is not None else z
        if (
            not m.rapid
            and x is not None
            and z is not None
            and abs(z - level_z) < 1e-6
            and abs(nz - level_z) < 1e-6
            and (nx, ny) != (x, y)
        ):
            segs.append(LineString([(x, y), (nx, ny)]))
        x, y, z = nx, ny, nz
    return unary_union([s.buffer(radius, quad_segs=32) for s in segs])


def check_pocket(shape, depth=5.0, **kw):
    op = PocketOp(shape=shape, depth=depth, **kw)
    moves = pocket_moves(op, stock(), R, 5.0, 1.0)
    cut = cut_union(moves, -depth)
    reachable = shape.buffer(-R).buffer(R)
    # 1) cobertura: no queda material alcanzable sin cortar (margen 0,02 mm por los bordes)
    assert reachable.buffer(-0.02).difference(cut).area < 0.01
    # 2) sin sobrecorte: la fresa no se sale de la forma (ni entra en las islas)
    assert cut.difference(shape.buffer(0.01)).area < 1e-3
    return moves


# ---------- taladrado ----------

def test_drill_through_single_plunge_reaches_thickness_plus_extra():
    moves = drill_moves(DrillOp(points=[(10, 10)]), stock(), 5.0, 1.0)
    assert min(m.z for m in moves if m.z is not None) == pytest.approx(-18.2)
    assert sum(1 for m in moves if not m.rapid and m.feed) == 1


def test_drill_peck_has_equal_steps_and_retracts_every_peck():
    moves = drill_moves(DrillOp(points=[(10, 10)], peck=5.0), stock(), 5.0, 1.0)
    plunges = [m.z for m in moves if not m.rapid]
    assert plunges == pytest.approx([-4.55, -9.1, -13.65, -18.2])
    # tras cada picoteo sube a Z de despeje antes de seguir
    assert sum(1 for m in moves if m.rapid and m.z == 1.0 and m.x is None) >= 4


def test_drill_visits_points_nearest_first():
    pts = [(0, 0), (100, 0), (10, 0), (50, 0)]
    moves = drill_moves(DrillOp(points=pts), stock(), 5.0, 1.0)
    visited = [m.x for m in moves if m.x is not None]
    assert visited == [0, 10, 50, 100]


def test_drill_keeps_given_order_when_not_optimized():
    pts = [(0, 0), (100, 0), (10, 0)]
    moves = drill_moves(DrillOp(points=pts, optimize_order=False), stock(), 5.0, 1.0)
    assert [m.x for m in moves if m.x is not None] == [0, 100, 10]


def test_drill_blind_deeper_than_stock_is_rejected():
    with pytest.raises(ValueError):
        drill_moves(DrillOp(points=[(0, 0)], through=False, depth=20.0), stock(), 5.0, 1.0)


def test_drill_without_points_is_rejected():
    with pytest.raises(ValueError):
        drill_moves(DrillOp(points=[]), stock(), 5.0, 1.0)


# ---------- vaciado ----------

def test_pocket_rectangle_is_fully_cleared_without_gouging():
    check_pocket(box(0, 0, 100, 60))


def test_pocket_with_island_respects_the_island():
    island = box(40, 20, 60, 40)
    shape = Polygon(box(0, 0, 100, 60).exterior, [island.exterior])
    moves = check_pocket(shape)
    cut = cut_union(moves, -5.0)
    assert cut.intersection(island.buffer(-0.01)).area < 1e-3


def test_pocket_concave_shape_l():
    shape = Polygon([(0, 0), (80, 0), (80, 30), (30, 30), (30, 70), (0, 70)])
    check_pocket(shape)


def test_pocket_rotated_passes_still_cover():
    check_pocket(box(0, 0, 100, 60), strategy="zigzag", angle=90.0)
    check_pocket(box(0, 0, 100, 60), strategy="zigzag", angle=30.0)


@pytest.mark.parametrize("strategy", ["offset", "zigzag"])
@pytest.mark.parametrize("name,shape", [
    ("rectangulo", box(0, 0, 100, 60)),
    ("forma en L", Polygon([(0, 0), (80, 0), (80, 30), (30, 30), (30, 70), (0, 70)])),
    ("con dos islas", Polygon(box(0, 0, 100, 60).exterior, [box(20, 20, 35, 40).exterior, box(60, 15, 80, 45).exterior])),
    ("circulo", Point(50, 50).buffer(30, quad_segs=32)),
])
def test_both_pocket_strategies_clear_without_gouging(strategy, name, shape):
    check_pocket(shape, strategy=strategy)


def test_offset_pocket_is_nested_rings_cut_from_the_inside_out_and_ends_on_the_edge():
    paths = plan_paths(PocketOp(shape=box(0, 0, 40, 24), depth=3), 2.0)
    assert all(p[0] == p[-1] for p in paths) and len(paths) > 2                        # solo anillos cerrados
    lengths = [LinearRing(p).length for p in paths]
    assert lengths == sorted(lengths)                                                # cada anillo rodea al anterior
    assert min(x for x, _ in paths[-1]) == pytest.approx(2.0)                         # el ultimo es el borde (centro de la fresa)
    assert paths[0][0] == pytest.approx((11.6, 11.6))                                 # el primero empieza abajo a la izquierda, como en la referencia


def test_offset_pocket_with_an_island_never_jumps_across_the_island():
    """El zigzag saltaba de un lado al otro de la isla; los anillos paralelos al borde no necesitan saltos largos."""
    shape = Polygon(box(0, 0, 100, 60).exterior, [box(30, 15, 70, 45).exterior])
    op = PocketOp(shape=shape, depth=3)
    moves = pocket_moves(op, stock(), R, 5.0, 1.0)
    jumps = [m for m in moves if m.rapid and m.x is not None and m.z is None]       # G0 en XY entre trazos
    assert len(jumps) <= 4                                                           # una entrada por trazo continuo
    check_pocket(shape)


@pytest.mark.parametrize("diameter,stepover", [(5, 0.6), (6, 0.4), (7.5, 0.3)])
def test_a_hole_the_ring_already_clears_is_cut_by_the_ring_only(diameter, stepover):
    """Un agujero pequeno (hasta ~2 diametros de fresa) se hace con el anillo del borde: sin pasadas de desbaste dentro."""
    shape = Point(0, 0).buffer(diameter / 2, quad_segs=32)
    paths = plan_paths(PocketOp(shape=shape, depth=3, stepover=stepover), 2.0)
    assert len(paths) == 1 and paths[0][0] == paths[0][-1]
    swept = LineString(paths[0]).buffer(2.0, quad_segs=32)
    assert shape.buffer(-0.02).difference(swept).area < 1e-3                          # y deja todo el agujero limpio


def test_a_bigger_hole_needs_inner_rings_too():
    shape = Point(0, 0).buffer(10, quad_segs=32)
    assert len(plan_paths(PocketOp(shape=shape, depth=3, stepover=0.4), 2.0)) > 1


def test_a_pocket_with_thousands_of_rings_is_planned_and_not_a_recursion_error():
    """Un pozo grande con la fresa fina y el paso al 2 % pide mas de mil vueltas: antes se salia de la recursion de Python."""
    paths = plan_paths(PocketOp(shape=box(0, 0, 400, 300), depth=3, stepover=0.02), 2.0)
    assert len(paths) > 1500 and all(p[0] == p[-1] for p in paths)
    lengths = [LinearRing(p).length for p in paths]
    assert lengths == sorted(lengths)                                                # de dentro afuera, como siempre
    assert min(x for x, _ in paths[-1]) == pytest.approx(2.0)                         # y el ultimo sigue siendo el borde


def test_a_whole_board_pocket_with_a_thin_bit_plans_instead_of_crashing():
    """El caso real: un pozo de 682 x 1000 mm con la fresa de 3,175 mm y el paso al 10 % (mas de mil vueltas)."""
    op = PocketOp(shape=box(10, 8.5, 692, 1008.5), depth=0.2, stepover=0.1)
    moves = pocket_moves(op, Stock(thickness=18.0, spoilboard_extra=0.2), 1.5875, 5.0, 1.0)
    assert len([m for m in moves if not m.rapid and m.x is not None]) > 4000        # miles de tramos de corte, sin romperse


def test_unknown_pocket_strategy_is_rejected():
    with pytest.raises(ValueError, match="strategy desconocida"):
        plan_paths(PocketOp(shape=box(0, 0, 40, 40), depth=3, strategy="espiral"), 2.0)


def test_pocket_multiple_levels_reach_final_depth():
    moves = pocket_moves(PocketOp(shape=box(0, 0, 60, 40), depth=7.0, stepdown=2.0), stock(), R, 5.0, 1.0)
    plunge_zs = sorted({round(m.z, 6) for m in moves if not m.rapid and m.feed == 300.0}, reverse=True)
    assert plunge_zs == pytest.approx([-1.75, -3.5, -5.25, -7.0])
    # cada nivel queda completamente cortado
    for z in plunge_zs:
        cut = cut_union(moves, z)
        reachable = box(0, 0, 60, 40).buffer(-R).buffer(R)
        assert reachable.buffer(-0.02).difference(cut).area < 0.01


def test_pocket_stays_inside_work_area_of_tool_center():
    shape = box(0, 0, 100, 60)
    area = pocket_area(shape, R)
    moves = pocket_moves(PocketOp(shape=shape, depth=3.0), stock(), R, 5.0, 1.0)
    xs = [m.x for m in moves if m.x is not None]
    ys = [m.y for m in moves if m.y is not None]
    assert min(xs) >= area.bounds[0] - 1e-6 and max(xs) <= area.bounds[2] + 1e-6
    assert min(ys) >= area.bounds[1] - 1e-6 and max(ys) <= area.bounds[3] + 1e-6


def test_pocket_tool_too_big_is_rejected():
    with pytest.raises(ValueError):
        pocket_moves(PocketOp(shape=box(0, 0, 4, 4), depth=2.0), stock(), R, 5.0, 1.0)


def test_pocket_deeper_than_stock_is_rejected():
    with pytest.raises(ValueError):
        pocket_moves(PocketOp(shape=box(0, 0, 50, 50), depth=19.0), stock(), R, 5.0, 1.0)


@pytest.mark.parametrize("stepover", [0, -0.1, 1.5])
def test_pocket_invalid_stepover_is_rejected(stepover):
    with pytest.raises(ValueError):
        pocket_moves(PocketOp(shape=box(0, 0, 50, 50), depth=2.0, stepover=stepover), stock(), R, 5.0, 1.0)


# ---------- trabajo completo ----------

def test_mixed_job_generates_in_given_order():
    pocket = Polygon(box(25, 20, 95, 60).exterior, [box(50, 30, 70, 50).exterior])
    job = Job(
        stock=stock(),
        tool=Tool(diameter=6.0),
        operations=[
            DrillOp(points=[(10, 10), (110, 10)], peck=6.0),
            PocketOp(shape=pocket, depth=6.0),
            ContourOp(shape=box(0, 0, 120, 80), side="outside", stepdown=3.0),
        ],
    )
    r = generate_gcode(job)
    assert r.min_z == pytest.approx(-18.2)
    assert not r.warnings
    lines = [ln for ln in r.text.splitlines() if not ln.startswith("(")]
    # el primer movimiento XY es el primer taladro; el contorno exterior (Y=-3) viene al final
    first_xy = next(ln for ln in lines if re.search(r"\bX", ln))
    assert first_xy == "G0 X10 Y10"
    assert max(i for i, ln in enumerate(lines) if "Y-3" in ln) > max(
        i for i, ln in enumerate(lines) if "X25" in ln or "X28" in ln
    )
    assert all(ord(c) < 128 for c in r.text)


# ---------- zonas que la fresa no alcanza ----------

def test_warns_when_tool_cannot_reach_thick_areas():
    # mancuerna: dos cuadrados unidos por un cuello de 4 mm (mas estrecho que la fresa de 6)
    dumbbell = box(0, 0, 30, 30).union(box(30, 13, 50, 17)).union(box(50, 0, 80, 30))
    job = Job(stock(), Tool(diameter=6.0), [ContourOp(shape=dumbbell, side="inside", through=True)])
    r = generate_gcode(job)
    assert any("no se pueden cortar" in w for w in r.warnings)


def test_no_reach_warning_for_plain_rectangle_or_big_enough_tool():
    job = Job(stock(), Tool(diameter=6.0), [ContourOp(shape=box(0, 0, 100, 60), side="inside", through=True),
                                            PocketOp(shape=box(0, 0, 100, 60), depth=3.0)])
    assert not generate_gcode(job).warnings
    dumbbell = box(0, 0, 30, 30).union(box(30, 13, 50, 17)).union(box(50, 0, 80, 30))
    ok = Job(stock(), Tool(diameter=2.0), [ContourOp(shape=dumbbell, side="inside", through=True)])
    assert not generate_gcode(ok).warnings


# ---------- separacion entre piezas ----------

def two_pieces(gap, tool=6.0):
    ops = [ContourOp(shape=box(0, 0, 40, 40), side="outside"),
           ContourOp(shape=box(40 + gap, 0, 80 + gap, 40), side="outside")]
    return Job(stock(), Tool(diameter=tool), ops)


def test_pieces_too_close_for_the_tool_warn():
    r = generate_gcode(two_pieces(gap=4.0))
    assert any("no cabe entre ellas" in w and "4.0 mm" in w for w in r.warnings)


def test_pieces_far_enough_do_not_warn():
    assert not any("no cabe" in w for w in generate_gcode(two_pieces(gap=10.0)).warnings)
    assert not any("no cabe" in w for w in generate_gcode(two_pieces(gap=4.0, tool=3.0)).warnings)


def test_piece_inside_another_is_not_a_spacing_problem():
    ops = [ContourOp(shape=box(0, 0, 100, 100), side="outside"),
           ContourOp(shape=box(40, 40, 60, 60), side="outside")]
    r = generate_gcode(Job(stock(), Tool(diameter=6.0), ops))
    assert not any("no cabe" in w for w in r.warnings)
