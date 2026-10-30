"""Stand-ins used while delivering and reviewing visits.

None are needed: the only outbound call is ``download_user_visit_attachments``, which fetches a visit's attachments
from HQ but skips ``form.xml``, and simulated deliver forms carry no other attachment.
"""


def patches():
    return []
