"""Applying proposed edits and checking them against change policy."""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from orchestra.schemas.agents import FileEdit
from orchestra.security.redaction import contains_secret, sensitive_path

PROTECTED_PREFIXES = (".github/workflows/", ".github/actions/", ".gitlab-ci", ".circleci/")
MAX_EDITS = 20
MAX_FILE_CHARS = 200_000


@dataclass
class PatchResult:
    files: dict[str, str] = field(default_factory=dict)  # path -> new content (changed files only)
    errors: list[str] = field(default_factory=list)
    policy_violations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and not self.policy_violations


def check_path(path: str) -> str | None:
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts or not path.strip():
        return f"{path}: path must be relative and inside the repository"
    if any(path.startswith(prefix) for prefix in PROTECTED_PREFIXES):
        return f"{path}: CI/CD configuration cannot be modified by agents"
    if sensitive_path(path):
        return f"{path}: credential or secret files cannot be modified by agents"
    return None


def apply_edits(original: dict[str, str], edits: list[FileEdit]) -> PatchResult:
    result = PatchResult()
    if len(edits) > MAX_EDITS:
        result.policy_violations.append(f"Too many edits ({len(edits)} > {MAX_EDITS})")
        return result
    working: dict[str, str] = {}
    for edit in edits:
        if violation := check_path(edit.path):
            result.policy_violations.append(violation)
            continue
        if contains_secret(edit.replace):
            result.policy_violations.append(f"{edit.path}: replacement text appears to contain a credential")
            continue
        current = working.get(edit.path, original.get(edit.path))
        if edit.find == "":
            if current is not None:
                result.errors.append(f"{edit.path}: empty `find` creates a file, but the file already exists")
                continue
            working[edit.path] = edit.replace
            continue
        if current is None:
            result.errors.append(f"{edit.path}: file does not exist")
            continue
        count = current.count(edit.find)
        if count != 1:
            result.errors.append(f"{edit.path}: `find` text occurs {count} times (must be exactly once)")
            continue
        updated = current.replace(edit.find, edit.replace, 1)
        if len(updated) > MAX_FILE_CHARS:
            result.policy_violations.append(f"{edit.path}: resulting file exceeds {MAX_FILE_CHARS} characters")
            continue
        working[edit.path] = updated
    result.files = {p: c for p, c in working.items() if original.get(p) != c}
    return result


def unified_diff(original: dict[str, str], updated: dict[str, str]) -> dict[str, str]:
    diffs: dict[str, str] = {}
    for path, new in updated.items():
        old = original.get(path, "")
        diffs[path] = "".join(
            difflib.unified_diff(
                old.splitlines(keepends=True),
                new.splitlines(keepends=True),
                fromfile=f"a/{path}" if path in original else "/dev/null",
                tofile=f"b/{path}",
            )
        )
    return diffs


def added_lines(diff: str) -> list[str]:
    return [ln[1:] for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
