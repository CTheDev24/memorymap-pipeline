import pytest

from memorymap_pipeline.desktop.print_settings import finish_colors


@pytest.mark.parametrize(
    "finish,base,roads,neutral",
    [
        ("Gallery", "#CAC6BC", "#CAC6BC", "#292A2B"),
        ("Nocturne", "#292A2B", "#505254", "#CAC6BC"),
    ],
)
@pytest.mark.parametrize(
    "accent,expected",
    [
        ("Signal Orange", "#F7591F"),
        ("Volt Lime", "#C7FF00"),
        ("Pulse Pink", "#FF3B8D"),
        ("Neutral", None),
        ("Color Wheel", "#123ABC"),
    ],
)
def test_finish_and_route_contract(finish, base, roads, neutral, accent, expected):
    colors = finish_colors(finish, accent, "#123abc")
    assert colors["base"] == colors["buildings"] == colors["water"] == colors["landscape"] == base
    assert colors["roads"] == roads
    assert colors["route"] == (expected or neutral)
