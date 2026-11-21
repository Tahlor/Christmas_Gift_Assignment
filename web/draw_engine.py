"""Single assignment candidate builder shared by real drafts and read-only tests.

All methods go through the same validation and history boundaries. Only the
calling official route persists a completed draft/salt; this module is pure.
"""
import random
from web.assignment import (
    AssignmentError, AssignmentInfeasibleError, fixed_rotation,
    shuffled_repeating_rotation, randomized_coverage_cycle,
    predictable_rotation, uniform_random_derangement,
    validate_fixed_rotation_config, validate_mapping_against_history,
    validate_rules_feasibility
)
from web.continuity import find_boundary_conflicts, find_boundary_safe_salt
from web.requests import best_random_matching, choose_preferred_rotation_salt


def build_candidate(family_ids, year, anchor_year, mode, seed, lookback,
                    coverage_k, enforce_coverage, history_by_year,
                    preferences=None, priorities=None, rotation_salt="",
                    official_exists=False, fixed_household_order=None,
                    fixed_offset_order=None, rng=None,
                    max_rotation_candidates=10000):
    """Return (mapping, metadata) without creating records or changing settings.

    History is {year: {giver_id: receiver_id}} and must include all known
    confirmed records relevant to the requested constraints. Preferences are
    best-effort and cannot violate history/coverage. Randomness is injectable
    for reproducible tests; official mode uses system randomness.
    """
    ids = list(family_ids)
    n = len(ids)
    feasibility = validate_rules_feasibility(n, lookback, coverage_k, enforce_coverage)
    if not feasibility["is_feasible"]:
        raise AssignmentInfeasibleError(" ".join(feasibility["errors"]))
    preferences = preferences or {}
    priorities = priorities or {}
    history_by_year = history_by_year or {}
    rng = rng or random.SystemRandom()
    metadata = {"rotation_salt": rotation_salt, "fixed_household_order": None,
                "fixed_offset_order": None}
    disallowed = {}
    for g in ids:
        disallowed[g] = {
            prior[g] for y, prior in history_by_year.items()
            if year - lookback <= y < year and g in prior
        } if lookback else set()

    if enforce_coverage and mode in ("randomized_cycle", "uniform_random"):
        raise AssignmentInfeasibleError(
            "This method cannot guarantee coverage in every sliding window. "
            "Choose a rotation or random shuffle with lookahead, or turn off the guarantee."
        )

    if mode in ("shuffled_rotation", "shuffled_repeating_rotation"):
        salt = rotation_salt
        if salt:
            schedule = lambda target: shuffled_repeating_rotation(
                ids, target, anchor_year, seed, salt=salt)
            if find_boundary_conflicts(schedule, history_by_year, year, lookback):
                if official_exists:
                    raise AssignmentInfeasibleError(
                        "The saved official rotation conflicts with the imported historical "
                        "lookback. Published rotation cannot be silently reseeded.")
                salt = ""
        if not salt:
            if official_exists:
                raise AssignmentInfeasibleError(
                    "An official rotation already exists but its stable salt is missing. "
                    "Refusing to silently choose a new production schedule.")
            if preferences:
                salt = choose_preferred_rotation_salt(
                    ids, year, anchor_year, seed, history_by_year, lookback,
                    preferences, priorities, max_candidates=max_rotation_candidates, rng=rng)
            else:
                salt = find_boundary_safe_salt(
                    ids, year, anchor_year, seed, history_by_year, lookback,
                    max_candidates=max_rotation_candidates)
            if salt is None:
                raise AssignmentInfeasibleError(
                    "No shuffled rotation satisfied the imported historical boundary "
                    "within the searched candidates. This is not proof every possible "
                    "schedule is infeasible.")
        metadata["rotation_salt"] = salt
        mapping = shuffled_repeating_rotation(ids, year, anchor_year, seed, salt=salt)
    elif mode == "fixed_rotation":
        house = list(fixed_household_order) if fixed_household_order else sorted(ids)
        offsets = (list(fixed_offset_order) if fixed_offset_order
                   else list(range(1, n)))
        valid, issues = validate_fixed_rotation_config(house, offsets, ids)
        if not valid:
            raise AssignmentInfeasibleError(
                "Fixed rotation configuration error: " + " ".join(issues))
        schedule = lambda target: fixed_rotation(house, offsets, target, anchor_year)
        conflicts = find_boundary_conflicts(schedule, history_by_year, year, lookback)
        if conflicts:
            raise AssignmentInfeasibleError(
                "Fixed rotation violates the historical no-repeat boundary. "
                "Adjust the offsets, lookback, or method.")
        mapping = schedule(year)
        metadata["fixed_household_order"] = house
        metadata["fixed_offset_order"] = offsets
    elif mode == "predictable_rotation":
        schedule = lambda target: predictable_rotation(ids, target, anchor_year)
        if find_boundary_conflicts(schedule, history_by_year, year, lookback):
            raise AssignmentInfeasibleError(
                f"Predictable rotation for {year} violates the requested {lookback}-year lookback constraint at the historical boundary. Refusing to create assignments.")
        mapping = schedule(year)
    elif mode == "random_lookahead":
        from web.lookahead import plan_cycle
        mapping = plan_cycle(
            ids, year, anchor_year, seed, history_by_year, lookback,
            coverage_k if enforce_coverage else None,
            preferences, priorities)[year]
    elif mode == "randomized_cycle":
        mapping = randomized_coverage_cycle(ids, year, anchor_year, seed)
    elif mode == "uniform_random":
        mapping = (best_random_matching(ids, disallowed, preferences, priorities, rng=rng)
                   if preferences else uniform_random_derangement(
                       ids, disallowed, rng=rng))
    else:
        raise AssignmentError("Unknown assignment method: " + str(mode))

    if set(mapping) != set(ids) or set(mapping.values()) != set(ids) or any(
            giver == recipient for giver, recipient in mapping.items()):
        raise AssignmentError("Algorithm returned an invalid one-to-one household matching.")
    conflicts = validate_mapping_against_history(mapping, disallowed)
    if conflicts:
        raise AssignmentInfeasibleError(
            "This matching repeats a recipient within the requested historical lookback.")
    return mapping, metadata
