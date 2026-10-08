"""The stage plan shared by the API, the orchestrator and every worker.

Six stages in two branches (application platform 6.1). The document branch is chained
``page_read -> line_items -> pen_marks`` because M6 linking needs the M5 row boxes: the
pen-marks command carries them (integration contracts section 12, decision 1), so the
high-level diagram's parallel M5/M6 edge is not used. Photographs and pages fan out one
command per item (the command schemas name one photo or page), so ``parts``, ``damage``
and ``page_read`` use the item as the job-key target; the other stages use ``all``.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ..contracts.common import make_job_key

STAGES = ("parts", "damage", "summary", "page_read", "line_items", "pen_marks")
IMAGE_STAGES = ("parts", "damage", "summary")
DOCUMENT_STAGES = ("page_read", "line_items", "pen_marks")
BRANCH_OF = {**{s: "image" for s in IMAGE_STAGES}, **{s: "document" for s in DOCUMENT_STAGES}}
NEXT_STAGE = {"parts": "damage", "damage": "summary", "page_read": "line_items", "line_items": "pen_marks"}
PER_ITEM = {"parts": "photo", "damage": "photo", "page_read": "page"}
TASKS = {"intake": "intake", "parts": "parts_segment", "damage": "damage_segment", "summary": "part_summary",
         "page_read": "page_read", "line_items": "line_items_extract", "pen_marks": "pen_marks_detect",
         "consolidate": "consolidate"}
STAGE_OF_TASK = {task: stage for stage, task in TASKS.items()}
INPUT_TOPIC = "cmev.evt.input-revision-created.v1"
COMMAND_TOPIC = {"parts": "cmev.cmd.parts-segment.v1", "damage": "cmev.cmd.damage-segment.v1",
                 "summary": "cmev.cmd.part-summary.v1", "page_read": "cmev.cmd.page-read.v1",
                 "line_items": "cmev.cmd.line-items-extract.v1", "pen_marks": "cmev.cmd.pen-marks-detect.v1",
                 "consolidate": "cmev.cmd.consolidate.v1"}
EVENT_TOPIC = {"parts": "cmev.evt.parts-segmented.v1", "damage": "cmev.evt.damage-segmented.v1",
               "summary": "cmev.evt.part-summarised.v1", "page_read": "cmev.evt.page-read.v1",
               "line_items": "cmev.evt.line-items-extracted.v1", "pen_marks": "cmev.evt.pen-marks-detected.v1"}
STAGE_OF_EVENT = {topic: stage for stage, topic in EVENT_TOPIC.items()}
READY_TOPIC = "cmev.evt.assessment-ready.v1"
FAILED_TOPIC = "cmev.evt.job-failed.v1"
SERVICE = {"api": "cmev-api", "orchestrator": "cmev-orchestrator", "parts": "cmev-worker-parts",
           "damage": "cmev-worker-damage", "summary": "cmev-worker-summary", "page_read": "cmev-worker-ocr",
           "line_items": "cmev-worker-lineitems", "pen_marks": "cmev-worker-penmarks",
           "consolidate": "cmev-consolidator"}
FIXTURE_FAMILY = {"parts": "parts", "damage": "damage", "summary": "summary", "page_read": "ocr",
                  "line_items": "lineitems", "pen_marks": "penmarks"}
CODE_VERSION = "0.2.0"


@dataclass(frozen=True)
class VersionBundle:
    """The versions each stage runs with; they form the job-key signature."""

    stages: Mapping[str, Mapping[str, str]]
    intake: Mapping[str, str] = field(default_factory=lambda: {"intake": "cmev-api-intake/0.2.0", "code": CODE_VERSION})

    def for_stage(self, stage: str) -> dict[str, str]:
        return dict(self.stages[stage])

    @classmethod
    def fixture(cls) -> VersionBundle:
        """Fixture mode: the stage versions are the contract fixtures' own version tags."""
        from ..contracts.fixtures import VERSIONS

        from ..vision.multiview.config import load_summary_config
        stages = {stage: dict(VERSIONS[family]) for stage, family in FIXTURE_FAMILY.items()}
        stages["summary"]["human_summary_config"] = load_summary_config().config_version
        return cls(stages=stages)

    @classmethod
    def for_runtime(cls, parts_producer: str = "fixture", damage_producer: str = "fixture") -> VersionBundle:
        """The bundle the orchestrator pins: the fixture tags, replaced for each stage a real worker owns.

        ``parts_producer="real"`` pins the configured M1 checkpoint. ``damage_producer="real"``
        also pins the configured M2 checkpoint and the real M3 rules, and needs real parts,
        because M2 reads the M1 mask. The workers verify these versions against what they load.
        """
        bundle = cls.fixture()
        if damage_producer == "real" and parts_producer != "real":
            raise ValueError("a real damage stage needs a real parts stage: M2 reads the M1 mask")
        if parts_producer != "real":
            return bundle
        stages = ("parts", "damage", "summary") if damage_producer == "real" else ("parts",)
        return cls(stages={**bundle.stages, **{stage: real_stage_versions(stage) for stage in stages}},
                   intake=bundle.intake)


def real_stage_versions(stage: str) -> dict[str, str]:
    """The versions of one real image stage: the configured checkpoint, or the M2/M3 rules.

    The orchestrator pins them and the stage's worker serves only commands pinned to them, so
    both read them from here. Only the configuration that stage uses is loaded, and no model
    library is imported.
    """
    from ..vision.parts.config import load_parts_config

    parts = load_parts_config()
    if stage == "parts":
        return parts.stage_versions(CODE_VERSION)
    if stage == "damage":
        from ..vision.damage.config import load_assignment_config
        from ..vision.damage.model_config import load_damage_model_config

        return load_damage_model_config().stage_versions(
            CODE_VERSION, parts_model=parts.model_version, assignment_config=load_assignment_config().config_version)
    if stage == "summary":
        from ..vision.multiview.config import load_summary_config

        return {"summary_config": load_summary_config().config_version, "taxonomy": parts.taxonomy_version,
                "code": CODE_VERSION}
    raise KeyError(f"{stage!r} has no real worker")


def consolidate_versions(rules_config_version: str, cost_table_version: str) -> dict[str, str]:
    """Exactly the map M8's ``job_key_for`` signs, so both compute one job key."""
    return {"rules_config": rules_config_version, "cost_table": cost_table_version}


def job_key(claim_id: str, input_revision: int, stage: str, versions: Mapping[str, str], target: str = "all") -> str:
    return make_job_key(claim_id, input_revision, TASKS[stage], versions, target)


def page_target(file_id: str, page_number: int, is_pdf: bool) -> str:
    """One page of an upload: the file id, or ``<file_id>.p<n>`` for a PDF page."""
    return f"{file_id}.p{page_number}" if is_pdf else file_id


def pinned_versions(stage_versions: Mapping[str, Mapping[str, str]]) -> dict[str, str]:
    """A flat, namespaced version map (``<stage>.<name>``) for the consolidate command."""
    pinned: dict[str, str] = {}
    for stage in STAGES:
        for name, value in sorted((stage_versions.get(stage) or {}).items()):
            pinned[f"{stage}.{name}"] = value
    return pinned


__all__ = [
    "BRANCH_OF", "CODE_VERSION", "COMMAND_TOPIC", "DOCUMENT_STAGES", "EVENT_TOPIC", "FAILED_TOPIC", "FIXTURE_FAMILY",
    "IMAGE_STAGES", "INPUT_TOPIC", "NEXT_STAGE", "PER_ITEM", "READY_TOPIC", "SERVICE", "STAGES", "STAGE_OF_EVENT",
    "STAGE_OF_TASK", "TASKS", "VersionBundle", "consolidate_versions", "job_key", "page_target", "pinned_versions",
    "real_stage_versions",
]
