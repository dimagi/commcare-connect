from functools import cached_property

import pghistory
import shapely
from django.conf import settings
from django.contrib.gis.db import models as geo_models
from django.db.models import Count, Index, Q, Sum
from django.utils.translation import gettext_lazy as _

from commcare_connect.opportunity.models import Opportunity, OpportunityAccess, UserVisit, VisitValidationStatus

# The most common SRID for geographic coordinates is 4326,
# which corresponds to “longitude/latitude on the WGS84 spheroid
SRID = 4326


class WorkAreaStatus(geo_models.TextChoices):
    UNASSIGNED = "UNASSIGNED", _("Unassigned")
    NOT_VISITED = "NOT_VISITED", _("Not Visited")
    VISITED = "VISITED", _("Visited")
    REQUEST_FOR_INACCESSIBLE = "REQUEST_FOR_INACCESSIBLE", _("Request for Inaccessible")
    EXPECTED_VISIT_REACHED = "EXPECTED_VISIT_REACHED", _("Expected Visit Count Reached")
    INACCESSIBLE = "INACCESSIBLE", _("Inaccessible")
    EXCLUDED = "EXCLUDED", _("Excluded")


class WorkAreaGroup(geo_models.Model):
    opportunity = geo_models.ForeignKey(Opportunity, on_delete=geo_models.CASCADE)
    ward = geo_models.SlugField(max_length=255)
    name = geo_models.CharField(max_length=255)
    centroid = geo_models.PointField(srid=SRID, null=True, help_text="Centroid of the Work Area Group as a Point.")

    def __str__(self):
        return self.name

    class Meta:
        constraints = [geo_models.UniqueConstraint(fields=["name", "opportunity"], name="unique_name_per_opportunity")]

    @cached_property
    def building_count(self):
        return (
            self.workarea_set.exclude(status=WorkAreaStatus.EXCLUDED).aggregate(total=Sum("building_count"))["total"]
            or 0
        )

    def update_centroid(self, commit=True):
        """
        Calculates the Work Area Group centroid from the Work Area boundaries
        and saves if the centroid value has changed.
        The commit parameter (default True) will save the change to database,
        to save manually set it to False.
        """
        work_areas_boundaries = self.workarea_set.exclude(
            status__in=[WorkAreaStatus.EXCLUDED, WorkAreaStatus.INACCESSIBLE]
        ).values_list("boundary", flat=True)
        old_centroid = self.centroid
        if work_areas_boundaries:
            self.centroid = shapely.MultiPolygon(work_areas_boundaries).centroid.wkt
        else:
            self.centroid = None
        if commit and old_centroid != self.centroid:
            self.save()


@pghistory.track(
    fields=["expected_visit_count", "work_area_group", "status", "opportunity_access", "excluded_reason"],
    meta={
        "indexes": [
            Index(fields=["pgh_obj", "status", "pgh_created_at"], name="wae_obj_status_created_idx"),
        ],
    },
)
class WorkArea(geo_models.Model):
    work_area_group = geo_models.ForeignKey(WorkAreaGroup, null=True, blank=True, on_delete=geo_models.SET_NULL)
    opportunity = geo_models.ForeignKey(Opportunity, on_delete=geo_models.CASCADE)
    opportunity_access = geo_models.ForeignKey(OpportunityAccess, null=True, blank=True, on_delete=geo_models.SET_NULL)
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
    ward = geo_models.SlugField(max_length=255)
    building_count = geo_models.PositiveIntegerField(default=0)
    expected_visit_count = geo_models.PositiveIntegerField(default=0)
    target_population = geo_models.PositiveIntegerField(default=0)
    status = geo_models.CharField(
        max_length=50,
        choices=WorkAreaStatus.choices,
        default=WorkAreaStatus.UNASSIGNED,
    )
    case_id = geo_models.CharField(max_length=255, unique=True, null=True)
    case_properties = geo_models.JSONField(default=dict, null=True, blank=True)
    excluded_by = geo_models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=geo_models.SET_NULL,
        related_name="excluded_work_areas",
    )
    excluded_reason = geo_models.CharField(max_length=500, blank=True, default="")

    class Meta:
        constraints = [geo_models.UniqueConstraint(fields=["slug", "opportunity"], name="unique_slug_per_opportunity")]

    def __str__(self):
        return f"{self.slug}-{self.opportunity_id}"

    VISIT_TRACKABLE_STATUSES = {
        WorkAreaStatus.NOT_VISITED,
        WorkAreaStatus.VISITED,
        WorkAreaStatus.EXPECTED_VISIT_REACHED,
    }

    def update_status(self):
        if self.status not in self.VISIT_TRACKABLE_STATUSES or self.opportunity_access is None:
            return

        counts = UserVisit.objects.filter(opportunity_access=self.opportunity_access, work_area=self).aggregate(
            total=Count("id"),
            approved=Count("id", filter=Q(status=VisitValidationStatus.approved)),
        )

        new_status = WorkAreaStatus.VISITED if counts["total"] else self.status
        if self.expected_visit_count and counts["approved"] >= self.expected_visit_count:
            new_status = WorkAreaStatus.EXPECTED_VISIT_REACHED

        if new_status != self.status:
            user = self.opportunity_access.user
            self.status = new_status
            with pghistory.context(username=user.username, user_email=user.email):
                self.save(update_fields=["status"])


class InaccessibilityRequestStatus(geo_models.TextChoices):
    PENDING = "PENDING", _("Pending Review")
    APPROVED = "APPROVED", _("Approved")
    DENIED = "DENIED", _("Denied")


class WorkAreaInaccessibilityRequest(geo_models.Model):
    work_area = geo_models.ForeignKey(WorkArea, on_delete=geo_models.CASCADE)
    opportunity_access = geo_models.ForeignKey(OpportunityAccess, on_delete=geo_models.CASCADE)
    xform_id = geo_models.CharField(max_length=50)
    date_of_visit = geo_models.DateField()
    location = geo_models.PointField(null=True, blank=True, srid=SRID)
    reason = geo_models.CharField(max_length=256)
    additional_details = geo_models.TextField(blank=True, default="")
    status = geo_models.CharField(
        max_length=20,
        choices=InaccessibilityRequestStatus.choices,
        default=InaccessibilityRequestStatus.PENDING,
    )

    class Meta:
        constraints = [
            geo_models.UniqueConstraint(
                fields=["work_area"],
                condition=Q(status=InaccessibilityRequestStatus.PENDING),
                name="unique_pending_work_area_inaccessibility",
            )
        ]

    def __str__(self):
        return f"WorkAreaInaccessibilityRequest {self.xform_id} - {self.work_area}"
