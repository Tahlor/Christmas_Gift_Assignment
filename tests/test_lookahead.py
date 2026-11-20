"""Independent tests for actual cycle feasibility rather than bounded salt guesses."""
import random
import pytest

from web.assignment import AssignmentInfeasibleError, uniform_random_derangement, batch_simulate_assignments
from web.lookahead import plan_cycle, SearchBudgetExceeded

IDS = [1, 2, 3, 4, 5]


def check_history(history, roster, anchor, lookback, rolling_k=None):
    period = len(roster) - 1
    for year in sorted(history):
        m = history[year]
        assert set(m) == set(m.values()) == set(roster)
        assert all(g != r for g, r in m.items())
        for other in range(year-lookback, year):
            if other in history:
                assert all(history[other][g] != r for g,r in m.items())
        if (year-anchor+1) % period == 0 and year >= anchor:
            for g in roster:
                assert {history[y][g] for y in range(year-period+1,year+1)} == set(roster)-{g}
        if rolling_k and year >= anchor+rolling_k-1:
            for g in roster:
                assert {history[y][g] for y in range(year-rolling_k+1,year+1)} == set(roster)-{g}


@pytest.mark.parametrize("lookback", [0,1,2,3])
@pytest.mark.parametrize("rolling_k", [None,4,5,6])
def test_several_cycles_and_yearly_replanning(lookback, rolling_k):
    history = {}
    for year in range(2026, 2038):
        witness = plan_cycle(IDS,year,2026,"test",history,lookback,rolling_k)
        assert witness == plan_cycle(IDS,year,2026,"test",history,lookback,rolling_k)
        assert year in witness
        check_history({**history, **witness},IDS,2026,lookback,rolling_k)
        history[year] = witness[year]
    check_history(history,IDS,2026,lookback,rolling_k)


def test_realistic_historical_boundary_and_preference():
    history = {2024:{1:3,2:5,3:2,4:1,5:4},2025:{1:5,2:4,3:2,4:1,5:3}}
    witness = plan_cycle(IDS,2026,2026,"test",history,1,4,{3:4},{})
    assert witness[2026][3] == 4
    assert all(witness[2026][g] != history[2025][g] for g in IDS)
    check_history(witness, IDS,2026,1,4)
    assert set(witness) == set(range(2026,2030))


def test_impossible_midcycle_detected_and_budget_is_not_infeasibility():
    past = {2026:{g:IDS[(i+1)%5] for i,g in enumerate(IDS)}}
    past[2027] = past[2026]
    with pytest.raises(AssignmentInfeasibleError):
        plan_cycle(IDS,2028,2026,"test",past,1)
    with pytest.raises(SearchBudgetExceeded):
        plan_cycle(IDS,2026,2026,"test",{},1,node_budget=1)


def test_large_roster_matching_exact_even_if_rejection_attempts_fail():
    ids=list(range(20))
    desired={g:ids[(i+1)%len(ids)] for i,g in enumerate(ids)}
    history={g:set(ids)-{g,desired[g]} for g in ids}
    assert uniform_random_derangement(ids,history,max_attempts=0,rng=random.Random(12)) == desired
    history[ids[0]].add(desired[ids[0]])
    with pytest.raises(AssignmentInfeasibleError):
        uniform_random_derangement(ids,history,max_attempts=0,rng=random.Random(12))


def test_batch_100_lookahead():
    result=batch_simulate_assignments(IDS,"random_lookahead",2026,2026,"batch",lookback_years=3,rolling_k=4)
    assert result["successful_runs"] == 100
    assert result["infeasible_runs"] == 0
