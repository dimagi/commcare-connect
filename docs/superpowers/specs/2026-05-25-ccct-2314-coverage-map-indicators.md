# CCCT-2314 — Coverage Map Indicators: Spec

**Status:** Based on responses from the Product team, Top-table column layout now defers directly to the audit Excel (~30 columns); Coverage Map entry point switches from a tile link to a dedicated button. `INACCESSIBLE` WAs are confirmed to count in all canonical denominators.

---

## Part 1 — Canonical metrics from CHC RCT Audit Indicators

(Names taken directly from the audit Excel. Each ward gets one row with all of these columns populated.)

### Targets (denominators)
| Column | Definition |
|---|---|
| `Number of Work Areas` | Count of WAs in the ward, **excluding** `EXCLUDED` WAs only — `INACCESSIBLE` WAs are included (per Q5 follow-up 2026-05-27). Final treatment of `EXCLUDED` is still pending Delivery clarification; working assumption is to exclude per the Excel header. |

All implicit denominators apply the same filter: `SUM(expected_visit_count)`, `SUM(building_count)`, and `SUM(target_population)` are taken **over WAs that are not `EXCLUDED`** — `INACCESSIBLE` WAs are included (per Q5 follow-up).

### Visits (throughput)
| Column | Notes |
|---|---|
| `visits_approved` | Count of approved `UserVisit` records |
| `pct_visits_approved` | `visits_approved / sum(expected_visit_count)` × 100 |

### WAs Visited (status = `VISITED`)
| Column | Notes |
|---|---|
| `WAs_Visited` | Count |
| `pct_WAs_visited` | `WAs_Visited / Number of Work Areas` × 100 |
| `pct_WA_visited_to_pct_visits` | `pct_WAs_visited / pct_visits_approved × 100` |
| `WAs_visited_last_week` | Rolling 7-day variant of count |
| `pct_WAs_visited_last_week` | Rolling 7-day variant |
| `pct_WA_visited_to_pct_visits_last_week` | Rolling 7-day variant |

### WAs EVC Reached (status = `EXPECTED_VISIT_REACHED`) — i.e., the WA has hit its `expected_visit_count` target
| Column | Notes |
|---|---|
| `WAs_evc_reached` | Count |
| `pct_WAs_evc_reached` | `WAs_evc_reached / Number of Work Areas` × 100. This is the **"Ward Saturation Goal"** in the mockup header |
| `pct_WA_evc_reached_to_pct_visit` | `pct_WAs_evc_reached / pct_visits_approved × 100`. This is **"WA Coverage to Visit Ratio"** in the mockup. |
| `WAs_evc_reached_last_week` | Rolling 7-day |
| `pct_WAs_evc_reached_last_week` | Rolling 7-day |
| `pct_WA_evc_reached_to_pct_visits_last_week` | Rolling 7-day |

### Buildings under EVC-reached WAs ("microplan completion" in the mockup)
| Column | Notes |
|---|---|
| `Buildings_covered_in_WAs_evc_reached` | `SUM(WorkArea.building_count)` for WAs that are EVC-reached |
| `pct_Buildings_covered_in_WAs_evc_reached` | `Buildings_covered_in_WAs_evc_reached / SUM(building_count) × 100`. Likely the **"pct_microplan_completion"** column in the mockup. |
| `pct_buildings_covered_in_WA_evc_reached_to_pct_visit` | `pct_Buildings_covered_in_WAs_evc_reached / pct_visits_approved × 100` |
| `Buildings_covered_in_WAs_evc_reached_last_week` | Rolling 7-day |
| `pct_buildings_covered_in_WAs_evc_reached_last_week` | Rolling 7-day |
| `pct_buildings_covered_in_WA_evc_reached_to_pct_visits_last_week` | Rolling 7-day |

### Buildings under Visited WAs
| Column | Notes |
|---|---|
| `Buildings_covered_in_WAs_visited` | `SUM(WorkArea.building_count)` for WAs with status = VISITED |
| `pct_Buildings_covered_in_WAs_visited` | `Buildings_covered_in_WAs_visited / SUM(building_count) × 100`. Same shape as `pct_Buildings_covered_in_WAs_evc_reached`, but uses the broader "visited" status in the numerator. |
| `pct_buildings_covered_in_WA_visited_to_pct_visit` | `pct_Buildings_covered_in_WAs_visited / pct_visits_approved × 100` |
| `Buildings_covered_in_WAs_visited_last_week` | Rolling 7-day |
| `pct_buildings_covered_in_WAs_visited_last_week` | Rolling 7-day |
| `pct_buildings_covered_in_WA_visited_to_pct_visits_last_week` | Rolling 7-day |

**Total: ~30 columns per ward** (26 derived metrics across the 5 sections above + 3 target columns + the ward identifier).

---

## Part 2 — What we are going to build

Translating the locked product team decisions into the concrete page. The page is opportunity-scoped (per Q2) and contains a header, two tables, and a single page-level date filter (Overall / Custom range).

**Date filter scope (per Q7):** the filter applies to **every no-suffix metric on the page** — i.e., every column in both tables that is not a `_last_week` variant. The `_last_week` columns are always the rolling previous 7 days, never affected by the filter — they exist as a constant comparator alongside the filtered view. (With the top table now mirroring the Excel, both tables carry `_last_week` columns.)

**Date filter semantics** — the "Filter behaviour" column in the tables below uses three tags:

- **`visit_date`**: filters `UserVisit.visit_date` against the selected window. Used by visit-domain metrics (`visits_approved` and its derivatives).
- **`visited-at`**: filters by the earliest date the WA's status transitioned to `VISITED`-or-above. Sourced from the pghistory event table (see Part 4 Option 1).
- **`evc-reached-at`**: filters by the earliest date the WA's status transitioned to `EXPECTED_VISIT_REACHED`. Same source.
- **Pinned 7d**: same predicate type, but locked to the rolling previous 7 days regardless of filter selection.
- **Ratio**: each numerator/denominator applies its own filter; the ratio is computed in Python from the two filtered values.

### 2.0 Coverage Map entry point

The Coverage Progress Tracker page is reached from the existing Coverage Map. Per Q8 follow-up (2026-05-27), Delivery wants to **keep the tile count capped at 6** and prefers not to remove a tile to make room for a Ward Saturation Goal tile. Resolution: the new page is accessed via a **dedicated button on the Coverage Map** (Claude Design has mocked this up).

**LOE impact:** minimal vs. the original tile-link plan. The button is a small template/CSS addition next to the existing tile grid, and routing was already going to land for the new page either way. The bulk of the work (page itself, 28-metric query plan, caching) is unchanged.

### 2.1 Page header

| Element | Canonical metric | Notes |
|---|---|---|
| "Ward Saturation Goal: 25%" | `pct_WAs_evc_reached` rolled up to the opportunity level | Single number; opportunity-wide aggregate (per Q2) |

### 2.2 Top table — per-Ward metrics (one row per Ward)

Scope: every ward in the opportunity, one row each (per Q1). The page-level date filter re-scopes every no-suffix column; `_last_week` columns stay pinned to the rolling previous 7 days.

Per **Q4 follow-up (2026-05-27)**, the per-Ward column layout defers directly to the CHC RCT Audit Indicators Excel(You can see the doc link in the ticket) — we carry the 26 derived metrics from Part 1 in their Excel order, alongside the three target columns from Q3. Result: ~30 columns, horizontally scrollable.

| # | Column header | Canonical metric | Filter behaviour |
|---|---|---|---|
| 1 | Ward | (ward name) | — |
| 2 | Population Target | `SUM(target_population)` | Static; denominator per Part 1 rules |
| 3 | Building Count | `SUM(WorkArea.building_count)` | Static; same denominator rules |
| 4 | Number of Work Areas | (count) | Static; same denominator rules |
| 5 | visits_approved | `visits_approved` | Filtered by `visit_date` |
| 6 | pct_visits_approved | `pct_visits_approved` | Filtered by `visit_date` |
| 7 | WAs_Visited | `WAs_Visited` | Filtered by `visited-at` |
| 8 | pct_WAs_visited | `pct_WAs_visited` | Filtered by `visited-at` |
| 9 | pct_WA_visited_to_pct_visits | `pct_WA_visited_to_pct_visits` | Ratio (col 8 / col 6) |
| 10 | WAs_visited_last_week | (rolling 7d) | Pinned 7d (`visited-at`) |
| 11 | pct_WAs_visited_last_week | (rolling 7d) | Pinned 7d (`visited-at`) |
| 12 | pct_WA_visited_to_pct_visits_last_week | (rolling 7d) | Ratio (pinned 7d) |
| 13 | WAs_evc_reached | `WAs_evc_reached` | Filtered by `evc-reached-at` |
| 14 | pct_WAs_evc_reached *("Ward Saturation Goal")* | `pct_WAs_evc_reached` | Filtered by `evc-reached-at` |
| 15 | pct_WA_evc_reached_to_pct_visit *("WA Coverage to Visit Ratio")* | `pct_WA_evc_reached_to_pct_visit` | Ratio (col 14 / col 6) |
| 16 | WAs_evc_reached_last_week | (rolling 7d) | Pinned 7d (`evc-reached-at`) |
| 17 | pct_WAs_evc_reached_last_week | (rolling 7d) | Pinned 7d (`evc-reached-at`) |
| 18 | pct_WA_evc_reached_to_pct_visits_last_week | (rolling 7d) | Ratio (pinned 7d) |
| 19 | Buildings_covered_in_WAs_evc_reached | (sum over WAs) | Filtered by `evc-reached-at` |
| 20 | pct_Buildings_covered_in_WAs_evc_reached | (sum) | Filtered by `evc-reached-at` |
| 21 | pct_buildings_covered_in_WA_evc_reached_to_pct_visit | (ratio) | Ratio (col 20 / col 6) |
| 22 | Buildings_covered_in_WAs_evc_reached_last_week | (rolling 7d) | Pinned 7d (`evc-reached-at`) |
| 23 | pct_buildings_covered_in_WAs_evc_reached_last_week | (rolling 7d) | Pinned 7d (`evc-reached-at`) |
| 24 | pct_buildings_covered_in_WA_evc_reached_to_pct_visits_last_week | (rolling 7d) | Ratio (pinned 7d) |
| 25 | Buildings_covered_in_WAs_visited | (sum over WAs) | Filtered by `visited-at` |
| 26 | pct_Buildings_covered_in_WAs_visited | (sum) | Filtered by `visited-at` |
| 27 | pct_buildings_covered_in_WA_visited_to_pct_visit | (ratio) | Ratio (col 26 / col 6) |
| 28 | Buildings_covered_in_WAs_visited_last_week | (rolling 7d) | Pinned 7d (`visited-at`) |
| 29 | pct_buildings_covered_in_WAs_visited_last_week | (rolling 7d) | Pinned 7d (`visited-at`) |
| 30 | pct_buildings_covered_in_WA_visited_to_pct_visits_last_week | (rolling 7d) | Ratio (pinned 7d) |

Columns 2/3/4 remain three separate columns (per Q3), not stacked. Header-row groupings ("Visits", "WAs Visited", "WAs EVC Reached", "Buildings under EVC-Reached WAs", "Buildings under Visited WAs") mirror Part 1's section breakdown to keep the wide table navigable.

### 2.3 Bottom table — "Metrics by Work Area Group" (one row per WorkAreaGroup)

Scope: all WAGs across the entire opportunity, with no per-ward filter (per Q6). Each metric appears twice: a date-filtered column (responds to the same page-level filter as the top table) and a `_last_week` constant pinned to the rolling previous 7 days (per Q7).

| # | Column header | Canonical metric | Filter behaviour |
|---|---|---|---|
| 1 | Work Area Group | (WAG name) | — |
| 2 | Ward | (parent ward name) | — |
| 3 | Population Target | `SUM(target_population)` (per WAG) | Static; denominator per Part 1 rules |
| 4 | pct_visits_approved | `pct_visits_approved` | Filtered by `visit_date` |
| 5 | pct_visits_approved_last_week | `pct_visits_approved_last_week` | Pinned 7d (`visit_date`) |
| 6 | pct_WAs_evc_reached | `pct_WAs_evc_reached` | Filtered by `evc-reached-at` |
| 7 | pct_WAs_evc_reached_last_week | `pct_WAs_evc_reached_last_week` | Pinned 7d (`evc-reached-at`) |
| 8 | pct_WA_visited_to_pct_visits | `pct_WA_visited_to_pct_visits` | Ratio (`visited-at` numerator / col 4) |
| 9 | pct_WA_visited_to_pct_visits_last_week | `pct_WA_visited_to_pct_visits_last_week` | Ratio (pinned 7d) |

Mockup label changes (per Q9 + Q4 follow-up): rename `pct_visits_completed` → `pct_visits_approved`, `pct_WAs_completed` → `pct_WAs_evc_reached`, and `pct_microplan_completion` → `pct_WA_visited_to_pct_visits` (per product team follow-up — `pct_microplan_completion` is conceptually closer to the WA-visited-to-visits ratio than to the building-coverage metric I originally mapped to) before frontend dev consumes the mockup.

---

## Part 3 — Questions & product team responses

*Reference only — these have all been resolved (see ✅ markers below). Captured here so future readers can see the original questions and the product team's locked answers without losing context. Source of truth for what we're building is Part 2.*


### Q1. Top "Core Metrics" table — row semantics — ✅ RESOLVED
**Question:** The mockup shows row labels "Example FLW", "Jane Doe", etc. Is each row a Ward (one row per ward), or something else (e.g., one row per FLW)?

**Product team response:** Confirmed — **one row per Ward**. The "Example FLW" / "Jane Doe" labels in the mockup were placeholders.

### Q2. Report scope — ✅ RESOLVED
**Question:** Is this an opportunity-level page showing all wards, or a per-ward drill-in?

**Product team response:** Confirmed — **opportunity-level page with one row per ward**. The header "Ward Saturation Goal" represents the opportunity-wide aggregate.

### Q3. "Ward Population Target" cell — ✅ RESOLVED
**Question:** Ticket says "Display the ward's population target, building number, and total number of Work Areas". Mockup shows a single number. Should it be:
- (a) Just population target with building/WA counts in a tooltip
- (b) Stacked text like "12,345 pop / 542 bldgs / 18 WAs"
- (c) Three separate columns

**Product team response:** **(c) — three separate columns.** Population target, building count, and WA count each get their own column.

### Q4. Mapping of `pct_microplan_completion` — ✅ RESOLVED (follow-up 2026-05-27)
**Question:** Confirming that the mockup's `pct_microplan_completion` maps to the audit Excel's `pct_Buildings_covered_in_WAs_evc_reached`.

**Initial product team response (2026-05-26):** Yes — will also confirm with Delivery.

**Product team follow-up (2026-05-27):** For the **per-Ward top table**, defer entirely to the [CHC RCT Audit Indicators Excel](https://docs.google.com/spreadsheets/d/1Fkab4itd0RvByXDOoA6eai92-6GXHmdlIZZ_t-eqqmA/edit?pli=1&gid=1383542375) for column order and calculations (so the top table mirrors the Excel layout in full). **More generally, `pct_microplan_completion` is more akin to `pct_WA_visited_to_pct_visits`** — not the building-coverage metric I originally guessed.

**Implication:** (1) Part 2.2 top table expanded to ~30 columns following the Excel order (26 derived metrics + 3 targets + ward name). (2) Part 2.3 bottom table's third metric pair changed from `pct_Buildings_covered_in_WAs_evc_reached` → `pct_WA_visited_to_pct_visits`.

### Q5. EXCLUDED / INACCESSIBLE WAs in denominators — ⏳ PARTIALLY RESOLVED (follow-up 2026-05-27)
**Question:** The Excel says "Number of Work Areas (not counting those marked as Excluded)". Apply the same `EXCLUDED` filter to other denominators (`SUM(target_population)`, `SUM(building_count)`, `SUM(expected_visit_count)`)? And what about `INACCESSIBLE`?

**Initial product team response (2026-05-26):** Leave out both `EXCLUDED` and `INACCESSIBLE` WAs from all denominators, barring any inaccessibility-specific metric. Will confirm with Delivery.

**Product team follow-up (2026-05-27):** **`INACCESSIBLE` WAs should still be included in all calculations**, except for one indicator that's part of the Audit workflow (exact indicator TBD; none of the canonical metrics in Part 1 are in that bucket). **`EXCLUDED` handling is still being clarified with Delivery.**

**Implication:** Part 1 updated — `Number of Work Areas` and all `SUM(...)` denominators **include `INACCESSIBLE` WAs**. `EXCLUDED` filtering follows the Excel header as a working assumption (i.e., exclude `EXCLUDED` from `Number of Work Areas`); verify before final implementation. Reflected in Part 2 columns 2–4 (top table) and column 3 (bottom table).

### Q6. Bottom table — scope across wards — ✅ RESOLVED
**Question:** The "Metrics by Work Area Group" table — does it show (a) all WAGs across the opportunity, (b) only WAGs for a selected ward, or (c) WAGs grouped by ward?

**Product team response:** **(a) — all Work Area Groups across the opportunity.** The top core-metrics table is per-ward (with a date filter); the bottom table is opportunity-wide. There is **no** way to filter the bottom table by a single ward.

### Q7. Date-filter behaviour — ✅ RESOLVED
**Question:** Does the date filter affect only the no-suffix columns, or also the `_last_week` columns?

**Product team response:** Confirmed — **the filter only affects the regular (no-suffix) metrics. The `_last_week` columns are always the rolling previous 7 days**, regardless of filter, serving as a constant comparator.

### Q8. Access point from Coverage Map — ✅ RESOLVED (follow-up 2026-05-27)
**Question:** Which Coverage Map tile gets the "see more" link? Add a new "Ward Saturation Goal" tile?

**Initial product team response (2026-05-26):** For now, the "Count of Work Areas EVC Reached" tile gets the "see more" link; will confirm with Delivery on whether to swap a tile out for a dedicated "Ward Saturation Goal" tile.

**Product team follow-up (2026-05-27):** Delivery wants to **keep the Coverage Map tile count capped at 6** and prefers not to remove an existing tile for the Ward Saturation Goal statistic. Resolution: **add a dedicated button on the Coverage Map** that routes to the new page (Claude Design has mocked this up). LOE impact is minimal — small template/CSS addition next to the tile grid; the new page itself (the bulk of the work) is unchanged.

### Q9. Naming convention — confirm new names — ✅ RESOLVED
**Question:** Mockup uses old names (e.g. `pct_visits_completed`, `pct_WAs_completed`); the audit Excel introduces new names that also split some metrics in two. Use new names everywhere?

**Product team response:** **Yes — standardize on the names from the Excel sheet moving forward.** Update the mockup's old labels to match. The renames + splits to apply:

| Old | New |
|---|---|
| `pct_visits_completed` | `pct_visits_approved` |
| `pct_WAs_completed` | `pct_WAs_visited` **and** `pct_WAs_evc_reached` |
| `pct_WA_Visits_completion_rate` | `pct_WA_visited_to_pct_visits` **and** `pct_WA_evc_reached_to_pct_visit` |

---

## Part 4 — Engineering decisions


### 1. Recommended — Direct query + `quickcache` 15 min, 3-slot split

**Summary.** On every page load, compute the metrics directly from `UserVisit` + `WorkArea` using a handful of focused SQL queries (one per data domain), merge the results in Python by ward / work-area-group, and cache the merged output in three independent `quickcache` slots so filter-independent data is reused across user filter toggles. No new model, no Celery; uses existing infrastructure (`quickcache` from `commcare_connect/cache.py`). The access pattern is similar to `microplanning.get_metrics_for_microplanning` already in production, with the added cost of per-ward / per-WAG grouping.

**Query strategy:** ~4 focused per-domain queries per table + 1 header (~9 total per page load), merged in Python by `ward` / `work_area_group_id`. Each query packs multiple aggregates into one statement so Postgres scans the row source once and produces several columns. Grouping:
- **(a) WA-row aggregates, no UserVisit join** — all targets + WA-status counts + building sums share one `WorkArea` scan
- **(b) Visit-join aggregate** — `visits_approved` kept separate; mixing visit joins with WA-row aggregates inflates both
- **(c) `_last_week` WA aggregates** — all 4 rolling-7d WA/building metrics share the `latest_approved_at` correlated lookup; pay that cost once, fan it across multiple FILTERed aggregates
- **(d) `_last_week` visit-join** — `visits_approved_last_week`, same separation as (b)

> **Bench note.** On a t3.medium-equivalent setup with ~266k UserVisits / 250k WAs, cache-miss page-load is ~5 s (filter=Overall) / ~4.5 s (filter=Last Week). Inside the under-10 s budget; numbers will shift with real prod data shapes.

**Caching:** three slots so cardinality is bounded by data shape, not filter cardinality.

| Slot | Filter-dependent? | TTL | Contents |
|---|---|---|---|
| `coverage:static:opp=X` | No | 15 min | Static targets per ward / WAG |
| `coverage:last_week:opp=X` | No (always rolling 7 d) | 15 min | All `_last_week` numerators |
| `coverage:filtered:opp=X:filter=Y` | Yes | 15 min | Date-filtered no-suffix numerators, keyed by the active filter selection (Overall, or a custom range hash). |

Backend: existing `quickcache` (`commcare_connect/cache.py`). TTL-only invalidation. Custom-range filters may skip caching or use a shorter TTL — defer to implementation.

**Safeguards:**
- **`statement_timeout = '30s'`** scoped to the view via `SET LOCAL` inside the request transaction (`ATOMIC_REQUESTS = True` is already on, so the setting auto-resets at commit). Aborts any runaway query before it ties up a connection; view catches `django.db.utils.OperationalError` and degrades gracefully. The error can be reported to Sentry. *Important:* the except block must also call `transaction.set_rollback(True)` — when `statement_timeout` fires, Postgres puts the transaction into an aborted state, and the ATOMIC_REQUESTS wrapper otherwise tries to COMMIT and fails the request anyway.
- **Redis single-flight lock** around each filtered-slot recompute, using `cache.lock(...)` from `django-redis` (already the configured backend). Prevents dogpiling when concurrent PM requests hit a cold cache for the same `(opp, filter)` — only one request does the ~4 s recompute, the rest wait briefly and read the freshly-populated cache.

**Pros:**
- Simplest path; no new model or Celery plumbing
- Filter-independent slots reused across selections → custom-range exploration only recomputes the cheaper filtered slot
- Cache footprint bounded by data shape rather than filter cardinality
- Similar access pattern to `microplanning.get_metrics_for_microplanning` already in production (WorkArea + correlated UserVisit + filtered counts), with the added cost of per-ward / per-WAG grouping — precedent within the codebase
- Easy upgrade to option 2 if metrics later push us there — the same per-domain queries become the refresh task

**Cons:**
- Cache miss = ~4 s of DB work in the request path; user-driven, not scheduled
- Dogpile risk on cold cache with concurrent PMs (mitigated by safeguards above)

> **Follow-up improvement (if prod numbers come in tight).** The `_last_week` queries do a correlated lookup against `WorkAreaEvent` to find each WA's earliest status-transition timestamp. Today this uses the FK auto-index on `pgh_obj_id` and then filters + sorts in memory. Adding a composite index on `WorkAreaEvent(pgh_obj_id, status, pgh_created_at)` would turn each per-WA lookup into a single index seek + LIMIT 1. **Projected** (by analogy to the equivalent UserVisit composite-index gain) to cut the `_last_week` query cost roughly in half. Not part of the initial implementation; add as a follow-up migration if monitoring shows the page bumping the under-10 s budget.

### 2. Rejected — `CoverageProgressSnapshot` model + scheduled Celery refresh

Adds a model that pre-computes per-`(opportunity, ward)` and per-`(opportunity, work_area_group)` metric rows, refreshed by a scheduled Celery task. Considered for its DB-load isolation (scheduled work, page reads sub-50 ms) but blocked by the date-filter requirements.

**Why rejected:**
- **The custom-range filter doesn't fit the snapshot model.** Snapshot rows can only pre-compute one date scope each (the natural choice is Overall + the always-pinned rolling-7d numerators). Arbitrary custom date ranges have no pre-computed row to read from, so the page would fall back to live querying `UserVisit` + `WorkArea` for that filter selection. Option B would therefore not be a clean snapshot architecture — it would be a snapshot for the default Overall view plus Option-1-style live query + cache for custom ranges, doubling the implementation surface for partial coverage.
- **Inconsistent freshness across filter selections.** With any non-trivial refresh interval, the snapshot-served data (Overall + `_last_week` columns) lags behind the live-queried custom-range data. A CHW visit submitted this morning would not appear under the Overall view but would appear when a PM picks a custom range that includes today. Same page, two freshness levels depending on filter choice — confusing UX, arguably incorrect.

Reconsider only if the custom-range filter is later dropped from the page requirements. Until then, Option 1 covers both filter selections with uniform 15-min staleness.

### 3. Rejected — Denormalize `visited_at` and `evc_reached_at` on `WorkArea`

Add two timestamp columns on `WorkArea` recording when each WA first transitioned to VISITED-or-above and to EVC, populated from `WorkArea.update_status` at the moment of transition and never overwritten (backfilled from pghistory events for existing rows). The page would then read these columns directly instead of correlating against `WorkAreaEvent`.

**Why rejected:**
- **Duplicates data already in pghistory.** The `WorkAreaEvent` table already records every status transition with `pgh_created_at`. The proposed columns are a denormalized cache of the earliest matching event row — same information, redundant storage on a large table.
- **Permanent schema and write-path cost for a read-only optimization.** Two new columns on `WorkArea` maintained forever, plus extra logic in every status-write path (`update_status` covers most, but admin edits, data migrations, and any future endpoint changing `WorkArea.status` also have to remember). PR review is the only safety net against drift.
- **The cheaper alternative recovers most of the perf gap.** A composite index on `WorkAreaEvent(pgh_obj_id, status, pgh_created_at)` (see the follow-up improvement under Option 1) cuts the per-WA event lookup to a single index seek + LIMIT 1, without schema changes, without new write-path logic, and reversible by dropping the index.
- **Wrong layer for the data.** Status transition timestamps are event-history concepts; storing them as columns on the live row pulls history-shaped data into a transactional table that doesn't otherwise carry it.

---

