/* global MapboxUtils, mapboxgl */
// `MapboxUtils` and `mapboxgl` are loaded as globals by the mapbox bundle.

function initReviewInaccessibilityMap() {
  const accessToken = JSON.parse(
    document.getElementById('mapbox-token').textContent,
  );
  const boundaryGeojson = JSON.parse(
    document.getElementById('boundary-data').textContent,
  );
  const requestLocationGeojson = JSON.parse(
    document.getElementById('location-data').textContent,
  );

  if (!MapboxUtils.setAccessToken(accessToken)) return;

  const map = MapboxUtils.createMap({
    container: 'review-inaccessibility-map',
    style: 'mapbox://styles/mapbox/standard',
    zoom: 12,
    center: [0, 0],
  });
  window.reviewInaccessibilityMap = map;

  map.once('load', () => {
    map.addSource('review-boundary', {
      type: 'geojson',
      data: boundaryGeojson,
    });

    map.addLayer({
      id: 'review-boundary-fill',
      type: 'fill',
      source: 'review-boundary',
      paint: {
        'fill-color': '#6366f1',
        'fill-opacity': 0.35,
      },
    });

    map.addLayer({
      id: 'review-boundary-outline',
      type: 'line',
      source: 'review-boundary',
      paint: {
        'line-color': '#4338ca',
        'line-width': 2,
      },
    });

    const bounds = new mapboxgl.LngLatBounds();
    const coords =
      boundaryGeojson.coordinates ??
      boundaryGeojson.geometries?.flatMap((g) => g.coordinates) ??
      [];
    function expandBounds(ring) {
      if (Array.isArray(ring[0])) {
        ring.forEach(expandBounds);
      } else {
        bounds.extend(ring);
      }
    }
    expandBounds(coords);
    if (!bounds.isEmpty()) {
      map.fitBounds(bounds, { padding: 40, duration: 0 });
    }

    if (requestLocationGeojson) {
      map.addSource('review-location', {
        type: 'geojson',
        data: requestLocationGeojson,
      });
      map.addLayer({
        id: 'review-location-point',
        type: 'circle',
        source: 'review-location',
        paint: {
          'circle-radius': 8,
          'circle-color': '#e11d48',
          'circle-stroke-width': 2,
          'circle-stroke-color': '#ffffff',
        },
      });
    }
  });
}

initReviewInaccessibilityMap();

function reviewFormBeforeRequest(form) {
  form.querySelectorAll('button').forEach((b) => (b.disabled = true));
  document
    .getElementById('review-inaccessibility-error')
    .classList.add('hidden');
}

function reviewFormAfterRequest(form) {
  form.querySelectorAll('button').forEach((b) => (b.disabled = false));
}

function reviewFormResponseError(event) {
  const el = document.getElementById('review-inaccessibility-error');
  if (event.detail.xhr.responseText)
    el.textContent = event.detail.xhr.responseText;
  el.classList.remove('hidden');
}

// Exposed for the inline hx-on handlers in review_inaccessibility_modal.html
window.reviewFormBeforeRequest = reviewFormBeforeRequest;
window.reviewFormAfterRequest = reviewFormAfterRequest;
window.reviewFormResponseError = reviewFormResponseError;
