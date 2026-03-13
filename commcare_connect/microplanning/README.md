# Microplanning App

The microplanning app manages geographic work areas for community health worker opportunities. It allows organizations to define, import, assign, and visualize work areas on a map, with bidirectional sync to CommCare HQ.

## Models

### WorkArea

A geographic area where work is to be done. Stores PostGIS geometry (SRID 4326/WGS84).

| Field                  | Type                 | Description                                                      |
| ---------------------- | -------------------- | ---------------------------------------------------------------- |
| `slug`                 | SlugField            | Unique identifier per opportunity                                |
| `ward`                 | SlugField            | Administrative ward name                                         |
| `centroid`             | PointField           | Center point (lon/lat)                                           |
| `boundary`             | PolygonField         | Area polygon boundary                                            |
| `building_count`       | PositiveIntegerField | Number of buildings in the area                                  |
| `expected_visit_count` | PositiveIntegerField | Target number of visits                                          |
| `status`               | CharField            | Current status (see statuses below)                              |
| `case_id`              | UUIDField            | CommCare HQ case ID (set after sync)                             |
| `case_properties`      | JSONField            | Extra properties: `max_wag`, `wag_serial_number`, `lga`, `state` |
| `opportunity`          | FK → Opportunity     | Parent opportunity                                               |
| `work_area_group`      | FK → WorkAreaGroup   | Optional group assignment                                        |

**Constraint**: `(slug, opportunity)` must be unique.

**Deletion protection**: Work areas referenced by `UserVisit` records cannot be deleted (`on_delete=PROTECT`).

### WorkAreaGroup

Groups work areas together and assigns them to a mobile worker.

| Field           | Type                   | Description                        |
| --------------- | ---------------------- | ---------------------------------- |
| `name`          | CharField              | Group name, unique per opportunity |
| `ward`          | SlugField              | Administrative ward                |
| `opportunity`   | FK → Opportunity       | Parent opportunity                 |
| `assigned_user` | FK → OpportunityAccess | Assigned mobile worker (optional)  |

### WorkAreaStatus

Lifecycle statuses for a work area:

| Status                     | Description                           |
| -------------------------- | ------------------------------------- |
| `NOT_STARTED`              | Initial state                         |
| `UNASSIGNED`               | Exists but not assigned               |
| `NOT_VISITED`              | Assigned but no visits recorded       |
| `VISITED`                  | Has visits but target not reached     |
| `REQUEST_FOR_INACCESSIBLE` | Worker requesting inaccessible status |
| `EXPECTED_VISIT_REACHED`   | Target visit count achieved           |
| `INACCESSIBLE`             | Confirmed inaccessible                |
| `EXCLUDED`                 | Excluded from the opportunity         |

## URL Endpoints

All routes are scoped under `/a/<org_slug>/microplanning/<opp_id>/`. Admin-only access.

| URL                         | View                      | Method | Description                     |
| --------------------------- | ------------------------- | ------ | ------------------------------- |
| `/`                         | `microplanning_home`      | GET    | Dashboard with map and metrics  |
| `/upload_work_areas/`       | `WorkAreaImport`          | GET    | Download CSV template           |
| `/upload_work_areas/`       | `WorkAreaImport`          | POST   | Upload CSV to import work areas |
| `/import_status/`           | `import_status`           | GET    | Poll async import task status   |
| `/tiles/<z>/<x>/<y>/`       | `WorkAreaTileView`        | GET    | Mapbox vector tiles (MVT)       |
| `/workareas_group_geojson/` | `workareas_group_geojson` | GET    | GeoJSON of group boundaries     |

## CSV Import

Work areas are bulk-imported via CSV upload. The import runs as an async Celery task with a cache-based lock to prevent concurrent imports for the same opportunity.

### CSV Columns

All columns are required. Column order is flexible.

| Column               | Format                        | Example                                 |
| -------------------- | ----------------------------- | --------------------------------------- |
| Area Slug            | Text (unique per opportunity) | `area-001`                              |
| Ward                 | Text                          | `ward-north`                            |
| Centroid             | `lon lat`                     | `77.5 28.6`                             |
| Boundary             | WKT Polygon                   | `POLYGON ((77.0 28.0, 77.1 28.0, ...))` |
| Building Count       | Non-negative integer          | `42`                                    |
| Expected Visit Count | Non-negative integer          | `10`                                    |
| Max WAG              | Text                          | `5`                                     |
| WAG Serial Number    | Text                          | `SN-001`                                |
| LGA                  | Text                          | `Lagos-Island`                          |
| State                | Text                          | `Lagos`                                 |

### Import Pipeline

1. **Upload**: POST CSV file to `/upload_work_areas/`
2. **Lock**: Cache lock prevents concurrent imports (1200s TTL)
3. **Validation pass**: All rows validated before any inserts. Errors returned with line numbers.
4. **Insert pass**: Batch `bulk_create` (5000 rows per batch)
5. **Cleanup**: Cache lock and uploaded CSV file are deleted

Use GET on `/upload_work_areas/` to download a template CSV with headers and an example row.

## Map Visualization

The dashboard renders work areas on a Mapbox map using:

- **Vector tiles (MVT)**: Served at `/tiles/<z>/<x>/<y>/` with min zoom level 6. Includes status, building count, group info, and assignee name per tile feature.
- **GeoJSON layer**: Aggregated group boundaries via PostGIS `Union` for group-level visualization.
- **Status colors**: Each status maps to Tailwind CSS classes defined in `const.py`.

## CommCare HQ Sync

Work areas sync to CommCare HQ as cases of type `work-area` (defined in `const.py`).

- **Serialization**: `WorkAreaCaseSerializer` converts a `WorkArea` to CommCare case format with properties like `bounding_box`, `centroid` (WKT), `wa_status`, `ward`, `building_count`, etc.
- **Sync trigger**: `create_or_update_case_by_work_area()` in `commcarehq/api.py` handles create/update with row-level locking.
- **Owner**: Set to the assigned user's HQ case (via ConnectID link or usercase lookup).
- **Case ID**: Stored in `WorkArea.case_id` after initial sync; subsequent calls update the existing case.

## Integration with Other Apps

| App               | Integration                                                                                        |
| ----------------- | -------------------------------------------------------------------------------------------------- |
| **opportunity**   | `UserVisit` has FK to `WorkArea`. `OpportunityAccess` referenced by `WorkAreaGroup.assigned_user`. |
| **commcarehq**    | Syncs work areas as HQ cases via `create_or_update_case_by_work_area()`.                           |
| **form_receiver** | Processes form submissions that reference work areas.                                              |
| **flags**         | `MICROPLANNING` flag gates all views at the opportunity level.                                     |

## Testing

Tests are in `tests/` using pytest + factory_boy.

- **`factories.py`**: `WorkAreaFactory`, `WorkAreaGroupFactory`
- **`test_views.py`**: Upload locking, flag enforcement, admin access, metrics edge cases
- **`test_serializers.py`**: Case serializer output format
- **`test_tasks.py`**: CSV validation (slug, boundary, centroid, numbers), duplicate detection, flexible column ordering

```bash
pytest commcare_connect/microplanning/
```
