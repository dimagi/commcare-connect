"""
Connect OAuth Views

Manual OAuth implementation for CommCare Connect production instance.
"""

import datetime
import hashlib
import secrets
from base64 import urlsafe_b64encode
from urllib.parse import urlencode

import httpx
from allauth.socialaccount.models import SocialAccount, SocialApp, SocialToken
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone


@login_required
def oauth2_login(request):
    """
    Manually initiate OAuth flow to Connect production.

    This bypasses allauth's complex view system and directly redirects to Connect.
    """
    # Get the SocialApp for Connect
    try:
        app = SocialApp.objects.get(provider="connect", sites__id=settings.SITE_ID)
    except SocialApp.DoesNotExist:
        return JsonResponse(
            {"error": "Connect OAuth not configured. Please add a Social Application in Django Admin."}, status=500
        )

    # Generate CSRF state token
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state
    request.session["oauth_next"] = request.GET.get("next", "/audit/")

    # Generate PKCE code verifier and challenge
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = (
        urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    )

    request.session["oauth_code_verifier"] = code_verifier

    # Build callback URL
    callback_url = request.build_absolute_uri(reverse("audit:connect_oauth_callback"))

    # Build OAuth authorize URL with PKCE
    params = {
        "client_id": app.client_id,
        "redirect_uri": callback_url,
        "response_type": "code",
        "scope": "export",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }

    authorize_url = f"{settings.CONNECT_PRODUCTION_URL}/o/authorize/?{urlencode(params)}"

    print(f"[OAuth] Redirecting to: {authorize_url}")

    return HttpResponseRedirect(authorize_url)


@login_required
def oauth2_callback(request):
    """
    Handle OAuth callback from Connect production.

    Exchange authorization code for access token and save it.
    """
    # Verify state to prevent CSRF
    state = request.GET.get("state")
    saved_state = request.session.get("oauth_state")

    if not state or state != saved_state:
        return JsonResponse({"error": "Invalid state parameter"}, status=400)

    # Get authorization code
    code = request.GET.get("code")
    if not code:
        error = request.GET.get("error", "Unknown error")
        return JsonResponse({"error": f"OAuth error: {error}"}, status=400)

    # Get the SocialApp
    try:
        app = SocialApp.objects.get(provider="connect", sites__id=settings.SITE_ID)
    except SocialApp.DoesNotExist:
        return JsonResponse({"error": "Connect OAuth not configured"}, status=500)

    # Get PKCE code verifier from session
    code_verifier = request.session.get("oauth_code_verifier")
    if not code_verifier:
        return JsonResponse({"error": "Missing code verifier in session"}, status=400)

    # Exchange code for token with PKCE
    callback_url = request.build_absolute_uri(reverse("audit:connect_oauth_callback"))
    token_url = f"{settings.CONNECT_PRODUCTION_URL}/o/token/"

    token_data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": callback_url,
        "client_id": app.client_id,
        "client_secret": app.secret,
        "code_verifier": code_verifier,
    }

    try:
        response = httpx.post(token_url, data=token_data, timeout=10)
        response.raise_for_status()
        token_json = response.json()
    except Exception as e:
        return JsonResponse({"error": f"Failed to exchange code for token: {str(e)}"}, status=500)

    # Get user info (optional - use local user if profile fetch fails)
    access_token = token_json["access_token"]
    profile_data = {}
    uid = str(request.user.id)  # Default to local user ID

    # Try to fetch Connect user profile (optional)
    profile_urls_to_try = [
        f"{settings.CONNECT_PRODUCTION_URL}/api/v1/identity/",
        f"{settings.CONNECT_PRODUCTION_URL}/api/identity/",
        f"{settings.CONNECT_PRODUCTION_URL}/accounts/api/identity/",
    ]

    for profile_url in profile_urls_to_try:
        try:
            profile_response = httpx.get(profile_url, headers={"Authorization": f"Bearer {access_token}"}, timeout=10)
            if profile_response.status_code == 200:
                profile_data = profile_response.json()
                uid = str(profile_data.get("id", request.user.id))
                print(f"[OAuth] Successfully fetched profile from {profile_url}")
                break
        except Exception:
            continue

    if not profile_data:
        print("[OAuth] Could not fetch profile, using local user data")
        profile_data = {
            "username": request.user.username,
            "email": request.user.email,
        }

    # Save or update SocialAccount and SocialToken
    social_account, _ = SocialAccount.objects.update_or_create(
        user=request.user,
        provider="connect",
        defaults={
            "uid": uid,
            "extra_data": profile_data,
        },
    )

    # Calculate token expiration (typically 2 weeks = 1209600 seconds)
    expires_in = token_json.get("expires_in", 1209600)
    expires_at = timezone.now() + datetime.timedelta(seconds=expires_in)

    SocialToken.objects.update_or_create(
        account=social_account,
        app=app,
        defaults={
            "token": access_token,
            "token_secret": token_json.get("refresh_token", ""),
            "expires_at": expires_at,
        },
    )

    # Clean up session
    del request.session["oauth_state"]
    del request.session["oauth_code_verifier"]
    next_url = request.session.pop("oauth_next", "/audit/")

    print(f"[OAuth] Successfully authenticated user {request.user.username}")
    print(f"[OAuth] Redirecting to: {next_url}")

    return redirect(next_url)
