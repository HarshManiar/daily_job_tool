from daily_job_tool.adapters.registry import all_adapters

EXPECTED = {"ashby", "darwinbox", "gem", "greenhouse", "lever", "pharmaace", "rippling", "workable"}


def test_adapters_registered():
    adapters = all_adapters()
    names = [a.name for a in adapters]
    assert names == sorted(names)
    assert EXPECTED <= set(names)
    assert all(a.tier >= 1 and a.hosts for a in adapters)
