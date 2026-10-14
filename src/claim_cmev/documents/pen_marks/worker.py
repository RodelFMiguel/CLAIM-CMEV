"""Live M6 handler: detector proposals plus actual M5 geometry, never decisions."""
from dataclasses import asdict
import io

import numpy as np
from PIL import ImageDraw

from claim_cmev.messaging.consumer import PermanentError
from claim_cmev.persistence.store import insert_records
from claim_cmev.documents import worker_common as common
from .linking import link_detections
from .rows import row_box_of


def make_pen_marks_handler(detector, storage, versions, selection, linking):
    def handler(ctx):
        common.check_request(ctx, versions)
        model_id, model_version = selection.model_version.split("/", 1)
        p = ctx.payload
        if (p["model_id"], p["model_version"], p["link_config_version"], p["trocr_enabled"]) != (
                model_id, model_version, linking.link_config_version, False):
            raise PermanentError("version_mismatch", "M6 payload differs from the selected model and linking rules")
        pages, images = common.load_pages(ctx, storage, reading=False)
        items = common.effective_records(ctx, "line_items").get("line_item", [])
        expected = {i.entry_id: row_box_of(i) for i in items}
        supplied = [row_box_of(r) for r in p["row_boxes"]]
        if len(supplied) != len(expected) or {r.entry_id: r for r in supplied} != expected:
            raise PermanentError("row_geometry_mismatch", "M6 requires the exact rows of the effective M5 job")
        if any(i.page_id not in images for i in items):
            raise PermanentError("row_page_mismatch", "M5 rows refer to an unavailable page")
        prov = common.provenance(ctx, [*pages, *items])
        detections = []
        for page in pages:
            detections.extend(detector.detect(np.array(images[page.page_id][1]), page_id=page.page_id))
        if any(d.page_id not in images for d in detections):
            raise PermanentError("detection_page_mismatch", "detector returned another page")
        env = ctx.envelope
        result = link_detections(detections, supplied, config=linking, job_key=env.job_key, claim_id=env.claim_id,
            input_revision=env.input_revision, provenance=prov, versions=versions,
            page_widths_px={p.page_id: p.transform.corrected_width for p in pages})
        overlays = {}
        for page in pages:
            image = images[page.page_id][1].copy()
            draw = ImageDraw.Draw(image)
            for mark in result.marks:
                if mark.page_id != page.page_id:
                    continue
                x0, y0, x1, y1 = mark.box_norm
                box = (x0 * image.width, y0 * image.height, x1 * image.width, y1 * image.height)
                color = "red" if mark.mark_type == "exclusion" else "blue"
                draw.rectangle(box, outline=color, width=3)
                draw.text((box[0], max(0, box[1]-12)), f"{mark.mark_type}: pending", fill=color)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            overlays[page.page_id] = common.write_artifact(ctx, storage, f"{page.page_id}-marks.png", buffer.getvalue(), "image/png")
        transforms = {p.page_id: p.transform for p in pages}
        artifact = common.write_json(ctx, storage, "pen-marks.json", {
            "detections": [{**d.model_dump(mode="json"),
                            "quad_source": transforms[d.page_id].norm_box_to_source_quad(d.box_norm)}
                           for d in detections],
            "marks": [m.model_dump(mode="json") for m in result.marks],
            "candidates": [asdict(c) for c in result.candidates],
            "merged_duplicates": [asdict(c) for c in result.merged_duplicates],
            "below_threshold": list(result.below_threshold), "counts": dict(result.counts),
            "overlays": overlays, "versions": dict(versions), "provenance": prov.model_dump(mode="json")})
        insert_records(ctx.session, [("pen_mark", m) for m in result.marks], claim_id=env.claim_id,
            input_revision=env.input_revision, stage="pen_marks", job_key=env.job_key, now=ctx.now)
        fields = {"mark_id", "page_id", "mark_type", "box_norm", "detection_confidence", "entry_id",
                  "candidate_entry_ids", "link_reason", "state", "trocr_suggestion"}
        event = {"mark_ids": [m.mark_id for m in result.marks],
                 "marks": [m.model_dump(mode="json", include=fields) for m in result.marks],
                 "unlinked_count": result.counts["unlinked"], "conflicting_count": result.counts["conflicting"],
                 "branch": "document"}
        common.emit(ctx, "pen_marks", event, prov)
        return {"mark_ids": event["mark_ids"], "detection_ref": artifact, "overlay_refs": overlays,
                "counts": dict(result.counts), "processing_status": "succeeded"}
    return handler
