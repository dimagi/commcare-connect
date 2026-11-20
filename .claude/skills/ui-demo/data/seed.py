"""Realistic, idempotent demo seed for commcare-connect. Run via reseed.sh (manage.py shell < seed.py).

Nothing on screen should read as test data: real-looking names, one plausible org domain, current dates.
Keep in sync with connect.mjs (USERS / ORG / PASSWORD).
"""

from allauth.account.models import EmailAddress

from commcare_connect.organization.models import Organization, OrganizationInvite
from commcare_connect.organization.models import UserOrganizationMembership as M
from commcare_connect.users.models import User

PASSWORD = "Connect2026!"
# Names and emails encode the role so a viewer can tell who is who from the screen alone.
# Org names come from the Connect programme catalogue (Child Health Campaign, Kangaroo Mother Care, ...).
ORG = {"name": "Child Health Campaign", "slug": "child-health-campaign"}  # slug is generated from the name
OTHER_ORGS = ["Kangaroo Mother Care", "Mother Baby Wellness"]  # exist as separate workspaces for multi-org demos
DOMAIN = "childhealth.org"
PEOPLE = [
    # email, name, role in org (None = has an account but is not a member), extra user flags
    (f"asha.admin@{DOMAIN}", "Asha Admin", M.Role.ADMIN, {"is_staff": True, "is_superuser": True}),
    (f"ravi.admin@{DOMAIN}", "Ravi Admin", M.Role.ADMIN, {}),
    (f"meera.member@{DOMAIN}", "Meera Member", M.Role.MEMBER, {}),
    (f"vikram.viewer@{DOMAIN}", "Vikram Viewer", M.Role.VIEWER, {}),
    (f"isha.invitee@{DOMAIN}", "Isha Invitee", None, {}),  # has a Connect account, not in the org yet
]
NEWCOMERS = [f"neel.new@{DOMAIN}"]  # must NOT exist at the start of a demo (brand-new signup path)


def upsert_user(email, name, **flags):
    user, _ = User.objects.get_or_create(email=email, defaults={"username": email.split("@")[0], "name": name})
    user.name = name
    for k, v in flags.items():
        setattr(user, k, v)
    # Only rotate the hash when needed: Django's session auth hash is derived from the password hash, so an
    # unconditional set_password() logs out every live session of this user — including another recording
    # running against the same local DB.
    if not user.check_password(PASSWORD):
        user.set_password(PASSWORD)
    user.save()
    # allauth: mark verified so login never bounces to the email-verification page mid-demo
    EmailAddress.objects.update_or_create(user=user, email=email, defaults={"verified": True, "primary": True})
    return user


# Organization.save() derives the slug from the name (slugify_uniquely) — look up by name and let the model slug it.
org = Organization.objects.filter(name=ORG["name"]).first() or Organization.objects.create(name=ORG["name"])
if org.slug != ORG["slug"]:
    raise SystemExit(f"org slug is {org.slug!r}; update ORG.slug in seed.py and connect.mjs")
for name in OTHER_ORGS:
    Organization.objects.filter(name=name).first() or Organization.objects.create(name=name)

for email, name, role, flags in PEOPLE:
    user = upsert_user(email, name, **flags)
    if role:
        M.objects.update_or_create(organization=org, user=user, defaults={"role": role})
    else:
        M.objects.filter(organization=org, user=user).delete()

# Anyone else in the demo org (e.g. personas from an older seed) would show up on camera — drop them.
M.objects.filter(organization=org).exclude(user__email__in=[p[0] for p in PEOPLE if p[2]]).delete()
OrganizationInvite.objects.filter(organization=org).delete()
User.objects.filter(email__in=NEWCOMERS).delete()

print(
    "seeded",
    org.slug,
    sorted(org.memberships.values_list("user__email", "role")),
    "newcomers cleared:",
    NEWCOMERS,
)
