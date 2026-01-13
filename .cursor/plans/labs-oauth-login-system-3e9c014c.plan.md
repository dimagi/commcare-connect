<!-- 3e9c014c-358e-4dc6-805b-ab856f1e1102 a2307260-4a67-4965-838a-ad9790a02458 -->

# Labs OAuth Login System (Session-Based, No DB Storage)

## Overview

Create an OAuth-only authentication system for labs that stores zero user data in the database. All authentication is session-based: OAuth tokens and user profiles stored in encrypted Django sessions only. Token validity = authentication.

## Core Architecture

**No Database Storage:**

- NO User model records created
- NO SocialAccount records
- NO SocialToken records
- User identity only in encrypted session data

**Session-Based Auth:**

- OAuth token + user profile stored in `request.session['labs_oauth']`
- Middleware populates `request.user` from session on each request
- Token expiration checked on each request
- Logout = clear session data

## Key Components

### 1. Labs App Structure

**Create `commcare_connect/labs/` app:**

Files to create:

- `commcare_connect/labs/__init__.py`
- `commcare_connect/labs/apps.py`
- `commcare_connect/labs/models.py` - LabsUser class (transient, not saved to DB)
- `commcare_connect/labs/auth_backend.py` - Session-based authentication backend
- `commcare_connect/labs/oauth_views.py` - Login/logout views
- `commcare_connect/labs/middleware.py` - Auth and URL whitelisting middleware
- `commcare_connect/labs/urls.py`

### 2. LabsUser Class (Transient User Object)

**`commcare_connect/labs/models.py`:**

```python
class LabsUser:
    """Transient user object that mimics Django User interface.

    Never saved to database. Initialized from session data only.
    """
    def __init__(self, user_data):
        self.id = user_data['id']
        self.pk = user_data['id']
        self.username = user_data['username']
        self.email = user_data['email']
        self.first_name = user_data.get('first_name', '')
        self.last_name = user_data.get('last_name', '')
        self.is_authenticated = True
        self.is_active = True
        self.is_staff = False
        self.is_superuser = False
        self.is_anonymous = False

    def save(self, *args, **kwargs):
        raise NotImplementedError("LabsUser cannot be saved to database")
```

### 3. OAuth Views (No Database Writes)

**`commcare_connect/labs/oauth_views.py`:**

Adapt from `audit/oauth_views.py` but store in session instead of DB:

**`labs_oauth_login(request)`:**

- No `@login_required` decorator
- Generate PKCE challenge (copy from audit)
- Store state and code_verifier in session
- Redirect to `{CONNECT_PRODUCTION_URL}/o/authorize/` with params:
  - `client_id`, `redirect_uri`, `response_type=code`
  - `scope=export` (expandable to multiple scopes)
  - `state`, `code_challenge`, `code_challenge_method=S256`

**`labs_oauth_callback(request)`:**

- Verify state token (CSRF protection)
- Exchange auth code for access token using PKCE
- Fetch user profile from Connect prod identity API
- Store in session (NO database):

  ```python
  request.session['labs_oauth'] = {
      'access_token': access_token,
      'refresh_token': refresh_token or '',
      'expires_at': (timezone.now() + timedelta(seconds=expires_in)).timestamp(),
      'user_profile': {
          'id': profile['id'],
          'username': profile['username'],
          'email': profile['email'],
          'first_name': profile.get('first_name', ''),
          'last_name': profile.get('last_name', ''),
      }
  }
  ```

- Clean up temporary session keys (oauth_state, oauth_code_verifier)
- Redirect to next_url or `/audit/`

**`labs_logout(request)`:**

- Clear `request.session['labs_oauth']`
- Redirect to labs login page

### 4. Authentication Backend

**`commcare_connect/labs/auth_backend.py`:**

```python
class LabsOAuthBackend:
    """Session-based authentication backend for labs environment."""

    def authenticate(self, request, **kwargs):
        # This won't be called directly, middleware handles it
        return None

    def get_user(self, user_id):
        # Return None - we don't load from DB
        return None
```

Middleware will handle populating `request.user` directly from session.

### 5. Labs Middleware (Core Logic)

**`commcare_connect/labs/middleware.py`:**

```python
class LabsAuthenticationMiddleware:
    """Populate request.user from session OAuth data (labs environment only)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Only run in labs environment
        if not getattr(settings, 'IS_LABS_ENVIRONMENT', False):
            return self.get_response(request)

        # Check session for OAuth data
        labs_oauth = request.session.get('labs_oauth')

        if labs_oauth:
            # Check token expiration
            expires_at = labs_oauth.get('expires_at', 0)
            if timezone.now().timestamp() < expires_at:
                # Token valid, populate request.user
                request.user = LabsUser(labs_oauth['user_profile'])
            else:
                # Token expired, clear session
                del request.session['labs_oauth']
                request.user = AnonymousUser()
        else:
            request.user = AnonymousUser()

        return self.get_response(request)
```

```python
class LabsURLWhitelistMiddleware:
    """Redirect non-whitelisted URLs to prod, require auth for whitelisted."""

    WHITELISTED_PREFIXES = [
        '/audit/',
        '/tasks/',
        '/labs/',
        '/static/',
        '/media/',
        '/admin/',
    ]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Only run in labs environment
        if not getattr(settings, 'IS_LABS_ENVIRONMENT', False):
            return self.get_response(request)

        path = request.path

        # Check if path is whitelisted
        is_whitelisted = any(path.startswith(prefix) for prefix in self.WHITELISTED_PREFIXES)

        if not is_whitelisted:
            # Redirect to production Connect
            return HttpResponseRedirect(f"https://connect.dimagi.com{path}")

        # Whitelisted path - require authentication (except login/callback)
        if path not in ['/labs/login/', '/labs/callback/']:
            if not request.user.is_authenticated:
                # Redirect to labs login
                return HttpResponseRedirect(f"/labs/login/?next={path}")

        return self.get_response(request)
```

### 6. Login Template

**`commcare_connect/templates/labs/login.html`:**

```django
{% extends "account/base.html" %}
{% load static %}

{% block head_title %}Sign In - Labs{% endblock %}

{% block inner %}
<div class="w-full min-h-[620px] h-fit flex flex-col gap-4">
  <h6 class="title text-brand-deep-purple">Labs Login</h6>
  <span class="text-sm text-gray-600">Sign in with your Connect account</span>

  <div class="space-y-2 place-items-center">
    <a href="{% url 'labs:oauth_login' %}?next={{ next|urlencode }}"
       class="flex gap-2 rounded-full bg-gray-100 items-center p-2 pr-4 cursor-pointer">
      <div class="w-6 h-6">
        <img src="{% static 'images/logo-color.svg' %}" alt="Connect Logo" class="w-full">
      </div>
      <span class="text-sm">Login with Connect</span>
    </a>
  </div>

  <div class="text-sm text-gray-400 text-center mt-auto border-t-gray-200 border-t-2 pt-4">
    Labs environment - OAuth only
  </div>
</div>
{% endblock %}
```

### 7. Settings Configuration

**NO CHANGES to `config/settings/base.py`** - Keep it clean!

**Create `config/settings/labs.py`:**

```python
from .staging import *

# Labs environment flags
IS_LABS_ENVIRONMENT = True
DEPLOY_ENVIRONMENT = "labs"

# OAuth configuration
LABS_OAUTH_SCOPES = ["export"]  # Expandable: ["export", "labs_data_storage"]

# Disable local registration
ACCOUNT_ALLOW_REGISTRATION = False

# Override login URL to labs OAuth
LOGIN_URL = "/labs/login/"

# Custom authentication (session-based, no DB)
AUTHENTICATION_BACKENDS = [
    "commcare_connect.labs.auth_backend.LabsOAuthBackend",
]

# Add labs app to installed apps
INSTALLED_APPS = INSTALLED_APPS + ["commcare_connect.labs"]

# Replace default AuthenticationMiddleware with labs version
# Add URL whitelist middleware
MIDDLEWARE = list(MIDDLEWARE)
_auth_idx = MIDDLEWARE.index("django.contrib.auth.middleware.AuthenticationMiddleware")
MIDDLEWARE[_auth_idx] = "commcare_connect.labs.middleware.LabsAuthenticationMiddleware"
MIDDLEWARE.insert(_auth_idx + 1, "commcare_connect.labs.middleware.LabsURLWhitelistMiddleware")
```

**To use labs settings:**

- Set environment variable: `DJANGO_SETTINGS_MODULE=config.settings.labs`
- Or in GitHub Actions workflow for labs deployment

### 8. URL Configuration

**`commcare_connect/labs/urls.py`:**

```python
from django.urls import path
from . import oauth_views

app_name = "labs"

urlpatterns = [
    path("login/", oauth_views.labs_oauth_login, name="oauth_login"),
    path("callback/", oauth_views.labs_oauth_callback, name="oauth_callback"),
    path("logout/", oauth_views.labs_logout, name="logout"),
]
```

**`config/urls.py`** - Add:

```python
path("labs/", include("commcare_connect.labs.urls", namespace="labs")),
```

## Implementation Steps

1. Create labs app structure
2. Create LabsUser transient class
3. Implement OAuth views (login, callback, logout) - session storage only
4. Create authentication backend (minimal, middleware does heavy lifting)
5. Implement middleware (authentication + URL whitelisting)
6. Create login template
7. Create labs.py settings file
8. Add labs URLs to main config
9. Test on labs server

## Code Reuse from Audit App

**Copy from `commcare_connect/audit/oauth_views.py`:**

- PKCE generation (lines 43-49)
- State token (lines 38-41)
- Token exchange (lines 107-121)
- Profile fetching (lines 129-144)

**Key differences:**

- No `@login_required` decorator
- Store in session, not DB models (SocialAccount, SocialToken)
- No User creation

## Security Notes

1. **Session Storage**: Encrypted via Django's SECRET_KEY
2. **PKCE**: Prevents auth code interception attacks
3. **State Token**: CSRF protection for OAuth flow
4. **HTTPS Required**: Session cookies secure in staging/labs
5. **Token Expiration**: Checked on every request by middleware
6. **No PII in Database**: All user data ephemeral (sessions only)

## Database Impact

**Labs database will contain:**

- Django sessions (encrypted, contains OAuth tokens)
- Audit/task prototype data
- NO user accounts or identity data

**If even sessions are a concern:**

Can switch to Redis/cache-based sessions instead of DB sessions.

## Future Enhancements

- Multiple OAuth scopes when data storage API ready
- Refresh token support for automatic token renewal
- Redis-based sessions for zero DB storage of auth data

### To-dos

- [ ] Create labs Django app with basic structure (apps.py, **init**.py, urls.py)
- [ ] Create oauth_views.py with labs_oauth_login and labs_oauth_callback functions, adapted from audit/oauth_views.py
- [ ] Add user auto-creation logic in callback view (fetch profile, create User with unusable password)
- [ ] Create labs/login.html template with single 'Login with Connect' button
- [ ] Create LabsEnvironmentMiddleware for environment detection and URL whitelisting/redirects
- [ ] Add labs app to INSTALLED_APPS, configure middleware, create labs.py settings file
- [ ] Add labs URLs to config/urls.py with namespace
- [ ] Test OAuth flow on labs server with environment variable set
