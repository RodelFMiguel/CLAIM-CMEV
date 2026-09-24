"""M9 server-rendered overlays: boxes land on the expected pixels in every frame.

Module 09 "Evidence panel"; UI specification sections 6.12 and 10.6; data contracts
section 4 (boxes normalised on the corrected render; photo boxes on the EXIF-oriented
original; masks mapped from the model frame without inventing values).
"""
from io import BytesIO

from PIL import Image
import pytest

from claim_cmev.contracts import ImageDamageObservation, ImageTransform, PageTransform
from claim_cmev.contracts.common import ContractError
from claim_cmev.review import (
    box_to_pixels,
    default_review_config,
    render_mark_crop,
    render_page_highlight,
    render_photo_overlay,
)
from m9_support import line_item, mark, scope

COLORS = default_review_config().overlay.colors
ALPHA = default_review_config().overlay.selected_fill_alpha
WHITE = (255, 255, 255)


def png(width, height, fmt="PNG", **save):
    buffer = BytesIO()
    Image.new("RGB", (width, height), WHITE).save(buffer, format=fmt, **save)
    return buffer.getvalue()


def decode(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return Image.open(BytesIO(data)).convert("RGB")


def blended(color):
    return tuple(round((c * ALPHA + 255 * (255 - ALPHA)) / 255) for c in color)


def near(actual, expected, tolerance=2):
    return all(abs(a - e) <= tolerance for a, e in zip(actual, expected))


def observation(observation_id, photo_id, bbox):
    return ImageDamageObservation.model_validate({
        **scope(), "versions": {"taxonomy": "damage-cardd-1.0.0", "damage_model": "d-1"},
        "observation_id": observation_id, "photo_id": photo_id, "damage_code": "dent", "damage_confidence": 0.83,
        "assignment_status": "assigned", "part_code": "front-bumper",
        "candidates": [{"part_code": "front-bumper", "containment": 0.91, "rank": 1}],
        "primary_containment": 0.91, "runner_up_containment": None, "background_containment": 0.03,
        "area_pixels": 5412, "area_fraction": 0.0206, "area_denominator_pixels": 262144,
        "damage_mask_ref": {"artifact_id": "dm1", "object_uri": "s3://t/dm1.png", "sha256": "a" * 64, "width": 512,
                            "height": 512, "encoding": "binary_png", "source_photo_id": photo_id},
        "part_mask_ref": {"artifact_id": "pm1", "object_uri": "s3://t/pm1.png", "sha256": "b" * 64, "width": 512,
                          "height": 512, "encoding": "class_index_png", "source_photo_id": photo_id},
        "bbox_norm": bbox, "assignment_config_version": "assign-1.0.0"})


def test_box_to_pixels_uses_pixel_edges():
    assert box_to_pixels((0.1, 0.2, 0.5, 0.4), 1000, 500) == (100, 100, 499, 199)
    assert box_to_pixels((0.0, 0.0, 1.0, 1.0), 10, 10) == (0, 0, 9, 9)


def test_row_and_mark_boxes_land_on_the_corrected_render():
    rows = [line_item("li1", 0, "front-bumper", "replace", "980.00", row_box_norm=(0.1, 0.2, 0.5, 0.4)),
            line_item("li2", 1, "hood", "repair", "340.00", row_box_norm=(0.6, 0.2, 0.9, 0.4))]
    marks = [mark("pm1", "li2", "exclusion", box_norm=(0.62, 0.25, 0.7, 0.3)),
             mark("pm9", "li1", "price_change", page_id="dp2", box_norm=(0.1, 0.6, 0.2, 0.7))]
    img = decode(render_page_highlight(png(1000, 500), page_id="dp1", line_items=rows, marks=marks,
                                       selected_entry_id="li2"))
    assert img.size == (1000, 500)
    assert img.getpixel((100, 150)) == COLORS.row and img.getpixel((499, 150)) == COLORS.row
    assert img.getpixel((98, 150)) == WHITE and img.getpixel((300, 150)) == WHITE, "unselected rows are outlined"
    assert img.getpixel((600, 150)) == COLORS.row_selected and img.getpixel((899, 150)) == COLORS.row_selected
    assert near(img.getpixel((750, 180)), blended(COLORS.row_selected)), "the selected row is filled"
    assert img.getpixel((620, 137)) == COLORS.mark_exclusion
    assert img.getpixel((150, 325)) == WHITE, "a mark on another page is not drawn"


def test_selected_mark_is_highlighted_and_marks_can_be_hidden():
    rows = [line_item("li1", 0, "front-bumper", "replace", "980.00", row_box_norm=(0.1, 0.2, 0.5, 0.4))]
    marks = [mark("pm1", "li1", "price_change", box_norm=(0.3, 0.5, 0.4, 0.6))]
    img = decode(render_page_highlight(png(1000, 500), page_id="dp1", line_items=rows, marks=marks,
                                       selected_mark_id="pm1"))
    assert img.getpixel((300, 275)) == COLORS.mark_selected
    assert near(img.getpixel((350, 275)), blended(COLORS.mark_selected))
    hidden = decode(render_page_highlight(png(1000, 500), page_id="dp1", line_items=rows, marks=marks,
                                          selected_mark_id="pm1", show_marks=False))
    assert hidden.getpixel((300, 275)) == WHITE and hidden.getpixel((100, 150)) == COLORS.row


@pytest.mark.parametrize("size, homography, inverse, expected", [
    # corrected = source / 2: box (0.1, 0.2, 0.5, 0.4) on 500x300 -> source (100, 120)-(500, 240)
    ((1000, 600, 500, 300), ((0.5, 0, 0), (0, 0.5, 0), (0, 0, 1)), ((2, 0, 0), (0, 2, 0), (0, 0, 1)),
     (100, 120, 500, 240)),
    # corrected = source shifted by (-100, -50): box on 600x500 -> source (160, 150)-(400, 250)
    ((800, 600, 600, 500), ((1, 0, -100), (0, 1, -50), (0, 0, 1)), ((1, 0, 100), (0, 1, 50), (0, 0, 1)),
     (160, 150, 400, 250)),
])
def test_boxes_map_through_the_page_transform_onto_the_uploaded_page(size, homography, inverse, expected):
    sw, sh, cw, ch = size
    transform = PageTransform(source_width=sw, source_height=sh, corrected_width=cw, corrected_height=ch,
                              geometry_correction="perspective", correction_reason="page_boundary_found",
                              homography=homography, homography_inverse=inverse)
    rows = [line_item("li1", 0, "front-bumper", "replace", "980.00", row_box_norm=(0.1, 0.2, 0.5, 0.4))]
    img = decode(render_page_highlight(png(sw, sh), page_id="dp1", line_items=rows, transform=transform,
                                       frame="source"))
    x0, y0, x1, y1 = expected
    mid_y, mid_x = (y0 + y1) // 2, (x0 + x1) // 2
    assert img.getpixel((x0 + 1, mid_y)) == COLORS.row and img.getpixel((x1 - 1, mid_y)) == COLORS.row
    assert img.getpixel((mid_x, y0 + 1)) == COLORS.row and img.getpixel((mid_x, y1 - 1)) == COLORS.row
    assert img.getpixel((x0 - 4, mid_y)) == WHITE and img.getpixel((mid_x, mid_y)) == WHITE


def test_page_frame_mismatches_are_refused():
    transform = PageTransform(source_width=1000, source_height=600, corrected_width=500, corrected_height=300,
                              geometry_correction="none", correction_reason="no_correction_needed")
    with pytest.raises(ContractError) as exc:
        render_page_highlight(png(1000, 600), page_id="dp1", transform=transform)
    assert exc.value.reason_code == "overlay_frame_mismatch"
    with pytest.raises(ContractError) as exc:
        render_page_highlight(png(500, 300), page_id="dp1", frame="source")
    assert exc.value.reason_code == "transform_required"


def test_damage_boxes_land_on_the_photo_and_the_selection_is_filled():
    observations = [observation("ob1", "ph1", (0.25, 0.5, 0.75, 1.0)), observation("ob2", "ph1", (0.0, 0.0, 0.2, 0.3)),
                    observation("ob3", "ph2", (0.8, 0.0, 1.0, 0.2))]
    img = decode(render_photo_overlay(png(200, 100), photo_id="ph1", observations=observations,
                                      selected_observation_id="ob1"))
    assert img.getpixel((50, 75)) == COLORS.damage_selected and img.getpixel((149, 75)) == COLORS.damage_selected
    assert img.getpixel((48, 75)) == WHITE
    assert near(img.getpixel((100, 80)), blended(COLORS.damage_selected))
    assert img.getpixel((0, 15)) == COLORS.damage and img.getpixel((20, 15)) == WHITE
    assert img.getpixel((180, 10)) == WHITE, "an observation of another photo is not drawn"


def test_photo_boxes_use_the_exif_oriented_original_frame():
    stored = Image.new("RGB", (100, 200), WHITE)
    exif = stored.getexif()
    exif[0x0112] = 6  # displayed rotated 90 degrees clockwise: 200 x 100
    buffer = BytesIO()
    stored.save(buffer, format="JPEG", exif=exif, quality=95)
    transform = ImageTransform(stored_width=100, stored_height=200, exif_orientation=6, model_width=512,
                               model_height=512, scale=2.56, pad_left=0, pad_top=128)
    img = decode(render_photo_overlay(buffer.getvalue(), photo_id="ph1", transform=transform,
                                      observations=[observation("ob1", "ph1", (0.25, 0.5, 0.75, 1.0))]))
    assert img.size == (200, 100)
    assert img.getpixel((50, 75)) == COLORS.damage and img.getpixel((149, 75)) == COLORS.damage


def test_mask_outline_maps_from_the_letterboxed_model_frame():
    transform = ImageTransform(stored_width=200, stored_height=100, model_width=100, model_height=100, scale=0.5,
                               pad_left=0, pad_top=25)
    mask = Image.new("L", (100, 100), 0)
    mask.paste(255, (20, 35, 40, 55))  # model frame x 20..39, y 35..54 -> photo x 40..79, y 20..59
    img = decode(render_photo_overlay(png(200, 100), photo_id="ph1", transform=transform,
                                      observations=[observation("ob1", "ph1", (0.1, 0.1, 0.9, 0.9))],
                                      damage_masks={"ob1": mask}))
    outline = COLORS.mask_outline
    assert img.getpixel((40, 40)) == outline and img.getpixel((79, 40)) == outline
    assert img.getpixel((60, 20)) == outline and img.getpixel((60, 59)) == outline
    assert img.getpixel((39, 40)) == WHITE and img.getpixel((60, 40)) == WHITE and img.getpixel((60, 19)) == WHITE
    in_photo_frame = mask.resize((200, 100))
    assert render_photo_overlay(png(200, 100), photo_id="ph1", damage_masks={"ob1": in_photo_frame},
                                observations=[observation("ob1", "ph1", (0.1, 0.1, 0.9, 0.9))])
    with pytest.raises(ContractError) as exc:
        render_photo_overlay(png(200, 100), photo_id="ph1", damage_masks={"ob1": Image.new("L", (64, 64))},
                             observations=[observation("ob1", "ph1", (0.1, 0.1, 0.9, 0.9))])
    assert exc.value.reason_code == "mask_geometry_mismatch"


def test_mark_crop_is_enlarged_around_the_box():
    crop = decode(render_mark_crop(png(1000, 500), (0.1, 0.2, 0.5, 0.4), zoom=2.0, margin=0.5))
    assert crop.size == (1400, 400)
    # page box left edge x=100 -> crop x=100 -> zoomed x=200..205 (3 px outline doubled); page y=150 -> zoomed 200
    assert near(crop.getpixel((202, 200)), COLORS.mark_selected, 12)
    assert near(crop.getpixel((600, 200)), WHITE, 2)
