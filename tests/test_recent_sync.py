from dataclasses import dataclass

from xiaomi_health_sync.recent_sync import run_recent_sync


@dataclass
class _HealthResult:
    discovered_keys: list[str]
    new_keys: list[str]


def _runners(calls):
    class HealthRunner:
        def __init__(self, **kwargs):
            calls.append(("health_init", kwargs))

        def sync_recent(self, **kwargs):
            calls.append(("health", kwargs))
            callback = kwargs.get("on_checked_range")
            if callback is not None:
                callback("sleep", 100, 200)
            return _HealthResult(["sleep"], [])

    class DietRunner:
        def __init__(self, **kwargs):
            calls.append(("diet_init", kwargs))

        def sync_window(self, **kwargs):
            calls.append(("diet", kwargs))
            return {"meals": 1}

    class WorkoutRunner:
        def __init__(self, **kwargs):
            calls.append(("workout_init", kwargs))

        def sync_window(self, **kwargs):
            calls.append(("workout", kwargs))
            return {"records": 2}

    return HealthRunner, DietRunner, WorkoutRunner


def test_all_recent_sources_share_one_window_and_callbacks() -> None:
    calls = []
    checked = []
    health, diet, workout = _runners(calls)

    result = run_recent_sync(
        client=object(),
        store=object(),
        recent_window_seconds=100,
        latest_limit=7,
        now_seconds=200,
        on_checked_range=lambda *args: checked.append(args),
        health_runner_class=health,
        diet_runner_class=diet,
        workout_runner_class=workout,
    )

    health_call = next(value for name, value in calls if name == "health")
    assert health_call["selected_keys"] is None
    assert health_call["recent_window_seconds"] == 100
    assert health_call["latest_limit"] == 7
    assert next(value for name, value in calls if name == "diet") == {
        "start_time": 100,
        "end_time": 200,
    }
    assert next(value for name, value in calls if name == "workout") == {
        "start_time": 100,
        "end_time": 200,
    }
    assert checked == [
        ("sleep", 100, 200),
        ("workout", 100, 200),
        ("diet", 100, 200),
    ]
    assert result.health.discovered_keys == ["sleep"]
    assert result.diet == {"meals": 1}
    assert result.workout == {"records": 2}


def test_targeted_diet_skips_health_and_workout() -> None:
    calls = []
    checked = []
    health, diet, workout = _runners(calls)

    result = run_recent_sync(
        client=object(),
        store=object(),
        recent_window_seconds=100,
        selected_keys={"diet"},
        now_seconds=200,
        on_checked_range=lambda *args: checked.append(args),
        health_runner_class=health,
        diet_runner_class=diet,
        workout_runner_class=workout,
    )

    assert [name for name, _ in calls] == ["diet_init", "diet"]
    assert checked == [("diet", 100, 200)]
    assert result.health is None
    assert result.workout is None
    assert result.diet == {"meals": 1}
