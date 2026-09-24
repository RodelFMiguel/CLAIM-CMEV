"""summarise_parts: late confirmations, reuse lineage, job keys, fixtures, determinism and the event."""
from datetime import UTC, datetime
import random

import pytest

from claim_cmev.contracts.common import ContractError, Provenance
from claim_cmev.contracts.events.envelope import Envelope
from claim_cmev.contracts.events.registry import validate_message
from claim_cmev.contracts.fixtures import SCENARIOS, fixture_bundle
from claim_cmev.vision.multiview import (
    SummaryContext,
    build_confirmation_index,
    confirmation_set_signature,
    summarise_parts,
    summary_job_key,
    summary_job_versions,
)
from m3_support import CFG, CLAIM_ID, SUMMARY_VERSIONS, context, covers, identity, obs, pred, run, slot, worked_example


def _late_confirmations(rev: int = 4):
    return ([identity("ic_1", "ph_01", "front-door", "left", rev=rev, review=5),
             identity("ic_2", "ph_02", "front-door", "left", rev=rev, review=6)],
            [covers("cc_1", "front-door", "left", ["ph_01", "ph_02"], rev=rev, review=7)])


def test_confirmation_arriving_late_changes_only_the_new_revision():
    observations, predictions = worked_example(rev=3)
    before = run(observations, predictions, rev=3)
    snapshot = before.coverage + before.summaries
    ids, cov = _late_confirmations()
    after = run(observations, predictions, ids, cov, rev=4, reuse=3)

    assert slot(before, "front-door").state == "unresolved"
    door = slot(after, "front-door", "left")
    assert (door.state, door.coverage_confirmation_id, door.input_revision) == ("adequate", "cc_1", 4)
    [resolved] = [g for g in after.summaries if g.identity_status == "resolved"]
    assert (resolved.part_code, resolved.side, resolved.member_observation_ids) == ("front-door", "left",
                                                                                   ["obs_1", "obs_2"])
    # The earlier revision's result is unchanged and reproducible; its rows keep their own IDs.
    assert before.coverage + before.summaries == snapshot == run(observations, predictions, rev=3).coverage + \
        run(observations, predictions, rev=3).summaries
    assert all(r.input_revision == 3 for r in snapshot)
    assert not set(before.coverage_ids) & set(after.coverage_ids)
    assert not set(before.summary_ids) & set(after.summary_ids)


def test_reuse_lineage_is_recorded_on_every_new_row():
    observations, predictions = worked_example(rev=3)
    ids, cov = _late_confirmations()
    after = run(observations, predictions, ids, cov, rev=4, reuse=3)
    assert after.reuse.input_revision == 3
    assert after.reuse.parts_models == ("test-parts/0.0.0",) and after.reuse.damage_models == ("test-damage/0.0.0",)
    expected = ["reused_from:input_revision=3", "reused_from:parts_model=test-parts/0.0.0",
                "reused_from:damage_model=test-damage/0.0.0"]
    for record in after.summaries + after.coverage:
        assert record.provenance.derivation_refs == expected and record.provenance.source_kind == "fixture"
    assert after.contributing_confirmation_ids == ("cc_1", "ic_1", "ic_2")


def test_a_later_confirmation_cannot_change_an_earlier_revision():
    observations, predictions = worked_example(rev=3)
    ids, cov = _late_confirmations(rev=4)
    with pytest.raises(ContractError) as err:
        run(observations, predictions, ids, cov, rev=3)
    assert err.value.reason_code == "confirmation_from_later_revision"


def test_stale_rows_are_rejected_without_a_matching_reuse_revision():
    observations, predictions = worked_example(rev=1)
    for reuse in (None, 2):
        with pytest.raises(ContractError) as err:
            run(observations, predictions, rev=3, reuse=reuse)
        assert err.value.reason_code == "stale_input_revision"
    with pytest.raises(ContractError) as err:
        run(observations, predictions, rev=3, reuse=3)
    assert err.value.reason_code == "invalid_reuse_revision"


def test_job_key_includes_the_confirmation_set():
    ids, cov = _late_confirmations()
    all_ids = [c.confirmation_id for c in [*ids, *cov]]
    plain = summary_job_key(CLAIM_ID, 4, SUMMARY_VERSIONS, [])
    with_set = summary_job_key(CLAIM_ID, 4, SUMMARY_VERSIONS, all_ids)
    assert plain != with_set
    assert with_set == summary_job_key(CLAIM_ID, 4, SUMMARY_VERSIONS, reversed(all_ids + all_ids))  # redelivery
    assert summary_job_versions(SUMMARY_VERSIONS, [])["confirmation_set"] == "none"
    index = build_confirmation_index(ids, cov)
    assert index.signature == confirmation_set_signature(all_ids)
    outcome = run(*worked_example(rev=3), ids, cov, rev=4, reuse=3)
    assert outcome.confirmation_ids == tuple(sorted(all_ids))
    assert outcome.confirmation_set_signature == index.signature


def test_superseded_confirmations_are_reported():
    old = identity("ic_old", "ph_01", "front-door", "left", review=1)
    new = identity("ic_new", "ph_01", "front-door", "right", review=2)
    outcome = run([], [pred("ph_01", "front-door")], [old, new])
    assert outcome.superseded_confirmation_ids == ("ic_old",)
    assert [c.side for c in outcome.coverage if c.part_code == "front-door"] == ["right"]


def test_confirmation_for_an_unknown_photo_is_rejected():
    with pytest.raises(ContractError) as err:
        run([], [], [identity("ic_1", "ph_99", "hood", "not_applicable")], photo_ids=["ph_01"])
    assert err.value.reason_code == "confirmation_unknown_photo"


def test_fixture_rows_keep_fixture_provenance():
    observations, predictions = worked_example()
    ctx = context(source_kind="real")
    with pytest.raises(ContractError) as err:
        summarise_parts(observations, predictions, context=ctx, config=CFG)
    assert err.value.reason_code == "fixture_provenance_required"


def test_summary_config_version_is_pinned():
    observations, predictions = worked_example()
    good = context()
    bad = SummaryContext(**{**good.__dict__, "versions": {**SUMMARY_VERSIONS, "summary_config": "m3-summary/9.9.9"}})
    with pytest.raises(ContractError) as err:
        summarise_parts(observations, predictions, context=bad, config=CFG)
    assert err.value.reason_code == "summary_config_version_mismatch"


def test_summarise_is_deterministic():
    observations, predictions = worked_example()
    ids = [identity("ic_1", "ph_01", "front-door", "left"), identity("ic_2", "ph_03", "fender", "left")]
    first = run(observations, predictions, ids)
    shuffled_obs, shuffled_pred = observations[:], predictions[:]
    random.Random(11).shuffle(shuffled_obs)
    random.Random(12).shuffle(shuffled_pred)
    second = run(shuffled_obs, shuffled_pred, ids[::-1])
    assert first == second


def test_event_payload_validates_against_the_topic_schema():
    observations, predictions = worked_example()
    outcome = run(observations, predictions, [identity("ic_1", "ph_01", "front-door", "left")])
    envelope = Envelope.build("cmev.evt.part-summarised.v1", claim_id=CLAIM_ID, input_revision=1, task="part_summary",
                              versions=summary_job_versions(SUMMARY_VERSIONS, outcome.confirmation_ids),
                              provenance=Provenance(source_kind="fixture", runtime_profile="lean",
                                                    producer_service="cmev-worker-summary"),
                              trace_id="t-m3", occurred_at=datetime(2026, 9, 24, tzinfo=UTC))
    payload = validate_message("cmev.evt.part-summarised.v1", envelope.message(outcome.event_payload()))["payload"]
    assert payload["unresolved_observation_ids"] == ["obs_2", "obs_4", "obs_3", "obs_5"]
    assert sum(payload["coverage_counts"].values()) == len(payload["coverage_ids"])
    assert payload["coverage_counts"]["adequate"] == 0


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_matches_the_contract_fixture_bundles(scenario):
    """Recompute M3 from each fixture bundle's M1/M2 rows and confirmations and compare shapes."""
    bundle = fixture_bundle(scenario, CLAIM_ID)
    signals = {(v.photo_id, c.part_code): v.signals for c in bundle.coverage for v in c.views}
    confirmation_ids = [c.confirmation_id for c in [*bundle.identity_confirmations, *bundle.coverage_confirmations]]
    outcome = summarise_parts(bundle.observations, bundle.part_predictions, bundle.identity_confirmations,
                              bundle.coverage_confirmations, context=context(confirmation_ids=confirmation_ids),
                              config=CFG, view_signals=signals, photo_ids=[f.file_id for f in bundle.files])

    def groups(summaries):
        return sorted((g.identity_status, g.part_code or "", g.side, tuple(sorted(g.member_observation_ids)),
                       g.representative_observation_id, tuple(g.reasons)) for g in summaries)

    def coverage(rows):
        return sorted((c.part_code, c.side, c.state, tuple(c.reasons), tuple(sorted(c.covering_photo_ids)),
                       c.coverage_confirmation_id, tuple(c.identity_confirmation_ids)) for c in rows)

    assert groups(outcome.summaries) == groups(bundle.part_summaries)
    assert coverage(outcome.coverage) == coverage(bundle.coverage)


def test_observations_with_missing_part_masks_stay_visible():
    outcome = run([obs("o_1", "ph_01", "dent", None, reason="part_masks_missing")], [])
    [group] = outcome.summaries
    assert (group.identity_status, group.reasons) == ("unresolved", ["part_masks_missing"])
    assert outcome.unresolved_observation_ids == ("o_1",)
