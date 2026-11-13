from dataclasses import dataclass, field
from decimal import Decimal

from commcare_connect.simulation.constants import DELIVER_UNIT_SLUGS


@dataclass
class PaymentUnitSpec:
    """What the "Add payment unit" form is filled in with. ``max_daily=None`` means no daily limit."""

    name: str = "Visit"
    amount: Decimal = Decimal("5")
    org_amount: Decimal = Decimal("1")
    max_total: int = 20
    max_daily: int | None = 5
    deliver_units: list[str] = field(default_factory=lambda: [DELIVER_UNIT_SLUGS[0]])
