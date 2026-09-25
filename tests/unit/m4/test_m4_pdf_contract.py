import pytest
from claim_cmev.contracts import PageTransform
import numpy as np
from claim_cmev.documents.text_layout.raster import render_pages
from claim_cmev.vision.transforms import map_points

def make_pdf(rotate, crop):
    content = b"0 0 0 rg 150 100 20 20 re f\n"
    objs = []
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objs.append(("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /CropBox [%s] /Rotate %d /Contents 4 0 R >>" % (crop, rotate)).encode())
    objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"endstream")
    out = b"%PDF-1.4\n"
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs)+1)
    for o in offs:
        out += b"%010d 00000 n \n" % o
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs)+1, xref)
    return out

@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
@pytest.mark.parametrize("crop", ["0 0 600 800", "100 50 500 750"])
def test_contract_maps_rendered_rectangle_to_original_pdf(rotate, crop):
    page = render_pages(make_pdf(rotate, crop), "application/pdf", 72, 5)[0]
    ys, xs = np.nonzero(page.image[:, :, 0] < 128)
    transform = PageTransform(source_width=page.image.shape[1], source_height=page.image.shape[0],
        corrected_width=page.image.shape[1], corrected_height=page.image.shape[0],
        geometry_correction="none", correction_reason="pdf_render", render_scale=page.source.render_scale,
        render_to_pdf=page.source.render_to_source.tolist())
    points = [transform.corrected_to_pdf_points(x, y) for x in [xs.min(), xs.max()+1]
              for y in [ys.min(), ys.max()+1]]
    assert np.min(points, axis=0) == pytest.approx([150, 100], abs=1)
    assert np.max(points, axis=0) == pytest.approx([170, 120], abs=1)
