"""Identify a HITL Supervisely export by its class titles, never by its folder name.

Under ``data/raw/`` the folder named ``Car damages dataset`` holds the 21 part classes
and ``Car parts dataset`` holds the 8 damage classes (data contracts section 3.1).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Literal

from ..contracts.common import ContractError
from . import load_damage_hitl, load_parts


@dataclass(frozen=True)
class HitlSubset:
    kind: Literal["parts", "damage"]
    taxonomy_version: str
    title_to_code: dict[str, str]


def _titles(meta: Mapping[str, Any] | Path | str) -> list[str]:
    if isinstance(meta, (str, Path)):
        path = Path(meta)
        if path.is_dir():
            path = path / "meta.json"
        meta = json.loads(path.read_text(encoding="utf-8"))
    classes = meta.get("classes") if isinstance(meta, Mapping) else None
    if not isinstance(classes, list) or not classes:
        raise ContractError("hitl_meta_invalid", "meta.json has no classes list")
    return [str(c["title"]) for c in classes]


def classify_supervisely_meta(meta: Mapping[str, Any] | Path | str, taxonomy_root: Path | None = None) -> HitlSubset:
    """Classify a meta.json (a mapping, a file or its folder) as the parts or damage subset.

    Every class title must map to exactly one vocabulary; a mixed or unknown set is
    refused rather than guessed.
    """
    titles = _titles(meta)
    parts = load_parts(taxonomy_root)
    damage = load_damage_hitl(taxonomy_root)
    part_titles = {p["hitl_title"]: p["code"] for p in parts.meta["parts"]}
    damage_titles = {d["hitl_title"]: d["code"] for d in damage.meta["codes"]}
    if set(titles) == set(part_titles):
        return HitlSubset("parts", parts.version, {t: part_titles[t] for t in titles})
    if set(titles) == set(damage_titles):
        return HitlSubset("damage", damage.version, {t: damage_titles[t] for t in titles})
    unknown = sorted(set(titles) - set(part_titles) - set(damage_titles))
    raise ContractError("hitl_subset_unrecognised",
                        f"class titles match neither the 21 part nor the 8 damage classes; unknown {unknown}")
