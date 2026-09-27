"""Claim input and file records (data contracts section 5).

Model year is display metadata only; it never enters a cost key. Object keys are
generated, so no user-supplied file name ever forms a storage path.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, model_validator

from .common import (
    ArtifactRef,
    ClaimScoped,
    ContractModel,
    Currency,
    Identifier,
    ReasonCode,
    RecordId,
    Revision,
    Sha256,
    UtcDatetime,
    VehicleClass,
    require_reasons,
)


_GENERATED_ID = re.compile(r"[0-9A-HJKMNP-TV-Z]{26}|[a-z]{2,8}-[0-9a-f]{24}")
"""The two server-generated identifier forms: ``runtime.uid()`` (ULID) and ``deterministic_id``."""
_KEY_EXTENSION = re.compile(r"\.[a-z0-9]{1,8}")


def generated_object_key(object_uri: str, file_id: str) -> bool:
    """True when the URI's object name is a generated identifier, never a user file name.

    The final path segment, minus at most one short extension, must be the file's own
    ``file_id`` or a generated identifier. The user's name is irrelevant to the decision,
    so ``evidence.jpg`` or ``01J8.jpg`` are accepted exactly like any other name.
    """
    name = object_uri.rstrip("/").rsplit("/", 1)[-1]
    stem, dot, extension = name.rpartition(".")
    if dot and stem and _KEY_EXTENSION.fullmatch(dot + extension):
        name = stem
    return name == file_id or bool(_GENERATED_ID.fullmatch(name))


class ReusedArtifact(ContractModel):
    """A prior-revision artifact explicitly carried forward, with its lineage."""

    artifact_id: RecordId
    kind: str = Field(min_length=1)
    source_input_revision: Revision
    producing_job_key: str = Field(min_length=1)


class ClaimInput(ClaimScoped):
    external_reference: str | None
    external_reference_reason: ReasonCode | None = None
    make: str = Field(min_length=1)
    model: str = Field(min_length=1)
    year: int | None = Field(default=None, ge=1900, le=2100)
    trim: str | None = None
    adas_features: list[str] = Field(default_factory=list)
    vehicle_class: VehicleClass
    vehicle_class_reason: ReasonCode | None = None
    currency: Currency
    cost_basis: Identifier
    file_ids: list[RecordId] = Field(default_factory=list)
    created_at: UtcDatetime
    previous_input_revision: Revision | None
    reused_artifacts: list[ReusedArtifact] = Field(default_factory=list)

    @model_validator(mode="after")
    def _rules(self) -> ClaimInput:
        require_reasons(self.__dict__, [("external_reference", "external_reference_reason")])
        if self.vehicle_class == "unknown" and not self.vehicle_class_reason:
            raise ValueError("vehicle_class 'unknown' needs vehicle_class_reason")
        if (self.previous_input_revision is None) != (self.input_revision == 1):
            raise ValueError("only input revision 1 has no previous input revision")
        if self.previous_input_revision is not None and self.previous_input_revision >= self.input_revision:
            raise ValueError("previous_input_revision precedes input_revision")
        if any(r.source_input_revision >= self.input_revision for r in self.reused_artifacts):
            raise ValueError("a reused artifact comes from an earlier input revision")
        if len(set(self.file_ids)) != len(self.file_ids):
            raise ValueError("file_ids are unique")
        return self


class ClaimFile(ClaimScoped):
    """One uploaded original. ``input_revision`` is the revision that first stored it."""

    file_id: RecordId
    kind: Literal["photo", "estimate_page"]
    member_revisions: list[Revision] = Field(min_length=1)
    original_name: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1)
    sha256: Sha256
    byte_count: int = Field(ge=0)
    object_ref: ArtifactRef
    upload_status: Literal["pending", "stored", "failed"]
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    exif_orientation: int | None = Field(default=None, ge=1, le=8)
    page_count: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _rules(self) -> ClaimFile:
        if self.input_revision not in self.member_revisions:
            raise ValueError("member_revisions include the revision that stored the file")
        if self.object_ref.sha256 != self.sha256 or self.object_ref.byte_count != self.byte_count:
            raise ValueError("the object reference must describe the same bytes")
        if not generated_object_key(self.object_ref.object_uri, self.file_id):
            raise ValueError("object keys are generated; a user-supplied name never forms a storage path")
        if self.upload_status == "stored":
            if self.media_type == "application/pdf":
                if self.page_count is None:
                    raise ValueError("a stored PDF records its page count")
            elif self.width is None or self.height is None:
                raise ValueError("a stored image records its dimensions")
        return self
