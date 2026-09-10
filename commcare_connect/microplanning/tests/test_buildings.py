import importlib
from datetime import date, timedelta

import pytest

from commcare_connect.microplanning.buildings import buildings_overlay_config

seed_migration = importlib.import_module("commcare_connect.microplanning.migrations.0018_overturerelease")


@pytest.mark.parametrize("recorded", ["2026-08-19.0", " 2026-08-19.0\n"])
def test_config_points_at_the_recorded_release(release, recorded):
    """The release is stripped before use: a stray newline must not reach the tile URL."""
    release(recorded)

    config = buildings_overlay_config()

    assert config["release"] == "2026-08-19.0"
    assert config["tilesUrl"].endswith("/tiles/2026-08-19.0/buildings.pmtiles")
    assert config["sourceLayer"] == "building"
    assert config["archiveMaxZoom"] == 14
    assert config["displayMinZoom"] == 14
    # Footprints must never start below the archive's deepest level: the lower zooms are thinned,
    # so drawing there would show a partial set of buildings while looking complete.
    assert config["displayMinZoom"] >= config["archiveMaxZoom"]


def test_the_config_follows_the_release_when_it_moves(release):
    """The day the daily task records a new release, the browser is pointed at that one."""
    release("2026-08-19.0")
    release("2026-09-17.0")

    assert buildings_overlay_config()["tilesUrl"].endswith("/tiles/2026-09-17.0/buildings.pmtiles")


def test_the_seeded_release_is_one_the_overlay_can_use(release):
    release(seed_migration.SEED_RELEASE)

    assert buildings_overlay_config() is not None


def test_the_seed_stops_being_written_once_overture_has_dropped_it():
    """
    The migration is replayed on every database made from here on, but the seed has a shelf life.
    """
    released_on = date.fromisoformat(seed_migration.SEED_RELEASE.split(".")[0])

    assert seed_migration.seed_is_still_published(released_on + seed_migration.OVERTURE_RETENTION)
    assert not seed_migration.seed_is_still_published(
        released_on + seed_migration.OVERTURE_RETENTION + timedelta(days=1)
    )


def test_config_credits_openstreetmap_and_overture(release):
    """Overture's buildings are largely OSM derived, so both are required in the attribution."""
    release("2026-08-19.0")

    attribution = buildings_overlay_config()["attribution"]

    assert "OpenStreetMap" in attribution
    assert "Overture Maps Foundation" in attribution


@pytest.mark.parametrize("recorded", [None, "", "   "])
def test_no_config_without_a_release(release, recorded):
    """
    A missing release makes the overlay unavailable rather than pointing the browser at a bad URL.

    The map itself has to keep working; only the footprint control goes away. A database migrated
    after the seeded release aged out has none until the daily task first runs, and a URL built
    from no release would 404 on every tile -- worse than showing no overlay at all.
    """
    release(recorded)

    assert buildings_overlay_config() is None
