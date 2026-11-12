"""Regression tests for transitioning from imported previous-year data."""
from web.assignment import shuffled_repeating_rotation
from web.continuity import find_boundary_safe_salt, find_boundary_conflicts


def test_transitions_check_all_future_boundary_years_not_only_first():
    families = [1, 2, 3, 4, 5]
    history = {
        yr: shuffled_repeating_rotation(families, yr, 2023, "old-0")
        for yr in range(2023, 2026)
    }
    unsafe = shuffled_repeating_rotation(families, 2026, 2026, "newproduction", "11")
    assert all(unsafe[g] != history[old][g] for g in families for old in history)
    unsafe_fn = lambda y: shuffled_repeating_rotation(
        families, y, 2026, "newproduction", "11"
    )
    assert find_boundary_conflicts(unsafe_fn, history, 2026, 3)

    safe = find_boundary_safe_salt(families, 2026, 2026, "newproduction", history, 3)
    assert safe is not None
    fn = lambda y: shuffled_repeating_rotation(families, y, 2026, "newproduction", safe)
    assert not find_boundary_conflicts(fn, history, 2026, 3)

    for first in range(2026, 2050):
        for giver in families:
            assert {
                fn(year)[giver] for year in range(first, first + 4)
            } == set(families) - {giver}


def test_boundary_gaps_and_no_history_do_not_fabricate_conflicts():
    families = [1, 2, 3, 4, 5]
    history = {2024: {1: 2}}
    safe = find_boundary_safe_salt(families, 2026, 2026, "seed", history, 3)
    assert safe is not None
    fn = lambda y: shuffled_repeating_rotation(families, y, 2026, "seed", safe)
    assert not find_boundary_conflicts(fn, history, 2026, 3)
    assert find_boundary_conflicts(fn, history, 2026, 0) == []
