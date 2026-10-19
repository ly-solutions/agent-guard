"""Background leases: what counts as detached, deadlines, a notice before every stop."""

import io
import json
import os
import sys

import pytest

from agent_guard import bg_lease as bg
from agent_guard import hooks

H = 3600.0
NOW = 1_800_000_000.0


def P(pid, ppid, age_h, argv, session="s-1", **env):
    e = dict(env)
    if session:
        e["CLAUDE_CODE_SESSION_ID"] = session
    return bg.Proc(pid, ppid, NOW - age_h * H, argv, e, "/tmp/x")


def table(*procs):
    return {p.pid: p for p in procs}


INIT = dict(pid=1, ppid=0, age_h=1000, argv=["/sbin/init"], session=None)


def test_nohup_loop_under_init_is_detached_and_its_children_are_not_roots():
    procs = table(P(**INIT), P(10, 1, 72, ["/bin/bash", "./sync-loop.sh"]), P(11, 10, 0.1, ["python3", "copy.py", "bucket-a"]))
    assert [p.pid for p in bg.detached_roots(procs, me=999)] == [10]


def test_process_under_live_agent_is_not_detached():
    procs = table(
        P(**INIT),
        P(5, 1, 50, ["/opt/agent/bin/claude-5cd90aabd83f8a15", "--session-id", "s-1"]),
        P(6, 5, 40, ["/bin/bash", "-c", "npm run dev"]),
        P(7, 6, 40, ["node", "vite"]),
    )
    assert bg.detached_roots(procs, me=999) == []


def test_unmarked_processes_are_not_tracked():
    procs = table(P(**INIT), P(20, 1, 500, ["sshd"], session=None))
    assert bg.detached_roots(procs, me=999) == []


def test_the_sweeper_and_its_ancestors_are_never_targets():
    procs = table(P(**INIT), P(30, 1, 20, ["python3", "-m", "agent_guard.cli", "bg", "sweep"]))
    assert bg.detached_roots(procs, me=30) == []


def test_deadline_sources():
    assert bg.deadline(P(2, 1, 1, ["x"]))[0] == NOW - H + 12 * H
    assert bg.deadline(P(2, 1, 1, ["x"], AGENT_BG_TTL="48"))[0] == NOW - H + 48 * H
    assert bg.deadline(P(2, 1, 1, ["x"], AGENT_BG_KEEP="migration window"))[0] is None
    assert bg.deadline(P(2, 1, 1, ["/usr/bin/node", "/x/bin/mcp-bridge", "serve"]))[0] is None


def test_ttl_comes_from_config(site):
    site({"background": {"ttl_hours": 2}})
    assert bg.deadline(P(2, 1, 1, ["x"]))[0] == NOW - H + 2 * H


def run_sweep(monkeypatch, procs, now):
    stopped = []
    monkeypatch.setattr(bg, "snapshot", lambda: procs)
    monkeypatch.setattr(bg, "stop_tree", lambda _procs, pid: stopped.append(pid) or [pid])
    monkeypatch.setattr(bg.os, "getpid", lambda: 999)
    bg.sweep(now=now)
    return stopped


def test_overdue_process_is_warned_first_and_stopped_an_hour_later(monkeypatch):
    procs = table(P(**INIT), P(10, 1, 72, ["/bin/bash", "./two-way-loop.sh"]))
    assert run_sweep(monkeypatch, procs, NOW) == []
    notices = bg.take_notices("s-1")
    assert len(notices) == 1 and "will stop" in notices[0] and "pid 10" in notices[0]
    assert run_sweep(monkeypatch, procs, NOW + 0.5 * H) == []
    assert run_sweep(monkeypatch, procs, NOW + 1.01 * H) == [10]
    assert bg.take_notices("s-1")[0].startswith("agent-guard stopped")


def test_young_process_is_left_alone(monkeypatch):
    procs = table(P(**INIT), P(10, 1, 1, ["python3", "-m", "http.server"]))
    assert run_sweep(monkeypatch, procs, NOW) == []
    assert bg.take_notices("s-1") == []


def test_kept_process_and_daemon_survive(monkeypatch):
    procs = table(
        P(**INIT),
        P(10, 1, 500, ["/bin/bash", "./copy.sh"], AGENT_BG_KEEP="image copy"),
        P(11, 1, 500, ["/usr/bin/node", "/h/bin/mcp-bridge", "serve"]),
        P(12, 11, 400, ["kubectl", "port-forward", "svc/docs"]),
    )
    for at in (NOW, NOW + 2 * H):
        assert run_sweep(monkeypatch, procs, at) == []


def test_lease_extends_a_running_process(monkeypatch):
    p = P(10, 1, 11.5, ["/bin/bash", "./loop.sh"])
    procs = table(P(**INIT), p)
    monkeypatch.setattr(bg, "snapshot", lambda: procs)
    monkeypatch.setattr(bg.time, "time", lambda: NOW)
    bg.keep(10, 24, "catch-up sync until the cut-over ends")
    assert bg.deadline(p)[0] == NOW + 24 * H
    with pytest.raises(SystemExit):
        bg.keep(10, 1, "  ")


def test_notices_are_taken_once():
    bg.notify("s-2", "first")
    bg.notify("s-2", "second")
    assert bg.take_notices("s-2") == ["first", "second"]
    assert bg.take_notices("s-2") == []


def test_due_claims_the_slot_once_per_period():
    assert bg.due(now=NOW) is True
    os.utime(os.path.join(bg.state_dir(), "sweep.stamp"), (NOW, NOW))
    assert bg.due(now=NOW + 60) is False
    assert bg.due(now=NOW + 600 + 1) is True


def test_hook_hints_once_per_session_after_detaching(monkeypatch, capsys):
    from agent_guard import config as config_mod

    event = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "session_id": "s-3",
        "tool_input": {"command": "cd s3 && (nohup ./two-way-loop.sh > log 2>&1 &)"},
    }
    for expected in (True, False):
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
        out = hooks.post_bash(event, config_mod.load())
        assert (out is not None and "AGENT_BG_TTL" in out["hookSpecificOutput"]["additionalContext"]) is expected


def test_stop_notice_reaches_the_session_with_a_system_message():
    from agent_guard import config as config_mod

    bg.notify("s-4", "agent-guard stopped your background process: pid 1")
    out = hooks.post_bash({"tool_name": "Bash", "session_id": "s-4", "tool_input": {"command": "ls"}}, config_mod.load())
    assert out["systemMessage"].startswith("agent-guard stopped 1")


@pytest.mark.parametrize(
    "command,detached",
    [
        ("(nohup ./loop.sh > log 2>&1 &)", True),
        ("setsid python3 server.py", True),
        ("python3 -m http.server 4480 &", True),
        ("npm run dev & sleep 2; curl localhost:3000", True),
        ("ls && echo ok", False),
        ("cmd 2>&1 | tail", False),
        ("make &> build.log && ok", False),
    ],
)
def test_detach_pattern(command, detached):
    assert bool(hooks.DETACH.search(command)) is detached
