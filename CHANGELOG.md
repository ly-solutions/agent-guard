# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-19

First public release.

### Added

- `PreToolUse` guard for shell commands: `force-push` (rewritten to `--force-with-lease`), `pkill-full` (rewritten to
  `pkill -A`), `rm-recursive`, `shared-checkout-write`, `agent-branch-delete`, `kubectl-mutation`, `helm-mutation`,
  `prod-db`, `kill-kubectl-exec`, `literal-token`, `guard-config-write` and `custom` rules. The rules see through
  wrappers, shell keywords, substitutions, `eval` and `bash -c`.
- `PreToolUse` guard for file writes, Codex `apply_patch` included: `secret-file-write`, `token-in-file`,
  `guard-config-write`.
- Helpers: `pipefail-gates`, `clear-proxy`.
- `PostToolUse` advice for a broken production `psql` stream (`orphaned-db-session`).
- Lifetime for detached agent processes (`background-lease`) with the `agent-bg` command.
- Configuration in TOML or JSON, with English and Russian messages.
- Claude Code plugin and marketplace manifests, Codex plugin manifest, and the `agent-guard check` CLI.

[Unreleased]: https://github.com/ly-solutions/agent-guard/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ly-solutions/agent-guard/releases/tag/v0.1.0
