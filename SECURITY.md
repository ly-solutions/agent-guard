# Security policy

## Reporting a vulnerability

Please report security issues **privately** through GitHub:
[Report a vulnerability](https://github.com/ly-solutions/agent-guard/security/advisories/new)
(the *Security* tab of this repository → *Report a vulnerability*). Do not open a public issue.

We aim to acknowledge a report within three working days and to agree on a fix and a disclosure date with you.

## What counts

agent-guard is a guard against honest mistakes of coding agents, not a sandbox (see the FAQ in the README). These are
in scope:

- a command or write that a rule is documented to refuse but that gets through, for example a wrapper, quoting or
  substitution that hides a command from the rules;
- a way for content the agent reads (a repository, a file, a tool result) to switch off or weaken the guards;
- a crash or slowdown that a crafted command can trigger;
- anything that leaks data from the hook process (agent-guard never makes network calls).

Out of scope: an agent deliberately working around the guard, for example by writing a script to a file and running it.
Use OS-level isolation for that.

## Supported versions

Fixes are released for the latest minor version.
