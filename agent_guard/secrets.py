"""What a live credential looks like, and what a secrets file is called."""

from __future__ import annotations

import re

TOKEN_PATTERNS = [
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",  # PEM private key
    r"\bsk-ant-[\w-]{20,}",  # Anthropic
    r"\bsk-(?:proj-)?[A-Za-z0-9_-]{40,}",  # OpenAI
    r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_\w{22,})",  # GitHub
    r"\bglpat-[\w-]{20,}",  # GitLab personal access token
    r"\bperm[:-][A-Za-z0-9=_+/-]{4,}\.[A-Za-z0-9=_+/-]{4,}\.[A-Za-z0-9=_+/-]{10,}",  # YouTrack permanent token
    r"\bhvs\.[A-Za-z0-9_-]{24,}",  # HashiCorp Vault / OpenBao service token
    r"\bxox[abprs]-[\w-]{10,}",  # Slack
    r"\b\d{8,10}:AA[\w-]{33}(?![\w-])",  # Telegram bot token
    r"\bAKIA[0-9A-Z]{16}\b",  # AWS access key id
    r"\bAIza[\w-]{35}",  # Google API key
]
# A match that contains one of these words is an example, not a credential.
FAKE = re.compile(r"fake|example|dummy|placeholder|x{8,}|0{8,}|redacted", re.I)

SECRET_FILE = re.compile(
    r"(^|/)(\.env(\.[\w.-]+)?|\.npmrc|\.netrc|\.pgpass|credentials(\.json)?|secrets?\.(ya?ml|json|env)"
    r"|[^/]+\.(pem|key|p12|pfx)|id_(rsa|ed25519|ecdsa))$"
)
SECRET_FILE_OK = re.compile(r"(^|/)\.env\.(example|sample|template|dist)$")
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|fixtures?|testdata|__fixtures__)/")


def token_res(cfg: dict) -> list[re.Pattern[str]]:
    return [re.compile(p) for p in TOKEN_PATTERNS + list(cfg["secrets"].get("extra_token_patterns") or [])]


def is_secret_file(cfg: dict, path: str) -> bool:
    if SECRET_FILE_OK.search(path):
        return False
    if SECRET_FILE.search(path):
        return True
    return any(re.search(p, path) for p in cfg["secrets"].get("extra_file_patterns") or [])


def real_tokens(cfg: dict, text: str, test_file: bool = False) -> bool:
    """True when the text holds something that looks like a live credential."""
    for rx in token_res(cfg):
        for m in rx.finditer(text or ""):
            if FAKE.search(m.group(0)) or (test_file and FAKE.search(text)):
                continue
            return True
    return False
