"""Hand-built structured inputs for the M8 experiment A rule tests (imported by name).

These are contract records authored by hand, never model output. ``World`` assembles one
claim revision: line items, pen marks, and image evidence per physical part.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
import hashlib

from claim_cmev.comparison import ConsolidationRequest, consolidate, load_rule_config
from claim_cmev.contracts.common import COST_BASIS, Provenance
from claim_cmev.contracts.documents import DeclarationCompleteness, FieldUncertainty, LineItem, PenMark
from claim_cmev.contracts.imaging import (
    AssignmentCandidate,
    CoverageConfirmation,
    IdentityConfirmation,
    ImageDamageObservation,
    MaskRef,
    PartCoverage,
    PartSummary,
)
from claim_cmev.costs.reference import PinnedCostTable

CLAIM = "01K6F1XTVRE000000000M8TEST"
NOW = datetime(2026, 9, 24, 8, 0, tzinfo=UTC)
PROV = Provenance(source_kind="fixture", runtime_profile="lean", producer_service="tests-m8")
TABLE = "t-m8-1"
CONFIG = load_rule_config()
PINNED = {"damage_model": "fixture-damage/0.0.0", "parts_model": "fixture-parts/0.0.0",
          "parser_config": "fixture-parser/0.0.0", "summary_config": "fixture-summary/0.0.0"}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def range_row(range_id: str, part: str, operation: str, lower: str | None, upper: str | None, *,
              vehicle_class: str = "sedan_standard", support: int = 47, withheld: str | None = None,
              table: str = TABLE) -> dict:
    row = {"schema_version": "0.2.0", "range_id": range_id, "part_code": part, "operation": operation,
           "vehicle_class": vehicle_class, "currency": "SGD", "cost_basis": COST_BASIS,
           "support_status": "withheld" if withheld else "supported", "lower_amount": lower, "upper_amount": upper,
           "independent_base_case_count": support, "record_count": support * 3, "withheld_reason": withheld,
           "method": "empirical_percentile", "nominal_coverage": "0.90", "as_of_date": "2026-09-22",
           "cutoff_date": "2026-06-30", "table_version": table, "synthetic": True,
           "provenance": {"source_kind": "synthetic", "generator_version": "gen-test", "seed": 7}}
    return row


DEFAULT_ROWS = (
    range_row("r-fd-repair", "front-door", "repair", "380.00", "620.00"),
    range_row("r-fd-paint", "front-door", "paint", "150.00", "300.00"),
    range_row("r-bd-repair", "back-door", "repair", "400.00", "700.00"),
    range_row("r-hood-replace", "hood", "replace", "754.57", "1146.14"),
    range_row("r-fb-replace", "front-bumper", "replace", "620.00", "1020.00"),
    range_row("r-mirror-replace", "mirror", "replace", None, None, support=3, withheld="insufficient_support"),
)


def table(rows=DEFAULT_ROWS, version: str = TABLE) -> PinnedCostTable:
    return PinnedCostTable.from_rows(version, [dict(r, table_version=version) for r in rows],
                                     min_independent_support=5)


RANGES = table()


@dataclass(frozen=True)
class StubResult:
    """An in-memory stand-in for M7 ``RangeResult`` (for shapes M7 refuses to publish)."""

    table_version: str
    support_status: str
    reason_code: str | None
    lower_amount: Decimal | None
    upper_amount: Decimal | None
    currency: str = "SGD"
    cost_basis: str = COST_BASIS
    range_id: str | None = None
    independent_base_case_count: int | None = None


def supported(lower: str, upper: str, *, range_id: str = "r-stub", table_version: str = TABLE) -> StubResult:
    return StubResult(table_version, "supported", None, Decimal(lower), Decimal(upper), range_id=range_id,
                      independent_base_case_count=12)


class StubRanges:
    def __init__(self, results: dict[tuple[str, str, str], StubResult], table_version: str = TABLE):
        self.table_version, self.results, self.calls = table_version, results, []

    def lookup(self, key):
        self.calls.append(key)
        found = self.results.get((key.part_code, key.operation, key.vehicle_class))
        return found or StubResult(self.table_version, "absent", "no_key", None, None)


def mask(name: str, photo: str) -> MaskRef:
    return MaskRef(artifact_id=f"mk-{name}", object_uri=f"s3://cmev-derived/t/{name}.png", sha256=sha(name),
                   width=512, height=512, encoding="class_index_png", source_photo_id=photo)


@dataclass
class World:
    """One claim input revision built by hand."""

    input_revision: int = 1
    vehicle_class: str = "sedan_standard"
    image_state: str = "complete"
    document_state: str = "complete"
    items: list[LineItem] = field(default_factory=list)
    marks: list[PenMark] = field(default_factory=list)
    observations: list[ImageDamageObservation] = field(default_factory=list)
    summaries: list[PartSummary] = field(default_factory=list)
    coverage: list[PartCoverage] = field(default_factory=list)
    identities: list[IdentityConfirmation] = field(default_factory=list)
    coverage_confirmations: list[CoverageConfirmation] = field(default_factory=list)
    declaration: DeclarationCompleteness | None = None

    @property
    def scope(self) -> dict:
        return {"claim_id": CLAIM, "input_revision": self.input_revision, "provenance": PROV}

    # ------------------------------------------------------------------ document branch
    def item(self, entry_id: str, part: str | None = "front-door", side: str = "left", operation: str | None = "repair",
             amount: str | None = "500.00", *, quantity: str | None = "1", currency: str = "SGD",
             basis: str = COST_BASIS, uncertain: tuple[tuple[str, str], ...] = (), side_source: str | None = None,
             part_status: str | None = None, page_id: str = "dp1") -> str:
        row = len(self.items)
        top = round(0.2 + 0.03 * row, 4)
        notes = list(uncertain)
        if part is None:
            notes.append(("part_code", "part_text_unmapped"))
        if operation is None:
            notes.append(("operation", "operation_text_unmapped"))
        if quantity is None:
            notes.append(("quantity", "column_not_located"))
        if amount is None:
            notes.append(("printed_line_amount", "ocr_low_confidence"))
        notes.append(("unit_price", "column_not_located"))
        self.items.append(LineItem(
            **self.scope, versions={"parser_config": "fixture-parser/0.0.0"}, entry_id=entry_id, page_id=page_id,
            page_number=1, row_box_norm=(0.08, top, 0.95, top + 0.025), amount_box_norm=(0.82, top, 0.93, top + 0.025),
            original_part_text=(part or "???").upper(), original_operation_text=(operation or "???").upper(),
            original_amount_text=amount, part_code=part,
            part_mapping_status=part_status or ("resolved" if part else "unmapped"), side=side,
            side_source=side_source or ("absent" if side == "unknown" else "document_text"), operation=operation,
            operation_mapping_status="resolved" if operation else "unmapped", quantity=quantity, unit_price=None,
            printed_line_amount=amount, effective_price=amount,
            effective_price_source="printed" if amount else "unresolved",
            effective_price_reason=None if amount else "amount_unreadable", currency=currency, cost_basis=basis,
            field_uncertainty=[FieldUncertainty(field=f, reason=r) for f, r in notes]))
        return entry_id

    def mark(self, mark_id: str, entry: str | None, kind: str = "exclusion", state: str = "pending", *,
             candidates: tuple[str, ...] | None = None, amount: str | None = None, origin: str = "detector",
             currency: str = "SGD", basis: str = COST_BASIS) -> str:
        human = origin == "human_added"
        decided = state != "pending"
        link = "human_link" if human else ("unambiguous_row_overlap" if entry else "mark_between_rows")
        self.marks.append(PenMark(
            **self.scope, versions={"penmark_model": "fixture-penmarks/0.0.0"}, mark_id=mark_id, page_id="dp1",
            box_norm=(0.8, 0.2, 0.93, 0.22), mark_type=kind, detection_confidence=None if human else 0.82,
            entry_id=entry, candidate_entry_ids=list(candidates if candidates is not None else ([entry] if entry else [])),
            link_reason=link, state=state, confirmed_amount=amount, confirmed_currency=currency if amount else None,
            confirmed_cost_basis=basis if amount else None,
            decision_action_id=f"ra-{mark_id}" if decided else None, decided_by="surveyor:t" if decided else None,
            decided_at=NOW if decided else None, review_revision=3 if decided else None, origin=origin))
        return mark_id

    def complete(self, state: str = "complete", *, human: bool = False, reasons: tuple[str, ...] = ()) -> None:
        extra = {"confirmed_by": "surveyor:t", "confirmed_at": NOW, "review_revision": 4} if human else {}
        self.declaration = DeclarationCompleteness(
            **self.scope, versions={"parser_config": "fixture-parser/0.0.0"}, state=state,
            reasons=list(reasons or (() if state == "complete" else (f"{state}_declaration",))),
            unparsed_region_count=0, layout_family="family-a-ruled-grid",
            source="human_confirmation" if human else "parser", **extra)

    # ------------------------------------------------------------------ image branch
    def _observation(self, oid: str, photo: str, part: str | None, damage: str, confidence: float,
                     candidates: tuple[tuple[str, float], ...] | None = None) -> ImageDamageObservation:
        cands = candidates or ((part, 0.9),)
        assigned = part is not None
        obs = ImageDamageObservation(
            **self.scope, versions={"taxonomy": "damage-cardd-1.0.0", "damage_model": "fixture-damage/0.0.0"},
            observation_id=oid, photo_id=photo, damage_code=damage, damage_confidence=confidence,
            assignment_status="assigned" if assigned else "unresolved", part_code=part,
            part_reason=None if assigned else "ambiguous_between_parts",
            candidates=[AssignmentCandidate(part_code=p, containment=c, rank=i + 1) for i, (p, c) in enumerate(cands)],
            primary_containment=cands[0][1], runner_up_containment=cands[1][1] if len(cands) > 1 else None,
            background_containment=0.05, area_pixels=5000, area_fraction=round(5000 / 262144, 6),
            area_denominator_pixels=262144, damage_mask_ref=mask(f"dm-{oid}", photo), part_mask_ref=mask(f"pm-{photo}", photo),
            bbox_norm=(0.3, 0.4, 0.5, 0.6), assignment_config_version="fixture-assign/0.0.0")
        self.observations.append(obs)
        return obs

    def _summary(self, sid: str, members: list[ImageDamageObservation], status: str, part: str | None, side: str,
                 identity_ids: list[str]) -> None:
        largest = max(members, key=lambda o: o.area_fraction)
        self.summaries.append(PartSummary(
            **self.scope, versions={"summary_config": "fixture-summary/0.0.0"}, summary_id=sid, identity_status=status,
            part_code=part, side=side, member_observation_ids=[o.observation_id for o in members],
            observation_count=len(members), damage_codes=sorted({o.damage_code for o in members}),
            supporting_photo_ids=sorted({o.photo_id for o in members}), representative_area_fraction=largest.area_fraction,
            representative_observation_id=largest.observation_id, max_confidence=max(o.damage_confidence for o in members),
            identity_confirmation_ids=identity_ids,
            reasons=[] if status == "resolved" else ["identity_not_resolved" if status == "part_only" else "ambiguous"]))

    def seen(self, part: str, side: str, damage: tuple[tuple[str, float], ...] = (("dent", 0.8),), *,
             coverage: str = "adequate", record_coverage_confirmation: bool = True,
             reasons: tuple[str, ...] = ()) -> str:
        """A physical part identified by a recorded identity confirmation, with its coverage slot."""
        key = f"{part}-{side}"
        photo = f"ph-{key}"
        identity = IdentityConfirmation(**self.scope, confirmation_id=f"ic-{key}", actor="surveyor:t", recorded_at=NOW,
                                        review_revision=1, photo_id=photo, part_code=part, side=side)
        self.identities.append(identity)
        coverage_id = None
        if coverage == "adequate":
            coverage_id = f"cc-{key}"
            if record_coverage_confirmation:
                self.coverage_confirmations.append(CoverageConfirmation(
                    **self.scope, confirmation_id=coverage_id, actor="surveyor:t", recorded_at=NOW, review_revision=2,
                    part_code=part, side=side, covering_photo_ids=[photo], covers_enough=True))
        default_reasons = {"inadequate": ("cropped_at_border",), "not_visible": ("no_accepted_part_mask",),
                           "unresolved": ("processing_failed",)}
        self.coverage.append(PartCoverage(
            **self.scope, versions={"summary_config": "fixture-summary/0.0.0"}, coverage_id=f"pc-{key}", part_code=part,
            side=side, state=coverage, covering_photo_ids=[photo] if coverage == "adequate" else [],
            reasons=[] if coverage == "adequate" else list(reasons or default_reasons[coverage]),
            coverage_confirmation_id=coverage_id, identity_confirmation_ids=[identity.confirmation_id]))
        members = [self._observation(f"ob-{key}-{i}", photo, part, code, conf) for i, (code, conf) in enumerate(damage)]
        if members:
            self._summary(f"ps-{key}", members, "resolved", part, side, [identity.confirmation_id])
        return photo

    def unsided(self, part: str, damage: tuple[tuple[str, float], ...] = (("dent", 0.8),)) -> None:
        """Damage assigned to a part category with no identity confirmation: side stays unknown."""
        photo = f"ph-{part}-unsided"
        members = [self._observation(f"ob-{part}-u{i}", photo, part, code, conf) for i, (code, conf) in enumerate(damage)]
        if members:
            self._summary(f"ps-{part}-unsided", members, "part_only", part, "unknown", [])
        self.coverage.append(PartCoverage(
            **self.scope, versions={"summary_config": "fixture-summary/0.0.0"}, coverage_id=f"pc-{part}-unknown",
            part_code=part, side="unknown", state="unresolved", reasons=["identity_not_resolved"]))

    def unresolved(self, oid: str, candidates: tuple[tuple[str, float], ...], damage: str = "scratch",
                   confidence: float = 0.7) -> None:
        obs = self._observation(oid, "ph-straddle", None, damage, confidence, candidates)
        self._summary(f"ps-{oid}", [obs], "unresolved", None, "unknown", [])

    # ------------------------------------------------------------------ request
    def request(self, *, assessment_revision: int = 1, **overrides) -> ConsolidationRequest:
        data = dict(claim_id=CLAIM, input_revision=self.input_revision, assessment_revision=assessment_revision,
                    image_branch_state=self.image_state, document_branch_state=self.document_state,
                    vehicle_class=self.vehicle_class, currency="SGD", part_summaries=self.summaries,
                    coverage=self.coverage, observations=self.observations, identity_confirmations=self.identities,
                    coverage_confirmations=self.coverage_confirmations, line_items=self.items, pen_marks=self.marks,
                    declaration=self.declaration, cost_table_version=TABLE,
                    rules_config_version=CONFIG.rules_config_version, pinned_versions=PINNED, provenance=PROV,
                    created_at=NOW)
        data.update(overrides)
        return ConsolidationRequest(**data)

    def run(self, *, config=CONFIG, ranges=RANGES, **overrides):
        if self.declaration is None and self.document_state == "complete":
            self.complete()
        return consolidate(self.request(**overrides), config=config, ranges=ranges)


def finding(result, entry_id: str):
    return next(f for f in result.assessment.findings if f.entry_id == entry_id)


def codes(finding_or_check) -> list[str]:
    return [r.code for r in finding_or_check.reasons]


def checks(f) -> dict[str, str]:
    return f.checks


def emitted_codes(result) -> set[str]:
    """Every reason code an assessment carries, wherever it is stored."""
    a = result.assessment
    found = set(a.incomplete_reasons) | {r.code for r in a.missing_repairs_check.reasons}
    for f in a.findings:
        found |= {r.code for r in f.reasons}
        for check in (f.documentary_check, f.mark_state_check, f.photographic_check):
            found |= {r.code for r in check.reasons}
        found |= {c for c in (f.cost_check.reason_code, f.cost_check.normalised_score_reason, f.applied_range_reason) if c}
    for addition in a.possible_additions + a.suppressed_additions:
        found.add(addition.reason.code)
    return found
