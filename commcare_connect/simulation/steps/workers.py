class WorkerSteps:
    """Inviting workers and what they do on the phone before delivering: claim and learn."""

    def invite(self, opportunity, count):
        """Invite ``count`` new ConnectID users by phone number, as ``nm_admin``. Returns the invited ``User``s."""
        raise NotImplementedError

    def claim(self, opportunity, workers):
        """Claim the opportunity for each worker through the mobile claim API."""
        raise NotImplementedError

    def learn(self, opportunity, workers, *, complete=1.0, score=100):
        """Submit learn-app forms for each worker.

        ``complete`` is the share of learn modules each worker finishes; workers who finish every module also
        submit the assessment with ``score``.
        """
        raise NotImplementedError
