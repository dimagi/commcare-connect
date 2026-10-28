from datetime import date, timedelta
from unittest import mock
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from commcare_connect.opportunity.models import Country
from commcare_connect.opportunity.tests.factories import OpportunityFactory
from commcare_connect.organization.filters import ContactFilterSet, OrganizationFilterSet
from commcare_connect.organization.forms import ContactForm, OrganizationDirectoryForm
from commcare_connect.organization.models import Contact, Organization, OrganizationStatus, PrimarySector
from commcare_connect.organization.tables import OrganizationDirectoryTable
from commcare_connect.organization.tasks import export_contacts
from commcare_connect.organization.views import OrganizationArchiveView
from commcare_connect.program.tests.factories import ProgramFactory
from commcare_connect.users.tests.factories import ContactFactory, OrganizationFactory, UserFactory
from commcare_connect.utils.celery import get_export_storage


def _filtered_organizations(params):
    return set(OrganizationFilterSet(params, queryset=Organization.objects.all()).qs)


@pytest.fixture
def user_with_directory_access(db):
    user = UserFactory()
    user.user_permissions.add(Permission.objects.get(codename="llo_directory_management_access"))
    return user


@pytest.fixture
def sectors(db):
    return {
        "health": PrimarySector.objects.create(name="Health", slug="health", description=""),
        "wash": PrimarySector.objects.create(name="WASH", slug="wash", description=""),
    }


@pytest.fixture
def countries(db):
    return {code: Country.objects.get(code=code) for code in ("KEN", "TZA", "IND")}


@pytest.mark.django_db
class TestOrganizationFilterSet:
    @pytest.mark.parametrize("search", ["umoja", "UCHT", "kenya", "wash"])
    def test_search_matches_name_short_name_country_and_sector(self, search, countries, sectors):
        org = OrganizationFactory(name="Umoja Health Trust", short_name="UCHT")
        org.countries.add(countries["KEN"])
        org.primary_sectors.add(sectors["wash"])
        OrganizationFactory(name="Unrelated")

        assert _filtered_organizations({"search": search}) == {org}

    def test_multi_select_filters_match_any_value_without_duplicates(self, countries, sectors):
        both = OrganizationFactory()
        both.countries.add(countries["KEN"], countries["TZA"])
        both.primary_sectors.add(sectors["health"], sectors["wash"])
        india = OrganizationFactory()
        india.countries.add(countries["IND"])
        india.primary_sectors.add(sectors["health"])

        by_country = OrganizationFilterSet({"countries": ["KEN", "TZA"]}, queryset=Organization.objects.all()).qs
        assert list(by_country) == [both]
        sector_ids = [sectors["health"].pk, sectors["wash"].pk]
        assert _filtered_organizations({"primary_sectors": sector_ids}) == {both, india}

    def test_status_filter(self):
        active = OrganizationFactory(status=OrganizationStatus.ACTIVE)
        prospective = OrganizationFactory(status=OrganizationStatus.PROSPECTIVE)
        OrganizationFactory(status=OrganizationStatus.INACTIVE)

        assert _filtered_organizations({"status": ["active", "prospective"]}) == {active, prospective}


@pytest.mark.django_db
class TestOrganizationListAccess:
    def test_requires_permission(self, client, user):
        client.force_login(user)
        assert client.get(reverse("organization_directory:list")).status_code == 403

    def test_lists_for_permitted_user(self, client, user_with_directory_access):
        org = OrganizationFactory()
        client.force_login(user_with_directory_access)

        response = client.get(reverse("organization_directory:list"))

        assert response.status_code == 200
        assert list(response.context["table"].data) == [org]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status, badge_class",
    [
        (OrganizationStatus.ACTIVE, "positive-light"),
        (OrganizationStatus.INACTIVE, "label"),
        (OrganizationStatus.PROSPECTIVE, "badge-indigo"),
    ],
)
def test_status_column_shows_a_badge(status, badge_class):
    table = OrganizationDirectoryTable([OrganizationFactory(status=status)])

    cell = table.rows[0].get_cell("status")

    assert badge_class in cell
    assert str(status.label) in cell


def _form_data(country_by_code, sector_by_key, **overrides):
    data = {
        "name": "Umoja Health Trust",
        "short_name": "UCHT",
        "status": OrganizationStatus.ACTIVE,
        "has_used_connect": "False",
        "countries": [country_by_code["KEN"].pk],
        "regions": "Nyanza",
        "primary_sectors": [sector_by_key["health"].pk],
        "contact_emails": "a@example.com\nb@example.com",
        "latest_msa_link": "https://example.com/msa",
        "eoi_links": "https://example.com/eoi-1\nhttps://example.com/eoi-2",
    }
    data.update(overrides)
    return data


@pytest.mark.django_db
class TestOrganizationDirectoryForm:
    @pytest.mark.parametrize("field", OrganizationDirectoryForm.REQUIRED_FIELDS)
    @pytest.mark.parametrize("editing", [False, True])
    def test_required_fields(self, field, editing, countries, sectors):
        instance = OrganizationFactory() if editing else None
        data = _form_data(countries, sectors, **{field: ""})

        form = OrganizationDirectoryForm(data=data, instance=instance)

        assert not form.is_valid()
        assert field in form.errors

    def test_saves_all_fields(self, countries, sectors):
        form = OrganizationDirectoryForm(data=_form_data(countries, sectors))

        assert form.is_valid(), form.errors
        org = form.save()

        assert org.status == OrganizationStatus.ACTIVE
        assert org.latest_msa_link == "https://example.com/msa"
        assert list(org.countries.all()) == [countries["KEN"]]
        assert org.contact_emails == "a@example.com\nb@example.com"
        assert org.eoi_links == "https://example.com/eoi-1\nhttps://example.com/eoi-2"

    def test_eoi_links_must_be_urls(self, countries, sectors):
        data = _form_data(countries, sectors, eoi_links="https://example.com/eoi-1\nnot a link")

        form = OrganizationDirectoryForm(data=data)

        assert "not a link" in form.errors["eoi_links"][0]


@pytest.mark.django_db
class TestOrganizationFormViews:
    def test_create_does_not_add_the_staff_member(self, client, user_with_directory_access, countries, sectors):
        client.force_login(user_with_directory_access)

        response = client.post(
            reverse("organization_directory:create"), _form_data(countries, sectors), HTTP_HX_REQUEST="true"
        )

        assert response.status_code == 200
        org = Organization.objects.get(name="Umoja Health Trust")
        assert not org.memberships.exists()
        assert org.created_by == org.modified_by == user_with_directory_access.email

    def test_edit_records_who_modified(self, client, user_with_directory_access, countries, sectors):
        org = OrganizationFactory(created_by="creator@example.com")
        client.force_login(user_with_directory_access)

        client.post(
            reverse("organization_directory:edit", args=(org.slug,)),
            _form_data(countries, sectors),
            HTTP_HX_REQUEST="true",
        )

        org.refresh_from_db()
        assert org.created_by == "creator@example.com"
        assert org.modified_by == user_with_directory_access.email

    @pytest.mark.parametrize(
        "next_url, expected",
        [
            ("/organizations/?search=x", "/organizations/?search=x"),
            ("https://evil.example.com/organizations/", "/organizations/"),
            ("/a/some-org/opportunity/", "/organizations/"),
        ],
    )
    def test_edit_returns_to_the_listing_it_came_from(
        self, client, user_with_directory_access, countries, sectors, next_url, expected
    ):
        org = OrganizationFactory()
        client.force_login(user_with_directory_access)
        url = f"{reverse('organization_directory:edit', args=(org.slug,))}?{urlencode({'next': next_url})}"

        response = client.post(url, _form_data(countries, sectors), HTTP_HX_REQUEST="true")

        assert response.headers["HX-Redirect"] == expected

    def test_invalid_submission_rerenders_the_form(self, client, user_with_directory_access, countries, sectors):
        org = OrganizationFactory()
        client.force_login(user_with_directory_access)

        response = client.post(
            reverse("organization_directory:edit", args=(org.slug,)),
            _form_data(countries, sectors, name=""),
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        assert "HX-Redirect" not in response.headers
        assert response.context["form"].errors["name"]

    @pytest.mark.parametrize("url_name", ["organization_directory:create", "organization_directory:edit"])
    def test_direct_visit_redirects_to_the_listing(self, client, user_with_directory_access, url_name):
        org = OrganizationFactory()
        client.force_login(user_with_directory_access)
        args = (org.slug,) if url_name.endswith("edit") else ()

        response = client.get(reverse(url_name, args=args))

        assert response.status_code == 302
        assert response.url == reverse("organization_directory:list")

    @pytest.mark.parametrize("url_name", ["organization_directory:create", "organization_directory:edit"])
    def test_requires_permission(self, client, user, url_name):
        org = OrganizationFactory()
        client.force_login(user)
        args = (org.slug,) if url_name.endswith("edit") else ()

        assert client.get(reverse(url_name, args=args)).status_code == 403


@pytest.mark.django_db
class TestOrganizationArchiveView:
    def test_get_shows_the_owned_programs_with_their_opportunity_counts(
        self, client, user_with_directory_access, organization
    ):
        ended = date.today() - timedelta(days=1)
        program = ProgramFactory(organization=organization, start_date=ended - timedelta(days=30), end_date=ended)
        OpportunityFactory.create_batch(2, program=program, end_date=ended)
        ProgramFactory(funder=organization, start_date=ended - timedelta(days=30), end_date=ended)
        client.force_login(user_with_directory_access)

        response = client.get(
            reverse("organization_directory:archive", args=(organization.slug,)), HTTP_HX_REQUEST="true"
        )

        assert [(listed, listed.opportunity_count) for listed in response.context["programs"]] == [(program, 2)]
        assert program.name in response.content.decode()

    def test_direct_visit_redirects_to_the_listing(self, client, user_with_directory_access, organization):
        client.force_login(user_with_directory_access)

        response = client.get(reverse("organization_directory:archive", args=(organization.slug,)))

        assert response.url == reverse("organization_directory:list")

    def test_post_archives_and_redirects(self, client, user_with_directory_access, organization):
        client.force_login(user_with_directory_access)

        response = _post_archive(client, organization, {"reason": "Duplicate"})

        assert response.headers["HX-Redirect"] == reverse("organization_directory:list")
        organization.refresh_from_db()
        assert organization.is_archived
        assert organization.notes.endswith(": Duplicate")

    def test_get_lists_the_blockers_instead_of_the_form(self, client, user_with_directory_access, organization):
        program = ProgramFactory(organization=organization)
        client.force_login(user_with_directory_access)

        response = client.get(
            reverse("organization_directory:archive", args=(organization.slug,)), HTTP_HX_REQUEST="true"
        )

        assert "organization/directory/organization_archive_refused.html" in [t.name for t in response.templates]
        assert [blocker.name for blocker in response.context["blockers"]] == [program.name]
        assert 'name="reason"' not in response.content.decode()

    def test_refusal_lists_each_blockers_roles(self, client, user_with_directory_access, organization):
        ProgramFactory(organization=organization, funder=organization)
        client.force_login(user_with_directory_access)

        response = client.get(
            reverse("organization_directory:archive", args=(organization.slug,)), HTTP_HX_REQUEST="true"
        )

        content = response.content.decode()
        assert "Program owner, Program funder" in content
        assert "programs" not in response.context

    def test_post_is_refused_when_work_became_live_after_the_modal_opened(
        self, client, user_with_directory_access, organization
    ):
        client.force_login(user_with_directory_access)
        ProgramFactory(organization=organization)

        response = _post_archive(client, organization, {"reason": "Duplicate"})

        assert "HX-Redirect" not in response.headers
        assert "organization/directory/organization_archive_refused.html" in [t.name for t in response.templates]
        organization.refresh_from_db()
        assert not organization.is_archived
        assert organization.notes == ""

    def test_post_locks_the_organization_row(self, rf, organization):
        view = OrganizationArchiveView()
        view.setup(rf.post("/"), slug=organization.slug)

        assert view.get_queryset().query.select_for_update

    def test_archived_organization_is_not_found(self, client, user_with_directory_access):
        organization = OrganizationFactory(is_archived=True, notes="Existing")
        client.force_login(user_with_directory_access)

        response = _post_archive(client, organization, {"reason": "Again"})

        assert response.status_code == 404
        organization.refresh_from_db()
        assert organization.notes == "Existing"

    @pytest.mark.parametrize("slug_exists", [True, False])
    def test_requires_permission(self, client, user, organization, slug_exists):
        client.force_login(user)
        slug = organization.slug if slug_exists else "no-such-org"

        response = client.post(reverse("organization_directory:archive", args=(slug,)))

        assert response.status_code == 403


def _post_archive(client, organization, data):
    return client.post(
        reverse("organization_directory:archive", args=(organization.slug,)), data, HTTP_HX_REQUEST="true"
    )


def _filtered_contacts(params):
    return list(ContactFilterSet(params, queryset=Contact.objects.order_by("name")).qs)


@pytest.mark.django_db
class TestContactFilterSet:
    @pytest.mark.parametrize("search", ["grace", "umoja", "UMOJAHEALTH.or"])
    def test_search_matches_name_organization_and_email(self, search):
        organization = OrganizationFactory(name="Umoja Health Trust")
        contact = ContactFactory(organization=organization, name="Grace Wanjiru", email="g@umojahealth.org")
        ContactFactory(name="Someone Else", email="else@example.com")

        assert _filtered_contacts({"search": search}) == [contact]

    def test_country_and_status_filters_use_the_organization(self, countries):
        kenyan = OrganizationFactory(status=OrganizationStatus.ACTIVE)
        kenyan.countries.add(countries["KEN"], countries["TZA"])
        contact = ContactFactory(organization=kenyan)
        ContactFactory(organization=OrganizationFactory(status=OrganizationStatus.INACTIVE))

        assert _filtered_contacts({"countries": ["KEN", "TZA"]}) == [contact]
        assert _filtered_contacts({"organization_status": ["active"]}) == [contact]

    def test_main_poc_only(self):
        main = ContactFactory(name="A Main", is_main_poc=True)
        other = ContactFactory(name="B Other", organization=main.organization)

        assert _filtered_contacts({"main_poc": "true"}) == [main]
        assert _filtered_contacts({"main_poc": ""}) == [main, other]

    def test_lists_archived_contacts_only_when_asked_for(self):
        active = ContactFactory()
        archived = ContactFactory(is_archived=True)

        assert _filtered_contacts({}) == [active]
        assert _filtered_contacts({"archived": "true"}) == [archived]


@pytest.mark.django_db
class TestContactListAccess:
    def test_requires_permission(self, client, user):
        client.force_login(user)
        assert client.get(reverse("organization_directory:contacts")).status_code == 403

    def test_lists_unarchived_contacts(self, client, user_with_directory_access):
        contact = ContactFactory()
        ContactFactory(is_archived=True)
        client.force_login(user_with_directory_access)

        response = client.get(reverse("organization_directory:contacts"))

        assert response.status_code == 200
        assert list(response.context["table"].data) == [contact]


def _contact_form_data(organization, **overrides):
    return {"organization": organization.pk, "name": "Grace Wanjiru", "email": "grace@umojahealth.org"} | overrides


@pytest.mark.django_db
class TestContactForm:
    def test_requires_name_organization_and_email(self):
        form = ContactForm(data={})

        assert set(form.errors) == {"name", "organization", "email"}

    def test_saves_all_fields(self, organization):
        data = _contact_form_data(
            organization, title="Executive Director", is_main_poc="True", phone="+254 722 118 340", notes="Met at AMR"
        )
        form = ContactForm(data=data)

        assert form.is_valid(), form.errors
        contact = form.save()

        assert contact.organization == organization
        assert contact.title == "Executive Director"
        assert contact.is_main_poc
        assert contact.phone == "+254 722 118 340"
        assert contact.notes == "Met at AMR"

    def test_becoming_main_poc_replaces_the_current_one(self):
        current_main = ContactFactory(is_main_poc=True)
        form = ContactForm(data=_contact_form_data(current_main.organization, is_main_poc="True"))

        assert form.is_valid(), form.errors
        new_main = form.save()

        current_main.refresh_from_db()
        assert new_main.is_main_poc
        assert not current_main.is_main_poc

    def test_saving_without_commit_leaves_the_current_main_poc(self):
        current_main = ContactFactory(is_main_poc=True)
        form = ContactForm(data=_contact_form_data(current_main.organization, is_main_poc="True"))

        assert form.is_valid(), form.errors
        form.save(commit=False)

        current_main.refresh_from_db()
        assert current_main.is_main_poc

    def test_email_already_used_by_another_contact_is_rejected(self, organization):
        existing = ContactFactory(email="grace@umojahealth.org")

        form = ContactForm(data=_contact_form_data(organization, email="GRACE@umojahealth.org"))

        assert not form.is_valid()
        assert existing.organization.name in form.errors["email"][0]

    def test_email_used_by_an_archived_contact_says_it_is_archived(self, organization):
        ContactFactory(name="Grace Wanjiru", email="grace@umojahealth.org", is_archived=True)

        form = ContactForm(data=_contact_form_data(organization, email="grace@umojahealth.org"))

        assert "Grace Wanjiru (archived)" in form.errors["email"][0]

    def test_a_contact_keeps_its_own_email_on_edit(self):
        contact = ContactFactory(email="grace@umojahealth.org")

        assert ContactForm(data=_contact_form_data(contact.organization), instance=contact).is_valid()


@pytest.mark.django_db
class TestContactFormViews:
    def test_create_returns_to_the_contacts_listing(self, client, user_with_directory_access, organization):
        client.force_login(user_with_directory_access)

        response = client.post(
            reverse("organization_directory:contact_create"), _contact_form_data(organization), HTTP_HX_REQUEST="true"
        )

        assert response.headers["HX-Redirect"] == reverse("organization_directory:contacts")
        assert organization.contacts.get().name == "Grace Wanjiru"

    @pytest.mark.parametrize(
        "url_name", ["organization_directory:contact_create", "organization_directory:contact_edit"]
    )
    def test_requires_permission(self, client, user, url_name):
        contact = ContactFactory()
        client.force_login(user)
        args = (contact.contact_id,) if url_name.endswith("edit") else ()

        assert client.get(reverse(url_name, args=args)).status_code == 403


@pytest.mark.django_db
class TestContactArchiveView:
    def test_archives_and_returns_to_the_filtered_listing(self, client, user_with_directory_access):
        contact = ContactFactory()
        client.force_login(user_with_directory_access)

        url = reverse("organization_directory:contact_archive", args=(contact.contact_id,))

        response = client.post(f"{url}?search=x")

        contact.refresh_from_db()
        assert contact.is_archived
        assert contact.modified_by == user_with_directory_access.email
        assert response.url == f"{reverse('organization_directory:contacts')}?search=x"

    def test_get_is_not_allowed(self, client, user_with_directory_access):
        contact = ContactFactory()
        client.force_login(user_with_directory_access)

        response = client.get(reverse("organization_directory:contact_archive", args=(contact.contact_id,)))

        assert response.status_code == 405
        contact.refresh_from_db()
        assert not contact.is_archived

    def test_requires_permission(self, client, user):
        contact = ContactFactory()
        client.force_login(user)

        url = reverse("organization_directory:contact_archive", args=(contact.contact_id,))

        assert client.post(url).status_code == 403


@pytest.mark.django_db
class TestContactRestoreView:
    def test_restores_and_returns_to_the_filtered_listing(self, client, user_with_directory_access):
        contact = ContactFactory(is_archived=True)
        client.force_login(user_with_directory_access)
        url = reverse("organization_directory:contact_restore", args=(contact.contact_id,))

        response = client.post(f"{url}?archived=true")

        contact.refresh_from_db()
        assert not contact.is_archived
        assert contact.modified_by == user_with_directory_access.email
        assert response.url == f"{reverse('organization_directory:contacts')}?archived=true"

    def test_requires_permission(self, client, user):
        contact = ContactFactory(is_archived=True)
        client.force_login(user)
        url = reverse("organization_directory:contact_restore", args=(contact.contact_id,))

        assert client.post(url).status_code == 403


@pytest.mark.django_db
class TestExportContacts:
    def test_exports_the_filtered_unarchived_contacts(self):
        ContactFactory(name="Grace Wanjiru", is_main_poc=True)
        ContactFactory(name="Peter Otieno")
        ContactFactory(name="Grace Archived", is_archived=True)

        with mock.patch.dict("sys.modules", {"commcare_connect.utils.storages": None}):
            saved_name = export_contacts("search=grace", user_id=1, export_format="csv")
            with get_export_storage().open(saved_name) as saved:
                rows = saved.read().decode().splitlines()

        assert rows[0].startswith("Contact Name,Organization,Role / Title,Main POC")
        assert len(rows) == 2
        assert rows[1].startswith("Grace Wanjiru,")
        assert ",Yes," in rows[1]

    def test_text_cannot_become_a_spreadsheet_formula(self):
        ContactFactory(name="=HYPERLINK(1)", phone="+254 722 118 340", email="grace@example.com")

        with mock.patch.dict("sys.modules", {"commcare_connect.utils.storages": None}):
            saved_name = export_contacts("", user_id=1, export_format="csv")
            with get_export_storage().open(saved_name) as saved:
                row = saved.read().decode().splitlines()[1]

        assert row.startswith("'=HYPERLINK(1),")
        assert ",grace@example.com,'+254 722 118 340," in row

    def test_saves_names_with_special_characters_as_utf8(self):
        ContactFactory(name="José Ñúñez")

        with mock.patch("commcare_connect.organization.tasks.get_export_storage") as get_storage:
            export_contacts("", user_id=1, export_format="csv")

        saved_content = get_storage.return_value.save.call_args.args[1].read()
        assert "José Ñúñez" in saved_content.decode("utf-8")


@pytest.mark.django_db
class TestContactExportViews:
    def test_starts_the_export_with_the_listing_filters(
        self, client, user_with_directory_access, django_capture_on_commit_callbacks
    ):
        client.force_login(user_with_directory_access)
        url = f"{reverse('organization_directory:contact_export')}?search=grace"

        with mock.patch.object(export_contacts, "apply_async") as apply_async:
            with django_capture_on_commit_callbacks(execute=True):
                response = client.post(url, {"export_format": "xlsx"})

        task_id = parse_qs(urlparse(response.url).query)["export_task_id"][0]
        apply_async.assert_called_once_with(
            args=("search=grace", user_with_directory_access.pk, "xlsx"), task_id=task_id
        )

    def test_rejects_an_unsupported_format(self, client, user_with_directory_access):
        client.force_login(user_with_directory_access)

        response = client.post(reverse("organization_directory:contact_export"), {"export_format": "pdf"})

        assert response.status_code == 400

    def test_status_of_another_users_export_shows_a_notice(self, client, user_with_directory_access):
        client.force_login(user_with_directory_access)
        other_users_task = mock.Mock()
        other_users_task._get_task_meta.return_value = {
            "status": "SUCCESS",
            "args": ["", user_with_directory_access.pk + 1, "csv"],
        }

        with mock.patch("commcare_connect.utils.celery.AsyncResult", return_value=other_users_task):
            response = client.get(reverse("organization_directory:contact_export_status", args=("task-id",)))

        assert response.status_code == 200
        assert "Start a new export" in response.content.decode()
        assert "Download Export" not in response.content.decode()

    def test_download_is_refused_to_other_users(self, client, user_with_directory_access):
        client.force_login(user_with_directory_access)
        other_users_task = mock.Mock(args=("", user_with_directory_access.pk + 1, "csv"))

        with mock.patch("commcare_connect.organization.views.AsyncResult", return_value=other_users_task):
            response = client.get(reverse("organization_directory:contact_export_download", args=("task-id",)))

        assert response.status_code == 404
