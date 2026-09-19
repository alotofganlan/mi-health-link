from __future__ import annotations

import json

import httpx

from xiaomi_health_sync import auth
from xiaomi_health_sync.config import Settings, XiaomiCredentials, load_settings
from xiaomi_health_sync.xiaomi import XiaomiHealthClient


def _session_payload() -> str:
    return "&&&START&&&" + json.dumps(
        {
            "code": 0,
            "ssecurity": "ZnJlc2gtc3NlY3VyaXR5",
            "nonce": 123,
            "userId": "12345",
            "cUserId": "c-user",
            "location": "https://sts-hlth.io.mi.com/healthapp/sts?foo=1",
        }
    )


def _settings(*, pass_token: str | None = "pass-token") -> Settings:
    return Settings(
        supabase_url=None,
        supabase_service_role_key=None,
        credentials_file=__import__("pathlib").Path("unused.json"),
        region="cn",
        user_agent="test-agent",
        pass_token=pass_token,
        device_id="test-device",
    )


def _credentials() -> XiaomiCredentials:
    return XiaomiCredentials(
        user_id="12345",
        c_user_id="old-c-user",
        service_token="old-service-token",
        ssecurity="b2xkLXNzZWN1cml0eQ==",
        region="cn",
    )


def test_pass_token_exchange_returns_a_fresh_health_session() -> None:
    refresh = getattr(auth, "refresh_session_with_pass_token", None)
    assert callable(refresh)

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "account.xiaomi.com":
            assert request.url.path == "/pass/serviceLogin"
            assert request.headers["cookie"].find("userId=12345") >= 0
            assert request.headers["cookie"].find("passToken=pass-token") >= 0
            return httpx.Response(200, text=_session_payload())
        if request.url.host == "sts-hlth.io.mi.com":
            assert request.url.path == "/healthapp/sts"
            return httpx.Response(
                200,
                headers={"set-cookie": "serviceToken=fresh-service-token; Path=/"},
            )
        raise AssertionError(f"unexpected URL: {request.url}")

    credentials = refresh(
        user_id="12345",
        pass_token="pass-token",
        region="cn",
        device_id="test-device",
        transport=httpx.MockTransport(handler),
    )

    assert credentials.user_id == "12345"
    assert credentials.c_user_id == "c-user"
    assert credentials.service_token == "fresh-service-token"
    assert credentials.ssecurity == "ZnJlc2gtc3NlY3VyaXR5"
    assert credentials.region == "cn"
    assert [request.url.host for request in requests] == [
        "account.xiaomi.com",
        "sts-hlth.io.mi.com",
    ]


def test_health_request_refreshes_once_after_xiaomi_401() -> None:
    requests: list[httpx.Request] = []
    health_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal health_calls
        requests.append(request)
        if request.url.host == "hlth.io.mi.com":
            health_calls += 1
            if health_calls == 1:
                return httpx.Response(401, text='{"code":401}')
            return httpx.Response(200, text='{"code":0,"result":{"ok":true}}')
        if request.url.host == "account.xiaomi.com":
            return httpx.Response(200, text=_session_payload())
        if request.url.host == "sts-hlth.io.mi.com":
            return httpx.Response(
                200,
                headers={"set-cookie": "serviceToken=fresh-service-token; Path=/"},
            )
        raise AssertionError(f"unexpected URL: {request.url}")

    client = XiaomiHealthClient(
        _settings(),
        _credentials(),
        transport=httpx.MockTransport(handler),
    )
    try:
        response = client.encrypted_post("/app/v1/data/get_latest_fitness_data", {})
    finally:
        client.close()

    assert response.status_code == 200
    assert response.json_data == {"code": 0, "result": {"ok": True}}
    assert health_calls == 2
    assert client.credentials.service_token == "fresh-service-token"
    assert [request.url.host for request in requests] == [
        "hlth.io.mi.com",
        "account.xiaomi.com",
        "sts-hlth.io.mi.com",
        "hlth.io.mi.com",
    ]


def test_failed_refresh_keeps_original_session_and_returns_original_401() -> None:
    health_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal health_calls
        if request.url.host == "hlth.io.mi.com":
            health_calls += 1
            return httpx.Response(401, text='{"code":401}')
        if request.url.host == "account.xiaomi.com":
            return httpx.Response(200, text='{"code":70002,"description":"passToken expired"}')
        raise AssertionError(f"unexpected URL: {request.url}")

    client = XiaomiHealthClient(
        _settings(),
        _credentials(),
        transport=httpx.MockTransport(handler),
    )
    try:
        response = client.encrypted_post("/test", {})
        current = client.credentials
    finally:
        client.close()

    assert response.status_code == 401
    assert health_calls == 1
    assert current.service_token == "old-service-token"
    assert current.ssecurity == "b2xkLXNzZWN1cml0eQ=="


def test_load_settings_reads_optional_pass_token_without_exposing_it(
    monkeypatch,
) -> None:
    monkeypatch.setenv("XIAOMI_PASS_TOKEN", "secret-pass-token")
    monkeypatch.setenv("XIAOMI_DEVICE_ID", "device-from-env")

    settings = load_settings()

    assert settings.pass_token == "secret-pass-token"
    assert settings.device_id == "device-from-env"
