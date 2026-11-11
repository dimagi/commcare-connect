# JavaScript

Read [`../code-organization.md`](../code-organization.md) first.

JavaScript lives in `.js` files, not templates. A helper used in one file stays private there until a second file needs it or it grows too large or complex for that file. Then move it to its own module in `static/js/`. Only webpack entries (`webpack/base.config.js`) can import, so a script loaded with `{% static %}`, such as `static/prelogin/app.js`, must become an entry first.
