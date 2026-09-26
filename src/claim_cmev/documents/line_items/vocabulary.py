"""Reviewed estimate vocabulary: printed part, operation and side text to canonical codes (M5 step 4).

Only listed aliases resolve. Text naming several possible codes, a component of a part,
or a listed alias plus unrecognised words maps to ``ambiguous`` with candidates and a
reason; nothing listed maps to ``unmapped``. Fuzzy matching is off by default and, when
enabled, only produces ``ambiguous`` suggestions. Side comes from printed text only.
"""
from __future__ import annotations

import difflib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator

from claim_cmev import taxonomy
from claim_cmev.contracts.common import OPERATIONS, PART_CODES, ContractError, MappingStatus, Operation, PartCode

from .text import find_phrases, tokens

DEFAULT_VOCABULARY_PATH = Path(__file__).resolve().parents[4] / "configs" / "pipeline" / "m5_estimate_vocabulary.yaml"
VOCABULARY_ENV = "CMEV_M5_VOCABULARY"
SideCode = Literal["left", "right", "centre"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReviewState(_Strict):
    state: Literal["proposed", "reviewed"]
    reviewed_by: str | None
    note: str


class MatchingSpec(_Strict):
    fuzzy_enabled: bool
    fuzzy_min_ratio: float = Field(gt=0, le=1)


class AmbiguousPart(_Strict):
    aliases: tuple[StrictStr, ...] = Field(min_length=1)
    candidates: tuple[PartCode, ...] = Field(min_length=1)
    reason: str = Field(pattern=r"^[a-z][a-z0-9_]*$")


class AmbiguousOperation(_Strict):
    aliases: tuple[StrictStr, ...] = Field(min_length=1)
    candidates: tuple[Operation, ...] = Field(min_length=1)
    reason: str = Field(pattern=r"^[a-z][a-z0-9_]*$")


class VocabularyFile(_Strict):
    schema_version: Literal["0.1.0"]
    vocabulary_version: str = Field(min_length=1)
    status: Literal["proposed", "frozen"]
    review: ReviewState
    taxonomy_version: str
    operations_taxonomy_version: str
    matching: MatchingSpec
    parts: dict[PartCode, tuple[StrictStr, ...]]
    ambiguous_parts: tuple[AmbiguousPart, ...] = ()
    qualifiers: tuple[StrictStr, ...] = ()
    component_terms: tuple[StrictStr, ...] = ()
    operations: dict[Operation, tuple[StrictStr, ...]]
    ambiguous_operations: tuple[AmbiguousOperation, ...] = ()
    sides: dict[SideCode, tuple[StrictStr, ...]]
    ambiguous_sides: tuple[StrictStr, ...] = ()

    @model_validator(mode="after")
    def _unique_after_normalisation(self) -> VocabularyFile:
        owners: dict[tuple[str, ...], str] = {}

        def claim(alias: str, owner: str, single: bool = False) -> None:
            key = tokens(alias)
            if not key:
                raise ValueError(f"{owner}: alias {alias!r} is empty after normalisation")
            if single and len(key) != 1:
                raise ValueError(f"{owner}: {alias!r} must be one token")
            if key in owners:
                raise ValueError(f"alias {alias!r} is listed for both {owners[key]} and {owner}")
            owners[key] = owner

        for code, aliases in self.parts.items():
            for alias in aliases:
                claim(alias, f"part {code}")
        for entry in self.ambiguous_parts:
            for alias in entry.aliases:
                claim(alias, f"ambiguous part {entry.candidates}")
        for word in self.qualifiers:
            claim(word, "qualifier", single=True)
        for word in self.component_terms:
            claim(word, "component term", single=True)
        for side, aliases in self.sides.items():
            for alias in aliases:
                claim(alias, f"side {side}")
        for alias in self.ambiguous_sides:
            claim(alias, "ambiguous side")
        op_owners: dict[tuple[str, ...], str] = {}
        for code, aliases in self.operations.items():
            if code == "unknown" and aliases:
                raise ValueError("the parser never resolves an operation to 'unknown'; list no alias for it")
            for alias in aliases:
                key = tokens(alias)
                if not key or key in op_owners:
                    raise ValueError(f"operation alias {alias!r} is empty or duplicated")
                op_owners[key] = code
        for entry in self.ambiguous_operations:
            for alias in entry.aliases:
                key = tokens(alias)
                if not key or key in op_owners:
                    raise ValueError(f"operation alias {alias!r} is empty or duplicated")
                op_owners[key] = "ambiguous"
        return self


@dataclass(frozen=True)
class MappingResult:
    """One vocabulary lookup. ``code`` is set exactly when ``status == "resolved"``."""

    code: str | None
    status: MappingStatus
    reason: str | None
    candidates: tuple[str, ...] = ()
    matched_alias: str | None = None


@dataclass(frozen=True)
class SideResult:
    side: Literal["left", "right", "centre", "not_applicable", "unknown"]
    source: Literal["document_text", "absent"]
    reason: str | None  # side_absent_in_text | side_text_ambiguous | multiple_sides_in_text
    text: tuple[str, ...] = ()  # the side words found


@dataclass(frozen=True)
class _Ambiguous:
    candidates: tuple[str, ...]
    reason: str


class EstimateVocabulary:
    """Compiled lookup tables for one vocabulary version."""

    def __init__(self, data: VocabularyFile, *, sided_parts: frozenset[str] | None = None):
        self.data = data
        self.version = data.vocabulary_version
        self.taxonomy_version = data.taxonomy_version
        self.fuzzy_enabled = data.matching.fuzzy_enabled
        self.fuzzy_min_ratio = data.matching.fuzzy_min_ratio
        self._parts: dict[tuple[str, ...], str | _Ambiguous] = {}
        for code, aliases in data.parts.items():
            self._parts.update({tokens(a): code for a in aliases})
        for entry in data.ambiguous_parts:
            self._parts.update({tokens(a): _Ambiguous(entry.candidates, entry.reason) for a in entry.aliases})
        self._operations: dict[tuple[str, ...], str | _Ambiguous] = {}
        for code, aliases in data.operations.items():
            self._operations.update({tokens(a): code for a in aliases})
        for entry in data.ambiguous_operations:
            self._operations.update({tokens(a): _Ambiguous(entry.candidates, entry.reason) for a in entry.aliases})
        self._sides: dict[tuple[str, ...], str | None] = {}
        for side, aliases in data.sides.items():
            self._sides.update({tokens(a): side for a in aliases})
        self._sides.update({tokens(a): None for a in data.ambiguous_sides})
        self._qualifiers = frozenset(tokens(q)[0] for q in data.qualifiers)
        self._components = frozenset(tokens(c)[0] for c in data.component_terms)
        self._sided = sided_parts

    def is_sided(self, part_code: str | None) -> bool:
        """Whether a left/right side exists for the part (informational, parts taxonomy)."""
        return part_code is None or self._sided is None or part_code in self._sided

    def find_side(self, words: tuple[str, ...]) -> tuple[SideResult, tuple[str, ...]]:
        """Side stated in printed words, and the words left once stated side words are removed.

        Ambiguous side words stay in the returned words so the part cannot resolve exactly.
        """
        matches = find_phrases(words, self._sides)
        stated = sorted({side for _, _, side in matches if side is not None})
        found = tuple(" ".join(words[s:e]) for s, e, _ in matches)
        drop = {i for s, e, side in matches if side is not None for i in range(s, e)}
        rest = tuple(w for i, w in enumerate(words) if i not in drop)
        if any(side is None for _, _, side in matches):
            return SideResult("unknown", "absent", "side_text_ambiguous", found), rest
        if len(stated) > 1:
            return SideResult("unknown", "absent", "multiple_sides_in_text", found), rest
        if stated:
            return SideResult(stated[0], "document_text", None, found), rest  # type: ignore[arg-type]
        return SideResult("unknown", "absent", "side_absent_in_text"), rest

    def map_part(self, text: str) -> tuple[MappingResult, SideResult]:
        """Map a printed description to a part code, and read its printed side."""
        side, rest = self.find_side(tokens(text))
        words = tuple(w for w in rest if w not in self._qualifiers)
        if not words:
            return MappingResult(None, "unmapped", "part_text_missing"), side
        part = self._lookup(words, self._parts, self._components, "part", tuple(PART_CODES))
        if part.code and not self.is_sided(part.code) and side.reason == "side_absent_in_text":
            side = SideResult("not_applicable", "absent", None)
        return part, side

    def map_operation(self, text: str) -> MappingResult:
        words = tokens(text)
        if not words:
            return MappingResult(None, "unmapped", "operation_text_missing")
        return self._lookup(words, self._operations, frozenset(), "operation", tuple(OPERATIONS))

    def _lookup(self, words: tuple[str, ...], table: dict, components: frozenset[str], kind: str,
                order: tuple[str, ...]) -> MappingResult:
        phrase = " ".join(words)
        exact = table.get(words)
        if isinstance(exact, str):
            return MappingResult(exact, "resolved", None, (exact,), phrase)
        if isinstance(exact, _Ambiguous):
            return MappingResult(None, "ambiguous", exact.reason, exact.candidates, phrase)
        matches = find_phrases(words, table)
        if matches:
            targets = {v if isinstance(v, str) else v.candidates for _, _, v in matches}
            codes: set[str] = set()
            for target in targets:
                codes.update((target,) if isinstance(target, str) else target)
            candidates = _ordered(codes, order)
            if len(targets) > 1:
                return MappingResult(None, "ambiguous", f"multiple_{kind}s_in_text", candidates, phrase)
            value = matches[0][2]
            if isinstance(value, _Ambiguous):
                return MappingResult(None, "ambiguous", value.reason, candidates, phrase)
            covered = {i for s, e, _ in matches for i in range(s, e)}
            leftover = [w for i, w in enumerate(words) if i not in covered]
            if components and all(w in components for w in leftover):
                return MappingResult(None, "ambiguous", f"{kind}_component_only", candidates, phrase)
            return MappingResult(None, "ambiguous", "partial_alias_match", candidates, phrase)
        if self.fuzzy_enabled:
            scored: dict[str, float] = {}
            for alias, value in table.items():
                if isinstance(value, str):
                    ratio = difflib.SequenceMatcher(None, phrase, " ".join(alias)).ratio()
                    scored[value] = max(scored.get(value, 0.0), ratio)
            best = max(scored.values(), default=0.0)
            if best >= self.fuzzy_min_ratio:
                winners = _ordered({c for c, r in scored.items() if r == best}, order)
                return MappingResult(None, "ambiguous", "fuzzy_suggestion", winners, phrase)
        return MappingResult(None, "unmapped", f"{kind}_alias_not_found", (), phrase)


def _ordered(values: set[str], order: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(v for v in order if v in values)


def load_estimate_vocabulary(path: str | os.PathLike[str] | None = None, *,
                             taxonomy_root: Path | None = None) -> EstimateVocabulary:
    """Load and check against the parts and operations taxonomies it was written for."""
    source = Path(path or os.getenv(VOCABULARY_ENV) or DEFAULT_VOCABULARY_PATH)
    with source.open(encoding="utf-8") as handle:
        data = VocabularyFile.model_validate(yaml.safe_load(handle))
    parts = taxonomy.load_parts(taxonomy_root)
    if data.taxonomy_version != parts.version:
        raise ContractError("taxonomy_version_mismatch",
                            f"vocabulary {data.vocabulary_version} targets {data.taxonomy_version}, "
                            f"runtime parts taxonomy is {parts.version}")
    operations = taxonomy.load_operations(taxonomy_root)
    if data.operations_taxonomy_version != operations.version:
        raise ContractError("taxonomy_version_mismatch",
                            f"vocabulary targets {data.operations_taxonomy_version}, runtime is {operations.version}")
    sided = frozenset(p["code"] for p in parts.meta["parts"] if p.get("sided"))
    return EstimateVocabulary(data, sided_parts=sided)
