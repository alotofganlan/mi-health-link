from pathlib import Path
import re


INITIAL_SCHEMA = Path("migrations/00000000_initial_schema.sql")
BOOTSTRAP_SCHEMA = Path("migrations/00000000_bootstrap.sql")
HARDENING_MIGRATION = Path(
    "migrations/20260917_harden_data_api_privileges.sql"
)

BASE_TABLES = {
    "raw_records",
    "health_records",
    "heart_rate_samples",
    "spo2_samples",
    "intensity_samples",
    "body_measurements",
    "menstrual_records",
    "sleep_sessions",
    "sleep_stages",
    "workouts",
    "xiaomi_sync_state",
    "xiaomi_sync_jobs",
}


def _normalized(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split()).lower()


def test_initial_schema_creates_every_base_table_used_by_current_code() -> None:
    sql = _normalized(INITIAL_SCHEMA)
    created = set(
        re.findall(r"create table if not exists public\.([a-z0-9_]+)", sql)
    )

    assert created == BASE_TABLES
    assert "unique (source, record_type, source_record_id)" in sql
    assert "unique (source, key, source_record_id)" in sql
    assert "unique (source, measured_at)" in sql
    assert "unique (source, source_record_id, measured_at)" in sql
    assert "primary key (source, key)" in sql
    assert "job_id text primary key" in sql


def test_bootstrap_schema_contains_all_tables_and_one_transaction() -> None:
    sql = BOOTSTRAP_SCHEMA.read_text(encoding="utf-8")
    tables = set(re.findall(r"create table if not exists public\.([a-z0-9_]+)", sql))
    assert BASE_TABLES | {
        "glucose_samples", "xiaomi_coverage_ranges", "diet_meals",
        "diet_food_items", "device_presence", "wake_report_deliveries",
        "device_wake_state", "morning_context_snapshots",
    } <= tables
    assert sql.count("begin;") == 1
    assert sql.count("commit;") == 1
    assert "create or replace function public.get_morning_heart_rate_daily" in sql

def test_initial_schema_keeps_base_tables_server_only() -> None:
    sql = _normalized(INITIAL_SCHEMA)

    for table in BASE_TABLES:
        assert f"alter table public.{table} enable row level security" in sql
        assert f"revoke all on table public.{table} from anon, authenticated" in sql
        assert (
            f"grant select, insert, update, delete on table public.{table} "
            "to service_role"
        ) in sql


def test_final_hardening_covers_every_migrated_table() -> None:
    sql = _normalized(HARDENING_MIGRATION)
    protected_tables = BASE_TABLES | {
        "glucose_samples",
        "xiaomi_coverage_ranges",
        "diet_meals",
        "diet_food_items",
        "device_presence",
        "wake_report_deliveries",
        "device_wake_state",
        "morning_context_snapshots",
    }

    for table in protected_tables:
        assert f"alter table public.{table} enable row level security" in sql
        assert f"revoke all on table public.{table} from anon, authenticated" in sql
    assert "grant select, insert on table public.morning_context_snapshots" in sql


def test_final_hardening_tolerates_tables_without_id_sequences() -> None:
    sql = _normalized(HARDENING_MIGRATION)

    assert "to_regclass('public.' || sequence_name) is not null" in sql
    assert "execute format(" in sql
    assert "revoke all on sequence public.%i from anon, authenticated" in sql
    assert "grant usage, select on sequence public.%i to service_role" in sql
