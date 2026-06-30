# Audio Attachments for Delivery Forms Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store audio attachments from received delivery forms in a dedicated `AudioAttachment` table (with transcript/translation metadata) and make them playable on the visit verification page.

**Architecture:** Audio attachments are detected at download time by MIME type (`audio/*`). Instead of the generic `BlobMeta` row used for images, audio creates an `AudioAttachment` row that FKs to the `UserVisit` and holds extra metadata. The file bytes still live in the same S3/default storage keyed by `blob_id`. A dedicated view serves the bytes for an HTML5 `<audio>` player on the visit-details page.

**Tech Stack:** Django 4.2, PostgreSQL/PostGIS, Celery, pytest + factory-boy, Tailwind/Alpine/htmx templates.

## Global Constraints

- Python line length 119, target py311 (ruff enforces).
- Models extend `BaseModel` by default — but this table deliberately uses plain `models.Model` to mirror its sibling `BlobMeta` (rows are created by a Celery task with no request user, so `created_by`/`modified_by` would always be null).
- Tests: pytest + factory-boy; prefer existing fixtures (`opportunity`, `mobile_user`, `org_user_member`, `client`) and factories. PostGIS is required for all tests.
- No inline JS in templates; use predefined Tailwind style classes where available. Native `<audio controls>` needs no JS.
- Run `prek run -a` before final commit to satisfy formatting/lint hooks.

---

### Task 1: `AudioAttachment` model, migration, factory, and `UserVisit.audio` accessor

**Files:**
- Modify: `commcare_connect/opportunity/models.py` (add model after `BlobMeta` at line 993; add `audio` property to `UserVisit` near `images` at line 883-885)
- Create: migration via `makemigrations` (e.g. `commcare_connect/opportunity/migrations/00XX_audioattachment.py`)
- Modify: `commcare_connect/opportunity/tests/factories.py` (add `AudioAttachmentFactory` after `BlobMetaFactory` at line 319-326)
- Test: `commcare_connect/opportunity/tests/test_models.py`

**Interfaces:**
- Produces:
  - `AudioAttachment(user_visit: UserVisit, blob_id: str, name: str, content_type: str|None, content_length: int, transcript: str, translation: str, date_created: datetime)` with `related_name="audio_attachments"` and `unique_together = (user_visit, name)`.
  - `UserVisit.audio` → queryset of related `AudioAttachment` rows.
  - `AudioAttachmentFactory` (factory-boy).

- [ ] **Step 1: Write the failing tests**

In `commcare_connect/opportunity/tests/test_models.py`, add (ensure these imports exist at the top of the file: `import pytest`, `from django.db.utils import IntegrityError`, `from commcare_connect.opportunity.models import AudioAttachment`, `from commcare_connect.opportunity.tests.factories import UserVisitFactory`):

```python
@pytest.mark.django_db
def test_audio_attachment_defaults_and_accessor():
    visit = UserVisitFactory.create()
    audio = AudioAttachment.objects.create(
        user_visit=visit,
        name="recording.m4a",
        content_type="audio/mp4",
        content_length=123,
    )
    assert str(audio.blob_id)  # uuid default populated
    assert audio.transcript == ""
    assert audio.translation == ""
    assert audio.date_created is not None
    assert list(visit.audio) == [audio]


@pytest.mark.django_db
def test_audio_attachment_unique_per_visit_and_name():
    visit = UserVisitFactory.create()
    AudioAttachment.objects.create(user_visit=visit, name="recording.m4a", content_length=1)
    with pytest.raises(IntegrityError):
        AudioAttachment.objects.create(user_visit=visit, name="recording.m4a", content_length=1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest commcare_connect/opportunity/tests/test_models.py::test_audio_attachment_defaults_and_accessor commcare_connect/opportunity/tests/test_models.py::test_audio_attachment_unique_per_visit_and_name -v`
Expected: FAIL — `ImportError`/`AttributeError` (cannot import `AudioAttachment`).

- [ ] **Step 3: Add the model**

In `commcare_connect/opportunity/models.py`, immediately after the `BlobMeta` class (ends line 993). `uuid4` is already imported (line 6):

```python
class AudioAttachment(models.Model):
    user_visit = models.ForeignKey(
        "UserVisit",
        on_delete=models.CASCADE,
        related_name="audio_attachments",
    )
    blob_id = models.CharField(max_length=255, default=uuid4)
    name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=255, null=True)
    content_length = models.IntegerField()
    transcript = models.TextField(blank=True, default="")
    translation = models.TextField(blank=True, default="")
    date_created = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [
            ("user_visit", "name"),
        ]
        indexes = [models.Index(fields=["blob_id"])]
```

- [ ] **Step 4: Add the `audio` accessor on `UserVisit`**

In `commcare_connect/opportunity/models.py`, directly below the existing `images` property (line 883-885):

```python
    @property
    def audio(self):
        return self.audio_attachments.all()
```

- [ ] **Step 5: Generate the migration**

Run: `./manage.py makemigrations opportunity`
Expected: creates a new migration adding `AudioAttachment`.

- [ ] **Step 6: Add the factory**

In `commcare_connect/opportunity/tests/factories.py`, after `BlobMetaFactory` (line 319-326). `SubFactory` and `Faker` are already imported in this file; `UserVisitFactory` is defined above (line 134):

```python
class AudioAttachmentFactory(DjangoModelFactory):
    user_visit = SubFactory(UserVisitFactory)
    blob_id = Faker("uuid4")
    name = Faker("file_name", extension="m4a")
    content_type = "audio/mp4"
    content_length = Faker("pyint", min_value=100, max_value=10000)

    class Meta:
        model = "opportunity.AudioAttachment"
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest commcare_connect/opportunity/tests/test_models.py::test_audio_attachment_defaults_and_accessor commcare_connect/opportunity/tests/test_models.py::test_audio_attachment_unique_per_visit_and_name -v`
Expected: PASS (2 passed).

- [ ] **Step 8: Commit**

```bash
git add commcare_connect/opportunity/models.py commcare_connect/opportunity/migrations/ commcare_connect/opportunity/tests/factories.py commcare_connect/opportunity/tests/test_models.py
git commit -m "Add AudioAttachment model, migration, factory, and UserVisit.audio accessor"
```

---

### Task 2: Route audio attachments to `AudioAttachment` at download time

**Files:**
- Modify: `commcare_connect/opportunity/tasks.py` (`_download_attachments` at line 379-400; `download_user_visit_attachments` at line 411-416; model import block starting line 39)
- Test: `commcare_connect/opportunity/tests/test_tasks.py`

**Interfaces:**
- Consumes: `AudioAttachment` (Task 1).
- Produces:
  - `_download_attachments(api_key, domain, xform_id, attachments, user_visit=None)` — when `user_visit` is provided and an attachment's `content_type` starts with `audio/`, the attachment is saved as an `AudioAttachment`; otherwise as a `BlobMeta`.
  - Helpers `_save_blob_attachment(api_key, url, name, blob, xform_id)` and `_save_audio_attachment(api_key, url, name, blob, user_visit)`.

- [ ] **Step 1: Write the failing tests**

In `commcare_connect/opportunity/tests/test_tasks.py`, add (file already imports `mock`, `BlobMeta`, `download_user_visit_attachments`, `UserVisitFactory`; add `from commcare_connect.opportunity.models import AudioAttachment` to the existing model import):

```python
def test_download_routes_audio_to_audio_attachment(mobile_user: User, opportunity: Opportunity):
    user_visit = UserVisitFactory.create(
        user=mobile_user,
        opportunity=opportunity,
        form_json={
            "attachments": {
                "myimage.jpg": {"content_type": "image/jpeg", "length": 20},
                "recording.m4a": {"content_type": "audio/mp4", "length": 50},
            }
        },
    )
    with (
        mock.patch("commcare_connect.opportunity.tasks.httpx.get") as get_response,
        mock.patch("commcare_connect.opportunity.tasks.default_storage.save") as save_blob,
    ):
        get_response.return_value.content = b"bytes"
        download_user_visit_attachments.run(user_visit.id)

    # Image still goes to BlobMeta
    blob_metas = BlobMeta.objects.filter(parent_id=user_visit.xform_id)
    assert blob_metas.count() == 1
    assert blob_metas.first().name == "myimage.jpg"

    # Audio goes to AudioAttachment, not BlobMeta
    assert not BlobMeta.objects.filter(name="recording.m4a").exists()
    audios = AudioAttachment.objects.filter(user_visit=user_visit)
    assert audios.count() == 1
    audio = audios.first()
    assert audio.name == "recording.m4a"
    assert audio.content_type == "audio/mp4"
    assert audio.content_length == 50
    # Bytes saved under the audio's blob_id
    saved_keys = [call.args[0] for call in save_blob.call_args_list]
    assert str(audio.blob_id) in saved_keys


def test_download_audio_attachment_is_idempotent(mobile_user: User, opportunity: Opportunity):
    user_visit = UserVisitFactory.create(
        user=mobile_user,
        opportunity=opportunity,
        form_json={"attachments": {"recording.m4a": {"content_type": "audio/mp4", "length": 50}}},
    )
    with (
        mock.patch("commcare_connect.opportunity.tasks.httpx.get") as get_response,
        mock.patch("commcare_connect.opportunity.tasks.default_storage.save") as save_blob,
    ):
        get_response.return_value.content = b"bytes"
        download_user_visit_attachments.run(user_visit.id)
        download_user_visit_attachments.run(user_visit.id)

    assert AudioAttachment.objects.filter(user_visit=user_visit).count() == 1
    assert save_blob.call_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest commcare_connect/opportunity/tests/test_tasks.py::test_download_routes_audio_to_audio_attachment commcare_connect/opportunity/tests/test_tasks.py::test_download_audio_attachment_is_idempotent -v`
Expected: FAIL — audio currently lands in `BlobMeta`, so `BlobMeta.objects.filter(name="recording.m4a").exists()` is True and `AudioAttachment` count is 0.

- [ ] **Step 3: Add `AudioAttachment` to the model import**

In `commcare_connect/opportunity/tasks.py`, in the `from commcare_connect.opportunity.models import (` block (starts line 39), add `AudioAttachment` (keep alphabetical ordering if the block is sorted).

- [ ] **Step 4: Refactor `_download_attachments` and add the two savers**

Replace the existing `_download_attachments` function (lines 379-400) with:

```python
def _download_attachments(api_key, domain: str, xform_id: str, attachments: dict, user_visit=None):
    for name, blob in attachments.items():
        if name == "form.xml":
            continue
        url = f"{api_key.hq_server.url}/a/{domain}/api/form/attachment/{xform_id}/{name}"
        content_type = blob.get("content_type") or ""
        if user_visit is not None and content_type.startswith("audio/"):
            _save_audio_attachment(api_key, url, name, blob, user_visit)
        else:
            _save_blob_attachment(api_key, url, name, blob, xform_id)


def _fetch_hq_attachment(api_key, url):
    response = httpx.get(
        url,
        headers={"Authorization": f"ApiKey {api_key.user.email}:{api_key.api_key}"},
    )
    return response.content


def _save_blob_attachment(api_key, url, name, blob, xform_id):
    with transaction.atomic():
        blob_meta, created = BlobMeta.objects.get_or_create(
            name=name,
            parent_id=xform_id,
            defaults={
                "content_length": blob["length"],
                "content_type": blob["content_type"],
            },
        )
        if not created:
            return
        content = _fetch_hq_attachment(api_key, url)
        default_storage.save(str(blob_meta.blob_id), ContentFile(content, name))


def _save_audio_attachment(api_key, url, name, blob, user_visit):
    with transaction.atomic():
        audio, created = AudioAttachment.objects.get_or_create(
            user_visit=user_visit,
            name=name,
            defaults={
                "content_length": blob["length"],
                "content_type": blob["content_type"],
            },
        )
        if not created:
            return
        content = _fetch_hq_attachment(api_key, url)
        default_storage.save(str(audio.blob_id), ContentFile(content, name))
```

- [ ] **Step 5: Pass `user_visit` from the user-visit task**

In `download_user_visit_attachments` (line 411-416), change the final call to pass the visit:

```python
    _download_attachments(api_key, domain, user_visit.xform_id, attachments, user_visit=user_visit)
```

Leave `download_inaccessibility_request_attachments` (line 427-433) unchanged — it has no `UserVisit`, so it calls `_download_attachments(api_key, domain, xform_id, attachments)` and everything continues to route to `BlobMeta`.

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `pytest commcare_connect/opportunity/tests/test_tasks.py::test_download_routes_audio_to_audio_attachment commcare_connect/opportunity/tests/test_tasks.py::test_download_audio_attachment_is_idempotent -v`
Expected: PASS (2 passed).

- [ ] **Step 7: Run the existing attachment tests to confirm no regressions**

Run: `pytest commcare_connect/opportunity/tests/test_tasks.py -k attachment -v`
Expected: PASS — including the pre-existing `test_download_attachments`, `test_download_inaccessibility_request_attachments_creates_blobs`, and `test_download_inaccessibility_request_attachments_skips_existing_blobs`.

- [ ] **Step 8: Commit**

```bash
git add commcare_connect/opportunity/tasks.py commcare_connect/opportunity/tests/test_tasks.py
git commit -m "Route audio form attachments to AudioAttachment at download time"
```

---

### Task 3: Serve audio bytes via a dedicated view + URL

**Files:**
- Modify: `commcare_connect/opportunity/views.py` (add `fetch_audio_attachment` after `fetch_attachment` at line 1146; add `AudioAttachment` to the model import block starting line ~39)
- Modify: `commcare_connect/opportunity/urls.py` (add route after line 102; add `fetch_audio_attachment` to the views import block at line 4-38)
- Test: `commcare_connect/opportunity/tests/test_views.py`

**Interfaces:**
- Consumes: `AudioAttachment` (Task 1), `AudioAttachmentFactory` (Task 1).
- Produces: URL name `opportunity:fetch_audio_attachment` with kwargs `org_slug`, `opp_id`, `pk` → streams the audio file with its `content_type`.

- [ ] **Step 1: Write the failing tests**

In `commcare_connect/opportunity/tests/test_views.py`, add (add imports at top as needed: `from django.core.files.base import ContentFile`, `from django.core.files.storage import storages`, `from django.urls import reverse`, `from commcare_connect.opportunity.tests.factories import AudioAttachmentFactory, UserVisitFactory`):

```python
@pytest.mark.django_db
def test_fetch_audio_attachment_returns_file(client, organization, org_user_member, opportunity):
    visit = UserVisitFactory.create(opportunity=opportunity)
    audio = AudioAttachmentFactory.create(user_visit=visit, content_type="audio/mp4")
    storages["default"].save(str(audio.blob_id), ContentFile(b"audiobytes"))

    url = reverse(
        "opportunity:fetch_audio_attachment",
        args=(organization.slug, opportunity.id, audio.pk),
    )
    client.force_login(org_user_member)
    response = client.get(url)

    assert response.status_code == 200
    assert response["Content-Type"] == "audio/mp4"
    assert b"".join(response.streaming_content) == b"audiobytes"


@pytest.mark.django_db
def test_fetch_audio_attachment_wrong_opportunity_returns_404(
    client, organization, org_user_member, opportunity
):
    other_visit = UserVisitFactory.create()  # belongs to a different opportunity
    audio = AudioAttachmentFactory.create(user_visit=other_visit)

    url = reverse(
        "opportunity:fetch_audio_attachment",
        args=(organization.slug, opportunity.id, audio.pk),
    )
    client.force_login(org_user_member)
    response = client.get(url)

    assert response.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest commcare_connect/opportunity/tests/test_views.py::test_fetch_audio_attachment_returns_file commcare_connect/opportunity/tests/test_views.py::test_fetch_audio_attachment_wrong_opportunity_returns_404 -v`
Expected: FAIL — `NoReverseMatch` (URL not defined yet).

- [ ] **Step 3: Add the view**

In `commcare_connect/opportunity/views.py`, add `AudioAttachment` to the `from commcare_connect.opportunity.models import (` block. Then add this function directly after `fetch_attachment` (after line 1146). Required names are already imported: `storages` (line 20), `get_object_or_404` (line 41), `FileResponse`/`HttpResponseNotFound` (line 39), and the `org_member_required`/`opportunity_required` decorators used by `fetch_attachment`.

```python
@org_member_required
@opportunity_required
def fetch_audio_attachment(request, org_slug, opp_id, pk):
    audio = get_object_or_404(AudioAttachment, pk=pk)

    if audio.user_visit.opportunity_id != request.opportunity.id:
        return HttpResponseNotFound()

    try:
        attachment = storages["default"].open(audio.blob_id)
    except FileNotFoundError:
        return HttpResponseNotFound()
    return FileResponse(attachment, filename=audio.name, content_type=audio.content_type)
```

- [ ] **Step 4: Add the URL route**

In `commcare_connect/opportunity/urls.py`, add `fetch_audio_attachment` to the `from commcare_connect.opportunity.views import (` block (alongside `fetch_attachment` at line 38), then add this route directly after line 102:

```python
    path(
        "<slug:opp_id>/fetch_audio_attachment/<int:pk>",
        view=fetch_audio_attachment,
        name="fetch_audio_attachment",
    ),
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest commcare_connect/opportunity/tests/test_views.py::test_fetch_audio_attachment_returns_file commcare_connect/opportunity/tests/test_views.py::test_fetch_audio_attachment_wrong_opportunity_returns_404 -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add commcare_connect/opportunity/views.py commcare_connect/opportunity/urls.py commcare_connect/opportunity/tests/test_views.py
git commit -m "Add fetch_audio_attachment view and URL for serving audio"
```

---

### Task 4: Render playable audio on the visit verification page

**Files:**
- Modify: `commcare_connect/templates/opportunity/user_visit_details.html` (insert after the image Media section `{% endif %}` at line 93, before the Verification Flags section at line 94)
- Test: `commcare_connect/opportunity/tests/test_views.py`

**Interfaces:**
- Consumes: `UserVisit.audio` (Task 1), `opportunity:fetch_audio_attachment` URL (Task 3).
- Produces: an "Audio" section rendering one `<audio controls>` per attachment plus transcript/translation text when present.

- [ ] **Step 1: Write the failing test**

In `commcare_connect/opportunity/tests/test_views.py`, add (reuses imports from Task 3):

```python
@pytest.mark.django_db
def test_user_visit_details_renders_audio_player(client, organization, org_user_member, opportunity):
    form_json = {
        "domain": "test",
        "id": "xform-123",
        "app_id": "app-1",
        "build_id": "build-1",
        "received_on": "2026-06-30T00:00:00Z",
        "form": {"@xmlns": "http://example.com/form"},
        "metadata": {
            "timeStart": "2026-06-30T00:00:00Z",
            "timeEnd": "2026-06-30T00:05:00Z",
            "app_build_version": "1",
            "username": "worker",
            "location": None,
        },
        "attachments": {},
    }
    visit = UserVisitFactory.create(opportunity=opportunity, form_json=form_json)
    audio = AudioAttachmentFactory.create(user_visit=visit, transcript="hello world")

    url = reverse(
        "opportunity:user_visit_details",
        args=(organization.slug, opportunity.opportunity_id, visit.user_visit_id),
    )
    client.force_login(org_user_member)
    response = client.get(url)

    assert response.status_code == 200
    audio_url = reverse(
        "opportunity:fetch_audio_attachment",
        args=(organization.slug, opportunity.opportunity_id, audio.pk),
    )
    assert audio_url.encode() in response.content
    assert b"hello world" in response.content
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest commcare_connect/opportunity/tests/test_views.py::test_user_visit_details_renders_audio_player -v`
Expected: FAIL — the audio URL is not in the rendered page.

- [ ] **Step 3: Add the Audio section to the template**

In `commcare_connect/templates/opportunity/user_visit_details.html`, insert between the image Media section's closing `{% endif %}` (line 93) and the `<!-- Verification Flags -->` comment (line 94):

```django
  <!-- Audio Section -->
  {% if user_visit.audio %}
    <div class="flex flex-col gap-4">
      <p class="text-sm font-medium text-brand-deep-purple">{% translate "Audio" %}</p>
      {% for audio in user_visit.audio %}
        <div class="flex flex-col gap-2">
          <audio controls preload="none" class="w-full">
            <source src="{% url 'opportunity:fetch_audio_attachment' org_slug=request.org.slug opp_id=user_visit.opportunity.opportunity_id pk=audio.pk %}"
                    type="{{ audio.content_type }}">
            {% translate "Your browser does not support the audio element." %}
          </audio>
          {% if audio.transcript %}
            <div>
              <p class="text-sm font-medium text-brand-blue-light">{% translate "Transcript" %}</p>
              <p class="text-sm font-normal text-brand-deep-purple">{{ audio.transcript }}</p>
            </div>
          {% endif %}
          {% if audio.translation %}
            <div>
              <p class="text-sm font-medium text-brand-blue-light">{% translate "Translation" %}</p>
              <p class="text-sm font-normal text-brand-deep-purple">{{ audio.translation }}</p>
            </div>
          {% endif %}
        </div>
      {% endfor %}
    </div>
    <hr class="h-px bg-gray-200 border-0">
  {% endif %}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest commcare_connect/opportunity/tests/test_views.py::test_user_visit_details_renders_audio_player -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add commcare_connect/templates/opportunity/user_visit_details.html commcare_connect/opportunity/tests/test_views.py
git commit -m "Render playable audio attachments on visit verification page"
```

---

### Task 5: Full verification pass

**Files:** none (verification only)

- [ ] **Step 1: Run the full opportunity test suite for affected areas**

Run: `pytest commcare_connect/opportunity/tests/test_models.py commcare_connect/opportunity/tests/test_tasks.py commcare_connect/opportunity/tests/test_views.py -v`
Expected: PASS (no failures, including all pre-existing tests).

- [ ] **Step 2: Run lint/format hooks**

Run: `prek run -a`
Expected: all hooks pass (ruff, ruff-format, prettier, djLint, etc.). Fix any reported issues and re-run.

- [ ] **Step 3: Confirm migrations are complete and consistent**

Run: `./manage.py makemigrations --check --dry-run opportunity`
Expected: "No changes detected" (the migration from Task 1 covers the model).

- [ ] **Step 4: Commit any lint/format fixes**

```bash
git add -A
git commit -m "Lint and format fixes for audio attachments" || echo "nothing to commit"
```

---

## Self-Review Notes

- **Spec coverage:** Model + transcript/translation (Task 1) ✓; download routing audio→AudioAttachment only (Task 2) ✓; dedicated serving view with `user_visit.opportunity` permission check + range support via `FileResponse` (Task 3) ✓; native `<audio>` rendering with transcript/translation (Task 4) ✓; testing across model/task/view (Tasks 1-4) plus regression pass (Task 5) ✓.
- **FK target:** `AudioAttachment.user_visit` (per the brainstorming decision); deliver-unit context is reachable via `user_visit.deliver_unit`.
- **Type consistency:** `_save_audio_attachment`/`_save_blob_attachment`/`_fetch_hq_attachment` and the `user_visit=` kwarg are defined in Task 2 and not referenced elsewhere; `UserVisit.audio` defined in Task 1 is consumed in Task 4; `fetch_audio_attachment` URL name defined in Task 3 is consumed in Task 4.
- **Inaccessibility path:** intentionally unchanged — no `UserVisit`, so audio there continues to route to `BlobMeta` (out of scope).
