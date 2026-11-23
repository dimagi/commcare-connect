import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from commcare_connect.program.models import Program
from commcare_connect.simulation.constants import DELIVER_UNIT_SLUGS
from commcare_connect.solicitation.models import Solicitation


@dataclass
class PaymentUnitSpec:
    """What the "Add payment unit" form is filled in with.

    ``max_daily=None`` means no daily limit, which the form only offers while the ``OPTIONAL_DAILY_LIMIT``
    switch is on, as on the page.
    """

    name: str = "Visit"
    amount: Decimal = Decimal("5")
    org_amount: Decimal = Decimal("1")
    max_total: int = 20
    max_daily: int | None = 5
    deliver_units: list[str] = field(default_factory=lambda: [DELIVER_UNIT_SLUGS[0]])


@dataclass
class SolicitationSpec:
    """What a solicitation's details page is filled in with.

    ``deadline`` must be after the moment the step runs, as the page requires, so a solicitation that has expired
    by now is built inside ``sim.at`` an earlier moment. ``delivery_type`` is a name, get-or-created like the
    program's, and ``program`` optionally links one of the posting organization's programs.
    """

    title: str
    deadline: datetime.datetime
    type: str = Solicitation.Type.RFP
    public: bool = True
    country: str = "USA"
    delivery_type: str = "Simulation"
    budget: tuple[int, int] = (10_000, 50_000)
    currency: str = "USD"
    program: Program | None = None
