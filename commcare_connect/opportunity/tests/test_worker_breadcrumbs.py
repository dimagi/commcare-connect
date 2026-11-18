import pytest
from django.urls import reverse

from commcare_connect.opportunity.views.worker import get_worker_breadcrumbs


@pytest.mark.django_db
@pytest.mark.parametrize(
    "connect_workers_url_name",
    [
        # BaseWorkerListView: the Connect Workers tabs
        "opportunity:worker_list",
        # WorkerPageView: per-worker Visits and Tasks pages, opened from the Deliver tab
        "opportunity:worker_deliver",
        # worker_learn_status_view: per-worker learn progress, opened from the Learn tab
        "opportunity:worker_learn",
    ],
    ids=["worker-tabs", "worker-page", "learn-status"],
)
def test_get_worker_breadcrumbs(opportunity, connect_workers_url_name):
    org_slug = opportunity.organization.slug
    opp_id = opportunity.opportunity_id

    path = get_worker_breadcrumbs(org_slug, opportunity, connect_workers_url_name)

    programs_url = reverse("program:home", args=(org_slug,))
    assert [(crumb["title"], crumb["url"]) for crumb in path] == [
        ("Programs", programs_url),
        (opportunity.program.name, programs_url),
        ("Opportunities", reverse("opportunity:list", args=(org_slug,))),
        (opportunity.name, reverse("opportunity:detail", args=(org_slug, opp_id))),
        ("Connect Workers", reverse(connect_workers_url_name, args=(org_slug, opp_id))),
    ]
