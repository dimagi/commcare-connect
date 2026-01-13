# Solicitations App: Access & Security Overview

## Current Security Implementation Analysis

### Intended Business Rules

1. **Only Program Managers can create solicitations** - tied to programs their organization owns
2. **Any user can respond to a solicitation** - as long as they are a member of an organization
3. **Only Program Managers can review responses** - to solicitations their organization published
4. **Super users can do any of these things**

---

## Current Implementation

### Organization Context Management ✅ **WELL DESIGNED**

The system uses URL-based organization selection with middleware support:

**URL Structure**: `/a/<org_slug>/solicitations/` - Users explicitly choose which organization they're acting as

**Middleware Logic** (`users/middleware.py`):

```python
def get_organization_for_request(request, view_kwargs):
    org_slug = view_kwargs.get("org_slug", None)  # From URL
    if org_slug:
        return Organization.objects.get(slug=org_slug)
    # Fallback to user's primary organization
    return request.user.memberships.first().organization
```

**Request Context**:

- `request.org` = Active organization (from URL or default)
- `request.org_membership` = User's membership in active org
- `request.memberships` = User's other memberships

### Security Mixins

#### `SolicitationAccessMixin` ✅ **CORRECTLY IMPLEMENTED**

```python
def test_func(self):
    return self.request.org_membership != None or self.request.user.is_superuser
```

- **Purpose**: Ensures user has membership in the active organization
- **Status**: ✅ Works correctly with middleware-provided context

#### `SolicitationManagerMixin` ✅ **CORRECTLY IMPLEMENTED**

```python
def test_func(self):
    org_membership = getattr(self.request, "org_membership", None)
    is_admin = getattr(org_membership, "is_admin", False)
    org = getattr(self.request, "org", None)
    program_manager = getattr(org, "program_manager", False)
    return (org_membership is not None and is_admin and program_manager) or self.request.user.is_superuser
```

- **Purpose**: Ensures user is admin of active program manager organization
- **Status**: ✅ Correctly implements the business rule

---

## Current View Security Mapping

### Views Using `SolicitationManagerMixin` (Program Manager Required)

- `ProgramSolicitationDashboard` - View program's solicitations
- `SolicitationResponseTableView` - View responses to a specific solicitation
- `SolicitationResponseAndReviewTable` - Combined table showing responses with review data on dashboard
- `SolicitationCreateOrUpdate` - Create/edit solicitations
- `SolicitationResponseReviewCreateOrUpdate` - Review responses

### Views Using `SolicitationAccessMixin` (Organization Member Required)

- `SolicitationResponseSuccessView` - View response success page
- `SolicitationResponseDetailView` - View own response details
- `SolicitationResponseCreateOrUpdate` - Create/edit responses
- `SolicitationResponseDraftListView` - View own draft responses
- `UserSolicitationDashboard` - User dashboard

### Views Using `SuperUserRequiredMixin`

- `AdminSolicitationOverview` - Admin overview of all solicitations

### Public Views (No Authentication Required)

- `PublicSolicitationListView` - Public list of solicitations
- `PublicSolicitationDetailView` - Public solicitation details

---

## Identified Issues (Corrected Analysis)

### 1. **Redundant Permission Checking** ⚠️ **NEEDS FIXING**

- `SolicitationResponseCreateOrUpdate` uses both `SolicitationAccessMixin` AND `calculate_response_permissions()`
- Double-checking organization membership in multiple places
- Conflicting error handling between mixin and helper function
- **Impact**: Code complexity, potential for inconsistent behavior

### 2. **Program Ownership Verification Gap** ⚠️ **NEEDS FIXING**

#### Rule 1 Gap: Program Manager Creation Rights

- Current: `SolicitationManagerMixin` checks if user is admin of the active program manager org
- **Problem**: Doesn't verify the user can create solicitations for the SPECIFIC program in the request
- **Gap**: User could potentially create solicitations for programs owned by other organizations
- **Example**: User from Org A (program manager) could create solicitation for Org B's program

#### Rule 3 Gap: Response Review Rights

- Current: `SolicitationResponseReviewCreateOrUpdate` uses `SolicitationManagerMixin`
- **Problem**: Doesn't verify the solicitation belongs to the user's active organization
- **Gap**: Program manager from Org A could review responses to Org B's solicitations

### 3. **Unnecessary Helper Function Complexity** ⚠️ **NEEDS CLEANUP**

- `calculate_response_permissions()` duplicates mixin logic
- `get_user_organization_context()` only used once
- Complex return values that calling code doesn't handle properly
- **Impact**: Code is harder to understand and maintain

---

## Security Logic Problems

### `calculate_response_permissions()` Issues

1. **Redundant checking**: Already handled by `SolicitationAccessMixin`
2. **Inconsistent return values**: Mix of redirect/no-redirect cases
3. **Organization check**: Returns `"organization_required"` but calling code doesn't handle it properly
4. **Complex logic**: Tries to do too much in one function

### Multiple User Organization Memberships

- Current implementation assumes single organization per user
- Uses `user.memberships.first().organization` throughout codebase
- **Problem**: User can have multiple org memberships but code only considers first one

---

## Recommendations (Corrected)

### 1. **Keep Current Mixins** ✅ **THEY'RE GOOD**

The current mixins are well-designed and work correctly with the middleware:

- `SolicitationAccessMixin` - ✅ Keep as-is
- `SolicitationManagerMixin` - ✅ Keep as-is

### 2. **Add Program/Solicitation Ownership Verification**

#### Add Per-View Ownership Checks:

```python
class SolicitationCreateOrUpdate(SolicitationManagerMixin, UpdateView):
    def get_object(self, queryset=None):
        # Existing code...
        if pk:
            obj = get_object_or_404(Solicitation, pk=pk)
            # NEW: Verify solicitation belongs to active org
            if obj.program.organization != self.request.org:
                raise Http404("You can only edit solicitations for your organization")
            return obj
        return None

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if not self.object:  # Create mode
            program_pk = self.kwargs.get("program_pk")
            program = get_object_or_404(Program, pk=program_pk)
            # NEW: Verify program belongs to active org
            if program.organization != self.request.org:
                raise Http404("You can only create solicitations for your organization's programs")
            kwargs["program"] = program
        return kwargs
```

#### Add Review Ownership Verification:

```python
class SolicitationResponseReviewCreateOrUpdate(SolicitationManagerMixin, UpdateView):
    def get_object(self, queryset=None):
        # Get the response being reviewed
        response_pk = self.kwargs.get("response_pk")
        response = get_object_or_404(SolicitationResponse, pk=response_pk)

        # NEW: Verify solicitation belongs to active org
        if response.solicitation.program.organization != self.request.org:
            raise Http404("You can only review responses to your organization's solicitations")

        # Rest of existing logic...
```

### 3. **Computed Properties for Clarity** (Optional - No DB Changes Needed)

```python
class Solicitation(models.Model):
    @property
    def publishing_organization(self):
        """Get the organization that published this solicitation"""
        return self.program.organization

    def user_can_manage(self, user):
        """Check if user can create/edit this solicitation"""
        if user.is_superuser:
            return True
        return user.memberships.filter(
            organization=self.program.organization,
            is_admin=True,
            organization__program_manager=True
        ).exists()
```

### 4. **Remove Helper Function Complexity**

- Remove `calculate_response_permissions()` entirely - it duplicates mixin logic
- Remove `get_user_organization_context()` helper - only used once
- Simplify `SolicitationResponseCreateOrUpdate` to use only the mixin
- **Result**: Cleaner, more maintainable code

---

## Implementation Priority

### Phase 1: Critical Security Fixes (High Priority)

1. **Add program ownership verification** to `SolicitationCreateOrUpdate`
2. **Add solicitation ownership verification** to `SolicitationResponseReviewCreateOrUpdate`
3. **Remove redundant permission logic** in `SolicitationResponseCreateOrUpdate`

### Phase 2: Code Cleanup (Medium Priority)

1. **Remove helper function complexity** - `calculate_response_permissions()` and `get_user_organization_context()`
2. **Add model properties** for clarity (optional)
3. **Improve documentation** of middleware behavior

### Phase 3: Testing (High Priority)

1. **Add ownership verification tests**
2. **Test multi-organization scenarios**
3. **Verify edge cases**

---

## Testing Requirements

### Security Test Cases Needed

1. **Program Manager Rights**: Verify users can only create solicitations for programs they own
2. **Response Rights**: Verify any org member can respond, but only once per org
3. **Review Rights**: Verify only program managers of publishing org can review responses
4. **Multi-Org Users**: Verify users with multiple org memberships work correctly
5. **Edge Cases**: Verify superuser access, inactive orgs, etc.

### Current Test Gaps

- No tests for multi-organization users
- No tests verifying program ownership for solicitation creation
- No tests verifying review permissions are limited to program managers of publishing org
- Insufficient edge case coverage

---

## Summary

**The AI-generated solicitations ACL is actually well-architected** with a solid foundation:

✅ **Strengths:**

- URL-based organization selection is clean and explicit
- Middleware-based context management works correctly
- Mixins implement business rules properly
- Multi-organization users are handled correctly

⚠️ **Issues to Fix:**

- **Security Gap**: Missing program/solicitation ownership verification in create/review views
- **Code Complexity**: Redundant helper functions that duplicate mixin logic
- **Testing Gap**: Insufficient coverage of ownership scenarios

**Bottom Line**: The core design is sound, but needs targeted fixes for ownership verification and code simplification.
