"""Unit tests for backend/track_scoring.py."""

import pytest

import track_scoring as ts


pytestmark = pytest.mark.unit


def test_extract_hint_flags_all_categories():
    """Parses direction/class hints into HintFlags boolean fields."""
    f_sdg = ts.extract_hint_flags("Down Carriage Siding SDG")
    assert f_sdg.is_siding is True
    assert f_sdg.is_running_line is False

    f_bay = ts.extract_hint_flags("Platform 3 Bay")
    assert f_bay.is_bay_or_term is True

    f_ul_dl = ts.extract_hint_flags("DL | Down Liverpool")
    assert f_ul_dl.is_ul_dl_named_line is True

    f_elec = ts.extract_hint_flags("Up Electric Line")
    assert f_elec.is_electric_or_sub is True

    f_slow2 = ts.extract_hint_flags("Down Slow 2")
    assert f_slow2.is_slow_2 is True

    f_sec_fast = ts.extract_hint_flags("Down Suburban DSB")
    assert f_sec_fast.is_secondary_fast is True

    f_run = ts.extract_hint_flags("Up Main (UM)")
    assert f_run.is_running_line is True

    f_empty = ts.extract_hint_flags(None)
    assert f_empty.nh_l == ""
    assert f_empty.tokens == frozenset()


def test_score_mileage_coverage_ranges():
    """Rewards strict containment, tolerates <=50m near-window, penalizes large gaps."""
    assert ts.score_mileage_coverage(1000.0, 2000.0, 1500.0) == 85.0
    assert ts.score_mileage_coverage(1000.0, 2000.0, 2010.0) == 35.0
    assert ts.score_mileage_coverage(1000.0, 2000.0, 2060.0) < 0.0
    assert ts.score_mileage_coverage(1000.0, 2000.0, 2500.0) == -80.0
    assert ts.score_mileage_coverage(1000.0, 2000.0, None) == 0.0
    assert ts.score_mileage_coverage(2000.0, 1000.0, 1500.0) == 85.0


def test_score_direction_alignment_up_down_reversible():
    """Scores NWR direction digit (1=Up, 2=Down, 3=Reversible) accurately."""
    assert ts.score_direction_alignment("1100", "up", None) == (70.0, False)
    assert ts.score_direction_alignment("3100", "up", None) == (25.0, False)
    assert ts.score_direction_alignment("2100", "up", None) == (-60.0, False)

    assert ts.score_direction_alignment("2100", "down", None) == (70.0, False)
    assert ts.score_direction_alignment("3100", "down", None) == (25.0, False)
    assert ts.score_direction_alignment("1100", "down", None) == (-60.0, False)

    assert ts.score_direction_alignment("3100", "reversible", None) == (70.0, True)
    assert ts.score_direction_alignment("1100", "reversible", "up") == (30.0, True)
    assert ts.score_direction_alignment("2100", "reversible", "up") == (10.0, True)
    assert ts.score_direction_alignment("2100", "reversible", "down") == (30.0, True)
    assert ts.score_direction_alignment("1100", "reversible", "down") == (10.0, True)
    assert ts.score_direction_alignment("1100", "reversible", None) == (20.0, True)
    assert ts.score_direction_alignment("9100", "reversible", None) == (0.0, True)
    assert ts.score_direction_alignment("1100", None, None) == (0.0, False)
    assert ts.score_direction_alignment("1100", "other", None) == (0.0, False)


def test_score_platform_and_line_seq_variants():
    """Tests platform 0, numbered platforms, lettered/numbered approach lines, and secondary suffixes."""
    # Platform 0 bay
    f_p0 = ts.extract_hint_flags("Platform 0")
    assert ts.score_platform_and_line_seq("3600", f_p0, "reversible", True, None, 0, 1000, 2000, 1500, None)[0] == 120.0
    assert ts.score_platform_and_line_seq("3601", f_p0, "reversible", True, None, 0, 1000, 2000, 1500, None)[0] == -55.0

    # Exact platform suffix match (3604) vs wrong suffix (3602)
    f_p4 = ts.extract_hint_flags("Platform 4")
    assert ts.score_platform_and_line_seq("3604", f_p4, "reversible", True, None, 4, 1000, 2000, 1500, None)[0] == 120.0
    assert ts.score_platform_and_line_seq("3602", f_p4, "reversible", True, None, 4, 1000, 2000, 1500, None)[0] == -55.0

    # Loop hint softer penalty (-15) on mismatched x5xx loop index
    f_loop = ts.extract_hint_flags("Up Loop")
    assert ts.score_platform_and_line_seq("3503", f_loop, "reversible", True, None, 2, 1000, 2000, 1500, None)[0] == -15.0

    # Numbered siding falling back to x5xx loop
    f_sdg = ts.extract_hint_flags("Down Siding 3 SDG")
    assert ts.score_platform_and_line_seq("1503", f_sdg, "up", False, None, 3, 1000, 2000, 1500, None)[0] == 25.0

    # Running line + platform_num prefers 00 suffix
    f_p1_um = ts.extract_hint_flags("P1: Up Main")
    assert ts.score_platform_and_line_seq("1100", f_p1_um, "up", False, None, 1, 1000, 2000, 1500, None)[0] == 5.0

    # Lettered approach line (A -> 01) & numbered approach line (L2 -> 02)
    f_let = ts.extract_hint_flags("U-A | Approach")
    s_let, exact = ts.score_platform_and_line_seq("1101", f_let, "up", False, None, None, 1000, 2000, 1500, None)
    assert s_let >= 125.0 and exact is True
    # Out-of-coverage-window lettered approach line (+40)
    s_let_far, _ = ts.score_platform_and_line_seq("1101", f_let, "up", False, None, None, 1000, 2000, 2500, None)
    assert s_let_far == 40.0
    # Mismatched numbered approach line (L3 vs 02)
    f_num = ts.extract_hint_flags("U-3L | Approach")
    assert ts.score_platform_and_line_seq("1102", f_num, "up", False, None, None, 1000, 2000, 1500, None)[0] < 0.0
    assert ts.score_platform_and_line_seq("1101", f_num, "up", False, None, None, 1000, 2000, 1500, None)[0] == -40.0

    # Secondary suffixes (Slow 2, Electric 52, Suburban 54, generic 50, xx01 slow)
    f_slow2 = ts.extract_hint_flags("Down Slow 2")
    assert ts.score_platform_and_line_seq("2202", f_slow2, "down", False, None, None, 1000, 2000, 1500, None)[0] == 25.0
    assert ts._score_secondary_running_suffix("2202", f_slow2, 2) == 25.0

    f_elec = ts.extract_hint_flags("Up Electric")
    assert ts.score_platform_and_line_seq("1152", f_elec, "up", False, None, None, 1000, 2000, 1500, None)[0] == 22.0
    assert ts._score_secondary_running_suffix("1152", f_elec, 52) == 22.0

    f_sub = ts.extract_hint_flags("Down Suburban DSB")
    assert ts.score_platform_and_line_seq("2154", f_sub, "down", False, None, None, 1000, 2000, 1500, None)[0] == 22.0
    assert ts._score_secondary_running_suffix("2154", f_sub, 54) == 22.0
    assert ts.score_platform_and_line_seq("2150", f_sub, "down", False, None, None, 1000, 2000, 1500, None)[0] == 20.0
    assert ts._score_secondary_running_suffix("2150", f_sub, 50) == 20.0

    f_plain = ts.extract_hint_flags("Up Relief")
    assert ts.score_platform_and_line_seq("1201", f_plain, "up", False, None, None, 1000, 2000, 1500, None)[0] == 5.0


def test_score_line_class_all_branches():
    """Exercises every Appendix A line-class branch in score_line_class and helpers."""
    assert ts.score_line_class("1100", ts.extract_hint_flags(None), False, None, False, 0, 100, 50, None) == (0.0, False)

    # Engine / Carriage
    f_eng = ts.extract_hint_flags("Engine Line")
    assert ts.score_line_class("1800", f_eng, False, None, False, 0, 100, 50, None)[0] == 55.0
    assert ts.score_line_class("1900", f_eng, False, None, False, 0, 100, 50, None)[0] == 35.0
    assert ts.score_line_class("1100", f_eng, False, None, False, 0, 100, 50, None)[0] == -25.0
    assert ts.score_line_class("1700", f_eng, False, None, False, 0, 100, 50, None)[0] == -20.0
    assert ts.score_line_class("1500", f_eng, False, None, False, 0, 100, 50, None)[0] == -10.0

    # Sidings
    f_sdg = ts.extract_hint_flags("Down Siding")
    assert ts.score_line_class("2901", f_sdg, False, None, False, 0, 100, 50, None)[0] == 55.0
    assert ts.score_line_class("2801", f_sdg, False, None, False, 0, 100, 50, None)[0] == 35.0
    assert ts.score_line_class("2100", f_sdg, False, None, False, 0, 100, 50, None)[0] == -25.0
    assert ts.score_line_class("2700", f_sdg, False, None, False, 0, 100, 50, None)[0] == -20.0
    assert ts.score_line_class("2500", f_sdg, False, None, False, 0, 100, 50, None)[0] == -10.0

    # Crossover
    f_xo = ts.extract_hint_flags("Main Crossover")
    assert ts.score_line_class("1700", f_xo, False, None, False, 0, 100, 50, None)[0] == 50.0
    assert ts.score_line_class("1100", f_xo, False, None, False, 0, 100, 50, None)[0] == -15.0

    # Dead-end
    f_de = ts.extract_hint_flags("Dead-End Spur")
    assert ts.score_line_class("3600", f_de, False, None, False, 0, 100, 50, None)[0] == 60.0
    assert ts.score_line_class("3900", f_de, False, None, False, 0, 100, 50, None)[0] == 55.0
    assert ts.score_line_class("3800", f_de, False, None, False, 0, 100, 50, None)[0] == 35.0
    assert ts.score_line_class("1100", f_de, False, None, False, 0, 100, 50, None)[0] == -25.0
    assert ts.score_line_class("3500", f_de, False, None, False, 0, 100, 50, None)[0] == 0.0

    # Bay / Terminal
    f_bay = ts.extract_hint_flags("Platform 1 Bay")
    assert ts.score_line_class("3601", f_bay, True, 1, False, 0, 100, 50, None)[0] == 140.0
    assert ts.score_line_class("1100", f_bay, False, 1, False, 0, 100, 50, None)[0] == -20.0
    assert ts.score_line_class("1700", f_bay, False, 1, False, 0, 100, 50, None)[0] == -45.0
    assert ts.score_line_class("1900", f_bay, False, 1, False, 0, 100, 50, None)[0] == -25.0
    assert ts.score_line_class("3500", f_bay, True, 1, False, 0, 100, 50, None)[0] == 0.0

    # Loops (goods loop, passenger loop, slow loop)
    f_gl = ts.extract_hint_flags("Down Goods Loop DGL")
    assert ts.score_line_class("2300", f_gl, False, None, False, 0, 100, 50, None)[0] == 65.0
    assert ts._score_loop_class("2500", "5", f_gl) == 55.0
    assert ts._score_loop_class("2200", "2", f_gl) == 5.0
    assert ts._score_loop_class("2100", "1", f_gl) == -20.0
    assert ts._score_loop_class("2600", "6", f_gl) == -60.0
    assert ts._score_loop_class("2800", "8", f_gl) == -25.0
    assert ts._score_loop_class("2400", "4", f_gl) == -10.0

    f_sl_loop = ts.extract_hint_flags("Down Slow Loop")
    assert ts._score_loop_class("2202", "2", f_sl_loop) == 55.0
    assert ts._score_loop_class("2100", "1", f_sl_loop) == -15.0

    # Goods / Freight
    f_gds = ts.extract_hint_flags("Up Goods")
    assert ts.score_line_class("1300", f_gds, False, None, False, 0, 100, 50, None)[0] == 55.0
    assert ts.score_line_class("1200", f_gds, False, None, False, 0, 100, 50, None)[0] == 25.0
    assert ts.score_line_class("1500", f_gds, False, None, False, 0, 100, 50, None)[0] == 10.0
    assert ts.score_line_class("1100", f_gds, False, None, False, 0, 100, 50, None)[0] == -20.0
    assert ts.score_line_class("1900", f_gds, False, None, False, 0, 100, 50, None)[0] == -15.0

    # Single line
    f_sgl = ts.extract_hint_flags("Single Line")
    assert ts.score_line_class("3400", f_sgl, True, None, False, 0, 100, 50, None)[0] == 70.0
    assert ts.score_line_class("1400", f_sgl, False, None, False, 0, 100, 50, None)[0] == 55.0
    assert ts.score_line_class("1100", f_sgl, False, None, False, 0, 100, 50, None)[0] == -10.0

    # Dedicated platform (no fast/slow/bay words)
    f_plat = ts.extract_hint_flags("Platform 3")
    assert ts.score_line_class("3503", f_plat, True, 3, False, 0, 100, 50, None)[0] == 75.0
    assert ts.score_line_class("3502", f_plat, True, 3, False, 0, 100, 50, None)[0] == 15.0
    assert ts.score_line_class("1100", f_plat, False, 3, False, 0, 100, 50, None)[0] == 15.0
    assert ts.score_line_class("1200", f_plat, True, 3, False, 0, 100, 50, None)[0] == 35.0
    assert ts.score_line_class("1200", f_plat, False, 3, False, 0, 100, 50, 60)[0] == 30.0
    assert ts.score_line_class("1200", f_plat, False, 3, False, 0, 100, 50, 120)[0] == 15.0
    assert ts.score_line_class("3400", f_plat, True, 3, False, 0, 100, 50, None)[0] == 15.0
    assert ts.score_line_class("3603", f_plat, True, 3, False, 0, 100, 50, None)[0] == -25.0
    assert ts.score_line_class("3803", f_plat, True, 3, False, 0, 100, 50, None)[0] == 5.0
    assert ts.score_line_class("3802", f_plat, True, 3, False, 0, 100, 50, None)[0] == -10.0
    assert ts.score_line_class("3900", f_plat, True, 3, False, 0, 100, 50, None)[0] == -30.0

    # Slow / Relief
    f_slow = ts.extract_hint_flags("Down Slow")
    assert ts.score_line_class("2200", f_slow, False, None, False, 0, 100, 50, None)[0] == 50.0
    assert ts.score_line_class("2100", f_slow, False, None, False, 0, 100, 50, None)[0] == -15.0
    assert ts.score_line_class("2600", f_slow, False, None, False, 0, 100, 50, None)[0] == -30.0
    assert ts.score_line_class("2900", f_slow, False, None, False, 0, 100, 50, None)[0] == -40.0
    assert ts.score_line_class("2500", f_slow, False, None, False, 0, 100, 50, None)[0] == 0.0

    # Fast / Main
    f_fast = ts.extract_hint_flags("Down Fast")
    assert ts.score_line_class("2100", f_fast, False, None, False, 0, 100, 50, None)[0] == 50.0
    assert ts.score_line_class("2200", f_fast, False, None, False, 0, 100, 50, None)[0] == -10.0
    assert ts.score_line_class("2600", f_fast, False, None, False, 0, 100, 50, None)[0] == -35.0
    assert ts.score_line_class("2804", f_fast, False, 4, False, 0, 100, 50, None)[0] == 5.0
    assert ts.score_line_class("2802", f_fast, False, 4, False, 0, 100, 50, None)[0] == -35.0
    assert ts.score_line_class("2700", f_fast, False, None, False, 0, 100, 50, None)[0] == -40.0
    assert ts.score_line_class("2900", f_fast, False, None, False, 0, 100, 50, None)[0] == -45.0
    assert ts.score_line_class("2500", f_fast, False, None, False, 0, 100, 50, None)[0] == 0.0

    # Unmatched generic hint
    assert ts.score_line_class("1100", ts.extract_hint_flags("Branch Chord"), False, None, False, 0, 100, 50, None) == (0.0, False)


def test_score_fallback_tiebreakers_and_end_to_end():
    """Tests vmax tiebreakers (<=80 slow, >=145 fast), signal penalties, and full scorer."""
    assert ts.score_fallback_tiebreakers("1100", False, "signal", 200) == 30.0
    assert ts.score_fallback_tiebreakers("1200", False, "signal", 75) == 25.0
    assert ts.score_fallback_tiebreakers("1700", False, "signal", None) == -45.0
    assert ts.score_fallback_tiebreakers("1900", False, "signal", None) == -15.0
    assert ts.score_fallback_tiebreakers("1100", True, "signal", 200) == 0.0

    # Down Fast vs Down Slow vs Siding
    s_df = ts.score_track_candidate("2100", "down", "Down Fast", 1000, 2000, 1500)
    s_ds = ts.score_track_candidate("2200", "down", "Down Fast", 1000, 2000, 1500)
    s_sdg = ts.score_track_candidate("2901", "down", "Down Fast", 1000, 2000, 1500)
    assert s_df > s_ds > s_sdg

    # Non-4-digit track_id returns coverage score + fallback without raising
    assert ts.score_track_candidate("99", "up", "Up Main", 1000, 2000, 1500) == 90.0

    # Platform + generic line class fallback (lines 630-637)
    f_pg = ts.extract_hint_flags("Platform 2 Fast")
    assert ts.score_line_class("1100", f_pg, False, 2, False, 0, 100, 50, None)[0] == 50.0
    f_p_only_other = ts.extract_hint_flags("Platform 2 Road")
    assert ts.score_line_class("1100", f_p_only_other, False, 2, False, 0, 100, 50, None)[0] == 15.0

    # No-hint fallback prefers primary 1100 over sidings
    assert ts.score_track_candidate("1100", None, None, 1000, 2000, 1500, asset_type="signal") > ts.score_track_candidate(
        "1901", None, None, 1000, 2000, 1500, asset_type="signal"
    )
