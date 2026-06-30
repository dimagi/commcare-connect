# Audio Attachments for Delivery Forms — Design

**Date:** 2026-06-30
**Branch:** ce/audio-attachments

## Problem

Delivery forms received from CommCare HQ can include audio attachments. Today, every
non-XML attachment on a received form is downloaded into the generic `BlobMeta` table
(keyed by `parent_id = xform_id`) and images are rendered on the visit verification page.
Audio currently lands in `BlobMeta` undifferentiated and is never surfaced.

We want audio attachments to live in a dedicated table so we can store additional
metadata (transcript, translation, and more later) alongside the pointer to the S3
object, and we want the audio to be playable on the visit verification page.

## Scope

- New `AudioAttachment` model (dedicated table, FK to `UserVisit`).
- Download routing: audio attachments go to `AudioAttachment` instead of `BlobMeta`.
- Dedicated view + URL to serve audio bytes to the browser.
- Render playable audio (native HTML5 `<audio controls>`) on the visit verification page.

Out of scope: populating `transcript`/`translation` (a separate process fills these
later), duration/other metadata fields (added later when needed), any change to image
handling.

## 1. Data model — `AudioAttachment`

Location: `commcare_connect/opportunity/models.py`.

Modeled as a sibling of `BlobMeta` (plain `models.Model`, matching that precedent —
**not** `BaseModel`, because these rows are created by a background Celery task with no
request user, so `created_by`/`modified_by` would always be null). An explicit
`date_created` is included since `transcript`/`translation` are filled in later and the
creation time is useful.

```python
class AudioAttachment(models.Model):
    user_visit = models.ForeignKey(
        "UserVisit", on_delete=models.CASCADE, related_name="audio_attachments"
    )
    blob_id = models.CharField(max_length=255, default=uuid4)  # S3 storage key
    name = models.CharField(max_length=255)                    # original filename
    content_type = models.CharField(max_length=255, null=True) # e.g. "audio/mp4"
    content_length = models.IntegerField()
    transcript = models.TextField(blank=True, default="")      # filled later
    translation = models.TextField(blank=True, default="")     # filled later
    date_created = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("user_visit", "name")]
        indexes = [models.Index(fields=["blob_id"])]
```

Notes:
- The deliver-unit context the feature was originally framed around is reachable via
  `audio_attachment.user_visit.deliver_unit` — no separate FK needed.
- `unique_together = (user_visit, name)` makes re-download on Celery task retry
  idempotent (mirrors `BlobMeta`'s `(parent_id, name)`).
- New migration required.

## 2. Download routing

Location: `commcare_connect/opportunity/tasks.py`.

`_download_attachments(api_key, domain, xform_id, attachments)` currently iterates the
attachments dict, creates a `BlobMeta` per entry (skipping `form.xml`), and saves the
downloaded bytes to `default_storage` keyed by `blob_id`.

Change: branch on `content_type`. When it starts with `audio/`, create an
`AudioAttachment` (via `get_or_create` on `user_visit` + `name`) and save the file to
storage by its `blob_id`; **skip** creating a `BlobMeta` for it. All other attachments
keep the existing `BlobMeta` path unchanged.

This requires the `user_visit` (or its id) to be available inside the helper to set the
FK. Today the helper only receives `xform_id`. The caller
`download_user_visit_attachments(user_visit_id)` already loads the `UserVisit`, so we
thread the `user_visit` (or id) through to the helper.
`download_inaccessibility_request_attachments` has no associated `UserVisit`; audio
routing only applies to the user-visit path, so the inaccessibility path continues to
create `BlobMeta` for everything (no `user_visit` to attach to).

## 3. Serving endpoint

Location: `commcare_connect/opportunity/views.py` + `commcare_connect/opportunity/urls.py`.

New view `fetch_audio_attachment(request, org_slug, opp_id, pk)`:
- Look up `AudioAttachment` by `pk` (404 if missing).
- Permission check: `audio.user_visit.opportunity == request.opportunity`
  (404 otherwise) — simpler than the `BlobMeta` check because the FK is direct.
- Open the file from `storages["default"]` by `blob_id` and return a `FileResponse`
  with `content_type=audio.content_type` and `filename=audio.name`. Mirrors
  `fetch_attachment`.

New URL route alongside `fetch_attachment`:

```python
path("<slug:opp_id>/fetch_audio_attachment/<int:pk>", view=fetch_audio_attachment,
     name="fetch_audio_attachment"),
```

## 4. Rendering

Location: `commcare_connect/templates/opportunity/user_visit_details.html`, plus the
backing view `user_visit_details` in `views.py`.

- `user_visit_details` already builds an `images` queryset
  (`BlobMeta.objects.filter(parent_id=..., content_type__startswith="image/")`) and
  passes it in context. Mirror that with an `audio_attachments` entry in the context,
  sourced from `user_visit.audio_attachments.all()`, so the template stays parallel to
  the image path.
- Add a new **"Audio"** section as a sibling to the existing image "Media" carousel,
  shown only when the visit has audio attachments. Each attachment renders:
  - a native HTML5 `<audio controls preload="none">` element whose `<source>` points at
    the `fetch_audio_attachment` URL (`content_type` set from the model). The dedicated
    view serving bytes via `FileResponse` supports range requests, enabling seeking.
  - the `transcript` and `translation` text beneath the player when non-empty.
- No JS library required; native controls cover play/pause/seek. Follows the project's
  "keep JS in JS files / no inline JS" rule — none needed here.

## Data flow (audio)

```
HQ form received
  └─ process_deliver_unit → UserVisit created (form_json holds attachments dict)
       └─ on_commit: download_user_visit_attachments.delay(user_visit.id)
            └─ _download_attachments: for each attachment,
                 content_type startswith "audio/"?
                   ├─ yes → AudioAttachment.get_or_create(user_visit, name) + save bytes to S3 by blob_id
                   └─ no  → BlobMeta.get_or_create(...) + save bytes (unchanged)

Verification page
  └─ user_visit_details renders Audio section
       └─ <audio><source src="…/fetch_audio_attachment/<pk>"></audio>
            └─ fetch_audio_attachment: perm check via user_visit.opportunity → FileResponse from S3
```

## Testing

- **Download routing** (`form_receiver`/`opportunity` task tests): given a form_json with
  an `audio/*` attachment, `_download_attachments` creates an `AudioAttachment` (correct
  FK, name, content_type, content_length) and saves bytes to storage; does **not** create
  a `BlobMeta` for it. An `image/*` attachment in the same form still creates a `BlobMeta`
  and no `AudioAttachment`. Re-running the task is idempotent (no duplicate rows).
- **Serving view**: returns the bytes/content_type for an audio attachment in the
  request's opportunity; returns 404 for an attachment belonging to a different
  opportunity and for a missing pk.
- **Rendering**: prefer testing the underlying accessor/helper (per project guidance —
  test functions, not view responses). A light template/context assertion that audio
  attachments are exposed is acceptable.
- Use existing fixtures (`opportunity`, `mobile_user`, etc.) and factories; add an
  `AudioAttachment` factory and a small audio-bearing `form_json` fixture as needed.
```

