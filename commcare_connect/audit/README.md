# Audit App - Simplified Plan

## Purpose

Local Django app for auditing FLW visit images. Review visits, mark pass/fail, export results as JSON. The current approach involves pulling data from Superset to populate uservisits and other database models for auditing purposes. Superset is connecting to our Connect's production instance. The code is being developed as a prototype for future production implementation.

## Setup

Once you have a local environment setup, you should only need to switch to this branch

## Core Components

### Models ✅

- `AuditSession`: Tracks audit of specific FLW/date range (uses text fields, not FKs)
- `AuditResult`: Individual visit pass/fail results

### Views ✅

- List/Create/Detail views for audit sessions
- AJAX endpoints for updating results
- JSON export functionality
- Image serving

### Management Commands

- `setup_audit_dependencies`: (Optional) Creates minimal database records from YAML config
  - Note: This is likely not needed if working with production data from Superset. Can be removed if not used.

**Note:** All data loading and clearing commands have been removed - the UI wizard handles everything through `/audit/create/`.

## Key URLs

```
/audit/                          # List sessions
/audit/create/                   # Create audit wizard (search programs, select opps, configure)
/audit/sessions/<id>/            # Main audit interface
/audit/sessions/<id>/export/     # Export JSON
/audit/api/database/reset/       # Clear audit data via API
/audit/oauth/connect/login/      # OAuth login for Connect production
```

## OAuth API Access

### Overview

The audit app supports two data sources:

1. **Superset** (default): Direct SQL queries to data warehouse
2. **OAuth API** (preferred): REST API access to connect.dimagi.com

When OAuth is configured, the app will automatically use the production APIs for data extraction, falling back to Superset only for data not available via API (e.g., blob metadata for images).

### Setup

#### 1. Create OAuth Application on connect.dimagi.com

1. Log into connect.dimagi.com as an admin
2. Navigate to Django Admin > OAuth2 Provider > Applications
3. Create a new application:
   - **Client Type**: Confidential
   - **Authorization Grant Type**: Authorization code
   - **Redirect URIs**: `https://your-audit-instance.example.com/audit/oauth/connect/callback/`
   - **Name**: "Audit App OAuth"
4. Note the **Client ID** and **Client Secret**

#### 2. Configure Environment Variables

Add these to your `.env` file:

```bash
# Connect Production OAuth
CONNECT_PRODUCTION_URL=https://connect.dimagi.com
CONNECT_OAUTH_CLIENT_ID=your_client_id_here
CONNECT_OAUTH_CLIENT_SECRET=your_client_secret_here
```

#### 3. Register Social Application in Django Admin

1. Navigate to Django Admin > Social Applications
2. Create new application:
   - **Provider**: connect
   - **Name**: CommCare Connect
   - **Client ID**: (same as OAuth app)
   - **Secret Key**: (same as OAuth app)
   - **Sites**: Select your site

#### 4. Authenticate

1. Visit `/audit/` (Audit Sessions list)
2. Click "Get Connect OAuth Token" button
3. Log into connect.dimagi.com when prompted
4. Authorize the application
5. You'll be redirected back with a green "Connected to Connect" badge

### What Data Comes from OAuth APIs?

When OAuth is configured, these operations use production APIs:

- ✓ **Program search** (`/export/opp_org_program_list/`)
- ✓ **Opportunity search** (`/export/opp_org_program_list/`)
- ✓ **Field worker listing** (`/export/opportunity/<id>/user_data/`)
- ✓ **Visit data download** (`/export/opportunity/<id>/user_visits/`)
- ✓ **Visit counts** (calculated from visit data API)
- ✓ **Form JSON** (included in visit data)

### Known Limitations

- ✗ **Blob metadata**: Image attachment metadata (BlobMeta) is not exposed via API
  - Images still need to be fetched from CommCare using existing attachment fetching
  - This is documented in download output: "Blob metadata not available via API"
- ✗ **Complex analytics**: Custom SQL queries for location analysis still use Superset

### OAuth Token Management

- Tokens are stored per-user in the `SocialToken` table
- Tokens expire after 2 weeks (configurable on OAuth server)
- Tokens auto-refresh when expired (if refresh token available)
- Users can see token expiration on the Audit Sessions page
- Required scope: `export` (enforced by API permission checks)

### Fallback Behavior

If OAuth is not configured or token is invalid:

- App automatically falls back to Superset for all operations
- No configuration changes needed
- Warning messages indicate fallback is being used

### Troubleshooting

**"No OAuth token available" error:**

- Click "Get Connect OAuth Token" button on `/audit/` page
- Ensure redirect URI matches exactly (including trailing slash)

**"Failed to refresh Connect OAuth token" error:**

- Re-authenticate by clicking the OAuth button again
- Check that Client ID and Secret are correct

**API returns 403 Forbidden:**

- Ensure user has access to the requested opportunities on connect.dimagi.com
- Verify OAuth app has `export` scope granted

## Data Flow (UI-Based Workflow)

1. Use audit creation wizard at `/audit/create/`
   - Search for programs
   - Select opportunities
   - Configure audit criteria (date range, per-FLW, last N, etc.)
   - Preview visit counts
2. System automatically downloads data from Superset and CommCare
3. Review visits with images, mark pass/fail
4. Export results as JSON

# CommCareApp Fallback CSV

commcare_app_fallback.csv

This file provides CommCareApp domain mapping that **SHOULD** be in Superset's `opportunity_commcareapp` table but is currently missing (the table has 0 rows in Superset)

## Fields

- **id**: The CommCareApp ID (matches `opportunity.deliver_app_id` in Superset)
- **cc_domain**: The CommCare project domain (e.g., "connect-experiments")
- **cc_app_id**: The CommCare application ID (32-character hex string)
- **name**: A friendly name for reference

# Notes to instruct AI on:

## Safe Styling colors to use for project that are included with tailwind:

text-green-600
text-slate-100
text-orange-600
text-violet-500
text-orange-600
text-indigo-700

bg-violet-500/20
bg-orange-600/20
bg-indigo-700/20
bg-green-600/20
bg-slate-100/20
bg-orange-600/20

bg-slate-100
gap-16

font-bold
border-red-500

## Connect Supserset Schema.csv

This the schema of all tables available in Superset. You need to check if the tables are empty however, as not everything is populated.
