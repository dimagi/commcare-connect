/**
 * Typeahead for the microplanning map's "Search Work Areas" box.
 *
 * Kept out of tomselect.js because the type badge needs render *functions*, which cannot be
 * expressed through that initializer's `data-tomselect:settings` JSON channel. The select must
 * therefore not carry `data-tomselect`, or the generic initializer would claim it first.
 *
 * Options are fetched once after page load rather than rendered inline (keeps the page payload
 * small) or per keystroke (avoids a round trip per character); matching is client-side.
 */

// There is exactly one of these on the page, so it is found by id rather than by a marker
// attribute. It is rendered in both progress and assignment mode — assignment mode has no filter
// sidebar, but it still drives the search box through applyAssignmentModeFilters().
const SEARCH_SELECT_ID = 'work-area-search';

// Keyed on the option's `kind`, not its `type` — `type` is translated, `kind` is not.
const BADGE_CLASSES = {
  wa: 'badge-indigo',
  wag: 'badge-teal',
  ia: 'badge-amber',
};

function typeBadge(data, escape) {
  const badgeClass = BADGE_CLASSES[data.kind] ?? 'badge-indigo';
  return `<span class="badge badge-sm ${badgeClass} shrink-0">${escape(
    data.type,
  )}</span>`;
}

// Dropdown row: one line each, so the list stays scannable and the badges line up.
function renderOption(data, escape) {
  return `<div class="flex items-center justify-between gap-2">
      <span class="truncate">${escape(data.label)}</span>
      ${typeBadge(data, escape)}
    </div>`;
}

// Selected chip: the name wraps rather than truncating, so a long one stays readable once
// chosen. `max-w-full`/`min-w-0` let the item and its label shrink below their content width
// (flex children default to min-width:auto, which would otherwise force one long line), and
// `break-words` handles slugs with no spaces to break at.
function renderItem(data, escape) {
  return `<div class="flex items-start gap-2 max-w-full min-w-0">
      <span class="min-w-0 break-words">${escape(data.label)}</span>
      ${typeBadge(data, escape)}
    </div>`;
}

function linkDescription(el, tomselect) {
  const describedBy = el.dataset.describedby;
  if (describedBy)
    tomselect.focus_node?.setAttribute('aria-describedby', describedBy);
}

function setPlaceholder(tomselect, text) {
  tomselect.settings.placeholder = text;
  tomselect.control_input.setAttribute('placeholder', text);
}

function loadOptions(el, tomselect) {
  fetch(el.dataset.optionsUrl)
    .then((res) => {
      if (!res.ok) throw new Error('Failed to load work area search options');
      return res.json();
    })
    .then((data) => {
      tomselect.addOptions(data.options);
      setPlaceholder(tomselect, el.dataset.readyText);
      tomselect.enable();
    })
    .catch(() => {
      // Leave the control disabled — an empty enabled box reads as "no matches exist".
      setPlaceholder(tomselect, el.dataset.errorText);
    });
}

function initWorkAreaSearch(el) {
  if (el.tomselect) return;

  const tomselect = new window.TomSelect(el, {
    maxItems: 1,
    valueField: 'value',
    labelField: 'label',
    searchField: ['label'],
    plugins: ['remove_button'],
    render: { option: renderOption, item: renderItem },
  });

  // TomSelect swallows the native event; re-dispatch so Alpine's @change fires. Clears made
  // with `clear(true)` stay silent, which is what keeps the search/filter reset from ping-ponging.
  tomselect.on('change', () => {
    el.dispatchEvent(new Event('change', { bubbles: true }));
  });

  tomselect.on('item_add', () => {
    tomselect.blur();
  });

  linkDescription(el, tomselect);
  tomselect.disable();
  setPlaceholder(tomselect, el.dataset.loadingText);
  loadOptions(el, tomselect);
}

document.addEventListener('DOMContentLoaded', () => {
  const el = document.getElementById(SEARCH_SELECT_ID);
  if (el) initWorkAreaSearch(el);
});
