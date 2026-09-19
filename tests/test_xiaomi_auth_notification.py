from __future__ import annotations

from pathlib import Path

import mi_health_link.notifications as notifications


class _Response:
    def raise_for_status(self) -> None:
        return None


def test_xiaomi_auth_ntfy_notification_is_optional(monkeypatch) -> None:
    monkeypatch.delenv("XIAOMI_AUTH_NTFY_URL", raising=False)
    monkeypatch.setattr(
        notifications.httpx,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not post")),
    )

    assert notifications.notify_xiaomi_auth_expired("expired") is False


def test_xiaomi_auth_ntfy_notification_posts_once_with_cooldown(
    monkeypatch,
    tmp_path: Path,
) -> None:
    marker = tmp_path / "auth-ntfy.json"
    monkeypatch.setenv("XIAOMI_AUTH_NTFY_URL", "https://ntfy.example/xiaomi-auth")
    monkeypatch.setenv("XIAOMI_AUTH_NTFY_TOKEN", "secret-token")
    monkeypatch.setenv("XIAOMI_AUTH_NTFY_STATE_FILE", str(marker))
    monkeypatch.setenv("XIAOMI_AUTH_NTFY_COOLDOWN_SECONDS", "21600")
    seen: list[tuple[str, dict[str, object]]] = []

    def post(url: str, **kwargs):
        seen.append((url, kwargs))
        return _Response()

    monkeypatch.setattr(notifications.httpx, "post", post)

    assert notifications.notify_xiaomi_auth_expired("session expired", now=100_000) is True
    assert notifications.notify_xiaomi_auth_expired("session expired", now=100_001) is False
    assert len(seen) == 1
    url, request = seen[0]
    assert url == "https://ntfy.example/xiaomi-auth"
    assert request["content"] == "session expired"
    headers = request["headers"]
    assert headers["Authorization"] == "Bearer secret-token"
    assert headers["Title"] == "Xiaomi Health login required"
    assert headers["Priority"] == "high"
    assert marker.exists()


def test_failed_ntfy_request_does_not_consume_cooldown(
    monkeypatch,
    tmp_path: Path,
) -> None:
    marker = tmp_path / "auth-ntfy.json"
    monkeypatch.setenv("XIAOMI_AUTH_NTFY_URL", "https://ntfy.example/xiaomi-auth")
    monkeypatch.setenv("XIAOMI_AUTH_NTFY_STATE_FILE", str(marker))

    def post(*args, **kwargs):
        raise notifications.httpx.ConnectError("offline")

    monkeypatch.setattr(notifications.httpx, "post", post)

    assert notifications.notify_xiaomi_auth_expired("session expired", now=100_000) is False
    assert marker.exists() is False
