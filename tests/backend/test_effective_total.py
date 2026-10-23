"""The queue's agreed total: confirmed exclusions count as zero, an undecided amount never does."""
from claim_cmev.api.views import _compute_effective_total
from claim_cmev.contracts.documents import with_effective_price
from claim_cmev.contracts.fixtures import fixture_bundle

CLAIM = "01K6F1XTVRE00000000000T001"
UNDECIDED = {"state": "pending", "decision_action_id": None, "decided_by": None, "decided_at": None,
             "review_revision": None}


def snapshot(scenario, *, undecided_exclusion=False):
    bundle = fixture_bundle(scenario, CLAIM, 1)
    marks = [m.model_copy(update=UNDECIDED) if undecided_exclusion and m.mark_type == "exclusion" else m
             for m in bundle.pen_marks]
    items = [with_effective_price(i, marks) for i in bundle.line_items]
    excluded = {m.entry_id for m in marks if m.mark_type == "exclusion" and m.state == "confirmed"}
    inputs = {"line_items": [i.model_dump(mode="json") for i in items],
              "pen_marks": [m.model_dump(mode="json") for m in marks]}
    body = {"findings": [{"entry_id": i.entry_id, "row_state": "excluded" if i.entry_id in excluded else "active"}
                         for i in items]}
    return inputs, body


def test_confirmed_exclusion_and_confirmed_price_change_give_the_agreed_total():
    # Rear door 480.00 excluded; bumper repriced 1150.00 -> 980.00; fender 320.00 as printed.
    assert _compute_effective_total(*snapshot("exclusion_and_supported")) == "1300.00"


def test_a_pending_price_change_leaves_the_agreed_total_undetermined():
    # Bumper 1150.00 has a pending price change; 480.00 + 860.00 is not an agreed total.
    assert _compute_effective_total(*snapshot("pending_price_change")) is None


def test_a_pending_exclusion_leaves_the_agreed_total_undetermined():
    # The rear door's exclusion is not yet confirmed, so whether its 480.00 counts is undecided.
    assert _compute_effective_total(*snapshot("exclusion_and_supported", undecided_exclusion=True)) is None


def test_an_unlinked_mark_leaves_the_agreed_total_undetermined():
    inputs, body = snapshot("exclusion_and_supported")
    inputs["pen_marks"][0].update(UNDECIDED, entry_id=None, link_reason="no_candidate_row", candidate_entry_ids=[])
    assert _compute_effective_total(inputs, body) is None
