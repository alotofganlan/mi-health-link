from pathlib import Path


def test_split_endpoint_migration_persists_unlock_and_deduplicates_sleep_id() -> None:
    sql = Path("migrations/20260914_split_wake_endpoints.sql").read_text()

    assert sql.startswith("begin;")
    assert sql.rstrip().endswith("commit;")
    assert "create table if not exists public.device_wake_state" in sql
    assert "last_unlock_at timestamptz not null" in sql
    assert "greatest(old.last_unlock_at, new.last_unlock_at)" in sql
    assert "partition by device, sleep_source_record_id" in sql
    assert "'location_update'" in sql
    assert "'nap'" in sql
    assert "unique (device, sleep_source_record_id)" in sql
    assert "service_role" in sql
    assert "anon, authenticated" in sql
