"""Private, nonbinding recipient requests for the Christmas gift exchange.

A private family token identifies the submitting household for validation, but
request targets are never returned on an organizer route or in aggregate reports.
Preferences cannot relax hard matching, history, or rotation constraints.
"""

from datetime import datetime, timezone
from functools import lru_cache
import random

from web.assignment import AssignmentInfeasibleError, shuffled_repeating_rotation, uniform_random_derangement
from web.continuity import find_boundary_conflicts
from web.db import get_db
from web.models import get_all_families, get_recent_history_disallowed, get_setting


def requests_are_open(year):
    """A sealed or released assignment locks the year's request window."""
    row = get_db().execute(
        "SELECT 1 FROM assignments WHERE year=? LIMIT 1", (year,)
    ).fetchone()
    return row is None


def request_choices(year, giver_id, lookback=None):
    """Return rule-based eligible and excluded options, not planned assignments.

    A locked repeating rotation may impose stricter requirements than these
    general exclusions. Never inspect or reveal the sealed/current mapping.
    """
    if lookback is None:
        lookback = int(get_setting("no_repeat_years", "1"))
    families = get_all_families(active_only=True)
    ids = [f["id"] for f in families]
    disallowed = get_recent_history_disallowed(ids, year, lookback).get(giver_id, set())
    eligible, excluded = [], []
    for family in families:
        entry = {"id": family["id"], "name": family["name"]}
        if family["id"] == giver_id:
            excluded.append({**entry, "reason": "Your own household"})
        elif family["id"] in disallowed:
            excluded.append({**entry, "reason": "Recent recipient under the no-repeat rule"})
        else:
            eligible.append(entry)
    return eligible, excluded


def has_request(year, giver_id):
    return get_db().execute(
        "SELECT 1 FROM assignment_requests WHERE year=? AND giver_family_id=?",
        (year, giver_id)
    ).fetchone() is not None


def save_request(year, giver_id, recipient_id=None):
    """Save/replace/withdraw one private request; always enforce current rules."""
    if not requests_are_open(year):
        raise ValueError("Requests are closed because assignments have been prepared.")
    active = {f["id"] for f in get_all_families(active_only=True)}
    if giver_id not in active:
        raise ValueError("Only participating households can submit a request.")
    if recipient_id is not None:
        eligible, _ = request_choices(year, giver_id)
        if recipient_id not in {f["id"] for f in eligible}:
            raise ValueError("That recipient is excluded under the current rules.")
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    with db:
        if recipient_id is None:
            db.execute("DELETE FROM assignment_requests WHERE year=? AND giver_family_id=?",
                       (year, giver_id))
        else:
            db.execute(
                """INSERT INTO assignment_requests
                   (year, giver_family_id, recipient_family_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(year, giver_family_id) DO UPDATE SET
                   recipient_family_id=excluded.recipient_family_id,
                   updated_at=excluded.updated_at""",
                (year, giver_id, recipient_id, now, now)
            )


def valid_requests(year, active_ids, history):
    """Return only current, legal preferences; never use stale exclusions."""
    ids = set(active_ids)
    rows = get_db().execute(
        """SELECT giver_family_id, recipient_family_id FROM assignment_requests
           WHERE year=?""", (year,)
    ).fetchall()
    return {
        row["giver_family_id"]: row["recipient_family_id"]
        for row in rows
        if row["giver_family_id"] in ids
        and row["recipient_family_id"] in ids
        and row["giver_family_id"] != row["recipient_family_id"]
        and row["recipient_family_id"] not in history.get(row["giver_family_id"], set())
    }


def request_count(year):
    """Only expose the overall count, never counts by family or priority."""
    row = get_db().execute(
        "SELECT COUNT(*) AS n FROM assignment_requests WHERE year=?", (year,)
    ).fetchone()
    return row["n"]


def get_priorities(year):
    """Smaller positive numbers are higher priority; zero is the default tier."""
    rows = get_db().execute(
        "SELECT family_id, priority FROM request_priorities WHERE year=?", (year,)
    ).fetchall()
    return {row["family_id"]: row["priority"] for row in rows}


def save_priorities(year, priorities):
    """Admin-only caller; updating priorities never changes a sealed draw."""
    if not requests_are_open(year):
        raise ValueError("Priorities are locked after the assignment draft is prepared.")
    active_ids = {f["id"] for f in get_all_families(active_only=True)}
    if set(priorities) != active_ids:
        raise ValueError("Priorities must cover the current participating households.")
    if any(not isinstance(p, int) or p < 0 or p > len(active_ids)
           for p in priorities.values()):
        raise ValueError("Priority must be 0 (normal) or an integer between 1 and the household count.")
    db = get_db()
    with db:
        db.execute("DELETE FROM request_priorities WHERE year=?", (year,))
        db.executemany(
            "INSERT INTO request_priorities (year, family_id, priority) VALUES (?, ?, ?)",
            [(year, fid, p) for fid, p in priorities.items() if p]
        )


def _score(mapping, preferences, priorities):
    """Lexicographically maximize matches by tier: 1, 2, ..., normal (0)."""
    levels = sorted(set(priorities.get(g, 0) for g in preferences),
                    key=lambda p: (p == 0, p))
    return tuple(sum(mapping.get(g) == requested
                     for g, requested in preferences.items()
                     if priorities.get(g, 0) == level) for level in levels)


def best_random_matching(family_ids, history, preferences, priorities, rng=None):
    """Exact lexicographic maximum of satisfiable soft requests.

    Every candidate is a full hard-constrained derangement. Equal-score
    solutions are sampled at random; an unmet request never blocks a draw.
    """
    givers = list(family_ids)
    n = len(givers)
    if n > 18:
        # Soft preferences must never make an otherwise possible draw fail.
        return uniform_random_derangement(givers, history, rng=rng)
    rng = rng or random.SystemRandom()
    levels = sorted(set(priorities.get(g, 0) for g in preferences),
                    key=lambda p: (p == 0, p))
    rank = {p: i for i, p in enumerate(levels)}
    zero = (0,) * len(levels)
    valid_receivers = [
        [j for j, r in enumerate(givers)
         if g != r and r not in history.get(g, set())]
        for g in givers
    ]

    @lru_cache(maxsize=None)
    def best(index, used):
        if index == n:
            return zero
        greatest = None
        for j in valid_receivers[index]:
            bit = 1 << j
            if used & bit:
                continue
            rest = best(index + 1, used | bit)
            if rest is None:
                continue
            gain = list(rest)
            giver = givers[index]
            if preferences.get(giver) == givers[j]:
                gain[rank[priorities.get(giver, 0)]] += 1
            score = tuple(gain)
            if greatest is None or score > greatest:
                greatest = score
        return greatest

    if best(0, 0) is None:
        raise AssignmentInfeasibleError(
            "No assignment satisfies the mandatory recipient restrictions."
        )
    mapping = {}
    used = 0
    for index, giver in enumerate(givers):
        optimal = best(index, used)
        choices = []
        for j in valid_receivers[index]:
            bit = 1 << j
            if used & bit:
                continue
            suffix = best(index + 1, used | bit)
            if suffix is None:
                continue
            gain = list(suffix)
            if preferences.get(giver) == givers[j]:
                gain[rank[priorities.get(giver, 0)]] += 1
            if tuple(gain) == optimal:
                choices.append(j)
        chosen = rng.choice(choices)
        mapping[giver] = givers[chosen]
        used |= 1 << chosen
    return mapping


def choose_preferred_rotation_salt(family_ids, year, anchor_year, seed,
                                   history_by_year, lookback, preferences, priorities,
                                   max_candidates=10000):
    """Choose a boundary-safe initial rotation honoring as many requests as possible.

    Called ONLY before a production schedule exists. Once a rotation salt has
    been saved, subsequent years must reuse it regardless of new requests.
    """
    champion_salt, champion_score = None, None
    # The maximum possible count in each priority tier.
    perfect = _score(preferences, preferences, priorities)
    for candidate in range(max_candidates):
        salt = str(candidate)
        schedule = lambda target_year: shuffled_repeating_rotation(
            family_ids, target_year, anchor_year, seed, salt=salt)
        if find_boundary_conflicts(schedule, history_by_year, year, lookback):
            continue
        score = _score(schedule(year), preferences, priorities)
        if champion_score is None or score > champion_score:
            champion_salt, champion_score = salt, score
            if score == perfect:
                break
    return champion_salt
