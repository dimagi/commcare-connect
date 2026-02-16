from django.contrib.gis.db import models as geo_models
from django.utils.translation import gettext_lazy as _

from commcare_connect.opportunity.models import Opportunity, OpportunityAccess

SRID = 4326


class WorkAreaStatus(geo_models.TextChoices):
    NOT_STARTED = "NOT_STARTED", _("Not Started")
    UNASSIGNED = "UNASSIGNED", _("Unassigned")
    NOT_VISITED = "NOT_VISITED", _("Not Visited")
    VISITED = "VISITED", _("Visited")
    REQUEST_FOR_INACCESSIBLE = "REQUEST_FOR_INACCESSIBLE", _("Request for Inaccessible")
    EXPECTED_VISIT_REACHED = "EXPECTED_VISIT_REACHED", _("Expected Visit Count Reached")
    INACCESSIBLE = "INACCESSIBLE", _("Inaccessible")
    EXCLUDED = "EXCLUDED", _("Excluded")


class WorkAreaGroup(geo_models.Model):
    opportunity = geo_models.ForeignKey(Opportunity, on_delete=geo_models.CASCADE)
    assigned_user = geo_models.ForeignKey(OpportunityAccess, null=True, blank=True, on_delete=geo_models.SET_NULL)
    ward = geo_models.SlugField(max_length=255)
    name = geo_models.CharField(
        max_length=255,
    )

    class Meta:
        constraints = [geo_models.UniqueConstraint(fields=["name", "opportunity"], name="unique_name_per_opportunity")]


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
        srid=SRID, help_text="Centroid of the Work Area as a Point. Use (longitude, latitude) when assigning manually."
    )
    boundary = geo_models.PolygonField(srid=SRID)
    ward = geo_models.CharField(max_length=255)
    building_count = geo_models.PositiveIntegerField(default=0)
    expected_visit_count = geo_models.PositiveIntegerField(default=0)
    status = geo_models.CharField(
        max_length=50,
        choices=WorkAreaStatus.choices,
        default=WorkAreaStatus.NOT_STARTED,
    )

    class Meta:
        constraints = [geo_models.UniqueConstraint(fields=["slug", "opportunity"], name="unique_slug_per_opportunity")]

    def __str__(self):
        return f"{self.slug}-{self.opportunity_id}"
