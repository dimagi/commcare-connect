# Labs OAuth Authentication

## Overview

Session-based OAuth authentication for the labs environment. No user data is stored in the database - everything is kept in encrypted Django sessions.

**This implementation improves upon the audit app's OAuth approach with:**

- Proper Python logging (not print statements)
- User-friendly error handling (not JSON responses)
- Full type hints throughout
- Settings validation at startup
- Better exception handling
- Django messages for user feedback

See `IMPROVEMENTS.md` for detailed comparison with the audit app implementation.

## Architecture

**Key Features:**

- OAuth-only login (no local passwords)
- No database storage of user accounts
- Session-based authentication
- URL whitelisting (non-whitelisted URLs redirect to connect.dimagi.com)
- Token expiration checking on every request

**What's NOT in the database:**

- User accounts
- SocialAccount records
- SocialToken records
- Any PII from Connect production

**What IS stored:**

- Django sessions (encrypted via SECRET_KEY)
- Contains OAuth tokens and user profile temporarily

## Configuration

### Environment Variables Required

Set these in your labs deployment:

```bash
# Use labs settings module
DJANGO_SETTINGS_MODULE=config.settings.labs

# Connect production OAuth credentials
CONNECT_PRODUCTION_URL=https://connect.dimagi.com
CONNECT_OAUTH_CLIENT_ID=your_client_id_here
CONNECT_OAUTH_CLIENT_SECRET=your_client_secret_here
```

### OAuth Application Setup on Connect Production

1. Log into connect.dimagi.com as an admin
2. Navigate to Django Admin > OAuth2 Provider > Applications
3. Create a new application:
   - **Client Type**: Confidential
   - **Authorization Grant Type**: Authorization code
   - **Redirect URIs**: `https://labs.connect.dimagi.com/labs/callback/`
   - **Name**: "Labs OAuth"
4. Note the **Client ID** and **Client Secret**
5. Use these as CONNECT_OAUTH_CLIENT_ID and CONNECT_OAUTH_CLIENT_SECRET

## URL Whitelisting

Only these URL prefixes are accessible in labs:

- `/audit/` - Audit app
- `/tasks/` - Tasks app
- `/labs/` - Labs login/logout
- `/static/` - Static files
- `/media/` - Media files
- `/admin/` - Django admin

All other URLs redirect to `https://connect.dimagi.com{path}`

## Usage

### Login Flow

1. User visits any whitelisted URL (e.g., `/audit/`)
2. Middleware checks if authenticated
3. If not authenticated, redirects to `/labs/login/`
4. User clicks "Login with Connect"
5. Redirected to Connect production OAuth
6. User authorizes the application
7. Callback stores token and profile in session
8. User redirected back to original URL

### Logout

Visit `/labs/logout/` to clear session and log out.

### Accessing User Data in Views

```python
# request.user is a LabsUser object (not saved to DB)
def my_view(request):
    username = request.user.username
    email = request.user.email
    full_name = request.user.get_full_name()

    # Check if authenticated
    if request.user.is_authenticated:
        # User has valid OAuth token
        pass
```

### Accessing OAuth Token

```python
def my_view(request):
    # Get OAuth data from session
    labs_oauth = request.session.get('labs_oauth')
    if labs_oauth:
        access_token = labs_oauth['access_token']
        # Use token to make API calls to Connect production
```

## Security

1. **PKCE**: Prevents authorization code interception
2. **State Token**: CSRF protection for OAuth flow
3. **Session Encryption**: Django SECRET_KEY encrypts session data
4. **HTTPS Required**: Session cookies secure in staging/labs
5. **Token Expiration**: Checked on every request
6. **No Database Storage**: User data only in sessions (ephemeral)

## Testing Locally

To test the labs authentication locally:

1. Create `.env` file with OAuth credentials:

   ```bash
   CONNECT_PRODUCTION_URL=https://connect.dimagi.com
   CONNECT_OAUTH_CLIENT_ID=your_test_client_id
   CONNECT_OAUTH_CLIENT_SECRET=your_test_client_secret
   ```

2. Run with labs settings:

   ```bash
   python manage.py runserver --settings=config.settings.labs
   ```

3. Visit `http://localhost:8000/labs/login/`

## Deployment

### GitHub Actions Deployment

Update the labs deployment workflow to use labs settings:

```yaml
- name: Deploy to Fargate
  env:
    DJANGO_SETTINGS_MODULE: config.settings.labs
```

### AWS Secrets Manager

Add OAuth credentials to AWS Secrets Manager for labs:

```bash
aws secretsmanager create-secret \
  --name labs-jj-connect-oauth-client-id \
  --secret-string "your_client_id" \
  --profile labs

aws secretsmanager create-secret \
  --name labs-jj-connect-oauth-client-secret \
  --secret-string "your_client_secret" \
  --profile labs
```

### Task Definition

Update ECS task definition to inject secrets:

```json
{
  "secrets": [
    {
      "name": "CONNECT_OAUTH_CLIENT_ID",
      "valueFrom": "arn:aws:secretsmanager:us-east-1:xxx:secret:labs-jj-connect-oauth-client-id"
    },
    {
      "name": "CONNECT_OAUTH_CLIENT_SECRET",
      "valueFrom": "arn:aws:secretsmanager:us-east-1:xxx:secret:labs-jj-connect-oauth-client-secret"
    }
  ]
}
```

## Future Enhancements

- Multiple OAuth scopes for data storage APIs
- Refresh token support for automatic renewal
- Redis-based sessions for zero DB storage
- Token refresh before expiration

## Files Created

```
commcare_connect/labs/
├── __init__.py
├── apps.py
├── auth_backend.py       # Minimal authentication backend
├── middleware.py         # Authentication and URL whitelisting
├── models.py             # LabsUser transient class
├── oauth_views.py        # Login/callback/logout views
└── urls.py               # URL configuration

commcare_connect/templates/labs/
└── login.html            # Login page template

config/settings/
└── labs.py               # Labs-specific settings
```
