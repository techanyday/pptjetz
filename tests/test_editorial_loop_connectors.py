from pptx import Presentation

from app.services.editorial.layouts import loop_diagram
from app.services.editorial.schema import DeckSpec
from app.services.editorial.theme import EditorialTheme


NOTES = " ".join(
    [
        "This note explains the slide claim for the audience.",
        "It adds a concrete example and connects the idea to the next decision.",
    ]
    * 5
)


def _spec(count: int):
    return DeckSpec(
        title="A loop story",
        audience="Leaders",
        thesis="A repeating system changes the decision",
        slides=[
            {
                "layout": "loop_diagram",
                "action_title": "A repeating system reinforces its outcome",
                "notes": NOTES,
                "fields": {"steps": [f"Step {index} advances the system" for index in range(count)]},
            }
        ],
    ).slides[0]


def test_three_step_loop_is_a_symmetric_triangle_with_connected_edges():
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    loop_diagram(slide, _spec(3), EditorialTheme.from_name("SLEEK"))

    nodes = [shape for shape in slide.shapes if shape.name.startswith("loop_node_")]
    assert len(nodes) == 3
    assert nodes[1].top == nodes[2].top
    top_center = nodes[0].left + nodes[0].width // 2
    bottom_center = (nodes[1].left + nodes[2].left + nodes[1].width) // 2
    assert abs(top_center - bottom_center) < 2000

    connectors = [shape for shape in slide.shapes if shape.name.startswith("loop_connector_")]
    assert len(connectors) == 3
    for connector in connectors:
        assert connector.line.width.pt == 1.5
        assert connector._element.spPr.ln.find(
            "{http://schemas.openxmlformats.org/drawingml/2006/main}tailEnd"
        ) is not None
        assert connector._element.find(
            ".//{http://schemas.openxmlformats.org/drawingml/2006/main}stCxn"
        ) is not None
        assert connector._element.find(
            ".//{http://schemas.openxmlformats.org/drawingml/2006/main}endCxn"
        ) is not None


def test_four_and_five_step_loops_have_one_connector_per_edge():
    for count in (4, 5):
        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        loop_diagram(slide, _spec(count), EditorialTheme.from_name("SLEEK"))
        connectors = [shape for shape in slide.shapes if shape.name.startswith("loop_connector_")]
        assert len(connectors) == count
        assert all(connector.line.width.pt == 1.5 for connector in connectors)
