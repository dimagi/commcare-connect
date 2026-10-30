class SetupSteps:
    """Organizations, programs and opportunities, created through the same forms the setup pages use."""

    def org(self, name, *, admin, program_manager=False):
        """Create an organization with ``admin`` as its admin member. Returns the ``Organization``."""
        raise NotImplementedError

    def program(self, pm_org, *, name, budget, start_date, end_date, currency="USD", country="USA"):
        """Create a program owned by ``pm_org``, acting as ``pm_admin``. Returns the ``Program``."""
        raise NotImplementedError

    def join_program(self, program, nm_org):
        """Invite ``nm_org`` to ``program`` (as PM), apply (as NM) and accept (as PM).

        Returns the accepted ``ProgramApplication``.
        """
        raise NotImplementedError

    def opportunity(
        self,
        program,
        nm_org,
        *,
        name,
        payment_units=None,
        start_date=None,
        end_date=None,
        total_budget=None,
        max_users=10,
        finalize=True,
        verification_flags=None,
    ):
        """Create a managed opportunity for ``nm_org`` under ``program`` through the setup wizard.

        Runs init, the learn-module/deliver-unit sync, one payment unit per ``PaymentUnitSpec`` in
        ``payment_units`` (one default unit when omitted) and, when ``finalize`` is true, the finalize step.
        ``verification_flags`` is a dict of ``OpportunityVerificationFlagsConfigForm`` fields. Each opportunity gets
        its own simulated app ids. Returns the ``Opportunity``.
        """
        raise NotImplementedError
