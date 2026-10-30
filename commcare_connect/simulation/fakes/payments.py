"""Stand-ins used while paying workers and invoicing (exchange rates, payment notifications)."""

from unittest import mock

# Units of each currency per US dollar, in the shape openexchangerates.org returns them.
FIXED_RATES = {
    "USD": 1.0,
    "ETB": 57.5,
    "EUR": 0.92,
    "GHS": 15.2,
    "INR": 83.4,
    "KES": 129.5,
    "MWK": 1735.0,
    "MZN": 63.9,
    "NGN": 1550.0,
    "TZS": 2690.0,
    "UGX": 3720.0,
    "ZMW": 26.8,
}
# A currency missing above is priced at par, so payments and invoices still work in it, whether the app looks up
# one currency or every opportunity currency at once.
DEFAULT_RATE = 1.0


def patches():
    return [
        mock.patch("commcare_connect.opportunity.tasks.request_rates", fake_request_rates),
        mock.patch("commcare_connect.opportunity.tasks.send_message_bulk", fake_send_message_bulk),
    ]


class _FixedRates(dict):
    def __missing__(self, currency):
        return DEFAULT_RATE

    def get(self, currency, default=None):
        return self[currency]


def fake_request_rates(url):
    """The ``rates`` openexchangerates.org would return, the same for every date.

    ``fetch_exchange_rates`` still stores what it is given, so the app's own rate lookup and caching run.
    """
    return _FixedRates(FIXED_RATES)


def fake_send_message_bulk(messages):
    """ConnectID would push these to the workers' phones; there is no phone to reach."""
