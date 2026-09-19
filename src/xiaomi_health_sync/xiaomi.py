from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from .config import REGION_HOSTS, XiaomiCredentials, Settings
from .auth import XiaomiAuthRefreshError, refresh_session_with_pass_token
from .crypto import decrypt_response, generate_nonce, sign_payload


AUTH_KEY = "rwelJuWBFJxmbMKD"
NETWORK_ATTEMPTS = 3
NETWORK_BACKOFF_SECONDS = (0.5, 1.0)


@dataclass
class XiaomiResponse:
    status_code: int
    json_data: Any | None
    text: str
    request_id: str | None = None


class XiaomiHealthClient:
    def __init__(
        self,
        settings: Settings,
        credentials: XiaomiCredentials,
        transport: httpx.BaseTransport | None = None,
        retry_progress: Callable[[str], None] | None = None,
    ):
        self.settings = settings
        self.credentials = credentials
        self._transport = transport
        self.retry_progress = retry_progress or (
            lambda message: print(message, file=sys.stderr, flush=True)
        )
        region = credentials.region or settings.region
        try:
            host = REGION_HOSTS[region]
        except KeyError as exc:
            raise ValueError(f"Unsupported Xiaomi region: {region}") from exc
        self.base_url = f"https://{host}"
        self.client = httpx.Client(
            base_url=self.base_url,
            timeout=30.0,
            transport=transport,
            headers={
                "User-Agent": settings.user_agent,
                "Accept": "*/*",
            },
            cookies=self._cookies(),
        )

    def _cookies(self) -> dict[str, str]:
        c = {
            "serviceToken": self.credentials.service_token,
            "userId": self.credentials.user_id,
            "locale": "en",
            "auth_key": AUTH_KEY,
        }
        if self.credentials.c_user_id:
            c["cUserId"] = self.credentials.c_user_id
        return c

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "XiaomiHealthClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def get_diet_records_by_time(
        self,
        *,
        dining: int,
        limit: int,
        start_time: int,
        end_time: int,
        reverse: bool,
        next_key: str,
    ) -> XiaomiResponse:
        """Read a bounded Xiaomi diet-record page without modifying cloud data."""
        return self.encrypted_post(
            "/app/v1/data/get_diet_records_by_time",
            {
                "dining": dining,
                "limit": limit,
                "start_time": start_time,
                "end_time": end_time,
                "reverse": reverse,
                "next_key": next_key,
            },
        )

    def _refresh_with_pass_token(self) -> bool:
        pass_token = self.settings.pass_token or self.credentials.pass_token
        if not pass_token:
            return False

        self.retry_progress("[auth] Xiaomi session expired; refreshing with passToken")
        refreshed = refresh_session_with_pass_token(
            user_id=self.credentials.user_id,
            pass_token=pass_token,
            region=self.credentials.region,
            device_id=self.settings.device_id or self.credentials.device_id,
            transport=self._transport,
        )
        self.credentials = refreshed
        self.client.cookies.clear()
        self.client.cookies.update(self._cookies())
        self.retry_progress("[auth] Xiaomi session refreshed")
        return True

    def encrypted_post(self, path: str, payload: dict[str, Any]) -> XiaomiResponse:
        response = self._encrypted_post_once(path, payload)
        if response.status_code != 401 or not (self.settings.pass_token or self.credentials.pass_token):
            return response

        try:
            refreshed = self._refresh_with_pass_token()
        except XiaomiAuthRefreshError as exc:
            self.retry_progress(
                f"[auth] Xiaomi session refresh failed: {type(exc).__name__}"
            )
            return response
        except Exception as exc:
            self.retry_progress(
                f"[auth] Xiaomi session refresh failed unexpectedly: "
                f"{type(exc).__name__}"
            )
            return response

        if not refreshed:
            return response
        return self._encrypted_post_once(path, payload)

    def _encrypted_post_once(self, path: str, payload: dict[str, Any]) -> XiaomiResponse:
        plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        signed = None
        response = None

        for attempt in range(NETWORK_ATTEMPTS):
            nonce = generate_nonce()
            signed = sign_payload(
                path=path,
                plaintext=plaintext,
                ssecurity_b64=self.credentials.ssecurity,
                nonce=nonce,
            )
            form = {
                "_nonce": signed.nonce,
                "data": signed.data,
                "rc4_hash__": signed.rc4_hash__,
                "signature": signed.signature,
                "ssecurity": self.credentials.ssecurity,
            }
            try:
                response = self.client.post(path, data=form)
                break
            except httpx.TransportError as exc:
                if attempt >= NETWORK_ATTEMPTS - 1:
                    raise
                self.retry_progress(
                    f"[network] {type(exc).__name__} on {path}; "
                    f"attempt {attempt + 1}/{NETWORK_ATTEMPTS} failed, retrying"
                )
                time.sleep(NETWORK_BACKOFF_SECONDS[attempt])

        assert response is not None
        assert signed is not None

        raw_text = response.text
        decoded_text = raw_text
        parsed = None

        # Xiaomi's encrypted Health responses are usually a Base64 ciphertext.
        # Some error paths return plain JSON, so attempt both.
        try:
            decoded_text = decrypt_response(
                ciphertext_b64=raw_text.strip(),
                signed_nonce_b64=signed.signed_nonce,
            )
        except Exception:
            decoded_text = raw_text

        try:
            parsed = json.loads(decoded_text)
        except json.JSONDecodeError:
            parsed = None

        return XiaomiResponse(
            status_code=response.status_code,
            json_data=parsed,
            text=decoded_text,
            request_id=response.headers.get("x-request-id"),
        )
