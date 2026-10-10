"""Context-window management.

Agents assemble prompts from many sources (request, plan, snippets, prior
outputs, memories). ``ContextBuilder`` packs sections in priority order into a
character budget (roughly 4 characters per token) and truncates the lowest
priority sections first, recording what was dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from orchestra.security.injection import wrap_untrusted


@dataclass
class _Section:
    title: str
    body: str
    priority: int
    untrusted_source: str | None


@dataclass
class ContextBuilder:
    budget_chars: int = 60_000
    sections: list[_Section] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)

    def add(self, title: str, body: str, *, priority: int = 50) -> ContextBuilder:
        if body:
            self.sections.append(_Section(title, body, priority, None))
        return self

    def add_untrusted(self, title: str, body: str, *, source: str, priority: int = 40) -> ContextBuilder:
        if body:
            self.sections.append(_Section(title, body, priority, source))
        return self

    def render(self) -> str:
        remaining = self.budget_chars
        kept: list[tuple[int, str]] = []
        order = sorted(range(len(self.sections)), key=lambda i: -self.sections[i].priority)
        for idx in order:
            sec = self.sections[idx]
            body = wrap_untrusted(sec.body, sec.untrusted_source) if sec.untrusted_source else sec.body
            block = f"## {sec.title}\n{body}\n"
            if len(block) > remaining:
                if remaining < 400:
                    self.dropped.append(sec.title)
                    continue
                block = block[: remaining - 40] + "\n…[truncated to fit context budget]\n"
                if sec.untrusted_source:
                    block += "</untrusted_document>\n"
                self.dropped.append(f"{sec.title} (truncated)")
            remaining -= len(block)
            kept.append((idx, block))
        kept.sort()
        return "\n".join(block for _, block in kept)
