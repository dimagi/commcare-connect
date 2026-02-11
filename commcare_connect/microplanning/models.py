from django.contrib.gis.db import models as geo_models
from django.utils.translation import gettext_lazy as _

from commcare_connect.opportunity.models import Opportunity, OpportunityAccess


class WorkAreaStatus:
    NOT_STARTED = "not_started"

    # To Do
    UNASSIGNED = "unassigned"
    NOT_VISITED = "not_visited"

    # In Progress
    VISITED = "visited"
    REQUEST_INACCESSIBLE = "request_inaccessible"

    # Done (Terminal)
    EXPECTED_VISIT_REACHED = "expected_visit_reached"
    INACCESSIBLE = "inaccessible"
    EXCLUDED = "excluded"

    CHOICES = [
        (NOT_STARTED, _("Not Started")),
        (UNASSIGNED, _("Unassigned")),
        (NOT_VISITED, _("Not Visited")),
        (VISITED, _("Visited")),
        (REQUEST_INACCESSIBLE, _("Request for Inaccessible")),
        (EXPECTED_VISIT_REACHED, _("Expected Visit Count Reached")),
        (INACCESSIBLE, _("Inaccessible")),
        (EXCLUDED, _("Excluded")),
    ]

    TO_DO_STATES = {UNASSIGNED, NOT_VISITED}
    IN_PROGRESS_STATES = {VISITED, REQUEST_INACCESSIBLE}
    TERMINAL_STATES = {EXPECTED_VISIT_REACHED, INACCESSIBLE, EXCLUDED}


class WorkAreaGroup(geo_models.Model):
    opportunity = geo_models.ForeignKey(Opportunity, on_delete=geo_models.CASCADE)
    assigned_user = geo_models.ForeignKey(OpportunityAccess, on_delete=geo_models.CASCADE)
    ward = geo_models.SlugField(max_length=255)
    name = geo_models.CharField(max_length=255)


class WorkArea(geo_models.Model):
    work_area_group = geo_models.ForeignKey(WorkAreaGroup, null=True, blank=True, on_delete=geo_models.SET_NULL)
    opportunity = geo_models.ForeignKey(Opportunity, on_delete=geo_models.CASCADE)
    slug = geo_models.SlugField(
        max_length=255,
        help_text=(
            "Unique identifier for the Work Area within an Opportunity. "
            "Automatically generated slugs must be unique per opportunity."
        ),
    )
    centroid = geo_models.PointField(
        srid=4326, help_text="Centroid of the Work Area as a Point. Use (longitude, latitude) when assigning manually."
    )
    boundary = geo_models.PolygonField(srid=4326)
    ward = geo_models.CharField(max_length=255)
    building_count = geo_models.PositiveIntegerField(default=0)
    expected_visit_count = geo_models.PositiveIntegerField(default=0)
    status = geo_models.CharField(
        choices=WorkAreaStatus.CHOICES,
        default=WorkAreaStatus.NOT_STARTED,
    )

    class Meta:
        constraints = [geo_models.UniqueConstraint(fields=["slug", "opportunity"], name="unique_slug_per_opportunity")]

    def __str__(self):
        return f"{self.slug}-{self.opportunity_id}"
