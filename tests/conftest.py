import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_guard import config as config_mod  # noqa: E402
from agent_guard import hooks  # noqa: E402

RUN = ROOT / "hooks" / "run.py"

# A neutral site configuration: a shared workspace with primary checkouts, task keys
# PROJ-n / TASK-n, test namespaces matched by the default pattern.
WS = "/srv/work"
WT = f"{WS}/.worktrees/web-1/task-1"
SITE = {
    "task_keys": {"pattern": r"\b(PROJ|TASK)-\d+\b"},
    "filesystem": {"workspace_roots": [WS], "writable_in_checkout": [".release/"]},
    "proxy": {"clear_for": ["kubectl", "tsh", "tbot"]},
    "custom": [
        {
            "name": "tsh-login",
            "pattern": r"^tsh login\b",
            "reason": "tsh login replaces the machine identity; use the bot's kubeconfig.",
        }
    ],
    "background": {"daemon_patterns": [r"/mcp-bridge(\s|$)"]},
}


@pytest.fixture(autouse=True)
def site(tmp_path, monkeypatch):
    """Write the config to a temp file and point agent-guard at it; returns a function to change it."""

    def use(extra: dict | None = None, base: dict | None = None) -> config_mod.Config:
        data = config_mod.merge(SITE if base is None else base, extra or {})
        path = tmp_path / "cfg" / "agent-guard" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        monkeypatch.setenv("AGENT_GUARD_CONFIG", str(path))
        config_mod._CACHE.clear()
        return config_mod.load()

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("TMPDIR", raising=False)
    use()
    yield use
    config_mod._CACHE.clear()


def bash(command: str, cwd: str | None = WT, codex: bool = False) -> dict | None:
    event: dict = {"tool_name": "Bash", "cwd": cwd, "tool_input": {"command": command, "description": "x"}}
    if codex:
        event["turn_id"] = "t-1"
    return hooks.pre_bash(event, config_mod.load())


def hook(kind: str, event: dict) -> dict | None:
    """Run the real entry point in a subprocess, the way the agent does."""
    out = subprocess.run([sys.executable, str(RUN), kind], input=json.dumps(event), capture_output=True, text=True, check=True).stdout
    return json.loads(out) if out.strip() else None


def denied(result: dict | None) -> bool:
    return bool(result) and result["hookSpecificOutput"].get("permissionDecision") == "deny"


def reason(result: dict | None) -> str:
    assert result, "expected an answer"
    return result["hookSpecificOutput"]["permissionDecisionReason"]


def rewritten(result: dict | None) -> str:
    assert result and "updatedInput" in result["hookSpecificOutput"], result
    return result["hookSpecificOutput"]["updatedInput"]["command"]
