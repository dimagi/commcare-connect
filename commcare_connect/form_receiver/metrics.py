"""Sentry metrics for forms received from CommCare HQ. Definitions are in README.md ("Metrics")."""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime

from django.db import transaction
from django.utils.timezone import now
from sentry_sdk import metrics

FORMS_METRIC = "form_receiver.forms"
PROCESSING_TIME_METRIC = "form_receiver.processing_time"
END_TO_END_TIME_METRIC = "form_receiver.end_to_end_time"

logger = logging.getLogger(__name__)


class Outcome:
    PROCESSED = "processed"
    IGNORED = "ignored"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    ERROR = "error"


@dataclass
class FormMetrics:
    """Collects what one POST to the receiver did, and records it once the request is finished."""

    started: float = field(default_factory=time.monotonic)
    received_on: datetime | None = None
    app_type: str | None = None
    duplicate: bool = False

    def record_response(self, status_code: int):
        """Record a finished request. A 200 is recorded when its transaction commits; a rolled-back one never is."""
        if self.duplicate:
            # Nothing was written, and the failed insert may have marked the transaction for rollback.
            self._record(Outcome.DUPLICATE)
        elif status_code < 300:
            transaction.on_commit(self._record_success, robust=True)
        else:
            self._record(Outcome.REJECTED if status_code < 500 else Outcome.ERROR)

    def record_error(self):
        self._record(Outcome.ERROR)

    def _record_success(self):
        self._record(Outcome.IGNORED if self.app_type is None else Outcome.PROCESSED)

    def _record(self, outcome: str):
        # The status code decides whether HQ retries, so a metrics failure must not change the response.
        try:
            self._send(outcome)
        except Exception:
            logger.exception("Failed to record form receiver metrics")

    def _send(self, outcome: str):
        attributes = {"outcome": outcome, "app_type": self.app_type or "none"}
        metrics.count(FORMS_METRIC, 1, attributes=attributes)
        processing_ms = (time.monotonic() - self.started) * 1000
        metrics.distribution(PROCESSING_TIME_METRIC, processing_ms, unit="millisecond", attributes=attributes)
        # Resends and ignored forms would skew the end-to-end time, so it covers newly processed forms only.
        if outcome == Outcome.PROCESSED and self.received_on:
            end_to_end_ms = (now() - self.received_on).total_seconds() * 1000
            metrics.distribution(END_TO_END_TIME_METRIC, end_to_end_ms, unit="millisecond", attributes=attributes)
