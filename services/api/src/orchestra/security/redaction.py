"""Secret redaction for logs, events, tool arguments, and memory.

Redaction is best-effort pattern matching. It is a second line of defense: the
primary control is that secrets are never placed into model context or events.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

REDACTED = "[REDACTED]"

_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),  # GitHub tokens
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),  # Anthropic keys
    re.compile(r"sk-[A-Za-z0-9]{32,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key id
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),  # Slack
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/]{16,}=*"),
    re.compile(
        r"(?i)((?:password|passwd|secret|api[_-]?key|access[_-]?token|client[_-]?secret)"
        r"\s*[:=]\s*)['\"]?[^\s'\",]{6,}"
    ),
]

_SENSITIVE_KEYS = {
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "password",
    "secret",
    "api_key",
    "apikey",
    "client_secret",
    "github_token",
    "anthropic_api_key",
}

_extra_literals: set[str] = set()


def register_secret(value: str | None) -> None:
    """Register a literal secret value (e.g. a configured token) for exact-match redaction."""
    if value and len(value) >= 8:
        _extra_literals.add(value)


def redact_text(text: str) -> str:
    for literal in _extra_literals:
        if literal in text:
            text = text.replace(literal, REDACTED)
    for pattern in _PATTERNS:
        if pattern.groups:
            text = pattern.sub(lambda m: m.group(1) + REDACTED, text)
        else:
            text = pattern.sub(REDACTED, text)
    return text


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Recursively redact secrets in JSON-like data."""
    if _depth > 12:
        return value
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {
            k: (REDACTED if str(k).lower() in _SENSITIVE_KEYS and v else redact(v, _depth=_depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v, _depth=_depth + 1) for v in value]
    return value


def contains_secret(text: str) -> bool:
    return redact_text(text) != text


def sensitive_path(path: str, extra: Iterable[str] = ()) -> bool:
    """Paths whose content must never be read into model context or memory."""
    lowered = path.lower()
    name = lowered.rsplit("/", 1)[-1]
    if name in {".env", ".npmrc", ".pypirc", ".netrc", "id_rsa", "id_ed25519", "credentials.json"}:
        return True
    if name.startswith(".env.") and not name.endswith((".example", ".sample", ".template")):
        return True
    if name.endswith((".pem", ".key", ".p12", ".pfx", ".pickle", ".keystore")):
        return True
    return any(lowered.startswith(p) for p in extra)
