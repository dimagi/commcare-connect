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
