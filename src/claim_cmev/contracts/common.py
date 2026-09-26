"""Shared primitives for contract schema 0.2.0 (data contracts sections 2 and 4).

Every producer and consumer builds records from these types. Money is an exact
decimal string, never a JSON number; a nullable field carries a reason when it is
absent; boxes are normalised, top-left origin and ordered.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Annotated, Any, Literal, get_args

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, WithJsonSchema

SCHEMA_VERSION = "0.2.0"
REJECTED_SCHEMA_VERSIONS = frozenset({"0.1.0"})

# Canonical vocabulary (data contracts section 3). The versioned YAML under
# configs/taxonomy/ is the reviewed source; tests assert these literals match it.
PartCode = Literal[
    "windshield", "back-windshield", "front-window", "back-window", "front-door", "back-door",
    "front-wheel", "back-wheel", "front-bumper", "back-bumper", "headlight", "tail-light", "hood",
    "trunk", "licence-plate", "mirror", "roof", "grille", "rocker-panel", "quarter-panel", "fender",
]
PART_CODES: tuple[str, ...] = get_args(PartCode)
Side = Literal["left", "right", "centre", "not_applicable", "unknown"]
SIDES: tuple[str, ...] = get_args(Side)
RESOLVED_SIDES = frozenset({"left", "right", "centre", "not_applicable"})
DamageCode = Literal["dent", "scratch", "crack", "glass-shatter", "lamp-broken", "tire-flat"]
"""The six CarDD codes, the only damage vocabulary v2 records accept."""
DAMAGE_CODES: tuple[str, ...] = get_args(DamageCode)
Operation = Literal["repair", "replace", "paint", "other", "unknown"]
OPERATIONS: tuple[str, ...] = get_args(Operation)
CostOperation = Literal["repair", "replace", "paint"]
COST_OPERATIONS: tuple[str, ...] = get_args(CostOperation)
CostVehicleClass = Literal["hatchback_small", "sedan_standard", "suv_crossover", "van_commercial"]
"""Proposed four classes (M7); frozen by the team at day 2."""
COST_VEHICLE_CLASSES: tuple[str, ...] = get_args(CostVehicleClass)
VehicleClass = Literal["hatchback_small", "sedan_standard", "suv_crossover", "van_commercial", "unknown"]
VEHICLE_CLASSES: tuple[str, ...] = get_args(VehicleClass)
MappingStatus = Literal["resolved", "ambiguous", "unmapped"]
ProcessingStatus = Literal["pending", "running", "succeeded", "failed"]
COST_BASIS = "single_part_pre_tax_no_discount_v1"
"""The fixed v2 cost basis: SGD, quantity exactly 1, pre-tax, no discount (M7)."""
COST_CURRENCY = "SGD"

ClaimId = Annotated[str, Field(pattern=r"^[0-9A-HJKMNP-TV-Z]{26}$")]
"""ULID in Crockford base32, also the Kafka message key."""
RecordId = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:\-]*$")]
Revision = Annotated[int, Field(ge=1)]
Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9_.\-]*$")]
"""Lower-case identifier such as a cost basis or layout family name."""


class ContractError(ValueError):
    """A contract violation with a stable machine reason code."""

    def __init__(self, reason_code: str, message: str = ""):
        super().__init__(f"{reason_code}: {message}" if message else reason_code)
        self.reason_code = reason_code
        self.message = message


class ContractModel(BaseModel):
    """Base for every shared record: unknown fields are errors and records are immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def check_schema_version(value: str) -> str:
    """Reject v1 and unknown contract versions; never reinterpret them."""
    if value != SCHEMA_VERSION:
        raise ContractError("schema_unsupported", f"schema_version {value!r} is not {SCHEMA_VERSION}")
    return value


SchemaVersion = Annotated[str, AfterValidator(check_schema_version)]

_DECIMAL_TEXT = re.compile(r"^\d{1,12}(\.\d{1,6})?$")


def _decimal_string(value: Any) -> str:
    # bool is an int subclass; floats lose exactness; JSON numbers are refused outright.
    if isinstance(value, Decimal):
        if not value.is_finite() or value < 0:
            raise ValueError("money must be a finite nonnegative decimal")
        value = format(value, "f")
    if not isinstance(value, str):
        raise ValueError("money and quantities travel as decimal strings, never JSON numbers")
    if not _DECIMAL_TEXT.fullmatch(value):
        raise ValueError(f"{value!r} is not a nonnegative decimal string")
    return value


DecimalStr = Annotated[str, BeforeValidator(_decimal_string), Field(pattern=_DECIMAL_TEXT.pattern)]
"""Exact nonnegative decimal string, used for money and quantities."""
Money = DecimalStr


def to_decimal(value: str | None) -> Decimal | None:
    """Parse a contract decimal string exactly; None stays None, never zero."""
    if value is None:
        return None
    try:
        return Decimal(_decimal_string(value))
    except InvalidOperation as exc:  # pragma: no cover - guarded by the pattern
        raise ValueError(value) from exc


Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]
ReasonCode = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=80)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must carry a timezone (RFC 3339 UTC)")
    return value


UtcDatetime = Annotated[datetime, AfterValidator(_utc)]


def _box(value: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = value
    if not all(0.0 <= v <= 1.0 for v in value):
        raise ValueError("normalised box coordinates must lie in [0, 1]")
    if x0 > x1 or y0 > y1:
        raise ValueError("box bounds must be ordered [x_min, y_min, x_max, y_max]")
    return value


BoxNorm = Annotated[tuple[float, float, float, float], AfterValidator(_box), WithJsonSchema({
    "type": "array", "items": {"type": "number", "minimum": 0, "maximum": 1},
    "minItems": 4, "maxItems": 4})]
"""[x_min, y_min, x_max, y_max] normalised to [0, 1], origin top-left."""


def _versions(value: dict[str, str]) -> dict[str, str]:
    if not value or any(not v for v in value.values()):
        raise ValueError("versions map and version values must not be empty")
    return value


Versions = Annotated[dict[str, Annotated[str, Field(min_length=1)]], AfterValidator(_versions), Field(min_length=1)]


class Reason(ContractModel):
    """Stable machine reason code plus readable text; never a bare error string."""

    code: ReasonCode
    message: str = Field(min_length=1)


class Provenance(ContractModel):
    source_kind: Literal["real", "synthetic", "fixture", "explainer"]
    runtime_profile: Literal["lean", "full"]
    producer_service: str = Field(min_length=1)
    source_dataset_id: str | None = None
    derivation_refs: list[str] = Field(default_factory=list)


class ArtifactRef(ContractModel):
    """Bytes are reached only through the backend, never via a presigned browser URL."""

    artifact_id: str = Field(min_length=1)
    object_uri: str = Field(min_length=1)
    sha256: Sha256
    media_type: str = Field(min_length=1)
    byte_count: int = Field(ge=0)


class ClaimScoped(ContractModel):
    """Identity every claim-scoped record carries (data contracts section 2)."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    claim_id: ClaimId
    input_revision: Revision
    provenance: Provenance


class ClaimRecord(ClaimScoped):
    """A claim-scoped machine record that also pins the versions that produced it."""

    versions: Versions


def require_reasons(values: Mapping[str, Any], pairs: Iterable[tuple[str, str]]) -> None:
    """Raise when a nullable field is None without its companion reason field.

    Use inside a ``model_validator(mode="after")`` with ``self.__dict__``.
    """
    for field, reason_field in pairs:
        if values.get(field) is None and not values.get(reason_field):
            raise ValueError(f"{field} is null without {reason_field}")


def version_signature(versions: Mapping[str, str]) -> str:
    """First 8 hex characters of sha256 over the versions map with sorted keys."""
    return hashlib.sha256(json.dumps(dict(versions), sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:8]


def make_job_key(claim_id: str, input_revision: int, task: str, versions: Mapping[str, str], target: str = "all") -> str:
    """Canonical job key: claim, input revision, task, target and version signature."""
    return f"{claim_id}:{input_revision}:{task}:{target}:{version_signature(versions)}"


def deterministic_id(prefix: str, job_key: str, *index: object) -> str:
    """Stable record ID from the job key plus a stable index, so a replay reuses it."""
    material = "|".join([job_key, *map(str, index)])
    return f"{prefix}-{hashlib.sha256(material.encode()).hexdigest()[:24]}"


def dedup_key(topic: str, job_key: str, attempt_epoch: int = 0) -> str:
    return hashlib.sha256(f"{topic}|{job_key}|{attempt_epoch}".encode()).hexdigest()
