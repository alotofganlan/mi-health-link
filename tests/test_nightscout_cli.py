from mi_health_link.nightscout_sync import build_parser


def test_nightscout_cli_defaults_are_safe_and_incremental():
    args = build_parser().parse_args([])
    assert args.url is None
    assert args.token is None
    assert args.batch_size == 1000
