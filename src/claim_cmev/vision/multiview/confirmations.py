"""The M3 confirmation index, run context, reuse lineage and job-key contribution.

An ``IdentityConfirmation`` states that one photo shows one physical part with a side;
a ``CoverageConfirmation`` states that named views show enough of one physical part.
They are separate human records and never rewrite a model row. When the surveyor
repeats a statement for the same photo/part/side, the latest confirmation wins.
Different sides on the same photo/part are retained as a conflict: the photo-level
contract cannot assign individual predictions to physical sides. Neither side wins.

The job key includes the set of supplied confirmation IDs, so a new confirmation is a
genuine recomputation while a plain redelivery is not (M3 "Duplicate delivery").
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
import hashlib

from ...contracts.common import SIDES, ContractError, Provenance, make_job_key
from ...contracts.imaging import CoverageConfirmation, IdentityConfirmation, ImageDamageObservation, PartPrediction

TASK = "part_summary"
CONFIRMATION_SET_KEY = "confirmation_set"
_SIDE_ORDER = {side: i for i, side in enumerate(SIDES)}


@dataclass(frozen=True)
class SummaryContext:
    """Identity, pinned versions and provenance copied onto every M3 record of one run.

    ``reuse_from_input_revision`` names the earlier revision whose M1/M2 rows are reused
    after a human confirmation created this revision; no neural model reruns.
    """

    claim_id: str
    input_revision: int
    job_key: str
    versions: Mapping[str, str]
    provenance: Provenance
    reuse_from_input_revision: int | None = None


@dataclass(frozen=True)
class ReuseLineage:
    """``provenance.reused_from`` as data: the reused revision and the models behind it."""

    input_revision: int
    parts_models: tuple[str, ...]
    damage_models: tuple[str, ...]

    def derivation_refs(self) -> list[str]:
        refs = [f"reused_from:input_revision={self.input_revision}"]
        refs += [f"reused_from:parts_model={m}" for m in self.parts_models]
        refs += [f"reused_from:damage_model={m}" for m in self.damage_models]
        return refs


def _latest_key(record: IdentityConfirmation | CoverageConfirmation) -> tuple:
    return (record.review_revision, record.recorded_at, record.confirmation_id)


@dataclass(frozen=True)
class ConfirmationIndex:
    identity: Mapping[tuple[str, str], IdentityConfirmation] = field(default_factory=dict)
    """(photo_id, part_code) -> the latest identity confirmation."""
    coverage: Mapping[tuple[str, str], CoverageConfirmation] = field(default_factory=dict)
    """(part_code, side) -> the latest coverage confirmation."""
    supplied_ids: tuple[str, ...] = ()
    superseded_ids: tuple[str, ...] = ()
    conflicting_identity_ids: tuple[str, ...] = ()

    def identity_side(self, photo_id: str, part_code: str) -> str | None:
        found = self.identity.get((photo_id, part_code))
        return found.side if found else None

    def resolved_sides(self, part_code: str) -> tuple[str, ...]:
        sides = {c.side for (_, part), c in self.identity.items() if part == part_code}
        return tuple(sorted(sides, key=_SIDE_ORDER.__getitem__))

    def confirmed_photos(self, part_code: str, side: str | None = None) -> tuple[str, ...]:
        """Photos with a confirmed identity for ``part_code`` (and ``side`` when given)."""
        return tuple(sorted(photo for (photo, part), c in self.identity.items()
                            if part == part_code and (side is None or c.side == side)))

    def identity_ids(self, part_code: str, side: str) -> tuple[str, ...]:
        return tuple(sorted(c.confirmation_id for (_, part), c in self.identity.items()
                            if part == part_code and c.side == side))

    def coverage_for(self, part_code: str, side: str) -> CoverageConfirmation | None:
        return self.coverage.get((part_code, side))

    @property
    def contributing_ids(self) -> tuple[str, ...]:
        """The confirmations in force after superseded ones are removed."""
        return tuple(sorted({c.confirmation_id for c in self.identity.values()}
                            | {c.confirmation_id for c in self.coverage.values()}))

    @property
    def signature(self) -> str:
        return confirmation_set_signature(self.supplied_ids)


def confirmation_set_signature(confirmation_ids: Iterable[str]) -> str:
    """Stable digest of a confirmation ID set; ``none`` for the empty set."""
    ids = sorted(set(confirmation_ids))
    if not ids:
        return "none"
    return hashlib.sha256("|".join(ids).encode()).hexdigest()[:16]


def build_confirmation_index(identity_confirmations: Sequence[IdentityConfirmation],
                             coverage_confirmations: Sequence[CoverageConfirmation], *,
                             claim_id: str | None = None, input_revision: int | None = None,
                             photo_ids: Iterable[str] | None = None) -> ConfirmationIndex:
    """Index confirmations; reject ones from another claim, a later revision or an unknown photo.

    A confirmation recorded for a later input revision must never change an earlier
    revision's result, so supplying one is an error rather than a silent filter.
    """
    known_photos = None if photo_ids is None else set(photo_ids)
    by_id: dict[str, IdentityConfirmation | CoverageConfirmation] = {}
    for record in [*identity_confirmations, *coverage_confirmations]:
        if claim_id is not None and record.claim_id != claim_id:
            raise ContractError("claim_mismatch", f"confirmation {record.confirmation_id} is for another claim")
        if input_revision is not None and record.input_revision > input_revision:
            raise ContractError("confirmation_from_later_revision",
                                f"{record.confirmation_id} belongs to input revision {record.input_revision}")
        photos = [record.photo_id] if isinstance(record, IdentityConfirmation) else record.covering_photo_ids
        if known_photos is not None and set(photos) - known_photos:
            raise ContractError("confirmation_unknown_photo", f"{record.confirmation_id} names an unknown photo")
        previous = by_id.get(record.confirmation_id)
        if previous is not None and previous != record:
            raise ContractError("confirmation_id_conflict", f"{record.confirmation_id} was supplied twice, differently")
        by_id[record.confirmation_id] = record  # an identical redelivery collapses

    identity: dict[tuple[str, str], IdentityConfirmation] = {}
    coverage: dict[tuple[str, str], CoverageConfirmation] = {}
    identity_sides: dict[tuple[str, str], set[str]] = {}
    for record in by_id.values():
        if isinstance(record, IdentityConfirmation):
            identity_sides.setdefault((record.photo_id, record.part_code), set()).add(record.side)
    conflicting = {key for key, sides in identity_sides.items() if len(sides) > 1}
    conflict_ids = []
    superseded: set[str] = set()
    for record in sorted(by_id.values(), key=_latest_key):
        if isinstance(record, IdentityConfirmation):
            key, target = (record.photo_id, record.part_code), identity
            if key in conflicting:
                conflict_ids.append(record.confirmation_id)
                continue  # photo-level identity cannot choose between physical sides
        else:
            key, target = (record.part_code, record.side), coverage
        if key in target:
            superseded.add(target[key].confirmation_id)
        target[key] = record
    return ConfirmationIndex(identity=identity, coverage=coverage, supplied_ids=tuple(sorted(by_id)),
                             superseded_ids=tuple(sorted(superseded)),
                             conflicting_identity_ids=tuple(sorted(conflict_ids)))


def summary_job_versions(versions: Mapping[str, str], confirmation_ids: Iterable[str]) -> dict[str, str]:
    """The versions map for the M3 job key, including the confirmation-set digest."""
    return {**versions, CONFIRMATION_SET_KEY: confirmation_set_signature(confirmation_ids)}


def summary_job_key(claim_id: str, input_revision: int, versions: Mapping[str, str],
                    confirmation_ids: Iterable[str]) -> str:
    """Canonical M3 job key: a new confirmation set is a new job; a redelivery is not."""
    return make_job_key(claim_id, input_revision, TASK, summary_job_versions(versions, confirmation_ids))


# ---------------------------------------------------------------- input checks
REQUIRED_VERSIONS = ("summary_config", "taxonomy")


def check_inputs(context: SummaryContext, config_version: str, observations: Sequence[ImageDamageObservation],
                 predictions: Sequence[PartPrediction]) -> ReuseLineage | None:
    """Validate identity, revisions, versions and provenance; return the reuse lineage."""
    missing = [key for key in REQUIRED_VERSIONS if not context.versions.get(key)]
    if missing:
        raise ContractError("versions_incomplete", f"summary versions lack {missing}")
    if context.versions["summary_config"] != config_version:
        raise ContractError("summary_config_version_mismatch",
                            f"{context.versions['summary_config']!r} != {config_version!r}")
    if not context.versions["taxonomy"].startswith("parts-"):
        raise ContractError("taxonomy_version_mismatch", "summary rows pin the parts taxonomy (parts-x.y.z)")
    reuse = context.reuse_from_input_revision
    if reuse is not None and not 1 <= reuse < context.input_revision:
        raise ContractError("invalid_reuse_revision", f"cannot reuse revision {reuse} for {context.input_revision}")
    allowed = {context.input_revision} if reuse is None else {context.input_revision, reuse}
    fixture = False
    for record in [*observations, *predictions]:
        if record.claim_id != context.claim_id:
            raise ContractError("claim_mismatch", f"{type(record).__name__} belongs to another claim")
        if record.input_revision not in allowed:
            raise ContractError("stale_input_revision",
                                f"{type(record).__name__} from revision {record.input_revision}, expected {sorted(allowed)}")
        fixture |= record.provenance.source_kind == "fixture"
    if fixture and context.provenance.source_kind != "fixture":
        raise ContractError("fixture_provenance_required", "rows derived from fixtures stay labelled as fixtures")
    if reuse is None:
        return None
    reused = [r for r in [*observations, *predictions] if r.input_revision == reuse]
    return ReuseLineage(
        input_revision=reuse,
        parts_models=tuple(sorted({r.versions["parts_model"] for r in reused if r.versions.get("parts_model")})),
        damage_models=tuple(sorted({r.versions["damage_model"] for r in reused if r.versions.get("damage_model")})))


def output_provenance(context: SummaryContext, lineage: ReuseLineage | None) -> Provenance:
    if lineage is None:
        return context.provenance
    refs = [*context.provenance.derivation_refs, *lineage.derivation_refs()]
    return context.provenance.model_copy(update={"derivation_refs": list(dict.fromkeys(refs))})
