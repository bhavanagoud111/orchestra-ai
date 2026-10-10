"""Targeted repository retrieval.

Instead of loading a whole repository into model context, the Repository
Intelligence agent:

1. lists the tree (one API call),
2. ranks *paths* against query terms and stack-trace references,
3. fetches only the top candidate files (bounded count and size),
4. splits them into overlapping line chunks, and
5. ranks chunks with BM25 so only the best snippets reach the model.
"""

from __future__ import annotations

import json
import math
import re
import tomllib
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from orchestra.integrations.github.protocol import CodeHost, CodeHostError, TreeEntry
from orchestra.security.redaction import contains_secret, sensitive_path

TEXT_EXTENSIONS = {
    ".py",
    ".pyi",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".rb",
    ".php",
    ".cs",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".swift",
    ".scala",
    ".sql",
    ".sh",
    ".md",
    ".rst",
    ".txt",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".cfg",
    ".ini",
    ".html",
    ".css",
}
LANGUAGE_BY_EXT = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".cpp": "C++",
    ".c": "C",
    ".swift": "Swift",
    ".scala": "Scala",
}
SKIP_DIRS = ("node_modules/", "vendor/", "dist/", "build/", ".git/", "__pycache__/", ".venv/", "venv/", ".next/")
ENTRY_NAMES = {
    "main.py",
    "__main__.py",
    "app.py",
    "api.py",
    "cli.py",
    "server.py",
    "index.ts",
    "index.js",
    "main.go",
    "main.rs",
    "manage.py",
}

_STOP = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "to",
    "of",
    "in",
    "on",
    "for",
    "is",
    "it",
    "be",
    "with",
    "when",
    "this",
    "that",
    "from",
    "as",
    "at",
    "by",
    "fix",
    "bug",
    "error",
    "issue",
    "please",
    "should",
    "we",
    "our",
    "i",
    "not",
    "are",
    "was",
    "has",
    "have",
    "can",
    "do",
    "does",
    "self",
    "return",
    "def",
    "import",
    "file",
    "line",
    "most",
    "recent",
    "call",
    "last",
    "traceback",
    "investigate",
    "why",
}
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")
_TRACE_FILE = re.compile(r'File "([^"]+)", line (\d+)(?:, in (\w+))?')
_PATH_REF = re.compile(r"\b([\w\-./]+\.(?:py|ts|tsx|js|go|rs|java|rb))(?::(\d+))?")


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in _TOKEN.findall(text):
        parts = [raw, *re.split(r"_|(?<=[a-z])(?=[A-Z])", raw)]
        for p in parts:
            p = p.lower()
            if len(p) > 1 and p not in _STOP:
                tokens.append(p)
    return tokens


@dataclass
class TraceFrame:
    path: str
    line: int
    function: str | None


def parse_stack_trace(text: str) -> list[TraceFrame]:
    frames = [TraceFrame(m.group(1), int(m.group(2)), m.group(3)) for m in _TRACE_FILE.finditer(text)]
    if not frames:
        frames = [TraceFrame(m.group(1), int(m.group(2) or 0), None) for m in _PATH_REF.finditer(text)]
    return frames


def error_signature(text: str) -> str | None:
    match = re.search(r"^\s*([A-Z]\w*(?:Error|Exception|Fault))(?::\s*(.+))?$", text, re.MULTILINE)
    if not match:
        return None
    return f"{match.group(1)}: {match.group(2).strip()}" if match.group(2) else match.group(1)


def extract_terms(text: str, limit: int = 24) -> list[str]:
    counts = Counter(tokenize(text))
    for frame in parse_stack_trace(text):
        for token in tokenize(frame.path.rsplit(".", 1)[0]):
            counts[token] += 2
        if frame.function:
            counts[frame.function.lower()] += 5
    return [t for t, _ in counts.most_common(limit)]


@dataclass
class Chunk:
    path: str
    start_line: int
    end_line: int
    text: str
    score: float = 0.0
    tokens: Counter[str] = field(default_factory=Counter, repr=False)


def chunk_text(path: str, content: str, window: int = 40, overlap: int = 10) -> list[Chunk]:
    lines = content.splitlines()
    if not lines:
        return []
    chunks: list[Chunk] = []
    step = max(window - overlap, 1)
    for start in range(0, len(lines), step):
        part = lines[start : start + window]
        text = "\n".join(part)
        chunks.append(Chunk(path, start + 1, start + len(part), text, tokens=Counter(tokenize(path + " " + text))))
        if start + window >= len(lines):
            break
    return chunks


def bm25(chunks: list[Chunk], query: list[str], k1: float = 1.4, b: float = 0.75) -> None:
    if not chunks or not query:
        return
    n = len(chunks)
    avg_len = sum(sum(c.tokens.values()) for c in chunks) / n or 1
    df = Counter(t for c in chunks for t in set(c.tokens) if t in query)
    for c in chunks:
        length = sum(c.tokens.values()) or 1
        score = 0.0
        for term in query:
            tf = c.tokens.get(term, 0)
            if not tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / avg_len))
        c.score = score


def _ext(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return "." + name.rsplit(".", 1)[-1] if "." in name else name


@dataclass
class RepoSnapshot:
    full_name: str
    ref: str
    files: list[TreeEntry]
    languages: dict[str, int]
    dependencies: list[str]
    entry_points: list[str]
    docs: list[str]
    tests: list[str]


class RepositoryReader:
    """Bounded, policy-aware read access to one repository revision."""

    def __init__(
        self,
        host: CodeHost,
        full_name: str,
        ref: str,
        *,
        max_files: int = 400,
        max_file_bytes: int = 120_000,
    ) -> None:
        self.host = host
        self.full_name = full_name
        self.ref = ref
        self.max_files = max_files
        self.max_file_bytes = max_file_bytes
        self._tree: list[TreeEntry] | None = None
        self._cache: dict[str, str] = {}
        self.skipped: list[str] = []

    async def tree(self) -> list[TreeEntry]:
        if self._tree is None:
            entries = await self.host.get_tree(self.full_name, self.ref)
            self._tree = [
                e
                for e in entries
                if e.type == "blob" and not any(e.path.startswith(d) or f"/{d}" in e.path for d in SKIP_DIRS)
            ]
        return self._tree

    async def read(self, path: str) -> str | None:
        if path in self._cache:
            return self._cache[path]
        if sensitive_path(path):
            self.skipped.append(f"{path} (sensitive path)")
            return None
        entry = next((e for e in await self.tree() if e.path == path), None)
        if entry is None:
            return None
        if entry.size and entry.size > self.max_file_bytes:
            self.skipped.append(f"{path} (too large: {entry.size} bytes)")
            return None
        try:
            content = await self.host.read_file(self.full_name, path, self.ref)
        except CodeHostError:
            return None
        if contains_secret(content):
            self.skipped.append(f"{path} (appears to contain credentials)")
            return None
        self._cache[path] = content
        return content

    async def snapshot(self) -> RepoSnapshot:
        tree = await self.tree()
        languages: Counter[str] = Counter()
        for e in tree:
            lang = LANGUAGE_BY_EXT.get(_ext(e.path))
            if lang:
                languages[lang] += 1
        paths = [e.path for e in tree]
        deps = await self._dependencies(paths)
        entry_points = [p for p in paths if p.rsplit("/", 1)[-1] in ENTRY_NAMES][:10]
        docs = [p for p in paths if p.lower().endswith((".md", ".rst")) and not p.startswith(".github/")][:20]
        tests = [p for p in paths if re.search(r"(^|/)(tests?/|test_|.*_test\.|.*\.test\.|.*\.spec\.)", p)][:50]
        return RepoSnapshot(self.full_name, self.ref, tree, dict(languages), deps, entry_points, docs, tests)

    async def _dependencies(self, paths: list[str]) -> list[str]:
        deps: list[str] = []
        if "pyproject.toml" in paths and (raw := await self.read("pyproject.toml")):
            try:
                project = tomllib.loads(raw).get("project", {})
                deps += project.get("dependencies", [])
                for extra in project.get("optional-dependencies", {}).values():
                    deps += extra
            except tomllib.TOMLDecodeError:
                pass
        if "requirements.txt" in paths and (raw := await self.read("requirements.txt")):
            deps += [ln.strip() for ln in raw.splitlines() if ln.strip() and not ln.startswith("#")]
        if "package.json" in paths and (raw := await self.read("package.json")):
            try:
                pkg = json.loads(raw)
                deps += list(pkg.get("dependencies", {})) + list(pkg.get("devDependencies", {}))
            except json.JSONDecodeError:
                pass
        if "go.mod" in paths and (raw := await self.read("go.mod")):
            deps += [ln.split()[0] for ln in raw.splitlines() if ln.startswith("\t")]
        return deps[:60]

    async def search(
        self, query_terms: list[str], *, top_k: int = 8, priority_paths: Sequence[str] = (), candidate_files: int = 25
    ) -> list[Chunk]:
        tree = await self.tree()
        query = [t.lower() for t in query_terms if t]
        priority = {p.lstrip("./") for p in priority_paths}

        def path_score(entry: TreeEntry) -> float:
            if _ext(entry.path) not in TEXT_EXTENSIONS:
                return -1
            score = sum(2.0 for t in query if t in entry.path.lower())
            if any(entry.path.endswith(p) or p.endswith(entry.path) for p in priority):
                score += 10
            if _ext(entry.path) in LANGUAGE_BY_EXT:
                score += 0.5
            return score

        ranked = sorted((e for e in tree if path_score(e) >= 0), key=path_score, reverse=True)
        chunks: list[Chunk] = []
        for entry in ranked[: min(candidate_files, self.max_files)]:
            content = await self.read(entry.path)
            if content:
                chunks.extend(chunk_text(entry.path, content))
        bm25(chunks, query)
        for c in chunks:
            if any(c.path.endswith(p) or p.endswith(c.path) for p in priority):
                c.score += 2.0
        chunks.sort(key=lambda c: c.score, reverse=True)
        best: list[Chunk] = []
        for c in chunks:
            if c.score <= 0 or len(best) >= top_k:
                break
            # skip heavily-overlapping chunks from the same file
            if any(b.path == c.path and not (c.end_line < b.start_line or c.start_line > b.end_line) for b in best):
                continue
            best.append(c)
        return best
