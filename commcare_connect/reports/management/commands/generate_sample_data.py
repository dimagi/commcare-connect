import io
import mimetypes
import random
import wave
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from functools import cache
from itertools import cycle
from pathlib import Path
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone as djtimezone
from faker import Faker
from PIL import Image

from commcare_connect.opportunity.models import (
    Assessment,
    AudioAttachment,
    BlobMeta,
    CompletedModule,
    CompletedWork,
    DeliverUnit,
    ExchangeRate,
    HQApiKey,
    Opportunity,
    OpportunityAccess,
    OpportunityClaim,
    Payment,
    PaymentInvoice,
    PaymentUnit,
    UserInvite,
    UserInviteStatus,
    UserVisit,
)
from commcare_connect.opportunity.tests.factories import (
    AssessmentFactory,
    CompletedModuleFactory,
    CompletedWorkFactory,
    DeliverUnitFactory,
    DeliveryTypeFactory,
    HQApiKeyFactory,
    OpportunityAccessFactory,
    OpportunityClaimFactory,
    OpportunityFactory,
    OpportunityVerificationFlagsFactory,
    PaymentFactory,
    PaymentInvoiceFactory,
    PaymentUnitFactory,
    UserInviteFactory,
    UserVisitFactory,
)
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import Program, ProgramApplication
from commcare_connect.program.tests.factories import ProgramApplicationFactory, ProgramFactory
from commcare_connect.users.tests.factories import MobileUserFactory

User = get_user_model()
fake = Faker()

# Sample data never reaches the real exchange rate service, so invoices are priced off these.
SAMPLE_EXCHANGE_RATE_DATE = date(2020, 1, 1)
SAMPLE_EXCHANGE_RATES = {"USD": Decimal("1")}
DEFAULT_SAMPLE_EXCHANGE_RATE = Decimal("100")

FORM_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
SAMPLE_FORM_XMLNS = "http://openrosa.org/formdesigner/sample-deliver-form"
# The HQ API key user is looked up by name so reruns reuse it instead of minting a new one.
SAMPLE_API_KEY_USERNAME = "sample-hq-api"

IMAGE_KIND = "image"
AUDIO_KIND = "audio"

# Without --media_dir, attachment bytes are generated here rather than downloaded from HQ, so they
# are still real files a browser can open and a player can play. One image and one audio clip are
# built once and shared: each is written to MEDIA_ROOT under its own blob_id, so keep them small.
SAMPLE_IMAGE_SIZE = (320, 240)
SAMPLE_IMAGE_COLOR = (70, 130, 180)
SAMPLE_AUDIO_RATE = 8000
SAMPLE_AUDIO_SECONDS = 1
# Stand-ins for the transcription service, which local setups have no credentials for.
SAMPLE_TRANSCRIPTS = [
    ("Namaste, aaj main teen ghar gaya.", "Hello, I visited three households today."),
    ("Bachche ko bukhar tha, dawa di.", "The child had a fever, so I gave medicine."),
    ("Maa aur bachcha dono theek hain.", "The mother and baby are both well."),
]


class Command(BaseCommand):
    help = "Generates fake data for testing purposes"

    def add_arguments(self, parser):
        parser.add_argument("num_visits", type=int, help="Number of visits to generate")
        parser.add_argument("org_slug", type=str, help="Primary organization slug to scope the data to")
        parser.add_argument(
            "--invited_org_slug",
            type=str,
            default=None,
            help="Slug for the invited organization (if not provided, one will be created)",
        )
        parser.add_argument(
            "--managed_opportunities",
            type=int,
            default=3,
            help="Number of managed opportunities to create",
        )
        parser.add_argument(
            "--image_percent",
            type=int,
            default=0,
            help="Percentage of visits that get a photo. Off by default.",
        )
        parser.add_argument(
            "--audio_percent",
            type=int,
            default=0,
            help=(
                "Percentage of visits that get one to three audio recordings, with transcripts "
                "and translations. Off by default."
            ),
        )
        parser.add_argument(
            "--media_dir",
            type=str,
            default=None,
            help=(
                "Directory to draw attachment files from instead of the generated placeholders. "
                "Searched recursively and sorted by type, so images become photos and audio "
                "becomes recordings. A type the directory has none of falls back to a placeholder."
            ),
        )

    def _sample_exchange_rate(self, currency_code):
        """A stand-in rate, so sample invoices carry a USD amount like real ones do.

        Real rates are fetched from an external service that local environments have no key for.
        """
        rate, _ = ExchangeRate.objects.get_or_create(
            currency_code=currency_code,
            rate_date=SAMPLE_EXCHANGE_RATE_DATE,
            defaults={"rate": SAMPLE_EXCHANGE_RATES.get(currency_code, DEFAULT_SAMPLE_EXCHANGE_RATE)},
        )
        return rate

    def clean_sample_data(self, org_ids):
        self.stdout.write("Cleaning up previous sample data...")
        deletions = [
            # Sample payments for an opportunity nobody was assigned to have no access to match
            # on, so they have to be found through their invoice or they outlive it -- and
            # Payment.invoice is DO_NOTHING, so the orphan breaks the invoice delete at commit.
            lambda: Payment.objects.filter(
                Q(opportunity_access__opportunity__organization_id__in=org_ids)
                | Q(invoice__opportunity__organization_id__in=org_ids)
            ).delete(),
            lambda: PaymentInvoice.objects.filter(opportunity__organization_id__in=org_ids).delete(),
            lambda: self._delete_attachments(org_ids),
            lambda: UserVisit.objects.filter(opportunity__organization_id__in=org_ids).delete(),
            lambda: OpportunityClaim.objects.filter(
                opportunity_access__opportunity__organization_id__in=org_ids
            ).delete(),
            lambda: CompletedModule.objects.filter(opportunity__organization_id__in=org_ids).delete(),
            lambda: Assessment.objects.filter(opportunity__organization_id__in=org_ids).delete(),
            lambda: CompletedWork.objects.filter(
                opportunity_access__opportunity__organization_id__in=org_ids
            ).delete(),
            # DeliverUnit.payment_unit is DO_NOTHING, so the unit has to go before what it points
            # at. It hangs off the opportunity's deliver app rather than the opportunity itself.
            lambda: DeliverUnit.objects.filter(payment_unit__opportunity__organization_id__in=org_ids).delete(),
            lambda: PaymentUnit.objects.filter(opportunity__organization_id__in=org_ids).delete(),
            lambda: UserInvite.objects.filter(opportunity__organization_id__in=org_ids).delete(),
            lambda: OpportunityAccess.objects.filter(opportunity__organization_id__in=org_ids).delete(),
            lambda: Opportunity.objects.filter(organization_id__in=org_ids).delete(),
            lambda: ProgramApplication.objects.filter(program__organization_id__in=org_ids).delete(),
            lambda: Program.objects.filter(organization_id__in=org_ids).delete(),
        ]

        for delete in deletions:
            delete()
        self.stdout.write("Cleanup complete.")

    def _delete_attachments(self, org_ids):
        """Drops attachment rows and the files behind them before visits are deleted.

        AudioAttachment would cascade with its visit, but BlobMeta is keyed by xform_id rather
        than a foreign key, so nothing deletes it on our behalf. The stored files go too, or
        MEDIA_ROOT grows by a full set of blobs on every run.
        """
        xform_ids = UserVisit.objects.filter(opportunity__organization_id__in=org_ids).values("xform_id")
        blobs = BlobMeta.objects.filter(parent_id__in=xform_ids)
        audio = AudioAttachment.objects.filter(user_visit__opportunity__organization_id__in=org_ids)
        blob_ids = [*blobs.values_list("blob_id", flat=True), *audio.values_list("blob_id", flat=True)]
        # Rows first: if a delete fails the files are still there for the rows that survive it.
        blobs.delete()
        audio.delete()
        for blob_id in blob_ids:
            default_storage.delete(str(blob_id))

    def _warn(self, message):
        self.stdout.write(self.style.WARNING(message))

    def handle(self, *args, **options):
        num_visits = options["num_visits"]
        org_slug = options["org_slug"]
        invited_org_slug = options["invited_org_slug"]
        num_managed_opps = options["managed_opportunities"]
        image_percent = options["image_percent"]
        audio_percent = options["audio_percent"]
        # Checked here rather than as an argparse type, which call_command's keyword options skip.
        for name, percent in (("--image_percent", image_percent), ("--audio_percent", audio_percent)):
            if not 0 <= percent <= 100:
                raise CommandError(f"{name} must be between 0 and 100, got {percent}")
        media = MediaLibrary(options["media_dir"], warn=self._warn)
        if options["media_dir"] and not (image_percent or audio_percent):
            self._warn(
                "--media_dir has no effect without --image_percent or --audio_percent; no attachments will be made."
            )
        BATCH_SIZE = 10000

        org, created = Organization.objects.get_or_create(slug=org_slug, defaults={"name": org_slug, "slug": org_slug})
        if created:
            self.stdout.write(f"Created primary organization: {org.name}")
        else:
            self.stdout.write(f"Using existing primary organization: {org.name}")

        if invited_org_slug:
            invited_org, invited_created = Organization.objects.get_or_create(
                slug=invited_org_slug,
                defaults={"name": invited_org_slug, "slug": invited_org_slug},
            )
        else:
            invited_org = Organization.objects.create(
                name=f"{org_slug} Invited Org",
                slug=f"{fake.slug()}-{random.randint(1000, 9999)}",
            )
            invited_created = True
        if invited_created:
            self.stdout.write(f"Created invited organization: {invited_org.name}")
        else:
            self.stdout.write(f"Using existing invited organization: {invited_org.name}")

        org_ids = [org.id, invited_org.id]
        self.clean_sample_data(org_ids)

        self.stdout.write("Generating prerequisite data (users, opportunities, etc.)...")

        users = [MobileUserFactory() for _ in range(10)]
        api_key = _sample_api_key()
        # The org's own program, or the factory builds a Program in a fresh random Organization for
        # each one, which then supervises it and is never cleaned up.
        own_program = ProgramFactory(organization=org)
        opportunities = [OpportunityFactory(organization=org, api_key=api_key, program=own_program) for _ in range(5)]

        managed_opportunities = []
        for _ in range(num_managed_opps):
            program = ProgramFactory(organization=org, delivery_type=DeliveryTypeFactory())
            ProgramApplicationFactory(program=program, organization=invited_org)
            managed_opp = OpportunityFactory(
                organization=invited_org,
                program=program,
                api_key=api_key,
            )
            managed_opportunities.append(managed_opp)

        all_opportunities = opportunities + managed_opportunities
        # The form receiver get_or_creates these as visits arrive. These visits are written
        # straight to the database instead, so nothing else would -- and the visit detail page
        # reads the record without guarding for its absence.
        for opp in all_opportunities:
            OpportunityVerificationFlagsFactory(opportunity=opp)
        all_accesses = []
        payment_units = {}
        for user in users:
            selected_opps = random.sample(all_opportunities, k=random.randint(1, 3))
            for opp in selected_opps:
                # accepted=True to match the invite below, which is already created as accepted.
                # The worker tables filter on it, so unaccepted access leaves every tab empty
                # however many visits the worker has.
                access = OpportunityAccessFactory(user=user, opportunity=opp, accepted=True)
                all_accesses.append(access)
                UserInviteFactory(opportunity=opp, opportunity_access=access, status=UserInviteStatus.accepted)

                # Create and cache PaymentUnit per opportunity
                if opp.id not in payment_units:
                    payment_units[opp.id] = PaymentUnitFactory(opportunity=opp)

                CompletedModuleFactory(opportunity=opp, user=user, opportunity_access=access)
                AssessmentFactory(opportunity=opp, user=user, opportunity_access=access)
                OpportunityClaimFactory(opportunity_access=access)

        self.stdout.write(f"Generating {num_visits} test visits for organization {org.name}...")

        if not all_accesses:
            self.stdout.write(self.style.WARNING("No OpportunityAccess records exist. Cannot generate visits."))
            return

        self.stdout.write("Pre-caching Deliver Units for all opportunities...")
        deliver_units_cache = {}
        for opp in all_opportunities:
            if opp.deliver_app and opp.deliver_app.id not in deliver_units_cache:
                # The payment unit has to be passed in: left to itself DeliverUnitFactory builds
                # one through a throwaway opportunity, which drags in its own HQ API key and a
                # UserFactory user -- whose username sequence restarts with the process and so
                # collides with a previous run's users.
                if opp.id not in payment_units:
                    payment_units[opp.id] = PaymentUnitFactory(opportunity=opp)
                deliver_units_cache[opp.deliver_app.id] = DeliverUnitFactory(
                    app=opp.deliver_app, payment_unit=payment_units[opp.id]
                )

        start_date = datetime(2024, 7, 1, tzinfo=UTC)
        end_date = djtimezone.now()
        works_to_create = []

        for count in range(num_visits):
            opp_access = random.choice(all_accesses)
            user = opp_access.user
            opportunity = opp_access.opportunity

            # Generate random data
            lat, lon = random.uniform(-90, 90), random.uniform(-180, 180)
            location = f"{lat:.7f} {lon:.7f} 0.0 3099.99"
            delta_days = (end_date - start_date).days
            random_date = start_date + timedelta(days=random.randint(0, delta_days))
            time_start = random_date.replace(hour=random.randint(8, 17), minute=random.randint(0, 59))
            time_end = time_start + timedelta(minutes=random.randint(5, 60))

            deliver_unit = deliver_units_cache.get(opportunity.deliver_app_id)
            payment_unit = payment_units.get(opportunity.id)

            xform_id = str(uuid4())
            form_json = _build_form_json(
                xform_id=xform_id,
                opportunity=opportunity,
                user=user,
                location=location,
                time_start=time_start,
                time_end=time_end,
                received_on=time_end,
            )
            attachments, media_sources = _plan_attachments(media, image_percent, audio_percent)
            if media_sources:
                form_json["attachments"] = attachments

            work = CompletedWorkFactory.build(
                opportunity_access=opp_access,
                payment_unit=payment_unit,
            )
            visit = UserVisitFactory.build(
                completed_work=None,
                opportunity=opportunity,
                user=user,
                opportunity_access=opp_access,
                deliver_unit=deliver_unit,
                visit_date=random_date,
                xform_id=xform_id,
                form_json=form_json,
            )
            # The bytes behind each attachment, carried until the visit has a primary key. They
            # stay off form_json, which is the payload HQ sent and holds no local paths.
            visit._transient_media = media_sources
            # Temporarily attach the visit to the work object to link them after creation
            work._transient_visit = visit
            works_to_create.append(work)

            if len(works_to_create) >= BATCH_SIZE:
                self.stdout.write(f"Processing batch of {len(works_to_create)} visits...")
                self._bulk_create_visits(works_to_create)
                works_to_create = []

        if works_to_create:
            self.stdout.write(f"Processing final batch of {len(works_to_create)} visits...")
            self._bulk_create_visits(works_to_create)

        self.stdout.write("Generating invoices and payments...")
        for opp in all_opportunities:
            exchange_rate = self._sample_exchange_rate(opp.currency_code)
            num_invoices = random.randint(1, 6)
            for _ in range(num_invoices):
                amount = Decimal(random.randrange(10_000, 1_000_000)) / 100
                invoice = PaymentInvoiceFactory(
                    opportunity=opp,
                    date=fake.date_time_between(start_date="-30d", end_date="now", tzinfo=UTC),
                    invoice_number=fake.pystr(),
                    amount=amount,
                    amount_usd=round(amount / exchange_rate.rate, 2),
                    exchange_rate=exchange_rate,
                )
                if random.choice([True, False]):
                    accesses = opp.opportunityaccess_set.all()
                    access = random.choice(list(accesses)) if accesses else None
                    PaymentFactory(
                        opportunity_access=access,
                        invoice=invoice,
                        amount=invoice.amount,
                        date_paid=invoice.date,
                    )

        self.stdout.write(self.style.SUCCESS(f"Successfully generated {num_visits} test visits."))

    @transaction.atomic
    def _bulk_create_visits(self, works_to_create):
        created_works = CompletedWork.objects.bulk_create(works_to_create)

        visits_for_creation = []
        for i, work_with_pk in enumerate(created_works):
            original_work_object = works_to_create[i]
            visit_object = original_work_object._transient_visit
            visit_object.completed_work = work_with_pk
            visits_for_creation.append(visit_object)

        UserVisit.objects.bulk_create(visits_for_creation)
        self._create_attachments(visits_for_creation)

    def _create_attachments(self, visits):
        """Creates the rows and stored files for the media each visit was planned with.

        This mirrors ``_save_attachment`` in ``opportunity.tasks``, which is what a real
        submission goes through: audio becomes an AudioAttachment keyed by the visit, anything
        else a BlobMeta keyed by the form, and the bytes land in storage under the blob_id.
        """
        blobs = []
        audio = []
        for visit in visits:
            # Cycled from a shuffled copy, so one visit's recordings read differently from each
            # other however many it has.
            transcripts = cycle(random.sample(SAMPLE_TRANSCRIPTS, k=len(SAMPLE_TRANSCRIPTS)))
            for name, source in getattr(visit, "_transient_media", {}).items():
                if source.kind == AUDIO_KIND:
                    transcript, translation = next(transcripts)
                    audio.append(
                        (
                            AudioAttachment(
                                user_visit=visit,
                                name=name,
                                content_type=source.content_type,
                                content_length=len(source.content),
                                transcript=transcript,
                                translation=translation,
                            ),
                            source,
                        )
                    )
                else:
                    blobs.append(
                        (
                            BlobMeta(
                                parent_id=visit.xform_id,
                                name=name,
                                content_type=source.content_type,
                                content_length=len(source.content),
                            ),
                            source,
                        )
                    )

        BlobMeta.objects.bulk_create([row for row, _ in blobs])
        AudioAttachment.objects.bulk_create([row for row, _ in audio])
        # Storage is outside the transaction: if a save fails the rows roll back, so take the files
        # already written with them rather than leaving them orphaned in MEDIA_ROOT.
        saved = []
        try:
            for row, source in [*blobs, *audio]:
                saved.append(default_storage.save(str(row.blob_id), ContentFile(source.content, row.name)))
        except Exception:
            for name in saved:
                default_storage.delete(name)
            raise


def _sample_api_key():
    """One HQApiKey shared by every sample opportunity, reused by later runs.

    OpportunityFactory would otherwise build an HQApiKey per opportunity, each with a user from
    UserFactory -- which numbers usernames from a sequence that restarts with the process, so a
    second run collides with the first run's users on the username unique constraint.
    """
    user, _ = User.objects.get_or_create(username=SAMPLE_API_KEY_USERNAME, defaults={"name": "Sample HQ API"})
    return HQApiKey.objects.filter(user=user).first() or HQApiKeyFactory(user=user)


def _build_form_json(*, xform_id, opportunity, user, location, time_start, time_end, received_on):
    """The submission payload the visit would have arrived with.

    The visit detail page feeds this to XFormSerializer and then calls save() without checking
    is_valid(), which asserts rather than degrading, so everything the serializer marks required
    is filled in here -- a metadata-only payload makes that page raise.
    """
    deliver_app = opportunity.deliver_app
    return {
        "domain": deliver_app.cc_domain if deliver_app else "sample-domain",
        "id": xform_id,
        "app_id": deliver_app.cc_app_id if deliver_app else str(uuid4()),
        "build_id": None,
        "received_on": received_on.strftime(FORM_TIMESTAMP_FORMAT),
        "form": {"@xmlns": SAMPLE_FORM_XMLNS, "name": opportunity.name},
        "metadata": {
            "location": location,
            "timeStart": time_start.strftime(FORM_TIMESTAMP_FORMAT),
            "timeEnd": time_end.strftime(FORM_TIMESTAMP_FORMAT),
            "app_build_version": None,
            "username": user.username,
        },
    }


def _plan_attachments(media, image_percent, audio_percent):
    """One visit's attachments: the ``form_json`` dict HQ reports, and the media behind each name.

    Images and audio are rolled separately, so a run can produce photos without recordings or the
    other way round. Names are ours rather than the source file's, which keeps them unique when
    the same file is drawn twice; only the suffix follows the file, so a player still sees the
    format it expects. The rows and stored files need the visit's primary key, so they are created
    once it has one (see ``_create_attachments``).
    """
    sources = {}
    if _rolls(image_percent):
        source = media.pick(IMAGE_KIND)
        sources[f"photo{source.suffix}"] = source
    if _rolls(audio_percent):
        for question in range(1, random.randint(1, 3) + 1):
            source = media.pick(AUDIO_KIND)
            sources[f"question_{question}{source.suffix}"] = source

    attachments = {"form.xml": {"content_type": "text/xml", "length": 1000}}
    for name, source in sources.items():
        attachments[name] = {"content_type": source.content_type, "length": len(source.content)}
    return attachments, sources


def _rolls(percent):
    """True for `percent` out of every hundred calls; 0 never fires and 100 always does."""
    return random.randrange(100) < percent


@dataclass(frozen=True)
class MediaFile:
    """The bytes one attachment is written from, with what to record about them."""

    kind: str
    content_type: str
    suffix: str
    content: bytes


class MediaLibrary:
    """Where sample attachments get their bytes.

    With no media directory it hands out the generated JPEG and WAV, shared by every attachment of
    that kind. Given one it draws a random file of the right kind from it instead, so a run can
    carry real recordings and photos -- useful for anything that reads the media itself rather
    than just its metadata. A kind the directory holds nothing of falls back to the generated
    file with a warning, so a directory of audio alone still produces photos.
    """

    def __init__(self, media_dir=None, warn=None):
        self._paths = {IMAGE_KIND: [], AUDIO_KIND: []}
        self._cache = {}
        if media_dir:
            self._index(Path(media_dir).expanduser(), warn or (lambda message: None))

    def pick(self, kind):
        paths = self._paths[kind]
        return self._read(random.choice(paths)) if paths else _generated_media(kind)

    def _index(self, media_dir, warn):
        if not media_dir.is_dir():
            raise CommandError(f"--media_dir is not a directory: {media_dir}")
        for path in sorted(media_dir.rglob("*")):
            # Hidden files are skipped: macOS AppleDouble "._photo.jpg" forks share the real
            # file's extension but hold no media.
            if not path.is_file() or path.name.startswith("."):
                continue
            kind = _media_kind(mimetypes.guess_type(path.name)[0])
            if kind:
                self._paths[kind].append(path)
        for kind in (IMAGE_KIND, AUDIO_KIND):
            count = len(self._paths[kind])
            if count:
                warn(f"Drawing {kind} attachments from {count} file(s) in {media_dir}.")
            else:
                warn(f"No {kind} files found in {media_dir}; using the generated placeholder.")

    def _read(self, path):
        """Files are cached, since one media directory is drawn from once per attachment."""
        if path not in self._cache:
            content_type = mimetypes.guess_type(path.name)[0]
            self._cache[path] = MediaFile(
                kind=_media_kind(content_type),
                content_type=content_type,
                suffix=path.suffix,
                content=path.read_bytes(),
            )
        return self._cache[path]


def _media_kind(content_type):
    """Which attachment list a content type belongs in, or None if it belongs in neither."""
    if not content_type:
        return None
    for kind in (IMAGE_KIND, AUDIO_KIND):
        if content_type.startswith(f"{kind}/"):
            return kind
    return None


@cache
def _generated_media(kind):
    if kind == IMAGE_KIND:
        return MediaFile(IMAGE_KIND, "image/jpeg", ".jpg", _sample_image_bytes())
    return MediaFile(AUDIO_KIND, "audio/wav", ".wav", _sample_audio_bytes())


@cache
def _sample_image_bytes():
    """A flat-colour JPEG, built once and shared by every image attachment."""
    buffer = io.BytesIO()
    Image.new("RGB", SAMPLE_IMAGE_SIZE, SAMPLE_IMAGE_COLOR).save(buffer, format="JPEG")
    return buffer.getvalue()


@cache
def _sample_audio_bytes():
    """A second of silence as a WAV, built once and shared by every audio attachment."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(1)
        clip.setframerate(SAMPLE_AUDIO_RATE)
        # 0x80 is the midpoint of 8-bit unsigned PCM, so this is silence rather than a click.
        clip.writeframes(b"\x80" * (SAMPLE_AUDIO_RATE * SAMPLE_AUDIO_SECONDS))
    return buffer.getvalue()
