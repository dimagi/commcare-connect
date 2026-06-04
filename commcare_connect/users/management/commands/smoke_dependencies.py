"""
Staging smoke-test for the dependency bumps that the unit suite can't reach
(production-only or never-called paths). Run against a staging deploy:

    ./manage.py smoke_dependencies --email-to you@dimagi.com --sms-to +15555550123

Each check is independent and non-fatal: a failure is reported, not raised, so
one broken integration doesn't hide the others. Exit code is non-zero if any
selected check fails (CI/deploy-gate friendly).
"""

import base64
import io
import uuid

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Smoke-test production-path dependency bumps (anymail/SES, twilio, S3, whitenoise, sentry, pillow+weasyprint)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--email-to", help="Recipient for the anymail/SES test send. Skipped if omitted.")
        parser.add_argument("--sms-to", help="E.164 number for the twilio test SMS. Skipped if omitted.")
        parser.add_argument(
            "--static-path",
            default="admin/css/base.css",
            help="A static asset path to resolve via the manifest storage (whitenoise). "
            "Defaults to a Django admin asset that is always present after collectstatic.",
        )
        parser.add_argument("--keep-s3", action="store_true", help="Don't delete the test object written to storage.")

    def handle(self, *args, **opts):
        self.results = []
        self._check_storage(keep=opts["keep_s3"])
        self._check_whitenoise(opts["static_path"])
        self._check_pillow_weasyprint()
        self._check_sentry()
        self._check_email(opts["email_to"])
        self._check_sms(opts["sms_to"])

        self.stdout.write("\n" + "=" * 60)
        failed = [name for name, status, _ in self.results if status == "FAIL"]
        for name, status, detail in self.results:
            style = {"OK": self.style.SUCCESS, "FAIL": self.style.ERROR, "SKIP": self.style.WARNING}[status]
            self.stdout.write(f"{style(status.ljust(4))} {name:24} {detail}")
        self.stdout.write("=" * 60)
        if failed:
            self.stderr.write(self.style.ERROR(f"\n{len(failed)} check(s) failed: {', '.join(failed)}"))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("\nAll selected checks passed."))

    # --- helpers ---------------------------------------------------------

    def _record(self, name, status, detail=""):
        self.results.append((name, status, detail))

    def _run(self, name, fn, skip_reason=None):
        if skip_reason:
            self._record(name, "SKIP", skip_reason)
            return
        try:
            detail = fn() or ""
            self._record(name, "OK", detail)
        except Exception as e:  # noqa: BLE001 - smoke test reports failures, never raises
            self._record(name, "FAIL", f"{type(e).__name__}: {e}")

    # --- checks ----------------------------------------------------------

    def _check_storage(self, keep):
        """django-storages 1.13->1.14: write + read-back + delete via default storage (S3 in prod)."""

        def fn():
            from django.core.files.base import ContentFile
            from django.core.files.storage import default_storage

            key = f"smoke/dep-check-{uuid.uuid4().hex}.txt"
            payload = b"smoke-test"
            saved = default_storage.save(key, ContentFile(payload))
            try:
                with default_storage.open(saved) as fh:
                    assert fh.read() == payload, "read-back mismatch"
                return f"backend={type(default_storage).__module__} key={saved}"
            finally:
                if not keep:
                    default_storage.delete(saved)

        self._run("storages (S3)", fn)

    def _check_whitenoise(self, static_path):
        """whitenoise 6.5->6.12: resolve a hashed URL via the manifest storage (requires collectstatic)."""

        def fn():
            from django.contrib.staticfiles.storage import staticfiles_storage

            url = staticfiles_storage.url(static_path)
            assert staticfiles_storage.exists(
                staticfiles_storage.stored_name(static_path)
            ), f"{static_path} not collected"
            return f"{static_path} -> {url}"

        self._run("whitenoise (static)", fn)

    def _check_pillow_weasyprint(self):
        """pillow 10->12 (+ django-weasyprint): render a raster image into a PDF."""

        def fn():
            from PIL import Image
            from PIL import __version__ as pil_version
            from weasyprint import HTML

            buf = io.BytesIO()
            Image.new("RGB", (4, 4), (200, 30, 30)).save(buf, format="PNG")
            data = base64.b64encode(buf.getvalue()).decode()
            pdf = HTML(string=f'<img src="data:image/png;base64,{data}">').write_pdf()
            assert pdf[:4] == b"%PDF", "weasyprint did not emit a PDF"
            return f"pillow={pil_version} pdf_bytes={len(pdf)}"

        self._run("pillow + weasyprint", fn)

    def _check_sentry(self):
        """sentry-sdk 1->2: confirm the SDK is initialised and can flush an event."""
        import sentry_sdk

        get_client = getattr(sentry_sdk, "get_client", None)
        client = get_client() if get_client else sentry_sdk.Hub.current.client
        if client is None or not getattr(client, "dsn", None):
            self._record("sentry-sdk", "SKIP", "no DSN configured in this environment")
            return

        def fn():
            event_id = sentry_sdk.capture_message("smoke_dependencies: sentry-sdk 2.x check")
            sentry_sdk.flush(timeout=5)
            return f"event_id={event_id}"

        self._run("sentry-sdk", fn)

    def _check_email(self, to):
        """django-anymail 10->14 (SES v1->v2): real send through the configured EMAIL_BACKEND."""

        def fn():
            from django.core.mail import send_mail

            sent = send_mail(
                subject="[smoke] dependency bump check",
                message="anymail/SES v2 smoke test from smoke_dependencies.",
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                recipient_list=[to],
                fail_silently=False,
            )
            return f"backend={settings.EMAIL_BACKEND} sent={sent} to={to}"

        self._run("anymail (SES)", fn, skip_reason=None if to else "pass --email-to to enable")

    def _check_sms(self, to):
        """twilio 8->9: exercise utils.sms.send_sms (Client(...).messages.create)."""

        def fn():
            from commcare_connect.utils.sms import send_sms

            msg = send_sms(to=to, body="[smoke] twilio 9.x dependency check")
            return f"sid={getattr(msg, 'sid', msg)} to={to}"

        self._run("twilio (SMS)", fn, skip_reason=None if to else "pass --sms-to to enable")
