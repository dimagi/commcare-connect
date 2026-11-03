import io
import random
import wave
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from functools import cache
from itertools import cycle

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand
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
    ExchangeRate,
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
    OpportunityAccessFactory,
    OpportunityClaimFactory,
    OpportunityFactory,
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

# Attachment bytes are generated here rather than downloaded from HQ, so they are real files a
# browser can open and a player can play. One image and one audio clip are built once and shared
# by every attachment: each is written to MEDIA_ROOT under its own blob_id, so keep them small.
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
            "--attachment_percent",
            type=int,
            default=25,
            help=(
                "Percentage of visits that get a photo and one to three audio recordings, "
                "with transcripts and translations. Pass 0 to generate none."
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
        xform_ids = list(
            UserVisit.objects.filter(opportunity__organization_id__in=org_ids).values_list("xform_id", flat=True)
        )
        blobs = BlobMeta.objects.filter(parent_id__in=xform_ids)
        audio = AudioAttachment.objects.filter(user_visit__opportunity__organization_id__in=org_ids)
        for blob_id in [*blobs.values_list("blob_id", flat=True), *audio.values_list("blob_id", flat=True)]:
            default_storage.delete(str(blob_id))
        blobs.delete()
        audio.delete()

    def handle(self, *args, **options):
        num_visits = options["num_visits"]
        org_slug = options["org_slug"]
        invited_org_slug = options["invited_org_slug"]
        num_managed_opps = options["managed_opportunities"]
        attachment_percent = options["attachment_percent"]
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
        opportunities = [OpportunityFactory(organization=org) for _ in range(5)]

        managed_opportunities = []
        for _ in range(num_managed_opps):
            program = ProgramFactory(organization=org, delivery_type=DeliveryTypeFactory())
            ProgramApplicationFactory(program=program, organization=invited_org)
            managed_opp = OpportunityFactory(
                organization=invited_org,
                program=program,
            )
            managed_opportunities.append(managed_opp)

        all_opportunities = opportunities + managed_opportunities
        all_accesses = []
        payment_units = {}
        for user in users:
            selected_opps = random.sample(all_opportunities, k=random.randint(1, 3))
            for opp in selected_opps:
                access = OpportunityAccessFactory(user=user, opportunity=opp)
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
                deliver_units_cache[opp.deliver_app.id] = DeliverUnitFactory(app=opp.deliver_app)

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

            form_json = {
                "metadata": {
                    "location": location,
                    "timeStart": time_start.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    "timeEnd": time_end.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                }
            }
            if random.randrange(100) < attachment_percent:
                form_json["attachments"] = _plan_attachments()

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
                form_json=form_json,
            )
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
        """Creates the rows and stored files for whatever each visit's form_json announced.

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
            for name, attachment in visit.form_json.get("attachments", {}).items():
                if name == "form.xml":
                    continue
                if attachment["content_type"].startswith("audio/"):
                    transcript, translation = next(transcripts)
                    audio.append(
                        AudioAttachment(
                            user_visit=visit,
                            name=name,
                            content_type=attachment["content_type"],
                            content_length=attachment["length"],
                            transcript=transcript,
                            translation=translation,
                        )
                    )
                else:
                    blobs.append(
                        BlobMeta(
                            parent_id=visit.xform_id,
                            name=name,
                            content_type=attachment["content_type"],
                            content_length=attachment["length"],
                        )
                    )

        BlobMeta.objects.bulk_create(blobs)
        AudioAttachment.objects.bulk_create(audio)
        for blob in blobs:
            default_storage.save(str(blob.blob_id), ContentFile(_sample_image_bytes(), blob.name))
        for attachment in audio:
            default_storage.save(str(attachment.blob_id), ContentFile(_sample_audio_bytes(), attachment.name))


def _plan_attachments():
    """One visit's attachments, in the shape HQ reports them in ``form_json``.

    Only names, types and sizes are decided here; the rows and files need the visit's primary
    key, so they are created once it has one (see ``_create_attachments``).
    """
    attachments = {
        "form.xml": {"content_type": "text/xml", "length": 1000},
        "photo.jpg": {"content_type": "image/jpeg", "length": len(_sample_image_bytes())},
    }
    for question in range(1, random.randint(1, 3) + 1):
        attachments[f"question_{question}.wav"] = {
            "content_type": "audio/wav",
            "length": len(_sample_audio_bytes()),
        }
    return attachments


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
