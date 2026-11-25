from app.services.editorial.theme import EditorialTheme


def test_route_theme_aliases_map_to_distinct_editorial_palettes():
    names = ["SLEEK", "PULSE", "MINIMAL FLUX", "BLUE HORIZON", "prime_meridian"]
    backgrounds = {EditorialTheme.from_name(name).background for name in names}
    assert len(backgrounds) == len(names)
