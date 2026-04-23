# Opportunity Automation API Design

## Goal

Provide a set of REST API endpoints that allow full automation of the managed opportunity creation pipeline: from program creation through opportunity setup to user invitation. Designed for internal automation scripts authenticated as existing admin users.

## Endpoints

All endpoints live under `/api/` and use token authentication (`IsAuthenticated`). The authenticated user must be an admin member of the relevant organization. Program-related endpoints additionally require `organization.program_manager = True`.

### 1. `POST /api/programs/`

Create a new program.

**Request body:**
```json
{
  "name": "Malaria Prevention Program",
  "description": "Community health worker malaria prevention",
  "organization": "dimagi",
  "delivery_type": "health-services",
  "budget": 500000,
  "currency": "MWK",
  "country": "Malawi",
  "start_date": "2026-05-01",
  "end_date": "2026-12-31"
}
```

- `organization`: org slug (must exist, caller must be admin, org must have `program_manager=True`)
- `delivery_type`: DeliveryType slug
- `currency`: Currency code
- `country`: Country name

**Response (201):**
```json
{
  "program_id": "uuid",
  "name": "Malaria Prevention Program",
  "slug": "malaria-prevention-program",
  "description": "...",
  "organization": "dimagi",
  "delivery_type": "health-services",
  "budget": 500000,
  "currency": "MWK",
  "country": "Malawi",
  "start_date": "2026-05-01",
  "end_date": "2026-12-31"
}
```

**Validation:**
- `end_date` must be after `start_date`
- Organization must exist and caller must be an admin member
- Organization must have `program_manager=True`

---

### 2. `POST /api/programs/{program_id}/applications/`

Invite an organization to the program.

**Request body:**
```json
{
  "organization": "partner-org-slug"
}
```

**Response (201):**
```json
{
  "program_application_id": "uuid",
  "program": "program-uuid",
  "organization": "partner-org-slug",
  "status": "invited"
}
```

**Permission:** Caller must be admin of the program's owning organization.

---

### 3. `POST /api/programs/{program_id}/applications/{program_application_id}/accept/`

Accept an organization's application/invitation. This is a shortcut for internal automation — it bypasses the flow where the invited org must log in and accept.

**Request body:** None

**Response (200):**
```json
{
  "program_application_id": "uuid",
  "program": "program-uuid",
  "organization": "partner-org-slug",
  "status": "accepted"
}
```

**Validation:**
- Application must be in `invited` or `applied` status

**Permission:** Caller must be admin of the program's owning organization.

---

### 4. `POST /api/programs/{program_id}/opportunities/`

Create a managed opportunity under a program. App registration happens inline — the caller provides HQ identifiers and the API creates CommCareApp records, validates them against HQ, and **synchronously** fetches app metadata (LearnModules and DeliverUnits) so they are available in the response.

**Request body:**
```json
{
  "name": "Malaria Prevention - Lilongwe",
  "description": "Web description for the opportunity",
  "short_description": "Mobile app description",
  "organization": "partner-org-slug",
  "start_date": "2026-05-01",
  "end_date": "2026-12-31",
  "total_budget": 100000,
  "learn_app": {
    "hq_server_url": "https://www.commcarehq.org",
    "api_key": "abc123...",
    "cc_domain": "malaria-project",
    "cc_app_id": "abc123def456",
    "description": "Learn about malaria prevention techniques",
    "passing_score": 80
  },
  "deliver_app": {
    "hq_server_url": "https://www.commcarehq.org",
    "api_key": "abc123...",
    "cc_domain": "malaria-project",
    "cc_app_id": "xyz789ghi012"
  }
}
```

- `organization`: must be an ACCEPTED member of this program
- `hq_server_url`: URL of an existing HQServer record (e.g., `https://www.commcarehq.org`)
- `api_key`: the API key string of an existing HQApiKey record
- `cc_domain` and `cc_app_id`: validated against CommCare HQ to confirm the app exists
- Learn and deliver apps must be different (`cc_app_id` must differ)
- `start_date` / `end_date`: must be within the program's date range, end after start
- `total_budget`: combined with other managed opportunities' budgets, must not exceed program budget

**Response (201):**
```json
{
  "id": 42,
  "opportunity_id": "uuid",
  "name": "Malaria Prevention - Lilongwe",
  "description": "...",
  "short_description": "...",
  "organization": "partner-org-slug",
  "managed": true,
  "program_id": "program-uuid",
  "learn_app": {
    "cc_domain": "malaria-project",
    "cc_app_id": "abc123def456",
    "name": "Malaria Learn App",
    "learn_modules": [
      {"id": 1, "slug": "module-1", "name": "Introduction"}
    ]
  },
  "deliver_app": {
    "cc_domain": "malaria-project",
    "cc_app_id": "xyz789ghi012",
    "name": "Malaria Deliver App",
    "deliver_units": [
      {"id": 1, "slug": "household_visit", "name": "Household Visit"},
      {"id": 2, "slug": "referral_form", "name": "Referral Form"}
    ]
  },
  "start_date": "2026-05-01",
  "end_date": "2026-12-31",
  "total_budget": 100000,
  "currency": "MWK",
  "country": "Malawi",
  "active": false
}
```

**Side effects:**
- Creates CommCareApp records for learn and deliver apps
- Sets `currency`, `country`, and `delivery_type` from the parent program
- Synchronously fetches and creates LearnModule and DeliverUnit records from the app XML (reuses the same logic as `create_learn_modules_and_deliver_units` but called directly, not via Celery)

**Errors:**
- Returns 400 if HQ validation fails (app not found, domain inaccessible, etc.)
- Returns 502 if HQ is unreachable

---

### 5. `POST /api/opportunities/{opportunity_id}/payment_units/`

Add one or more payment units to an opportunity. Accepts a list so multiple can be created in one call.

**Request body:**
```json
{
  "payment_units": [
    {
      "name": "Household Visit",
      "description": "Complete a household malaria assessment",
      "amount": 500,
      "org_amount": 100,
      "max_total": 50,
      "max_daily": 10,
      "required_deliver_units": [1, 2],
      "optional_deliver_units": [3],
      "start_date": null,
      "end_date": null
    }
  ]
}
```

- `amount`: worker payment per visit (required)
- `org_amount`: organization payment per visit (required for managed opportunities)
- `max_total`: maximum visits per user (required)
- `max_daily`: maximum visits per day (required)
- `required_deliver_units`: list of DeliverUnit IDs — all must be submitted for payment accrual
- `optional_deliver_units`: list of DeliverUnit IDs — any one (combined with required) accrues payment
- `start_date`/`end_date`: optional overrides (defaults to opportunity dates)

**Response (201):**
```json
{
  "payment_units": [
    {
      "id": 1,
      "payment_unit_id": "uuid",
      "name": "Household Visit",
      "description": "...",
      "amount": 500,
      "org_amount": 100,
      "max_total": 50,
      "max_daily": 10,
      "required_deliver_units": [1, 2],
      "optional_deliver_units": [3],
      "start_date": null,
      "end_date": null
    }
  ]
}
```

**Validation:**
- DeliverUnit IDs must belong to the opportunity's deliver app
- If payment unit `end_date` is set, it must be after `start_date`
- `org_amount` is required when `opportunity.managed = True`

**Permission:** Caller must be admin of the opportunity's organization or admin of the program's owning organization.

---

### 6. `POST /api/opportunities/{opportunity_id}/activate/`

Activate the opportunity after payment units have been added. No request body required.

**Request body:** None

**Response (200):**
```json
{
  "id": 42,
  "opportunity_id": "uuid",
  "active": true
}
```

**Validation:**
- At least one PaymentUnit must exist
- Opportunity must not already be active

**Permission:** Caller must be admin of the opportunity's organization or admin of the program's owning organization.

---

### 7. `POST /api/opportunities/{opportunity_id}/invite_users/`

Invite users by phone number. Triggers the existing async invitation pipeline (ConnectID lookup, OpportunityAccess creation, SMS, push notification).

**Request body:**
```json
{
  "phone_numbers": ["+265999111222", "+265999333444"]
}
```

**Response (202):**
```json
{
  "invited_count": 2,
  "message": "User invitations are being processed"
}
```

Returns 202 Accepted because invitations are processed asynchronously via the existing `add_connect_users` Celery task.

**Validation:**
- Opportunity must be active and not ended
- Phone numbers must be non-empty strings

**Permission:** Caller must be admin of the opportunity's organization or admin of the program's owning organization.

---

## Authentication and Permissions

All endpoints use `rest_framework.authentication.TokenAuthentication` combined with `IsAuthenticated`, matching the existing API pattern.

A custom permission class `IsOrganizationAdmin` checks that the authenticated user is an admin member of the relevant organization. For program endpoints, this is the program-owning org. For managed opportunity endpoints, this can be either the program-owning org or the network manager org (depending on the endpoint).

For program creation specifically, an additional check verifies `organization.program_manager = True`.

## URL Registration

New endpoints are registered in `config/api_router.py` alongside the existing API routes. Program-scoped endpoints use `program_id` (UUID) as the path parameter. Opportunity-scoped endpoints use `opportunity_id` (UUID).

## Serializers

New serializers are created for the automation API, separate from the existing mobile-facing serializers:

- `ProgramCreateSerializer` — validates and creates Program
- `ProgramApplicationSerializer` — reads/creates ProgramApplication
- `ManagedOpportunityCreateSerializer` — validates app data, creates ManagedOpportunity + CommCareApps, synchronously fetches metadata
- `PaymentUnitCreateSerializer` — validates and creates PaymentUnit with deliver unit linkage
- `ManagedOpportunityCreateSerializer` — validates app data, dates/budget constraints, creates opportunity + CommCareApps
- `UserInviteSerializer` — accepts phone numbers list

These are placed in a new `commcare_connect/opportunity/api/automation_serializers.py` to keep them separate from the existing mobile API serializers. Views go in `commcare_connect/opportunity/api/automation_views.py`.

## Error Response Format

All validation errors use DRF's standard format:

```json
{
  "field_name": ["Error message"],
  "non_field_errors": ["Error message"]
}
```

With appropriate HTTP status codes: 400 for validation errors, 403 for permission errors, 404 for not found, 502 for upstream HQ failures.

## Typical Automation Flow

```
1. POST /api/programs/                                          → program_id
2. POST /api/programs/{program_id}/applications/                → application_id
3. POST /api/programs/{program_id}/applications/{id}/accept/    → accepted
4. POST /api/programs/{program_id}/opportunities/               → opportunity_id + deliver_units (includes dates/budget)
5. POST /api/opportunities/{opportunity_id}/payment_units/      → payment_unit_ids
6. POST /api/opportunities/{opportunity_id}/activate/           → active opportunity
7. POST /api/opportunities/{opportunity_id}/invite_users/       → invitations queued
```

## Out of Scope

- Organization CRUD (orgs assumed to exist)
- HQServer / HQApiKey CRUD (infrastructure, assumed to exist)
- Non-managed opportunity creation (can be added later using the same patterns)
- GET/LIST/UPDATE/DELETE for the above resources (can be added incrementally)
- Rate limiting, OAuth2 scopes (not needed for internal automation)
