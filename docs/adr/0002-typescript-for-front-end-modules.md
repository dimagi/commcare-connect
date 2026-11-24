# ADR-0002: TypeScript for front-end modules

**Status:** Accepted

**Author:** Chris Smit · **Created:** 2026-11-24

## Context

The front end is server-rendered Django templates, enhanced with Alpine.js and htmx, plus plain JavaScript modules that webpack bundles. Most of that JavaScript is short glue code. Some widgets are larger: the tag input for phone numbers, emails and links keeps state for tags, editing, selection and paste handling. Plain JavaScript gives no checks on the shape of that state or on the calls between modules, so mistakes show up only in the browser.

TypeScript can only be used in files that webpack compiles. Inline scripts in templates are already disallowed, so this limit costs nothing.

## Decision

We will write new front-end code that warrants its own module file in TypeScript.

- Types are checked with `tsc` in strict mode and never emitted; webpack's Babel step strips them when bundling.
- `npm run typecheck` runs in CI, and ESLint applies the TypeScript rules to `.ts` files.
- Existing `.js` files stay as they are. A file is converted only when it is substantially rewritten.
- TypeScript code reaches Django's JavaScript translation catalog through a small typed wrapper, and translation extraction scans `.ts` as well as `.js` files.

## Consequences

- Type errors in widget state and module interfaces fail CI instead of failing in the browser.
- Plain JavaScript modules can import TypeScript modules, so adoption needs no migration.
- The front end has two languages until the remaining JavaScript is converted or retired.
- Contributors need TypeScript knowledge to change these modules.
- Because Babel strips types without checking them, a build can succeed while `typecheck` fails, so CI must keep running both.

## Alternatives considered

- Plain JavaScript with JSDoc type comments — rejected because the annotations are verbose and editors check them less reliably than TypeScript.
- A React component for the widget — rejected because it adds a rendering framework the rest of the front end, built on Alpine.js and htmx, does not use.
- Converting all existing JavaScript to TypeScript — rejected because most of it is short glue code where types add little.
