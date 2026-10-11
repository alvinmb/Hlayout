"""Analytic checks of the thermal solver core (hlayout.core.thermal).

Every case here has a closed-form answer, so these test the physics of
the finite-volume assembly rather than pinning whatever it happens to
produce. No layout or GUI involved -- models are built by hand."""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")

from hlayout.core.thermal import (  # noqa: E402
    Part,
    ThermalError,
    ThermalModel,
    contour_segments,
    nice_contour_step,
    material_k,
    rgb_to_png,
    temps_to_rgb,
)


def _uniform_source(model, z, power_w, name="P"):
    mask = np.ones((model.ny, model.nx), dtype=bool)
    area_mm2 = model.nx * model.ny * (model.pitch_um / 1000.0) ** 2
    return model.add_part(Part(name, "resistor", power_w, z, mask, area_mm2))


# --- 1D conduction: rise = P * t / (k * A) ---------------------------------

def test_uniform_heat_through_a_single_slab_matches_p_t_over_k_a():
    m = ThermalModel(10, 10, 100.0, base_temp_c=25.0)
    # split into 4 slices so the heated top slice sits a known height up
    for _ in range(4):
        m.add_slice("sub", 24.0, 250.0)
    _uniform_source(m, 3, 1.0)
    r = m.solve()
    area = (10 * 100e-6) ** 2
    # heat is generated in the top slice's mid-plane: 875 um above the base
    expected = 25.0 + 1.0 * 875e-6 / (24.0 * area)
    top = r.slice_temps(3)
    assert np.allclose(top, expected, rtol=1e-6)
    assert r.energy_error < 1e-9


def test_two_layer_slab_is_the_series_sum_of_both_resistances():
    m = ThermalModel(8, 8, 125.0, base_temp_c=0.0)
    m.add_slice("substrate", 24.0, 635.0)
    m.add_slice("dielectric", 3.0, 30.0)
    m.add_slice("resistor", 2.0, 15.0)
    _uniform_source(m, 2, 0.5)
    r = m.solve()
    area = (8 * 125e-6) ** 2
    resistance = (635e-6 / 24.0 + 30e-6 / 3.0 + 7.5e-6 / 2.0) / area
    assert r.parts[0].t_max == pytest.approx(0.5 * resistance, rel=1e-6)
    # the thin dielectric contributes about a quarter of the rise here
    assert (30e-6 / 3.0) / (resistance * area) > 0.2


def test_a_missing_film_does_not_insulate_what_is_printed_above_it():
    """A dielectric slice that only exists on the left half must not
    leave the right half's resistor floating over an empty cell."""
    m = ThermalModel(4, 1, 100.0, base_temp_c=0.0)
    m.add_slice("substrate", 24.0, 600.0)
    t = np.zeros((1, 4)); t[0, :2] = 30.0
    m.add_slice("dielectric", 3.0, t)
    m.add_slice("resistor", 2.0, 15.0)
    left = np.array([[True, False, False, False]])
    right = np.array([[False, False, False, True]])
    m.add_part(Part("L", "resistor", 0.01, 2, left, 0.01))
    m.add_part(Part("R", "resistor", 0.01, 2, right, 0.01))
    r = m.solve()
    by = {p.ref_des: p for p in r.parts}
    assert math.isfinite(by["R"].t_max)
    assert by["L"].t_max > by["R"].t_max > 0.0      # dielectric adds resistance on the left
    assert r.energy_error < 1e-9
    assert np.isnan(r.slice_temps(1)[0, 3])         # no dielectric there


def test_heat_transfer_coefficient_base_adds_one_over_h_a():
    m = ThermalModel(5, 5, 200.0, base_mode="htc", base_htc=500.0, ambient_c=40.0)
    m.add_slice("substrate", 24.0, 635.0)
    _uniform_source(m, 0, 2.0)
    r = m.solve()
    area = (5 * 200e-6) ** 2
    expected = 40.0 + 2.0 * (1.0 / 500.0 + 317.5e-6 / 24.0) / area
    assert r.parts[0].t_avg == pytest.approx(expected, rel=1e-6)


def test_top_convection_takes_its_share_of_the_heat():
    m = ThermalModel(6, 6, 200.0, base_mode="htc", base_htc=1000.0,
                     ambient_c=25.0, top_htc=1000.0)
    m.add_slice("substrate", 400.0, 100.0)       # near-isothermal slab
    _uniform_source(m, 0, 1.0)
    r = m.solve()
    assert r.power_top == pytest.approx(r.power_base, rel=1e-3)
    assert r.power_out == pytest.approx(1.0, rel=1e-9)


# --- linearity, symmetry, coupling -----------------------------------------

def _two_source_model():
    m = ThermalModel(21, 11, 100.0, base_temp_c=25.0)
    for _ in range(3):
        m.add_slice("substrate", 24.0, 200.0)
    m.add_slice("resistor", 2.0, 15.0)
    a = np.zeros((11, 21), dtype=bool); a[4:7, 2:5] = True
    b = np.zeros((11, 21), dtype=bool); b[4:7, 16:19] = True
    m.add_part(Part("A", "resistor", 0.2, 3, a, 0.09))
    m.add_part(Part("B", "resistor", 0.2, 3, b, 0.09))
    return m


def test_a_mirrored_layout_gives_a_mirrored_map():
    r = _two_source_model().solve()
    top = r.slice_temps(2)
    assert np.allclose(top, top[:, ::-1], rtol=1e-9)
    a, b = r.parts
    assert a.t_max == pytest.approx(b.t_max, rel=1e-9)


def test_two_sources_equal_the_sum_of_each_alone():
    both = _two_source_model().solve().slice_temps(2) - 25.0
    only_a = _two_source_model(); only_a.parts[1].power_w = 0.0
    only_b = _two_source_model(); only_b.parts[0].power_w = 0.0
    ra = only_a.solve().slice_temps(2) - 25.0
    rb = only_b.solve().slice_temps(2) - 25.0
    assert np.allclose(both, ra + rb, rtol=1e-8, atol=1e-10)


def test_coupling_matrix_reproduces_a_fresh_solve_at_other_powers():
    m = _two_source_model()
    c = m.coupling()
    m.parts[0].power_w, m.parts[1].power_w = 0.5, 0.05
    fresh = {p.ref_des: p for p in m.solve().parts}
    again = c.temperatures({"A": 0.5, "B": 0.05})
    for name in ("A", "B"):
        assert again[name][0] == pytest.approx(fresh[name].t_max, rel=1e-8)
        assert again[name][1] == pytest.approx(fresh[name].t_avg, rel=1e-8)
    mat = c.matrix()
    assert mat[0][0] > mat[0][1] > 0.0             # self-heating beats the neighbour's
    assert mat[0][1] == pytest.approx(mat[1][0], rel=1e-8)


def test_iterative_and_direct_solvers_agree():
    direct = _two_source_model().solve()
    it = _two_source_model()
    it.DIRECT_LIMIT = 0                            # force conjugate gradient
    r = it.solve()
    assert r.max_temp == pytest.approx(direct.max_temp, rel=1e-6)
    assert r.energy_error < 1e-6


def test_grid_refinement_converges_for_a_small_source_on_a_plate():
    """Spreading from a 1 mm square source on a 10 mm alumina plate:
    halving the pitch must change the peak rise by less than 2%."""
    def peak(pitch):
        n = int(round(10000 / pitch))
        m = ThermalModel(n, n, pitch, base_temp_c=0.0)
        for _ in range(5):
            m.add_slice("substrate", 24.0, 127.0)
        m.add_slice("film", 2.0, 15.0)
        lo, hi = int(round(4500 / pitch)), int(round(5500 / pitch))
        mask = np.zeros((n, n), dtype=bool); mask[lo:hi, lo:hi] = True
        m.add_part(Part("S", "resistor", 1.0, 5, mask, 1.0))
        return m.solve().parts[0].t_max
    coarse, fine = peak(250.0), peak(125.0)
    assert abs(coarse - fine) / fine < 0.02
    # and it must sit between pure 1D conduction under the source and the
    # whole plate conducting uniformly
    one_d = 635e-6 / (24.0 * 1e-6)
    whole = 635e-6 / (24.0 * 1e-4)
    assert whole < fine < one_d + 15e-6 / (2 * 2.0 * 1e-6) + 1.0


# --- limits, results, errors ------------------------------------------------

def test_part_over_its_temperature_or_density_limit_is_flagged():
    m = ThermalModel(4, 4, 250.0, base_temp_c=25.0)
    m.add_slice("substrate", 24.0, 635.0)
    mask = np.ones((4, 4), dtype=bool)
    m.add_part(Part("R1", "resistor", 5.0, 0, mask, 1.0))
    hot = m.solve({"resistor_max_c": 30.0})
    assert not hot.parts[0].ok and hot.failures
    dense = m.solve({"resistor_max_c": 1e6, "resistor_max_w_mm2": 0.08})
    assert not dense.parts[0].ok
    assert dense.parts[0].density_w_mm2 == pytest.approx(5.0)
    fine = m.solve({"resistor_max_c": 1e6, "resistor_max_w_mm2": 10.0})
    assert fine.parts[0].ok


def test_temperature_at_and_surface_view():
    m = _two_source_model()
    r = m.solve()
    x, y = m.cell_center(5, 3)
    assert r.temperature_at((x, y)) == pytest.approx(r.slice_temps(3)[5, 3])
    assert math.isnan(r.temperature_at((-1.0, -1.0)))
    assert r.slice_index("resistor") == 3 and r.slice_index(-1) == 3
    assert any("hottest point" in line for line in r.summary_lines())


def test_no_heat_path_and_bad_setup_raise():
    m = ThermalModel(2, 2, 100.0, base_mode="htc", base_htc=0.0, top_htc=0.0)
    m.add_slice("substrate", 24.0, 635.0)
    with pytest.raises(ThermalError):
        m.solve()
    with pytest.raises(ThermalError):
        ThermalModel(0, 2, 100.0)
    with pytest.raises(ThermalError):
        material_k("unobtainium")
    assert material_k("alumina_96") == 24.0 and material_k(12) == 12.0


def test_nice_contour_step_fits_the_range_it_is_given():
    assert nice_contour_step(25.0, 27.46) == 0.5         # ~5 lines over 2.5 C
    assert nice_contour_step(25.0, 105.0) == 10.0
    assert nice_contour_step(25.0, 40.3) == 2.0
    assert nice_contour_step(25.0, 25.0) == 1.0          # flat field: any step, no crash
    for lo, hi in ((25.0, 27.46), (0.0, 0.013), (20.0, 400.0)):
        step = nice_contour_step(lo, hi)
        assert 4 <= (hi - lo) / step <= 10


def test_contours_and_png_helpers():
    g = np.fromfunction(lambda j, i: i * 1.0, (4, 6))
    segs = contour_segments(g, 2.5)
    assert len(segs) == 3 and all(abs(p[0] - 2.5) < 1e-9 for s in segs for p in s)
    g[1, 1] = np.nan
    assert contour_segments(g, 0.5)                # NaN cells skipped, not fatal
    rgb = temps_to_rgb(np.array([[0.0, np.nan], [1.0, 0.5]]))
    assert rgb.shape == (2, 2, 3) and tuple(rgb[1, 1]) == (255, 255, 255)
    png = rgb_to_png(rgb, scale=3)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and b"IEND" in png
