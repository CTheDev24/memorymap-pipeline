from __future__ import annotations

import math
from typing import Sequence

import numpy as np


def project_points(points: Sequence[object], center_lat: float, center_lon: float) -> np.ndarray:
    coordinates = np.array([[point.latitude, point.longitude] for point in points], dtype=float)
    lat0 = math.radians(center_lat)
    lon0 = math.radians(center_lon)

    lat = np.radians(coordinates[:, 0])
    lon = np.radians(coordinates[:, 1])

    x = np.cos(lat) * np.sin(lon - lon0)
    y = np.cos(lat0) * np.sin(lat) - np.sin(lat0) * np.cos(lat) * np.cos(lon - lon0)

    projected = np.column_stack((x, y))
    scale = 111319.49
    return projected * scale


def normalize_and_scale_points(projected: np.ndarray, width_mm: float, height_mm: float) -> np.ndarray:
    if projected.shape[0] == 0:
        raise ValueError("No projected points available")

    xs = projected[:, 0]
    ys = projected[:, 1]
    min_x, max_x = np.min(xs), np.max(xs)
    min_y, max_y = np.min(ys), np.max(ys)

    span_x = max_x - min_x
    span_y = max_y - min_y
    if span_x == 0 and span_y == 0:
        return np.column_stack((np.zeros(len(projected)), np.zeros(len(projected))))

    scale_x = width_mm / span_x if span_x > 0 else 1.0
    scale_y = height_mm / span_y if span_y > 0 else 1.0
    scale = min(scale_x, scale_y)

    normalized = np.column_stack(((xs - min_x) * scale, (ys - min_y) * scale))
    return normalized


def normalize_scale_and_center_points(projected: np.ndarray, width_mm: float, height_mm: float, margin_mm: float) -> np.ndarray:
    if projected.shape[0] == 0:
        raise ValueError("No projected points available")

    target_width = width_mm - 2.0 * margin_mm
    target_height = height_mm - 2.0 * margin_mm
    if target_width <= 0 or target_height <= 0:
        raise ValueError("Margin is too large for the provided map dimensions")

    xs = projected[:, 0]
    ys = projected[:, 1]
    min_x, max_x = np.min(xs), np.max(xs)
    min_y, max_y = np.min(ys), np.max(ys)

    span_x = max_x - min_x
    span_y = max_y - min_y
    if span_x == 0 and span_y == 0:
        centered = np.column_stack((np.full(len(projected), margin_mm), np.full(len(projected), margin_mm)))
        return centered

    scale_x = target_width / span_x if span_x > 0 else 1.0
    scale_y = target_height / span_y if span_y > 0 else 1.0
    scale = min(scale_x, scale_y)

    normalized = np.column_stack(((xs - min_x) * scale, (ys - min_y) * scale))
    centered = normalized + np.array([margin_mm, margin_mm], dtype=float)
    return centered
