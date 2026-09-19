from __future__ import annotations

from mi_health_link.xiaomi import XiaomiHealthClient, XiaomiResponse


def test_get_diet_records_by_time_uses_read_only_diet_endpoint(monkeypatch):
    client = object.__new__(XiaomiHealthClient)
    calls = []

    def fake_encrypted_post(path, payload):
        calls.append((path, payload))
        return XiaomiResponse(status_code=200, json_data={"code": 0}, text='{"code":0}')

    monkeypatch.setattr(client, "encrypted_post", fake_encrypted_post)

    response = client.get_diet_records_by_time(
        dining=0,
        limit=100,
        start_time=1_777_219_200_000,
        end_time=1_777_305_600_000,
        reverse=False,
        next_key="",
    )

    assert response.status_code == 200
    assert calls == [
        (
            "/app/v1/data/get_diet_records_by_time",
            {
                "dining": 0,
                "limit": 100,
                "start_time": 1_777_219_200_000,
                "end_time": 1_777_305_600_000,
                "reverse": False,
                "next_key": "",
            },
        )
    ]
