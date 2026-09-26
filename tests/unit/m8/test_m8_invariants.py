"""Safety invariants swept over a grid of coverage, damage, mark, amount and side states.

Each combination is a hand-built structured input; the assertions are the v2 safety
rules, which must hold for every combination, not a sample.
"""
from __future__ import annotations

import itertools

import pytest

from claim_cmev.comparison import REASON_CODES

from m8_support import World, emitted_codes, finding

COVERAGE = ("adequate", "inadequate", "not_visible")
DAMAGE = ((), (("dent", 0.3),), (("dent", 0.8),))
MARKS = ("none", "pending_exclusion", "confirmed_exclusion", "rejected_exclusion", "pending_price",
         "confirmed_price_in", "confirmed_price_out", "unlinked", "conflicting")
AMOUNTS = ("500.00", "900.00", None)
SIDES = ("left", "unknown")
GRID = list(itertools.product(COVERAGE, DAMAGE, MARKS, AMOUNTS, SIDES))


def build(coverage, damage, marks, amount, side) -> World:
    world = World()
    world.seen("front-door", "left", damage=damage, coverage=coverage)
    world.item("e1", side=side, amount=amount)
    if marks == "pending_exclusion":
        world.mark("m1", "e1", "exclusion", "pending")
    elif marks == "confirmed_exclusion":
        world.mark("m1", "e1", "exclusion", "confirmed")
    elif marks == "rejected_exclusion":
        world.mark("m1", "e1", "exclusion", "rejected")
    elif marks == "pending_price":
        world.mark("m1", "e1", "price_change", "pending")
    elif marks == "confirmed_price_in":
        world.mark("m1", "e1", "price_change", "confirmed", amount="560.00")
    elif marks == "confirmed_price_out":
        world.mark("m1", "e1", "price_change", "confirmed", amount="990.00")
    elif marks == "unlinked":
        world.mark("m1", None, "price_change", "pending", candidates=("e1",))
    elif marks == "conflicting":
        world.mark("m1", "e1", "exclusion", "confirmed")
        world.mark("m2", "e1", "price_change", "confirmed", amount="560.00")
    return world


@pytest.mark.parametrize("coverage", COVERAGE)
def test_safety_invariants_hold_for_every_combination(coverage):
    for cov, damage, marks, amount, side in (g for g in GRID if g[0] == coverage):
        result = build(cov, damage, marks, amount, side).run()
        f = finding(result, "e1")
        label = (cov, damage, marks, amount, side)
        assert emitted_codes(result) <= set(REASON_CODES), label
        assert f.overall_result in ("ok", "unsupported", "cost_outlier", "insufficient_evidence", "not_evaluated")
        if marks in ("pending_exclusion", "pending_price", "unlinked", "conflicting"):
            assert f.overall_result == "insufficient_evidence" and result.outcome_rules["e1"] == "R2", label
        if marks in ("pending_price", "unlinked", "conflicting"):
            assert f.cost_check.amount is None and f.cost_check.range_id is None, label  # never the printed amount
        if marks == "confirmed_exclusion":
            assert (f.row_state, f.overall_result) == ("excluded", "not_evaluated"), label
        else:
            assert f.row_state == "active", label
        if f.overall_result in ("ok", "unsupported", "cost_outlier"):
            assert side == "left" and cov == "adequate", label  # no decision without identity and coverage
        if f.overall_result == "unsupported":
            assert damage == () and f.photographic_check.result == "failed", label
        if f.photographic_check.result == "passed" and f.cost_check.result != "within_range":
            assert f.overall_result != "ok", label
        if f.overall_result == "ok":
            assert f.cost_check.result == "within_range" and damage == (("dent", 0.8),), label
            expected = "560.00" if marks == "confirmed_price_in" else amount
            assert f.cost_check.amount == expected, label
        if f.cost_check.result not in ("within_range", "outside_range"):
            assert f.cost_check.absolute_deviation is None and f.cost_check.direction is None, label
        for check in (f.documentary_check, f.mark_state_check, f.photographic_check):
            if check.result == "not_evaluated":
                assert check.reasons, label  # a skipped check always says why, never a blank pass
