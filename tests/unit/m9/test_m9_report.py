"""M9 print payload: one frozen assessment with its matching frozen review revision only.

Module 09 "Print view and report"; application platform section 9.2; data contracts
section 11; UI specification section 6.13 and AC-11.
"""
import pytest

from claim_cmev.review import PrintRefused, build_print_payload, finalize_review
from m9_support import (
    ALL_STAGES_DONE,
    BASIS,
    LATER,
    PINNED,
    apply,
    assessment,
    claim_input,
    findings,
    request,
    state,
)


def frozen(s=None, presented=0):
    s = s or state("clean")
    out = finalize_review(s, presented_review_revision=presented, stage_states=ALL_STAGES_DONE,
                          actor="surveyor:rm", finalized_at=LATER, finalization_id="fin-1")
    assert out.kind == "finalized", out.preconditions
    return out.state


def payload(review, **kw):
    return build_print_payload(claim_input(kw.pop("kind", "fixture")), review.assessment, review,
                               reason_catalogue=kw.pop("reason_catalogue", {}), **kw)


def test_footer_carries_every_revision_and_pinned_version():
    review = frozen()
    p = payload(review, printed_at=LATER, printed_by="surveyor:rm")
    f = p.footer
    assert (f.input_revision, f.assessment_revision, f.review_revision, f.review_frozen) == (1, 1, 0, True)
    assert dict(f.pinned_versions) == PINNED and list(f.pinned_versions) == sorted(PINNED.items())
    assert (f.rules_config_version, f.cost_table_version, f.cost_basis) == ("rc-0.1.0", "2026.09.1", BASIS)
    assert "synthetic" in f.synthetic_cost_statement.lower()
    assert f.fixture_marker and (f.printed_at, f.printed_by) == (LATER, "surveyor:rm")
    assert p.header.review_revision == 0 and p.header.reviewed_by == "surveyor:rm"


def test_report_labels_synthetic_costs_unrecorded_approval_and_fixture_origin():
    p = payload(frozen())
    assert p.cost_reference.synthetic and p.cost_reference.cost_basis == BASIS and p.cost_reference.currency_supported
    assert p.final_approval.status == "not_recorded" and "not recorded" in p.final_approval.text
    assert p.fixture.is_fixture and p.fixture.notice


def test_real_provenance_prints_no_fixture_marker():
    review = frozen(state("clean", kind="real"))
    p = payload(review, kind="real")
    assert not p.fixture.is_fixture and p.fixture.notice is None and p.footer.fixture_marker is None


def test_more_information_needed_rows_print_with_reasons_and_never_as_passes():
    p = payload(frozen())
    outstanding = {r.entry_id: r for r in p.more_information_needed}
    assert set(outstanding) == {"li2", "li5"}
    for row in outstanding.values():
        assert row.overall_result == "insufficient_evidence" and row.result_label == "More information needed"
        assert row.reasons and all(r.text for r in row.reasons)
    assert {r.entry_id for r in p.findings} == {"li2", "li4", "li5"}, "every checked non-ok row is a finding"
    rows = {r.entry_id: r for r in p.line_items}
    assert rows["li1"].result_label == "No discrepancy found"
    assert rows["li3"].row_state == "excluded" and rows["li3"].result_label == "Excluded by surveyor"
    assert p.excluded_entry_ids == ("li3",)
    assert rows["li2"].printed_line_amount == "340.00" and rows["li2"].effective_price == "300.00"
    assert {m.mark_id: m.state for m in p.mark_decisions} == {"pm1": "confirmed", "pm2": "confirmed",
                                                              "pm4": "confirmed"}


def test_dismissals_and_notes_print_with_their_reasons():
    s = apply(state("clean"), request("dismiss_finding", finding_id="f4", reason_code="parts_price_change",
                                      note="Supplier raised the price.")).state
    s = apply(s, request("dismiss_addition", 1, candidate_id="cand1", reason_code="system_error"), 2).state
    s = apply(s, request("add_note", 2, note="Workshop called."), 3).state
    p = payload(frozen(s, presented=3))
    row = next(r for r in p.findings if r.entry_id == "li4")
    assert row.overall_result == "cost_outlier" and row.dismissal.reason_code == "parts_price_change"
    assert row.dismissal.note == "Supplier raised the price."
    assert [a.candidate_id for a in p.dismissed_additions] == ["cand1"] and p.open_additions == ()
    assert [a.candidate_id for a in p.withheld_additions] == ["cand2"]
    assert [n.note for n in p.notes] == ["Workshop called."]


def test_reason_text_comes_from_the_catalogue_or_the_stored_record_never_composed():
    review = frozen()
    by_mapping = payload(review, reason_catalogue={"no_key": "No reference range for this combination"})
    li2 = next(r for r in by_mapping.line_items if r.entry_id == "li2")
    assert (li2.reasons[0].text, li2.reasons[0].text_source) == ("No reference range for this combination",
                                                                 "catalogue")
    by_callable = payload(review, reason_catalogue=lambda code: {"no_key": "From M8"}.get(code))
    assert next(r for r in by_callable.line_items if r.entry_id == "li2").reasons[0].text == "From M8"
    fallback = payload(review, reason_catalogue={})
    li5 = next(r for r in fallback.line_items if r.entry_id == "li5")
    assert (li5.reasons[0].text, li5.reasons[0].text_source) == ("Take more pictures of this part", "record")


def test_reprinting_the_same_frozen_pair_is_identical():
    review = frozen()
    assert payload(review, printed_at=LATER) == payload(review, printed_at=LATER)


def test_unfinalized_review_is_refused():
    with pytest.raises(PrintRefused) as exc:
        payload(state("clean"))
    assert (exc.value.reason_code, exc.value.http_status) == ("not_finalized", 412)


def test_mismatched_revisions_are_refused():
    review = frozen()
    other = assessment(findings("clean"), revision=1, rules_config_version="rc-0.2.0")
    newer = assessment([], revision=2)
    cases = [
        (claim_input(), newer, "review_revision_mismatch"),
        (claim_input(), other, "assessment_mismatch"),
        (claim_input(input_revision=2), review.assessment, "claim_snapshot_mismatch"),
    ]
    for claim, target, code in cases:
        with pytest.raises(PrintRefused) as exc:
            build_print_payload(claim, target, review, reason_catalogue={})
        assert (exc.value.reason_code, exc.value.http_status) == (code, 409)
    stale = review.model_copy(update={"review_revision": 4})
    with pytest.raises(PrintRefused) as exc:
        build_print_payload(claim_input(), review.assessment, stale, reason_catalogue={})
    assert exc.value.reason_code == "review_revision_mismatch"


def test_superseded_assessment_is_never_printed():
    superseded = frozen(state("clean", assessment=assessment(findings("clean"), superseded=True),
                              current_input_revision=1))
    with pytest.raises(PrintRefused) as exc:
        payload(superseded)
    assert exc.value.reason_code == "assessment_superseded"
