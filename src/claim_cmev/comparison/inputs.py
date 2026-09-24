"""Deterministic views over the branch records, shared by the per-item rules and additions.

Nothing here decides a finding. It answers narrow questions the rules ask: which physical
part and side a row names (an uncertain or unreadable value counts as unknown), which pen
marks gate a row, and which image evidence belongs to one resolved physical part. Side
comes only from the document text or a human correction on the row, and only from a
recorded identity confirmation on the image side; nothing is inferred.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from claim_cmev.contracts.common import RESOLVED_SIDES, ContractError
from claim_cmev.contracts.documents import DocumentPage, LineItem, PenMark
from claim_cmev.contracts.imaging import (
    CoverageConfirmation,
    IdentityConfirmation,
    ImageDamageObservation,
    PartCoverage,
    PartSummary,
)

UNKNOWN = "unknown"


def uncertain_fields(item: LineItem) -> frozenset[str]:
    return frozenset(u.field for u in item.field_uncertainty)


def page_states(pages: Iterable[DocumentPage]) -> dict[str, str]:
    return {page.page_id: page.quality.state for page in pages}


def line_item_order(item: LineItem) -> tuple:
    """Stable reading order independent of the order rows were fetched in."""
    return (item.page_number, item.row_box_norm[1], item.row_box_norm[0], item.entry_id)


@dataclass(frozen=True)
class RowIdentity:
    """The physical part a row names. ``None`` part or ``unknown`` side means unresolved."""

    part_code: str | None
    side: str
    readable: bool

    @property
    def resolved(self) -> bool:
        return self.part_code is not None and self.side in RESOLVED_SIDES

    def matches(self, part_code: str, side: str) -> bool:
        """Side-exact identity match; an unresolved side never matches."""
        return self.resolved and self.part_code == part_code and self.side == side

    def could_be(self, part_code: str, side: str) -> bool:
        """True when the row might name this part and side but does not resolve to it."""
        if self.matches(part_code, side):
            return False
        return (self.part_code is None or self.part_code == part_code) and (self.side == UNKNOWN or self.side == side)


def row_identity(item: LineItem, page_state: str | None = None) -> RowIdentity:
    readable = page_state != "unreadable"
    uncertain = uncertain_fields(item)
    part = item.part_code if (readable and item.part_mapping_status == "resolved"
                              and "part_code" not in uncertain) else None
    side = item.side if (readable and item.side in RESOLVED_SIDES and "side" not in uncertain) else UNKNOWN
    return RowIdentity(part, side, readable)


@dataclass(frozen=True)
class RowMarks:
    """The non-rejected pen marks that bear on one row. Rejected marks never count."""

    entry_id: str
    linked: tuple[PenMark, ...]
    unlinked: tuple[PenMark, ...]

    @property
    def pending(self) -> tuple[PenMark, ...]:
        return tuple(m for m in self.linked if m.state == "pending")

    @property
    def conflicting(self) -> bool:
        """M6 rule: two price changes, or an exclusion and a price change, on one row.
        Duplicate exclusion boxes are one exclusion, not a conflict."""
        price_changes = [m for m in self.linked if m.mark_type == "price_change"]
        exclusions = [m for m in self.linked if m.mark_type == "exclusion"]
        return len(price_changes) > 1 or bool(price_changes and exclusions)

    @property
    def blocking_codes(self) -> list[str]:
        """R2 reason codes, conflicting first; empty when the marks allow the row to be checked."""
        codes = []
        if self.conflicting:
            codes.append("mark_conflicting")
        if self.pending:
            codes.append("mark_pending")
        if self.unlinked:
            codes.append("mark_unlinked")
        return codes

    @property
    def confirmed_exclusion(self) -> PenMark | None:
        found = [m for m in self.linked if m.mark_type == "exclusion" and m.state == "confirmed"]
        return min(found, key=lambda m: m.mark_id) if found else None

    @property
    def confirmed_price_change(self) -> PenMark | None:
        found = [m for m in self.linked if m.mark_type == "price_change" and m.state == "confirmed"]
        return found[0] if len(found) == 1 else None

    @property
    def excluded(self) -> bool:
        """R3: a confirmed exclusion, reached only when no mark blocks the row."""
        return not self.blocking_codes and self.confirmed_exclusion is not None

    @property
    def marks(self) -> tuple[PenMark, ...]:
        return tuple(sorted(self.linked + self.unlinked, key=lambda m: m.mark_id))


def row_marks(entry_id: str, marks: Iterable[PenMark]) -> RowMarks:
    linked, unlinked = [], []
    for mark in marks:
        if mark.state == "rejected":
            continue
        if mark.entry_id == entry_id:
            linked.append(mark)
        elif mark.entry_id is None and entry_id in mark.candidate_entry_ids:
            unlinked.append(mark)
    key = lambda m: m.mark_id  # noqa: E731
    return RowMarks(entry_id, tuple(sorted(linked, key=key)), tuple(sorted(unlinked, key=key)))


class EvidenceIndex:
    """Image-branch records indexed by physical part. Refuses internally inconsistent input."""

    def __init__(self, *, part_summaries: Sequence[PartSummary], coverage: Sequence[PartCoverage],
                 observations: Sequence[ImageDamageObservation],
                 identity_confirmations: Sequence[IdentityConfirmation],
                 coverage_confirmations: Sequence[CoverageConfirmation]):
        self.observations = {o.observation_id: o for o in observations}
        self.identity = {c.confirmation_id: c for c in identity_confirmations}
        self.coverage_confirmations = {c.confirmation_id: c for c in coverage_confirmations}
        self.slots: dict[tuple[str, str], PartCoverage] = {}
        for row in coverage:
            slot = (row.part_code, row.side)
            if slot in self.slots:
                raise ContractError("duplicate_coverage_slot", f"two coverage rows for {slot}")
            self.slots[slot] = row
        self.summaries = sorted(part_summaries, key=lambda s: (s.part_code or "", s.side, s.summary_id))
        for summary in self.summaries:
            missing = [m for m in summary.member_observation_ids if m not in self.observations]
            if missing:
                raise ContractError("summary_member_missing", f"{summary.summary_id} members {missing} not supplied")

    def slot(self, part_code: str, side: str) -> PartCoverage | None:
        return self.slots.get((part_code, side))

    def confirmed_identity_ids(self, part_code: str, side: str, confirmation_ids: Iterable[str]) -> list[str]:
        """The supplied identity confirmations among ``confirmation_ids`` that name this part and side."""
        found = []
        for cid in sorted(set(confirmation_ids)):
            confirmation = self.identity.get(cid)
            if confirmation is not None and (confirmation.part_code, confirmation.side) == (part_code, side):
                found.append(cid)
        return found

    def identity_resolved(self, part_code: str, side: str) -> bool:
        """A coverage slot for this exact part and side backed by a recorded identity confirmation."""
        if side not in RESOLVED_SIDES:
            return False
        slot = self.slot(part_code, side)
        return slot is not None and bool(self.confirmed_identity_ids(part_code, side, slot.identity_confirmation_ids))

    def coverage_confirmation(self, slot: PartCoverage) -> CoverageConfirmation | None:
        """The recorded 'covers enough' confirmation behind an adequate slot, when supplied."""
        confirmation = self.coverage_confirmations.get(slot.coverage_confirmation_id or "")
        if confirmation is None or not confirmation.covers_enough:
            return None
        if (confirmation.part_code, confirmation.side) != (slot.part_code, slot.side):
            return None
        return confirmation

    def members(self, summary: PartSummary) -> list[ImageDamageObservation]:
        return [self.observations[m] for m in sorted(summary.member_observation_ids)]

    def summary_resolved(self, summary: PartSummary) -> bool:
        return (summary.identity_status == "resolved" and summary.part_code is not None
                and summary.side in RESOLVED_SIDES
                and bool(self.confirmed_identity_ids(summary.part_code, summary.side,
                                                     summary.identity_confirmation_ids)))

    def resolved_summaries(self, part_code: str, side: str) -> list[PartSummary]:
        return [s for s in self.summaries
                if (s.part_code, s.side) == (part_code, side) and self.summary_resolved(s)]

    def unsided_observations(self, part_code: str) -> list[ImageDamageObservation]:
        """Observations that could be on this part category but whose physical identity is open:
        ``part_only`` groups of the part, and unresolved groups naming it as a candidate."""
        found: dict[str, ImageDamageObservation] = {}
        for summary in self.summaries:
            if summary.identity_status == "part_only" and summary.part_code == part_code:
                found.update((o.observation_id, o) for o in self.members(summary))
            elif summary.identity_status == "unresolved":
                for obs in self.members(summary):
                    if any(c.part_code == part_code for c in obs.candidates):
                        found[obs.observation_id] = obs
            elif (summary.identity_status == "resolved" and summary.part_code == part_code
                  and not self.summary_resolved(summary)):
                found.update((o.observation_id, o) for o in self.members(summary))
        return [found[k] for k in sorted(found)]

    def has_unsided_evidence(self, part_code: str) -> bool:
        """The part was seen but its side is not established by any confirmation."""
        return ((part_code, UNKNOWN) in self.slots
                or any(s.identity_status == "part_only" and s.part_code == part_code for s in self.summaries))


__all__ = ["UNKNOWN", "EvidenceIndex", "RowIdentity", "RowMarks", "line_item_order", "page_states", "row_identity",
           "row_marks", "uncertain_fields"]
