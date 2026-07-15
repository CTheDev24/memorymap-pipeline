from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class MapFrame(BaseModel):
    center_lat: float = Field(ge=-90, le=90)
    center_lon: float = Field(ge=-180, le=180)
    coverage_width_m: float = Field(gt=0)
    coverage_height_m: float = Field(gt=0)
    rotation_degrees: float = Field(default=0, ge=-180, le=180)
    print_width_mm: float = Field(default=240, gt=0, le=1000)
    print_height_mm: float = Field(default=190, gt=0, le=1000)
    margin_mm: float = Field(default=8, ge=0)

    @model_validator(mode="after")
    def validate_margin(self) -> "MapFrame":
        if self.margin_mm * 2 >= min(self.print_width_mm, self.print_height_mm):
            raise ValueError("margin_mm leaves no printable area")
        return self


class LayerSettings(BaseModel):
    route: bool = True
    roads: bool = False
    buildings: bool = False


class PreviewRequest(BaseModel):
    frame: MapFrame
    layers: LayerSettings = Field(default_factory=LayerSettings)


class GenerateRequest(BaseModel):
    frame: MapFrame
    layers: LayerSettings = Field(default_factory=LayerSettings)
    route_width_mm: float = Field(default=1.2, gt=0, le=20)
    route_height_mm: float = Field(default=2, gt=0, le=50)
    base_thickness_mm: float = Field(default=1.6, gt=0, le=20)


class JobStatus(BaseModel):
    id: str
    status: Literal["queued", "running", "complete", "failed"]
    progress: int = Field(ge=0, le=100)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    result_url: str | None = None
