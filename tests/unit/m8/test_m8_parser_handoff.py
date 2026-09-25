"""Real M5 parser output with explicit fixture image evidence, not model evaluation."""
from pathlib import Path
import pytest
from claim_cmev.contracts.common import make_job_key
from m8_support import World, CLAIM


@pytest.mark.parametrize("description,part,side,operation,amount,expected", [
    ("FRT BUMPER", "front-bumper", "not_applicable", "REPLACE", "900.00", "ok"),
    ("BONNET", "hood", "not_applicable", "REPLACE", "900.00", "ok"),
    ("FRT DOOR", "front-door", "left", "REPAIR", "500.00", "insufficient_evidence"),
])
def test_parser_identity_reaches_m8_without_invented_side(monkeypatch, description, part, side, operation, amount, expected):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "m5"))
    from m5_support import parse, make_page, estimate_cells, item
    page = make_page(estimate_cells([item(description, operation, "1", amount, amount)])).model_copy(
        update={"claim_id": CLAIM})
    parsed = parse([page], claim_id=CLAIM, job_key=make_job_key(CLAIM, 1, "line_items_extract", {"code":"test"}))
    assert parsed.completeness.state == "complete"
    [row] = parsed.line_items
    assert row.side_source == "absent"
    world = World(items=list(parsed.line_items), declaration=parsed.completeness)
    world.seen(part, side)
    result = world.run(pages=[page])
    assert result.assessment.findings[0].overall_result == expected
    if expected == "insufficient_evidence":
        assert row.side == "unknown"
