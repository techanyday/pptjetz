from datetime import date

from app.services.pptx_builder import timeline_date_range_labels


def test_timeline_date_range_labels_default_6_months_3_phases() -> None:
    labels = timeline_date_range_labels(today=date(2026, 1, 27), total_months=6, phases=3)
    assert labels == [
        "Jan 2026 – Feb 2026",
        "Mar 2026 – Apr 2026",
        "May 2026 – Jun 2026",
    ]
