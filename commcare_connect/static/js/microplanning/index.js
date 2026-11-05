/**
 * Entry point for the progress map (microplanning/home.html).
 *
 * Alpine, htmx and Mapbox are used through `window`, never imported: vendors-bundle.js and
 * mapbox-bundle.js already set them up, and importing them here would bundle a second copy (for
 * Mapbox, one whose access token is never set). eslint enforces this for this directory.
 * gettext() is also a global, from Django's JavaScript catalog, which home.html loads first.
 *
 * home.html loads this bundle without `defer` so the alpine:init listener is in place before the
 * deferred vendors bundle calls Alpine.start(). That means it runs before <body> is parsed, so the
 * config is read inside the listener, not at module level.
 */
import { readConfig } from './config';
import { createMapController } from './controller';

document.addEventListener('alpine:init', () => {
  const config = readConfig(document);
  window.Alpine.data('mapController', () => createMapController(config));
});
