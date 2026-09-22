"""Sample results, deliberately unrelated to uploaded pixels. Never ML results."""
from copy import deepcopy
from sqlalchemy import select
from .runtime import Record, get, put, now, enqueue

PROVENANCE = {"source_kind": "fixture", "runtime_profile": "lean", "producer_service": "cmev-worker-combined"}
VERSIONS = {"fixture": "baseline-1", "schema": "0.2.0", "cost_table": "synthetic-demo-1", "parser": "mock-not-run", "models": "mock-not-loaded"}


def assessment_for(claim, revision, input_data, previous=None):
    rows = deepcopy(previous["line_items"]) if previous else []
    marks = deepcopy(previous["marks"]) if previous else []
    has_documents = bool(input_data.get("has_documents", True))
    if not rows and has_documents:
        for index, (description, part, operation, amount) in enumerate([
            ("Front bumper repair", "front-bumper", "repair", "1250.00"),
            ("Left headlamp replacement", "headlight", "replace", "860.00"),
            ("Front fender refinishing", "fender", "paint", "480.00")]):
            rows.append({"entry_id": f"entry-{index + 1}", "description": description,
                "part_code": part, "side": "unknown", "operation": operation, "printed_amount": amount,
                "effective_amount": None if index == 0 else amount, "currency": claim["currency"], "cost_basis": "single_part",
                "mark_state": "pending" if index == 0 else "none", "photo_check": "insufficient_evidence",
                "cost_check": "not_checked", "overall_result": "insufficient_evidence",
                "reason_code": "pending_price_change" if index == 0 else "fixture_evidence_only",
                "reason": "Confirm the mark and enter the revised amount." if index == 0 else "Mock processing: uploaded evidence has not been analysed.",
                "synthetic_range": None, "evidence_ids": input_data.get("file_ids", [])})
        marks = [{"mark_id": "mark-1", "entry_id": "entry-1", "type": "price_change", "state": "pending",
                  "page_no": 1, "reason": "Illustrative pen mark; confirm or reject before finalizing."}]
    for action in input_data.get("corrections", []):
        if action["type"] == "mark_decision":
            mark = next(m for m in marks if m["mark_id"] == action["mark_id"])
            mark["state"] = "confirmed" if action["decision"] == "confirm" else "rejected"
            mark["human_action"] = action
            row = next(r for r in rows if r["entry_id"] == mark["entry_id"])
            row["mark_state"] = mark["state"]
            row["effective_amount"] = action["amount"] if action["decision"] == "confirm" else row["printed_amount"]
            row.update(overall_result="insufficient_evidence", reason_code="fixture_evidence_only",
                       reason="Human price decision recorded; uploaded evidence has not been analysed.")
    findings = [{"finding_id": f"finding-{r['entry_id']}", "entry_id": r["entry_id"], "type": r["overall_result"],
                 "reason_code": r["reason_code"], "message": r["reason"]} for r in rows]
    return {"claim_id": claim["claim_id"], "assessment_revision": revision, "input_revision": input_data["input_revision"],
        "review_revision": claim["review_revision"], "state": "completed", "created_at": now(), "schema_version": "0.2.0",
        "provenance": PROVENANCE, "versions": VERSIONS, "declaration_completeness": "partial" if has_documents else "unreadable",
        "line_items": rows, "marks": marks, "findings": findings,
        "damage_summary": [{"part_code": "front-bumper", "side": "unknown", "coverage": "unresolved",
            "reason": "Illustrative summary only. No model analysed these photographs.", "photo_count": claim["photograph_count"]}] if claim["photograph_count"] else [],
        "possible_additions": [], "fixture_notice": "Demonstration fixtures only. Not model results, repair-price validation or a final claim approval."}


def seed(db):
    if get(db, "seed:baseline-v1"):
        return
    samples = [
        ("CLM-24019", "MOT/2026/884213", "R. Miguel", "Toyota", "Corolla Altis 1.6", 2021, "SJB 4412 K", "Northbridge Autoworks", "2026-09-09", "in_review", 14, 3, "2590.00", 3),
        ("CLM-24020", "MOT/2026/884977", "L. Tan", "Honda", "City 1.5", 2019, "SGA 9087 M", "Kallang Panel & Paint", "2026-09-12", "processing", 9, 3, "2590.00", 3),
        ("CLM-24021", "MOT/2026/885104", "P. Sharma", "Mazda", "CX-5 2.0", 2022, "SLK 2210 B", "Westgate Motor Services", "2026-09-15", "awaiting_upload", 0, 0, None, 0),
        ("CLM-24018", "MOT/2026/883760", "A. Fernandez", "Hyundai", "Tucson 1.6T", 2020, "SDF 7741 X", "Northbridge Autoworks", "2026-09-02", "ready_to_print", 21, 3, "2590.00", 3),
    ]
    for i, sample in enumerate(samples):
        ref, policy, surveyor, make, model, year, plate, workshop, loss, status, photos, estimates, total, count = sample
        cid = f"01K5000000000000000000000{i}"
        c = {"claim_id": cid, "reference": ref, "policy_number": policy, "surveyor": surveyor,
            "owner_id": "demo-surveyor", "vehicle": {"make": make, "model": model, "year": year, "plate": plate, "vehicle_class": "unknown"},
            "workshop": workshop, "loss_date": loss, "status": status, "photograph_count": photos, "estimate_row_count": estimates,
            "declared_total": total, "currency": "SGD", "finding_count": count, "input_revision": 0 if i == 2 else 1,
            "assessment_revision": 1 if i in (0, 3) else None, "review_revision": 0, "source_kind": "fixture", "created_at": now()}
        put(db, "claim:" + cid, "claim", c, cid)
        if i == 2:
            continue
        inp = {"claim_id": cid, "input_revision": 1, "file_ids": [], "has_documents": True, "created_at": now(), "source_kind": "fixture", "fixture_seed": True, "corrections": []}
        put(db, f"input:{cid}:1", "input", inp, cid)
        if i == 1:
            enqueue(db, cid, 1)
            continue
        a = assessment_for(c, 1, inp)
        if i == 3:
            a["marks"] = []
            for row in a["line_items"]:
                row.update(mark_state="none", effective_amount=row["printed_amount"], overall_result="insufficient_evidence", reason_code="fixture_evidence_only", reason="Illustrative data only; evidence has not been analysed.")
            a["findings"] = [{"finding_id": f"finding-{r['entry_id']}", "entry_id": r["entry_id"], "type": r["overall_result"], "reason_code": r["reason_code"], "message": r["reason"]} for r in a["line_items"]]
        put(db, f"assessment:{cid}:1", "assessment", a, cid)
        review = {"review_revision": 0, "assessment_revision": 1, "actions": [], "finalized": i == 3}
        if i == 3:
            review["finalized_at"] = now()
            review["claim_snapshot"] = deepcopy(c)
        put(db, f"review:{cid}:1", "review", review, cid)
    put(db, "seed:baseline-v1", "seed", {"created_at": now()})
