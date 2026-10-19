"""The write rules: no secrets files, no live tokens in files, no edits to agent-guard's own config.

Covers Claude Code's Write/Edit/MultiEdit/NotebookEdit and Codex's apply_patch.
"""

from __future__ import annotations

import os
import re

from . import config as config_mod
from . import secrets
from .messages import t
from .rules import Denial, _is_guard_config

PATCH_FILE = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+?)\s*$|^\*\*\* Move to: (.+?)\s*$")


def check_write(path: str, texts: list[str], cfg: config_mod.Config | None = None, cwd: str | None = None) -> Denial | None:
    cfg = cfg or config_mod.load()
    rel = path or ""
    full = rel if os.path.isabs(rel) or not cwd else os.path.join(cwd, rel)
    if cfg.on("guard-config-write") and full and _is_guard_config(os.path.expanduser(full)):
        return Denial("guard-config-write", t(cfg, "guard_config", path=full))
    if cfg.on("secret-file-write") and secrets.is_secret_file(cfg, rel):
        return Denial("secret-file-write", t(cfg, "secret_file", name=os.path.basename(rel), where=cfg["secrets"]["where"]))
    if cfg.on("token-in-file"):
        test_file = bool(secrets.TEST_PATH.search(rel))
        for text in texts:
            if secrets.real_tokens(cfg, text, test_file=test_file):
                return Denial("token-in-file", t(cfg, "token_in_file", where=cfg["secrets"]["where"]))
    return None


def parse_patch(patch: str) -> dict[str, list[str]]:
    """Codex apply_patch text -> {path: [added lines]} (a moved file is listed under both names)."""
    files: dict[str, list[str]] = {}
    current: str | None = None
    for line in patch.splitlines():
        m = PATCH_FILE.match(line)
        if m:
            current = m.group(2) or m.group(3)
            files.setdefault(current, [])
            continue
        if current and line.startswith("+") and not line.startswith("+++"):
            files[current].append(line[1:])
    return files


def check_patch(patch: str, cfg: config_mod.Config | None = None, cwd: str | None = None) -> Denial | None:
    for path, added in parse_patch(patch).items():
        denial = check_write(path, ["\n".join(added)], cfg, cwd)
        if denial:
            return denial
    return None
