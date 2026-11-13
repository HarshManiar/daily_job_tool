import json
import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from daily_job_tool.cli import app
from daily_job_tool.ops import backup as bk
from daily_job_tool.ops.heartbeat import HeartbeatWriter, classify, read_heartbeat
from daily_job_tool.ops.power import PowerAssertion
from daily_job_tool.ops.status import collect_status, format_status

ROOT = Path(__file__).resolve().parents[2]


class FakeProc:
    def __init__(self):
        self.terminated = False

    def terminate(self):
        self.terminated = True


class Spawner:
    def __init__(self, exc=None):
        self.calls = []
        self.procs = []
        self.exc = exc

    def __call__(self, args):
        if self.exc:
            raise self.exc
        self.calls.append(args)
        self.procs.append(FakeProc())
        return self.procs[-1]


def test_heartbeat_roundtrip_and_throttle(tmp_path):
    path = tmp_path / "sub" / "hb.json"
    hb = HeartbeatWriter(path, interval=30, pid=42)
    hb.poll()
    hb.last_finished_source = "greenhouse/warp"
    hb.tick(3)
    first = read_heartbeat(path)
    assert first["pid"] == 42 and first["in_flight"] == 3
    assert first["last_finished_source"] == "greenhouse/warp" and first["last_poll_at"]
    hb.tick(0)  # throttled
    assert read_heartbeat(path)["in_flight"] == 3
    hb.tick(0, force=True)
    assert read_heartbeat(path)["in_flight"] == 0
    hb.clear()
    assert read_heartbeat(path) is None


def test_heartbeat_classify():
    now = datetime.now(UTC)
    assert classify(None, now) == "not running"
    assert classify({"updated_at": now.isoformat()}, now) == "alive"
    assert classify({"updated_at": (now - timedelta(minutes=10)).isoformat()}, now) == "stale"
    assert classify({"bogus": 1}, now) == "stale"


def test_power_starts_once_and_stops_when_idle():
    sp = Spawner()
    p = PowerAssertion(sp, platform="darwin", pid=99)
    p.update(0)
    assert not sp.calls
    p.update(1)
    p.update(3)
    p.update(2)
    assert sp.calls == [["caffeinate", "-i", "-w", "99"]]
    assert p.active
    p.update(0)
    assert sp.procs[0].terminated and not p.active
    p.update(1)
    assert len(sp.calls) == 2
    p.stop()
    assert sp.procs[1].terminated


def test_power_noop_off_darwin_and_missing_caffeinate():
    sp = Spawner()
    PowerAssertion(sp, platform="linux").update(5)
    assert not sp.calls
    missing = Spawner(exc=FileNotFoundError("caffeinate"))
    p = PowerAssertion(missing, platform="darwin")
    p.update(2)  # tolerated
    p.update(2)
    assert not p.active


async def test_collect_status(db_clean, tmp_path):
    from sqlalchemy import text

    async with db_clean.begin() as c:
        cid = (
            await c.execute(
                text("INSERT INTO companies (name, slug) VALUES ('Acme','acme') RETURNING id")
            )
        ).scalar()
        for tok, status in (("a", "active"), ("b", "paused"), ("c", "blocked")):
            await c.execute(
                text(
                    "INSERT INTO sources (company_id, adapter, token, status, next_crawl_at) "
                    "VALUES (:c, 'greenhouse', :t, CAST(:s AS source_status), now() - interval '1 hour')"
                ),
                {"c": cid, "t": tok, "s": status},
            )
    hb_path = tmp_path / "hb.json"
    now = datetime.now(UTC)
    st = await collect_status(db_clean, now, backup_dir=tmp_path / "none", heartbeat_path=hb_path)
    assert st["sources_active"] == 1 and st["sources_due"] == 1
    assert st["sources_paused"] == 1 and st["sources_blocked"] == 1
    assert st["worker_state"] == "not running" and st["last_backup_at"] is None
    assert st["db_size_bytes"] > 0 and st["last_crawl_finished_at"] is None
    hb_path.write_text(json.dumps({"pid": 1, "updated_at": now.isoformat(), "in_flight": 0}))
    (tmp_path / "bk").mkdir()
    (tmp_path / "bk" / "daily_job_tool-20260101-0330.dump").write_text("x")
    st = await collect_status(db_clean, now, backup_dir=tmp_path / "bk", heartbeat_path=hb_path)
    assert st["worker_state"] == "alive" and st["last_backup_at"] is not None
    out = format_status(st, "")
    assert "BOT_CONTACT" in out and len(out.splitlines()) < 30
    assert "BOT_CONTACT" not in format_status(st, "me@example.com")


def test_prune_backups(tmp_path):
    for i in range(20):
        (tmp_path / f"daily_job_tool-202601{i + 1:02d}-0330.dump").write_text("x")
    (tmp_path / "other.txt").write_text("keep")
    pruned = bk.prune_backups(tmp_path, keep=14)
    assert len(pruned) == 6
    left = sorted(p.name for p in tmp_path.glob("*.dump"))
    assert len(left) == 14 and left[0] == "daily_job_tool-20260107-0330.dump"
    assert (tmp_path / "other.txt").exists()


def test_make_backup_uses_pg_dump(tmp_path, monkeypatch):
    seen = []

    def fake_run(cmd, env):
        seen.append(cmd)
        Path(cmd[cmd.index("-f") + 1]).write_text("dump")

    monkeypatch.setattr(bk, "_run", fake_run)
    out, _ = bk.make_backup(
        "postgresql+asyncpg://u:pw@localhost:5432/mydb", tmp_path, now=datetime(2026, 1, 2, 3, 4)
    )
    assert out.name == "daily_job_tool-20260102-0304.dump" and out.exists()
    assert seen[0][:2] == ["pg_dump", "-Fc"] and seen[0][-1] == "mydb"
    assert not list(tmp_path.glob("*.partial"))


def test_restore_refuses_existing_and_bad_names(tmp_path, monkeypatch):
    dump = tmp_path / "d.dump"
    dump.write_text("x")
    calls = []

    def fake_run(cmd, env):
        calls.append(cmd[0])
        return subprocess.CompletedProcess(cmd, 0, stdout="1\n", stderr="")

    monkeypatch.setattr(bk, "_run", fake_run)
    with pytest.raises(bk.BackupError, match="already exists"):
        bk.restore_backup("postgresql://localhost/x", dump, "taken")
    assert calls == ["psql"]
    with pytest.raises(bk.BackupError, match="database name"):
        bk.restore_backup("postgresql://localhost/x", dump, "bad;name")
    calls.clear()
    bk.restore_backup("postgresql://localhost/x", dump, "taken", overwrite=True)
    assert calls == ["psql", "dropdb", "createdb", "pg_restore"]


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_install_agent_dry_run_and_plists(tmp_path):
    r = subprocess.run(
        [str(ROOT / "scripts/install_agent.sh"), "--dry-run"],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    assert str(ROOT) in r.stdout
    assert "__" not in r.stdout and "bootstrap gui/" in r.stdout
    assert "com.dailyjobtool.backup.plist" in r.stdout
    if shutil.which("plutil"):
        for name in ("web", "worker", "backup"):
            text = (ROOT / f"launchd/com.dailyjobtool.{name}.plist").read_text()
            gen = tmp_path / f"{name}.plist"
            gen.write_text(
                text.replace("__PROJECT_DIR__", str(ROOT))
                .replace("__HOME__", os.path.expanduser("~"))
                .replace("__UV__", "/opt/homebrew/bin/uv")
            )
            subprocess.run(["plutil", "-lint", str(gen)], check=True, capture_output=True)
    u = subprocess.run(
        [str(ROOT / "scripts/uninstall_agent.sh"), "--dry-run"], capture_output=True, text=True
    )
    assert u.returncode == 0 and "bootout" in u.stdout


def test_cli_smoke(tmp_path, monkeypatch):
    runner = CliRunner()
    r = runner.invoke(app, ["restore", str(tmp_path / "missing.dump"), "--into", "x_new"])
    assert r.exit_code == 1 and "no such dump" in r.output
    r = runner.invoke(app, ["worker", "--help"])
    assert r.exit_code == 0 and "--forever" in r.output
    r = runner.invoke(app, ["backup", "--help"])
    assert r.exit_code == 0 and "--keep" in r.output
