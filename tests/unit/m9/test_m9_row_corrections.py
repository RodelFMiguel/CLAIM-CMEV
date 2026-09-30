"""Row corrections as the consolidator replays them: never an invalid row, never an inferred side."""
import pytest

from claim_cmev.contracts.fixtures import fixture_bundle
from claim_cmev.orchestration.corrections import correct_line_item

CLAIM = "01K50000000000000000000001"


def bumper():
    items = fixture_bundle("exclusion_and_supported", CLAIM, 1).line_items
    item = next(i for i in items if i.part_code == "front-bumper")
    assert (item.side, item.side_source) == ("not_applicable", "absent")  # unsided part, no printed side
    return item


@pytest.mark.parametrize("part", ["fender", "unknown"])
def test_moving_to_a_sided_or_unknown_part_leaves_the_side_unknown(part):
    row = correct_line_item(bumper(), {"part_code": part}, input_revision=2, action_id="ra-1")
    assert (row.side, row.side_source) == ("unknown", "absent")
    assert row.part_code == (None if part == "unknown" else part)


def test_moving_to_another_unsided_part_keeps_not_applicable():
    row = correct_line_item(bumper(), {"part_code": "hood"})
    assert (row.part_code, row.side, row.side_source) == ("hood", "not_applicable", "absent")


def test_an_explicit_side_is_a_human_correction():
    row = correct_line_item(bumper(), {"part_code": "fender", "side": "left"})
    assert (row.side, row.side_source) == ("left", "human_correction")
