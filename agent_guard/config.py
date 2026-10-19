"""Configuration: built-in defaults merged with one user-level file.

The file is looked up in this order and the first one found wins:

1. ``$AGENT_GUARD_CONFIG`` (a ``.toml`` or ``.json`` path);
2. ``$XDG_CONFIG_HOME/agent-guard/config.toml`` (``~/.config/agent-guard/config.toml``);
3. ``$XDG_CONFIG_HOME/agent-guard/config.json``.

There is deliberately no per-repository config: a repository the agent is working in
must not be able to switch the guards off. TOML needs Python 3.11+ (``tomllib``); on
3.10 use the JSON form, or install ``tomli``.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from typing import Any

RULES = (
    "pkill-full",
    "force-push",
    "agent-branch-delete",
    "rm-recursive",
    "shared-checkout-write",
    "kubectl-mutation",
    "helm-mutation",
    "prod-db",
    "kill-kubectl-exec",
    "literal-token",
    "secret-file-write",
    "token-in-file",
    "guard-config-write",
    "custom",
    "pipefail-gates",
    "clear-proxy",
    "orphaned-db-session",
    "background-lease",
)

DEFAULTS: dict[str, Any] = {
    "language": "en",
    "rules": {name: True for name in RULES},
    "codex": {
        # Codex accepts a rewritten command only together with permissionDecision "allow",
        # which also skips its approval prompt. "suggest" turns a safety rewrite into a
        # refusal that carries the safe command; "allow" rewrites in place.
        "rewrites": "suggest",
    },
    "git": {
        "protected_branches": ["main", "master", "release", "production"],
        "agent_branch_prefixes": ["agent/"],
    },
    "kubernetes": {
        "safe_namespace_pattern": "(test|dev|preview|stand|sandbox|staging|local)",
        "argocd_namespace": "argocd",
        "change_hint": "Production changes go through a commit to your GitOps repository; by hand, only test namespaces.",
    },
    "databases": {
        "statement_timeout_max_s": 900,
        "lock_timeout_max_s": 30,
        "backup_hint": "Take the copy from your backup job instead.",
    },
    "task_keys": {
        # A regular expression such as "\\b(PROJ|OPS)-\\d+\\b". Empty: off.
        "pattern": "",
    },
    "filesystem": {
        # Directories whose direct children are shared primary checkouts (people and other
        # agents work in them). Empty: the shared-checkout rule is inert.
        "workspace_roots": [],
        "worktree_dir": ".worktrees",
        "worktree_hint": "git worktree add ../.worktrees/<task> -b agent/<task>",
        "writable_in_checkout": [],
        "scratch_roots": ["/tmp", "/var/tmp"],
        "cache_dirs": [".cache", ".npm", ".gradle", "go/pkg", ".local/share/pnpm", ".cargo/registry", ".m2/repository"],
        "build_dirs": [
            "node_modules",
            ".pnpm-store",
            "dist",
            "build",
            ".nx",
            ".angular",
            ".next",
            "coverage",
            ".turbo",
            ".playwright-mcp",
            "target",
            "out",
            "tmp",
        ],
    },
    "secrets": {
        "where": "a secret manager or an environment variable",
        "extra_token_patterns": [],
        "extra_file_patterns": [],
    },
    "proxy": {
        # Commands that must not go through an HTTP proxy, e.g. ["kubectl", "helm"]. Empty: off.
        "clear_for": [],
        "variables": ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"],
    },
    "gates": {
        # Extra regular expression for commands whose exit code matters (tests, lint, build).
        "extra_pattern": "",
    },
    "background": {
        "ttl_hours": 12.0,
        "warn_hours": 2.0,
        "notice_grace_s": 3600,
        "sweep_every_s": 600,
        "daemon_patterns": [],
        "agent_binaries": ["claude", "codex"],
        "session_env": "CLAUDE_CODE_SESSION_ID",
    },
    # Your own refusals: [{"name": "...", "pattern": "<regex on one command>", "reason": "..."}]
    "custom": [],
}


def config_paths() -> list[str]:
    explicit = os.environ.get("AGENT_GUARD_CONFIG")
    if explicit:
        return [os.path.abspath(os.path.expanduser(explicit))]
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    folder = os.path.join(base, "agent-guard")
    return [os.path.join(folder, "config.toml"), os.path.join(folder, "config.json")]


def _parse(path: str) -> dict[str, Any]:
    with open(path, "rb") as f:
        raw = f.read()
    if path.endswith(".json"):
        return json.loads(raw.decode())
    try:
        import tomllib  # type: ignore[import-not-found]
    except ModuleNotFoundError:  # Python 3.10
        try:
            import tomli as tomllib  # type: ignore[import-not-found, no-redef]
        except ModuleNotFoundError as exc:
            raise RuntimeError(f"{path}: TOML needs Python 3.11+ or the tomli package; use config.json instead") from exc
    return tomllib.loads(raw.decode())


def merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def unknown_keys(user: dict[str, Any], base: dict[str, Any] = DEFAULTS, prefix: str = "") -> list[str]:
    out = []
    for key, value in user.items():
        if key not in base:
            out.append(prefix + key)
        elif isinstance(value, dict) and isinstance(base[key], dict) and key != "rules":
            out += unknown_keys(value, base[key], prefix + key + ".")
        elif key == "rules" and isinstance(value, dict):
            out += [f"rules.{name}" for name in value if name not in RULES]
    return out


class Config(dict):
    """The merged configuration plus where it came from."""

    source: str | None = None

    def on(self, rule: str) -> bool:
        return bool(self["rules"].get(rule, True))


_CACHE: dict[str, Config] = {}


def load(fresh: bool = False) -> Config:
    """Defaults merged with the first config file found. A broken file falls back to defaults (and says so on stderr)."""
    paths = config_paths()
    key = "|".join(paths)
    if not fresh and key in _CACHE:
        return _CACHE[key]
    cfg = Config(copy.deepcopy(DEFAULTS))
    for path in paths:
        if not os.path.isfile(path):
            continue
        try:
            cfg = Config(merge(DEFAULTS, _parse(path)))
            cfg.source = path
        except Exception as exc:  # noqa: BLE001 - a broken config must not break the agent
            print(f"agent-guard: ignoring {path}: {exc}", file=sys.stderr)
        break
    _CACHE[key] = cfg
    return cfg
