import dataclasses
from enum import StrEnum

FCM_ANALYTICS_LABEL = "commcare-connect-notification"


@dataclasses.dataclass
class ConnectIdUser:
    name: str
    username: str
    phone_number: str

    def __str__(self) -> str:
        return f"{self.name} ({self.username})"


@dataclasses.dataclass
class DemoUser:
    token: str
    phone_number: str


class MessageStatus(StrEnum):
    success = "success"
    error = "error"
    deactivated = "deactivated"


@dataclasses.dataclass
class UserMessageStatus:
    username: str
    status: MessageStatus

    @classmethod
    def build(cls, username: str, status: str):
        return cls(username, MessageStatus(status))


@dataclasses.dataclass
class MessagingResponse:
    all_success: bool
    responses: list[UserMessageStatus]

    @classmethod
    def build(cls, all_success: bool, responses: list[dict]):
        return cls(all_success, [UserMessageStatus.build(**response) for response in responses])

    def get_failures(self) -> list[UserMessageStatus]:
        return [response for response in self.responses if response.status != MessageStatus.success]


@dataclasses.dataclass
class Message:
    usernames: list[str]
    title: str = None
    body: str = None
    data: dict = None
    fcm_options: dict = dataclasses.field(default_factory=lambda: {"analytics_label": FCM_ANALYTICS_LABEL})

    def asdict(self):
        return {k: v for k, v in dataclasses.asdict(self).items() if v is not None}


@dataclasses.dataclass
class Credential:
    name: str
    slug: str

    def __str__(self) -> str:
        return self.name
