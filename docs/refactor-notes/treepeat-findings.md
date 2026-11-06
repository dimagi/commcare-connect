# Treepeat duplicate-code findings

Command, run from the repo root on 2026-10-19:

```bash
.venv/bin/treepeat detect . -s 90 \
  -i '**/tests/**,**/test_*.py,**/conftest.py,**/migrations/**,**/static/bundles/**,**/node_modules/**,.venv/**,**/.venv/**,**/staticfiles/**'
```

Treepeat found 70 groups at 90% similarity or higher. Most of them are in CSS and YAML. This file lists only the Python and JavaScript groups.

## Worth refactoring

| Similarity | Locations | What |
| --- | --- | --- |
| 94–97% | `commcare_connect/ocs_provider/views.py:7`, `commcare_connect/commcarehq_provider/views.py:7` | `OcsOAuth2Adapter` and `CommcareHQOAuth2Adapter`, including `__init__` and `complete_login` |
| 95% | `commcare_connect/program/tasks.py:25`, `:50` | `send_program_invite_applied_email` and `send_program_invite_email` |
| 91% | `commcare_connect/opportunity/tasks.py:929`, `:951`, `commcare_connect/microplanning/tasks.py:511` | `send_task_assignment_notification`, `send_task_completion_notification`, `send_work_area_assignment_notification` |
| 92% | `commcare_connect/static/js/dashboard.js:197`, `:257` | `createProgramPieChart` and `createStatusPieChart` (about 60 lines each) |
| 91–94% | `commcare_connect/microplanning/tasks.py:375`, `:452` (and `:329`, `:418`) | `import_work_areas_task` and `import_implementation_areas_task`, plus `_validate_slug` and `_validate_name` |
| 95% | `commcare_connect/opportunity/forms.py:367`, `:595` | Two copies of `_add_supervising_organization_field` |
| 100% | `commcare_connect/opportunity/forms.py:2228`, `:2443` | Two identical `clean_due_date` methods |
| 100% | `commcare_connect/opportunity/models.py:172`, `commcare_connect/program/models.py:45` | Two identical `currency_code` methods |
| 100% | `commcare_connect/opportunity/models.py:963`, `:1152` | Two identical `__setattr__` methods |
| 100% | `commcare_connect/opportunity/models.py:63`, `commcare_connect/organization/models.py:15` | `DeliveryType` and `PrimarySector` classes have the same structure |
| 96% | `commcare_connect/microplanning/tables.py:84`, `:97` | `CoverageWardTable` and `CoverageWAGTable` |
| 96–97% | `commcare_connect/microplanning/views.py:702`, `:756` | `WorkAreaTileView` and `UserVisitTileView`, including `get_layers` |
| 92% | `commcare_connect/data_export/views.py:270`, `:284` | `_get_program_or_404` and `_get_org_or_404` |
| 90.5% | `commcare_connect/opportunity/admin.py:227`, `commcare_connect/program/admin.py:21`, `:39` | `OpportunitySupervisingOrganizationEventAdmin`, `ProgramFunderEventAdmin`, `ProgramWatcherEventAdmin` |
| 90% | `commcare_connect/organization/merge.py:257`, `:283` | `_merge_program_applications` and `_merge_memberships` |

## Probably fine as they are

These are short, parallel helpers. Combining them would likely make the code harder to read.

| Similarity | Locations | What |
| --- | --- | --- |
| 92.5% | `commcare_connect/opportunity/tasks.py:232`, `:240`, `:248`, `:269`, `:609` | Five 5-line `generate_*_export` tasks |
| 98% | `commcare_connect/organization/decorators.py:194`, `:202`, `:210`, `:218` | Four access mixins |
| 93% | `commcare_connect/users/middleware.py:36`, `:48` | `_is_opportunity_pm` and `_opportunity_access_level` |
| 93.5% | `commcare_connect/organization/forms.py:148`, `:155` | `clean_contact_emails` and `clean_eoi_links` |
| 95% | `commcare_connect/program/api/serializers.py:319`, `:328` | `get_learn_app` and `get_deliver_app` |
| 92% | `commcare_connect/program/api/serializers.py:30`, `:69` | Two `Meta` classes |
| 98% | `commcare_connect/opportunity/helpers.py:254`, `:266` | `completed_work_status_subquery` and `completed_work_status_total_subquery` |
| 91% | `commcare_connect/opportunity/visit_import.py:90`, `:96` | `get_missing_message` and `get_locked_message` |
| 97.5% | `commcare_connect/opportunity/visit_import.py:522`, `:625` | `bulk_update_completed_work_status` and `bulk_update_visit_review_status` |
| 91% | `commcare_connect/opportunity/tables.py:688`, `:744` | Two `Meta` classes |
| 97% | `commcare_connect/opportunity/tables.py:109`, `:357` | `render_details` and `render_user_visit` |
| 98% | `commcare_connect/opportunity/views.py:408`, `:440` | Two `get_context_data` methods |
| 93% | `commcare_connect/opportunity/management/commands/cleanup_historical_exports.py:33`, `commcare_connect/users/management/commands/backfill_hq_user_uuid.py:40` | Two `add_arguments` methods |
| 90% | `commcare_connect/data_export/serializer.py:271`, `:304` | `AssessmentDataSerializer` and `CompletedModuleDataSerializer` |
| 93% | `commcare_connect/opportunity/models.py:417`, `commcare_connect/microplanning/models.py:42` | `visit_count` and `building_count` |
| 95% | `commcare_connect/microplanning/tables.py:89`, `:102` | Two `__init__` methods |
| 97% | `commcare_connect/static/js/work_area_search.js:32`, `:43` | `renderOption` and `renderItem` |

## False positives

- `docs/superpowers/specs/2026-08-17-program-access-redesign-design.md` matches `commcare_connect/opportunity/forms.py:353` and `commcare_connect/program/helpers.py:51`, because the spec quotes the code it describes.
- `commcare_connect/jira_service_desk/tests.py` got through the test filter because it is named `tests.py`. To exclude it, add `**/tests.py` to the ignore list.
