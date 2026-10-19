import os

from conftest import WT, denied, hook, reason

FAKE_GITLAB = "glpat-" + "Ab3dE5gH7jK9mN1pQ3sT"
FAKE_AWS = "AKIA" + "Q3EGRZ7YBN5WD2LT"


def write(path: str, content: str) -> dict | None:
    return hook("pre-write", {"tool_name": "Write", "tool_input": {"file_path": path, "content": content}})


def patch(text: str, cwd: str = WT) -> dict | None:
    return hook("pre-write", {"tool_name": "apply_patch", "turn_id": "t", "cwd": cwd, "tool_input": {"command": text}})


def test_secret_files_and_live_tokens_are_refused() -> None:
    assert denied(write(f"{WT}/.env", "A=1"))
    assert denied(write(f"{WT}/.env.production", "A=1"))
    assert denied(write(f"{WT}/deploy/key.pem", "x"))
    assert denied(write(f"{WT}/src/config.ts", f"export const token = '{FAKE_GITLAB}';"))
    assert denied(write(f"{WT}/src/aws.py", f"KEY = '{FAKE_AWS}'"))


def test_examples_and_variable_names_pass() -> None:
    assert write(f"{WT}/.env.example", "GITLAB_TOKEN=") is None
    assert write(f"{WT}/src/config.ts", "export const token = process.env.GITLAB_TOKEN;") is None
    assert write(f"{WT}/tests/fixtures/redact.json", '{"t": "glpat-FAKEFAKEFAKEFAKEFAKE00"}') is None
    assert write(f"{WT}/README.md", "Tokens look like `glpat-xxxxxxxxxxxxxxxxxxxx`.") is None


def test_edit_and_multiedit_are_checked_too() -> None:
    event = {"tool_name": "MultiEdit", "tool_input": {"file_path": f"{WT}/a.py", "edits": [{"old_string": "a", "new_string": f"T='{FAKE_GITLAB}'"}]}}
    assert denied(hook("pre-write", event))
    event = {"tool_name": "Edit", "tool_input": {"file_path": f"{WT}/a.py", "old_string": "a", "new_string": "b"}}
    assert hook("pre-write", event) is None


def test_codex_apply_patch() -> None:
    added = "*** Begin Patch\n*** Add File: src/settings.py\n+TOKEN = '" + FAKE_GITLAB + "'\n*** End Patch\n"
    assert reason(patch(added)).startswith("agent-guard (token-in-file)")
    env = "*** Begin Patch\n*** Update File: .env\n@@\n-A=1\n+A=2\n*** End Patch\n"
    assert reason(patch(env)).startswith("agent-guard (secret-file-write)")
    moved = "*** Begin Patch\n*** Update File: notes.txt\n*** Move to: .env\n@@\n+A=2\n*** End Patch\n"
    assert denied(patch(moved))
    plain = "*** Begin Patch\n*** Update File: src/a.py\n@@\n-x = 1\n+x = os.environ['TOKEN']\n*** End Patch\n"
    assert patch(plain) is None


def test_the_guard_config_is_off_limits() -> None:
    path = os.environ["AGENT_GUARD_CONFIG"]
    assert reason(write(path, "{}")).startswith("agent-guard (guard-config-write)")


def test_extra_patterns_from_config(site) -> None:
    site({"secrets": {"extra_token_patterns": [r"\bacme_[0-9a-f]{32}\b"], "extra_file_patterns": [r"(^|/)vault\.hcl$"]}})
    assert denied(write(f"{WT}/a.txt", "acme_" + "0123456789abcdef" * 2))
    assert denied(write(f"{WT}/deploy/vault.hcl", "x"))
