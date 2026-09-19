from mi_health_link.supabase_store import SupabaseStore


def test_modern_secret_key_uses_apikey_only():
    store = SupabaseStore("https://example.supabase.co", "sb_secret_test")
    headers = store._headers()
    assert headers["apikey"] == "sb_secret_test"
    assert "Authorization" not in headers


def test_legacy_service_role_keeps_bearer_header():
    key = "eyJhbGciOiJIUzI1NiJ9.legacy"
    store = SupabaseStore("https://example.supabase.co", key)
    headers = store._headers()
    assert headers["apikey"] == key
    assert headers["Authorization"] == f"Bearer {key}"
