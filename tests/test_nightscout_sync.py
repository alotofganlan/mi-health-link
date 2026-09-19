from datetime import datetime, timezone

import pytest

from mi_health_link.nightscout_sync import (
    NightscoutEntryError,
    build_entries_params,
    parse_entry,
)


def test_parse_entry_normalizes_nightscout_glucose_fields():
    sample = parse_entry({
        "_id": "abc123",
        "sgv": 108,
        "date": 1_777_000_000_000,
        "direction": "Flat",
    })

    assert sample == {
        "source": "nightscout",
        "source_record_id": "abc123",
        "measured_at": datetime.fromtimestamp(
            1_777_000_000, tz=timezone.utc
        ).isoformat(),
        "glucose_mg_dl": 108,
        "glucose_mmol_l": 6.0,
        "direction": "Flat",
    }


def test_parse_entry_uses_timestamp_fallback_id_when_nightscout_id_missing():
    sample = parse_entry({"sgv": 90, "date": 1_777_000_001_000})
    assert sample["source_record_id"] == "date:1777000001000"
    assert sample["direction"] is None


def test_parse_entry_rejects_missing_or_nonpositive_glucose():
    with pytest.raises(NightscoutEntryError):
        parse_entry({"date": 1_777_000_000_000})
    with pytest.raises(NightscoutEntryError):
        parse_entry({"date": 1_777_000_000_000, "sgv": 0})


def test_build_entries_params_supports_incremental_and_backward_paging():
    params = build_entries_params(
        since_ms=1_700_000_000_000,
        before_ms=1_800_000_000_000,
        count=1000,
        token="reader-token",
    )

    assert params == {
        "count": 1000,
        "find[date][$gt]": 1_700_000_000_000,
        "find[date][$lt]": 1_800_000_000_000,
        "token": "reader-token",
    }


def test_build_entries_params_omits_optional_filters_for_initial_page():
    assert build_entries_params(count=500) == {"count": 500}
