"""Check that a new repeating rotation respects known historical boundaries.

The steady-state n-1-year period guarantees rolling coverage on its own, but
history from a *different* system can conflict with more than the first new year.
This module checks every upcoming year that can still overlap recorded history.
"""
from typing import Callable, Dict, List, Optional, Tuple
from web.assignment import shuffled_repeating_rotation

History = Dict[int, Dict[int, int]]
Conflict = Tuple[int, int, int, int]  # new_year, giver, receiver, historical_year


def find_boundary_conflicts(
    schedule: Callable[[int], Dict[int, int]],
    history_by_year: History,
    start_year: int,
    lookback_years: int,
) -> List[Conflict]:
    """Compare all upcoming assignments to every known prior year within L years.

    Only known (confirmed) history should be supplied. Future years in one
    periodic schedule already avoid each other for L <= n-2.
    """
    if lookback_years <= 0 or not history_by_year:
        return []
    conflicts: List[Conflict] = []
    # After L future years, no historical year falls within the lookback.
    for new_year in range(start_year, start_year + lookback_years):
        proposed = schedule(new_year)
        for old_year, old_mapping in history_by_year.items():
            if old_year >= start_year or new_year - old_year > lookback_years:
                continue
            for giver, receiver in proposed.items():
                if old_mapping.get(giver) == receiver:
                    conflicts.append((new_year, giver, receiver, old_year))
    return conflicts


def find_boundary_safe_salt(
    family_ids: List[int],
    start_year: int,
    anchor_year: int,
    production_seed: str,
    history_by_year: History,
    lookback_years: int,
    max_candidates: int = 10000,
) -> Optional[str]:
    """Find the first deterministic stable rotation that respects the full boundary.

    Returns a salt string. It must be persisted once and reused for all
    subsequent official years; callers must NEVER automatically reseed later.
    """
    for candidate in range(max_candidates):
        salt = str(candidate)
        fn = lambda y: shuffled_repeating_rotation(
            family_ids, y, anchor_year, production_seed, salt=salt
        )
        if not find_boundary_conflicts(fn, history_by_year, start_year, lookback_years):
            return salt
    return None


def confirmed_history_by_year(db, start_year: int, lookback_years: int) -> History:
    """Read confirmed past pairings without exposing them in organizer HTML."""
    if lookback_years <= 0:
        return {}
    rows = db.execute(
        """SELECT year, giver_family_id, receiver_family_id FROM assignments
           WHERE year < ? AND year >= ? AND published = 1
             AND confidence = 'confirmed'""",
        (start_year, start_year - lookback_years),
    ).fetchall()
    history: History = {}
    for row in rows:
        history.setdefault(row["year"], {})[row["giver_family_id"]] = row["receiver_family_id"]
    return history
