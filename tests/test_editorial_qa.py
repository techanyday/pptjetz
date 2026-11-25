from pptx import Presentation
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.util import Inches

from app.services.editorial.qa import run_editorial_qa
from app.services.editorial.layouts import _remove_theme_style


def test_editorial_qa_accepts_bounded_shadowless_slide():
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    shape = slide.shapes.add_shape(
        MSO_AUTO_SHAPE_TYPE.RECTANGLE,
        Inches(1),
        Inches(1),
        Inches(4),
        Inches(1),
    )
    shape.name = "content"
    shape.shadow.inherit = False
    _remove_theme_style(shape)
    shape.text = "A bounded claim"
    assert run_editorial_qa(prs) == []


def test_editorial_qa_reports_out_of_bounds_shapes():
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    shape = slide.shapes.add_textbox(Inches(12.8), Inches(7), Inches(2), Inches(1))
    shape.text = "Clipped"
    findings = run_editorial_qa(prs)
    assert any("outside slide bounds" in finding for finding in findings)
