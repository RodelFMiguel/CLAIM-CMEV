"""``cmev-consolidator``: run M8 over the stored branch records with the pinned cost table.

On ``cmd.consolidate`` the consolidator loads the records of each completed stage for the
input revision (following reuse lineage to the revision that produced them), applies the
recorded pen-mark actions through M6 ``apply_mark_action`` (fixture surveyor actions first,
then the surveyor's corrections carried by the input revision), derives each row's
effective price, calls the pure M8 ``consolidate`` and, in one transaction, inserts the
immutable assessment, moves the claim's current pointer forward only when the revision is
current, and writes ``evt.assessment-ready`` into the outbox. The cost table named in the
command is loaded by version and never re-read from the active pointer.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from ..comparison import ConsolidationRequest, consolidate, load_rule_config
from ..comparison.config import RuleConfig
from ..contracts.claims import ReusedArtifact
from ..contracts.common import ContractError, Provenance
from ..contracts.documents import with_effective_price
from ..costs.reference import CostTableError, PinnedCostTable, active_table_version, load_table
from ..documents.pen_marks import MarkAction, apply_mark_action
from ..messaging.consumer import Context, PermanentError
from ..messaging.outbox import build_message
from ..persistence.store import load_records
from ..persistence.tables import assessments, current_pointer
from ..runtime import get
from . import state
from .corrections import apply_corrections, review_events
from .intake import contract_vehicle_class
from .plan import READY_TOPIC, SERVICE, STAGES

GROUP = "cmev-consolidator"
OPEN_RESULTS = frozenset({"unsupported", "cost_outlier", "insufficient_evidence"})
_MARK_ACTION_FIELDS = ("action_type", "action_id", "mark_id", "target_entry_id", "confirmed_amount",
                       "confirmed_currency", "confirmed_cost_basis", "reason_code", "note")


class Consolidator:
    def __init__(self, *, cost_table_root: str | Path, rule_config: RuleConfig | None = None):
        self.root = Path(cost_table_root)
        self.config = rule_config or load_rule_config()
        self._tables: dict[str, PinnedCostTable] = {}

    def handlers(self) -> dict[str, Any]:
        return {"cmev.cmd.consolidate.v1": self.on_consolidate}

    def table(self, version: str) -> PinnedCostTable:
        if version not in self._tables:
            self._tables[version] = load_table(self.root, version)
        return self._tables[version]

    def readiness(self, configured_version: str | None = None) -> dict[str, str]:
        """Refuse readiness unless the rule config and the table new assessments pin both load."""
        version = configured_version or active_table_version(self.root)
        if not version:
            raise CostTableError("table_missing", f"no active cost table under {self.root}")
        self.table(version)
        return {"rules_config_version": self.config.rules_config_version, "cost_table_version": version}

    # ------------------------------------------------------------------ handler
    def on_consolidate(self, ctx: Context) -> dict[str, Any]:
        env, payload, session = ctx.envelope, ctx.payload, ctx.session
        claim_id, rev = env.claim_id, env.input_revision
        if payload["rules_config_version"] != self.config.rules_config_version:
            raise PermanentError("rules_config_version_mismatch", "the command pins another M8 rule configuration")
        try:
            table = self.table(payload["cost_table_version"])
        except CostTableError as exc:
            raise PermanentError("cost_table_unavailable", f"pinned cost table does not load: {exc.reason_code}") from exc
        claim = get(session, "claim:" + claim_id)
        claim_input = get(session, f"input:{claim_id}:{rev}")
        if claim is None or claim_input is None:
            raise PermanentError("claim_input_missing", "claim or input revision record not found")

        records, lineage, inputs = self._records(session, claim_id, rev, payload)
        marks, actions = self._decided_marks(records, claim_input, rev)
        items, marks, declarations = apply_corrections(records, marks, claim_input, rev)
        actions += [{"source": "surveyor", "action_id": e.action_id, "action_type": e.action_type, "mark_id": e.mark_id}
                    for e in review_events(claim_input)]
        inputs["declaration"] = declarations[0].model_dump(mode="json") if declarations else None
        pointer = session.execute(select(current_pointer).where(current_pointer.c.claim_id == claim_id)
                                  .with_for_update()).mappings().first()
        assessment_revision = 1 + (session.execute(select(func.max(assessments.c.assessment_revision))
                                                   .where(assessments.c.claim_id == claim_id)).scalar() or 0)
        superseded = claim.get("input_revision", rev) != rev
        vehicle_class = contract_vehicle_class((claim.get("vehicle") or {}).get("vehicle_class"))
        prior = claim_input.get("base_assessment_revision")
        provenance = Provenance(**ctx.provenance(source_dataset_id=claim_input.get("fixture_source")))
        try:
            request = ConsolidationRequest(
                claim_id=claim_id, input_revision=rev, assessment_revision=assessment_revision,
                review_revision=payload["review_revision"], trigger=payload["trigger"],
                image_branch_state=payload["image_branch_state"], document_branch_state=payload["document_branch_state"],
                vehicle_class=vehicle_class, currency=claim.get("currency", "SGD"),
                part_summaries=records.get("part_summary", []), coverage=records.get("part_coverage", []),
                observations=records.get("damage_observation", []),
                identity_confirmations=records.get("identity_confirmation", []),
                coverage_confirmations=records.get("coverage_confirmation", []),
                line_items=items, pen_marks=marks, declaration=declarations[0] if declarations else None,
                pages=records.get("document_page", []), cost_table_version=payload["cost_table_version"],
                rules_config_version=payload["rules_config_version"], pinned_versions=payload["pinned_versions"],
                provenance=provenance, created_at=ctx.now, reuse_lineage=lineage,
                prior_assessment_revision=prior if prior and prior < assessment_revision else None,
                superseded=superseded)
            result = consolidate(request, config=self.config, ranges=table)
        except (ContractError, ValueError) as exc:
            raise PermanentError(getattr(exc, "reason_code", "consolidation_input_invalid"),
                                 str(exc).splitlines()[0][:200]) from exc
        if result.job_key != env.job_key:
            raise PermanentError("job_key_mismatch", "M8 computed another job key for this command")
        assessment = result.assessment
        body = assessment.model_dump(mode="json")
        snapshot = {**inputs, "line_items": [i.model_dump(mode="json") for i in items],
                    "pen_marks": [m.model_dump(mode="json") for m in marks], "mark_actions_applied": actions,
                    "fixture_scenario": claim_input.get("fixture_scenario")}
        session.execute(insert(assessments).values(
            claim_id=claim_id, assessment_revision=assessment_revision, input_revision=rev, state=assessment.state,
            superseded=superseded, trigger=payload["trigger"], job_key=env.job_key,
            cost_table_version=assessment.cost_table_version, rules_config_version=assessment.rules_config_version,
            body=body, inputs=snapshot, reuse_lineage=state.lineage_records(session, claim_id, rev),
            created_at=ctx.now))
        if not superseded:
            self._advance_pointer(session, pointer, claim_id, rev, assessment_revision, body, items, ctx.now)
        job = ctx.job or {}
        message = build_message(READY_TOPIC, claim_id=claim_id, input_revision=rev, task="consolidate",
                                versions=env.versions, provenance=ctx.provenance(), trace_id=env.trace_id,
                                occurred_at=ctx.now, payload=result.ready_payload(),
                                attempt_epoch=job.get("attempt_epoch", 0), assessment_revision=assessment_revision,
                                causation_id=env.dedup_key)
        ctx.emit(READY_TOPIC, message)
        return {"assessment_revision": assessment_revision, "superseded": superseded,
                "finding_counts": result.finding_counts}

    # ------------------------------------------------------------------ helpers
    def _records(self, session: Session, claim_id: str, rev: int,
                 payload: Mapping[str, Any]) -> tuple[dict[str, list[Any]], list[ReusedArtifact], dict[str, Any]]:
        image = payload["image_branch_state"] == "complete"
        document = payload["document_branch_state"] == "complete"
        wanted = [s for s in STAGES if (s in ("parts", "damage", "summary") and image)
                  or (s in ("page_read", "line_items", "pen_marks") and document)]
        records: dict[str, list[Any]] = {}
        lineage: list[ReusedArtifact] = []
        for stage in wanted:
            jobs = state.effective_jobs(session, claim_id, rev, stage)
            if not jobs:
                raise PermanentError("branch_result_missing", f"no succeeded {stage} results for this revision")
            for job in jobs:
                if job["input_revision"] < rev:
                    lineage.append(ReusedArtifact(artifact_id=job["job_key"], kind=f"{stage}_records",
                                                  source_input_revision=job["input_revision"],
                                                  producing_job_key=job["job_key"]))
            for kind, found in load_records(session, claim_id, [j["job_key"] for j in jobs]).items():
                records.setdefault(kind, []).extend(found)
        expected = {"part_summary": ("summary_id", "summary_ids"), "part_coverage": ("coverage_id", "coverage_ids"),
                    "line_item": ("entry_id", "line_item_ids"), "pen_mark": ("mark_id", "mark_ids")}
        for kind, (field, key) in expected.items():
            stored = sorted(getattr(r, field) for r in records.get(kind, []))
            if stored != sorted(payload[key]):
                raise PermanentError("branch_result_mismatch", f"stored {kind} records differ from the command")
        photos = sorted({p.photo_id for p in records.get("part_prediction", [])}
                        | {q.photo_id for q in records.get("image_quality", [])})
        inputs = {"part_summaries": [r.model_dump(mode="json") for r in records.get("part_summary", [])],
                  "coverage": [r.model_dump(mode="json") for r in records.get("part_coverage", [])],
                  "observations": [r.model_dump(mode="json") for r in records.get("damage_observation", [])],
                  "declaration": (records["declaration_completeness"][0].model_dump(mode="json")
                                  if records.get("declaration_completeness") else None),
                  "pages": [{"page_id": p.page_id, "file_id": p.file_id, "page_number": p.page_number,
                             "quality": p.quality.model_dump(mode="json")} for p in records.get("document_page", [])],
                  "fixture_photo_ids": photos}
        return records, lineage, inputs

    def _decided_marks(self, records: Mapping[str, list[Any]], claim_input: Mapping[str, Any],
                       rev: int) -> tuple[list[Any], list[dict[str, Any]]]:
        marks = list(records.get("pen_mark", []))
        rows = list(records.get("line_item", []))
        fixture_actions = sorted(records.get("fixture_mark_action", []), key=lambda a: (a["review_revision"],
                                                                                         a["action_id"]))
        corrections = [c for c in claim_input.get("corrections", []) if c.get("kind") == "mark_action"]
        applied = []
        for source, entry in [("fixture", a) for a in fixture_actions] + [("surveyor", c) for c in corrections]:
            if not marks:
                break
            action = MarkAction(**{k: entry.get(k) for k in _MARK_ACTION_FIELDS})
            try:
                marks = apply_mark_action(marks, action, actor=entry["actor"],
                                          recorded_at=datetime.fromisoformat(entry["recorded_at"]),
                                          review_revision=entry["review_revision"], rows=rows,
                                          input_revision=rev if any(m.input_revision != rev for m in marks) else None)
            except ContractError as exc:
                raise PermanentError(f"mark_action_{exc.reason_code}"[:80], "a recorded pen-mark action no longer "
                                     "applies to the stored marks") from exc
            applied.append({"source": source, "action_id": entry["action_id"], "action_type": entry["action_type"],
                            "mark_id": entry.get("mark_id")})
        return marks, applied

    def _advance_pointer(self, session: Session, pointer: Mapping[str, Any] | None, claim_id: str, rev: int,
                         assessment_revision: int, body: Mapping[str, Any], items: Sequence[Any],
                         now: datetime) -> None:
        """Forward-only: a newer input revision, or a newer assessment of the same revision."""
        if pointer is not None and (pointer["input_revision"], pointer["assessment_revision"]) >= (rev, assessment_revision):
            return
        printed = [i.printed_line_amount for i in items]
        total = (format(sum((Decimal(p) for p in printed), Decimal(0)), ".2f")
                 if printed and all(p is not None for p in printed) else None)
        values = {"assessment_revision": assessment_revision, "input_revision": rev,
                  "finding_count": sum(1 for f in body["findings"] if f["overall_result"] in OPEN_RESULTS),
                  "estimate_row_count": len(items), "declared_total": total, "updated_at": now}
        if pointer is None:
            session.execute(insert(current_pointer).values(claim_id=claim_id, **values))
        else:
            session.execute(update(current_pointer).where(current_pointer.c.claim_id == claim_id).values(values))


__all__ = ["Consolidator", "GROUP", "OPEN_RESULTS", "SERVICE"]
