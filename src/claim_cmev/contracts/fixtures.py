"""Validated fixture claim bundles for the lean profile and the M8 rule tests.

Every record carries ``provenance.source_kind = "fixture"``. These are contract
fixtures: hand-authored branch outputs, never model, parser or detector results, and
never evidence of accuracy. A bundle is deterministic from (scenario, claim_id,
input_revision). ``notes`` describe the situation each record exercises; they are not
expected M8 outcomes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import hashlib
from typing import Any, Literal

from pydantic import model_validator

from .claims import ClaimFile, ClaimInput
from .common import (
    COST_BASIS,
    PART_CODES,
    ArtifactRef,
    ContractModel,
    Provenance,
    deterministic_id,
    make_job_key,
)
from .documents import (
    DeclarationCompleteness,
    DocumentPage,
    FieldUncertainty,
    LineItem,
    PageQuality,
    PageTransform,
    PenMark,
    TextBox,
    with_effective_price,
)
from .imaging import (
    AssignmentCandidate,
    CoverageConfirmation,
    IdentityConfirmation,
    ImageDamageObservation,
    ImageQuality,
    ImageTransform,
    MaskRef,
    PartCoverage,
    PartPrediction,
    PartSummary,
    ViewScreen,
)

FIXTURE_TAG = "contracts-fixtures-1"
VERSIONS: dict[str, dict[str, str]] = {
    "intake": {"fixture": FIXTURE_TAG, "code": "0.2.0"},
    "parts": {"parts_model": "fixture-parts/0.0.0", "preprocess_config": "fixture-vision-pre/0.0.0",
              "taxonomy": "parts-1.0.0", "fixture": FIXTURE_TAG, "code": "0.2.0"},
    "damage": {"damage_model": "fixture-damage/0.0.0", "parts_model": "fixture-parts/0.0.0",
               "assignment_config": "fixture-assign/0.0.0", "taxonomy": "damage-cardd-1.0.0",
               "fixture": FIXTURE_TAG, "code": "0.2.0"},
    "summary": {"summary_config": "fixture-summary/0.0.0", "taxonomy": "parts-1.0.0", "fixture": FIXTURE_TAG, "code": "0.2.0"},
    "ocr": {"ocr": "fixture-ocr/0.0.0", "preprocess_config": "fixture-doc-pre/0.0.0", "fixture": FIXTURE_TAG, "code": "0.2.0"},
    "lineitems": {"parser_config": "fixture-parser/0.0.0", "taxonomy": "parts-1.0.0", "fixture": FIXTURE_TAG, "code": "0.2.0"},
    "penmarks": {"penmark_model": "fixture-penmarks/0.0.0", "link_config": "fixture-marklink/0.0.0",
                 "fixture": FIXTURE_TAG, "code": "0.2.0"},
}
SERVICES = {"intake": "cmev-api", "parts": "cmev-worker-parts", "damage": "cmev-worker-damage",
            "summary": "cmev-worker-summary", "ocr": "cmev-worker-ocr", "lineitems": "cmev-worker-lineitems",
            "penmarks": "cmev-worker-penmarks", "review": "cmev-api"}
BASE_TIME = datetime(2026, 9, 22, 3, 0, tzinfo=UTC)
MODEL = 512  # model frame edge, pixels
PAGE_W, PAGE_H = 2480, 3508  # corrected render, pixels
ACTOR = "surveyor:fixture"


# ---------------------------------------------------------------- scenario specs
@dataclass
class _Photo:
    name: str
    visible: dict[str, tuple[int, float, bool, tuple[str, ...]]]  # part -> (pixels, conf, screen passes, fail reasons)


@dataclass
class _Obs:
    photo: str
    damage: str
    confidence: float
    candidates: tuple[tuple[str, float], ...]
    bbox: tuple[float, float, float, float]
    area_pixels: int
    part_reason: str | None = None  # None means assigned to the first candidate
    background: float = 0.03


@dataclass
class _Row:
    part_text: str
    op_text: str
    qty_text: str
    unit_text: str
    amount_text: str
    part_code: str | None
    side: str
    operation: str | None
    part_status: str = "resolved"
    op_status: str = "resolved"
    uncertain: tuple[tuple[str, str], ...] = ()
    low_confidence: tuple[str, ...] = ()  # columns whose OCR text is low confidence


@dataclass
class _Mark:
    kind: str
    state: str
    row: int | None
    candidates: tuple[int, ...] = ()
    link_reason: str = "unambiguous_row_overlap"
    amount: str | None = None
    confidence: float = 0.85
    rule_id: str | None = None
    between: tuple[int, int] | None = None


@dataclass
class _Scenario:
    title: str
    make: str
    model: str
    year: int
    vehicle_class: str
    photos: list[_Photo]
    observations: list[_Obs]
    identity: list[tuple[str, str, str]]  # (photo, part, side)
    coverage: list[tuple[str, str, tuple[str, ...], bool, str | None]]  # (part, side, photos, covers_enough, reason)
    rows: list[_Row]
    marks: list[_Mark]
    completeness: tuple[str, tuple[str, ...], int]  # state, reasons, unparsed regions
    notes: dict[str, str] = field(default_factory=dict)  # "row:<i>" / "obs:<i>" / "slot:<part>:<side>" -> note


PASS = (True, ())
_SCENARIOS: dict[str, _Scenario] = {
    "pending_price_change": _Scenario(
        title="Pending price change on a supported part, rows with unknown side, no identity confirmations yet",
        make="Toyota", model="Corolla Altis", year=2019, vehicle_class="sedan_standard",
        photos=[
            _Photo("front.jpg", {"front-bumper": (48211, 0.91, *PASS), "grille": (9310, 0.86, *PASS),
                                 "hood": (61550, 0.89, *PASS), "headlight": (6120, 0.84, *PASS)}),
            _Photo("front_left.jpg", {"front-bumper": (30118, 0.88, *PASS), "fender": (22504, 0.87, *PASS),
                                      "front-door": (40102, 0.9, *PASS), "headlight": (4870, 0.8, *PASS),
                                      "mirror": (3055, 0.78, *PASS)}),
        ],
        observations=[
            _Obs("front.jpg", "dent", 0.83, (("front-bumper", 0.91), ("grille", 0.06)), (0.312, 0.5504, 0.4871, 0.6693), 5412),
            _Obs("front_left.jpg", "dent", 0.78, (("front-bumper", 0.88),), (0.2011, 0.5902, 0.3314, 0.6887), 3902),
            _Obs("front_left.jpg", "scratch", 0.64, (("front-door", 0.52), ("fender", 0.44)), (0.6015, 0.4402, 0.7233, 0.488),
                 913, part_reason="ambiguous_between_parts", background=0.04),
        ],
        identity=[], coverage=[],
        rows=[
            _Row("FRT BUMPER", "REPLACE", "1", "1150.00", "1150.00", "front-bumper", "not_applicable", "replace"),
            _Row("FRT DOOR", "REPAIR", "1", "480.00", "480.00", "front-door", "unknown", "repair",
                 uncertain=(("side", "side_absent_in_text"),)),
            _Row("HEADLAMP", "REPLACE", "1", "860.00", "860.00", "headlight", "unknown", "replace",
                 uncertain=(("side", "side_absent_in_text"),)),
        ],
        marks=[_Mark("price_change", "pending", 0, (0,), confidence=0.79, rule_id="PC-1")],
        completeness=("complete", (), 0),
        notes={"row:0": "Pending price change linked to this row: the effective price is unresolved and the printed "
                        "1150.00 is never used.",
               "row:1": "Side absent from the printed text: side unknown, identity unresolved.",
               "row:2": "Side absent from the printed text: side unknown.",
               "obs:2": "Damage straddles front-door and fender: unresolved, both candidates kept.",
               "coverage": "No identity confirmations yet, so every slot is unresolved (identity_not_resolved)."}),
    "exclusion_and_supported": _Scenario(
        title="Confirmed exclusion, confirmed price change on a supported part, cropped fender, possible addition",
        make="Honda", model="City", year=2019, vehicle_class="sedan_standard",
        photos=[
            _Photo("front.jpg", {"front-bumper": (47020, 0.92, *PASS), "grille": (9120, 0.85, *PASS),
                                 "hood": (60210, 0.9, *PASS), "headlight": (5910, 0.83, *PASS)}),
            _Photo("front_left.jpg", {"front-bumper": (29870, 0.89, *PASS),
                                      "fender": (11020, 0.81, False, ("cropped_at_border",)),
                                      "front-door": (39540, 0.9, *PASS), "mirror": (2980, 0.77, *PASS)}),
            _Photo("rear_left.jpg", {"back-door": (41230, 0.9, *PASS), "back-bumper": (26410, 0.87, *PASS),
                                     "tail-light": (4410, 0.82, *PASS), "quarter-panel": (18820, 0.85, *PASS)}),
        ],
        observations=[
            _Obs("front.jpg", "dent", 0.81, (("front-bumper", 0.93), ("grille", 0.04)), (0.3301, 0.5611, 0.4705, 0.6602), 4980),
            _Obs("front_left.jpg", "scratch", 0.72, (("front-bumper", 0.86), ("fender", 0.1)), (0.1902, 0.6012, 0.3011, 0.6511), 2210),
            _Obs("front.jpg", "dent", 0.77, (("hood", 0.95),), (0.4102, 0.3105, 0.5207, 0.3906), 3610, background=0.05),
            _Obs("rear_left.jpg", "dent", 0.69, (("back-door", 0.9), ("quarter-panel", 0.07)), (0.3605, 0.4402, 0.4808, 0.5301), 4120),
        ],
        identity=[("front.jpg", "front-bumper", "not_applicable"), ("front_left.jpg", "front-bumper", "not_applicable"),
                  ("front.jpg", "hood", "not_applicable"), ("front_left.jpg", "fender", "left"),
                  ("rear_left.jpg", "back-door", "left")],
        coverage=[("front-bumper", "not_applicable", ("front.jpg", "front_left.jpg"), True, None),
                  ("hood", "not_applicable", ("front.jpg",), True, None),
                  ("back-door", "left", ("rear_left.jpg",), True, None)],
        rows=[
            _Row("REAR DOOR LH", "REPAIR", "1", "480.00", "480.00", "back-door", "left", "repair"),
            _Row("FRT BUMPER", "REPLACE", "1", "1150.00", "1150.00", "front-bumper", "not_applicable", "replace"),
            _Row("FENDER LH", "REPAIR", "1", "320.00", "320.00", "fender", "left", "repair"),
        ],
        marks=[_Mark("exclusion", "confirmed", 0, (0,), confidence=0.88, rule_id="EX-1"),
               _Mark("price_change", "confirmed", 1, (1,), amount="980.00", confidence=0.79, rule_id="PC-1")],
        completeness=("complete", (), 0),
        notes={"row:0": "Confirmed exclusion: a row state, never an ok finding.",
               "row:1": "Confirmed identity, confirmed adequate coverage and a supporting dent; confirmed price change "
                        "980.00 is the effective price, not the printed 1150.00.",
               "row:2": "Fender left is identified but its only view is cropped: coverage inadequate.",
               "obs:2": "Dent on the resolved, adequately covered hood with no declared row.",
               "obs:3": "Dent on the resolved back-door left, the same physical part as the excluded row 0."}),
    "partial_extraction": _Scenario(
        title="Readable page parsed to partial completeness, unreadable amount, ambiguous row, unlinked price change",
        make="Mazda", model="CX-5", year=2022, vehicle_class="suv_crossover",
        photos=[_Photo("front_right.jpg", {"front-bumper": (45120, 0.9, *PASS), "headlight": (5880, 0.82, *PASS),
                                           "hood": (58830, 0.88, *PASS), "fender": (20410, 0.85, *PASS)})],
        observations=[
            _Obs("front_right.jpg", "crack", 0.74, (("headlight", 0.89), ("front-bumper", 0.08)), (0.6804, 0.4903, 0.7502, 0.5405), 1840),
            _Obs("front_right.jpg", "dent", 0.58, (("front-bumper", 0.84),), (0.4406, 0.6208, 0.5503, 0.7004), 3350),
        ],
        identity=[], coverage=[],
        rows=[
            _Row("FRT BUMPER", "REPAIR", "1", "420.00", "420.00", "front-bumper", "not_applicable", "repair"),
            _Row("FRT DOOR LH", "REPAIR", "1", "48O.OO", "48O.OO", "front-door", "left", "repair",
                 uncertain=(("unit_price", "ocr_letter_digit_confusion"), ("printed_line_amount", "ocr_letter_digit_confusion")),
                 low_confidence=("unit", "amount")),
            _Row("HD LAMP BRKT", "R/R", "", "", "135.00", None, "unknown", None, part_status="ambiguous",
                 op_status="unmapped",
                 uncertain=(("part_code", "ocr_low_confidence"), ("operation", "column_not_located"),
                            ("quantity", "column_not_located"), ("unit_price", "column_not_located"),
                            ("side", "side_absent_in_text")),
                 low_confidence=("part",)),
        ],
        marks=[_Mark("price_change", "pending", None, (0, 1), link_reason="mark_between_rows", confidence=0.71,
                     rule_id="PC-3", between=(0, 1))],
        completeness=("partial", ("uncertain_required_field",), 1),
        notes={"row:0": "Readable amount, but an unlinked price change names this row: effective price unresolved.",
               "row:1": "Printed amount '48O.OO' has a letter/digit confusion: amount null, never zero; the "
                        "effective price is unresolved.",
               "row:2": "Part text maps ambiguously and the operation is unmapped: both null with reasons.",
               "mark:0": "Price change written between rows 0 and 1: unlinked, both candidates retained.",
               "declaration": "Readable page with an uncertain required field: partial, never explicitly_empty."}),
    "unphotographed_part": _Scenario(
        title="Declared part absent from every photograph, one fully supported row, one row with unknown side",
        make="Hyundai", model="Tucson", year=2020, vehicle_class="suv_crossover",
        photos=[
            _Photo("left_side.jpg", {"front-door": (52110, 0.91, *PASS), "back-door": (47050, 0.9, *PASS),
                                     "fender": (18030, 0.84, *PASS), "mirror": (3120, 0.8, *PASS),
                                     "rocker-panel": (9920, 0.79, *PASS)}),
            _Photo("front.jpg", {"front-bumper": (46200, 0.91, *PASS), "hood": (59940, 0.89, *PASS),
                                 "grille": (9010, 0.86, *PASS)}),
        ],
        observations=[
            _Obs("left_side.jpg", "scratch", 0.8, (("front-door", 0.94), ("fender", 0.03)), (0.3208, 0.4506, 0.4403, 0.5102), 2640),
            _Obs("left_side.jpg", "dent", 0.66, (("back-door", 0.87), ("rocker-panel", 0.09)), (0.5602, 0.5204, 0.6307, 0.5908), 1980),
        ],
        identity=[("left_side.jpg", "front-door", "left"), ("left_side.jpg", "tail-light", "left")],
        coverage=[("front-door", "left", ("left_side.jpg",), True, None)],
        rows=[
            _Row("TAIL LAMP LH", "REPLACE", "1", "310.00", "310.00", "tail-light", "left", "replace"),
            _Row("FRT DOOR LH", "REPAIR", "1", "540.00", "540.00", "front-door", "left", "repair"),
            _Row("REAR DOOR", "REPAIR", "1", "450.00", "450.00", "back-door", "unknown", "repair",
                 uncertain=(("side", "side_absent_in_text"),)),
        ],
        marks=[], completeness=("complete", (), 0),
        notes={"row:0": "The left tail-light is identified on the left-side photo but no photo holds an accepted "
                        "tail-light mask: coverage not_visible.",
               "row:1": "Confirmed identity, confirmed adequate coverage and a supporting scratch.",
               "row:2": "Side absent from the printed text; the back-door observation stays part_only.",
               "marks": "No pen marks were proposed on this page; that is not proof that none exist."}),
}
SCENARIOS: tuple[str, ...] = tuple(_SCENARIOS)


# ---------------------------------------------------------------- bundle record
class FixtureBundle(ContractModel):
    scenario: str
    title: str
    claim: ClaimInput
    files: list[ClaimFile]
    image_quality: list[ImageQuality]
    part_predictions: list[PartPrediction]
    observations: list[ImageDamageObservation]
    identity_confirmations: list[IdentityConfirmation]
    coverage_confirmations: list[CoverageConfirmation]
    part_summaries: list[PartSummary]
    coverage: list[PartCoverage]
    pages: list[DocumentPage]
    line_items: list[LineItem]
    declaration: DeclarationCompleteness
    pen_marks: list[PenMark]
    image_branch_state: Literal["complete", "failed", "skipped_no_photos"] = "complete"
    document_branch_state: Literal["complete", "failed", "skipped_no_pages"] = "complete"
    notes: dict[str, str]

    @property
    def vehicle_class(self) -> str:
        return self.claim.vehicle_class

    @property
    def currency(self) -> str:
        return self.claim.currency

    @property
    def cost_basis(self) -> str:
        return self.claim.cost_basis

    def records(self) -> list[Any]:
        """Every claim-scoped record in the bundle."""
        groups = (self.files, self.image_quality, self.part_predictions, self.observations, self.identity_confirmations,
                  self.coverage_confirmations, self.part_summaries, self.coverage, self.pages, self.line_items,
                  self.pen_marks)
        return [self.claim, self.declaration, *(r for group in groups for r in group)]

    @model_validator(mode="after")
    def _consistent(self) -> FixtureBundle:
        for record in self.records():
            if record.provenance.source_kind != "fixture":
                raise ValueError(f"{type(record).__name__} is not marked as a fixture")
            if (record.claim_id, record.input_revision) != (self.claim.claim_id, self.claim.input_revision):
                raise ValueError(f"{type(record).__name__} belongs to another claim or input revision")
        entries = {i.entry_id for i in self.line_items}
        for mark in self.pen_marks:
            if (mark.entry_id and mark.entry_id not in entries) or set(mark.candidate_entry_ids) - entries:
                raise ValueError(f"mark {mark.mark_id} references an unknown line item")
        observations = [o.observation_id for o in self.observations]
        members = [m for s in self.part_summaries for m in s.member_observation_ids]
        if sorted(members) != sorted(observations):
            raise ValueError("every observation belongs to exactly one part summary")
        slots = [(c.part_code, c.side) for c in self.coverage]
        if len(slots) != len(set(slots)) or {c.part_code for c in self.coverage} != set(PART_CODES):
            raise ValueError("coverage holds one row per slot and covers every part code")
        pages = {p.page_id for p in self.pages}
        if any(i.page_id not in pages for i in self.line_items):
            raise ValueError("line items reference stored pages")
        return self


# ---------------------------------------------------------------- builders
class _Ids:
    def __init__(self, claim_id: str, rev: int):
        self.claim_id, self.rev = claim_id, rev

    def key(self, task: str, family: str, target: str = "all") -> str:
        return make_job_key(self.claim_id, self.rev, task, VERSIONS[family], target)

    def artifact(self, name: str, media_type: str, size: int = 4096) -> ArtifactRef:
        uri = f"s3://cmev-derived/fixture/{self.claim_id}/{self.rev}/{name}"
        return ArtifactRef(artifact_id=deterministic_id("art", uri), object_uri=uri,
                           sha256=hashlib.sha256(uri.encode()).hexdigest(), media_type=media_type, byte_count=size)


def _prov(family: str, scenario: str) -> Provenance:
    return Provenance(source_kind="fixture", runtime_profile="lean", producer_service=SERVICES[family],
                      source_dataset_id=f"fixture:{scenario}")


def _norm(box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = box
    return (round(x0 / PAGE_W, 6), round(y0 / PAGE_H, 6), round(x1 / PAGE_W, 6), round(y1 / PAGE_H, 6))


def _quad(box: tuple[float, float, float, float]):
    x0, y0, x1, y1 = box
    return ((x0, y0), (x1, y0), (x1, y1), (x0, y1))


_COLUMNS = {"part": (198, 940), "op": (986, 1340), "qty": (1402, 1520), "unit": (1600, 1900), "amount": (2040, 2290)}
_HEADERS = {"part": "DESCRIPTION", "op": "OPERATION", "qty": "QTY", "unit": "UNIT PRICE", "amount": "AMOUNT"}
_ROW_TOP, _ROW_PITCH, _ROW_HEIGHT = 872, 60, 54


def _row_y(index: int) -> tuple[float, float]:
    top = _ROW_TOP + index * _ROW_PITCH
    return top, top + _ROW_HEIGHT


def fixture_bundle(scenario: str, claim_id: str, input_revision: int = 1) -> FixtureBundle:
    """Build the named, validated fixture bundle for ``claim_id`` at ``input_revision``."""
    if scenario not in _SCENARIOS:
        raise KeyError(f"unknown fixture scenario {scenario!r}; choose from {SCENARIOS}")
    spec = _SCENARIOS[scenario]
    ids = _Ids(claim_id, input_revision)
    scope = {"claim_id": claim_id, "input_revision": input_revision}
    prov = lambda family: _prov(family, scenario)  # noqa: E731
    at = lambda minutes: BASE_TIME + timedelta(minutes=minutes)  # noqa: E731

    # files ---------------------------------------------------------------
    intake = ids.key("intake", "intake")
    files, photo_ids = [], {}
    for index, photo in enumerate(spec.photos):
        file_id = deterministic_id("ph", intake, index)
        photo_ids[photo.name] = file_id
        uri = f"s3://cmev-originals/fixture/{claim_id}/{file_id}.jpg"
        sha = hashlib.sha256(uri.encode()).hexdigest()
        files.append(ClaimFile(**scope, provenance=prov("intake"), file_id=file_id, kind="photo",
                               member_revisions=[input_revision], original_name=photo.name, media_type="image/jpeg",
                               sha256=sha, byte_count=3_500_000 + index,
                               object_ref=ArtifactRef(artifact_id=file_id, object_uri=uri, sha256=sha,
                                                      media_type="image/jpeg", byte_count=3_500_000 + index),
                               upload_status="stored", width=4032, height=3024, exif_orientation=1))
    page_file_id = deterministic_id("pg", intake, 0)
    page_uri = f"s3://cmev-originals/fixture/{claim_id}/{page_file_id}.jpg"
    page_sha = hashlib.sha256(page_uri.encode()).hexdigest()
    files.append(ClaimFile(**scope, provenance=prov("intake"), file_id=page_file_id, kind="estimate_page",
                           member_revisions=[input_revision], original_name="estimate_page_1.jpg",
                           media_type="image/jpeg", sha256=page_sha, byte_count=2_214_480,
                           object_ref=ArtifactRef(artifact_id=page_file_id, object_uri=page_uri, sha256=page_sha,
                                                  media_type="image/jpeg", byte_count=2_214_480),
                           upload_status="stored", width=PAGE_W, height=PAGE_H, exif_orientation=1))
    claim = ClaimInput(**scope, provenance=prov("intake"), external_reference=f"FIX-{scenario.upper()}",
                       make=spec.make, model=spec.model, year=spec.year, vehicle_class=spec.vehicle_class,
                       currency="SGD", cost_basis=COST_BASIS, file_ids=[f.file_id for f in files], created_at=at(0),
                       previous_input_revision=None if input_revision == 1 else input_revision - 1)

    # image branch ----------------------------------------------------------
    scale = MODEL / 4032
    transform = ImageTransform(stored_width=4032, stored_height=3024, model_width=MODEL, model_height=MODEL,
                               scale=scale, pad_left=0.0, pad_top=(MODEL - 3024 * scale) / 2)
    quality, predictions, part_masks, views_by_part = [], [], {}, {}
    for photo in spec.photos:
        photo_id = photo_ids[photo.name]
        key = ids.key("parts_segment", "parts", photo_id)
        mask = ids.artifact(f"parts_{photo_id}.png", "image/png")
        part_masks[photo.name] = MaskRef(artifact_id=mask.artifact_id, object_uri=mask.object_uri, sha256=mask.sha256,
                                         width=MODEL, height=MODEL, encoding="class_index_png", source_photo_id=photo_id)
        quality.append(ImageQuality(**scope, provenance=prov("parts"), versions=VERSIONS["parts"], photo_id=photo_id,
                                    state="acceptable", blur_score=210.0, exposure_state="normal",
                                    config_version="fixture-quality/0.0.0"))
        for index, (part, (pixels, conf, passes, fails)) in enumerate(photo.visible.items()):
            predictions.append(PartPrediction(**scope, provenance=prov("parts"), versions=VERSIONS["parts"],
                                              prediction_id=deterministic_id("pp", key, index), photo_id=photo_id,
                                              part_code=part, mask_ref=part_masks[photo.name], mean_confidence=conf,
                                              pixel_count=pixels, transform=transform))
            signals = {"part_area_fraction": round(pixels / MODEL**2, 4), "blur_score": 185.0, "mean_luma": 126.0,
                       "clipped_fraction": 0.02, "border_touch_fraction": 0.46 if "cropped_at_border" in fails else 0.05}
            views_by_part.setdefault(part, []).append(
                ViewScreen(photo_id=photo_id, screen_result="pass" if passes else "fail", signals=signals,
                           reasons=list(fails)))

    observations = []
    for index, obs in enumerate(spec.observations):
        photo_id = photo_ids[obs.photo]
        key = ids.key("damage_segment", "damage", photo_id)
        damage_mask = ids.artifact(f"damage_{photo_id}.png", "image/png")
        assigned = obs.part_reason is None
        candidates = [AssignmentCandidate(part_code=p, containment=c, rank=r + 1) for r, (p, c) in enumerate(obs.candidates)]
        observations.append(ImageDamageObservation(
            **scope, provenance=prov("damage"), versions=VERSIONS["damage"],
            observation_id=deterministic_id("ob", key, index), photo_id=photo_id, damage_code=obs.damage,
            damage_confidence=obs.confidence, assignment_status="assigned" if assigned else "unresolved",
            part_code=obs.candidates[0][0] if assigned else None, part_reason=obs.part_reason, candidates=candidates,
            primary_containment=obs.candidates[0][1],
            runner_up_containment=obs.candidates[1][1] if len(obs.candidates) > 1 else None,
            background_containment=obs.background, area_pixels=obs.area_pixels,
            area_fraction=round(obs.area_pixels / MODEL**2, 6), area_denominator_pixels=MODEL**2,
            damage_mask_ref=MaskRef(artifact_id=damage_mask.artifact_id, object_uri=damage_mask.object_uri,
                                    sha256=damage_mask.sha256, width=MODEL, height=MODEL, encoding="class_index_png",
                                    source_photo_id=photo_id, component_index=index + 1),
            part_mask_ref=part_masks[obs.photo], bbox_norm=obs.bbox, assignment_config_version="fixture-assign/0.0.0"))

    review = f"{claim_id}:{input_revision}:review"
    identities = [IdentityConfirmation(**scope, provenance=prov("review"),
                                       confirmation_id=deterministic_id("ic", review, i), actor=ACTOR,
                                       recorded_at=at(20 + i), review_revision=i + 1, photo_id=photo_ids[photo],
                                       part_code=part, side=side)
                  for i, (photo, part, side) in enumerate(spec.identity)]
    coverage_confs = [CoverageConfirmation(**scope, provenance=prov("review"),
                                           confirmation_id=deterministic_id("cc", review, i), actor=ACTOR,
                                           recorded_at=at(40 + i), review_revision=len(identities) + i + 1,
                                           part_code=part, side=side,
                                           covering_photo_ids=[photo_ids[p] for p in photos],
                                           covers_enough=enough, reason=reason)
                      for i, (part, side, photos, enough, reason) in enumerate(spec.coverage)]

    # M3 grouping: resolved by a recorded identity confirmation on the observation's photo
    summary_key = ids.key("part_summary", "summary")
    identity_of = {(c.photo_id, c.part_code): c for c in identities}
    groups: dict[tuple, list[ImageDamageObservation]] = {}
    for obs in observations:
        confirmation = identity_of.get((obs.photo_id, obs.part_code)) if obs.part_code else None
        if confirmation:
            key = ("resolved", obs.part_code, confirmation.side)
        elif obs.part_code:
            key = ("part_only", obs.part_code, "unknown")
        else:
            key = ("unresolved", obs.observation_id, "unknown")
        groups.setdefault(key, []).append(obs)
    summaries = []
    for index, ((status, part, side), members) in enumerate(groups.items()):
        largest = max(members, key=lambda o: o.area_fraction)
        confirmation_ids = sorted({identity_of[(o.photo_id, o.part_code)].confirmation_id for o in members
                                   if (o.photo_id, o.part_code) in identity_of})
        summaries.append(PartSummary(
            **scope, provenance=prov("summary"), versions=VERSIONS["summary"],
            summary_id=deterministic_id("ps", summary_key, index), identity_status=status,
            part_code=None if status == "unresolved" else part, side=side,
            member_observation_ids=[o.observation_id for o in members], observation_count=len(members),
            damage_codes=sorted({o.damage_code for o in members}),
            supporting_photo_ids=sorted({o.photo_id for o in members}), mask_refs=[o.damage_mask_ref for o in members],
            representative_area_fraction=largest.area_fraction, representative_observation_id=largest.observation_id,
            max_confidence=max(o.damage_confidence for o in members), identity_confirmation_ids=confirmation_ids,
            reasons=[] if status == "resolved" else [
                "identity_not_resolved" if status == "part_only" else largest.part_reason]))

    # M3 coverage: one row per slot; a slot is resolved only by an identity confirmation
    resolved_slots: dict[tuple[str, str], list[IdentityConfirmation]] = {}
    for confirmation in identities:
        resolved_slots.setdefault((confirmation.part_code, confirmation.side), []).append(confirmation)
    coverage_of = {(c.part_code, c.side): c for c in coverage_confs}
    coverage, index = [], 0
    for part in PART_CODES:
        slots = [s for s in resolved_slots if s[0] == part] or [(part, "unknown")]
        for slot in slots:
            confirmations = resolved_slots.get(slot, [])
            confirmed_photos = {c.photo_id for c in confirmations}
            views = [v for v in views_by_part.get(part, []) if v.photo_id in confirmed_photos]
            confirmation = coverage_of.get(slot)
            if slot[1] == "unknown":
                state, reasons = "unresolved", ["identity_not_resolved"]
                views = views_by_part.get(part, [])
            elif not views:
                state, reasons = "not_visible", ["no_accepted_part_mask"]
            elif not any(v.screen_result == "pass" for v in views):
                state, reasons = "inadequate", sorted({r for v in views for r in v.reasons})
            elif confirmation is None:
                state, reasons = "inadequate", ["awaiting_coverage_confirmation"]
            elif not confirmation.covers_enough:
                state, reasons = "inadequate", [confirmation.reason]
            else:
                state, reasons = "adequate", []
            coverage.append(PartCoverage(
                **scope, provenance=prov("summary"), versions=VERSIONS["summary"],
                coverage_id=deterministic_id("pc", summary_key, index), part_code=part, side=slot[1], state=state,
                covering_photo_ids=[v.photo_id for v in views if v.screen_result == "pass"] if state == "adequate" else [],
                views=views, reasons=reasons,
                coverage_confirmation_id=confirmation.confirmation_id if confirmation else None,
                identity_confirmation_ids=[c.confirmation_id for c in confirmations]))
            index += 1

    # document branch ---------------------------------------------------------
    page_key = ids.key("page_read", "ocr", page_file_id)
    page_id = deterministic_id("dp", page_key, 0)
    boxes, order = [], 0

    def add_box(text: str, px: tuple[float, float, float, float], confidence: float, flags: tuple[str, ...] = ()) -> str:
        nonlocal order
        box_id = deterministic_id("bx", page_key, order)
        boxes.append(TextBox(box_id=box_id, order_index=order, text=text, confidence=confidence, box_norm=_norm(px),
                             quad_rectified=_quad(px), quad_original=_quad(px), granularity="line", flags=list(flags)))
        order += 1
        return box_id

    for column, (x0, x1) in _COLUMNS.items():
        add_box(_HEADERS[column], (x0, 806, x1, 850), 0.99)
    row_source: list[list[str]] = []
    for r, row in enumerate(spec.rows):
        top, bottom = _row_y(r)
        texts = {"part": row.part_text, "op": row.op_text, "qty": row.qty_text, "unit": row.unit_text,
                 "amount": row.amount_text}
        source = []
        for column, text in texts.items():
            if not text:
                continue
            low = column in row.low_confidence
            x0, x1 = _COLUMNS[column]
            source.append(add_box(text, (x0, top + 4, x1, bottom - 4), 0.41 if low else 0.95,
                                  ("low_confidence_region",) if low else ()))
        row_source.append(source)
    total_top = _row_y(len(spec.rows))[0] + 30
    add_box("SUB TOTAL", (1600, total_top, 1900, total_top + 44), 0.98)
    page = DocumentPage(
        **scope, provenance=prov("ocr"), versions=VERSIONS["ocr"], page_id=page_id, file_id=page_file_id, page_number=1,
        corrected_render_ref=ids.artifact(f"corrected_{page_file_id}.png", "image/png", 5_120_934),
        page_reading_ref=ids.artifact(f"reading_{page_file_id}.json", "application/json", 84_211),
        transform=PageTransform(source_width=PAGE_W, source_height=PAGE_H, corrected_width=PAGE_W,
                                corrected_height=PAGE_H, exif_orientation=1, geometry_correction="none",
                                correction_reason="flat_scan"),
        text_granularity="line", text_boxes=boxes,
        mean_text_confidence=round(sum(b.confidence for b in boxes) / len(boxes), 4),
        quality=PageQuality(state="complete", box_count=len(boxes), low_confidence_fraction=round(
            sum(b.confidence < 0.5 for b in boxes) / len(boxes), 4)))

    item_key = ids.key("line_items_extract", "lineitems")
    items = []
    for r, row in enumerate(spec.rows):
        top, bottom = _row_y(r)
        printed = row.amount_text if row.amount_text and "printed_line_amount" not in dict(row.uncertain) else None
        unit = row.unit_text if row.unit_text and "unit_price" not in dict(row.uncertain) else None
        items.append(LineItem(
            **scope, provenance=prov("lineitems"), versions=VERSIONS["lineitems"],
            entry_id=deterministic_id("li", item_key, page_id, r), page_id=page_id, page_number=1,
            row_box_norm=_norm((_COLUMNS["part"][0], top, 2300, bottom)),
            amount_box_norm=_norm((_COLUMNS["amount"][0], top + 4, _COLUMNS["amount"][1], bottom - 4)),
            original_part_text=row.part_text, original_operation_text=row.op_text,
            original_amount_text=row.amount_text or None,
            part_code=row.part_code, part_mapping_status=row.part_status, side=row.side,
            side_source="absent" if row.side in ("unknown", "not_applicable") else "document_text",
            operation=row.operation, operation_mapping_status=row.op_status,
            quantity=row.qty_text or None, unit_price=unit, printed_line_amount=printed,
            effective_price=printed, effective_price_source="printed" if printed else "unresolved",
            effective_price_reason=None if printed else "amount_unreadable", currency="SGD", cost_basis=COST_BASIS,
            field_uncertainty=[FieldUncertainty(field=f, reason=why) for f, why in row.uncertain],
            source_box_ids=row_source[r]))

    mark_key = ids.key("pen_marks_detect", "penmarks")
    marks = []
    for m, mark in enumerate(spec.marks):
        if mark.between:
            top = _row_y(mark.between[0])[1] - 22
            px = (2005, top, 2210, top + 44)
        elif mark.kind == "exclusion":
            top, bottom = _row_y(mark.row)
            px = (1850, top, 2320, bottom + 8)
        else:
            top, _ = _row_y(mark.row)
            px = (2030, top - 2, 2280, top + 44)
        decided = mark.state != "pending"
        entry = items[mark.row].entry_id if mark.row is not None else None
        marks.append(PenMark(
            **scope, provenance=prov("penmarks"), versions=VERSIONS["penmarks"],
            mark_id=deterministic_id("pm", mark_key, m), page_id=page_id, box_norm=_norm(px), mark_type=mark.kind,
            detection_confidence=mark.confidence, entry_id=entry,
            candidate_entry_ids=[items[c].entry_id for c in mark.candidates], link_reason=mark.link_reason,
            state=mark.state, confirmed_amount=mark.amount, confirmed_currency="SGD" if mark.amount else None,
            confirmed_cost_basis=COST_BASIS if mark.amount else None,
            decision_action_id=deterministic_id("ra", review, "mark", m) if decided else None,
            decided_by=ACTOR if decided else None, decided_at=at(60 + m) if decided else None,
            review_revision=len(identities) + len(coverage_confs) + m + 1 if decided else None, origin="detector",
            model_entry_id=entry, rule_id=mark.rule_id))
    items = [with_effective_price(item, marks) for item in items]

    state, reasons, unparsed = spec.completeness
    declaration = DeclarationCompleteness(
        **scope, provenance=prov("lineitems"), versions=VERSIONS["lineitems"], state=state, reasons=list(reasons),
        unparsed_region_count=unparsed, layout_family="family-a-ruled-grid", pages_covered=[page_id], source="parser")

    def resolve(label: str) -> str:
        kind, _, rest = label.partition(":")
        if kind == "row" and rest.isdigit():
            return items[int(rest)].entry_id
        if kind == "obs" and rest.isdigit():
            return observations[int(rest)].observation_id
        if kind == "mark" and rest.isdigit():
            return marks[int(rest)].mark_id
        return label

    return FixtureBundle(scenario=scenario, title=spec.title, claim=claim, files=files, image_quality=quality,
                         part_predictions=predictions, observations=observations, identity_confirmations=identities,
                         coverage_confirmations=coverage_confs, part_summaries=summaries, coverage=coverage,
                         pages=[page], line_items=items, declaration=declaration, pen_marks=marks,
                         notes={resolve(k): v for k, v in spec.notes.items()})


def all_fixture_bundles(claim_ids: dict[str, str] | None = None, input_revision: int = 1) -> dict[str, FixtureBundle]:
    """Every scenario, each under a stable default ULID unless ``claim_ids`` maps it to another."""
    defaults = {name: f"01K6F1XTVRE000000000000{index:03d}" for index, name in enumerate(SCENARIOS)}
    chosen = {**defaults, **(claim_ids or {})}
    return {name: fixture_bundle(name, chosen[name], input_revision) for name in SCENARIOS}
