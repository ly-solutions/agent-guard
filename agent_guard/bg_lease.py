"""A lifetime for the background processes an agent leaves behind (Linux; a no-op elsewhere).

Agents start loops, dev servers and port-forwards with ``nohup … &`` and forget them. The
session that started them may stay alive for days; it simply never looks back. A
forgotten sync loop keeps copying, a forgotten port-forward keeps a production database
reachable, long after anybody remembers why.

The rule is mechanical; nothing guesses what a command is for:

- an *agent process* carries the session marker (``CLAUDE_CODE_SESSION_ID`` by default) in
  its environment; agents that leave no marker are not tracked;
- it is *detached* when no live agent process (``claude``, ``codex``) is among its
  ancestors: ``nohup … &``, ``setsid``, a double fork. What still hangs under the agent
  dies with it anyway;
- a detached process lives ``ttl_hours`` from its start unless extended:
  ``AGENT_BG_TTL=<hours>`` or ``AGENT_BG_KEEP=<reason>`` in its environment, or
  ``agent-bg keep <pid> --hours N --why …`` (a lease file);
- the owning session is told ``warn_hours`` before the deadline; the sweep stops a process
  only when its deadline has passed *and* the warning is at least ``notice_grace_s`` old, so
  nothing dies unannounced, not even what was already overdue when agent-guard arrived.
  It stops the process with its descendants (SIGTERM, then SIGKILL) and says so.

Daemons matching ``daemon_patterns`` and everything under them are left alone.
"""

from __future__ import annotations

import json
import os
import re
import signal
import time

from . import config as config_mod
from .messages import t

TTL_ENV = "AGENT_BG_TTL"
KEEP_ENV = "AGENT_BG_KEEP"

try:
    _HZ = os.sysconf("SC_CLK_TCK")
except (AttributeError, ValueError, OSError):  # pragma: no cover - non-POSIX
    _HZ = 100


def cfg() -> config_mod.Config:
    return config_mod.load()


def bg_cfg() -> dict:
    return cfg()["background"]


def state_dir() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "agent-guard", "bg")


def _path(*parts: str) -> str:
    return os.path.join(state_dir(), *parts)


def _read(path: str, mode: str = "r"):
    try:
        with open(path, mode) as f:
            return f.read()
    except OSError:
        return None


def _agent_bin() -> re.Pattern[str]:
    names = "|".join(re.escape(n) for n in bg_cfg()["agent_binaries"]) or "(?!)"
    return re.compile(rf"^({names})(-[0-9a-f]+)?$")


class Proc:
    __slots__ = ("argv", "cwd", "env", "pid", "ppid", "start")

    def __init__(self, pid: int, ppid: int, start: float, argv: list[str], env: dict | None, cwd: str):
        self.pid, self.ppid, self.start, self.argv, self.env, self.cwd = pid, ppid, start, argv, env, cwd

    @property
    def key(self) -> str:
        return f"{self.pid}-{int(self.start)}"

    @property
    def cmd(self) -> str:
        return " ".join(self.argv)

    @property
    def session(self) -> str | None:
        return (self.env or {}).get(bg_cfg()["session_env"])

    def is_agent(self) -> bool:
        return bool(self.argv) and bool(_agent_bin().match(os.path.basename(self.argv[0])))


def snapshot(proc_root: str = "/proc") -> dict[int, Proc]:
    boot = time.time() - float((_read(f"{proc_root}/uptime") or "0").split()[0])
    out = {}
    for name in os.listdir(proc_root):
        if not name.isdigit():
            continue
        stat = _read(f"{proc_root}/{name}/stat")
        raw = _read(f"{proc_root}/{name}/cmdline", "rb")
        if not stat or raw is None:
            continue
        rest = stat[stat.rindex(")") + 2 :].split()
        env_raw = _read(f"{proc_root}/{name}/environ", "rb")
        env = None
        if env_raw is not None:
            env = dict(x.split("=", 1) for x in env_raw.decode(errors="replace").split("\0") if "=" in x)
        try:
            cwd = os.readlink(f"{proc_root}/{name}/cwd")
        except OSError:
            cwd = ""
        argv = [a for a in raw.decode(errors="replace").split("\0") if a]
        out[int(name)] = Proc(int(name), int(rest[1]), boot + int(rest[19]) / _HZ, argv, env, cwd)
    return out


def live_sessions(procs: dict[int, Proc]) -> set[str]:
    live = set()
    for p in procs.values():
        if p.is_agent() and "--session-id" in p.argv[:-1]:
            live.add(p.argv[p.argv.index("--session-id") + 1])
        if p.is_agent() and p.session:
            live.add(p.session)
    return live


def _lease(p: Proc) -> dict | None:
    raw = _read(_path("leases", p.key + ".json"))
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def _daemon(p: Proc) -> bool:
    return any(re.search(rx, p.cmd) for rx in bg_cfg()["daemon_patterns"])


def deadline(p: Proc) -> tuple[float | None, str]:
    """Absolute stop time (None = never) and where it comes from."""
    c = cfg()
    if _daemon(p):
        return None, t(c, "bg_daemon")
    lease = _lease(p)
    if lease:
        return lease.get("until"), t(c, "bg_kept", why=lease.get("why") or "")
    env = p.env or {}
    if env.get(KEEP_ENV):
        return None, f"{KEEP_ENV}: {env[KEEP_ENV]}"
    try:
        hours = float(env.get(TTL_ENV, ""))
        return p.start + hours * 3600, f"{TTL_ENV}={hours:g}"
    except ValueError:
        ttl = float(c["background"]["ttl_hours"])
        return p.start + ttl * 3600, t(c, "bg_default", hours=ttl)


def detached_roots(procs: dict[int, Proc], me: int | None = None) -> list[Proc]:
    """Tops of detached agent subtrees: agent-marked, no live agent above, parent not one of them."""
    me = me or os.getpid()
    mine = set()
    cur = procs.get(me)
    while cur:
        mine.add(cur.pid)
        cur = procs.get(cur.ppid) if cur.ppid != cur.pid else None

    def chain(p: Proc | None):
        seen = set()
        while p and p.pid not in seen:
            seen.add(p.pid)
            yield p
            p = procs.get(p.ppid)

    detached = {}
    for p in procs.values():
        if not p.session or p.is_agent() or p.pid in mine:
            continue
        if any(a.is_agent() for a in list(chain(p))[1:]):
            continue
        detached[p.pid] = p
    return [p for p in detached.values() if p.ppid not in detached]


def descendants(procs: dict[int, Proc], root: int) -> list[int]:
    kids: dict[int, list[int]] = {}
    for p in procs.values():
        kids.setdefault(p.ppid, []).append(p.pid)
    out, todo = [], [root]
    while todo:
        pid = todo.pop()
        out.append(pid)
        todo.extend(kids.get(pid, []))
    return out


def fmt_age(seconds: float) -> str:
    h = seconds / 3600
    return t(cfg(), "bg_hours", n=h) if h < 48 else t(cfg(), "bg_days", n=h / 24)


def describe(p: Proc, now: float, live: set[str]) -> dict:
    c = cfg()
    until, source = deadline(p)
    if until is None:
        left = t(c, "bg_no_limit")
    elif until <= now:
        left = t(c, "bg_expired")
    else:
        left = fmt_age(until - now)
    return {
        "pid": p.pid,
        "key": p.key,
        "age": fmt_age(now - p.start),
        "left": left,
        "source": source,
        "session": p.session,
        "session_alive": p.session in live,
        "cwd": p.cwd,
        "cmd": p.cmd[:200],
    }


def notify(session: str | None, text: str) -> None:
    if not session:
        return
    os.makedirs(_path("notices"), exist_ok=True)
    with open(_path("notices", session + ".jsonl"), "a") as f:
        f.write(json.dumps({"at": time.time(), "text": text}, ensure_ascii=False) + "\n")


def take_notices(session: str | None) -> list[str]:
    if not session:
        return []
    path = _path("notices", session + ".jsonl")
    taken = path + f".{os.getpid()}"
    try:
        os.rename(path, taken)
    except OSError:
        return []
    raw = _read(taken) or ""
    os.unlink(taken)
    out = []
    for line in raw.splitlines():
        try:
            out.append(json.loads(line)["text"])
        except (ValueError, KeyError):
            continue
    return out


def log(text: str) -> None:
    os.makedirs(state_dir(), exist_ok=True)
    with open(_path("sweep.log"), "a") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text + "\n")


def stop_tree(procs: dict[int, Proc], root: int, grace: float = 5.0) -> list[int]:
    pids = descendants(procs, root)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    end = time.time() + grace
    while time.time() < end and any(os.path.exists(f"/proc/{pid}") for pid in pids):
        time.sleep(0.2)
    for pid in pids:
        if os.path.exists(f"/proc/{pid}"):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    return pids


def sweep(dry_run: bool = False, now: float | None = None) -> list[str]:
    """Stop expired detached agent processes, warn about the ones close to expiry."""
    c = cfg()
    b = c["background"]
    now = now or time.time()
    procs = snapshot()
    live = live_sessions(procs)
    try:
        warned = dict(json.loads(_read(_path("warned.json")) or "{}"))
    except (ValueError, TypeError):
        warned = {}
    report = []
    for p in detached_roots(procs):
        until, source = deadline(p)
        if until is None or until - now > float(b["warn_hours"]) * 3600:
            continue
        d = describe(p, now, live)
        where = t(c, "bg_where", pid=p.pid, age=d["age"], cwd=p.cwd, cmd=d["cmd"][:120])
        if p.key not in warned:
            report.append(t(c, "bg_report_warned", where=where, source=source))
            if dry_run:
                continue
            warned[p.key] = now
            stop_at = max(until, now + float(b["notice_grace_s"]))
            notify(p.session, t(c, "bg_warn", left=fmt_age(stop_at - now), where=where, source=source, pid=p.pid))
        elif until <= now and now - warned[p.key] >= float(b["notice_grace_s"]):
            report.append(t(c, "bg_report_stopped", where=where, source=source))
            if dry_run:
                continue
            pids = stop_tree(procs, p.pid)
            log(f"stop session={p.session} pids={pids} age={d['age']} source={source!r} cmd={p.cmd[:300]!r}")
            notify(p.session, t(c, "bg_stopped", where=where, source=source, ttl_env=TTL_ENV, keep_env=KEEP_ENV))
    if not dry_run:
        alive_keys = {p.key for p in procs.values()}
        os.makedirs(state_dir(), exist_ok=True)
        with open(_path("warned.json"), "w") as f:
            json.dump({k: v for k, v in warned.items() if k in alive_keys}, f)
        if os.path.isdir(_path("leases")):
            for name in os.listdir(_path("leases")):
                if name[:-5] not in alive_keys:
                    os.unlink(_path("leases", name))
    return report


def keep(pid: int, hours: float | None, why: str) -> dict:
    c = cfg()
    procs = snapshot()
    p = procs.get(pid)
    if not p:
        raise SystemExit(t(c, "bg_no_process", pid=pid))
    if not why.strip():
        raise SystemExit(t(c, "bg_need_reason"))
    lease = {
        "until": None if hours is None else time.time() + hours * 3600,
        "why": why,
        "cmd": p.cmd[:300],
        "by": os.environ.get(c["background"]["session_env"]),
    }
    os.makedirs(_path("leases"), exist_ok=True)
    with open(_path("leases", p.key + ".json"), "w") as f:
        json.dump(lease, f, ensure_ascii=False)
    log(f"keep pid={pid} hours={hours} why={why!r}")
    return lease


def due(now: float | None = None) -> bool:
    """True once per `sweep_every_s` across all sessions of the machine user (claims the slot)."""
    now = now or time.time()
    stamp = _path("sweep.stamp")
    os.makedirs(state_dir(), exist_ok=True)
    try:
        if now - os.stat(stamp).st_mtime < float(bg_cfg()["sweep_every_s"]):
            return False
    except OSError:
        pass
    with open(stamp, "w") as f:
        f.write(str(now))
    return True
