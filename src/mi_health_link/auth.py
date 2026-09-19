from __future__ import annotations

import base64
import getpass
import hashlib
import json
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse, parse_qsl, urlencode, urlunparse

import httpx

from .config import XiaomiCredentials


ACCOUNT = "https://account.xiaomi.com"
DEFAULT_SID = "miothealth"
DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124 Safari/537.36"
)


class XiaomiLoginError(RuntimeError):
    pass


class XiaomiAuthRefreshError(XiaomiLoginError):
    """Raised when an account passToken cannot mint a Xiaomi Health session."""


PASS_TOKEN_SID = "miothealth"
STS_HEALTH_URL = "https://sts-hlth.io.mi.com/healthapp/sts"


def parse_xiaomi_json(text: str) -> dict[str, Any]:
    marker = "&&&START&&&"
    text = text.strip()
    if text.startswith(marker):
        text = text[len(marker):]
    value = json.loads(text)
    if not isinstance(value, dict):
        raise XiaomiLoginError("Xiaomi Passport returned non-object JSON")
    return value


def make_client_sign(nonce: Any, ssecurity: str) -> str:
    material = f"nonce={nonce}&{ssecurity}".encode("utf-8")
    return base64.b64encode(hashlib.sha1(material).digest()).decode("ascii")


def append_query(url: str, **params: str) -> str:
    parts = urlparse(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(params)
    return urlunparse(parts._replace(query=urlencode(query)))


def login_interactive(
    *,
    username: str | None,
    password: str | None,
    region: str,
    sid: str = DEFAULT_SID,
    output: Path,
) -> XiaomiCredentials:
    """
    Minimal Xiaomi Passport flow for accounts that can complete password auth
    directly. Captcha/notification challenges are surfaced to the user rather
    than bypassed.
    """
    username = username or input("Xiaomi account email/phone: ").strip()
    password = password or getpass.getpass("Xiaomi password: ")

    if not username or not password:
        raise XiaomiLoginError("Username and password are required")

    device_id = hashlib.md5(username.encode("utf-8")).hexdigest()[:16]
    cookies = {"sdkVersion": "3.8.6", "deviceId": device_id}

    with httpx.Client(
        base_url=ACCOUNT,
        timeout=30.0,
        headers={"User-Agent": DEFAULT_UA},
        cookies=cookies,
        follow_redirects=False,
    ) as client:
        step1 = client.get(
            "/pass/serviceLogin",
            params={"sid": sid, "_json": "true"},
        )
        step1.raise_for_status()
        first = parse_xiaomi_json(step1.text)

        payload = {
            "user": username,
            "hash": hashlib.md5(password.encode("utf-8")).hexdigest().upper(),
            "callback": first.get("callback", ""),
            "sid": first.get("sid") or sid,
            "qs": first.get("qs", ""),
            "_sign": first.get("_sign", ""),
            "_json": "true",
        }

        step2 = client.post(
            "/pass/serviceLoginAuth2",
            data=payload,
            params={"_json": "true"},
        )
        step2.raise_for_status()
        auth = parse_xiaomi_json(step2.text)

        captcha_url = auth.get("captchaUrl")
        if captcha_url:
            full = urljoin(ACCOUNT, captcha_url)
            raise XiaomiLoginError(
                "Xiaomi requires a captcha. Open this URL in a browser, "
                f"complete the challenge, then retry login:\n{full}"
            )

        notification_url = auth.get("notificationUrl")
        if notification_url:
            raise XiaomiLoginError(
                "Xiaomi requires additional account verification. Open this URL, "
                "approve the login, then retry:\n"
                f"{notification_url}"
            )

        location = auth.get("location")
        ssecurity = auth.get("ssecurity")
        nonce = auth.get("nonce")
        user_id = auth.get("userId")
        c_user_id = auth.get("cUserId")

        if not (location and ssecurity and nonce and user_id):
            description = auth.get("description") or auth.get("message") or auth.get("code")
            raise XiaomiLoginError(
                f"Xiaomi login did not return a usable session: {description!r}"
            )

        client_sign = make_client_sign(nonce, ssecurity)
        sts_url = append_query(
            location,
            _userIdNeedEncrypt="true",
            clientSign=client_sign,
        )
        sts = client.get(sts_url)
        if sts.status_code not in (200, 302):
            raise XiaomiLoginError(f"STS exchange failed with HTTP {sts.status_code}")

        # httpx cookie jar includes cookies collected across redirects/hosts.
        service_token = client.cookies.get("serviceToken")
        if not service_token:
            # Some STS responses expose it directly on the response cookie jar.
            service_token = sts.cookies.get("serviceToken")
        if not service_token:
            raise XiaomiLoginError(
                "Login succeeded but no serviceToken was returned. "
                "The account may require a different Xiaomi service SID."
            )

        pass_token = (
            auth.get("passToken")
            or _cookie_value(client.cookies, "passToken")
            or _cookie_value(sts.cookies, "passToken")
        )
        creds = XiaomiCredentials(
            user_id=str(user_id),
            c_user_id=(str(c_user_id) if c_user_id else None),
            service_token=str(service_token),
            ssecurity=str(ssecurity),
            region=region,
            pass_token=pass_token,
            device_id=device_id,
        )

    output.write_text(
        json.dumps(
            {
                "user_id": creds.user_id,
                "c_user_id": creds.c_user_id,
                "service_token": creds.service_token,
                "ssecurity": creds.ssecurity,
                "region": creds.region,
                "pass_token": creds.pass_token,
                "device_id": creds.device_id,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    try:
        output.chmod(0o600)
    except OSError:
        pass
    return creds



def _cookie_value(cookies: httpx.Cookies, name: str) -> str | None:
    for cookie in cookies.jar:
        if cookie.name == name and cookie.value:
            return str(cookie.value)
    return None


def _random_device_id() -> str:
    return f"an_{secrets.token_hex(16)}"


def refresh_session_with_pass_token(
    *,
    user_id: str,
    pass_token: str,
    region: str = "cn",
    device_id: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> XiaomiCredentials:
    """Exchange a Xiaomi account passToken for a fresh miothealth session."""
    user_id = str(user_id).strip()
    pass_token = str(pass_token)
    if not user_id or not pass_token:
        raise XiaomiAuthRefreshError(
            "Xiaomi user_id and passToken are required for session refresh"
        )

    device_id = str(device_id or "").strip() or _random_device_id()
    cookies = {
        "sdkVersion": "3.9",
        "userId": user_id,
        "passToken": pass_token,
        "deviceId": device_id,
    }

    try:
        with httpx.Client(
            base_url=ACCOUNT,
            timeout=30.0,
            headers={"User-Agent": DEFAULT_UA},
            cookies=cookies,
            follow_redirects=False,
            transport=transport,
        ) as client:
            step1 = client.get(
                "/pass/serviceLogin",
                params={"sid": PASS_TOKEN_SID, "_json": "true"},
            )
            if step1.status_code in (401, 403):
                raise XiaomiAuthRefreshError(
                    "Xiaomi passToken is invalid or expired"
                )
            step1.raise_for_status()
            first = parse_xiaomi_json(step1.text)
            if first.get("code") not in (None, 0):
                raise XiaomiAuthRefreshError(
                    f"Xiaomi serviceLogin rejected passToken: "
                    f"{first.get('description') or first.get('message') or first.get('code')}"
                )

            data = first
            if not (data.get("ssecurity") and data.get("userId") and data.get("location")):
                if not (data.get("_sign") and data.get("callback") and data.get("qs")):
                    raise XiaomiAuthRefreshError(
                        "Xiaomi serviceLogin response did not contain refresh parameters"
                    )
                auth_response = client.post(
                    "/pass/serviceLoginAuth2",
                    data={
                        "user": user_id,
                        "sid": PASS_TOKEN_SID,
                        "_json": "true",
                        "_parset": "true",
                        "callback": data["callback"],
                        "_sign": data["_sign"],
                        "qs": data["qs"],
                    },
                    params={"_json": "true"},
                )
                if auth_response.status_code in (401, 403):
                    raise XiaomiAuthRefreshError(
                        "Xiaomi passToken is invalid or expired"
                    )
                auth_response.raise_for_status()
                data = parse_xiaomi_json(auth_response.text)

            if data.get("code") not in (None, 0):
                raise XiaomiAuthRefreshError(
                    f"Xiaomi serviceLoginAuth2 rejected passToken: "
                    f"{data.get('description') or data.get('message') or data.get('code')}"
                )

            session_user_id = data.get("userId")
            ssecurity = data.get("ssecurity")
            location = data.get("location")
            nonce = data.get("nonce")
            if not (session_user_id and ssecurity and location and nonce is not None):
                raise XiaomiAuthRefreshError(
                    "Xiaomi session response is missing ssecurity, nonce, userId or location"
                )

            sts_url = append_query(
                str(location),
                _userIdNeedEncrypt="true",
                clientSign=make_client_sign(nonce, str(ssecurity)),
            )
            sts = client.get(sts_url)
            if sts.status_code not in (200, 302):
                raise XiaomiAuthRefreshError(
                    f"Xiaomi STS exchange failed with HTTP {sts.status_code}"
                )

            service_token = _cookie_value(client.cookies, "serviceToken")
            if not service_token:
                service_token = _cookie_value(sts.cookies, "serviceToken")
            if not service_token:
                raise XiaomiAuthRefreshError(
                    "Xiaomi STS exchange returned no serviceToken"
                )

            rotated_pass_token = _cookie_value(client.cookies, "passToken") or pass_token
            return XiaomiCredentials(
                user_id=str(session_user_id),
                c_user_id=(
                    str(data["cUserId"]) if data.get("cUserId") else None
                ),
                service_token=service_token,
                ssecurity=str(ssecurity),
                region=str(region).lower(),
                pass_token=rotated_pass_token,
                device_id=device_id,
            )
    except XiaomiAuthRefreshError:
        raise
    except (httpx.HTTPError, XiaomiLoginError) as exc:
        raise XiaomiAuthRefreshError(
            f"Xiaomi session refresh failed: {type(exc).__name__}"
        ) from exc

def import_credentials(source: Path, output: Path, region: str = "cn") -> XiaomiCredentials:
    """
    Import common Xiaomi auth-state JSON shapes without exposing the secrets.
    Accepts snake_case and camelCase field names.
    """
    raw = json.loads(source.read_text(encoding="utf-8"))

    # Some tools wrap auth state one level down.
    candidates = [raw]
    for key in ("auth", "xiaomi", "credentials", "session"):
        if isinstance(raw.get(key), dict):
            candidates.append(raw[key])

    def pick(*names: str):
        for candidate in candidates:
            for name in names:
                if candidate.get(name) not in (None, ""):
                    return candidate[name]
        return None

    user_id = pick("user_id", "userId")
    c_user_id = pick("c_user_id", "cUserId")
    service_token = pick("service_token", "serviceToken")
    ssecurity = pick("ssecurity")
    pass_token = pick("pass_token", "passToken")
    device_id = pick("device_id", "deviceId")
    found_region = pick("region", "server", "country") or region

    if not (user_id and service_token and ssecurity):
        raise XiaomiLoginError(
            "Could not find user_id/userId, service_token/serviceToken and ssecurity "
            "in the auth-state JSON."
        )

    creds = XiaomiCredentials(
        user_id=str(user_id),
        c_user_id=str(c_user_id) if c_user_id else None,
        service_token=str(service_token),
        ssecurity=str(ssecurity),
        region=str(found_region).lower(),
        pass_token=str(pass_token) if pass_token else None,
        device_id=str(device_id) if device_id else None,
    )
    output.write_text(
        json.dumps(
            {
                "user_id": creds.user_id,
                "c_user_id": creds.c_user_id,
                "service_token": creds.service_token,
                "ssecurity": creds.ssecurity,
                "region": creds.region,
                "pass_token": creds.pass_token,
                "device_id": creds.device_id,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    try:
        output.chmod(0o600)
    except OSError:
        pass
    return creds
