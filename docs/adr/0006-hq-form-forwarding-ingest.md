# ADR-0006: Receive field data only through CommCare HQ form forwarding

**Status:** Accepted

**Author:** Charl Smit · **Created:** 2026-11-13

## Context

Workers fill in learn and deliver forms in the CommCare mobile app, which submits them to CommCare HQ. Connect needs each form to record training progress and paid work, and HQ already has a per-domain data forwarding feature (repeaters) with retries and a delivery report. A domain can run apps that have nothing to do with Connect, and Connect works with more than one HQ server.

## Decision

We will have HQ push every form to Connect's form receiver, and make the receiver's HTTP response the contract for whether HQ retries.

- HQ posts each form as JSON to `/api/receiver/`, authenticated with OAuth2 client credentials. Each HQ server has its own OAuth application linked to an `HQServer` row, which identifies the sender.
- Connect reads only the Connect blocks in the form (identified by the Connect `@xmlns`) and a fixed set of metadata, so it works with both HQ's full-form and metadata-only payloads.
- A form is routed to the one opportunity that is active, not past its `end_date`, and whose learn or deliver `CommCareApp` matches the form's domain and app on that server. More than one match is an error.
- A 400 means resending the same form can never succeed, and HQ won't retry it. A 500 or a timeout means it might, and HQ retries with backoff. A form for an app with no active opportunity gets a 200 and is ignored.
- Each form is processed in one transaction; any error rolls back every record created from it.
- A resent form is identified by `xform_id`. Unique constraints that include `xform_id` turn a resend into a no-op 200 for visits and completed modules.

## Consequences

- Connect never calls HQ to collect forms, so a form that HQ doesn't send, or stops retrying, never arrives, and its status is visible only in HQ's forwarding report.
- A rejected form has to be resent from HQ by hand once its cause is fixed.
- Errors that might be temporary must not be raised as 400s, or the form is lost.
- Forms arrive late and out of order (HQ retries over days), so no processing step may assume order.
- A form edited in HQ is resent with the same `xform_id` and is ignored, so edits after the first send never reach Connect.
- Duplicate detection depends on constraint names, so renaming one of those constraints turns a resend into a 500 that HQ retries.
- An app can serve only one active opportunity at a time.
- Anything that reads outside the Connect blocks, including attachments, does not work for domains that forward metadata only.

## Alternatives considered

- Connect pulls forms from HQ's APIs — not chosen; HQ's forwarding already provides delivery, retries and a per-form audit trail.
- Assume one HQ server per deployment — rejected once organizations needed opportunities on more than one HQ instance.
- Have HQ forward the worker's own token instead of using client credentials — rejected because HQ would be impersonating the worker.
