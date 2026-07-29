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
    # The trigonometric terms above operate in radians, so convert with the
    # Web Mercator sphere radius (metres per radian), not metres per degree.
    scale = 6378137.0
    return projected * scale


def project_lonlat_array(latitudes: np.ndarray, longitudes: np.ndarray, center_lat: float, center_lon: float) -> np.ndarray:
    """Project arrays of lat/lon to the same local projected coordinate system used for GPX.

    Inputs are arrays of equal length. Output is Nx2 array in meters.
    """
    lat0 = math.radians(center_lat)
    lon0 = math.radians(center_lon)

    lat = np.radians(latitudes)
    lon = np.radians(longitudes)

    x = np.cos(lat) * np.sin(lon - lon0)
    y = np.cos(lat0) * np.sin(lat) - np.sin(lat0) * np.cos(lat) * np.cos(lon - lon0)

    projected = np.column_stack((x, y))
    scale = 6378137.0
    return projected * scale


def unproject_local_array(
    projected: np.ndarray,
    center_lat: float,
    center_lon: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Invert the local orthographic projection used by GPX and map layers."""
    coordinates = np.asarray(projected, dtype=float)
    if coordinates.ndim != 2 or coordinates.shape[1] != 2:
        raise ValueError("Projected coordinates must be an Nx2 array")
    radius = 6378137.0
    x = coordinates[:, 0] / radius
    y = coordinates[:, 1] / radius
    rho = np.hypot(x, y)
    if np.any(rho > 1.0 + 1e-9):
        raise ValueError("Projected coordinates exceed the orthographic horizon")
    rho = np.clip(rho, 0.0, 1.0)
    central_angle = np.arcsin(rho)
    sin_c = np.sin(central_angle)
    cos_c = np.cos(central_angle)
    lat0 = math.radians(center_lat)
    lon0 = math.radians(center_lon)
    safe_rho = np.where(rho > 1e-15, rho, 1.0)
    latitude = np.arcsin(
        cos_c * math.sin(lat0)
        + y * sin_c * math.cos(lat0) / safe_rho
    )
    longitude = lon0 + np.arctan2(
        x * sin_c,
        safe_rho * math.cos(lat0) * cos_c
        - y * math.sin(lat0) * sin_c,
    )
    center = rho <= 1e-15
    latitude[center] = lat0
    longitude[center] = lon0
    return np.degrees(latitude), np.degrees(longitude)


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
    leftover_x = target_width - (span_x * scale)
    leftover_y = target_height - (span_y * scale)
    offset_x = margin_mm + leftover_x / 2.0
    offset_y = margin_mm + leftover_y / 2.0
    centered = normalized + np.array([offset_x, offset_y], dtype=float)
    return centered


def compute_normalize_center_transform(projected: np.ndarray, width_mm: float, height_mm: float, margin_mm: float) -> dict:
    """Compute transform params (scale, min_x, min_y, margin_mm) needed to normalize and center points.

    Returns a dict that can be used with `apply_transform` to reproduce the same normalization.
    """
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
        return {"scale": 1.0, "min_x": min_x, "min_y": min_y, "offset_x": margin_mm, "offset_y": margin_mm}

    scale_x = target_width / span_x if span_x > 0 else 1.0
    scale_y = target_height / span_y if span_y > 0 else 1.0
    scale = float(min(scale_x, scale_y))
    leftover_x = target_width - (span_x * scale)
    leftover_y = target_height - (span_y * scale)
    offset_x = float(margin_mm + leftover_x / 2.0)
    offset_y = float(margin_mm + leftover_y / 2.0)

    return {
        "scale": scale,
        "min_x": float(min_x),
        "min_y": float(min_y),
        "offset_x": offset_x,
        "offset_y": offset_y,
    }


def apply_transform(projected: np.ndarray, transform: dict) -> np.ndarray:
    """Apply a transform returned by `compute_normalize_center_transform` to projected (meters) points.

    Returns points in millimeters already centered with margin applied.
    """
    scale = float(transform["scale"])
    min_x = float(transform["min_x"])
    min_y = float(transform["min_y"])
    offset_x = float(transform.get("offset_x", transform.get("margin_mm", 0.0)))
    offset_y = float(transform.get("offset_y", transform.get("margin_mm", 0.0)))

    xs = projected[:, 0]
    ys = projected[:, 1]
    normalized = np.column_stack(((xs - min_x) * scale, (ys - min_y) * scale))
    centered = normalized + np.array([offset_x, offset_y], dtype=float)
    return centered
