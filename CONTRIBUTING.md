# Contributing to agent-guard

Thanks for helping. agent-guard sits between an agent and a shell, so a contribution has to clear two bars:
it must stop a real mistake, and it must not get in the way of everything else.

## Reporting a false positive or a miss

The most useful report is the **exact command** (or the file write), the directory it ran from, what agent-guard did,
and what you expected. `bin/agent-guard check --cwd <dir> -- '<command>'` reproduces a decision without an agent.
Please replace real hosts, names and tokens with placeholders before you paste anything.

## Proposing a rule

Open an issue first and describe:

1. **The incident.** What an agent actually did and what it cost. Rules that come from real damage are the ones worth
   their false positives.
2. **The safe alternative.** A refusal has to tell the agent what to run instead. If there is no safe alternative, a
   refusal only makes the agent try something worse.
3. **What must keep passing.** The nearest commands that look alike and are fine.

Prefer a rewrite to a refusal when the safe form is unambiguous (`--force-with-lease`, `pkill -A`). Anything specific to
one company (namespaces, task keys, tools) belongs in configuration, not in code.

## Development

```sh
git clone https://github.com/ly-solutions/agent-guard && cd agent-guard
python -m pip install pytest ruff
python -m pytest
ruff check . && ruff format --check .
```

- Python 3.10+ and the standard library only. Keep it that way: the hooks start once per tool call.
- Every rule change comes with tests on both sides: commands that must be refused **and** look-alikes that must pass
  (see `DANGEROUS` and `NORMAL` in `tests/test_bash_guard.py`).
- Refusal texts are written for the agent: the reason, then the command to run instead. New texts go into both
  catalogs in `agent_guard/messages.py` (English is required; if you cannot write the Russian one, copy the English text
  and say so in the PR).
- A hook must never fail closed. Errors are swallowed in `hooks/run.py`; do not add code paths that block on failure.

## Pull requests

Keep a PR to one change, describe the behaviour before and after, and add a line to `CHANGELOG.md` under *Unreleased*.
By contributing you agree that your contribution is licensed under the Apache License 2.0.
