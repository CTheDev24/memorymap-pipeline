import pytest

from memorymap_pipeline.palettes import (
    PALETTE_PRESETS,
    default_preset,
    normalize_color,
    resolve_palette,
    rgba,
)


def test_profile_defaults_preserve_existing_colors():
    assert default_preset("urban") == "urban-classic"
    assert resolve_palette("urban")["base"] == "#FFFFFF"
    assert resolve_palette("landscape")["base"] == "#D6CBAB"
    assert resolve_palette("landscape")["water"] == "#3399FF"


def test_palette_overrides_are_normalized_without_mutating_preset():
    colors = resolve_palette("urban", overrides={"route": "#a1b2c3"})

    assert colors["route"] == "#A1B2C3"
    assert resolve_palette("urban")["route"] == "#FF6633"
    assert rgba(colors["route"]) == (161, 178, 195, 255)


@pytest.mark.parametrize("value", ["red", "#12345", "#GG0000", 123456])
def test_invalid_colors_are_rejected(value):
    with pytest.raises(ValueError, match="Invalid layer color"):
        normalize_color(value)


def test_unknown_layers_and_presets_are_rejected():
    with pytest.raises(ValueError, match="Unsupported color preset"):
        resolve_palette("urban", "missing")
    with pytest.raises(ValueError, match="Unsupported color layer"):
        resolve_palette("urban", overrides={"labels": "#112233"})


def test_named_collections_have_complete_layer_colors():
    expected = {
        "heritage",
        "gallery-concrete",
        "nocturne",
        "ridgeline-sage",
        "desert-archive",
        "coastal-limestone",
        "deco-after-dark",
        "meridian-atlas",
        "vector-lab",
    }

    assert expected <= PALETTE_PRESETS.keys()
    for name in expected:
        assert set(resolve_palette("urban", name)) == {
            "base", "route", "roads", "buildings", "water", "landscape"
        }
    assert resolve_palette("urban", "vector-lab")["route"] == "#D7D602"
    assert resolve_palette("urban", "meridian-atlas") == {
        "base": "#BBADA4",
        "route": "#C06443",
        "roads": "#E4D0B0",
        "buildings": "#485155",
        "water": "#5F778E",
        "landscape": "#BBADA4",
    }
