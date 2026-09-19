from __future__ import annotations

from typing import Any

from .xiaomi import XiaomiResponse


def diagnostic_response_payload(response: XiaomiResponse) -> dict[str, Any]:
    """Format a Xiaomi response for transient/explicit diagnostic output."""
    return {
        "status_code": response.status_code,
        "request_id": response.request_id,
        "response": response.json_data if response.json_data is not None else response.text,
    }
