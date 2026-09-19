import httpx

from xiaomi_health_sync.supabase_store import SupabaseStore, stable_source_record_id


def test_stable_record_id_ignores_json_key_order():
    a = {"b": 2, "a": 1}
    b = {"a": 1, "b": 2}
    assert stable_source_record_id("sleep", a) == stable_source_record_id("sleep", b)


def test_record_type_is_part_of_id():
    payload = {"x": 1}
    assert stable_source_record_id("sleep", payload) != stable_source_record_id(
        "heart_rate", payload
    )


def test_delete_raw_type_deletes_only_requested_source_and_record_type():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    store = SupabaseStore(
        "https://example.supabase.co",
        "sb_secret_test",
        transport=httpx.MockTransport(handler),
    )

    store.delete_raw_type(record_type="health_record:sleep", source="xiaomi")

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "DELETE"
    assert request.url.path == "/rest/v1/raw_records"
    assert request.url.params["source"] == "eq.xiaomi"
    assert request.url.params["record_type"] == "eq.health_record:sleep"
