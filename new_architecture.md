# New Architecture: Production-Backed Audit App with Write OAuth

## Executive Summary

This document outlines an architectural approach to evolve the Audit app from a local-data model to a production-backed model, where audit workflows operate directly against production infrastructure while maintaining strict isolation from existing production data.

**Key Goals:**

1. Enable OAuth-based write access to production, scoped exclusively to Audit models
2. Store all audit data in production database (not local)
3. Allow rapid iteration on audit features without local data sync complexity
4. Maintain zero risk of corrupting existing production data models
5. Support local development while using production as the source of truth

### Security Model (TL;DR)

The security is **simple and straightforward**:

```
Local Code (AI-generated) → Production API Endpoints (reviewed) → Database
```

- **Local code has NO direct database access** - it can only call specific API endpoints via OAuth
- **Production API endpoints are explicitly scoped** - each viewset only touches one audit model
- **Code review is the enforcement mechanism of APIs** - production team verifies viewsets before deployment

This means you (JJ) can iterate rapidly on local features, while the production team ensures API endpoints are safely scoped through standard code review.

---

## Current State

### What Works Today

- ✅ OAuth read access to Connect production APIs
- ✅ Local audit app with local database models
- ✅ Data sync from Superset warehouse to local environment
- ✅ Audit workflow operates on local data copies

### Current Limitations

- ❌ Data sync is complex and brittle (Superset → local CSV → local DB)
- ❌ Local and production data can drift out of sync
- ❌ Cannot write audit results back to production
- ❌ Local-only workflow limits collaboration and data persistence
- ❌ Each developer needs their own data sync setup

---

## Proposed Architecture

### Core Principle: **API-Scoped Isolation**

The audit app will operate as a "protected workspace" within production, where:

- All audit models live in production database
- Production provides dedicated API endpoints for audit models only
- Each API endpoint (viewset) is explicitly scoped to a single audit model
- Local code uses production APIs for all audit CRUD operations (no direct DB access)
- API endpoints are code-reviewed to ensure they only touch audit models
- Existing production models remain inaccessible through the audit API endpoints

---

## Implementation Components

### 1. OAuth Write Grant (Production Side)

**New OAuth Scope: `audit:write`**

```python
# Production: config/settings/base.py
OAUTH2_PROVIDER = {
    'SCOPES': {
        'read': 'Read access to Connect data',
        'audit:write': 'Write access to Audit models only (isolated namespace)',
    },
    'ALLOWED_REDIRECT_URIS': [
        'http://localhost:8000/audit/oauth/connect/callback/',
        'https://staging.connect.dimagi.com/audit/oauth/connect/callback/',
    ],
}
```

### 2. Audit API Endpoints (Production Side)

**RESTful API for Audit Models:**

⚠️ **THIS IS THE SECURITY BOUNDARY** - These viewsets are explicitly scoped to audit models only.

```python
# Production: commcare_connect/audit/api/views.py
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from oauth2_provider.contrib.rest_framework import OAuth2Authentication

class AuditScopePermission(BasePermission):
    """Requires audit:write scope for write operations"""
    def has_permission(self, request, view):
        if request.method in ['POST', 'PUT', 'PATCH', 'DELETE']:
            return 'audit:write' in request.auth.scope
        return 'read' in request.auth.scope or 'audit:write' in request.auth.scope


class AuditDefinitionViewSet(viewsets.ModelViewSet):
    """
    API endpoint for AuditDefinition CRUD.

    SECURITY: This viewset ONLY touches AuditDefinition model.
    Code reviewers must verify:
    - queryset only queries AuditDefinition
    - perform_create/update don't touch other models
    - No accidental joins to non-audit tables
    """
    queryset = AuditDefinition.objects.all()  # ← ONLY AuditDefinition
    serializer_class = AuditDefinitionSerializer
    authentication_classes = [OAuth2Authentication, SessionAuthentication]
    permission_classes = [IsAuthenticated, AuditScopePermission]

    def perform_create(self, serializer):
        # Only creates AuditDefinition instances
        serializer.save(created_by=self.request.user)


class AuditSessionViewSet(viewsets.ModelViewSet):
    """
    API endpoint for AuditSession CRUD.
    SECURITY: Only touches AuditSession model.
    """
    queryset = AuditSession.objects.all()  # ← ONLY AuditSession
    serializer_class = AuditSessionSerializer
    authentication_classes = [OAuth2Authentication, SessionAuthentication]
    permission_classes = [IsAuthenticated, AuditScopePermission]


# Similar viewsets for:
# - AuditReaderViewSet (only touches AuditReader)
# - AuditVisitViewSet (only touches AuditVisit)
# - AuditResultViewSet (only touches AuditResult)
```

**Key Security Points:**

1. **Each viewset is explicitly scoped** to a single audit model
2. **Code review is the enforcement mechanism** - reviewers check the queryset and methods
3. **No generic endpoints** - each audit model gets its own dedicated viewset
4. **Local code cannot bypass** - it can only call these specific endpoints

**API URL Structure:**

```
Production API Endpoints:
  POST   /api/v1/audit/definitions/          # Create audit definition
  GET    /api/v1/audit/definitions/          # List audit definitions
  GET    /api/v1/audit/definitions/{id}/     # Get audit definition
  PUT    /api/v1/audit/definitions/{id}/     # Update audit definition
  DELETE /api/v1/audit/definitions/{id}/     # Delete audit definition

  POST   /api/v1/audit/sessions/             # Create audit session
  GET    /api/v1/audit/sessions/             # List audit sessions
  GET    /api/v1/audit/sessions/{id}/        # Get audit session

  POST   /api/v1/audit/visits/               # Create audit visit
  GET    /api/v1/audit/visits/               # List audit visits
  PATCH  /api/v1/audit/visits/{id}/          # Update audit visit

  POST   /api/v1/audit/results/              # Submit audit result
  GET    /api/v1/audit/results/              # List audit results
```

### 3. Audit Data Models (Production Database)

**Model Migration Strategy:**

```python
# Production PR: commcare_connect/audit/models.py

class AuditDefinition(models.Model):
    """
    Audit Definition model.

    Security: API endpoints (AuditDefinitionViewSet) are scoped to only touch this model.
    This is enforced through code review of the viewset.
    """
    class Meta:
        app_label = 'audit'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    name = models.CharField(max_length=255)
    opportunity = models.ForeignKey(
        'opportunity.Opportunity',  # Read-only FK reference
        on_delete=models.PROTECT,
        related_name='audit_definitions'
    )
    # ... rest of fields

    created_by = models.ForeignKey(User, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    # Optional: Add validation if you want defense-in-depth
    # def save(self, *args, **kwargs):
    #     AuditModelValidator.validate_model_is_audit(self.__class__)
    #     super().save(*args, **kwargs)


class AuditSession(models.Model):
    """Audit Session model - accessed via AuditSessionViewSet API"""
    class Meta:
        app_label = 'audit'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    # ... fields


class AuditReader(models.Model):
    """Stores reader assignments for audit sessions"""
    class Meta:
        app_label = 'audit'

    # ... fields


class AuditVisit(models.Model):
    """Audit Visit model - accessed via AuditVisitViewSet API"""
    class Meta:
        app_label = 'audit'

    # ... fields


class AuditResult(models.Model):
    """Audit Result model - accessed via AuditResultViewSet API"""
    class Meta:
        app_label = 'audit'

    # ... fields
```

**Foreign Key Strategy:**

- **Audit models CAN reference production models** (Opportunity, User, etc.) via ForeignKey
- **Read-only references**: Audit models can read from production models for FK lookups
- **Write isolation**: API endpoints are scoped to audit models only
- **Enforcement**: Code review ensures viewsets don't modify referenced production models

**Adding New Audit Models:**

When you need to add a new model to the audit system:

```python
# Step 1: Define the model in commcare_connect/audit/models.py
class AuditNewModel(models.Model):
    """New audit model - accessed via AuditNewModelViewSet API"""
    class Meta:
        app_label = 'audit'

    # ... fields ...

# Step 2: Create and run migration
python manage.py makemigrations audit
python manage.py migrate audit

# Step 3: Create API endpoint (THIS IS THE SECURITY BOUNDARY)
class AuditNewModelViewSet(viewsets.ModelViewSet):
    """
    API endpoint for AuditNewModel.

    Security: This viewset is ONLY allowed to touch AuditNewModel.
    Code review must verify it doesn't accidentally query other models.
    """
    queryset = AuditNewModel.objects.all()
    serializer_class = AuditNewModelSerializer
    authentication_classes = [OAuth2Authentication, SessionAuthentication]
    permission_classes = [IsAuthenticated, AuditScopePermission]
```

**Important**: The security comes from code review of the viewset. Reviewers must verify the viewset only touches audit models.

### 4. Local Development Client (This Codebase)

**Production API Client:**

```python
# Local: commcare_connect/audit/production_client.py
import httpx
from django.conf import settings
from allauth.socialaccount.models import SocialToken

class ProductionAuditClient:
    """
    Client for interacting with production Audit API.
    Uses OAuth token with audit:write scope.
    """

    def __init__(self, user):
        self.user = user
        self.base_url = settings.CONNECT_PRODUCTION_URL
        self.token = self._get_oauth_token()
        self.client = httpx.Client(
            base_url=f"{self.base_url}/api/v1/audit/",
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=30.0
        )

    def _get_oauth_token(self):
        """Get OAuth token with audit:write scope"""
        token = SocialToken.objects.get(
            account__user=self.user,
            account__provider='connect'
        )
        # Verify scope includes audit:write
        if 'audit:write' not in token.scope:
            raise PermissionError("Token missing audit:write scope")
        return token.token

    # CRUD Methods
    def create_audit_definition(self, data):
        """POST /definitions/"""
        response = self.client.post("definitions/", json=data)
        response.raise_for_status()
        return response.json()

    def get_audit_definitions(self, **filters):
        """GET /definitions/"""
        response = self.client.get("definitions/", params=filters)
        response.raise_for_status()
        return response.json()

    def create_audit_session(self, data):
        """POST /sessions/"""
        response = self.client.post("sessions/", json=data)
        response.raise_for_status()
        return response.json()

    def submit_audit_result(self, data):
        """POST /results/"""
        response = self.client.post("results/", json=data)
        response.raise_for_status()
        return response.json()

    # ... more methods
```

**Local Views (Thin Layer):**

```python
# Local: commcare_connect/audit/views.py
from .production_client import ProductionAuditClient

@login_required
def create_audit_definition(request):
    """
    Local view - delegates to production API.
    No local database writes for audit models.
    """
    if request.method == 'POST':
        form = AuditDefinitionForm(request.POST)
        if form.is_valid():
            client = ProductionAuditClient(request.user)

            # Create in production, not locally
            audit_def = client.create_audit_definition(
                form.cleaned_data
            )

            messages.success(
                request,
                f"Audit definition '{audit_def['name']}' created in production"
            )
            return redirect('audit:definition_list')
    else:
        form = AuditDefinitionForm()

    return render(request, 'audit/definition_form.html', {'form': form})


@login_required
def audit_session_list(request):
    """
    List audit sessions from production.
    """
    client = ProductionAuditClient(request.user)
    sessions = client.get_audit_sessions(
        created_by=request.user.id
    )

    return render(request, 'audit/session_list.html', {
        'sessions': sessions
    })
```

---

## Safety Mechanisms

### Core Security Boundary

**Local Code → API Endpoints → Database**

The key insight: **Local code has NO direct database access**. It only calls production API endpoints via OAuth. Therefore, the security boundary is actually quite simple:

1. **API Endpoint Scoping** (Primary Defense)

   - Each viewset explicitly operates on specific audit models only
   - E.g., `AuditDefinitionViewSet` only touches `AuditDefinition`
   - API code is reviewed before deployment
   - Local code cannot access database directly - only through these endpoints

2. **OAuth Scope** (Access Control)
   - `audit:write` scope grants access to audit API endpoints only
   - Token cannot be used to call other production APIs
   - Standard OAuth2 token validation

---
