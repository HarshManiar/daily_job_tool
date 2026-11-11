import pytest

from daily_job_tool.match.profile import load_profile


@pytest.fixture(scope="session")
def profile():
    return load_profile("data/profile.example.yaml")
