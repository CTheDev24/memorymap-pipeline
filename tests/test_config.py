import pytest

from memorymap_pipeline.config import route_height_for_profile, route_slope_for_layer_height


@pytest.mark.parametrize(
    ("layer_height", "expected_slope"),
    [
        (0.08, 0.16),
        (0.12, 0.24),
        (0.16, 0.30),
        (0.20, 0.35),
        (0.24, 0.40),
        (0.28, 0.45),
    ],
)
def test_route_slope_matches_layer_height(layer_height, expected_slope):
    assert route_slope_for_layer_height(layer_height) == pytest.approx(expected_slope)


def test_route_slope_uses_nearest_supported_layer_height():
    assert route_slope_for_layer_height(0.17) == pytest.approx(0.30)


def test_route_slope_rejects_nonpositive_layer_height():
    with pytest.raises(ValueError, match="positive"):
        route_slope_for_layer_height(0.0)


def test_route_height_uses_profile_defaults_and_explicit_override():
    assert route_height_for_profile("urban") == pytest.approx(2.0)
    assert route_height_for_profile("landscape") == pytest.approx(1.2)
    assert route_height_for_profile("landscape", 2.0) == pytest.approx(2.0)


def test_route_height_rejects_invalid_profile_and_height():
    with pytest.raises(ValueError, match="Unsupported"):
        route_height_for_profile("satellite")
    with pytest.raises(ValueError, match="positive"):
        route_height_for_profile("urban", 0.0)
