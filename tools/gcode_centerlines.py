"""Sample relative-I/J XY arcs at exact circular centerline positions.

Convention: https://marlinfw.org/docs/gcode/G002-G003.html
"""

import math


def arc_midpoints(x, y, nx, ny, i, j, *, clockwise, step_mm=0.05):
    cx, cy = x + i, y + j
    radius = math.hypot(i, j)
    if radius <= 0 or not math.isfinite(radius) or step_mm <= 0:
        raise ValueError("Arc radius and sample spacing must be positive")
    start = math.atan2(y - cy, x - cx)
    end = math.atan2(ny - cy, nx - cx)
    sweep = -((start - end) % math.tau) if clockwise else (end - start) % math.tau
    if abs(sweep) < 1e-12:
        sweep = -math.tau if clockwise else math.tau
    count = max(1, math.ceil(abs(sweep) * radius / step_mm))
    for index in range(count):
        fraction = (index + 0.5) / count
        angle = start + sweep * fraction
        yield cx + radius * math.cos(angle), cy + radius * math.sin(angle), fraction
