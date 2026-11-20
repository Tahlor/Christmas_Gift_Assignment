"""Constraint-aware cycle draws: random search with an actual completion witness.

Every aligned n-1-year block is an edge decomposition of the complete
directed graph without loops. Archived years are immutable. Search the
remaining years before selecting today's matching, including lookback
restrictions across cycle boundaries and optional rolling coverage.
"""
import hashlib
import random
from web.assignment import AssignmentError, AssignmentInfeasibleError


class SearchBudgetExceeded(AssignmentError):
    """Search stopped without proving impossibility."""


def plan_cycle(family_ids, year, anchor_year, seed, history, lookback=1,
               rolling_k=None, preferences=None, priorities=None,
               node_budget=500_000):
    """Return current and remaining years of one feasible aligned cycle.

    The returned witness is not an official assignment for future years.
    Replan from archived actual mappings each year. In a stable roster,
    repeating any completed cycle is a feasible fallback at its boundary.
    Random order is seeded; maximize current-year soft preferences exactly
    within the search budget. Never silently relax a hard constraint.
    """
    ids = tuple(sorted(family_ids))
    roster = set(ids)
    n = len(ids)
    period = n - 1
    if n < 2 or len(roster) != n:
        raise AssignmentError("At least two distinct households are required.")
    if year < anchor_year:
        raise AssignmentError("Draw year must be on or after the cycle anchor.")
    if not 0 <= lookback <= n - 2:
        raise AssignmentInfeasibleError(
            f"Perpetual no-repeat lookback must be between 0 and {n-2} for {n} families.")
    if rolling_k is not None and rolling_k < period:
        raise AssignmentInfeasibleError(f"Rolling coverage needs at least {period} years.")
    start = anchor_year + ((year - anchor_year) // period) * period
    end = start + period
    past = {y: dict(m) for y, m in history.items() if y < year}
    used = {g: set() for g in ids}
    for y in range(start, year):
        m = past.get(y)
        if (m is None or set(m) != roster or set(m.values()) != roster
                or any(g == r for g, r in m.items())):
            raise AssignmentError(
                "An earlier year in this cycle has missing or incompatible history. "
                "Enter its actual assignments or explicitly start a new cycle.")
        for g, r in m.items():
            if r in used[g]:
                raise AssignmentInfeasibleError(
                    "Existing assignments repeat a recipient within this cycle; "
                    "the remaining years cannot complete it. Start a new cycle explicitly.")
            used[g].add(r)
    preferences = preferences or {}
    priorities = priorities or {}
    tiers = sorted({priorities.get(g, 0) for g in preferences},
                   key=lambda p: (p == 0, p))
    rng = random.Random(int.from_bytes(hashlib.sha256(
        f"lookahead:v1:{seed}:{year}:{ids}".encode()).digest(), "big"))
    nodes = 0
    failed = set()

    def tick():
        nonlocal nodes
        nodes += 1
        if nodes > node_budget:
            raise SearchBudgetExceeded(
                "The lookahead search reached its safety limit; feasibility is "
                "undetermined, not impossible. No assignments were changed.")

    def choices(y):
        available = {}
        for g in ids:
            forbidden = {g} | used[g]
            for py in range(y - lookback, y):
                r = past.get(py, {}).get(g)
                if r is not None:
                    forbidden.add(r)
            allowed = roster - forbidden
            if rolling_k is not None and y - rolling_k + 1 >= anchor_year:
                window = range(y - rolling_k + 1, y)
                # A rolling window cannot be certified when a past year is unknown.
                if any(py not in past or g not in past[py] for py in window):
                    raise AssignmentError(
                        "Rolling coverage cannot be verified across missing archived years. "
                        "Enter the history or explicitly restart the cycle.")
                seen = {past[py][g] for py in window}
                missing = roster - {g} - seen
                if len(missing) > 1:
                    return None
                if missing:
                    allowed &= missing
            if not allowed:
                return None
            available[g] = allowed
        return available

    def matchings(available):
        if not available:
            return
        order = list(ids)
        rng.shuffle(order)
        # Most constrained givers first, random ties and receiver order.
        order.sort(key=lambda g: len(available[g]))
        options = {}
        for g in order:
            options[g] = sorted(available[g])
            rng.shuffle(options[g])
        def visit(i, taken, mapping):
            tick()
            if i == n:
                yield dict(mapping)
                return
            g = order[i]
            for r in options[g]:
                if r in taken:
                    continue
                mapping[g] = r
                yield from visit(i + 1, taken | {r}, mapping)
                del mapping[g]
        yield from visit(0, set(), {})

    def score(mapping):
        return tuple(sum(mapping.get(g) == r for g, r in preferences.items()
                         if priorities.get(g, 0) == tier) for tier in tiers)

    def search(y):
        if y == end:
            return {}
        tick()
        # Memoize failed states by used edges and the chronological history
        # that could constrain later years. Past inputs are fixed.
        span = max(lookback, (rolling_k or 1) - 1)
        state = (y, tuple(tuple(sorted(used[g])) for g in ids),
                 tuple(tuple(past.get(py, {}).get(g) for g in ids)
                       for py in range(max(start, y-span), y)))
        if state in failed:
            return None
        available = choices(y)
        if not available:
            failed.add(state)
            return None
        candidates = matchings(available)
        if y == year and preferences:
            # Sort all current matchings by preference score, preserving random
            # order within ties. A lower score is tried only after higher-score
            # matchings have proved unable to finish the cycle.
            candidates = sorted(candidates, key=score, reverse=True)
        for mapping in candidates:
            past[y] = mapping
            for g, r in mapping.items():
                used[g].add(r)
            suffix = search(y + 1)
            for g, r in mapping.items():
                used[g].remove(r)
            del past[y]
            if suffix is not None:
                return {y: mapping, **suffix}
        failed.add(state)
        return None

    witness = search(year)
    if witness is None:
        raise AssignmentInfeasibleError(
            "No assignment can complete the current cycle with the archived "
            "pairings and selected no-repeat/coverage rules. Review the cycle "
            "anchor and history, or explicitly change a rule.")
    return witness
