from decimal import ROUND_HALF_UP, Decimal

import django_tables2 as tables
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django_tables2 import columns

from commcare_connect.opportunity.tables import STAT_COLUMN_ATTRS
from commcare_connect.solicitation.models import Solicitation, SolicitationApplicationReview
from commcare_connect.utils.tables import DMYTColumn, IndexColumn, order_empty_last, select_column


def details_url(record, table):
    if record.is_editable:
        return reverse("solicitation:details", args=(table.request.org.slug, record.solicitation_id))
    return None


def applications_url(record, table):
    return reverse(
        "received_applications:list",
        args=(table.request.org.slug,),
        query={"solicitation": record.solicitation_id},
    )


def invitations_url(record, table):
    # Invitations are only managed while a solicitation is open.
    if record.is_open:
        return reverse("solicitation:invitations", args=(table.request.org.slug, record.solicitation_id))
    return None


def reviewers_url(record, table):
    # Reviewers are managed until a solicitation is cancelled; scoring continues after it closes.
    if record.status != Solicitation.Status.CANCELLED:
        return reverse("solicitation:reviewers", args=(table.request.org.slug, record.solicitation_id))
    return None


class SolicitationTable(tables.Table):
    title = columns.Column(verbose_name=_("Title"), linkify=details_url, attrs={"a": {"class": "underline"}})
    type = columns.Column(verbose_name=_("Type"))
    visibility = columns.Column(verbose_name=_("Visibility"), accessor="public")
    status = columns.TemplateColumn(verbose_name=_("Status"), template_name="solicitation/status_badge.html")
    application_deadline = DMYTColumn(verbose_name=_("Deadline"))
    invited_count = columns.Column(
        verbose_name=_("Invited"), linkify=invitations_url, attrs=STAT_COLUMN_ATTRS | {"a": {"class": "underline"}}
    )
    reviewer_count = columns.Column(
        verbose_name=_("Review Team"), linkify=reviewers_url, attrs=STAT_COLUMN_ATTRS | {"a": {"class": "underline"}}
    )
    response_count = columns.Column(
        verbose_name=_("Responses"), linkify=applications_url, attrs=STAT_COLUMN_ATTRS | {"a": {"class": "underline"}}
    )
    actions = columns.Column(verbose_name="", orderable=False, empty_values=())

    class Meta:
        model = Solicitation
        fields = (
            "title",
            "type",
            "visibility",
            "status",
            "application_deadline",
            "invited_count",
            "reviewer_count",
            "response_count",
        )
        empty_text = _("No solicitations yet.")

    def render_visibility(self, value):
        return _("Public") if value else _("Private")

    def render_actions(self, record):
        url_args = (self.request.org.slug, record.solicitation_id)
        actions = []
        if url := reviewers_url(record, self):
            # A page of its own, so it opts out of the menu's boost into the list page's modal.
            actions.append({"title": _("Manage Review Team"), "url": url, "hx_boost": "false"})
        if record.is_open:
            actions.append({"title": _("Close"), "url": reverse("solicitation:close", args=url_args)})
        if record.is_editable:
            actions.append({"title": _("Cancel"), "url": reverse("solicitation:cancel", args=url_args)})
        # Cancelled solicitations are finished, so they get no menu.
        return render_to_string("solicitation/row_actions.html", {"actions": actions}) if actions else ""


class SolicitationInvitationTable(tables.Table):
    select = select_column()
    index = IndexColumn()
    organization = columns.Column(verbose_name=_("Organization"), accessor="organization__name")
    invited_by = columns.Column(verbose_name=_("Invited by"), orderable=False)
    date_created = DMYTColumn(verbose_name=_("Invited on"))

    class Meta:
        empty_text = _("No organizations invited yet.")


class SolicitationReviewerTable(tables.Table):
    select = select_column()
    index = IndexColumn()
    name = columns.Column(verbose_name=_("Name"), accessor="user__name")
    email = columns.Column(verbose_name=_("Email"), accessor="user__email")
    role = columns.Column(verbose_name=_("Role"))
    invited_by = columns.Column(verbose_name=_("Added by"), orderable=False)
    date_created = DMYTColumn(verbose_name=_("Added on"))

    class Meta:
        empty_text = _("No reviewers or observers yet.")


Recommendation = SolicitationApplicationReview.Recommendation
RECOMMENDATION_BADGE_CLASSES = {
    Recommendation.APPROVE: "positive-light",
    Recommendation.NEEDS_REVISION: "warning-light",
    Recommendation.REJECT: "negative-light",
}


class SolicitationApplicationTable(tables.Table):
    """Applications with how far their review has got.

    Expects rows annotated by SolicitationApplicationQuerySet.with_review_summary.
    """

    # Each sortable column also orders by pk, so rows with equal values stay in the same order across pages.
    solicitation = columns.Column(
        verbose_name=_("Solicitation"), accessor="solicitation__title", order_by=("solicitation__title", "pk")
    )
    organization = columns.Column(
        verbose_name=_("Organization"), accessor="organization__name", order_by=("organization__name", "pk")
    )
    submitted_date = DMYTColumn(verbose_name=_("Submitted"), order_by=("submitted_date", "pk"))
    status = columns.TemplateColumn(
        verbose_name=_("Status"),
        template_name="solicitation/partials/application_status_badge.html",
        order_by=("status", "pk"),
    )
    shortlisted = columns.BooleanColumn(
        verbose_name=_("Shortlisted"),
        yesno=(_("Yes"), _("No")),
        attrs=STAT_COLUMN_ATTRS,
        order_by=("shortlisted", "pk"),
    )
    average_score = columns.Column(verbose_name=_("Avg. score (/100)"), attrs=STAT_COLUMN_ATTRS)
    recommendations = columns.Column(verbose_name=_("Recommendations"), empty_values=(), orderable=False)
    review_progress = columns.TemplateColumn(
        verbose_name=_("Reviews"), template_name="solicitation/partials/review_progress.html", orderable=False
    )

    class Meta:
        empty_text = _("No applications yet.")

    def render_average_score(self, value):
        return value.quantize(Decimal(1), rounding=ROUND_HALF_UP)

    def order_average_score(self, queryset, is_descending):
        # Unscored applications sort last.
        return order_empty_last(queryset=queryset, field="average_score", is_descending=is_descending)

    def render_recommendations(self, record):
        badges = []
        for recommendation, badge_class in RECOMMENDATION_BADGE_CLASSES.items():
            count = getattr(record, f"{recommendation}_count")
            if count:
                badges.append((recommendation.label, count, badge_class))
        return render_to_string(template_name="solicitation/partials/recommendations.html", context={"badges": badges})
