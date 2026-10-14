"""Live M5 handler: persisted M4 evidence to immutable neural extraction records."""
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from claim_cmev.messaging.consumer import PermanentError
from claim_cmev.persistence.store import insert_records
from claim_cmev.documents import worker_common as common
from .layoutlmv3 import adapt_predictions


def make_line_items_handler(model, storage, versions, selection, layout, vocabulary):
    def handler(ctx):
        common.check_request(ctx, versions)
        payload = ctx.payload
        model_id, model_version = selection.model_version.split("/", 1)
        if (payload["engine"], payload["model_id"], payload["model_version"],
                payload["parser_config_version"], payload["taxonomy_version"]) != (
                "layoutlmv3", model_id, model_version, layout.config_version, vocabulary.taxonomy_version):
            raise PermanentError("version_mismatch", "M5 payload differs from the selected model and rules")
        pages, images = common.load_pages(ctx, storage, reading=True)
        prov = common.provenance(ctx, pages)
        predictions = {}
        with TemporaryDirectory(prefix="cmev-m5-") as directory:
            for index, page in enumerate(pages):
                path = Path(directory) / f"{index}.png"
                path.write_bytes(images[page.page_id][0])
                predictions[page.page_id] = model.predict(page, path)
        env = ctx.envelope
        result = adapt_predictions(pages, predictions, config=layout, vocabulary=vocabulary,
            family_id=selection.family_id, job_key=env.job_key, claim_id=env.claim_id,
            input_revision=env.input_revision, currency=payload["claim_currency"], cost_basis=selection.cost_basis,
            model_version=selection.model_version, provenance=prov, min_confidence=selection.min_confidence)
        items = [i.model_copy(update={"versions": {**i.versions, **versions}}) for i in result.line_items]
        declaration = result.completeness.model_copy(update={"versions": {**result.completeness.versions, **versions}})
        artifact = common.write_json(ctx, storage, "line-items.json", {
            "line_items": [i.model_dump(mode="json") for i in items],
            "declaration": declaration.model_dump(mode="json"), "rows": [asdict(r) for r in result.rows],
            "unparsed_regions": [asdict(r) for r in result.unparsed_regions],
            "pages": [asdict(p) for p in result.pages],
            "predictions": {pid: [asdict(p) for p in values] for pid, values in predictions.items()},
            "versions": dict(versions), "provenance": prov.model_dump(mode="json")})
        insert_records(ctx.session, [("line_item", i) for i in items] + [("declaration_completeness", declaration)],
            claim_id=env.claim_id, input_revision=env.input_revision, stage="line_items", job_key=env.job_key, now=ctx.now)
        fields = ("entry_id", "page_id", "row_box_norm", "part_code", "part_mapping_status", "side", "operation",
                  "operation_mapping_status", "quantity", "unit_price", "printed_line_amount", "currency",
                  "cost_basis", "field_uncertainty")
        event = {"line_item_ids": [i.entry_id for i in items],
                 "line_items": [i.model_dump(mode="json", include=set(fields)) for i in items],
                 "layout_family": declaration.layout_family, "declaration_completeness": declaration.state,
                 "completeness_reasons": declaration.reasons, "unparsed_region_count": declaration.unparsed_region_count,
                 "engine": "layoutlmv3", "branch_stage": "line_items"}
        common.emit(ctx, "line_items", event, prov)
        rows = [i.model_dump(mode="json", include={"entry_id", "page_id", "row_box_norm", "amount_box_norm"}) for i in items]
        return {"line_item_ids": event["line_item_ids"], "row_boxes": rows, "extraction_ref": artifact,
                "processing_status": "succeeded", "declaration_completeness": declaration.state}
    return handler
