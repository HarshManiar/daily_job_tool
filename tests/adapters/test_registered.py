from daily_job_tool.adapters.registry import all_adapters


def test_all_three_registered():
    adapters = all_adapters()
    assert [a.name for a in adapters] == ["ashby", "greenhouse", "lever"]
    assert all(a.tier == 1 and a.hosts for a in adapters)
