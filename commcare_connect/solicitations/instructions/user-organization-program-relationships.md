# User, Organization, and Program Relationships in CommCare Connect

## Overview

CommCare Connect has a hierarchical structure where Users belong to Organizations, and Organizations can manage Programs. There are two distinct organizational roles that determine access and capabilities.

## Core Model Relationships

```
User ←→ UserOrganizationMembership ←→ Organization ←→ Program
```

### User Model

- Basic user account (extends Django's AbstractUser)
- Can belong to multiple organizations
- Has phone number, email, name fields

### Organization Model

- Represents a partner organization
- Has a `program_manager` boolean field (key differentiator)
- Can have multiple users as members
- Can own multiple programs

### UserOrganizationMembership (Through Model)

- Links Users to Organizations
- Defines user's role within that organization:
  - **ADMIN** - Can manage the organization
  - **MEMBER** - Regular member
  - **VIEWER** - Read-only access
- Has `accepted` field for invitation workflow

### Program Model

- Belongs to exactly one Organization
- Has budget, timeline, delivery type
- Can have multiple ManagedOpportunities

## Two Types of Organizations

### 1. Program Manager Organizations

```python
organization.program_manager = True
```

**Who they are:**

- Organizations that CREATE and MANAGE programs
- Usually larger organizations or funders
- Have oversight of program implementation

**What they can do:**

- Create new programs
- Create solicitations for their programs
- Manage program applications from Network Managers
- Oversee all opportunities within their programs
- Review and approve Network Manager applications

**User Requirements:**

- Must be an **ADMIN** in a Program Manager organization
- `membership.is_program_manager` returns `True` (combines org.program_manager + membership.is_admin)

### 2. Network Manager Organizations

```python
organization.program_manager = False  # Default
```

**Who they are:**

- Organizations that IMPLEMENT programs on the ground
- Local partners, NGOs, service delivery organizations
- Apply to participate in programs created by Program Managers

**What they can do:**

- Apply to join programs (via ProgramApplication)
- Respond to solicitations
- Manage opportunities assigned to them
- Implement program activities once accepted

**User Requirements:**

- Can be ADMIN, MEMBER, or VIEWER
- Different access levels within their organization

## Permission Hierarchy

### Program Manager Permissions

```python
def is_program_manager(request):
    return (
        request.org.program_manager and
        request.org_membership.is_admin
    ) or request.user.is_superuser
```

**Can access:**

- Program creation and management
- Solicitation creation and management
- Program dashboard with all network managers
- Review applications from Network Managers
- Oversight of all program activities

### Network Manager Permissions

```python
# Any member of a non-program-manager organization
request.org_membership is not None
```

**Can access:**

- Apply to programs
- Respond to solicitations
- Manage assigned opportunities
- View their own organization's activities

## Program Application Workflow

1. **Program Manager** creates a Program
2. **Program Manager** creates Solicitations for the Program
3. **Network Managers** respond to Solicitations
4. **Program Manager** reviews responses and creates ProgramApplications
5. **Network Managers** can accept/decline invitations
6. **Accepted Network Managers** can then manage ManagedOpportunities within the Program

## Database Relationships

```sql
-- User belongs to multiple organizations
User
├── UserOrganizationMembership (role: admin/member/viewer)
    └── Organization (program_manager: true/false)
        └── Program
            ├── Solicitation (created by Program Manager)
            │   └── SolicitationResponse (from Network Managers)
            ├── ProgramApplication (Network Manager applies)
            └── ManagedOpportunity (assigned to Network Manager)
```

## UI Differences

### Program Manager Home (`pm_home.html`)

- Shows all programs they manage
- Create new programs
- Manage solicitations
- Review applications
- Full oversight dashboard

### Network Manager Home (`nm_home.html`)

- Shows programs they've applied to or are accepted in
- Apply to new programs
- Respond to solicitations
- Manage assigned opportunities
- Activity focused on their implementation work

## Key Properties and Methods

### UserOrganizationMembership

```python
@property
def is_admin(self):
    return self.role == self.Role.ADMIN

@property
def is_program_manager(self):
    return self.organization.program_manager and self.is_admin
```

### Organization

```python
# Boolean flag that determines organization type
program_manager = models.BooleanField(default=False)
```

## Access Control Patterns

### Program Management Views

```python
class ProgramManagerMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return (
            self.request.org_membership.is_admin and
            self.request.org.program_manager
        ) or self.request.user.is_superuser
```

### General Organization Views

```python
class SolicitationAccessMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return (
            self.request.org_membership is not None
        ) or self.request.user.is_superuser
```

## Summary

- **Program Managers**: Create and oversee programs, manage solicitations, approve network partners
- **Network Managers**: Apply to programs, respond to solicitations, implement activities on the ground
- **The `organization.program_manager` flag** is the key differentiator between these two organizational types
- **User role within organization** (admin/member/viewer) determines specific permissions within that context
