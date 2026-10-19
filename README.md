<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/wordmark-dark.svg">
    <img alt="agent-guard." src="assets/wordmark-light.svg" height="92">
  </picture>
</p>

<p align="center">
  <b>Safety hooks for coding agents.</b><br>
  Refuse the destructive command, hand the agent the safe one.
</p>

<p align="center">
  <a href="https://github.com/ly-solutions/agent-guard/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/ly-solutions/agent-guard/actions/workflows/ci.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="License: Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-blue"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-blue">
  <img alt="Zero dependencies" src="https://img.shields.io/badge/dependencies-0-brightgreen">
</p>

---

Coding agents are good at finishing tasks and bad at noticing what they break on the way.
Left to run without asking, an agent will sooner or later:

- `pkill -f vite`, and kill **its own shell** with it, because `-f` matches the agent's command line too;
- `git push --force` after a rebase and wipe a colleague's commits;
- `rm -rf` a directory it took for a temp folder;
- paste a live token into a `curl` command, a config file or `.env`;
- run a bare `git stash` in a checkout another agent is working in;
- open `psql` into production through `kubectl exec`, lose the stream, and leave a session holding a table lock until the next release fails;
- start a sync loop with `nohup … &` and forget it for days;
- run `pnpm test | tail -20`, read the exit code of `tail`, and report green.

A line in the system prompt does not stop this. An agent that has decided the forbidden command is the fastest way to
finish will run it anyway, and runtimes that do not ask for permission have nothing else in the way.
**A hook does stop it.** agent-guard is a set of `PreToolUse`/`PostToolUse` hooks for
[Claude Code](https://docs.claude.com/en/docs/claude-code/hooks) and [Codex](https://developers.openai.com/codex/hooks)
that reads every shell command and file write before it runs. It then does one of three things:

- **lets it through**, which is what happens to almost every command;
- **rewrites it** into its safe form (`--force` becomes `--force-with-lease`, `pkill` becomes `pkill -A`, a piped test run gets `pipefail`);
- **refuses it** with a reason written for the agent: what went wrong and what to run instead, so its next step is the right one.

Python 3.10+, standard library only, no network. If agent-guard itself fails, it stays silent and the command goes
through, so a bug in the guard never blocks your work.

## Install in 30 seconds

**Claude Code**

```text
/plugin marketplace add ly-solutions/agent-guard
/plugin install agent-guard@agent-guard
```

**Codex**

```sh
codex plugin marketplace add ly-solutions/agent-guard
codex plugin add agent-guard@agent-guard
```

Codex runs plugin hooks only after you review them: open `/hooks` in Codex once and trust agent-guard's entries.

You need `python3` (3.10 or newer) on `PATH`; Linux and macOS are supported. Want to see what it does before installing
it into an agent? Clone the repository and ask it directly:

```console
$ bin/agent-guard check -- git push --force origin main
DENY  agent-guard (force-push): `git push --force` overwrites whatever is on the remote, other people's commits included. After a rebase push with `--force-with-lease`; never force-push to main, master, release, production.

$ bin/agent-guard check -- git push -f origin agent/fix-login
REWRITE
git push --force-with-lease origin agent/fix-login
```

## What it guards

Every rule has a name. You will see it in refusals, and you can switch any rule off by that name ([configuration](#configuration)).

### Refusals and safe rewrites

| Rule | What it does | Why |
| --- | --- | --- |
| `force-push` | Rewrites `--force`/`-f` on a working branch to `--force-with-lease`; refuses force (even with a lease) to `main`, `master`, `release`, `production`; refuses `+refspec` pushes. | A plain force-push overwrites whatever is on the remote. A lease only overwrites what you have actually seen. |
| `pkill-full` | Runs `pkill` as `pkill -A` (procps-ng 4.0.3+), which skips the caller's own ancestors. Where that is not available, or behind `sudo`/`timeout`/`xargs`, `pkill -f` is refused with `pgrep` + `kill <pid>` as the way out. | `pkill -f pattern` also matches the agent's own shell, whose command line contains the pattern. The shell dies (exit 144). |
| `rm-recursive` | `rm -r` is allowed below `/tmp`, `/var/tmp`, `$TMPDIR`, inside `.worktrees/…`, in caches (`~/.cache`, `~/.npm`, …), in build output (`node_modules`, `dist`, `target`, …) and below the directory the command started in. It is refused for everything else, and always for `/`, `~` and workspace roots. | A `cd ..` inside the command moves where relative paths point, not what the session may delete: `cd .. && rm -rf shared-data` is refused. |
| `shared-checkout-write` | *Opt-in* (set `workspace_roots`). No edits, commits, `git stash`, `reset --hard`, `checkout -b` or `sed -i` in a shared primary checkout. Points the agent to a worktree instead. | In a checkout that people and several agents share, a bare `git stash` or a stray edit lands in someone else's work. |
| `agent-branch-delete` | Refuses `git branch -D agent/…` and deleting `agent/…` branches on the remote. | Until it is merged, an agent's branch is the only copy of its work. `git branch -d` still works: it refuses an unmerged branch by itself. |
| `kubectl-mutation` | `apply`, `delete`, `patch`, `edit`, `scale`, `set`, `rollout restart`, `drain` and friends only in test namespaces (`test`, `dev`, `staging`, `preview`, … configurable). Read-only commands pass everywhere. | Production changes made by hand are invisible to GitOps and get reverted, or worse, they don't. |
| `helm-mutation` | `install`, `upgrade`, `uninstall`, `rollback` only in test namespaces. | Same reason: a release installed by hand cannot be reproduced from the repository. |
| `prod-db` | Outside test namespaces, `kubectl exec` into a database must stay inside a server-side envelope. `psql` needs `statement_timeout`, `lock_timeout`, `default_transaction_read_only` and `PGAPPNAME`. `clickhouse-client` needs `--readonly` and `--max_execution_time`. Refused: `pg_dump`, port-forwards to databases, `FLUSHALL`/`KEYS *`/`CONFIG SET` in Redis, Kafka topic deletes and offset resets, hand-run migrations, `COPY … TO STDOUT` through the exec stream. | A psql whose client went away stays alive in the pod and keeps holding its locks. Only the server can put a bound on that. |
| `kill-kubectl-exec` | Killing a local `kubectl exec` is refused until the server-side session is ended. The refusal gives the exact query for that. | Killing the local process leaves the psql in the pod running, with its locks. |
| `literal-token` | Refuses commands that contain a live credential: private keys, Anthropic, OpenAI, GitHub, GitLab, Slack, AWS, Google, Telegram bot, HashiCorp Vault/OpenBao and YouTrack token formats, plus your own patterns. Obvious placeholders (`example`, `xxxxxxxx`, `redacted`) pass. | A token typed into a command stays in the transcript, the shell history and, often, a log. |
| `secret-file-write` | No writes to `.env*` (except `.env.example`/`.sample`/`.template`), `*.pem`, `*.key`, `id_rsa`, `credentials.json`, `secrets.yaml`, `.npmrc`, `.netrc`, `.pgpass`. | Secrets belong in a secret manager or the environment, not in a file an agent edits and may commit. |
| `token-in-file` | No live tokens written into any file. In test fixtures, a file that is clearly fake passes. | Same reason, for the token that ends up in `config.ts`. |
| `guard-config-write` | The agent cannot edit or delete agent-guard's own configuration, whether with an edit tool or from the shell. | A guard the agent can switch off is not a guard. |
| `custom` | Your own refusals: a regular expression and a reason ([example](#configuration)). | Every team has its own `terraform apply`. |

Rules look through what usually hides a command: wrappers (`sudo`, `env`, `timeout`, `nice`, `xargs`, `nohup`, `watch`),
shell keywords (`for … do`, `until`, `if … then`), `$(…)` and backticks, `eval`, `bash -c '…'` and `tsh kubectl`.
Text in a heredoc body or in quotes is not treated as a command, so `echo 'rm -rf ~' > notes.txt` passes.

### Helpers (rewrite, never refuse)

| Rule | What it does |
| --- | --- |
| `pipefail-gates` | A test, lint, typecheck or build command piped into `tail`/`grep`/`head` runs under `set -o pipefail` and prints `[exit code: N]` as its last line, so the agent sees the gate's real result. |
| `clear-proxy` | *Opt-in.* Unsets the HTTP proxy variables for the commands you list (e.g. `kubectl`), for setups where the corporate proxy breaks them. |

### After the command

| Rule | What it does |
| --- | --- |
| `orphaned-db-session` | When a `kubectl exec … psql` into production breaks (timeout, reset, moved to the background), the agent is told right away to find and end the orphaned server session, with the command for it. |
| `background-lease` | *Linux, Claude Code sessions.* A process the agent detaches (`nohup`, `setsid`, `&`) lives 12 hours by default. The session is warned 2 hours before the deadline, and the process with its children is stopped no sooner than an hour after that warning. The agent can extend a process: `agent-bg keep <pid> --hours 48 --why "…"`. Nothing is stopped without a warning first. |

## How a refusal looks

The agent sees the refusal as the result of its tool call and acts on it:

```text
● Bash(rm -rf ../shared-data)
  ⎿  PreToolUse:Bash hook error: agent-guard (rm-recursive): `rm -r` outside scratch space and
     worktrees: /srv/work/shared-data. Recursive deletes are allowed in /tmp, /var/tmp, inside
     a worktree (`.worktrees/…`), in caches, in build output and below the directory the
     command started in; ask a human for anything else.
```

A rewrite is silent. The command that runs is the safe one, and the transcript keeps the one the agent typed:

```console
$ bin/agent-guard check -- 'pnpm vitest run | tail -20'
REWRITE
set -o pipefail
pnpm vitest run | tail -20
__gate_exit=$?; echo "[exit code: $__gate_exit]"; exit $__gate_exit
```

## Configuration

Out of the box, agent-guard needs no configuration. To change something, create
`~/.config/agent-guard/config.toml` (or `config.json`; TOML needs Python 3.11+) and write only the keys you change.
Your file is merged over the built-in defaults. `$AGENT_GUARD_CONFIG` points to a different file.
[`examples/config.toml`](examples/config.toml) lists every key.

```toml
language = "en"                      # or "ru"

[rules]
clear-proxy = false                  # switch any rule off by name

[git]
protected_branches = ["main", "release"]
agent_branch_prefixes = ["agent/", "claude/"]

[kubernetes]
safe_namespace_pattern = "-(dev|test|preview)$"

[task_keys]
pattern = "\\bPROJ-\\d+\\b"          # a declared write to a prod database must carry a task key

[filesystem]
workspace_roots = ["~/work"]         # children of these are shared checkouts; agents use worktrees
worktree_hint = "git worktree add ../.worktrees/<task> -b agent/<task>"

[[custom]]
name = "no-terraform-apply"
pattern = "^terraform (apply|destroy)\\b"
reason = "terraform apply runs from CI only. Run `terraform plan` and open a pull request."
```

| Key | Default | Meaning |
| --- | --- | --- |
| `language` | `"en"` | Language of refusals and advice: `en` or `ru`. |
| `rules.<name>` | all `true` | Switch a rule off. |
| `codex.rewrites` | `"suggest"` | How rewrites work in Codex ([below](#codex)). |
| `git.protected_branches` | `main`, `master`, `release`, `production` | Never force-pushed, not even with a lease. |
| `git.agent_branch_prefixes` | `agent/` | Branches that are never force-deleted. |
| `kubernetes.safe_namespace_pattern` | `(test\|dev\|preview\|stand\|sandbox\|staging\|local)` | Namespaces where kubectl, helm and database rules do not apply. |
| `kubernetes.argocd_namespace` | `argocd` | Where a refresh annotation, or a patch of a test namespace's Application, is allowed. |
| `kubernetes.change_hint` | GitOps sentence | Appended to kubectl/helm refusals: how changes are made at your place. |
| `databases.statement_timeout_max_s` / `lock_timeout_max_s` | `900` / `30` | Ceilings the database envelope accepts. |
| `databases.backup_hint` | sentence | Appended to the `pg_dump` refusal. |
| `task_keys.pattern` | `""` (off) | Regex a declared production write must match (`PGAPPNAME`, `--log_comment`). |
| `filesystem.workspace_roots` | `[]` (off) | Parents of shared checkouts; turns `shared-checkout-write` on. |
| `filesystem.worktree_dir` / `worktree_hint` | `.worktrees` / `git worktree add …` | Where worktrees live; the command suggested instead. `{repo}` is replaced with the checkout name. |
| `filesystem.writable_in_checkout` | `[]` | Paths inside a shared checkout that may still be written to. |
| `filesystem.scratch_roots` / `cache_dirs` / `build_dirs` | `/tmp`, `/var/tmp` / … / … | Where `rm -r` is fine. |
| `secrets.where` | "a secret manager or an environment variable" | Where your secrets live, as told to the agent. |
| `secrets.extra_token_patterns` / `extra_file_patterns` | `[]` | Your own credential formats and secrets file names (regexes). |
| `proxy.clear_for` / `proxy.variables` | `[]` / the `*_PROXY` variables | Commands run with the proxy unset. |
| `gates.extra_pattern` | `""` | More commands that get `pipefail` when piped. |
| `background.ttl_hours` / `warn_hours` / `notice_grace_s` | `12` / `2` / `3600` | Lifetime of detached processes and the warning before a stop. |
| `background.daemon_patterns` | `[]` | Long-lived helpers that are never stopped (regexes on the command line). |
| `background.agent_binaries` / `session_env` | `claude`, `codex` / `CLAUDE_CODE_SESSION_ID` | How agent processes and their sessions are recognised. |
| `custom` | `[]` | `{name, pattern, reason}` entries of your own. |

`agent-guard config` prints the effective configuration, which file it came from, and any key it does not recognise.

There is no per-repository configuration on purpose: a repository the agent works in must not be able to switch off the
guards that protect you from that agent.

## Codex

The same plugin works in Codex, which sends Claude Code's hook payload. Two differences are handled for you:

- **Rewrites.** Codex applies a rewritten command only together with `permissionDecision: "allow"`, and "allow" also skips
  Codex's approval prompt. agent-guard does not approve anything on your behalf, so by default (`codex.rewrites = "suggest"`)
  a safety rewrite becomes a refusal that carries the safe command ("Run this instead: …"). The convenience helpers
  (`pipefail-gates`, `clear-proxy`) are skipped. Set `codex.rewrites = "allow"` if you want in-place rewrites anyway.
- **File edits.** Codex edits files with `apply_patch`; agent-guard reads the patch and applies the write rules to every
  file in it, renames included.

The background lease tracks processes by the session marker Claude Code puts in their environment. Codex leaves no
such marker, so processes started by Codex are not tracked.

### Wiring hooks by hand

Without the plugin system, point your agent's hook configuration at a clone. For Claude Code, add this to
`~/.claude/settings.json`; for Codex, the same `hooks` block goes into `~/.codex/hooks.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Bash", "hooks": [{ "type": "command", "command": "python3 /opt/agent-guard/hooks/run.py pre-bash", "timeout": 5 }] },
      { "matcher": "Write|Edit|MultiEdit|NotebookEdit|apply_patch", "hooks": [{ "type": "command", "command": "python3 /opt/agent-guard/hooks/run.py pre-write", "timeout": 5 }] }
    ],
    "PostToolUse": [
      { "matcher": "Bash", "hooks": [{ "type": "command", "command": "python3 /opt/agent-guard/hooks/run.py post-bash", "timeout": 5 }] }
    ],
    "SessionStart": [{ "hooks": [{ "type": "command", "command": "python3 /opt/agent-guard/hooks/run.py session", "timeout": 5 }] }],
    "UserPromptSubmit": [{ "hooks": [{ "type": "command", "command": "python3 /opt/agent-guard/hooks/run.py session", "timeout": 5 }] }]
  }
}
```

Any other agent that can run a command before a tool call and pass it the event as JSON on stdin can use the same entry
points. Each one reads `{"tool_name", "tool_input", "cwd", …}` and prints a Claude Code-style answer, or nothing.

## Background processes

```console
$ agent-bg list                                  # detached agent processes, age, time left, owning session
$ agent-bg keep 41235 --hours 48 --why "nightly import until Friday"
$ agent-bg keep 41235 --forever --why "local docs server"
$ agent-bg stop 41235                            # with its children
$ agent-bg sweep --dry-run                       # what the next sweep would warn about or stop
```

The sweep starts on its own from the session hooks, at most once every 10 minutes per machine user, so there is no cron
entry to install. To give a process a longer life when you start it, set `AGENT_BG_TTL=<hours>` in its environment, or
`AGENT_BG_KEEP=<reason>` for no limit at all. `bin/` holds the `agent-guard` and `agent-bg` commands. Put them on your
`PATH` (`ln -s "$PWD/bin/agent-bg" ~/.local/bin/`), or `pip install .` to get both.

## FAQ

**Why hooks and not permissions?**
Permission rules match command prefixes: `Bash(git push:*)` is allowed or it is not. A prefix cannot tell
`git push --force origin agent/fix` from `git push --force origin main`. It cannot see `kubectl delete` behind
`timeout 30`, inside a `for` loop or in `$(…)`. It cannot check that a `psql` carries a `statement_timeout`, and it cannot
turn a dangerous command into a safe one. Many teams also run agents with permissions bypassed or auto-approved,
and then nothing asks at all. Hooks run in every mode. Use both: permissions for coarse allow-lists, agent-guard for
what the command actually does.

**Is this a sandbox?**
No. agent-guard is a seatbelt against honest mistakes, not a jail for a hostile agent. An agent that wants around it can
write a script to a file and run that. For isolation, run agents in a container or VM. agent-guard covers the
mistakes that still happen inside one.

**What if agent-guard has a bug?**
It fails open. Any error inside a hook (bad input, a broken config, an unexpected tool) produces no output, and the
command runs as if agent-guard were not there. A broken config file falls back to the defaults and is reported on stderr.

**How much does it slow the agent down?**
Each tool call starts one short Python process. No network, no dependencies, no daemon.

**Will it refuse things I actually want?**
Sometimes. That is why every refusal names its rule and says what to do instead. If a rule does not fit your setup,
switch it off or tune its pattern. Bug reports with the exact command are very welcome.

**Can the agent switch agent-guard off?**
Not through its tools: the config is outside the repository, and the `guard-config-write` rule refuses edits to it from
both the edit tools and the shell.

## Development

```sh
python -m pytest            # stdlib + pytest
ruff check . && ruff format --check .
```

See [CONTRIBUTING.md](CONTRIBUTING.md). Security issues go through
[private vulnerability reporting](https://github.com/ly-solutions/agent-guard/security/advisories/new), see
[SECURITY.md](SECURITY.md).

## License

[Apache-2.0](LICENSE) © Ly Solutions
