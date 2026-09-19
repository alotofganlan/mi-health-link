from __future__ import annotations

import json
import os
from pathlib import Path
import time

import httpx


DEFAULT_COOLDOWN_SECONDS = 6 * 60 * 60
XIAOMI_AUTH_EXPIRED_MESSAGE = (
    "Xiaomi Cloud session expired. Open Mi Fitness and sign in again, "
    "then update the VPS Xiaomi credentials."
)
DEFAULT_STATE_FILE = (
    Path.home() / ".local" / "state" / "mi-health-link" / "xiaomi-auth-ntfy.json"
)


def _positive_int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _last_sent_at(path: Path) -> float | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        value = payload.get("sent_at")
        return float(value) if value is not None else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _remember_sent(path: Path, sent_at: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps({"sent_at": sent_at}, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)


def notify_xiaomi_auth_expired(
    message: str,
    *,
    now: float | None = None,
) -> bool:
    """Send one optional, rate-limited ntfy alert without masking sync failures."""
    url = (os.getenv("XIAOMI_AUTH_NTFY_URL") or "").strip()
    if not url:
        return False

    current_time = time.time() if now is None else float(now)
    state_file = Path(
        (os.getenv("XIAOMI_AUTH_NTFY_STATE_FILE") or "").strip()
        or DEFAULT_STATE_FILE
    ).expanduser()
    cooldown = _positive_int_env(
        "XIAOMI_AUTH_NTFY_COOLDOWN_SECONDS",
        DEFAULT_COOLDOWN_SECONDS,
    )
    last_sent = _last_sent_at(state_file)
    if last_sent is not None and current_time - last_sent < cooldown:
        return False

    headers = {
        "Title": "Xiaomi Health login required",
        "Priority": "high",
        "Tags": "warning,xiaomi",
    }
    token = (os.getenv("XIAOMI_AUTH_NTFY_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        response = httpx.post(
            url,
            content=str(message),
            headers=headers,
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return False

    try:
        _remember_sent(state_file, current_time)
    except OSError:
        # Preserve the original authentication failure after successful delivery.
        pass
    return True
