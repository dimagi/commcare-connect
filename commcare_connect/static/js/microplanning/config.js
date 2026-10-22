/**
 * Reading the server-rendered config for the progress map. Everything here takes `document` as an
 * argument so it can be called with a stub in tests.
 */

export const CONFIG_ELEMENT_ID = 'microplanning-config';

export function readConfig(doc) {
  return readJson(doc, CONFIG_ELEMENT_ID);
}

export function readJson(doc, id) {
  const element = doc.getElementById(id);
  return element ? JSON.parse(element.textContent) : null;
}
