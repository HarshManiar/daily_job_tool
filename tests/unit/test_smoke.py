def test_cli_app_exists():
    from firsthand.cli import app

    assert app is not None
