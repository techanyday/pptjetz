from types import SimpleNamespace
from unittest.mock import patch

from app.services.editorial.images import get_slide_photo


def test_editorial_photo_uses_orientation_and_best_candidate():
    spec = SimpleNamespace(queries=["woman writing planner"], image_style="warm natural light")
    candidates = [
        {"id": "wide", "image_url": "https://wide", "width": 2400, "height": 800, "alt": "woman writing planner"},
        {"id": "portrait", "image_url": "https://portrait", "width": 1200, "height": 1800, "alt": "woman writing planner"},
    ]
    with patch("app.services.editorial.images.search_pexels_candidates", return_value=candidates) as search, patch(
        "app.services.editorial.images.download_image_bytes", return_value=b"x" * 3000
    ):
        result = get_slide_photo(spec, 4.0, 7.5, set())
    assert result == b"x" * 3000
    assert search.call_args.kwargs["orientation"] == "portrait"


def test_editorial_photo_degrades_when_key_or_results_are_missing():
    spec = {"queries": ["abstract growth", "business people"], "image_style": "natural light"}
    with patch("app.services.editorial.images.search_pexels_candidates", return_value=[]):
        assert get_slide_photo(spec, 13.333, 7.5, set()) is None


def test_editorial_photo_skips_used_candidates():
    spec = SimpleNamespace(queries=["people building"], image_style="")
    candidates = [
        {"id": "used", "image_url": "https://used", "width": 1600, "height": 900, "alt": "people building"},
        {"id": "new", "image_url": "https://new", "width": 1600, "height": 900, "alt": "people building"},
    ]
    with patch("app.services.editorial.images.search_pexels_candidates", return_value=candidates), patch(
        "app.services.editorial.images.download_image_bytes", return_value=b"new" * 1000
    ) as download:
        result = get_slide_photo(spec, 13.333, 7.5, {"used"})
    assert result == b"new" * 1000
    assert download.call_args.args[0] == "https://new"
