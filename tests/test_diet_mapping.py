from __future__ import annotations

import pytest

from mi_health_link.diet import normalize_diet_records


@pytest.mark.parametrize(
    ("dining", "expected"),
    [
        (1, "breakfast"),
        (2, "morning_snack"),
        (3, "lunch"),
        (4, "afternoon_snack"),
        (5, "dinner"),
        (6, "evening_snack"),
    ],
)
def test_normalize_diet_records_uses_xiaomi_dining_mapping(
    dining: int,
    expected: str,
) -> None:
    rows = normalize_diet_records(
        [
            {
                "sid": "sid-1",
                "dining": dining,
                "time": 1788250638,
                "value": '{"item_list":[{"level1":{"name":"test food","weight":1,"weight_unit":"份"}}]}',
                "zone_offset": 28800,
                "watermark": f"wm-{dining}",
            }
        ]
    )

    assert rows[0]["meal_type"] == expected
