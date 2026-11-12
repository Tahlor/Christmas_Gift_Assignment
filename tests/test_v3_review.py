"""Independent V3 mathematical invariants, tested without touching production."""
from web.assignment import (
    shuffled_repeating_rotation, fixed_rotation, validate_rules_feasibility,
    validate_fixed_rotation_config,
)
import pytest


@pytest.mark.parametrize("seed", ["simple", "another seed", "test 99", "🎄"])
def test_every_sliding_four_year_window_has_full_coverage(seed):
    ids = [11, 22, 33, 44, 55]
    years = range(2000, 2050)
    history = {y: shuffled_repeating_rotation(ids, y, 2019, seed) for y in years}
    assert history == {y: shuffled_repeating_rotation(list(reversed(ids)), y, 2019, seed) for y in years}
    for y in range(2000, 2047):
        for giver in ids:
            receivers = [history[y+step][giver] for step in range(4)]
            assert len(set(receivers)) == 4, (seed, giver, y, receivers)
            assert set(receivers) == set(ids) - {giver}
            for gap in (1, 2, 3):
                assert receivers[0] != receivers[gap]


def test_custom_fixed_rotation_is_sliding_safe():
    order = [5, 4, 3, 2, 1]
    offsets = [3, 1, 4, 2]
    ok, issues = validate_fixed_rotation_config(order, offsets, order)
    assert ok, issues
    m = {y: fixed_rotation(order, offsets, y, 2014) for y in range(2000, 2042)}
    for year in range(2000, 2039):
        for giver in order:
            assert {m[y][giver] for y in range(year, year+4)} == set(order)-{giver}


def test_infeasible_rules_explained_for_five_families():
    v = validate_rules_feasibility(5, lookback_l=4, coverage_k=3, enforce_coverage=True)
    assert not v["is_feasible"]
    assert v["min_k"] == 4 and v["max_l"] == 3
    assert v["suggested_k"] == 4 and v["suggested_l"] == 3
    assert len(v["errors"]) >= 2
