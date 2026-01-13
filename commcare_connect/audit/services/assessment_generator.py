"""
Assessment Generation Service

Generates assessments for audits based on available data.
Currently supports image assessments, with future support for flags and form data.
"""

from commcare_connect.audit.models import Assessment, Audit, AuditResult


def generate_assessments_for_session(audit: Audit) -> dict:
    """
    Generate assessments for all visits in an audit.

    Currently generates image assessments. Future: flags, form data elements.

    Args:
        audit: Audit to generate assessments for

    Returns:
        Dictionary with statistics about generated assessments
    """
    stats = {
        "visits_processed": 0,
        "images_processed": 0,
        "assessments_created": 0,
    }

    # Get all visits in this audit
    visits = audit.visits.all()

    for visit in visits:
        stats["visits_processed"] += 1

        # Create or get the AuditResult for this visit
        audit_result, created = AuditResult.objects.get_or_create(
            audit=audit,
            user_visit=visit,
            defaults={
                "result": "pass",  # Default to pass, auditor can change
                "notes": "",
            },
        )

        # Generate image assessments
        images = visit.images.all()
        for image in images:
            stats["images_processed"] += 1

            assessment, created = Assessment.objects.get_or_create(
                audit_result=audit_result,
                assessment_type=Assessment.AssessmentType.IMAGE,
                blob_id=image.blob_id,
                defaults={
                    "question_id": image.question_id or "",
                    "result": None,  # Not assessed yet
                    "notes": "",
                },
            )

            if created:
                stats["assessments_created"] += 1

    return stats


def generate_assessments_for_visit(audit_result: AuditResult) -> dict:
    """
    Generate assessments for a single visit/audit result.
    Useful for incremental updates or when visits are added later.

    Args:
        audit_result: AuditResult to generate assessments for

    Returns:
        Dictionary with statistics
    """
    stats = {
        "images_processed": 0,
        "assessments_created": 0,
    }

    visit = audit_result.user_visit
    images = visit.images.all()

    for image in images:
        stats["images_processed"] += 1

        assessment, created = Assessment.objects.get_or_create(
            audit_result=audit_result,
            assessment_type=Assessment.AssessmentType.IMAGE,
            blob_id=image.blob_id,
            defaults={
                "question_id": image.question_id or "",
                "result": None,
                "notes": "",
            },
        )

        if created:
            stats["assessments_created"] += 1

    return stats
