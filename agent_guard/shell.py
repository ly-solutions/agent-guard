"""Just enough shell parsing to see which commands a command line really runs.

Not a shell parser: it cuts a command line into top-level commands (heredoc bodies and
quoted text are not commands), and peels off the wrappers and keywords that stand in
front of the real command. Without that, ``timeout 40 kubectl …``, ``for …; do kubectl …``
and ``xargs kubectl …`` look like ``timeout``, ``do`` and ``xargs`` and slip past every rule.
"""

from __future__ import annotations

import re
import shlex
from typing import NamedTuple

HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n\s*\2[ \t]*(?=\n|$)", re.S)

SHELL_KEYWORDS = {"do", "then", "else", "elif", "if", "while", "until", "!", "{", "("}
PLAIN_WRAPPERS = {"env", "command", "exec", "time", "nohup", "setsid", "builtin"}
# wrapper -> its flags that take a separate value
FLAG_WRAPPERS = {
    "sudo": {"-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-U"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "-n", "-p", "--class", "--classdata"},
    "stdbuf": {"-i", "-o", "-e"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "xargs": {"-I", "-n", "-P", "-L", "-d", "-s", "-E", "-a", "--max-args", "--max-procs", "--delimiter", "--replace", "--arg-file"},
    "watch": {"-n", "--interval"},
}
# Wrappers that exec a binary directly and so skip a shell function of the same name.
FUNCTION_BYPASS = {"env", "command", "exec", "builtin", "nohup", "setsid"} | set(FLAG_WRAPPERS)


class Words(NamedTuple):
    tokens: list[str]
    behind_wrapper: bool = False


def split_segments(command: str) -> list[str]:
    """Top-level commands: heredoc bodies dropped, split on unquoted ``;`` ``&&`` ``||`` ``|`` and newlines."""
    text = HEREDOC.sub(" ", command).replace("\\\n", " ")
    out: list[str] = []
    cur: list[str] = []
    quote = None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
            elif ch == "\\" and quote == '"' and i + 1 < len(text):
                cur.append(text[i + 1])
                i += 1
        elif ch in ("'", '"'):
            quote = ch
            cur.append(ch)
        elif ch == "\\" and i + 1 < len(text):
            cur.append(text[i : i + 2])
            i += 1
        elif ch in ";\n" or text.startswith("&&", i) or text.startswith("||", i) or ch == "|":
            out.append("".join(cur))
            cur = []
            if text.startswith("&&", i) or text.startswith("||", i):
                i += 1
        else:
            cur.append(ch)
        i += 1
    out.append("".join(cur))
    return [seg.strip() for seg in out if seg.strip()]


def unwrap(tokens: list[str]) -> Words:
    """Drop assignments, keywords and wrappers (``sudo -u x nice -n 5 timeout 30 …``) in front of the real command."""
    behind = False
    while tokens:
        head = tokens[0].lstrip("(`")
        if head.startswith("$("):
            head = head[2:]
        if head != tokens[0]:
            tokens = ([head] if head else []) + tokens[1:]
            continue
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", head) or head in SHELL_KEYWORDS or head in PLAIN_WRAPPERS:
            behind = behind or head in FUNCTION_BYPASS
            tokens = tokens[1:]
        elif head in FLAG_WRAPPERS:
            behind = True
            value_flags, rest = FLAG_WRAPPERS[head], tokens[1:]
            while rest and rest[0].startswith("-"):
                rest = rest[2:] if rest[0] in value_flags else rest[1:]
            if head == "timeout" and rest:
                rest = rest[1:]  # the duration
            tokens = rest
        else:
            break
    return Words(tokens, behind)


def words(segment: str) -> Words:
    try:
        tokens = shlex.split(segment, comments=False)
    except ValueError:
        tokens = segment.split()
    return unwrap(tokens)


def substitutions(segment: str) -> list[str]:
    """Bodies of ``$(...)`` and backticks: ``P=$(kubectl -n prod delete pod x)`` is a command too."""
    out: list[str] = []
    i, quote = 0, None
    while i < len(segment):
        ch = segment[i]
        if quote == "'":
            quote = None if ch == "'" else quote
        elif ch == "'" and quote is None:
            quote = "'"
        elif ch == "\\":
            i += 1
        elif segment.startswith("$(", i) and not segment.startswith("$((", i):
            depth, j = 1, i + 2
            while j < len(segment) and depth:
                depth += segment[j] == "("
                depth -= segment[j] == ")"
                j += 1
            out.append(segment[i + 2 : j - 1] if not depth else segment[i + 2 :])
            i = j - 1
        elif ch == "`":
            j = segment.find("`", i + 1)
            if j > i:
                out.append(segment[i + 1 : j])
                i = j
        i += 1
    return [body for body in out if body.strip()]


def git_sub(tokens: list[str]) -> tuple[str | None, list[str]]:
    """``git -C dir -c k=v push -f`` -> ("push", ["-f"])."""
    if tokens[:1] != ["git"]:
        return None, []
    rest = tokens[1:]
    while rest and rest[0].startswith("-"):
        rest = rest[2:] if rest[0] in ("-C", "-c") else rest[1:]
    return (rest[0], rest[1:]) if rest else (None, [])


def namespace(args: list[str]) -> str | None:
    for i, a in enumerate(args):
        if a in ("-n", "--namespace") and i + 1 < len(args):
            return args[i + 1]
        if a.startswith("--namespace="):
            return a.split("=", 1)[1]
        if a.startswith("-n") and len(a) > 2 and not a.startswith("--"):
            return a[2:]
    return None
