from pathlib import Path


MIGRATION = Path("migrations/20260917_add_location_district.sql")


def test_location_district_migration_is_additive_and_idempotent() -> None:
    sql = " ".join(MIGRATION.read_text().split()).lower()

    assert sql.startswith("begin;") and sql.endswith("commit;")
    assert "add column if not exists district text" in sql
