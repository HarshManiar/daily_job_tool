def test_cli_app_exists():
    from daily_job_tool.cli import app

    assert app is not None
