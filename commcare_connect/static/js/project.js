import '../sass/project.scss';
import * as bootstrap from 'bootstrap';
import mapboxgl from 'mapbox-gl';
import circle from '@turf/circle';

/* Project specific Javascript goes here. */

function refreshTooltips() {
  const tooltipTriggerList = document.querySelectorAll(
    '[data-bs-toggle="tooltip"]',
  );
  const tooltipList = [...tooltipTriggerList].map(
    (tooltipTriggerEl) => new bootstrap.Tooltip(tooltipTriggerEl),
  );
}
window.refreshTooltips = refreshTooltips;

window.mapboxgl = mapboxgl;
window.circle = circle;

/**
 * Add gps data accuracy circles on the visit markers on a mapbox map.
 * @param {mapboxgl.Map} map - Mapbox Map
 * @param {Array.<{lng: float, lat: float, precision: float}> visit_data - Visit location data for User
 */
function addAccuracyCircles(map, visit_data) {
  map.on('load', () => {
    const visit_accuracy_circles = [];
    visit_data.forEach((loc) => {
      visit_accuracy_circles.push(
        circle([loc.lng, loc.lat], loc.precision, { units: 'meters' }),
      );
    });
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
        'fill-opacity': 0.3,
      },
    });

    map.addLayer({
      id: 'visit-accuracy-circle-outlines-layer',
      source: 'visit_accuracy_circles',
      type: 'line',
      paint: {
        'line-color': '#fcbf49',
        'line-width': 3,
        'line-opacity': 0.5,
      },
    });
  });
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
