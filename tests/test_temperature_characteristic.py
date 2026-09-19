from __future__ import annotations

from xiaomi_health_sync.temperature_store import TemperatureAwareStore


def test_temperature_characteristic_is_written_to_health_records() -> None:
    store = TemperatureAwareStore("https://project.supabase.co", "service-role-test")
    write = store._write_for_record({
        "key": "temperature_characteristic",
        "sid": "watch",
        "time": 1_700_000_000,
        "update_time": 1_700_000_100,
        "zone_name": "Asia/Shanghai",
        "zone_offset": 28800,
        "value": {"example": 1},
        "metrics": {},
    })

    assert write is not None
    table, conflict, body = write
    assert table == "health_records"
    assert conflict == "source,key,source_record_id"
    assert body["key"] == "temperature_characteristic"
    assert body["value"] == {"example": 1}
