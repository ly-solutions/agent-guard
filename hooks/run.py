#!/usr/bin/env python3
"""Hook entry point: `run.py pre-bash|pre-write|post-bash|session` reads the event on stdin.

Silent on any error of its own: a broken guard must never block the agent's work.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> None:
    try:
        from agent_guard.hooks import run

        out = run(sys.argv[1], sys.stdin.read())
    except Exception:
        if os.environ.get("AGENT_GUARD_DEBUG"):
            raise
        return
    if out:
        print(out)


if __name__ == "__main__":
    main()
