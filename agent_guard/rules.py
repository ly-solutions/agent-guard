"""The shell rules. Each takes one top-level command and returns a reason to refuse, or None.

A hook is the only gate that holds in a runtime that does not ask for permission: a
prohibition written in a prompt does not stop an agent that has decided the prohibited
thing is the fastest way to finish its task.
"""

from __future__ import annotations

import os
import re
from typing import NamedTuple

from . import config as config_mod
from . import secrets
from .messages import t
from .shell import Words, git_sub, namespace, split_segments, substitutions, words


class Denial(NamedTuple):
    rule: str
    reason: str


class Ctx:
    """What every rule may look at besides its own command."""

    def __init__(self, cfg: config_mod.Config, session_root: str | None, full_command: str, pkill_shim: bool = False):
        self.cfg = cfg
        self.session_root = session_root
        self.full_command = full_command
        # True when the Bash hook will run the command with the `pkill -A` shim.
        self.pkill_shim = pkill_shim


# --------------------------------------------------------------------------- processes

PKILL_FULL = re.compile(r"^-[A-Za-z]*f[A-Za-z]*$")


def pkill_full(w: Words, _cwd: str | None, ctx: Ctx) -> str | None:
    """`pkill -f` called as itself runs through the shim (`pkill -A` spares the agent's shell); behind a wrapper it would not."""
    tokens = w.tokens
    if tokens[:1] != ["pkill"] or not any(a == "--full" or PKILL_FULL.match(a) for a in tokens[1:]):
        return None
    if ctx.pkill_shim and not w.behind_wrapper:
        return None
    return t(ctx.cfg, "pkill")


KUBECTL_PROCESS = re.compile(r"kubectl\b|\bexec -i\b")


def kill_kubectl_exec(w: Words, _cwd: str | None, ctx: Ctx) -> str | None:
    tokens = w.tokens
    killer = tokens[0] in ("kill", "pkill", "killall") or (tokens[0] == "xargs" and "kill" in tokens)
    if killer and KUBECTL_PROCESS.search(ctx.full_command):
        return t(ctx.cfg, "kill_kubectl_exec")
    return None


# --------------------------------------------------------------------------- git


def _protected(cfg: dict) -> re.Pattern[str]:
    names = "|".join(re.escape(b) for b in cfg["git"]["protected_branches"]) or "(?!)"
    return re.compile(rf"(^|[:+/])({names})$")


def force_push(w: Words, _cwd: str | None, ctx: Ctx) -> str | None:
    sub, args = git_sub(w.tokens)
    if sub != "push":
        return None
    lease = any(a.startswith("--force-with-lease") or a == "--force-if-includes" for a in args)
    hard = any(a == "--force" or (re.match(r"^-[a-zA-Z]+$", a) and "f" in a) for a in args)
    hard = hard or any(a.startswith("+") for a in args if not a.startswith("-"))
    protected = any(_protected(ctx.cfg).search(a) for a in args if not a.startswith("-"))
    if hard:
        return t(ctx.cfg, "force_push", protected=", ".join(ctx.cfg["git"]["protected_branches"]) or "protected branches")
    if lease and protected:
        return t(ctx.cfg, "force_protected")
    return None


def agent_branch_delete(w: Words, _cwd: str | None, ctx: Ctx) -> str | None:
    prefixes = tuple(ctx.cfg["git"]["agent_branch_prefixes"])
    if not prefixes:
        return None
    sub, args = git_sub(w.tokens)
    names = [a for a in args if not a.startswith("-")]
    if sub == "branch":
        force = "-D" in args or ("--delete" in args and ("--force" in args or "-f" in args)) or ("-d" in args and "-f" in args)
        if force and any(n.startswith(prefixes) for n in names):
            return t(ctx.cfg, "branch_local")
    if sub == "push" and ("--delete" in args or "-d" in args or any(n.startswith(":") and n[1:].startswith(prefixes) for n in names)):
        if any(n.lstrip(":").startswith(prefixes) for n in names):
            return t(ctx.cfg, "branch_remote")
    return None


# --------------------------------------------------------------------------- kubernetes and databases

KUBE_MUTATING = {
    "apply",
    "delete",
    "patch",
    "edit",
    "replace",
    "scale",
    "create",
    "set",
    "label",
    "annotate",
    "rollout",
    "drain",
    "cordon",
    "uncordon",
    "taint",
    "expose",
    "autoscale",
}
KUBE_VALUE_FLAGS = {"-n", "--namespace", "--context", "--kubeconfig", "--cluster", "--user", "-s", "--server", "--token", "--as"}
MIGRATION = re.compile(
    r"\b(migrate|migration|flyway|liquibase|alembic|goose|db:migrate|db push)\b"
    r"|\b(alter|drop|truncate)\s+(table|index|schema|database|column)\b"
    r"|\bcreate\s+(table|index|schema|database)\b|\bpg_restore\b",
    re.I,
)
STDOUT_EXPORT = re.compile(r"\\?copy\b[^;\n]*\bto\s+stdout\b", re.I)
DB_TARGET = re.compile(r"(postgres|pgsql|clickhouse|redis|valkey|kafka|mongo|mysql|mariadb)", re.I)
PG_DUMP = re.compile(r"(^|[\s/;|&'\"])(pg_dump|pg_dumpall)(\s|$)")
PSQL = re.compile(r"(^|[\s/;|&'\"])psql(\s|$)")
CLICKHOUSE = re.compile(r"(^|[\s/;|&'\"])clickhouse(-client|\s+client)(\s|$)")
REDIS_CLI = re.compile(r"(^|[\s/;|&'\"])(redis-cli|valkey-cli)(\s|$)")
REDIS_DANGER = re.compile(r"\b(flushall|flushdb|swapdb|shutdown|debug\s+\w+|config\s+(set|rewrite|resetstat)|script\s+flush)\b|\bkeys\s+\S", re.I)
KAFKA_CLI = re.compile(r"\bkafka-[\w-]+(\.sh)?\b")
KAFKA_DANGER = re.compile(r"--delete\b|--reset-offsets\b|--alter\b|\bkafka-delete-records|\bkafka-reassign-partitions|--delete-offsets\b")
PSQL_ENVELOPE = (
    "env PGAPPNAME=agent-<task> PGOPTIONS='-c statement_timeout=60s -c lock_timeout=3s "
    "-c idle_in_transaction_session_timeout=60s -c default_transaction_read_only=on' "
    'sh -c \'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select ..."\''
)
CLICKHOUSE_ENVELOPE = 'clickhouse-client --readonly=2 --max_execution_time=60 -q "select ..."'


def safe_namespace(cfg: dict, ns: str | None) -> bool:
    pattern = cfg["kubernetes"]["safe_namespace_pattern"]
    return bool(ns and pattern and re.search(pattern, ns, re.I))


def _where(cfg: dict, ns: str | None) -> str:
    return t(cfg, "where_ns", ns=ns) if ns else t(cfg, "where_default_ns")


def _task_key_declared(cfg: dict, text: str) -> bool:
    pattern = cfg["task_keys"]["pattern"]
    return not pattern or bool(re.search(pattern, text))


def _pg_setting_seconds(name: str, text: str) -> float | None:
    """Value of a Postgres timeout as written in PGOPTIONS or a SET; None when absent."""
    m = re.search(name + r"\s*(?:=|\bto\b)?\s*['\"]?(\d+)\s*(ms|s|min|h)?", text, re.I)
    if not m:
        return None
    return int(m.group(1)) * {"ms": 0.001, "s": 1, "min": 60, "h": 3600}[(m.group(2) or "ms").lower()]


def psql_envelope(cfg: dict, text: str, where: str) -> str | None:
    """A psql session in a production database must be bounded by the SERVER, not by the client.

    An export through `kubectl exec … psql -f -` that loses its client leaves the psql in the
    pod alive, holding its locks, until somebody notices; the next release's migration then
    waits behind it. A server-side statement_timeout ends such a session by itself, read-only
    mode makes SQL fed through stdin (which this guard cannot see) harmless, and the
    application name says whose session it is.
    """
    db = cfg["databases"]
    statement_max, lock_max = db["statement_timeout_max_s"], db["lock_timeout_max_s"]
    missing = []
    statement = _pg_setting_seconds("statement_timeout", text)
    lock = _pg_setting_seconds("lock_timeout", text)
    read_only = re.search(r"default_transaction_read_only\s*(?:=|\bto\b)\s*['\"]?(on|off|true|false|1|0)", text, re.I)
    if not statement or statement > statement_max:
        missing.append(t(cfg, "psql_missing_statement", max=statement_max))
    if not lock or lock > lock_max:
        missing.append(t(cfg, "psql_missing_lock", max=lock_max))
    if not read_only:
        missing.append(t(cfg, "psql_missing_ro"))
    if not re.search(r"application_name\s*=|PGAPPNAME=", text):
        missing.append(t(cfg, "psql_missing_name"))
    if missing:
        return t(cfg, "psql_envelope", where=where, missing=", ".join(missing), envelope=PSQL_ENVELOPE)
    if read_only.group(1).lower() in ("off", "false", "0") and not _task_key_declared(cfg, text):
        return t(cfg, "psql_write", where=where)
    return None


def clickhouse_envelope(cfg: dict, text: str, where: str) -> str | None:
    limit_max = cfg["databases"]["statement_timeout_max_s"]
    limit = re.search(r"max_execution_time\s*[= ]\s*['\"]?(\d+)", text)
    mode = re.search(r"readonly\s*[= ]\s*['\"]?([012])", text)
    missing = []
    if not limit or not 0 < int(limit.group(1)) <= limit_max:
        missing.append(t(cfg, "ch_missing_time", max=limit_max))
    if not mode:
        missing.append(t(cfg, "ch_missing_ro"))
    if missing:
        return t(cfg, "ch_envelope", where=where, missing=", ".join(missing), envelope=CLICKHOUSE_ENVELOPE)
    if mode.group(1) == "0" and not _task_key_declared(cfg, text):
        return t(cfg, "ch_write", where=where)
    return None


def exec_into_prod(cfg: dict, text: str, where: str) -> str | None:
    if PG_DUMP.search(text):
        return t(cfg, "pg_dump", where=where, hint=cfg["databases"]["backup_hint"])
    if PSQL.search(text):
        return psql_envelope(cfg, text, where)
    if CLICKHOUSE.search(text):
        return clickhouse_envelope(cfg, text, where)
    if REDIS_CLI.search(text) and REDIS_DANGER.search(text):
        return t(cfg, "redis", where=where)
    if KAFKA_CLI.search(text) and KAFKA_DANGER.search(text):
        return t(cfg, "kafka", where=where)
    return None


def _kubectl(w: Words) -> list[str] | None:
    tokens = w.tokens
    if tokens[:2] == ["tsh", "kubectl"]:
        tokens = tokens[1:]
    return tokens[1:] if tokens[:1] == ["kubectl"] else None


def _kube_verbs(args: list[str]) -> list[str]:
    verbs, skip = [], False
    for a in args:
        if skip:
            skip = False
        elif a in KUBE_VALUE_FLAGS:
            skip = True
        elif a == "--":
            break
        elif not a.startswith("-"):
            verbs.append(a)
    return verbs


def kubectl_mutation(w: Words, _cwd: str | None, ctx: Ctx) -> str | None:
    args = _kubectl(w)
    if args is None:
        return None
    cfg = ctx.cfg
    verbs = _kube_verbs(args)
    verb = verbs[0] if verbs else ""
    ns = namespace(args)
    if safe_namespace(cfg, ns):
        return None
    argocd = cfg["kubernetes"]["argocd_namespace"]
    if argocd and ns == argocd:
        # Asking Argo CD to refresh, or patching an Application of a test namespace, is not a production change.
        if verb == "annotate" and all(a.startswith("argocd.argoproj.io/refresh=") for a in verbs[3:] if "=" in a):
            return None
        if verb == "patch" and len(verbs) > 2 and safe_namespace(cfg, verbs[2]):
            return None
    if verb == "rollout" and not ({"restart", "undo", "pause", "resume"} & set(verbs[1:2])):
        return None
    if verb in KUBE_MUTATING:
        return t(cfg, "kubectl", verb=verb, where=_where(cfg, ns), hint=cfg["kubernetes"]["change_hint"])
    return None


def prod_db(w: Words, _cwd: str | None, ctx: Ctx) -> str | None:
    args = _kubectl(w)
    if args is None:
        return None
    cfg = ctx.cfg
    verbs = _kube_verbs(args)
    verb = verbs[0] if verbs else ""
    ns = namespace(args)
    if safe_namespace(cfg, ns):
        return None
    where = _where(cfg, ns)
    joined = " ".join(args)
    if verb == "exec" and MIGRATION.search(joined):
        return t(cfg, "kube_migration", where=where)
    if verb == "exec" and "psql" in joined and STDOUT_EXPORT.search(ctx.full_command):
        return t(cfg, "kube_copy_stdout", where=where)
    if verb == "exec":
        inner = args[args.index("--") + 1 :] if "--" in args else args
        return exec_into_prod(cfg, " ".join(inner), where)
    if verb == "port-forward" and any(DB_TARGET.search(v) for v in verbs[1:]):
        return t(cfg, "port_forward_db", where=where, envelope=PSQL_ENVELOPE)
    return None


HELM_MUTATING = {"install", "upgrade", "uninstall", "delete", "rollback"}
HELM_VALUE_FLAGS = {
    "-n",
    "--namespace",
    "--kube-context",
    "--kubeconfig",
    "-f",
    "--values",
    "--set",
    "--set-string",
    "--set-file",
    "--version",
    "--repo",
    "--timeout",
}


def helm_mutation(w: Words, _cwd: str | None, ctx: Ctx) -> str | None:
    if w.tokens[:1] != ["helm"]:
        return None
    args = w.tokens[1:]
    verbs, skip = [], False
    for a in args:
        if skip:
            skip = False
        elif a in HELM_VALUE_FLAGS:
            skip = True
        elif not a.startswith("-"):
            verbs.append(a)
    ns = namespace(args)
    if not verbs or verbs[0] not in HELM_MUTATING or safe_namespace(ctx.cfg, ns):
        return None
    return t(ctx.cfg, "helm", verb=verbs[0], where=_where(ctx.cfg, ns), hint=ctx.cfg["kubernetes"]["change_hint"])


# --------------------------------------------------------------------------- files


def _home() -> str:
    return os.path.expanduser("~")


def _workspaces(cfg: dict) -> list[str]:
    return [os.path.normpath(os.path.expanduser(p)) for p in cfg["filesystem"]["workspace_roots"] if p]


def _scratch_roots(cfg: dict) -> list[str]:
    roots = list(cfg["filesystem"]["scratch_roots"])
    if os.environ.get("TMPDIR"):
        roots.append(os.environ["TMPDIR"])
    return [os.path.normpath(os.path.expanduser(r)) for r in roots if r]


def _build_dirs(cfg: dict) -> re.Pattern[str]:
    names = "|".join(re.escape(d) for d in cfg["filesystem"]["build_dirs"]) or "(?!)"
    return re.compile(rf"/({names})(/|$)")


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _allowed_rm(cfg: dict, target: str, cwd: str | None, session_root: str | None) -> bool:
    home = _home()
    if re.search(r"\$|`", target):
        return True
    path = os.path.expanduser(target)
    if not os.path.isabs(path):
        if cwd is None:
            return True
        path = os.path.join(cwd or os.getcwd(), path)
    head = re.split(r"[*?\[]", path, maxsplit=1)[0]
    norm = os.path.normpath(head) if head else "/"
    workspaces = _workspaces(cfg)
    if norm in ("/", home) or any(norm in (ws, os.path.dirname(ws)) for ws in workspaces):
        return False
    for root in _scratch_roots(cfg):
        if norm == root:
            return False
        if _under(norm, root):
            return True
    wt = cfg["filesystem"]["worktree_dir"]
    if wt and f"/{wt}/" in norm + "/" and not norm.endswith("/" + wt):
        return True
    if any(_under(norm, os.path.join(home, d)) for d in cfg["filesystem"]["cache_dirs"]):
        return True
    if _build_dirs(cfg).search(norm[len(home) :] if norm.startswith(home) else norm):
        return True
    # The directory the command started in: a `cd ..` inside the command moves where relative
    # paths point, not what the session may delete (`cd <parent> && rm -rf shared-data`).
    root_norm = os.path.normpath(session_root) if session_root else ""
    in_workspace = any(_under(root_norm, ws) for ws in workspaces)
    return bool(root_norm) and not in_workspace and root_norm not in ("/", home) and norm.startswith(root_norm + "/")


REDIRECT = re.compile(r"^\d*[<>]|^&>|>")


def rm_recursive(w: Words, cwd: str | None, ctx: Ctx) -> str | None:
    tokens = w.tokens
    if tokens[:1] != ["rm"]:
        return None
    flags = [a for a in tokens[1:] if a.startswith("-")]
    if not any(a == "--recursive" or (re.match(r"^-[a-zA-Z]+$", a) and ("r" in a or "R" in a)) for a in flags):
        return None
    targets = [a for a in tokens[1:] if not a.startswith("-") and not REDIRECT.match(a)]
    bad = [x for x in targets if not _allowed_rm(ctx.cfg, x, cwd, ctx.session_root)]
    if not bad:
        return None
    fs = ctx.cfg["filesystem"]
    return t(ctx.cfg, "rm", targets=", ".join(bad[:3]), scratch=", ".join(fs["scratch_roots"]), worktree_dir=fs["worktree_dir"])


GIT_WRITES = {"commit", "apply", "am", "merge", "rebase", "cherry-pick", "revert", "mv", "rm", "restore"}
FILE_WRITERS = {"touch", "mkdir", "truncate", "rm", "rmdir", "unlink"}


def _resolve(target: str, cwd: str | None) -> str | None:
    if re.search(r"\$|`|^-", target) or target in ("/dev/null", "/dev/stdout", "/dev/stderr"):
        return None
    path = os.path.expanduser(target)
    if os.path.isabs(path):
        return path
    return os.path.join(cwd, path) if cwd else None


def write_targets(tokens: list[str]) -> list[str]:
    """Paths a command writes to: redirections, `sed -i`, `tee`, `cp`/`mv` destinations, `touch`, `rm`, ..."""
    head, args = tokens[0], tokens[1:]
    plain = [a for a in args if not a.startswith("-") and not re.search(r"[<>()]", a)]
    out: list[str] = []
    for i, a in enumerate(tokens):
        if a in (">", ">>", "1>", "1>>") and i + 1 < len(tokens):
            out.append(tokens[i + 1])
        elif re.match(r"^1?>>?[^>&]", a):
            out.append(re.sub(r"^1?>>?", "", a))
    if head == "sed" and any(a == "--in-place" or re.match(r"^-[a-zA-Z]*i", a) for a in args):
        out += plain if any(a in ("-e", "-f") for a in args) else plain[1:]
    elif head == "perl" and any(re.match(r"^-[a-zA-Z]*i", a) for a in args):
        out += plain[1:]
    elif head == "tee":
        out += plain
    elif head in ("mv", "cp", "ln", "install", "rsync") and plain:
        out.append(plain[-1])
    elif head == "chmod":
        out += plain[1:]
    elif head in FILE_WRITERS:
        out += plain
    return out


def shared_checkout(cfg: dict, path: str) -> tuple[str, str] | None:
    """(repo name, path inside it) when `path` lies in a shared primary checkout under a workspace root."""
    norm = os.path.normpath(path)
    for ws in _workspaces(cfg):
        if not norm.startswith(ws + "/"):
            continue
        first = norm[len(ws) + 1 :].split("/", 1)[0]
        if first.startswith(".") or not os.path.exists(os.path.join(ws, first, ".git")):
            continue
        return first, norm[len(ws) + 1 + len(first) :]
    return None


def _writable_inside(cfg: dict, inside: str) -> bool:
    allowed = [".git/"] + [p.rstrip("/") + "/" for p in cfg["filesystem"]["writable_in_checkout"]]
    rel = inside.lstrip("/") + "/"
    return any(rel.startswith(p) for p in allowed) or bool(_build_dirs(cfg).search(inside))


def shared_checkout_write(w: Words, cwd: str | None, ctx: Ctx) -> str | None:
    """Edit tools are covered by the write hook; this sees the same writes made from the shell."""
    cfg = ctx.cfg
    if not _workspaces(cfg):
        return None
    tokens = w.tokens
    targets = [_resolve(x, cwd) for x in write_targets(tokens)]
    sub, args = git_sub(tokens)
    writes_tree = (
        sub in GIT_WRITES
        or (sub == "reset" and "--hard" in args)
        or (sub == "stash" and (not args or args[0] in ("pop", "apply", "push", "drop", "clear")))
        or (sub in ("checkout", "switch") and any(a in ("-b", "-c", "--") for a in args))
    )
    if sub == "checkout" and "--" in args:
        paths = [p for p in args[args.index("--") + 1 :] if p]
        if paths and all(_writable_inside(cfg, p) for p in paths):
            writes_tree = False
    if writes_tree:
        place = cwd
        if "-C" in tokens[:4]:
            place = _resolve(tokens[tokens.index("-C") + 1], cwd)
        targets.append(os.path.join(place, ".") if place else None)
    for target in targets:
        hit = shared_checkout(cfg, target) if target else None
        if hit and not _writable_inside(cfg, hit[1]):
            repo = hit[0]
            return t(cfg, "shared_checkout", repo=repo, hint=cfg["filesystem"]["worktree_hint"].replace("{repo}", repo))
    return None


def _is_guard_config(path: str) -> bool:
    norm = os.path.normpath(path)
    for cfg_path in config_mod.config_paths():
        if norm == os.path.normpath(cfg_path):
            return True
        folder = os.path.dirname(cfg_path)
        if os.path.basename(folder) == "agent-guard" and _under(norm, folder):
            return True
    return False


def guard_config_write(w: Words, cwd: str | None, ctx: Ctx) -> str | None:
    for target in write_targets(w.tokens):
        path = _resolve(target, cwd)
        if path and _is_guard_config(path):
            return t(ctx.cfg, "guard_config", path=path)
    return None


def custom(w: Words, _cwd: str | None, ctx: Ctx) -> str | None:
    text = " ".join(w.tokens)
    for item in ctx.cfg.get("custom") or []:
        try:
            if item.get("pattern") and re.search(item["pattern"], text):
                return str(item.get("reason") or f"refused by the custom rule {item.get('name') or item['pattern']}")
        except re.error:
            continue
    return None


BASH_RULES = (
    ("custom", custom),
    ("pkill-full", pkill_full),
    ("force-push", force_push),
    ("kubectl-mutation", kubectl_mutation),
    ("prod-db", prod_db),
    ("helm-mutation", helm_mutation),
    ("kill-kubectl-exec", kill_kubectl_exec),
    ("rm-recursive", rm_recursive),
    ("agent-branch-delete", agent_branch_delete),
    ("guard-config-write", guard_config_write),
    ("shared-checkout-write", shared_checkout_write),
)


def _cd(tokens: list[str], cwd: str | None) -> str | None:
    if len(tokens) < 2 or tokens[1] == "-":
        return cwd
    if re.search(r"\$|`", tokens[1]):
        return None
    if cwd is None and not os.path.isabs(os.path.expanduser(tokens[1])):
        return None
    target = os.path.expanduser(tokens[1])
    return os.path.normpath(target if os.path.isabs(target) else os.path.join(cwd or os.getcwd(), target))


def literal_token(cfg: config_mod.Config, command: str) -> str | None:
    if secrets.real_tokens(cfg, command):
        return t(cfg, "literal_token")
    return None


def check_bash(command: str, cwd: str | None, cfg: config_mod.Config | None = None, pkill_shim: bool = False) -> Denial | None:
    """The first rule that refuses `command` (run from `cwd`), or None."""
    cfg = cfg or config_mod.load()
    ctx = Ctx(cfg=cfg, session_root=cwd, full_command=command, pkill_shim=pkill_shim)
    if cfg.on("literal-token"):
        reason = literal_token(cfg, command)
        if reason:
            return Denial("literal-token", reason)
    return _check(command, cwd, ctx)


def _check(command: str, cwd: str | None, ctx: Ctx) -> Denial | None:
    for segment in split_segments(command):
        w = words(segment)
        tokens = w.tokens
        if not tokens:
            continue
        if tokens[0] in ("cd", "pushd"):
            cwd = _cd(tokens, cwd)
            continue
        if tokens[0] in ("bash", "sh", "zsh") and len(tokens) >= 3 and tokens[1] in ("-c", "-lc", "-ec"):
            # An exported shell function reaches a child bash, not sh or zsh.
            saved = ctx.pkill_shim
            ctx.pkill_shim = saved and tokens[0] == "bash" and not w.behind_wrapper
            denial = _check(tokens[2], cwd, ctx)
            ctx.pkill_shim = saved
            if denial:
                return denial
            continue
        if tokens[0] == "eval" and len(tokens) >= 2:
            denial = _check(" ".join(tokens[1:]), cwd, ctx)
            if denial:
                return denial
            continue
        for inner in substitutions(segment):
            denial = _check(inner, cwd, ctx)
            if denial:
                return denial
        for name, rule in BASH_RULES:
            if not ctx.cfg.on(name):
                continue
            reason = rule(w, cwd, ctx)
            if reason:
                if name == "custom":
                    name = _custom_name(ctx.cfg, w)
                return Denial(name, reason)
    return None


def _custom_name(cfg: dict, w: Words) -> str:
    text = " ".join(w.tokens)
    for item in cfg.get("custom") or []:
        try:
            if item.get("pattern") and re.search(item["pattern"], text):
                return str(item.get("name") or "custom")
        except re.error:
            continue
    return "custom"
