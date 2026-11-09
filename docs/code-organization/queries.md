# Queries, QuerySets and caches

Read [`../code-organization.md`](../code-organization.md) first.

## QuerySet methods

A function that takes one model's queryset and returns it filtered or annotated stays private in its module until a second module uses it, or until it mirrors a model property. Then it becomes a method on a custom `QuerySet`, exposed with `QuerySet.as_manager()` or `Manager.from_queryset()` so it chains after `.filter()`, e.g. `Opportunity.objects.accessible_to(org)`.

- A filter that limits rows to what a user may see is a `QuerySet` method that `permissions` calls.
- An SQL version of a model property sits next to the property, with a test that the two agree.
- A filter on another app's models goes in `queries` (see [`cross-app.md`](cross-app.md)).

## ORM expressions

- A function that builds an expression for another query (`Subquery`, `Case`, `Q` with lookups) goes in `queries`. A `Q` or `Case` that names no model is pure.
- Keep near-identical builders, such as a family of `Subquery` functions, together, where the most-shared one would go.

## Caches

The app whose data is cached owns the cache key and the function that clears it, next to the cached read in its `queries`. Other apps call that function.
