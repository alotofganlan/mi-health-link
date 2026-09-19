from pathlib import Path


MIGRATION = Path("migrations/20260916_add_morning_heart_rate_daily.sql")


def test_daily_heart_rate_function_is_private_and_security_invoker():
    sql = " ".join(MIGRATION.read_text().split()).lower()

    assert "get_morning_heart_rate_daily" in sql
    assert "security invoker" in sql
    assert "group by" in sql
    assert "revoke all" in sql
    assert "from public, anon, authenticated" in sql
    assert "grant execute" in sql
    assert "to service_role" in sql
