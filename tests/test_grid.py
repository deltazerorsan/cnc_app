"""La rejilla del lienzo (nucleo sin Qt): el paso que sale con cada zoom, las lineas, los numeros."""
import math

import pytest

from cnc import grid as gr


# --- la serie 1-2-5 ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value, expected", [
    (1, True), (2, True), (5, True), (10, True), (20, True), (50, True), (0.5, True), (0.2, True), (0.05, True),
    (100, True), (1000, True), (3, False), (7, False), (25, False), (0.25, False), (15, False), (0, False), (-1, False)])
def test_which_steps_are_round(value, expected):
    assert gr.is_nice(value) is expected


@pytest.mark.parametrize("minimum, expected", [
    (0.7, 1), (1, 1), (1.01, 2), (2, 2), (2.0001, 5), (4.99, 5), (5, 5), (5.01, 10), (9.9, 10), (10, 10), (11, 20),
    (21, 50), (51, 100), (0.09, 0.1), (0.11, 0.2), (0.3, 0.5), (3333, 5000)])
def test_the_smallest_round_step_that_is_not_smaller_than_asked(minimum, expected):
    assert gr.nice_step(minimum) == pytest.approx(expected)


def test_a_minimum_that_is_a_round_step_plus_float_noise_still_gives_that_step():
    assert gr.nice_step(2 * (1 + 1e-13)) == 2 and gr.nice_step(0.1 + 0.2 - 0.2 + 1e-17) == pytest.approx(0.1)
    assert gr.nice_step(2 * (1 + 1e-9)) == 5                                  # pero una diferencia de verdad ya no cuenta


@pytest.mark.parametrize("bad", [0, -1, math.nan, math.inf])
def test_a_minimum_that_is_not_a_positive_number_is_refused(bad):
    with pytest.raises(ValueError):
        gr.nice_step(bad)


# --- el paso con cada zoom ----------------------------------------------------------------------------------------
def test_lines_never_get_closer_than_the_minimum_on_screen():
    for ppm in (0.01, 0.03, 0.1, 0.37, 1, 2.5, 7, 19, 80, 400):
        step = gr.step_for(ppm)
        assert step * ppm >= gr.MIN_PIXELS - 1e-9
        assert gr.is_nice(step)
        smaller = [s for s in (step / 2, step / 2.5, step / 2.0) if gr.is_nice(s) and s < step]
        assert all(s * ppm < gr.MIN_PIXELS for s in smaller)             # y es el menor paso redondo que lo cumple


def test_zooming_in_never_makes_the_step_bigger():
    steps = [gr.step_for(ppm) for ppm in (0.05 * 1.1 ** k for k in range(80))]
    assert steps == sorted(steps, reverse=True)


def test_a_fixed_step_gives_its_own_multiples_so_the_lines_fall_where_the_mouse_grid_does():
    for fixed in (1, 5, 7, 0.25, 12.5):
        for ppm in (0.05, 0.4, 1, 3, 25, 200):
            step = gr.step_for(ppm, fixed)
            assert step * ppm >= gr.MIN_PIXELS - 1e-6
            assert step / fixed == pytest.approx(round(step / fixed))
            if step > fixed:
                assert (step - fixed) * ppm < gr.MIN_PIXELS             # el menor multiplo que cumple
    assert gr.step_for(100, 10) == 10                                    # con sitio de sobra, el paso es el suyo
    assert gr.step_for(0.1, 10) == 140


def test_the_zoom_has_to_be_positive():
    for bad in (0, -2):
        with pytest.raises(ValueError):
            gr.step_for(bad)


# --- cada cuantas lineas hay un numero --------------------------------------------------------------------------
def test_numbered_lines_are_far_enough_apart_and_round():
    for ppm in (0.02, 0.1, 0.5, 1, 2, 5, 13, 40, 150):
        step = gr.step_for(ppm)
        ratio = gr.label_ratio(step, ppm)
        assert ratio in gr.RATIOS
        assert gr.is_nice(step * ratio)                                  # 5, 10, 20, 50... nunca 15 ni 25
        assert step * ratio * ppm >= gr.LABEL_PIXELS - 1e-9 or ratio == gr.RATIOS[-1]


def test_a_fixed_odd_step_numbers_every_few_lines():
    assert gr.label_ratio(7, 3) == 5                                     # 7 mm a 3 px/mm: 21 px, hay que saltar
    assert gr.label_ratio(7, 20) == 2                                    # con sitio, de dos en dos


@pytest.mark.parametrize("step, expected", [(1, 0), (10, 0), (0.5, 1), (0.1, 1), (0.25, 2), (0.05, 2), (0.001, 3), (2.5, 1)])
def test_decimals_that_a_step_needs(step, expected):
    assert gr.decimals(step) == expected


def test_labels_are_written_with_the_decimals_of_the_step_and_never_as_minus_zero():
    assert gr.label(100, 10) == "100" and gr.label(-50, 10) == "-50"
    assert gr.label(0.5, 0.1) == "0.5" and gr.label(1.25, 0.05) == "1.25"
    assert gr.label(-0.0000001, 1) == "0" and gr.label(-0.0, 0.1) == "0"


# --- las lineas --------------------------------------------------------------------------------------------------
def test_lines_fall_on_multiples_of_the_step_inside_the_view():
    g = gr.grid_for((-30, -5, 123, 77), 2.0)                             # 2 px/mm: paso 10
    assert g.step == 10
    xs = sorted(g.x_fine + g.x_label)
    assert xs == [-30, -20, -10, 0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120]
    ys = sorted(g.y_fine + g.y_label)
    assert ys == [0, 10, 20, 30, 40, 50, 60, 70]
    assert all(v % g.step == 0 for v in xs + ys)


def test_the_numbered_lines_are_every_ratio_lines_and_include_zero():
    g = gr.grid_for((-30, -5, 123, 77), 2.0)
    assert g.ratio == 5 and g.x_label == (0, 50, 100) and g.y_label == (0, 50)
    assert 0 in g.x_label and 0 in g.y_label
    assert set(g.x_fine).isdisjoint(g.x_label) and set(g.y_fine).isdisjoint(g.y_label)


def test_a_line_exactly_on_the_edge_of_the_view_counts():
    g = gr.grid_for((0, 0, 100, 100), 2.0)
    assert 0 in g.x_label and 100 in g.x_label and 0 in g.y_label and 100 in g.y_label


def test_no_float_drift_in_the_positions_of_a_small_step():
    g = gr.grid_for((0, 0, 3, 3), 400.0)                                 # paso 0,05
    assert g.step == pytest.approx(0.05)
    xs = sorted(g.x_fine + g.x_label)
    assert xs[:4] == [0.0, 0.05, 0.1, 0.15] and xs[-1] == 3.0 and len(xs) == 61


def test_a_fixed_step_view_marks_its_own_multiples():
    g = gr.grid_for((0, 0, 100, 50), 20.0, fixed=7.0)                    # 7 mm a 20 px/mm: 140 px, cabe
    assert g.step == 7
    assert sorted(g.x_fine + g.x_label) == [7.0 * i for i in range(15)]


def test_too_many_lines_means_none_rather_than_freezing():
    g = gr.grid_for((0, 0, 1e9, 1e9), 1.0)
    assert g.count == 0 and g.step > 0
    assert gr.grid_for((0, 0, 1000, 1000), 1.0).count > 0


def test_a_view_that_is_not_a_rectangle_is_refused():
    for bad in ((10, 0, 0, 5), (0, 10, 5, 0), (0, 0, math.nan, 5), (0, 0, 5, math.inf)):
        with pytest.raises(ValueError):
            gr.grid_for(bad, 1.0)


def test_the_count_of_lines_stays_small_at_any_zoom():
    for ppm in (0.01, 0.1, 1, 10, 100, 1000):
        view = (-123.4, 55.5, -123.4 + 2400 / ppm, 55.5 + 1300 / ppm)    # una ventana de 2400 x 1300 px
        g = gr.grid_for(view, ppm)
        assert 0 < g.count < 400
