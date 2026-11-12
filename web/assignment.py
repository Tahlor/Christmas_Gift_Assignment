"""Assignment algorithm module with mathematical coverage guarantees.

Pure functions implementing:
1. Predictable rotation over n-1 offsets (deterministic cyclic derangements).
2. Randomized full-coverage cycle (pseudorandom cycle-keyed derangements).
3. Uniform random derangement with recent history repeat avoidance (rejection sampling).
4. Cycle analysis, fairness metrics, and roster change detection.
"""

import hashlib
import random
from typing import Dict, List, Optional, Set, Tuple


class AssignmentError(Exception):
    """Base exception for assignment algorithm failures."""
    pass


class AssignmentInfeasibleError(AssignmentError):
    """Raised when no valid assignment can be found under constraints."""
    pass


def compute_roster_hash(family_ids: List[int]) -> str:
    """Compute deterministic SHA-256 fingerprint for a set of family IDs."""
    sorted_ids = sorted(family_ids)
    data = ",".join(str(i) for i in sorted_ids)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]


def predictable_rotation(
    family_ids: List[int],
    year: int,
    anchor_year: int
) -> Dict[int, int]:
    """Generate deterministic cyclic assignment.

    Guarantees:
    - No self-assignment.
    - Exactly one giver and one receiver per family.
    - Over any aligned block of (n - 1) consecutive years starting from
      anchor_year + C*(n - 1), every giver gives to every other family
      exactly once.
    """
    n = len(family_ids)
    if n < 2:
        raise AssignmentError("At least 2 active families are required for a gift exchange.")

    sorted_families = sorted(family_ids)
    cycle_length = n - 1
    # Offset k in 1 .. n-1
    k = ((year - anchor_year) % cycle_length) + 1

    assignments = {}
    for i, giver in enumerate(sorted_families):
        receiver_idx = (i + k) % n
        assignments[giver] = sorted_families[receiver_idx]

    return assignments


def randomized_coverage_cycle(
    family_ids: List[int],
    year: int,
    anchor_year: int,
    cycle_secret: str
) -> Dict[int, int]:
    """Generate pseudorandom assignment with mathematical full-coverage guarantee per cycle.

    Guarantees:
    - Permutes the roster order and offset order deterministically per cycle index C.
    - No self-assignment.
    - Over an aligned block of (n - 1) years for cycle C:
      anchor_year + C*(n-1) to anchor_year + (C+1)*(n-1) - 1,
      each giver gives to every other family exactly once.
    - When roster changes, the cycle guarantee resets.
    """
    n = len(family_ids)
    if n < 2:
        raise AssignmentError("At least 2 active families are required for a gift exchange.")

    sorted_families = sorted(family_ids)
    cycle_length = n - 1
    cycle_index = (year - anchor_year) // cycle_length
    step_in_cycle = (year - anchor_year) % cycle_length  # 0 .. n-2

    # Derive deterministic PRNG seed for this cycle index and stable roster
    seed_key = f"cycle:{cycle_secret}:{cycle_index}:{sorted_families}"
    seed_hash = hashlib.sha256(seed_key.encode("utf-8")).digest()
    cycle_seed = int.from_bytes(seed_hash[:8], byteorder="big")
    rng = random.Random(cycle_seed)

    # Deterministically permute the family sequence for this entire cycle
    permuted_families = list(sorted_families)
    rng.shuffle(permuted_families)

    # Deterministically permute all valid non-zero offsets [1, 2, ..., n-1]
    offsets = list(range(1, n))
    rng.shuffle(offsets)

    # Selected shift for this year
    shift = offsets[step_in_cycle]

    assignments = {}
    for i, giver in enumerate(permuted_families):
        receiver_idx = (i + shift) % n
        assignments[giver] = permuted_families[receiver_idx]

    return assignments


def uniform_random_derangement(
    family_ids: List[int],
    history: Optional[Dict[int, Set[int]]] = None,
    max_attempts: int = 1500,
    rng: Optional[random.Random] = None
) -> Dict[int, int]:
    """Generate uniform random derangement avoiding recent history using permutation rejection.

    Rejection sampling over uniform permutations guarantees that all feasible matchings
    are sampled with equal probability. If rejection sampling does not find a match within
    max_attempts, a randomized backtracking search is used to find a valid matching or
    prove infeasibility.
    """
    n = len(family_ids)
    if n < 2:
        raise AssignmentError("At least 2 active families are required for a gift exchange.")

    if history is None:
        history = {}

    if rng is None:
        rng = random.SystemRandom()

    givers = list(family_ids)
    receivers = list(family_ids)

    # 1. Uniform Permutation Rejection Sampling
    for _ in range(max_attempts):
        rng.shuffle(receivers)
        valid = True
        for g, r in zip(givers, receivers):
            if g == r:
                valid = False
                break
            if r in history.get(g, set()):
                valid = False
                break
        if valid:
            return dict(zip(givers, receivers))

    # 2. Rejection sampling failed: count all feasible completions and sample
    # exactly in proportion to their counts, preserving uniformity.
    # Avoid unbounded exponential work for enormous rosters.
    if n > 18:
        raise AssignmentInfeasibleError(
            "Too many constraints to sample uniformly within the safety limit. "
            "Reduce the no-repeat lookback and try again."
        )
    solution = _uniform_matching(givers, history, rng)
    if solution is None:
        raise AssignmentInfeasibleError(
            f"No valid gift assignment exists for {n} families with the selected "
            "recent-recipient restrictions. Reduce the lookback."
        )
    return solution


def _uniform_matching(
    givers: List[int],
    history: Dict[int, Set[int]],
    rng: random.Random
) -> Optional[Dict[int, int]]:
    """Count perfect matchings with memoization and draw an exactly uniform one."""
    from functools import lru_cache

    n = len(givers)
    candidates = [
        [j for j, receiver in enumerate(givers)
         if receiver != giver and receiver not in history.get(giver, set())]
        for giver in givers
    ]

    @lru_cache(maxsize=None)
    def count(idx: int, used_mask: int) -> int:
        if idx == n:
            return 1
        return sum(count(idx + 1, used_mask | (1 << j))
                   for j in candidates[idx] if not (used_mask & (1 << j)))

    total = count(0, 0)
    if not total:
        return None

    result: Dict[int, int] = {}
    used_mask = 0
    for idx, giver in enumerate(givers):
        ticket = rng.randrange(total)
        for j in candidates[idx]:
            bit = 1 << j
            if used_mask & bit:
                continue
            branches = count(idx + 1, used_mask | bit)
            if ticket < branches:
                result[giver] = givers[j]
                used_mask |= bit
                total = branches
                break
            ticket -= branches
        else:
            raise AssertionError("Uniform matching sampler count mismatch")
    return result


def get_cycle_info(
    n: int,
    current_year: int,
    anchor_year: int
) -> Dict[str, object]:
    """Calculate cycle duration, year index, and mathematical coverage limits.

    Mathematical rules:
    - Cycle length L = n - 1.
    - If n <= 6: cycle length is <= 5 years, so full coverage is guaranteed within <= 5 years.
    - If n > 6: cycle length is > 5 years. By Pigeonhole Principle, full coverage within 5 years
      is mathematically impossible, requiring at least n - 1 years.
    """
    if n < 2:
        return {
            "cycle_length": 0,
            "cycle_index": 0,
            "year_in_cycle": 0,
            "cycle_start_year": current_year,
            "cycle_end_year": current_year,
            "covers_within_5_years": False,
            "message": "At least 2 families required for cycle calculation."
        }

    cycle_length = n - 1
    cycle_index = (current_year - anchor_year) // cycle_length
    step_in_cycle = (current_year - anchor_year) % cycle_length
    year_in_cycle = step_in_cycle + 1
    cycle_start = anchor_year + (cycle_index * cycle_length)
    cycle_end = cycle_start + cycle_length - 1
    covers_within_5_years = (cycle_length <= 5)

    if covers_within_5_years:
        explanation = (
            f"With {n} active families, a complete cycle is {cycle_length} years. "
            f"Every family is guaranteed to give to every other family within {cycle_length} years (<= 5 years) "
            f"over aligned cycles ({cycle_start}–{cycle_end})."
        )
    else:
        explanation = (
            f"With {n} active families, full coverage requires at least {cycle_length} years "
            f"({cycle_start}–{cycle_end}). Full coverage within 5 years is mathematically impossible "
            f"by the Pigeonhole Principle (each family can only give to 5 distinct families in 5 years, "
            f"leaving {cycle_length - 5} unassigned)."
        )

    return {
        "cycle_length": cycle_length,
        "cycle_index": cycle_index,
        "year_in_cycle": year_in_cycle,
        "cycle_start_year": cycle_start,
        "cycle_end_year": cycle_end,
        "covers_within_5_years": covers_within_5_years,
        "explanation": explanation
    }


def analyze_history_coverage(
    family_ids: List[int],
    history_by_year: Dict[int, Dict[int, int]]
) -> Dict[str, object]:
    """Analyze historical assignments for fairness, coverage, and repeats.

    history_by_year: mapping of {year: {giver_id: receiver_id}}
    """
    n = len(family_ids)
    sorted_years = sorted(history_by_year.keys())
    
    # Track recipients per giver
    giver_recipients: Dict[int, List[Tuple[int, int]]] = {fid: [] for fid in family_ids}
    # Track pairs (giver, receiver) across years
    pair_history: Dict[Tuple[int, int], List[int]] = {}
    consecutive_repeats = []

    for idx, yr in enumerate(sorted_years):
        yr_assignments = history_by_year[yr]
        for giver, receiver in yr_assignments.items():
            if giver in giver_recipients:
                giver_recipients[giver].append((yr, receiver))
            pair = (giver, receiver)
            if pair not in pair_history:
                pair_history[pair] = []
            pair_history[pair].append(yr)

            # Check consecutive repeat from previous year
            if idx > 0:
                prev_yr = sorted_years[idx - 1]
                if prev_yr in history_by_year:
                    if history_by_year[prev_yr].get(giver) == receiver:
                        consecutive_repeats.append({
                            "giver": giver,
                            "receiver": receiver,
                            "years": (prev_yr, yr)
                        })

    stats_per_family = {}
    for fid in family_ids:
        recipients_list = [r for (_, r) in giver_recipients[fid]]
        distinct_recipients = set(recipients_list)
        stats_per_family[fid] = {
            "total_given": len(recipients_list),
            "distinct_given": len(distinct_recipients),
            "coverage_pct": round((len(distinct_recipients) / (n - 1) * 100), 1) if n > 1 else 100.0,
            "unassigned_count": max(0, (n - 1) - len(distinct_recipients)) if n > 1 else 0
        }

    return {
        "total_years_recorded": len(sorted_years),
        "consecutive_repeats": consecutive_repeats,
        "family_stats": stats_per_family,
        "pair_history": pair_history
    }
