## What changes

<!-- Behaviour before and after, in one or two sentences. -->

## Why

<!-- The incident or false positive behind it. Link the issue if there is one. -->

## Checklist

- [ ] Tests for commands that must be refused **and** look-alikes that must pass
- [ ] Refusal texts tell the agent what to run instead (English, plus Russian if you can)
- [ ] Nothing company-specific in code; new knobs are in `agent_guard/config.py` and `examples/config.toml`
- [ ] `python -m pytest` and `ruff check . && ruff format --check .` pass
- [ ] `CHANGELOG.md` updated under *Unreleased*
