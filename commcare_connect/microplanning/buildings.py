"""
Building footprints for the microplanning map.

Overture publishes its buildings as a PMTiles archive and Mapbox GL reads PMTiles natively, so the
browser fetches footprints straight from Overture. This module only works out what to point it at:
there is no proxy view and no cache, and no building data passes through this process. The only
thing held on our side is which release to read.

That release is the part needing care: Overture's buckets drop everything older than 60 days, so
one that is fine today is a dead URL in two months. Which release to point at is not decided here
either -- it is whatever ``OvertureRelease`` holds, kept current by a daily task.
"""

from commcare_connect.microplanning.models import OvertureRelease

# https://docs.overturemaps.org/examples/overture-tiles/
OVERTURE_TILES_URL = (
    "https://overturemaps-extras-us-west-2.s3.us-west-2.amazonaws.com/tiles/{release}/buildings.pmtiles"
)

OVERTURE_BUILDINGS_LAYER = "building"

# A fact about the archive, handed to the Mapbox *source* as `maxzoom`: Overture builds tiles down
# to zoom 14 and no deeper. Declaring it is what makes Mapbox overzoom those z14 tiles for closer
# views; without it Mapbox requests z15+ tiles that do not exist and the overlay comes up empty.
OVERTURE_ARCHIVE_MAX_ZOOM = 14

# Our display policy, handed to the Mapbox *layers* as `minzoom`: the zoom at which footprints
# start being drawn at all.
#
# Set to the archive's max zoom, so footprints appear at Overture's own deepest tiles and are drawn
# at native resolution there; only the zooms past it are overzoomed. These two are not a range --
# one is a property of the data, this one is a choice.
#
# Do not drop it below the archive max. z14 is the last complete level: Overture thins the lower
# zooms hard (a z13 tile over Kibera carries about a fifth of the buildings its z14 tiles do), so
# drawing there would show a partial set of buildings while looking complete.
BUILDINGS_DISPLAY_MIN_ZOOM = 14

# Overture's buildings are largely OpenStreetMap derived, so both need crediting. This is the
# attribution Overture ships in the archive's own metadata.
OVERTURE_ATTRIBUTION = (
    '<a href="https://www.openstreetmap.org/copyright" target="_blank">&copy; OpenStreetMap</a> '
    '<a href="https://docs.overturemaps.org/attribution" target="_blank">&copy; Overture Maps Foundation</a>'
)


def buildings_overlay_config():
    """
    Return the config the map needs to draw building footprints, or ``None`` if it cannot.
    """
    release = (OvertureRelease.current() or "").strip()
    if not release:
        return None

    return {
        # Named in the overlay's tooltip, so a reader can tell which release is on screen.
        "release": release,
        "tilesUrl": OVERTURE_TILES_URL.format(release=release),
        "sourceLayer": OVERTURE_BUILDINGS_LAYER,
        "archiveMaxZoom": OVERTURE_ARCHIVE_MAX_ZOOM,
        "displayMinZoom": BUILDINGS_DISPLAY_MIN_ZOOM,
        "attribution": OVERTURE_ATTRIBUTION,
    }
