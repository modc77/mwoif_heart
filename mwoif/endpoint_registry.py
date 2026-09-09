from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class EndpointInfo:
    action: str
    transport: str
    endpoint: str
    status: str


ENDPOINTS: dict[str, EndpointInfo] = {
    "friend_list": EndpointInfo(
        "friend_list", "grpc",
        "/service.api.FriendAPI/ListFriends",
        "LIVE_VALIDATED",
    ),
    "friend_add": EndpointInfo(
        "friend_add", "grpc",
        "/service.api.FriendAPI/SendFriendRequest",
        "LIVE_VALIDATED",
    ),
    "friend_accept": EndpointInfo(
        "friend_accept", "grpc",
        "/service.api.FriendAPI/HandleFriendRequest",
        "LIVE_VALIDATED",
    ),
    "friend_remove": EndpointInfo(
        "friend_remove", "grpc",
        "/service.api.FriendAPI/RemoveFriend",
        "LIVE_VALIDATED_FIELD2_PLAYER_IDS",
    ),
    "heart_send": EndpointInfo(
        "heart_send", "ds",
        "game/sendLifeMail2.ds",
        "LIVE_VALIDATED_DS_V4",
    ),
    "heart_mail_list": EndpointInfo(
        "heart_mail_list", "ds",
        "game/myMailList.ds",
        "LIVE_VALIDATED_DS_V4_READ_ONLY",
    ),
    "heart_receive": EndpointInfo(
        "heart_receive", "ds",
        "game/acceptLifeMail4.ds",
        "LIVE_VALIDATED_DS_V4",
    ),
}


def public_registry() -> dict[str, dict[str, str]]:
    return {name: asdict(info) for name, info in ENDPOINTS.items()}
