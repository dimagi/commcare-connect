import 'mapbox-gl/dist/mapbox-gl.css';
import mapboxgl from 'mapbox-gl';
import circle from '@turf/circle';
import MapboxDraw from '@mapbox/mapbox-gl-draw';
import '@mapbox/mapbox-gl-draw/dist/mapbox-gl-draw.css';

window.mapboxgl = mapboxgl;
window.circle = circle;

/**
 * Add gps data accuracy circles on the visit markers on a mapbox map.
 * @param {mapboxgl.Map} map - Mapbox Map
 * @param {Array.<{lng: float, lat: float, precision: float}> visit_data - Visit location data for User
 */
function addAccuracyCircles(map, visit_data) {
  const FILL_OPACITY = 0.1;
  const OUTLINE_COLOR = '#fcbf49';
  const OUTLINE_WIDTH = 3;
  const OUTLINE_OPACITY = 0.5;

  const visit_accuracy_circles = visit_data.map((loc) =>
    circle([loc.lng, loc.lat], loc.precision, { units: 'meters' }),
  );

  // Check if the source exists, then update or add the source
  if (map.getSource('visit_accuracy_circles')) {
    map.getSource('visit_accuracy_circles').setData({
      type: 'FeatureCollection',
      features: visit_accuracy_circles,
    });
  } else {
    map.addSource('visit_accuracy_circles', {
      type: 'geojson',
      data: {
        type: 'FeatureCollection',
        features: visit_accuracy_circles,
      },
    });

    map.addLayer({
      id: 'visit-accuracy-circles-layer',
      source: 'visit_accuracy_circles',
      type: 'fill',
      paint: {
        'fill-antialias': true,
        'fill-opacity': FILL_OPACITY,
      },
    });

    // Add the outline layer
    map.addLayer({
      id: 'visit-accuracy-circle-outlines-layer',
      source: 'visit_accuracy_circles',
      type: 'line',
      paint: {
        'line-color': OUTLINE_COLOR,
        'line-width': OUTLINE_WIDTH,
        'line-opacity': OUTLINE_OPACITY,
      },
    });
  }
}

window.addAccuracyCircles = addAccuracyCircles;

function addCatchmentAreas(map, catchments) {
  const ACTIVE_COLOR = '#3366ff';
  const INACTIVE_COLOR = '#ff4d4d';
  const CIRCLE_OPACITY = 0.15;

  const catchmentCircles = catchments.map((catchment) =>
    circle([catchment.lng, catchment.lat], catchment.radius, {
      units: 'meters',
      properties: { active: catchment.active },
    }),
  );

  if (map.getSource('catchment_circles')) {
    map.getSource('catchment_circles').setData({
      type: 'FeatureCollection',
      features: catchmentCircles,
    });
  } else {
    map.addSource('catchment_circles', {
      type: 'geojson',
      data: {
        type: 'FeatureCollection',
        features: catchmentCircles,
      },
    });

    map.addLayer({
      id: 'catchment-circles-layer',
      source: 'catchment_circles',
      type: 'fill',
      paint: {
        'fill-color': ['case', ['get', 'active'], ACTIVE_COLOR, INACTIVE_COLOR],
        'fill-opacity': CIRCLE_OPACITY,
      },
    });

    map.addLayer({
      id: 'catchment-circle-outlines-layer',
      source: 'catchment_circles',
      type: 'line',
      paint: {
        'line-color': '#fcbf49',
        'line-width': 3,
        'line-opacity': 0.5,
      },
    });
  }

  if (catchments?.length) {
    window.Alpine.nextTick(() => {
      const legendElement = document.getElementById('legend');
      if (legendElement) {
        const legendData = window.Alpine.$data(legendElement);
        legendData.show = true;
      }
    });
  }
}

window.addCatchmentAreas = addCatchmentAreas;

const BUILDINGS_SOURCE = 'overture-buildings';
const BUILDINGS_FILL_LAYER = 'overture-buildings-fill';
const BUILDINGS_OUTLINE_LAYER = 'overture-buildings-outline';

const MapboxUtils = {
  setAccessToken(token) {
    if (!token) {
      // eslint-disable-next-line no-console -- legitimate diagnostic for a misconfigured token
      console.error('Mapbox access token is not provided.');
      return false;
    }
    mapboxgl.accessToken = token;
    return true;
  },

  createMap(options) {
    options = options || {};
    const container = options.container || 'map';
    const style = options.style || 'mapbox://styles/mapbox/streets-v12';
    const center = options.center || [0, 0];
    const zoom = typeof options.zoom === 'number' ? options.zoom : 2;
    const mapOpts = { container, style, center, zoom };
    if (options.projection) mapOpts.projection = options.projection;
    return new mapboxgl.Map(mapOpts);
  },

  addNavigation(map, position) {
    position = position || 'top-left';
    map.addControl(new mapboxgl.NavigationControl(), position);
  },

  addDrawControls(map, opts) {
    opts = opts || {};
    const draw = new MapboxDraw(
      Object.assign(
        {
          displayControlsDefault: false,
          controls: { polygon: true, trash: true },
        },
        opts,
      ),
    );
    map.addControl(draw, 'top-left');
    return draw;
  },

  /**
   * Draw Overture building footprints, read by the browser straight from Overture's PMTiles archive.
   *
   * `archiveMaxZoom` is where Overture's tiles stop; `displayMinZoom` is where we choose to start
   * drawing. Declaring the former is what makes Mapbox overzoom the deepest tiles for closer views
   * rather than request tiles that do not exist.
   *
   * The source and layers are created on the first `setVisible(true)` rather than up front: adding
   * a PMTiles source is not lazy in Mapbox, so doing it eagerly costs the provider plugin and an
   * S3 range read of the archive header on every map load, even for the users who never switch
   * footprints on.
   *
   * @param {mapboxgl.Map} map - Mapbox Map
   * @param {{tilesUrl: string, sourceLayer: string, archiveMaxZoom: number, displayMinZoom: number, attribution: string}} config
   * @param {object} [options]
   * @param {string} [options.beforeId] - existing layer to insert the footprints beneath, so they
   *   sit under the map's own layers rather than over them.
   * @param {function(boolean): void} [options.onLoadingChange] - called when footprint tiles start
   *   and finish loading. Only ever true while footprints are shown.
   * @param {function(boolean): void} [options.onAvailabilityChange] - called with whether the map
   *   is zoomed in far enough for footprints to draw at all.
   * @param {function(): void} [options.onFailed] - called once if the archive turns out to be
   *   unreadable, so the caller can withdraw the toggle and say so. Never called for a failure
   *   the overlay can recover from.
   * @returns {{setVisible: function(boolean): void}} handle for toggling the footprints on and off
   */
  addBuildingsOverlay(map, config, options = {}) {
    const { beforeId, onLoadingChange, onAvailabilityChange, onFailed } =
      options;
    const FILL_COLOR = '#1d4ed8';
    const FILL_OPACITY = 0.25;
    const OUTLINE_COLOR = '#1e3a8a';
    const OUTLINE_WIDTH = 0.8;

    let added = false;
    const addSourceAndLayers = () => {
      if (added) return;
      added = true;

      map.addSource(BUILDINGS_SOURCE, {
        type: 'vector',
        url: config.tilesUrl,
        maxzoom: config.archiveMaxZoom,
        attribution: config.attribution,
      });

      // Below displayMinZoom footprints are too small to tell apart, so Mapbox is told not to draw
      // them rather than the overlay policing zoom itself.
      const shared = {
        source: BUILDINGS_SOURCE,
        'source-layer': config.sourceLayer,
        minzoom: config.displayMinZoom,
      };

      map.addLayer(
        {
          ...shared,
          id: BUILDINGS_FILL_LAYER,
          type: 'fill',
          paint: { 'fill-color': FILL_COLOR, 'fill-opacity': FILL_OPACITY },
        },
        beforeId,
      );

      map.addLayer(
        {
          ...shared,
          id: BUILDINGS_OUTLINE_LAYER,
          type: 'line',
          paint: { 'line-color': OUTLINE_COLOR, 'line-width': OUTLINE_WIDTH },
        },
        beforeId,
      );
    };

    // Mapbox does the fetching, so progress has to be read back off its source events rather than
    // tracked around a request of our own. Hidden layers load no tiles, so `shown` gates this: an
    // idle map with the overlay off is not "loading", it has nothing to load.
    let shown = false;
    let loading = false;
    const setLoading = (next) => {
      if (next === loading) return;
      loading = next;
      if (onLoadingChange) onLoadingChange(loading);
    };
    const syncLoading = () =>
      setLoading(shown && added && !map.isSourceLoaded(BUILDINGS_SOURCE));

    // sourcedataloading covers the archive header and directory reads as well as the tiles, so the
    // first toggle reports progress while Mapbox is still working out where the tiles are.
    let everLoaded = false;
    ['sourcedataloading', 'sourcedata'].forEach((event) => {
      map.on(event, (e) => {
        if (e.sourceId !== BUILDINGS_SOURCE) return;
        // Remembered so the error handler can tell a dead archive from a tile that dropped out of
        // one that works: reaching loaded even once proves the archive itself is readable.
        if (map.isSourceLoaded(BUILDINGS_SOURCE)) everLoaded = true;
        syncLoading();
      });
    });
    // An idle map has nothing in flight, so this clears the indicator outright instead of asking
    // the source again: a tile that failed leaves the source looking unloaded forever, and reading
    // it here would leave the indicator spinning on a load that has already given up.
    map.on('idle', () => setLoading(false));
    // The release this points at is retired by Overture after 60 days, at which point the archive
    // 404s and the overlay silently draws nothing. Surface that rather than spinning forever.

    let failed = false;
    map.on('error', (e) => {
      if (e.sourceId !== BUILDINGS_SOURCE) return;
      setLoading(false);
      // eslint-disable-next-line no-console -- the retired-release case has no other signal
      console.error('Overture buildings source failed to load', e.error);
      if (everLoaded || failed) return;
      failed = true;
      if (onFailed) onFailed();
    });

    // One threshold, applied twice from here: as the layers' Mapbox `minzoom`, and as the
    // availability reported to the caller. Callers never re-derive it, so the control cannot end up
    // offering a toggle for a zoom at which Mapbox draws nothing.
    let available = null;
    const syncAvailability = () => {
      const next = map.getZoom() >= config.displayMinZoom;
      if (next === available) return;
      available = next;
      if (onAvailabilityChange) onAvailabilityChange(available);
    };
    syncAvailability();
    map.on('zoomend', syncAvailability);

    return {
      setVisible(visible) {
        if (visible) addSourceAndLayers();
        if (!added) return;

        const visibility = visible ? 'visible' : 'none';
        [BUILDINGS_FILL_LAYER, BUILDINGS_OUTLINE_LAYER].forEach((layer) => {
          map.setLayoutProperty(layer, 'visibility', visibility);
        });
        shown = visible;
        syncLoading();
      },
    };
  },

  createMarker(map, opts) {
    // Creating markers using HTML might present performance issues at scale, so better
    // to use layers instead for large datasets.
    opts = opts || {};
    const markerOpts = {};
    if (opts.color) markerOpts.color = opts.color;
    if (opts.scale) markerOpts.scale = opts.scale;
    const marker = new mapboxgl.Marker(markerOpts).setLngLat([
      opts.lng,
      opts.lat,
    ]);
    if (opts.popupHtml)
      marker.setPopup(new mapboxgl.Popup().setHTML(opts.popupHtml));
    marker.addTo(map);
    return marker;
  },
};

window.MapboxUtils = MapboxUtils;
