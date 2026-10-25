"""Unit tests for survey report metadata extraction and manual-edit preservation."""
from __future__ import annotations

from claim_cmev.documents.surveyor import (
    apply_extracted_metadata_to_claim,
    claim_needs_metadata_extraction,
    extract_report_metadata,
    extract_report_metadata_from_pages,
    extract_surveyor_name,
)


def test_subaru_survey_report_metadata_extraction():
    boxes = [
        {"text": "VEHICLE DETAILS", "box_norm": [0.010, 0.011, 0.158, 0.031]},
        {"text": "Vehicle Number", "box_norm": [0.004, 0.048, 0.135, 0.077]},
        {"text": "SMB1234A", "box_norm": [0.230, 0.047, 0.353, 0.082]},
        {"text": "Odometer km", "box_norm": [0.505, 0.048, 0.620, 0.078]},
        {"text": "95,432", "box_norm": [0.715, 0.042, 0.796, 0.086]},
        {"text": "Make and Model", "box_norm": [0.005, 0.094, 0.142, 0.122]},
        {"text": "Subaru Forester", "box_norm": [0.231, 0.091, 0.402, 0.125]},
        {"text": "Colour", "box_norm": [0.505, 0.092, 0.562, 0.123]},
        {"text": "Silver", "box_norm": [0.716, 0.085, 0.781, 0.128]},
        {"text": "Chassis or VIN", "box_norm": [0.005, 0.135, 0.126, 0.168]},
        {"text": "JF1SJD[C6]H123456", "box_norm": [0.232, 0.135, 0.460, 0.169]},
        {"text": "Registration Date", "box_norm": [0.506, 0.138, 0.652, 0.166]},
        {"text": "2018-05-18", "box_norm": [0.718, 0.135, 0.847, 0.169]},
        {"text": "Surveyor", "box_norm": [0.503, 0.200, 0.571, 0.218]},
        {"text": "H. Tan", "box_norm": [0.693, 0.198, 0.780, 0.220]},
        {"text": "Date of Accident", "box_norm": [0.058, 0.632, 0.180, 0.646]},
        {"text": "2026-10-12", "box_norm": [0.256, 0.628, 0.374, 0.648]},
        {"text": "Repairer", "box_norm": [0.503, 0.631, 0.571, 0.646]},
        {"text": "Star Automotive", "box_norm": [0.693, 0.628, 0.842, 0.649]},
    ]
    meta = extract_report_metadata(boxes)
    assert meta == {
        "surveyor": "H. Tan",
        "vehicle_make": "Subaru",
        "vehicle_model": "Forester",
        "vehicle_year": 2018,
        "plate": "SMB1234A",
        "workshop": "Star Automotive",
        "loss_date": "2026-10-12",
    }
    assert extract_surveyor_name(boxes) == "H. Tan"


def test_apply_extracted_metadata_respects_manually_set_fields():
    claim = {
        "claim_id": "01TEST",
        "surveyor": "Acery AI",
        "surveyor_manually_set": False,
        "manually_set_fields": ["workshop"],
        "policy_number": "",
        "workshop": "Custom Garage",
        "loss_date": None,
        "vehicle": {
            "make": "",
            "model": "",
            "year": None,
            "plate": "",
            "vehicle_class": "unknown",
        },
    }
    assert claim_needs_metadata_extraction(claim) is True
    extracted = {
        "surveyor": "H. Tan",
        "vehicle_make": "Subaru",
        "vehicle_model": "Forester",
        "vehicle_year": 2018,
        "plate": "SMB1234A",
        "workshop": "Star Automotive",
        "loss_date": "2026-10-12",
    }
    changed = apply_extracted_metadata_to_claim(claim, extracted)
    assert changed is True
    assert claim["surveyor"] == "H. Tan"
    assert claim["vehicle"]["make"] == "Subaru"
    assert claim["vehicle"]["model"] == "Forester"
    assert claim["vehicle"]["year"] == 2018
    assert claim["vehicle"]["plate"] == "SMB1234A"
    assert claim["vehicle"]["vehicle_class"] == "suv_crossover"
    assert claim["workshop"] == "Custom Garage"  # Preserved manual input
    assert claim["loss_date"] == "2026-10-12"
    assert claim_needs_metadata_extraction(claim) is False


def test_extract_report_metadata_from_pages_and_text_boxes():
    pages = [{"boxes": [{"text": "Surveyor: Cheryl Ong", "box_norm": [0.1, 0.1, 0.4, 0.13]}]}]
    text_boxes = [
        {"text": "Make / Model: Mercedes-Benz C180", "box_norm": [0.1, 0.2, 0.5, 0.23]},
        {"text": "Incident Date: 15/09/2026", "box_norm": [0.1, 0.3, 0.4, 0.33]},
    ]
    meta = extract_report_metadata_from_pages(pages, text_boxes)
    assert meta["surveyor"] == "Cheryl Ong"
    assert meta["vehicle_make"] == "Mercedes-Benz"
    assert meta["vehicle_model"] == "C180"
    assert meta["loss_date"] == "2026-09-15"


def test_full_page_dense_rows_do_not_bleed_across_adjacent_rows():
    """Verify closely spaced full-page rows (~0.022 dy) do not pick values from adjacent rows."""
    boxes = [
        # Row 1 (y ~ 0.584..0.600, cy=0.592)
        {"text": "Policy Excess", "box_norm": [0.503, 0.584, 0.598, 0.600]},
        {"text": "1,000.00", "box_norm": [0.691, 0.584, 0.765, 0.600]},
        # Row 2 (y ~ 0.607..0.622, cy=0.6145)
        {"text": "Date of Accident", "box_norm": [0.058, 0.607, 0.180, 0.622]},
        {"text": "2026-10-12", "box_norm": [0.254, 0.607, 0.354, 0.622]},
        {"text": "Repairer", "box_norm": [0.503, 0.607, 0.571, 0.622]},
        {"text": "Star Automotive", "box_norm": [0.693, 0.607, 0.842, 0.622]},
        # Row 3 (y ~ 0.630..0.646, cy=0.638)
        {"text": "Make and Model", "box_norm": [0.058, 0.630, 0.182, 0.646]},
        {"text": "Subaru Forester", "box_norm": [0.256, 0.630, 0.402, 0.646]},
        {"text": "Capacity", "box_norm": [0.503, 0.630, 0.570, 0.646]},
        {"text": "1995 cc", "box_norm": [0.691, 0.630, 0.760, 0.646]},
        # Row 4 (y ~ 0.653..0.669, cy=0.661)
        {"text": "Registration Date", "box_norm": [0.503, 0.653, 0.652, 0.669]},
        {"text": "2018-05-18", "box_norm": [0.693, 0.653, 0.805, 0.669]},
    ]
    meta = extract_report_metadata(boxes)
    assert meta["workshop"] == "Star Automotive"
    assert meta["vehicle_make"] == "Subaru"
    assert meta["vehicle_model"] == "Forester"
    assert meta["vehicle_year"] == 2018
    assert meta["loss_date"] == "2026-10-12"


