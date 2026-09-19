from pathlib import Path


MIGRATION = Path("migrations/20260915_add_morning_context_snapshots.sql")


def test_snapshot_migration_is_private_and_one_per_report():
    sql = MIGRATION.read_text()

    assert "report_id uuid primary key" in sql
    assert "references public.wake_report_deliveries(id)" in sql
    assert "enable row level security" in sql
    assert (
        "revoke all on table public.morning_context_snapshots "
        "from anon, authenticated, service_role;"
    ) in " ".join(sql.split())
    assert "service_role" in sql
    assert "snapshot_mode in ('native', 'legacy_hydrated')" in sql
