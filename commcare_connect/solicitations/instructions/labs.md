# CommCare Connect Labs Environment

## Overview

The **CommCare Connect Labs** environment (`labs.connect.dimagi.com`) is a prototype/experimental environment that enables rapid iteration and testing of new features while sharing the production database. This approach allows for seamless "vibe coding" and stakeholder feedback without the complexity of data synchronization.

## Architecture

### Core Concept: Shared Database, Separate Application

```
┌─────────────────────┐    ┌─────────────────────┐
│  connect.dimagi.com │    │ labs.connect.dimagi │
│                     │    │                     │
│  Production Code    │    │  Experimental Code  │
│  Stable Features    │    │  Rapid Iteration    │
│  Same Session ──────┼────┼──── Same Session    │
│  Same Database ─────┼────┼──── Same Database   │
└─────────────────────┘    └─────────────────────┘
           │                           │
           └───────────┬───────────────┘
                       │
              ┌─────────────────┐
              │ Shared Database │
              │ - Users         │
              │ - Organizations │
              │ - Opportunities │
              │ - Solicitations │
              └─────────────────┘
```

### Key Benefits

- ✅ **Seamless Authentication**: Users logged into production are automatically authenticated in labs
- ✅ **Real Data Context**: Test with actual users, organizations, and opportunities
- ✅ **Zero Data Migration**: Features developed in labs persist to production database
- ✅ **Rapid Iteration**: Deploy experimental code without affecting production stability

## Authentication Flow

### Centralized Authentication Strategy

All authentication happens through the production environment to ensure session consistency:

1. **User visits `labs.connect.dimagi.com`** (unauthenticated)
2. **Automatic redirect** to `connect.dimagi.com/accounts/login/`
3. **User authenticates** via CommCare OAuth or standard login
4. **Production creates session** with shared cookie domain
5. **Redirect back** to `labs.connect.dimagi.com`
6. **Labs environment** recognizes shared session → user logged in

### Session Configuration

```python
# Both production.py and labs.py
SESSION_COOKIE_DOMAIN = '.dimagi.com'  # Shared across subdomains
SESSION_COOKIE_NAME = 'connect_sessionid'
CSRF_COOKIE_DOMAIN = '.dimagi.com'
```

## Data Safety & Write Restrictions

### Protected Models (Read-Only in Labs)

The labs environment has **read-only access** to critical production data:

- ❌ **Users**: Cannot create/modify user accounts
- ❌ **Organizations**: Cannot modify organizational structures
- ❌ **Opportunities**: Cannot change existing opportunities
- ❌ **OpportunityAccess**: Cannot modify user access permissions
- ❌ **Assessments/UserVisits**: Cannot modify work history

### Writable Models (Labs-Safe)

Labs environment can **create and modify** experimental features, in the case of Solicitations:

- ✅ **Solicitations**: New EOIs and RFPs for testing
- ✅ **SolicitationQuestions**: Custom form configurations
- ✅ **SolicitationResponses**: Organization responses
- ✅ **ResponseAttachments**: File uploads

### Implementation: Database Router

```python
class LabsDatabaseRouter:
    """
    Router that restricts labs environment writes to specific models
    """

    LABS_WRITABLE_MODELS = {
        'solicitations.solicitation',
        'solicitations.solicitationquestion',
        'solicitations.solicitationresponse',
        'solicitations.responseattachment',
    }

    def db_for_write(self, model, **hints):
        if settings.ENVIRONMENT == 'labs':
            model_key = f"{model._meta.app_label}.{model._meta.model_name}"
            if model_key not in self.LABS_WRITABLE_MODELS:
                raise PermissionError(
                    f"Labs environment cannot write to {model_key}. "
                    f"This operation is restricted to protect production data."
                )
        return super().db_for_write(model, **hints)
```

## Development Workflow

### Phase 1: Initial Feature Development

1. **Local Development**: Build solicitation features locally
2. **Deploy to Labs**: Push experimental code to `labs.connect.dimagi.com`
3. **Test with Real Data**: Use actual production users/orgs for testing
4. **Iterate Rapidly**: Deploy changes in minutes, not hours

### Phase 2: Stakeholder Feedback

2. **User Testing**: Let stakeholders create real solicitations
3. **Feedback Collection**: Gather input on workflows and UI
4. **Rapid Adjustments**: Implement feedback immediately

### Phase 3: Production Migration

1. **Code Review**: Mature features ready for production
2. **Merge to Main**: Move stable code to production codebase
3. **Production Deploy**: Features go live with existing data
4. **Seamless Transition**: No data migration needed

## Current Implementation Status

### ✅ Completed

- [x] Solicitation models and basic CRUD operations
- [x] Form builder for custom questions
- [x] Response submission and management
- [x] File attachment handling
- [x] Basic access controls and permissions

### 🚧 In Progress

- [ ] Labs environment configuration
- [ ] Database write restrictions
- [ ] Authentication redirect flow
- [ ] Cross-domain navigation

## Technical Implementation

### Environment Setup

```python
# config/settings/labs.py
from .production import *

# Environment identification
ENVIRONMENT = 'labs'

# Shared authentication
SESSION_COOKIE_DOMAIN = '.dimagi.com'
SESSION_COOKIE_NAME = 'connect_sessionid'
CSRF_COOKIE_DOMAIN = '.dimagi.com'

# Database write restrictions
DATABASE_ROUTERS = ["commcare_connect.multidb.db_router.LabsDatabaseRouter"]

# Labs-specific settings
ALLOWED_HOSTS = ["labs.connect.dimagi.com"]
CSRF_TRUSTED_ORIGINS = ["https://labs.connect.dimagi.com"]

# Disable login - redirect to production
LOGIN_URL = "https://connect.dimagi.com/accounts/login/"
```

### Deployment Configuration

```yaml
# deploy/config/deploy.labs.yml
service: connect-labs
image: connect-labs
servers:
  - labs.connect.dimagi.com
env:
  DJANGO_SETTINGS_MODULE: config.settings.labs
  DATABASE_URL: <same-as-production>
  ENVIRONMENT: labs
```

## Security Considerations

### Data Protection

- **Read-only access** to critical production models
- **Database-level enforcement** of write restrictions
- **Session security** maintained across domains
- **Audit logging** of labs environment activities

### Access Control

- **Same user permissions** as production environment
- **Organization-based access** to solicitations
- **Role-based features** for different user types
- **Admin oversight** of experimental features

### Rollback Strategy

- **Instant disable**: Can shut down labs environment immediately
- **Feature flags**: Toggle experimental features on/off
- **Data isolation**: Labs-created data clearly identified
- **Production stability**: Zero impact on production operations
