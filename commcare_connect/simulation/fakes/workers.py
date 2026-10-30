"""ConnectID, Twilio and HQ-user stand-ins used while inviting, claiming and learning."""

import random
import uuid
from types import SimpleNamespace
from unittest import mock

import httpx

from commcare_connect.connect_id_client.models import ConnectIdUser, MessagingResponse

FIRST_NAMES = ("Amina", "Chidi", "Esther", "Kofi", "Lindiwe", "Musa", "Nia", "Tendai", "Wanjiru", "Yaw")
LAST_NAMES = ("Banda", "Diallo", "Mensah", "Moyo", "Nkosi", "Okafor", "Otieno", "Phiri", "Sow", "Tembo")
# A fixed namespace keeps each simulated HQ user's id the same across runs.
HQ_USER_NAMESPACE = uuid.UUID("5b0a3e8e-6a43-4c55-9f4e-6f3c3f7d9a21")


def patches():
    return [
        mock.patch("commcare_connect.opportunity.tasks.fetch_users", fake_fetch_users),
        mock.patch("commcare_connect.opportunity.tasks.send_sms", fake_send_sms),
        mock.patch("commcare_connect.opportunity.tasks.send_message", fake_send_message),
        # Only the HTTP call is replaced, so the HQ payload and response handling around it run for real.
        mock.patch("commcare_connect.users.helpers.httpx", FakeHQHttpx),
    ]


def fake_fetch_users(phone_number_list):
    """Every number belongs to a ConnectID user whose username and name follow from the number."""
    return [connect_id_user(number) for number in phone_number_list]


def connect_id_user(phone_number):
    digits = phone_number.lstrip("+")
    rng = random.Random(digits)
    return ConnectIdUser(
        name=f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
        username=f"sim{digits}",
        phone_number=phone_number,
    )


def fake_send_sms(to, body):
    return SimpleNamespace(sid=f"SM{uuid.uuid4().hex}")


def fake_send_message(message):
    return MessagingResponse(all_success=True, responses=[])


class FakeHQHttpx:
    """The slice of ``httpx`` that ``users.helpers`` uses to create HQ mobile workers."""

    HTTPStatusError = httpx.HTTPStatusError

    @staticmethod
    def post(url, *, json, headers, timeout):
        """HQ answers a created mobile worker with 201 and its id."""
        domain = url.split("/a/", 1)[1].split("/", 1)[0]
        hq_user_id = uuid.uuid5(HQ_USER_NAMESPACE, f"{domain}/{json['username']}").hex
        return httpx.Response(201, json={"id": hq_user_id}, request=httpx.Request("POST", url))
