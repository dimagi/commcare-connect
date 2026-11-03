"""Reprocess 9 ULAB Ghana - Healed World forms rejected because of a renamed deliver unit ID.

One-off remediation. App build v31 of "ULAB Household Survey - Ghana" sends its deliver block
with ``id="ulab_hh_survey"``, but the opportunity's deliver unit has the slug
``ulab_hh_survey_du`` (the ID v49 sends). ``get_or_create_deliver_unit`` matches on
``(app, slug)``, so each v31 form creates a new deliver unit with no payment unit and is
rejected with "Payment unit is not configured for the deliver unit". The ``ProcessingError``
rolls the request back, so the stray deliver unit never persists, and requeuing from HQ
resends the same ID and fails the same way.

This script fetches each form from HQ's form API, rebuilds the payload HQ's
``ConnectFormRepeaterPayloadGenerator`` sends (top-level fields plus only the Connect blocks),
rewrites the deliver block ID to ``ulab_hh_survey_du`` and runs it through ``process_xform``.
Forms are processed in submission order. Limits are checked as of today, so visits beyond a
worker's claim are recorded as ``over_limit`` and can still be approved through visit review.

Usage: run once as a dry run, review the printed visits, then set ``DRY_RUN = False`` and
re-run::

    ./manage.py shell < scripts/reprocess_ulab_ghana_v31_forms.py
"""

import httpx
from django.db import transaction

from commcare_connect.form_receiver.const import CCC_LEARN_XMLNS
from commcare_connect.form_receiver.processor import process_xform
from commcare_connect.form_receiver.serializers import XFormSerializer
from commcare_connect.opportunity.models import Opportunity, UserVisit

DRY_RUN = True
OLD_ID, NEW_ID = "ulab_hh_survey", "ulab_hh_survey_du"
# Fill in the affected form IDs in submission order, so daily-limit counting matches the original day
XFORM_IDS = []

opp = Opportunity.objects.get(id=2378)
hq_server = opp.deliver_app.hq_server
api_key = opp.api_key
assert api_key, "opportunity has no HQ API key"
headers = {"Authorization": f"ApiKey {api_key.user.email}:{api_key.api_key}"}


def connect_blocks(node):
    """Keep only Connect-namespaced blocks at their original paths, like HQ's ConnectFormRepeaterPayloadGenerator."""
    if not isinstance(node, dict):
        return None
    if node.get("@xmlns") == CCC_LEARN_XMLNS:
        return node
    kept = {k: pruned for k, v in node.items() if (pruned := connect_blocks(v)) is not None}
    return kept or None


def build_payload(form_id):
    url = f"{hq_server.url}/a/{opp.deliver_app.cc_domain}/api/v0.4/form/{form_id}/"
    full = httpx.get(url, headers=headers, timeout=30).raise_for_status().json()
    payload = {f: full.get(f) for f in ("domain", "id", "app_id", "build_id", "received_on", "metadata")}
    payload["form"] = connect_blocks(full["form"])
    deliver_blocks = [b for b in _walk(payload["form"]) if b.get("@id") == OLD_ID]
    assert len(deliver_blocks) == 1, f"{form_id}: expected one '{OLD_ID}' deliver block, got {len(deliver_blocks)}"
    deliver_blocks[0]["@id"] = NEW_ID
    return payload


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)


class DryRunRollback(Exception):
    pass


# Skip forms a previous committed run already processed, so a re-run doesn't hit the unique constraint
already_processed = set(UserVisit.objects.filter(xform_id__in=XFORM_IDS).values_list("xform_id", flat=True))
for form_id in sorted(already_processed):
    print(f"{form_id}: already has a visit, skipping")
payloads = [build_payload(form_id) for form_id in XFORM_IDS if form_id not in already_processed]
try:
    with transaction.atomic():
        for payload in payloads:
            serializer = XFormSerializer(data=payload)
            serializer.is_valid(raise_exception=True)
            xform = serializer.save()
            process_xform(xform, hq_server)
            for v in UserVisit.objects.filter(xform_id=xform.id):
                print(
                    f"{xform.id} -> visit {v.id} du={v.deliver_unit_id} status={v.status} "
                    f"user={v.user.username} reasons={v.over_limit_reasons}"
                )
        if DRY_RUN:
            raise DryRunRollback
except DryRunRollback:
    print("DRY RUN: rolled back. Set DRY_RUN = False and re-run to commit.")
