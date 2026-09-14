import pytest

from commcare_connect.microplanning import buildings
from commcare_connect.microplanning.buildings import buildings_overlay_config


@pytest.fixture
def release(monkeypatch):
    """Set the pinned Overture release for one test."""

    def _set(value):
        monkeypatch.setattr(buildings, "OVERTURE_RELEASE", value)

    return _set


@pytest.mark.parametrize("pinned", ["2026-08-19.0", " 2026-08-19.0\n"])
def test_config_points_at_the_configured_release(release, pinned):
    """The release is stripped before use: a stray newline must not reach the tile URL."""
    release(pinned)

    config = buildings_overlay_config()

    assert config["tilesUrl"].endswith("/tiles/2026-08-19.0/buildings.pmtiles")
    assert config["sourceLayer"] == "building"
    assert config["archiveMaxZoom"] == 14
    assert config["displayMinZoom"] == 14
    # Footprints must never start below the archive's deepest level: the lower zooms are thinned,
    # so drawing there would show a partial set of buildings while looking complete.
    assert config["displayMinZoom"] >= config["archiveMaxZoom"]


def test_config_credits_openstreetmap_and_overture():
    """Overture's buildings are largely OSM derived, so both are required in the attribution."""
    attribution = buildings_overlay_config()["attribution"]

    assert "OpenStreetMap" in attribution
    assert "Overture Maps Foundation" in attribution


def test_the_pinned_release_is_usable():
    """The pin ships in code, so a blank one would silently disable the overlay for everyone."""
    assert buildings_overlay_config() is not None


@pytest.mark.parametrize("pinned", [None, "", "   "])
def test_no_config_without_a_release(release, pinned):
    """
    A missing release makes the overlay unavailable rather than pointing the browser at a bad URL.

    The map itself has to keep working; only the footprint control goes away. Nothing can blank the
    pin today, but the release is due to move to the database, where absence is a real state.
    """
    release(pinned)

    assert buildings_overlay_config() is None
