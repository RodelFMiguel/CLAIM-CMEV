"""Surveyor name and claim/vehicle metadata extraction from document text boxes.

Pure, deterministic extraction of the field surveyor, vehicle make/model, registration year,
registration plate, workshop/repairer, date of loss, and policy number from OCR text boxes
located on uploaded estimate or survey report pages.
"""
from __future__ import annotations

from datetime import datetime
import re
from collections.abc import Mapping, Sequence
from typing import Any

_SINGLE_PATTERN = re.compile(
    r"^\s*(?:field\s+surveyor|surveyor|loss\s+adjuster|inspected\s+by|assessor)\s*[:\-]\s+([A-Za-z][A-Za-z0-9\.\s\-]{1,35})$",
    re.IGNORECASE,
)
_LABEL_PATTERN = re.compile(
    r"^\s*(?:field\s+surveyor|surveyor|loss\s+adjuster|inspected\s+by|assessor)\s*[:\-]?\s*$",
    re.IGNORECASE,
)
_STOP_WORDS = frozenset(
    {
        "report",
        "assessment",
        "details",
        "name",
        "date",
        "type",
        "claim",
        "vehicle",
        "none",
        "not applicable",
        "n/a",
        "na",
        "nil",
        "unspecified",
        "unknown",
    }
)

_FIELD_PATTERNS: dict[str, tuple[re.Pattern[str], re.Pattern[str]]] = {
    "surveyor": (
        _SINGLE_PATTERN,
        _LABEL_PATTERN,
    ),
    "plate": (
        re.compile(
            r"^\s*(?:vehicle\s+number|vehicle\s+no\.?|registration\s+plate|registration\s+number|registration\s+no\.?|reg\.?\s+no\.?|car\s+plate|plate\s+number|plate\s+no\.?|license\s+plate|vehicle\s+reg\.?)\s*[:\-]\s*(.+)$",
            re.IGNORECASE,
        ),
        re.compile(
            r"^\s*(?:vehicle\s+number|vehicle\s+no\.?|registration\s+plate|registration\s+number|registration\s+no\.?|reg\.?\s+no\.?|car\s+plate|plate\s+number|plate\s+no\.?|license\s+plate|vehicle\s+reg\.?)\s*[:\-]?\s*$",
            re.IGNORECASE,
        ),
    ),
    "vehicle_make": (
        re.compile(
            r"^\s*(?:vehicle\s+make|make)\s*[:\-]\s*(.+)$",
            re.IGNORECASE,
        ),
        re.compile(
            r"^\s*(?:vehicle\s+make|make)\s*[:\-]?\s*$",
            re.IGNORECASE,
        ),
    ),
    "vehicle_model": (
        re.compile(
            r"^\s*(?:vehicle\s+model|model)\s*[:\-]\s*(.+)$",
            re.IGNORECASE,
        ),
        re.compile(
            r"^\s*(?:vehicle\s+model|model)\s*[:\-]?\s*$",
            re.IGNORECASE,
        ),
    ),
    "make_and_model": (
        re.compile(
            r"^\s*(?:vehicle\s+)?make\s*(?:and|&|/)\s*model\s*[:\-]\s*(.+)$",
            re.IGNORECASE,
        ),
        re.compile(
            r"^\s*(?:vehicle\s+)?make\s*(?:and|&|/)\s*model\s*[:\-]?\s*$",
            re.IGNORECASE,
        ),
    ),
    "vehicle_year": (
        re.compile(
            r"^\s*(?:vehicle\s+year|year\s+of\s+registration|registration\s+year|year\s+of\s+manufacture|manufacture\s+year|reg\.?\s+year|mfg\.?\s+year|year)\s*[:\-]\s*(.+)$",
            re.IGNORECASE,
        ),
        re.compile(
            r"^\s*(?:vehicle\s+year|year\s+of\s+registration|registration\s+year|year\s+of\s+manufacture|manufacture\s+year|reg\.?\s+year|mfg\.?\s+year|year)\s*[:\-]?\s*$",
            re.IGNORECASE,
        ),
    ),
    "registration_date": (
        re.compile(
            r"^\s*(?:registration\s+date|date\s+of\s+registration|reg\.?\s+date|first\s+registration\s+date|original\s+registration\s+date)\s*[:\-]\s*(.+)$",
            re.IGNORECASE,
        ),
        re.compile(
            r"^\s*(?:registration\s+date|date\s+of\s+registration|reg\.?\s+date|first\s+registration\s+date|original\s+registration\s+date)\s*[:\-]?\s*$",
            re.IGNORECASE,
        ),
    ),
    "workshop": (
        re.compile(
            r"^\s*(?:repairer(?:\s+name)?|repair\s+workshop|repair\s+shop|workshop(?:\s+name)?|appointed\s+workshop|garage)\s*[:\-]\s*(.+)$",
            re.IGNORECASE,
        ),
        re.compile(
            r"^\s*(?:repairer(?:\s+name)?|repair\s+workshop|repair\s+shop|workshop(?:\s+name)?|appointed\s+workshop|garage)\s*[:\-]?\s*$",
            re.IGNORECASE,
        ),
    ),
    "loss_date": (
        re.compile(
            r"^\s*(?:accident\s+date|date\s+of\s+accident|date\s+of\s+loss|loss\s+date|incident\s+date|date\s+of\s+incident)\s*[:\-]\s*(.+)$",
            re.IGNORECASE,
        ),
        re.compile(
            r"^\s*(?:accident\s+date|date\s+of\s+accident|date\s+of\s+loss|loss\s+date|incident\s+date|date\s+of\s+incident)\s*[:\-]?\s*$",
            re.IGNORECASE,
        ),
    ),
    "policy_number": (
        re.compile(
            r"^\s*(?:policy\s+number|policy\s+no\.?|policy\s*#)\s*[:\-]\s*(.+)$",
            re.IGNORECASE,
        ),
        re.compile(
            r"^\s*(?:policy\s+number|policy\s+no\.?|policy\s*#)\s*[:\-]?\s*$",
            re.IGNORECASE,
        ),
    ),
}

_KNOWN_OTHER_LABELS = re.compile(
    r"^\s*(?:odometer(?:\s*km)?|colour|color|chassis(?:\s+or\s+vin)?|vin|engine(?:\s+or\s+motor)?(?:\s+number)?|"
    r"capacity|general\s+condition|inspection\s+type|steering|brakes|tyre\s+specification|tyre\s+tread|"
    r"repair\s+estimate|excess|policy\s+excess|repair\s+work\s+days|gst|claim\s+number|claim\s+type|"
    r"assignment\s+date|survey\s+date|vehicle\s+details|claim\s+details|condition\s+and\s+damage|"
    r"valuation\s+and\s+settlement|vehicle\s+and\s+estimate\s+details|parts\s+and\s+labour\s+list)\s*[:\-]?\s*$",
    re.IGNORECASE,
)

_MULTI_WORD_MAKES = (
    "mercedes-benz",
    "mercedes benz",
    "land rover",
    "alfa romeo",
    "aston martin",
    "rolls-royce",
    "rolls royce",
    "great wall",
)

_KNOWN_MAKES = frozenset(
    {
        "subaru",
        "toyota",
        "honda",
        "nissan",
        "mazda",
        "hyundai",
        "kia",
        "mitsubishi",
        "suzuki",
        "daihatsu",
        "lexus",
        "infiniti",
        "acura",
        "isuzu",
        "hino",
        "volkswagen",
        "vw",
        "mercedes-benz",
        "mercedes benz",
        "mercedes",
        "bmw",
        "audi",
        "porsche",
        "volvo",
        "peugeot",
        "renault",
        "citroen",
        "skoda",
        "seat",
        "opel",
        "fiat",
        "alfa romeo",
        "jaguar",
        "land rover",
        "mini",
        "ford",
        "chevrolet",
        "jeep",
        "tesla",
        "byd",
        "mg",
        "geely",
        "chery",
        "ora",
        "zeekr",
        "xpeng",
        "nio",
        "polestar",
        "proton",
        "perodua",
    }
)

_MODEL_TO_MAKE: dict[str, str] = {
    "forester": "Subaru",
    "outback": "Subaru",
    "impreza": "Subaru",
    "crosstrek": "Subaru",
    "xv": "Subaru",
    "wrx": "Subaru",
    "legacy": "Subaru",
    "levorg": "Subaru",
    "brz": "Subaru",
    "corolla": "Toyota",
    "altis": "Toyota",
    "camry": "Toyota",
    "vios": "Toyota",
    "yaris": "Toyota",
    "rav4": "Toyota",
    "harrier": "Toyota",
    "fortuner": "Toyota",
    "sienta": "Toyota",
    "prius": "Toyota",
    "aqua": "Toyota",
    "wish": "Toyota",
    "alphard": "Toyota",
    "vellfire": "Toyota",
    "noah": "Toyota",
    "voxy": "Toyota",
    "hiace": "Toyota",
    "chr": "Toyota",
    "c-hr": "Toyota",
    "civic": "Honda",
    "accord": "Honda",
    "city": "Honda",
    "jazz": "Honda",
    "fit": "Honda",
    "vezel": "Honda",
    "hr-v": "Honda",
    "hrv": "Honda",
    "cr-v": "Honda",
    "crv": "Honda",
    "odyssey": "Honda",
    "freed": "Honda",
    "shuttle": "Honda",
    "stepwgn": "Honda",
    "sylphy": "Nissan",
    "qashqai": "Nissan",
    "x-trail": "Nissan",
    "xtrail": "Nissan",
    "kicks": "Nissan",
    "note": "Nissan",
    "almera": "Nissan",
    "teana": "Nissan",
    "serena": "Nissan",
    "nv200": "Nissan",
    "nv350": "Nissan",
    "cx-3": "Mazda",
    "cx-5": "Mazda",
    "cx-8": "Mazda",
    "cx-9": "Mazda",
    "cx-30": "Mazda",
    "mazda2": "Mazda",
    "mazda 2": "Mazda",
    "mazda3": "Mazda",
    "mazda 3": "Mazda",
    "mazda6": "Mazda",
    "mazda 6": "Mazda",
    "avante": "Hyundai",
    "elantra": "Hyundai",
    "sonata": "Hyundai",
    "tucson": "Hyundai",
    "santa fe": "Hyundai",
    "kona": "Hyundai",
    "ioniq": "Hyundai",
    "staria": "Hyundai",
    "cerato": "Kia",
    "k3": "Kia",
    "k5": "Kia",
    "optima": "Kia",
    "sportage": "Kia",
    "sorento": "Kia",
    "niro": "Kia",
    "seltos": "Kia",
    "picanto": "Kia",
    "lancer": "Mitsubishi",
    "attrage": "Mitsubishi",
    "outlander": "Mitsubishi",
    "eclipse cross": "Mitsubishi",
    "swift": "Suzuki",
    "golf": "Volkswagen",
    "polo": "Volkswagen",
    "jetta": "Volkswagen",
    "passat": "Volkswagen",
    "tiguan": "Volkswagen",
    "touareg": "Volkswagen",
    "scirocco": "Volkswagen",
    "caddy": "Volkswagen",
    "transporter": "Volkswagen",
    "c180": "Mercedes-Benz",
    "c200": "Mercedes-Benz",
    "e200": "Mercedes-Benz",
    "e250": "Mercedes-Benz",
    "s350": "Mercedes-Benz",
    "a180": "Mercedes-Benz",
    "a200": "Mercedes-Benz",
    "gla": "Mercedes-Benz",
    "glb": "Mercedes-Benz",
    "glc": "Mercedes-Benz",
    "gle": "Mercedes-Benz",
    "vito": "Mercedes-Benz",
    "sprinter": "Mercedes-Benz",
    "318i": "BMW",
    "320i": "BMW",
    "330i": "BMW",
    "520i": "BMW",
    "530i": "BMW",
    "116i": "BMW",
    "118i": "BMW",
    "x1": "BMW",
    "x3": "BMW",
    "x5": "BMW",
    "a3": "Audi",
    "a4": "Audi",
    "a6": "Audi",
    "q3": "Audi",
    "q5": "Audi",
    "q7": "Audi",
    "macan": "Porsche",
    "cayenne": "Porsche",
    "xc40": "Volvo",
    "xc60": "Volvo",
    "xc90": "Volvo",
    "atto 3": "BYD",
    "dolphin": "BYD",
    "seal": "BYD",
    "model 3": "Tesla",
    "model y": "Tesla",
}


def infer_make_from_model(model: str | None) -> str | None:
    """Return canonical vehicle make if ``model`` uniquely identifies a known manufacturer."""
    if not model:
        return None
    low = model.strip().lower()
    if low in _MODEL_TO_MAKE:
        return _MODEL_TO_MAKE[low]
    for kw, canonical_make in _MODEL_TO_MAKE.items():
        if re.search(r"\b" + re.escape(kw) + r"\b", low):
            return canonical_make
    return None

_SUV_KEYWORDS = (
    "forester",
    "outback",
    "crosstrek",
    "xv",
    "rav4",
    "harrier",
    "fortuner",
    "chr",
    "c-hr",
    "corolla cross",
    "yariscross",
    "yaris cross",
    "cr-v",
    "crv",
    "hr-v",
    "hrv",
    "vezel",
    "cx-3",
    "cx-5",
    "cx-8",
    "cx-9",
    "cx-30",
    "qashqai",
    "x-trail",
    "xtrail",
    "kicks",
    "tucson",
    "santa fe",
    "kona",
    "sportage",
    "sorento",
    "niro",
    "seltos",
    "outlander",
    "eclipse cross",
    "tiguan",
    "touareg",
    "x1",
    "x3",
    "x5",
    "q3",
    "q5",
    "q7",
    "gla",
    "glb",
    "glc",
    "gle",
    "xc40",
    "xc60",
    "xc90",
    "macan",
    "cayenne",
    "defender",
    "discovery",
    "range rover",
    "atto 3",
    "model y",
    "suv",
)
_SEDAN_KEYWORDS = (
    "corolla",
    "altis",
    "camry",
    "vios",
    "crown",
    "civic",
    "accord",
    "city",
    "mazda 3",
    "mazda3",
    "mazda 6",
    "mazda6",
    "sylphy",
    "teana",
    "almera",
    "impreza",
    "wrx",
    "legacy",
    "avante",
    "elantra",
    "sonata",
    "ioniq 6",
    "cerato",
    "k3",
    "optima",
    "k5",
    "lancer",
    "attrage",
    "jetta",
    "passat",
    "c180",
    "c200",
    "e200",
    "e250",
    "s350",
    "318i",
    "320i",
    "330i",
    "520i",
    "530i",
    "a3",
    "a4",
    "a6",
    "model 3",
    "seal",
    "sedan",
)
_HATCHBACK_KEYWORDS = (
    "yaris",
    "prius c",
    "aqua",
    "sienta",
    "jazz",
    "fit",
    "swift",
    "baleno",
    "ignis",
    "mazda 2",
    "mazda2",
    "note",
    "march",
    "picanto",
    "rio",
    "i10",
    "i20",
    "i30",
    "golf",
    "polo",
    "scirocco",
    "a180",
    "a200",
    "116i",
    "118i",
    "cooper",
    "mini",
    "dolphin",
    "hatchback",
)
_VAN_KEYWORDS = (
    "hiace",
    "townace",
    "liteace",
    "proace",
    "nv200",
    "nv350",
    "urvan",
    "cabstar",
    "kangoo",
    "trafic",
    "berlingo",
    "dispatch",
    "partner",
    "expert",
    "caddy",
    "transporter",
    "vito",
    "sprinter",
    "n-series",
    "canter",
    "dyna",
    "h1",
    "staria",
    "van",
)


def infer_vehicle_class(make: str | None, model: str | None) -> str | None:
    """Infer canonical vehicle_class from make/model when unambiguous."""
    combined = f"{make or ''} {model or ''}".strip().lower()
    if not combined:
        return None
    for kw in _SUV_KEYWORDS:
        if re.search(r"\b" + re.escape(kw) + r"\b", combined):
            return "suv_crossover"
    for kw in _VAN_KEYWORDS:
        if re.search(r"\b" + re.escape(kw) + r"\b", combined):
            return "van_commercial"
    for kw in _HATCHBACK_KEYWORDS:
        if re.search(r"\b" + re.escape(kw) + r"\b", combined):
            return "hatchback_small"
    for kw in _SEDAN_KEYWORDS:
        if re.search(r"\b" + re.escape(kw) + r"\b", combined):
            return "sedan_standard"
    return None


def _normalise_boxes(boxes: Sequence[Mapping[str, Any] | Any]) -> list[dict[str, Any]]:
    norm_boxes: list[dict[str, Any]] = []
    for b in boxes:
        if isinstance(b, Mapping):
            text = str(b.get("text", "")).strip()
            coords = b.get("box_norm") or [0.0, 0.0, 0.0, 0.0]
        else:
            text = str(getattr(b, "text", "")).strip()
            coords = getattr(b, "box_norm", None) or [0.0, 0.0, 0.0, 0.0]
        if text and len(coords) == 4:
            norm_boxes.append({"text": text, "box_norm": [float(c) for c in coords]})
    return norm_boxes


def _is_label_or_stopword(text: str) -> bool:
    clean = text.strip()
    if not clean or len(clean) < 2 or clean.lower() in _STOP_WORDS:
        return True
    if _KNOWN_OTHER_LABELS.match(clean):
        return True
    for _, (_, label_pat) in _FIELD_PATTERNS.items():
        if label_pat.match(clean):
            return True
    return False


def _extract_field_raw(norm_boxes: Sequence[dict[str, Any]], field_key: str) -> str | None:
    single_pat, label_pat = _FIELD_PATTERNS[field_key]

    # 1. Single box with label + value
    for b in norm_boxes:
        m = single_pat.match(b["text"])
        if m:
            val = m.group(1).strip()
            if not _is_label_or_stopword(val):
                return val

    # 2. Separate label box and value box
    for b in norm_boxes:
        if label_pat.match(b["text"]):
            bx0, by0, bx1, by1 = b["box_norm"]
            b_cy = (by0 + by1) / 2.0
            b_h = max(0.005, by1 - by0)

            horiz_candidates: list[tuple[float, float, str]] = []
            vert_candidates: list[tuple[float, str]] = []

            for other in norm_boxes:
                if other is b:
                    continue
                otext = other["text"]
                if _is_label_or_stopword(otext):
                    continue
                ox0, oy0, ox1, oy1 = other["box_norm"]
                o_cy = (oy0 + oy1) / 2.0
                o_h = max(0.005, oy1 - oy0)
                v_overlap = min(by1, oy1) - max(by0, oy0)

                # Same horizontal row: must vertically overlap and sit to the right within the same column pair
                if (
                    v_overlap >= 0.0
                    and abs(o_cy - b_cy) <= max(b_h, o_h) * 0.65
                    and ox0 >= bx0 + 0.005
                    and (ox0 - bx1) < 0.30
                ):
                    dist = ox0 - bx1
                    horiz_candidates.append((dist, abs(o_cy - b_cy), otext))

                # Vertically stacked directly below (within 0.04 vertical fraction, substantially overlapping x)
                h_overlap = min(bx1, ox1) - max(bx0, ox0)
                min_w = max(0.01, min(bx1 - bx0, ox1 - ox0))
                if 0 <= oy0 - by1 < 0.04 and h_overlap >= 0.4 * min_w:
                    vert_candidates.append((oy0 - by1, otext))

            if horiz_candidates:
                horiz_candidates.sort(key=lambda c: (c[0], c[1]))
                for _, _, val in horiz_candidates:
                    if not _is_label_or_stopword(val):
                        return val

            if vert_candidates:
                vert_candidates.sort(key=lambda c: c[0])
                for _, val in vert_candidates:
                    if not _is_label_or_stopword(val):
                        return val

    return None


def _split_make_and_model(text: str) -> tuple[str | None, str | None]:
    clean = " ".join(text.strip().split())
    if not clean:
        return None, None
    lower = clean.lower()
    for mw in _MULTI_WORD_MAKES:
        if lower.startswith(mw + " "):
            make = clean[: len(mw)].strip()
            model = clean[len(mw) :].strip()
            return (make or None), (model or None)
        if lower == mw:
            return clean, None
    parts = clean.split(" ", 1)
    if len(parts) == 1:
        return parts[0], None
    return parts[0].strip() or None, parts[1].strip() or None


def _parse_year(raw: str | None) -> int | None:
    if not raw:
        return None
    m = re.search(r"\b(19[5-9]\d|20\d\d)\b(?!\s*cc\b)", raw, re.IGNORECASE)
    if m:
        year = int(m.group(1))
        if 1950 <= year <= 2100:
            return year
    return None


def _parse_iso_date(raw: str | None) -> str | None:
    if not raw:
        return None
    clean = raw.strip()
    # Try ISO YYYY-MM-DD or YYYY/MM/DD
    m_iso = re.search(r"\b(\d{4})[-/\.](\d{1,2})[-/\.](\d{1,2})\b", clean)
    if m_iso:
        try:
            dt = datetime(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass
    # Try DD/MM/YYYY or DD-MM-YYYY
    m_dmy = re.search(r"\b(\d{1,2})[-/\.](\d{1,2})[-/\.](\d{4})\b", clean)
    if m_dmy:
        try:
            dt = datetime(int(m_dmy.group(3)), int(m_dmy.group(2)), int(m_dmy.group(1)))
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass
    # Try DD MMM YYYY / DD Month YYYY
    for fmt in ("%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(clean, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def extract_surveyor_name(boxes: Sequence[Mapping[str, Any] | Any]) -> str | None:
    """Extract surveyor name from a list of OCR text box dictionaries or records.

    Each box is expected to provide text (str) and normalised coordinates box_norm [x0, y0, x1, y1].
    """
    if not boxes:
        return None
    norm_boxes = _normalise_boxes(boxes)
    return _extract_field_raw(norm_boxes, "surveyor")


def extract_surveyor_from_pages(pages: Sequence[Any]) -> str | None:
    """Extract surveyor name from a list of DocumentPage records or dicts."""
    if not pages:
        return None
    for p in pages:
        boxes = getattr(p, "text_boxes", None) if not isinstance(p, Mapping) else p.get("text_boxes")
        if boxes:
            name = extract_surveyor_name(boxes)
            if name:
                return name
    return None


def extract_report_metadata(boxes: Sequence[Mapping[str, Any] | Any]) -> dict[str, Any]:
    """Extract claim and vehicle metadata fields from OCR text boxes on a report page.

    Returns a dictionary of extracted non-empty values among:
      - ``surveyor`` (str)
      - ``vehicle_make`` (str)
      - ``vehicle_model`` (str)
      - ``vehicle_year`` (int)
      - ``plate`` (str)
      - ``workshop`` (str)
      - ``loss_date`` (str, YYYY-MM-DD)
      - ``policy_number`` (str)
    """
    if not boxes:
        return {}
    norm_boxes = _normalise_boxes(boxes)
    if not norm_boxes:
        return {}

    out: dict[str, Any] = {}

    surveyor = _extract_field_raw(norm_boxes, "surveyor")
    if surveyor:
        out["surveyor"] = surveyor

    make = _extract_field_raw(norm_boxes, "vehicle_make")
    if make and not re.search(r"[A-Za-z]{2,}", make):
        make = None
    model = _extract_field_raw(norm_boxes, "vehicle_model")
    if not make or not model:
        combined = _extract_field_raw(norm_boxes, "make_and_model")
        if combined:
            c_make, c_model = _split_make_and_model(combined)
            if not make and c_make:
                make = c_make
            if not model and c_model:
                model = c_model
    if model and (not make or make.strip().lower() not in _KNOWN_MAKES):
        inferred_make = infer_make_from_model(model)
        if inferred_make:
            make = inferred_make
    if make:
        out["vehicle_make"] = make
    if model:
        out["vehicle_model"] = model

    year = _parse_year(_extract_field_raw(norm_boxes, "vehicle_year"))
    if year is None:
        year = _parse_year(_extract_field_raw(norm_boxes, "registration_date"))
    if year is not None:
        out["vehicle_year"] = year

    plate = _extract_field_raw(norm_boxes, "plate")
    if plate:
        out["plate"] = plate

    workshop = _extract_field_raw(norm_boxes, "workshop")
    if workshop and re.search(r"[A-Za-z]{2,}", workshop):
        out["workshop"] = workshop

    loss_date = _parse_iso_date(_extract_field_raw(norm_boxes, "loss_date"))
    if loss_date:
        out["loss_date"] = loss_date

    policy_number = _extract_field_raw(norm_boxes, "policy_number")
    if policy_number:
        out["policy_number"] = policy_number

    return out


def extract_report_metadata_from_pages(
    pages: Sequence[Any],
    text_boxes: Sequence[Mapping[str, Any] | Any] | None = None,
) -> dict[str, Any]:
    """Extract and merge report metadata across a sequence of DocumentPage/page_reading records or text_boxes."""
    merged: dict[str, Any] = {}
    if pages:
        for p in pages:
            if isinstance(p, Mapping):
                boxes = p.get("text_boxes") or p.get("boxes")
            else:
                boxes = getattr(p, "text_boxes", None) or getattr(p, "boxes", None)
            if not boxes:
                continue
            page_meta = extract_report_metadata(boxes)
            for k, v in page_meta.items():
                if k not in merged and v not in (None, ""):
                    merged[k] = v
    if text_boxes:
        box_meta = extract_report_metadata(text_boxes)
        for k, v in box_meta.items():
            if k not in merged and v not in (None, ""):
                merged[k] = v
    return merged


def claim_needs_metadata_extraction(claim: Mapping[str, Any]) -> bool:
    """Return True if any auto-extractable field on ``claim`` is blank/unset and not manually locked."""
    manual = set(claim.get("manually_set_fields") or [])
    if claim.get("surveyor_manually_set"):
        manual.add("surveyor")
    if "surveyor" not in manual and claim.get("surveyor") in (None, "", "Acery AI"):
        return True
    vehicle = claim.get("vehicle") or {}
    cur_make = str(vehicle.get("make") or "")
    cur_model = str(vehicle.get("model") or "")
    if "vehicle_make" not in manual and (
        cur_make in ("", "unknown", "Pending")
        or not re.search(r"[A-Za-z]{2,}", cur_make)
        or (cur_make.strip().lower() not in _KNOWN_MAKES and infer_make_from_model(cur_model) is not None)
    ):
        return True
    if "vehicle_model" not in manual and vehicle.get("model") in (None, "", "unknown", "Pending"):
        return True
    if "vehicle_year" not in manual and vehicle.get("year") in (None, 0, ""):
        return True
    if "plate" not in manual and not vehicle.get("plate"):
        return True
    cur_workshop = str(claim.get("workshop") or "")
    if "workshop" not in manual and (not cur_workshop or not re.search(r"[A-Za-z]{2,}", cur_workshop)):
        return True
    if "loss_date" not in manual and not claim.get("loss_date"):
        return True
    return False



def apply_extracted_metadata_to_claim(claim: dict[str, Any], extracted: Mapping[str, Any]) -> bool:
    """Populate blank/unset claim fields from extracted report metadata without overwriting manual edits.

    Returns True if any field on ``claim`` was updated.
    """
    if not extracted:
        return False
    changed = False
    manual = set(claim.get("manually_set_fields") or [])
    if claim.get("surveyor_manually_set"):
        manual.add("surveyor")

    if extracted.get("surveyor") and "surveyor" not in manual:
        if claim.get("surveyor") in (None, "", "Acery AI"):
            claim["surveyor"] = extracted["surveyor"]
            changed = True

    vehicle = dict(claim.get("vehicle") or {})
    vehicle_changed = False

    if extracted.get("vehicle_model") and "vehicle_model" not in manual:
        if vehicle.get("model") in (None, "", "unknown", "Pending"):
            vehicle["model"] = extracted["vehicle_model"]
            vehicle_changed = True

    if extracted.get("vehicle_make") and "vehicle_make" not in manual:
        cur_make = str(vehicle.get("make") or "")
        if (
            cur_make in ("", "unknown", "Pending")
            or not re.search(r"[A-Za-z]{2,}", cur_make)
            or cur_make.strip().lower() not in _KNOWN_MAKES
        ):
            vehicle["make"] = extracted["vehicle_make"]
            vehicle_changed = True

    if extracted.get("vehicle_year") and "vehicle_year" not in manual:
        if vehicle.get("year") in (None, 0, ""):
            vehicle["year"] = int(extracted["vehicle_year"])
            vehicle_changed = True

    if extracted.get("plate") and "plate" not in manual:
        if not vehicle.get("plate"):
            vehicle["plate"] = extracted["plate"]
            vehicle_changed = True

    if "vehicle_class" not in manual and vehicle.get("vehicle_class") in (None, "", "unknown"):
        inferred_class = infer_vehicle_class(vehicle.get("make"), vehicle.get("model"))
        if inferred_class:
            vehicle["vehicle_class"] = inferred_class
            vehicle_changed = True

    if vehicle_changed:
        claim["vehicle"] = vehicle
        changed = True

    if extracted.get("workshop") and "workshop" not in manual:
        cur_workshop = str(claim.get("workshop") or "")
        if not cur_workshop or not re.search(r"[A-Za-z]{2,}", cur_workshop):
            claim["workshop"] = extracted["workshop"]
            changed = True

    if extracted.get("loss_date") and "loss_date" not in manual:
        if not claim.get("loss_date"):
            claim["loss_date"] = extracted["loss_date"]
            changed = True

    if extracted.get("policy_number") and "policy_number" not in manual:
        if not claim.get("policy_number"):
            claim["policy_number"] = extracted["policy_number"]
            changed = True

    return changed
