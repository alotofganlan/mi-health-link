from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os

from dotenv import load_dotenv


REGION_HOSTS = {
    "cn": "hlth.io.mi.com",
    "sg": "sg.hlth.io.mi.com",
    "us": "us.hlth.io.mi.com",
    "de": "de.hlth.io.mi.com",
    "ru": "ru.hlth.io.mi.com",
    "i2": "i2.hlth.io.mi.com",
}

DEFAULT_USER_AGENT = (
    "APP/com.xiaomi.miwatch.pro APPV/3.49.1 "
    "iosPassportSDK/4.2.64 iOS/18.7.8"
)


@dataclass(frozen=True)
class XiaomiCredentials:
    user_id: str
    c_user_id: str | None
    service_token: str
    ssecurity: str
    region: str = "cn"
    pass_token: str | None = None
    device_id: str | None = None


@dataclass(frozen=True)
class Settings:
    supabase_url: str | None
    supabase_service_role_key: str | None
    credentials_file: Path
    region: str
    user_agent: str
    pass_token: str | None = None
    device_id: str | None = None

    @property
    def health_host(self) -> str:
        try:
            return REGION_HOSTS[self.region]
        except KeyError as exc:
            raise ValueError(f"Unsupported Xiaomi region: {self.region}") from exc


def load_settings() -> Settings:
    load_dotenv()
    region = os.getenv("XIAOMI_REGION", "cn").strip().lower()
    return Settings(
        supabase_url=(os.getenv("SUPABASE_URL") or "").rstrip("/") or None,
        supabase_service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY") or None,
        credentials_file=Path(
            os.getenv("XIAOMI_CREDENTIALS_FILE", "./xiaomi-credentials.json")
        ),
        region=region,
        user_agent=os.getenv("XIAOMI_USER_AGENT") or DEFAULT_USER_AGENT,
        pass_token=(os.getenv("XIAOMI_PASS_TOKEN") or "").strip() or None,
        device_id=(os.getenv("XIAOMI_DEVICE_ID") or "").strip() or None,
    )


def load_credentials(path: Path, default_region: str = "cn") -> XiaomiCredentials:
    raw = json.loads(path.read_text(encoding="utf-8"))
    required = ["user_id", "service_token", "ssecurity"]
    missing = [k for k in required if not raw.get(k)]
    if missing:
        raise ValueError(f"Credentials missing fields: {', '.join(missing)}")
    return XiaomiCredentials(
        user_id=str(raw["user_id"]),
        c_user_id=(str(raw["c_user_id"]) if raw.get("c_user_id") else None),
        service_token=str(raw["service_token"]),
        ssecurity=str(raw["ssecurity"]),
        region=str(raw.get("region") or default_region).lower(),
        pass_token=(str(raw["pass_token"]) if raw.get("pass_token") else None),
        device_id=(str(raw["device_id"]) if raw.get("device_id") else None),
    )
