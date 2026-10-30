class ReviewSteps:
    """Delivery forms and the review of the visits they create."""

    def deliver(
        self,
        opportunity,
        workers,
        *,
        per_worker,
        over_days=14,
        deliver_unit=None,
        duplicates=0,
        no_gps=0,
        too_short=0,
    ):
        """Submit ``per_worker`` deliver forms for each worker, spread over the last ``over_days`` days.

        ``duplicates``, ``no_gps`` and ``too_short`` are how many of each worker's forms trip that visit flag;
        whether a flag is raised still depends on the opportunity's verification settings, as in real use.
        ``deliver_unit`` is a deliver-unit slug; defaults to the first one. Returns the created ``UserVisit``s.
        """
        raise NotImplementedError

    def review(self, opportunity, *, approve, reject=0.0):
        """Approve and reject shares of the pending visits, as ``nm_admin``."""
        raise NotImplementedError

    def pm_review(self, opportunity, *, agree=1.0):
        """Agree with a share of the reviewed visits and disagree with the rest, as ``pm_admin``."""
        raise NotImplementedError
