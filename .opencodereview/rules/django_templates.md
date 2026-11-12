> Only raise an issue you are confident is a real defect. These are Django templates rendered server-side, with Tailwind, htmx and Alpine.js for interactivity.

#### Escaping

- `|safe`, `{% autoescape off %}`, or a value built with `mark_safe`, applied to anything a worker, organization member or program manager can set
- User content interpolated into a `<script>` block or an Alpine expression, where template autoescaping does not protect it. Use `json_script`
- `x-html` fed a value that is not already escaped

#### URLs and htmx

- A hardcoded path where `{% url %}` should be used, or a `{% url %}` naming a route that does not exist
- `hx-target`/`hx-swap` pointing at an id the template never renders
- JavaScript that enhances elements of a fragment htmx swaps in, run at insertion time. htmx restores the old element's attributes during settle, so it has to run on `htmx:afterSettle` (see `static/js/tomselect.js`)

#### Project Conventions

- Inline `<script>` with logic in it. JavaScript belongs in a `.js` file under `commcare_connect/static/`
- A `<select>` marked `data-tomselect` on a page that does not load `bundles/js/tomselect-bundle.js` and `bundles/css/tomselect.css`. The field silently stays native
- A button, link or map that depends on optional configuration (a Mapbox token, a `SocialApp`, an OCS connection) rendered without checking that the configuration is present
- `{# ... #}` spread over several lines. Django does not support it; use `{% comment %}{% endcomment %}`
- A new user-facing string left untranslated where the surrounding template uses `{% translate %}`

#### Do Not Report

- A missing `{% csrf_token %}` on an `hx-post`/`hx-put`/`hx-delete`. The base template sends the token on every htmx request through `hx-headers` on `<body>`
- Indentation, attribute wrapping, or tag spacing. djlint formats this file
- The djlint rules this project disables: H006, H016, H020, H021, H023, H030, H031, T002, T003
- Raw Tailwind utility classes where a predefined class exists. Common in older templates and not a defect
