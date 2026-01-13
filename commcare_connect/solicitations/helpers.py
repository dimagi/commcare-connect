"""
Helper functions for the solicitations app.

Business logic functions for solicitation management, response processing,
and dashboard statistics.
"""

from django.db.models import Count, Q

from .models import Solicitation, SolicitationQuestion, SolicitationResponse


def get_user_organization_context(user):
    """
    Get organization context for a user.

    Args:
        user: User instance

    Returns:
        dict with organization context or None if no membership
    """
    if not user.memberships.exists():
        return None

    membership = user.memberships.first()
    return {"organization": membership.organization, "membership": membership, "has_organization": True}


def calculate_response_permissions(user, solicitation):
    """
    Calculate response permissions for a user and solicitation.

    Args:
        user: User instance
        solicitation: Solicitation instance

    Returns:
        dict with permission results and context
    """
    result = {
        "can_respond": False,
        "error_message": None,
        "redirect_needed": False,
        "existing_draft": None,
        "existing_submitted_response": None,
    }

    # Check if solicitation accepts responses
    if not solicitation.can_accept_responses:
        result["error_message"] = "This solicitation is not currently accepting responses."
        result["redirect_needed"] = True
        return result

    # Check if user has organization membership
    org_context = get_user_organization_context(user)
    if not org_context:
        result["error_message"] = "organization_required"
        return result

    user_org = org_context["organization"]

    # Check if organization already submitted a response (not draft)
    existing_submitted_response = SolicitationResponse.objects.filter(
        solicitation=solicitation,
        organization=user_org,
        status=SolicitationResponse.Status.SUBMITTED,
    ).first()

    if existing_submitted_response:
        result["existing_submitted_response"] = existing_submitted_response
        result["error_message"] = (
            f"Your organization has already submitted a response on "
            f"{existing_submitted_response.submission_date.strftime('%B %d, %Y')}."
        )
        result["redirect_needed"] = True
        return result

    # Check if there's an existing draft
    existing_draft = SolicitationResponse.objects.filter(
        solicitation=solicitation,
        organization=user_org,
        status=SolicitationResponse.Status.DRAFT,
    ).first()

    result["can_respond"] = True
    result["existing_draft"] = existing_draft
    return result


def process_question_form_data(cleaned_data, is_draft_save=False):
    """
    Process question form data from SolicitationResponseForm.

    Args:
        cleaned_data: Form's cleaned_data dict
        is_draft_save: Whether this is a draft save or final submission

    Returns:
        dict with processed responses data

    Raises:
        SolicitationQuestion.DoesNotExist: If form references non-existent question
    """
    responses_data = {}

    for field_name, value in cleaned_data.items():
        if field_name.startswith("question_"):
            question_id = field_name.split("_")[1]
            # Don't silently ignore missing questions - this indicates data corruption
            question = SolicitationQuestion.objects.get(id=question_id)

            # For drafts, save all values (including empty ones to preserve user's clearing of fields)
            # For submissions, only save non-empty values
            if is_draft_save:
                responses_data[question.question_text] = value
            elif value:  # For submission, only save non-empty values
                responses_data[question.question_text] = value

    return responses_data


def get_existing_response_for_organization(solicitation, organization, include_drafts=True):
    """
    Get existing response for an organization to a solicitation.

    Args:
        solicitation: Solicitation instance
        organization: Organization instance
        include_drafts: Whether to include draft responses

    Returns:
        SolicitationResponse instance or None
    """
    queryset = SolicitationResponse.objects.filter(
        solicitation=solicitation,
        organization=organization,
    )

    if not include_drafts:
        queryset = queryset.exclude(status=SolicitationResponse.Status.DRAFT)

    return queryset.first()


def get_solicitation_dashboard_statistics(solicitations_queryset):
    """
    Calculate dashboard statistics for a set of solicitations.

    Args:
        solicitations_queryset: Queryset of solicitations

    Returns:
        dict with statistics
    """
    stats = {
        "total_solicitations": solicitations_queryset.count(),
        "active_eois": solicitations_queryset.filter(
            solicitation_type=Solicitation.Type.EOI, status=Solicitation.Status.ACTIVE
        ).count(),
        "active_rfps": solicitations_queryset.filter(
            solicitation_type=Solicitation.Type.RFP, status=Solicitation.Status.ACTIVE
        ).count(),
        "total_responses": 0,
        "draft_count": solicitations_queryset.filter(status=Solicitation.Status.DRAFT).count(),
        "active_count": solicitations_queryset.filter(status=Solicitation.Status.ACTIVE).count(),
        "closed_count": solicitations_queryset.filter(status=Solicitation.Status.CLOSED).count(),
    }

    # Calculate total responses across all solicitations
    annotated_solicitations = solicitations_queryset.annotate(
        total_responses=Count("responses", filter=~Q(responses__status=SolicitationResponse.Status.DRAFT))
    )
    stats["total_responses"] = sum(s.total_responses or 0 for s in annotated_solicitations)

    return stats


def process_solicitation_questions(questions_data_json, solicitation):
    """
    Process question form data for solicitation creation/editing.

    Args:
        questions_data_json: JSON string containing questions data from form
        solicitation: Solicitation instance to associate questions with

    Returns:
        tuple: (success: bool, error_message: str or None)
    """
    import json

    if not questions_data_json:
        return True, None

    try:
        questions = json.loads(questions_data_json)

        # Create question objects - let Django model validation handle any issues
        for question_data in questions:
            SolicitationQuestion.objects.create(
                solicitation=solicitation,
                question_text=question_data.get("question_text", ""),
                question_type=question_data.get("question_type", SolicitationQuestion.Type.TEXTAREA),
                is_required=question_data.get("is_required", True),
                options=question_data.get("options", None),
                order=question_data.get("order", 1),
            )

        return True, None

    except json.JSONDecodeError as e:
        return False, f"Malformed JSON in questions data: {str(e)}"
    except Exception as e:
        return False, f"Error processing questions: {str(e)}"


def build_question_context(solicitation, is_edit=False):
    """
    Build question context for template rendering.

    Args:
        solicitation: Solicitation instance (None for create mode)
        is_edit: Whether this is edit mode

    Returns:
        dict with question context
    """
    import json

    if is_edit and solicitation:
        # Load existing questions for editing
        existing_questions = list(
            solicitation.questions.all()
            .order_by("order")
            .values("id", "question_text", "question_type", "is_required", "options", "order")
        )
        return {
            "existing_questions": json.dumps(existing_questions),
            "is_create": False,
        }
    else:
        # No questions for new solicitations
        return {
            "existing_questions": json.dumps([]),
            "is_create": True,
        }


def update_solicitation_questions(questions_data_json, solicitation):
    """
    Update questions for an existing solicitation.

    Args:
        questions_data_json: JSON string containing questions data from form
        solicitation: Solicitation instance to update questions for

    Returns:
        tuple: (success: bool, error_message: str or None)
    """
    import json

    if not questions_data_json:
        return True, None

    try:
        questions = json.loads(questions_data_json)

        # Delete existing questions and create new ones
        # This is simpler than trying to update in place
        solicitation.questions.all().delete()

        # Create new question objects
        for question_data in questions:
            SolicitationQuestion.objects.create(
                solicitation=solicitation,
                question_text=question_data.get("question_text", ""),
                question_type=question_data.get("question_type", SolicitationQuestion.Type.TEXTAREA),
                is_required=question_data.get("is_required", True),
                options=question_data.get("options", None),
                order=question_data.get("order", 1),
            )

        return True, None

    except json.JSONDecodeError as e:
        return False, f"Malformed JSON in questions data: {str(e)}"
    except Exception as e:
        return False, f"Error updating questions: {str(e)}"
