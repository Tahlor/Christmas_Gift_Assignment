"""Safe, read-only test reports using the exact official draw candidate builder."""
import hashlib
import random
from collections import Counter

from web.assignment import AssignmentError, AssignmentInfeasibleError
from web.draw_engine import build_candidate


RANDOM_METHODS = {"uniform_random", "random_lookahead", "randomized_cycle"}


def _seed_rng(seed, label):
    digest = hashlib.sha256(f"dryrun:v2:{seed}:{label}".encode()).digest()
    return random.Random(int.from_bytes(digest[:16], "big"))


def _empty_matrix(ids):
    return {g: {r: 0 for r in ids if r != g} for g in ids}


def _constraints_history(history, year, n, lookback, rolling_k):
    horizon = max(lookback, (rolling_k - 1) if rolling_k else 0, n - 2)
    return {y: dict(mapping) for y, mapping in history.items()
            if year - horizon <= y < year}


def run_trial_report(family_ids, year, anchor_year, mode, seed, lookback,
                     coverage_k, enforce_coverage, history_by_year,
                     preferences=None, priorities=None, rotation_salt="",
                     official_exists=False, fixed_household_order=None,
                     fixed_offset_order=None, trials=100):
    """Independent randomized candidates for one and the same Christmas year.

    For locked deterministic schedules all trials necessarily produce the same
    draw. For random methods, independent seed-derived choices explore feasible
    alternatives using identical official rules and actual prior history.
    """
    ids = list(family_ids)
    matrix = _empty_matrix(ids)
    preferences = preferences or {}
    priorities = priorities or {}
    unique = set()
    failures = Counter()
    hits = 0
    successes = 0
    salt = rotation_salt
    for i in range(trials):
        trial_seed = (seed if i == 0 or mode not in RANDOM_METHODS
                      else f"{seed}:candidate:{i}")
        try:
            mapping, meta = build_candidate(
                ids, year, anchor_year, mode, trial_seed, lookback, coverage_k,
                enforce_coverage, history_by_year,
                preferences=preferences, priorities=priorities,
                rotation_salt=salt, official_exists=official_exists,
                fixed_household_order=fixed_household_order,
                fixed_offset_order=fixed_offset_order,
                rng=_seed_rng(seed, str(i)),
            )
            if mode in ("shuffled_rotation", "shuffled_repeating_rotation"):
                salt = meta["rotation_salt"]
        except (AssignmentError, AssignmentInfeasibleError) as exc:
            failures[str(exc)] += 1
            continue
        successes += 1
        unique.add(tuple(sorted(mapping.items())))
        hits += sum(mapping.get(g) == r for g, r in preferences.items())
        for giver, receiver in mapping.items():
            matrix[giver][receiver] += 1

    return {
        "num_runs": trials,
        "successful_runs": successes,
        "infeasible_runs": trials - successes,
        "repeat_violations": 0,  # Failed search is not evidence of a repeat.
        "total_assignments_generated": successes * len(ids),
        "distinct_mappings": len(unique),
        "distinct_pairs_seen": sum(bool(n) for row in matrix.values() for n in row.values()),
        "possible_pairs": len(ids) * (len(ids) - 1),
        "preferences_total": len(preferences),
        "requests_satisfied": hits,
        "failure_reasons": dict(failures),
        "matrix": matrix,
        "family_ids": ids,
    }


def run_projection_report(family_ids, year, anchor_year, mode, seed, lookback,
                          coverage_k, enforce_coverage, history_by_year,
                          preferences=None, priorities=None, rotation_salt="",
                          official_exists=False, fixed_household_order=None,
                          fixed_offset_order=None, years=100):
    """Follow one complete hypothetical future, with zero changes to real DB."""
    ids = list(family_ids)
    history = {y: dict(mapping) for y, mapping in history_by_year.items()}
    matrix = _empty_matrix(ids)
    failures = Counter()
    successes = 0
    hits = 0
    salt = rotation_salt
    actual_modes = ("shuffled_rotation", "shuffled_repeating_rotation")
    for y in range(year, year + years):
        try:
            mapping, meta = build_candidate(
                ids, y, anchor_year, mode, seed, lookback, coverage_k,
                enforce_coverage, history,
                preferences=preferences if y == year else {},
                priorities=priorities if y == year else {},
                rotation_salt=salt, official_exists=official_exists,
                fixed_household_order=fixed_household_order,
                fixed_offset_order=fixed_offset_order,
                rng=_seed_rng(seed, str(y)),
            )
        except (AssignmentError, AssignmentInfeasibleError) as exc:
            failures[f"{y}: {exc}"] += 1
            break
        history[y] = mapping
        if mode in actual_modes:
            salt = meta["rotation_salt"]
        successes += 1
        if y == year:
            hits = sum(mapping.get(g) == r for g, r in (preferences or {}).items())
        for g, r in mapping.items():
            matrix[g][r] += 1
    return {
        "num_runs": years,
        "successful_runs": successes,
        "infeasible_runs": int(bool(failures)),
        "not_simulated_years": max(0, years - successes - int(bool(failures))),
        "repeat_violations": 0,
        "total_assignments_generated": successes * len(ids),
        "distinct_mappings": None,
        "distinct_pairs_seen": sum(bool(n) for row in matrix.values() for n in row.values()),
        "possible_pairs": len(ids) * (len(ids) - 1),
        "preferences_total": len(preferences or {}),
        "requests_satisfied": hits,
        "failure_reasons": dict(failures),
        "matrix": matrix,
        "family_ids": ids,
    }
