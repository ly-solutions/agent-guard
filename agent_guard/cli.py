"""Command line: try a command against the rules, show the effective config, manage background leases.

  agent-guard check [--cwd DIR] [--codex] -- <command …>   what the Bash hook would do with it
  agent-guard check-write PATH [--text TEXT]                what the write hook would do
  agent-guard config [--defaults]                            effective configuration (JSON)
  agent-guard bg list [--json]                               detached agent processes and their time left
  agent-guard bg keep <pid> --hours N --why '…'              extend (--forever: no limit)
  agent-guard bg stop <pid>                                  stop a process with its descendants
  agent-guard bg sweep [--dry-run] [--quiet]                 stop expired ones, warn about close ones

`agent-bg …` is a shortcut for `agent-guard bg …`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import __version__, bg_lease, hooks
from . import config as config_mod


def cmd_check(args: argparse.Namespace) -> int:
    command = " ".join(args.command)
    event: dict = {"tool_name": "Bash", "cwd": args.cwd, "tool_input": {"command": command}}
    if args.codex:
        event["turn_id"] = "cli"
    answer = hooks.pre_bash(event, config_mod.load())
    out = (answer or {}).get("hookSpecificOutput", {})
    if out.get("permissionDecision") == "deny":
        print("DENY  " + out["permissionDecisionReason"])
        return 2
    if "updatedInput" in out:
        print("REWRITE\n" + out["updatedInput"]["command"])
        return 0
    print("ALLOW")
    return 0


def cmd_check_write(args: argparse.Namespace) -> int:
    event = {"tool_name": "Write", "cwd": os.getcwd(), "tool_input": {"file_path": args.path, "content": args.text or ""}}
    answer = hooks.pre_write(event, config_mod.load())
    if answer:
        print("DENY  " + answer["hookSpecificOutput"]["permissionDecisionReason"])
        return 2
    print("ALLOW")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    if args.defaults:
        print(json.dumps(config_mod.DEFAULTS, indent=2, ensure_ascii=False))
        return 0
    cfg = config_mod.load()
    print(f"# source: {cfg.source or 'built-in defaults'} (searched: {', '.join(config_mod.config_paths())})", file=sys.stderr)
    if cfg.source:
        try:
            unknown = config_mod.unknown_keys(config_mod._parse(cfg.source))
        except Exception:  # noqa: BLE001
            unknown = []
        for key in unknown:
            print(f"# warning: unknown key {key}", file=sys.stderr)
    print(json.dumps(cfg, indent=2, ensure_ascii=False))
    return 0


def cmd_bg_list(args: argparse.Namespace) -> int:
    procs = bg_lease.snapshot()
    live = bg_lease.live_sessions(procs)
    now = time.time()
    rows = [bg_lease.describe(p, now, live) for p in bg_lease.detached_roots(procs)]
    rows.sort(key=lambda r: procs[r["pid"]].start)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("no detached agent processes")
        return 0
    for r in rows:
        sess = (r["session"] or "")[:8] + (" alive" if r["session_alive"] else " closed")
        print(f"{r['pid']:>8}  {r['age']:>9}  left {r['left']:<10} session {sess:<15} {r['source']}")
        print(f"{'':>10}{r['cwd']}\n{'':>10}{r['cmd'][:160]}")
    return 0


def cmd_bg_keep(args: argparse.Namespace) -> int:
    if args.hours is None and not args.forever:
        raise SystemExit("give --hours N or --forever")
    lease = bg_lease.keep(args.pid, None if args.forever else args.hours, args.why or "")
    until = "no limit" if lease["until"] is None else time.strftime("%Y-%m-%d %H:%M", time.localtime(lease["until"]))
    print(f"pid {args.pid}: lives until {until} — {lease['why']}")
    return 0


def cmd_bg_stop(args: argparse.Namespace) -> int:
    procs = bg_lease.snapshot()
    if args.pid not in procs:
        raise SystemExit(f"no process {args.pid}")
    pids = bg_lease.stop_tree(procs, args.pid)
    bg_lease.log(f"stop-manual pids={pids} cmd={procs[args.pid].cmd[:300]!r}")
    print(f"stopped: {' '.join(map(str, pids))}")
    return 0


def cmd_bg_sweep(args: argparse.Namespace) -> int:
    report = bg_lease.sweep(dry_run=args.dry_run)
    if not args.quiet:
        print("\n".join(report) if report else "nothing expired")
    return 0


def _bg_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("list", help="detached agent processes and their time left")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_bg_list)
    p = sub.add_parser("keep", help="extend the lifetime of a detached process")
    p.add_argument("pid", type=int)
    p.add_argument("--hours", type=float)
    p.add_argument("--forever", action="store_true")
    p.add_argument("--why", required=True)
    p.set_defaults(fn=cmd_bg_keep)
    p = sub.add_parser("stop", help="stop a process with its descendants")
    p.add_argument("pid", type=int)
    p.set_defaults(fn=cmd_bg_stop)
    p = sub.add_parser("sweep", help="stop expired detached processes, warn about close ones")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(fn=cmd_bg_sweep)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog="agent-guard", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--version", action="version", version=f"agent-guard {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check", help="what the Bash hook would do with a command")
    p.add_argument("--cwd", default=os.getcwd())
    p.add_argument("--codex", action="store_true", help="answer as for Codex")
    p.add_argument("command", nargs=argparse.REMAINDER)
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("check-write", help="what the write hook would do with a file write")
    p.add_argument("path")
    p.add_argument("--text")
    p.set_defaults(fn=cmd_check_write)
    p = sub.add_parser("config", help="print the effective configuration")
    p.add_argument("--defaults", action="store_true")
    p.set_defaults(fn=cmd_config)
    p = sub.add_parser("bg", help="background process leases")
    _bg_parser(p.add_subparsers(dest="bg_cmd", required=True))
    args = ap.parse_args(argv)
    if getattr(args, "command", None) and args.command[:1] == ["--"]:
        args.command = args.command[1:]
    return args.fn(args)


def bg_main() -> int:
    return main(["bg", *sys.argv[1:]])


if __name__ == "__main__":
    sys.exit(main())
