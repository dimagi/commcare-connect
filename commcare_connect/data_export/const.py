APP_TYPE_LEARN = "learn"
APP_TYPE_DELIVER = "deliver"
APP_TYPE_BOTH = "both"
VALID_APP_TYPES = (APP_TYPE_LEARN, APP_TYPE_DELIVER, APP_TYPE_BOTH)

LEARN_APP_KEY = "learn_app"
DELIVER_APP_KEY = "deliver_app"

# Attachment lists the user-visit export can include, each opted into with ?<name>=true.
# "images" are the form's image BlobMeta rows; "audio" are the visit's AudioAttachment rows,
# which carry their transcript and translation text as well.
ATTACHMENT_FIELDS = ("images", "audio")

# One indexed id__in lookup with no per-row work, so a large batch is cheap; sized so a
# caller holding a few hundred opportunities can check them all in one request.
OPPORTUNITY_ACCESS_CHECK_MAX_IDS = 500
