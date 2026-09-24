"""Cost records (data contracts section 8, M7 row schema).

The v2 cost key is exactly part, operation, vehicle class and currency under the one
fixed basis ``single_part_pre_tax_no_discount_v1``. A withheld or missing range is
null with a reason, never ``[0, 0]``; support counts independent base cases.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from .common import (
    COST_BASIS,
    ClaimId,
    ContractError,
    ContractModel,
    CostOperation,
    CostVehicleClass,
    Currency,
    DecimalStr,
    Identifier,
    Money,
    PartCode,
    Provenance,
    ReasonCode,
    RecordId,
    Revision,
    SchemaVersion,
    SCHEMA_VERSION,
    Sha256,
    UtcDatetime,
    to_decimal,
)

COST_KEY_FIELDS: tuple[str, ...] = ("part_code", "operation", "vehicle_class", "currency")
CostBasis = Literal["single_part_pre_tax_no_discount_v1"]
CostCheckResult = Literal["within_range", "outside_range", "insufficient_support", "not_evaluated"]
WithheldReason = Literal["insufficient_support", "no_records", "unsupported_combination"]


def check_cost_key_fields(fields: Sequence[str]) -> None:
    """Refuse a cost table whose manifest ``cost_key_fields`` is not the v2 key.

    A v1 table keyed by side, damage type or model year is not loadable by a v2 runtime.
    """
    if tuple(fields) != COST_KEY_FIELDS:
        raise ContractError("schema_unsupported", f"cost_key_fields {list(fields)} is not the v2 key {list(COST_KEY_FIELDS)}")


class CostKey(ContractModel):
    """Exactly four fields. Side, damage type and model year are deliberately absent."""

    part_code: PartCode
    operation: CostOperation
    vehicle_class: CostVehicleClass
    currency: Currency


class GeneratorProvenance(ContractModel):
    """Generator reference, version and seed of a synthetic range (M7 row schema)."""

    source_kind: Literal["synthetic", "fixture"]
    generator_version: str = Field(min_length=1)
    seed: int
    generator_ref: str | None = None


class ObservedCalibration(ContractModel):
    coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    mean_width: Money | None = None
    report_ref: str | None = None


class ReferenceCostRange(ContractModel):
    """One row of a published, immutable reference table (data contracts section 8.2)."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    table_version: str = Field(min_length=1)
    range_id: RecordId
    part_code: PartCode
    operation: CostOperation
    vehicle_class: CostVehicleClass
    currency: Currency
    cost_basis: CostBasis = COST_BASIS
    support_status: Literal["supported", "withheld"]
    lower_amount: Money | None
    upper_amount: Money | None
    withheld_reason: WithheldReason | None = None
    independent_base_case_count: int = Field(ge=0)
    record_count: int | None = Field(default=None, ge=0)
    nominal_coverage: DecimalStr = "0.90"
    observed_calibration: ObservedCalibration | None = None
    as_of_date: date
    cutoff_date: date
    method: Literal["empirical_percentile", "lightgbm_quantile"]
    synthetic: Literal[True] = True
    provenance: GeneratorProvenance

    @property
    def key(self) -> CostKey:
        return CostKey(part_code=self.part_code, operation=self.operation,
                       vehicle_class=self.vehicle_class, currency=self.currency)

    @model_validator(mode="after")
    def _rules(self) -> ReferenceCostRange:
        coverage = to_decimal(self.nominal_coverage)
        if not Decimal(0) < coverage < Decimal(1):
            raise ValueError("nominal_coverage must lie strictly between 0 and 1")
        if self.support_status == "supported":
            if self.lower_amount is None or self.upper_amount is None:
                raise ValueError("a supported range needs both bounds")
            if to_decimal(self.lower_amount) > to_decimal(self.upper_amount):
                raise ValueError("crossed bounds can never be published")
            if self.withheld_reason is not None:
                raise ValueError("a supported range has no withheld_reason")
        else:
            if self.lower_amount is not None or self.upper_amount is not None:
                raise ValueError("a withheld range publishes no bounds, never [0, 0] or a fallback")
            if self.withheld_reason is None:
                raise ValueError("a withheld range needs withheld_reason")
        if self.record_count is not None and self.record_count < self.independent_base_case_count:
            raise ValueError("independent base cases cannot exceed records; support counts base cases, not quotes")
        return self


class CostCheck(ContractModel):
    """Data contracts section 8.3. No deviation is computed for incompatible amounts."""

    entry_id: RecordId
    result: CostCheckResult
    amount: Money | None = None
    lower_amount: Money | None = None
    upper_amount: Money | None = None
    range_id: RecordId | None = None
    direction: Literal["below", "above"] | None = None
    absolute_deviation: Money | None = None
    normalised_score: DecimalStr | None = None
    normalised_score_reason: ReasonCode | None = None
    independent_base_case_count: int | None = Field(default=None, ge=0)
    reason_code: ReasonCode | None = None
    policy_version: str = Field(min_length=1)
    cost_table_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def _rules(self) -> CostCheck:
        compared = self.result in ("within_range", "outside_range")
        if not compared:
            if not self.reason_code:
                raise ValueError(f"cost check {self.result!r} needs a reason_code")
            if any(v is not None for v in (self.direction, self.absolute_deviation, self.normalised_score,
                                           self.lower_amount, self.upper_amount, self.range_id)):
                raise ValueError("no range, direction or deviation is recorded when no comparison ran")
            return self
        if None in (self.amount, self.lower_amount, self.upper_amount, self.range_id, self.absolute_deviation):
            raise ValueError("a comparison records the amount, both bounds, the range_id and the deviation")
        a, lo, hi = to_decimal(self.amount), to_decimal(self.lower_amount), to_decimal(self.upper_amount)
        dev = to_decimal(self.absolute_deviation)
        if lo > hi:
            raise ValueError("crossed bounds cannot be applied")
        if self.result == "within_range":
            if not lo <= a <= hi or dev != 0 or self.direction is not None:
                raise ValueError("within_range means L <= a <= U, zero deviation and no direction")
        elif a < lo:
            if self.direction != "below" or dev != lo - a:
                raise ValueError("below the range: direction 'below' and deviation L - a")
        elif a > hi:
            if self.direction != "above" or dev != a - hi:
                raise ValueError("above the range: direction 'above' and deviation a - U")
        else:
            raise ValueError("an amount inside the bounds, equality included, is within_range")
        if lo == hi and self.normalised_score is not None:
            raise ValueError("a zero-width interval has no normalised score")
        return self


class ApprovalRecord(ContractModel):
    """Data contracts section 8.4. Stretch S3 only; empty in the core scope."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    approval_id: RecordId
    source_record_id: str = Field(min_length=1)
    claim_id: ClaimId
    assessment_revision: Revision | None = None
    reviewed_entry_ids: list[RecordId] = Field(min_length=1)
    status: Literal["approved", "rejected", "pending", "superseded"]
    approved_amount: Money | None = None
    currency: Currency | None = None
    cost_basis: Identifier | None = None
    approver: str = Field(min_length=1)
    approved_at: UtcDatetime | None = None
    effective_at: UtcDatetime | None = None
    synthetic: bool
    supersedes_approval_id: RecordId | None = None
    provenance: Provenance

    @property
    def is_cost_eligible(self) -> bool:
        """Only an approved, unsuperseded final amount may feed a reference build."""
        return self.status == "approved"

    @model_validator(mode="after")
    def _rules(self) -> ApprovalRecord:
        money = (self.approved_amount, self.currency, self.cost_basis)
        if any(v is not None for v in money) and any(v is None for v in money):
            raise ValueError("approved_amount, currency and cost_basis travel together")
        if self.status == "approved" and (self.approved_amount is None or self.approved_at is None):
            raise ValueError("an approved record needs its final amount and approval time")
        if self.supersedes_approval_id == self.approval_id:
            raise ValueError("an approval cannot supersede itself")
        return self


class ReferenceBuildMember(ContractModel):
    """One source record considered by a reference build, with its inclusion decision."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    build_id: str = Field(min_length=1)
    table_version: str = Field(min_length=1)
    source_record_id: str = Field(min_length=1)
    source_kind: Literal["approval", "synthetic_seed"]
    source_hash: Sha256
    included: bool
    inclusion_reason: ReasonCode
    cutoff_date: date
    split: Literal["train", "validation", "calibration", "test"] | None
    base_case_id: str = Field(min_length=1)
    weight: float = Field(default=1.0, ge=0.0)
    cost_key: CostKey | None = None

    @model_validator(mode="after")
    def _rules(self) -> ReferenceBuildMember:
        if self.included and self.split is None:
            raise ValueError("an included member is assigned to a split")
        return self


def count_independent_base_cases(members: Iterable[ReferenceBuildMember], *, key: CostKey | None = None,
                                 split: str | None = None) -> int:
    """Distinct base cases among included members; repeated quotes never inflate support."""
    return len({m.base_case_id for m in members
                if m.included and (key is None or m.cost_key == key) and (split is None or m.split == split)})
