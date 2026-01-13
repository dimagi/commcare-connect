"""
Database Management Service for Audit Application

This service handles database cleanup and management operations for the audit system.
"""

from django.contrib.auth import get_user_model
from django.db import transaction

from commcare_connect.audit.models import Assessment, Audit, AuditResult, AuditTemplate
from commcare_connect.opportunity.models import BlobMeta, CommCareApp, DeliverUnit, Opportunity, PaymentUnit, UserVisit
from commcare_connect.organization.models import Organization, UserOrganizationMembership

User = get_user_model()


def reset_audit_database():
    """
    Reset all audit-related database tables.

    This function clears all audit data while preserving:
    - Connect web login accounts (staff/superuser accounts)
    - HQServers (to avoid OAuth application issues)

    Deletes:
    - All BlobMeta records (form attachments/images)
    - All UserVisit records (loaded from Superset for audits)
    - All AuditDefinition records (audit configurations)
    - All AuditSession records and results
    - All Opportunity records
    - Payment and deliver units
    - Organizations created for audits (slug='audit-org')
    - FLW users who had visits (loaded from Superset)
    - CommCare apps created for audits

    Returns:
        dict: Counts of deleted records
    """
    # Count before deletion
    deleted = {
        "opportunities": Opportunity.objects.count(),
        "visits": UserVisit.objects.count(),
        "audit_templates": AuditTemplate.objects.count(),
        "audits": Audit.objects.count(),
        "audit_results": AuditResult.objects.count(),
        "assessments": Assessment.objects.count(),
        "users": User.objects.filter(is_superuser=False, is_staff=False).count(),
        "attachments": BlobMeta.objects.count(),
    }

    # Use transaction to ensure all-or-nothing
    with transaction.atomic():
        # First, identify FLW users (before deleting visits)
        # Convert to list to capture IDs (not lazy QuerySet that re-evaluates)

        # Get FLW users: those who have UserVisits (loaded from Superset for audits)
        flw_user_ids = list(UserVisit.objects.values_list("user_id", flat=True).distinct())

        # Delete in correct order to respect foreign keys
        # 1. Delete attachments (BlobMeta records tied to visits)
        BlobMeta.objects.all().delete()

        # 2. Delete visits (removes FK to users)
        UserVisit.objects.all().delete()

        # 3. Delete audits and all related data
        # Note: These cascade from Audit, but we delete explicitly for clarity
        Assessment.objects.all().delete()
        AuditResult.objects.all().delete()
        Audit.objects.all().delete()

        # 4. Delete audit templates (audit configurations and preview data)
        AuditTemplate.objects.all().delete()

        # 5. Delete payment and deliver units that block opportunity deletion
        PaymentUnit.objects.all().delete()
        DeliverUnit.objects.all().delete()

        # 6. Delete CommCare apps created for audit
        CommCareApp.objects.filter(cc_app_id="audit-app").delete()

        # 7. Now we can delete opportunities
        Opportunity.objects.all().delete()

        # 8. Clean up organizations created for audit (before users due to membership FK)
        Organization.objects.filter(slug="audit-org").delete()

        # 9. Delete organization memberships for FLW users
        UserOrganizationMembership.objects.filter(user_id__in=flw_user_ids).delete()

        # 10. Delete FLW users (preserve all Connect web login accounts)
        # Only deletes users who had visits (loaded from Superset for audits)
        User.objects.filter(id__in=flw_user_ids).delete()

    # Note: We don't delete HQServers to avoid OAuth application issues
    # HQServers require OAuth applications and are shared across the system

    return deleted


def get_database_stats():
    """
    Get current counts of audit-related database records.

    Returns:
        dict: Counts of key database records
    """
    # Count FLW users with visits (these are the ones that will be deleted on reset)
    flw_user_ids = UserVisit.objects.values_list("user_id", flat=True).distinct()
    flw_users_with_visits_count = len(flw_user_ids)

    return {
        "opportunities": Opportunity.objects.count(),
        "users": flw_users_with_visits_count,
        "visits": UserVisit.objects.count(),
        "audit_templates": AuditTemplate.objects.count(),
        "audits": Audit.objects.count(),
        "audit_results": AuditResult.objects.count(),
        "assessments": Assessment.objects.count(),
        "attachments": BlobMeta.objects.count(),
    }


def download_missing_attachments(progress_tracker=None):
    """
    Download missing attachments for all audit sessions.

    This function reuses the same download logic as audit creation.
    It:
    1. Collects all visits from audit sessions
    2. Uses AuditDataLoader.download_attachments() to download missing files
    3. Regenerates assessments for affected sessions

    Args:
        progress_tracker: Optional ProgressTracker for reporting progress

    Returns:
        dict: Statistics about the download operation
    """
    import os

    from commcare_connect.audit.management.extractors.connect_api_facade import ConnectAPIFacade
    from commcare_connect.audit.services.assessment_generator import generate_assessments_for_session
    from commcare_connect.audit.services.data_loader import AuditDataLoader

    stats = {
        "sessions_scanned": 0,
        "visits_scanned": 0,
        "attachments_downloaded": 0,
        "sessions_regenerated": set(),
        "errors": [],
    }

    # Check if CommCare credentials are available
    if not os.getenv("COMMCARE_USERNAME") or not os.getenv("COMMCARE_API_KEY"):
        error_msg = "CommCare credentials not configured (COMMCARE_USERNAME/COMMCARE_API_KEY)"
        stats["errors"].append(error_msg)
        if progress_tracker:
            progress_tracker.error(error_msg)
        return stats

    try:
        # Step 1: Collect all visits from audits
        if progress_tracker:
            progress_tracker.update(0, 100, "Scanning audits...", "loading", step_name="scanning")

        audits = Audit.objects.all().prefetch_related("visits")

        if not audits.exists():
            error_msg = "No audits found"
            stats["errors"].append(error_msg)
            if progress_tracker:
                progress_tracker.error(error_msg)
            return stats

        print(f"[INFO] Scanning {audits.count()} audits for visits...")

        # Collect all unique visits with their domain metadata
        all_visits = []
        for audit in audits:
            stats["sessions_scanned"] += 1
            visits = audit.visits.all()

            for visit in visits:
                stats["visits_scanned"] += 1

                # Set domain metadata on visit for download_attachments to use
                # Try to get from opportunity first
                if visit.opportunity and visit.opportunity.deliver_app:
                    visit._temp_cc_domain = visit.opportunity.deliver_app.cc_domain
                    visit._temp_cc_app_id = visit.opportunity.deliver_app.cc_app_id
                elif audit.domain != "unknown":
                    # Fall back to audit metadata (computed property)
                    visit._temp_cc_domain = audit.domain
                    visit._temp_cc_app_id = audit.app_id

                all_visits.append(visit)

        # Deduplicate visits (same visit may be in multiple audits)
        unique_visits = list({v.id: v for v in all_visits}.values())

        print(f"[INFO] Found {len(unique_visits)} unique visits across {stats['sessions_scanned']} audits")

        if progress_tracker:
            progress_tracker.complete_step("scanning", f"Found {len(unique_visits)} visits")

        # Step 2: Use AuditDataLoader to download attachments (same logic as creation)
        if progress_tracker:
            progress_tracker.update(
                0, len(unique_visits) or 1, "Starting attachment download...", "downloading", step_name="attachments"
            )

        # Initialize facade and loader
        facade = ConnectAPIFacade()
        if not facade.authenticate():
            error_msg = "Failed to authenticate with Superset data source"
            stats["errors"].append(error_msg)
            if progress_tracker:
                progress_tracker.error(error_msg)
            return stats

        try:
            loader = AuditDataLoader(facade=facade, dry_run=False)

            # Track attachments before download
            attachments_before = BlobMeta.objects.count()

            # Download attachments - this handles deduplication, domain grouping, etc.
            loader.download_attachments(unique_visits, progress_tracker=progress_tracker)

            # Calculate how many were downloaded
            attachments_after = BlobMeta.objects.count()
            stats["attachments_downloaded"] = attachments_after - attachments_before

            if progress_tracker:
                progress_tracker.complete_step(
                    "attachments", f"Downloaded {stats['attachments_downloaded']} attachments"
                )

        finally:
            facade.close()

        # Step 3: Regenerate assessments for all audits
        if progress_tracker:
            progress_tracker.update(
                0, stats["sessions_scanned"], "Regenerating assessments...", "processing", step_name="assessments"
            )

        print(f"[INFO] Regenerating assessments for {stats['sessions_scanned']} audits...")
        for idx, audit in enumerate(audits, 1):
            try:
                generate_assessments_for_session(audit)
                stats["sessions_regenerated"].add(audit.id)

                if progress_tracker and idx % 10 == 0:
                    progress_tracker.update(
                        idx,
                        stats["sessions_scanned"],
                        f"Regenerating assessments ({idx}/{stats['sessions_scanned']})...",
                        "processing",
                        step_name="assessments",
                    )

            except Exception as e:
                error_msg = f"Failed to regenerate assessments for audit {audit.id}: {str(e)}"
                print(f"[WARNING] {error_msg}")
                stats["errors"].append(error_msg)

        if progress_tracker:
            progress_tracker.complete_step("assessments", f"Regenerated {len(stats['sessions_regenerated'])} sessions")

        # Convert set to count for JSON serialization
        stats["sessions_regenerated"] = len(stats["sessions_regenerated"])

        print(f"[OK] Download complete: {stats['attachments_downloaded']} attachments downloaded")

        if progress_tracker:
            progress_tracker.complete("Download complete!", result_data={"stats": stats})

        return stats

    except Exception as e:
        import traceback

        error_msg = f"Error during download: {str(e)}"
        error_details = traceback.format_exc()
        print(f"[ERROR] {error_msg}")
        print(error_details)

        stats["errors"].append(error_msg)

        if progress_tracker:
            progress_tracker.error(error_msg, error_details)

        return stats
