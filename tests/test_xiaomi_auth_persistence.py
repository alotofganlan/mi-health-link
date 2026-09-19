from __future__ import annotations

import json

import httpx

from mi_health_link.auth import import_credentials, refresh_session_with_pass_token
from mi_health_link.config import load_credentials


def test_import_auth_preserves_account_session_for_future_refresh(tmp_path) -> None:
    source = tmp_path / "auth.json"
    output = tmp_path / "xiaomi-credentials.json"
    source.write_text(
        json.dumps(
            {
                "userId": "12345",
                "serviceToken": "service-token",
                "ssecurity": "c2VjdXJpdHk=",
                "passToken": "account-pass-token",
                "deviceId": "device-1",
            }
        ),
        encoding="utf-8",
    )

    import_credentials(source, output)
    credentials = load_credentials(output)

    assert credentials.pass_token == "account-pass-token"
    assert credentials.device_id == "device-1"


def test_refresh_keeps_account_pass_token_in_rotated_credentials() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "account.xiaomi.com":
            return httpx.Response(
                200,
                text="&&&START&&&"
                + json.dumps(
                    {
                        "code": 0,
                        "ssecurity": "ZnJlc2gtc3NlY3VyaXR5",
                        "nonce": 123,
                        "userId": "12345",
                        "location": "https://sts-hlth.io.mi.com/healthapp/sts",
                    }
                ),
            )
        if request.url.host == "sts-hlth.io.mi.com":
            return httpx.Response(
                200,
                headers={"set-cookie": "serviceToken=fresh-service-token; Path=/"},
            )
        raise AssertionError(request.url)

    credentials = refresh_session_with_pass_token(
        user_id="12345",
        pass_token="account-pass-token",
        region="cn",
        device_id="device-1",
        transport=httpx.MockTransport(handler),
    )

    assert credentials.pass_token == "account-pass-token"
    assert credentials.device_id == "device-1"


def test_interactive_login_persists_pass_token_returned_in_json(tmp_path, monkeypatch) -> None:
    from mi_health_link import auth

    class FakeResponse:
        def __init__(self, text: str, status_code: int = 200) -> None:
            self.text = text
            self.status_code = status_code
            self.cookies = httpx.Cookies()

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(
                    "request failed",
                    request=httpx.Request("GET", "https://example.test"),
                    response=httpx.Response(self.status_code),
                )

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            self.cookies = httpx.Cookies()

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def get(self, path, **kwargs):
            if path == "/pass/serviceLogin":
                return FakeResponse(
                    "&&&START&&&"
                    + json.dumps(
                        {
                            "code": 0,
                            "callback": "",
                            "sid": "miothealth",
                            "qs": "?sid=miothealth",
                            "_sign": "sign",
                        }
                    )
                )
            self.cookies.set("serviceToken", "fresh-service-token")
            return FakeResponse("", 200)

        def post(self, path, **kwargs):
            return FakeResponse(
                "&&&START&&&"
                + json.dumps(
                    {
                        "code": 0,
                        "ssecurity": "ZnJlc2gtc3NlY3VyaXR5",
                        "nonce": 123,
                        "userId": "12345",
                        "cUserId": "c-user",
                        "passToken": "json-pass-token",
                        "location": "https://sts-hlth.io.mi.com/healthapp/sts",
                    }
                )
            )

    monkeypatch.setattr(auth.httpx, "Client", FakeClient)
    output = tmp_path / "credentials.json"

    auth.login_interactive(
        username="user@example.com",
        password="password",
        region="cn",
        output=output,
    )

    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["pass_token"] == "json-pass-token"
