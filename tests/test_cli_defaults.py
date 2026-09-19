from mi_health_link.cli import build_parser


def test_discover_uses_per_key_history_policy_by_default():
    args = build_parser().parse_args(["discover"])
    assert args.backfill_start_time is None
    assert args.history_window_days == 30


def test_probe_raw_persistence_is_explicit_opt_in():
    args = build_parser().parse_args([
        "probe",
        "--path", "/example",
        "--payload", "{}",
        "--record-type", "diagnostic",
    ])
    assert args.save is False


def test_probe_presets_raw_persistence_is_explicit_opt_in():
    args = build_parser().parse_args(["probe-presets"])
    assert args.save is False
