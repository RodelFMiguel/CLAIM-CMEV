"""Versioned vocabulary loaded from ``configs/taxonomy/`` (data contracts section 3).

The CarDD and HITL damage taxonomies are distinct and never merged: a damage label is
the pair ``(taxonomy_version, code)``, so CarDD ``dent`` and HITL ``dent`` never compare
equal and ``merge_damage_vocabularies`` always refuses different families.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import cache
import os
from pathlib import Path
from typing import Any

import yaml

from ..contracts.common import ContractError

TAXONOMY_ENV = "CMEV_TAXONOMY_DIR"


class TaxonomyMergeError(ContractError):
    def __init__(self, message: str):
        super().__init__("taxonomy_merge_refused", message)


def taxonomy_root() -> Path:
    """``$CMEV_TAXONOMY_DIR`` or the repository's ``configs/taxonomy``."""
    configured = os.getenv(TAXONOMY_ENV)
    return Path(configured) if configured else Path(__file__).resolve().parents[3] / "configs" / "taxonomy"


@dataclass(frozen=True)
class Vocabulary:
    name: str
    version: str
    codes: tuple[str, ...]
    status: str
    meta: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    def __contains__(self, code: object) -> bool:
        return code in self.codes

    def require(self, code: str) -> str:
        """Return ``code`` if it belongs to this vocabulary; a foreign label is rejected, never mapped."""
        if code not in self.codes:
            raise ContractError("taxonomy_version_mismatch", f"{code!r} is not in {self.version}")
        return code


@dataclass(frozen=True)
class DamageLabel:
    """A damage label is only meaningful together with its taxonomy version."""

    taxonomy_version: str
    code: str


@cache
def _read(name: str, root: str) -> dict[str, Any]:
    path = Path(root) / f"{name}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data.get("taxonomy_version"):
        raise ContractError("taxonomy_invalid", f"{path} has no taxonomy_version")
    return data


def _load(name: str, key: str, root: Path | None) -> Vocabulary:
    data = _read(name, str(root or taxonomy_root()))
    entries = data[key]
    codes = tuple(e["code"] if isinstance(e, dict) else e for e in entries)
    if len(set(codes)) != len(codes):
        raise ContractError("taxonomy_invalid", f"{name} repeats a code")
    return Vocabulary(name, data["taxonomy_version"], codes, data.get("status", "proposed"), data)


def load_parts(root: Path | None = None) -> Vocabulary:
    return _load("parts", "parts", root)


def load_sides(root: Path | None = None) -> Vocabulary:
    return _load("sides", "values", root)


def load_damage_cardd(root: Path | None = None) -> Vocabulary:
    vocab = _load("damage_cardd", "codes", root)
    if not vocab.version.startswith("damage-cardd-"):
        raise ContractError("taxonomy_invalid", "the CarDD vocabulary is versioned damage-cardd-<x.y.z>")
    return vocab


def load_damage_hitl(root: Path | None = None) -> Vocabulary:
    vocab = _load("damage_hitl", "codes", root)
    if not vocab.version.startswith("damage-hitl-"):
        raise ContractError("taxonomy_invalid", "the HITL contingency vocabulary is versioned damage-hitl-<x.y.z>")
    return vocab


def load_operations(root: Path | None = None) -> Vocabulary:
    return _load("operations", "values", root)


def cost_eligible_operations(root: Path | None = None) -> tuple[str, ...]:
    return tuple(load_operations(root).meta["cost_eligible"])


def load_vehicle_classes(root: Path | None = None) -> Vocabulary:
    """The four proposed classes; ``unknown`` is separate and receives no cost comparison."""
    return _load("vehicle_classes", "classes", root)


def load_mapping_status(root: Path | None = None) -> Vocabulary:
    return _load("mapping_status", "values", root)


def load_cost_basis(root: Path | None = None) -> dict[str, Any]:
    """The fixed cost basis with its currency, quantity, tax and operation semantics."""
    return dict(_read("cost_basis", str(root or taxonomy_root())))


def damage_label(code: str, vocabulary: Vocabulary) -> DamageLabel:
    return DamageLabel(vocabulary.version, vocabulary.require(code))


def merge_damage_vocabularies(first: Vocabulary, second: Vocabulary) -> Vocabulary:
    """Refuse to merge two damage taxonomies. Only an identical version is 'merged' (a no-op)."""
    if first.version != second.version:
        raise TaxonomyMergeError(
            f"{first.version} and {second.version} are different taxonomies; mapping between them needs a "
            "recorded, reviewed decision and is never implicit")
    return first


__all__ = [
    "DamageLabel", "TAXONOMY_ENV", "TaxonomyMergeError", "Vocabulary", "cost_eligible_operations", "damage_label",
    "load_cost_basis", "load_damage_cardd", "load_damage_hitl", "load_mapping_status", "load_operations", "load_parts",
    "load_sides", "load_vehicle_classes", "merge_damage_vocabularies", "taxonomy_root",
]
