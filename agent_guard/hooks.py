"""Hook handlers: one JSON event in, one JSON answer (or nothing) out.

The same handlers serve Claude Code and Codex. Both send the Claude Code hook payload;
Codex adds ``turn_id``, reports its shell tool as ``Bash`` and file edits as ``apply_patch``
(with the patch in ``tool_input.command``), and accepts a rewritten command only together
with ``permissionDecision: "allow"``.

Every handler is silent on anything it does not recognise and on any error of its own:
a broken guard must never block work (the dispatcher swallows exceptions).
"""

from __future__ import annotations

import json
import os
import re
import sys

from . import bg_lease
from . import config as config_mod
from .messages import t
from .rules import Denial, check_bash, safe_namespace
from .shell import namespace, split_segments, words
from .writes import check_patch, check_write

GATE = re.compile(
    r"\b(nx|pnpm|npm|yarn|npx|bun)\b[^|]*\b(test|lint|build|typecheck|e2e)\b|\bgo (test|vet|build)\b|\bpytest\b|\bvitest\b"
    r"|\btsc\b|\beslint\b|\bruff\b|\bmypy\b|\bcargo (test|build|clippy)\b|\bgradlew?\b[^|]*\b(test|build|check)\b"
)
PIPE_TAIL = re.compile(r"\|\s*(tail|head|grep|tee|sed|awk|cat)\b")
PKILL_WORD = re.compile(r"\bpkill\b")
PKILL_SHIM = 'pkill() { command pkill -A "$@"; }; export -f pkill'
FORCE_FLAG = re.compile(r"(\bpush\b[^;&|\n]*?\s)(--force|-f)(?=\s|$)")
DETACH = re.compile(r"\b(nohup|setsid|disown)\b|(?<![&>|<])&(?![&>])")
BROKEN_STREAM = re.compile(
    r"moved to the background|connection reset|unexpected EOF|error executing command in container|timed out|SIGPIPE|broken pipe",
    re.I,
)


def is_codex(event: dict) -> bool:
    return "turn_id" in event


def _out(event_name: str, **fields: object) -> dict:
    return {"hookSpecificOutput": {"hookEventName": event_name, **fields}}


def deny(cfg: config_mod.Config, denial: Denial) -> dict:
    reason = t(cfg, "prefix", rule=denial.rule) + denial.reason
    return _out("PreToolUse", permissionDecision="deny", permissionDecisionReason=reason)


def rewrite(event: dict, tool_input: dict, command: str) -> dict:
    fields: dict = {"updatedInput": {**tool_input, "command": command}}
    if is_codex(event):
        fields["permissionDecision"] = "allow"  # Codex applies updatedInput only with "allow"
    return _out("PreToolUse", **fields)


def pkill_spares_ancestors() -> bool:
    """procps-ng 4.0.3+ `pkill -A` (--ignore-ancestors) skips the caller's own ancestors, the agent's shell among them."""
    import subprocess

    try:
        out = subprocess.run(["pkill", "--help"], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return False
    return "--ignore-ancestors" in out.stdout + out.stderr


def lease_instead_of_force(command: str, cwd: str | None, cfg: config_mod.Config) -> str | None:
    """The same push with --force-with-lease when that alone makes it pass; None when a heredoc makes the text unsafe to touch."""
    if "<<" in command:
        return None
    lease = FORCE_FLAG.sub(r"\1--force-with-lease", command)
    return lease if lease != command and check_bash(lease, cwd, cfg) is None else None


def piped_gate(cfg: config_mod.Config, command: str) -> bool:
    extra = cfg["gates"]["extra_pattern"]
    for segment in re.split(r"&&|;|\n", command):
        hit = GATE.search(segment) or (re.search(extra, segment) if extra else None)
        if hit and PIPE_TAIL.search(segment[hit.end() :]):
            return True
    return False


def _proxy_rewrite(cfg: config_mod.Config, command: str) -> str:
    tools = cfg["proxy"]["clear_for"]
    variables = cfg["proxy"]["variables"]
    if not tools or not variables:
        return command
    names = "|".join(re.escape(x) for x in tools)
    if not re.search(rf"(^|[;&|(]\s*|\s)({names})\s", command):
        return command
    if re.search(r"unset\s+(" + "|".join(re.escape(v) for v in variables) + r")\b", command):
        return command
    return f"unset {' '.join(variables)}\n{command}"


def pre_bash(event: dict, cfg: config_mod.Config) -> dict | None:
    if event.get("tool_name") != "Bash":
        return None
    tool_input = event.get("tool_input") or {}
    command = tool_input.get("command") or ""
    if not command:
        return None
    cwd = event.get("cwd") or None
    can_rewrite = not is_codex(event) or cfg["codex"]["rewrites"] == "allow"
    shim = can_rewrite and cfg.on("pkill-full") and any(PKILL_WORD.search(seg) for seg in split_segments(command)) and pkill_spares_ancestors()
    denial = check_bash(command, cwd, cfg, pkill_shim=shim)
    changed = command
    if denial and denial.rule == "force-push":
        lease = lease_instead_of_force(command, cwd, cfg)
        if lease and can_rewrite:
            changed, denial = lease, None
        elif lease:
            denial = Denial(denial.rule, denial.reason + "\n" + t(cfg, "run_instead", command=lease))
    if denial:
        return deny(cfg, denial)
    if shim:
        changed = f"{PKILL_SHIM}\n{changed}"
    if can_rewrite:
        if cfg.on("clear-proxy"):
            changed = _proxy_rewrite(cfg, changed)
        if cfg.on("pipefail-gates") and piped_gate(cfg, changed) and "pipefail" not in changed and "PIPESTATUS" not in changed:
            label = t(cfg, "exit_code", code="$__gate_exit")
            changed = f'set -o pipefail\n{changed}\n__gate_exit=$?; echo "{label}"; exit $__gate_exit'
    return rewrite(event, tool_input, changed) if changed != command else None


def pre_write(event: dict, cfg: config_mod.Config) -> dict | None:
    tool_input = event.get("tool_input") or {}
    cwd = event.get("cwd") or None
    if event.get("tool_name") == "apply_patch":
        patch = tool_input.get("command") or tool_input.get("patch") or tool_input.get("input") or ""
        denial = check_patch(patch, cfg, cwd) if isinstance(patch, str) else None
    else:
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        texts = [tool_input.get("content"), tool_input.get("new_string"), tool_input.get("new_source")]
        texts += [e.get("new_string") for e in tool_input.get("edits") or [] if isinstance(e, dict)]
        denial = check_write(path, [x for x in texts if isinstance(x, str)], cfg, cwd)
    return deny(cfg, denial) if denial else None


def prod_psql_exec(cfg: config_mod.Config, command: str) -> bool:
    for segment in split_segments(command):
        tokens = words(segment).tokens
        if tokens[:1] != ["kubectl"] or "exec" not in tokens or "psql" not in segment:
            continue
        if safe_namespace(cfg, namespace(tokens[1:])):
            continue
        return True
    return False


def _hinted_once(session: str) -> bool:
    marker = os.path.join(bg_lease.state_dir(), "hinted", session)
    if os.path.exists(marker):
        return True
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    open(marker, "w").close()
    return False


def _bg_answer(cfg: config_mod.Config, name: str, context: list[str]) -> dict | None:
    if not context:
        return None
    out = _out(name, additionalContext="\n\n".join(context))
    stopped = sum(1 for c in context if c.startswith(t(cfg, "bg_stopped_tag")))
    if stopped:
        out["systemMessage"] = t(cfg, "bg_system", n=stopped)
    return out


def _session_id(event: dict, cfg: config_mod.Config) -> str | None:
    return event.get("session_id") or os.environ.get(cfg["background"]["session_env"])


def post_bash(event: dict, cfg: config_mod.Config) -> dict | None:
    """After a shell command: advice on an orphaned production psql; news about background processes."""
    if event.get("tool_name") != "Bash":
        return None
    command = (event.get("tool_input") or {}).get("command") or ""
    context: list[str] = []
    if cfg.on("orphaned-db-session"):
        response = json.dumps(event.get("tool_response"), ensure_ascii=False)
        if prod_psql_exec(cfg, command) and BROKEN_STREAM.search(response):
            context.append(t(cfg, "aftercare"))
    if cfg.on("background-lease"):
        session = _session_id(event, cfg)
        context += bg_lease.take_notices(session)
        if session and DETACH.search(command) and not _hinted_once(session):
            ttl = float(cfg["background"]["ttl_hours"])
            context.append(t(cfg, "bg_hint", ttl=ttl, ttl_env=bg_lease.TTL_ENV, keep_env=bg_lease.KEEP_ENV))
    return _bg_answer(cfg, "PostToolUse", context)


def spawn_sweep() -> None:
    import subprocess

    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "CODEX"))}
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.Popen(
        [sys.executable, "-m", "agent_guard.cli", "bg", "sweep", "--quiet"],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )


def session(event: dict, cfg: config_mod.Config) -> dict | None:
    """SessionStart / UserPromptSubmit: deliver what the sweep did, and start the sweep at most once per period."""
    if not cfg.on("background-lease") or not sys.platform.startswith("linux"):
        return None
    name = event.get("hook_event_name") or "SessionStart"
    context = bg_lease.take_notices(_session_id(event, cfg))
    if bg_lease.due():
        spawn_sweep()
    return _bg_answer(cfg, name, context)


HANDLERS = {"pre-bash": pre_bash, "pre-write": pre_write, "post-bash": post_bash, "session": session}


def run(kind: str, stdin: str) -> str:
    """Handle one event; returns what to print (possibly empty)."""
    event = json.loads(stdin)
    answer = HANDLERS[kind](event, config_mod.load())
    return json.dumps(answer, ensure_ascii=False) if answer else ""
