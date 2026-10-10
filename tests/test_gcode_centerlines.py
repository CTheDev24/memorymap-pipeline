import math

import pytest

from tools.gcode_centerlines import arc_midpoints


@pytest.mark.parametrize("clockwise", [False, True])
def test_arc_direction_traverses_correct_quadrants(clockwise):
    points = list(arc_midpoints(1, 0, 0, 1, -1, 0, clockwise=clockwise))
    assert all(math.hypot(x, y) == pytest.approx(1) for x, y, _ in points)
    if clockwise:
        assert any(x < 0 and y < 0 for x, y, _ in points)
    else:
        assert all(x > 0 and y > 0 for x, y, _ in points)


def test_closed_arc_samples_all_quadrants():
    points = list(arc_midpoints(3, 2, 3, 2, -1, 0, clockwise=True))
    assert {(x > 2, y > 2) for x, y, _ in points} == {
        (False, False),
        (False, True),
        (True, False),
        (True, True),
    }
    assert all(math.hypot(x - 2, y - 2) == pytest.approx(1) for x, y, _ in points)


def test_arc_rejects_zero_radius():
    with pytest.raises(ValueError, match="radius"):
        list(arc_midpoints(0, 0, 1, 1, 0, 0, clockwise=False))
