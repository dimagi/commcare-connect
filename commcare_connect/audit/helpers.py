"""
Helper functions for the audit application
"""
import datetime

import httpx
from allauth.socialaccount.models import SocialAccount, SocialApp, SocialToken
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from commcare_connect.opportunity.models import UserVisit, VisitValidationStatus


def calculate_audit_progress(audit):
    """
    Calculate the audit progress percentage based on assessed images.

    Progress is determined by the number of assessments that have been reviewed
    (have a non-null result) compared to the total number of assessments.

    Args:
        audit: Audit object

    Returns:
        Tuple of (percentage, assessed_count, total_count)
    """
    from commcare_connect.audit.models import Assessment

    # Get all assessments for this audit
    assessments = Assessment.objects.filter(audit_result__audit=audit)
    total_assessments = assessments.count()

    if total_assessments == 0:
        return 0.0, 0, 0

    # Count assessments that have been reviewed (have a result)
    assessed_count = assessments.exclude(result__isnull=True).count()

    percentage = round((assessed_count / total_assessments) * 100, 1)

    return percentage, assessed_count, total_assessments


def get_approved_visits_for_audit(flw_user, opportunity, start_date, end_date):
    """
    Get UserVisit records that are approved and have images for auditing

    Args:
        flw_user: User object for the field worker
        opportunity: Opportunity object
        start_date: Start date for the audit period
        end_date: End date for the audit period

    Returns:
        QuerySet of UserVisit objects ready for audit
    """
    return (
        UserVisit.objects.filter(
            user=flw_user,
            opportunity=opportunity,
            visit_date__date__range=[start_date, end_date],
            status=VisitValidationStatus.approved,
        )
        .exclude(
            # Exclude visits that don't have images
            Q(images__isnull=True)
            | Q(images__content_type__isnull=True)
        )
        .distinct()
        .prefetch_related("images")
        .order_by("visit_date")
    )


def generate_audit_export(audit_session):
    """
    Generate comprehensive JSON export of audit session

    Args:
        audit_session: AuditSession object

    Returns:
        Dictionary containing all audit data for export
    """
    return {
        "metadata": {
            "export_version": "1.0",
            "exported_at": audit_session.created_at.isoformat(),
            "total_visits_audited": audit_session.results.count(),
            "total_visits_available": audit_session.get_audit_visits().count(),
        },
        "audit_session": {
            "id": audit_session.id,
            "auditor": {
                "username": audit_session.auditor.username,
                "email": audit_session.auditor.email,
            },
            "flw_user": {
                "username": audit_session.flw_user.username,
                "email": audit_session.flw_user.email,
            },
            "opportunity": {
                "id": audit_session.opportunity.id,
                "name": audit_session.opportunity.name,
                "organization": audit_session.opportunity.organization.name,
            },
            "audit_period": {
                "start_date": str(audit_session.start_date),
                "end_date": str(audit_session.end_date),
            },
            "status": audit_session.status,
            "overall_result": audit_session.overall_result,
            "notes": audit_session.notes,
            "kpi_notes": audit_session.kpi_notes,
            "created_at": audit_session.created_at.isoformat(),
            "completed_at": audit_session.completed_at.isoformat() if audit_session.completed_at else None,
        },
        "visit_results": [
            {
                "visit_details": {
                    "id": result.user_visit.id,
                    "xform_id": result.user_visit.xform_id,
                    "visit_date": result.user_visit.visit_date.isoformat(),
                    "entity_id": result.user_visit.entity_id,
                    "entity_name": result.user_visit.entity_name,
                    "location": result.user_visit.location,
                },
                "audit_result": {
                    "result": result.result,
                    "notes": result.notes,
                    "audited_at": result.audited_at.isoformat(),
                },
                "media_info": {
                    "image_count": result.user_visit.images.count(),
                    "images": [
                        {
                            "name": img.name,
                            "content_type": img.content_type,
                            "size_bytes": img.content_length,
                        }
                        for img in result.user_visit.images.all()
                    ],
                },
            }
            for result in audit_session.results.select_related("user_visit")
            .prefetch_related("user_visit__images")
            .all()
        ],
        "summary_statistics": {
            "total_visits": audit_session.get_audit_visits().count(),
            "audited_visits": audit_session.results.count(),
            "passed_visits": audit_session.results.filter(result="pass").count(),
            "failed_visits": audit_session.results.filter(result="fail").count(),
            "completion_percentage": audit_session.progress_percentage,
        },
    }


def validate_audit_session_data(flw_user, opportunity, start_date, end_date):
    """
    Validate that an audit session can be created with the given parameters

    Args:
        flw_user: User object for the field worker
        opportunity: Opportunity object
        start_date: Start date for the audit period
        end_date: End date for the audit period

    Returns:
        Tuple of (is_valid, error_message, visit_count)
    """
    if start_date >= end_date:
        return False, "Start date must be before end date", 0

    # Check if there are any approved visits with images in the date range
    visits = get_approved_visits_for_audit(flw_user, opportunity, start_date, end_date)
    visit_count = visits.count()

    if visit_count == 0:
        return False, f"No approved visits with images found for {flw_user.username} in the specified date range", 0

    return True, None, visit_count


def get_connect_oauth_token(user, request=None):
    """
    Get OAuth token for Connect production instance.

    Retrieves the stored OAuth token for the user and refreshes it if expired.
    This token is used for making API requests to connect.dimagi.com.

    Args:
        user: Django User object (or LabsUser in labs mode)
        request: HttpRequest object (required in labs mode to access session)

    Returns:
        String token if valid, None if no token exists

    Raises:
        Exception: If token refresh fails
    """
    # In labs mode, get token from session
    if getattr(settings, "IS_LABS_ENVIRONMENT", False):
        if not request:
            return None
        labs_oauth = request.session.get("labs_oauth", {})
        # Check if token is expired
        expires_at = labs_oauth.get("expires_at", 0)
        if timezone.now().timestamp() < expires_at:
            return labs_oauth.get("access_token")
        return None

    # Normal mode: Check if user has a Connect social account
    try:
        social_account = SocialAccount.objects.get(user=user, provider="connect")
    except SocialAccount.DoesNotExist:
        return None

    # Get the associated token
    try:
        social_token = SocialToken.objects.get(account=social_account)
    except SocialToken.DoesNotExist:
        return None

    # Check if token is expired
    if social_token.expires_at and social_token.expires_at > timezone.now():
        # Token is still valid
        return social_token.token

    # Token is expired, attempt to refresh
    social_app = SocialApp.objects.filter(provider="connect").first()
    if not social_app:
        raise ValueError("Connect OAuth application not configured in SocialApp")

    # Attempt to refresh the token
    response = httpx.post(
        f"{settings.CONNECT_PRODUCTION_URL}/o/token/",
        data={
            "grant_type": "refresh_token",
            "client_id": social_app.client_id,
            "client_secret": social_app.secret,
            "refresh_token": social_token.token_secret,
        },
        timeout=10,
    )

    if response.status_code != 200:
        raise Exception(f"Failed to refresh Connect OAuth token: {response.text}")

    data = response.json()
    if data.get("access_token"):
        social_token.token = data["access_token"]
        if data.get("refresh_token"):
            social_token.token_secret = data["refresh_token"]
        # Set expiration (typically 2 weeks = 1209600 seconds)
        expires_in = data.get("expires_in", 1209600)
        social_token.expires_at = timezone.now() + datetime.timedelta(seconds=expires_in)
        social_token.save()
        return social_token.token

    raise Exception("No access token in refresh response")
