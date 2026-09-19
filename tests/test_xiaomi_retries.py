from pathlib import Path
from urllib.parse import parse_qs

import httpx

from mi_health_link.config import Settings, XiaomiCredentials
from mi_health_link.xiaomi import XiaomiHealthClient


def settings():
    return Settings(
        supabase_url=None,
        supabase_service_role_key=None,
        credentials_file=Path("unused.json"),
        region="cn",
        user_agent="test-agent",
    )


def credentials():
    return XiaomiCredentials(
        user_id="1",
        c_user_id=None,
        service_token="token",
        ssecurity="MDAwMDAwMDAwMDAwMDAwMA==",
        region="cn",
    )


def test_encrypted_post_includes_ssecurity_form_field():
    captured = {}

    def handler(request: httpx.Request):
        captured.update(parse_qs(request.content.decode()))
        return httpx.Response(200, text='{"code":0,"result":{}}')

    client = XiaomiHealthClient(settings(), credentials(), transport=httpx.MockTransport(handler))
    try:
        client.encrypted_post("/healthapp/user/get_miot_user_profile", {})
    finally:
        client.close()

    assert captured["ssecurity"] == [credentials().ssecurity]
    assert "_nonce" in captured
    assert "data" in captured
    assert "rc4_hash__" in captured
    assert "signature" in captured


def test_encrypted_post_retries_transient_connect_timeout(monkeypatch):
    calls = 0
    sleeps = []
    retry_messages = []

    def handler(request: httpx.Request):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise httpx.ConnectTimeout("temporary tls timeout", request=request)
        return httpx.Response(200, text='{"code":0,"result":{"data_list":[]}}')

    monkeypatch.setattr("mi_health_link.xiaomi.time.sleep", sleeps.append)
    client = XiaomiHealthClient(
        settings(),
        credentials(),
        transport=httpx.MockTransport(handler),
        retry_progress=retry_messages.append,
    )
    try:
        response = client.encrypted_post("/app/v1/data/get_latest_fitness_data", {"params": []})
    finally:
        client.close()

    assert calls == 3
    assert response.status_code == 200
    assert response.json_data["code"] == 0
    assert sleeps == [0.5, 1.0]
    assert len(retry_messages) == 2
    assert "attempt 1/3" in retry_messages[0]
    assert "ConnectTimeout" in retry_messages[0]
    assert "attempt 2/3" in retry_messages[1]


def test_encrypted_post_does_not_retry_http_response(monkeypatch):
    calls = 0

    def handler(request: httpx.Request):
        nonlocal calls
        calls += 1
        return httpx.Response(503, text='{"code":503,"message":"busy"}')

    monkeypatch.setattr("mi_health_link.xiaomi.time.sleep", lambda _: None)
    client = XiaomiHealthClient(
        settings(),
        credentials(),
        transport=httpx.MockTransport(handler),
    )
    try:
        response = client.encrypted_post("/test", {})
    finally:
        client.close()

    assert calls == 1
    assert response.status_code == 503
