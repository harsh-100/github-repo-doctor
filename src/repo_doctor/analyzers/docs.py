"""Read README-style documentation into structured evidence. Pure text processing: nothing is executed."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from repo_doctor.security.redaction import redact

_HEADING = re.compile(r"^(#{1,4})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)\s*([\w+-]*)")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*\S)")
_TAG_ONLY = re.compile(r"^\s*(<[^>]+>\s*)+$")
_BADGE = re.compile(r"^\s*(\[!\[|!\[|\[\s*<img)")
_CMD_START = re.compile(
    r"^\s*(?:\$\s+|>\s+)?(pip3?|python3?|uv|pipx|npm|npx|yarn|pnpm|bun|node|git|brew|apt(?:-get)?|cargo|go|make|docker|"
    r"claude|poetry|conda|curl|pytest|repo-doctor|export|set|cd|source|\./|/[a-z][\w-]*)\b", re.IGNORECASE)
_ENV_VAR = re.compile(r"\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_(?:API_KEY|API_TOKEN|TOKEN|SECRET|KEY|URL))\b")
_PAID = re.compile(r"(?i)\b(subscription|paid|pricing|api credits?|credits|billing|pay[- ]per|free tier|no free tier|"
                   r"requires? an? (?:api key|account))\b")

SECTION_KINDS = {
    "prerequisites": r"prereq|requirement|before you (?:begin|start)|what you need|dependencies",
    "install": r"install|setup|set up|getting started|quick ?start|get started",
    "configure": r"config|environment|api key|credential|settings|authenticat",
    "usage": r"usage|\buse\b|how to use|running|commands|examples?|workflow",
    "limitations": r"limitation|known issue|caveat|not supported|disclaimer|warning",
    "features": r"feature|what (?:it|this) does|capabilit|highlights|what you can",
    "plans": r"roadmap|planned|future|todo|coming soon|wishlist",
    "problem": r"why|problem|motivation|background|about",
    "audience": r"who (?:is|are)|audience|for whom|intended|use cases?",
}


@dataclass
class Section:
    title: str
    level: int
    text: str
    code_blocks: list[tuple[str, list[str]]] = field(default_factory=list)  # (lang, lines)
    bullets: list[str] = field(default_factory=list)


def parse_sections(markdown: str) -> list[Section]:
    """Split Markdown into sections by heading, ignoring `#` lines inside fenced code."""
    sections = [Section("", 0, "")]
    in_fence, lang, block = False, "", []
    for line in markdown.splitlines():
        fence = _FENCE.match(line)
        if fence:
            if in_fence:
                sections[-1].code_blocks.append((lang, block))
                in_fence, block = False, []
            else:
                in_fence, lang = True, fence.group(2).lower()
            sections[-1].text += line + "\n"
            continue
        if in_fence:
            block.append(line)
            sections[-1].text += line + "\n"
            continue
        h = _HEADING.match(line)
        if h:
            sections.append(Section(re.sub(r"[`*_]|\[|\]\([^)]*\)", "", h.group(2)).strip(), len(h.group(1)), ""))
            continue
        b = _BULLET.match(line)
        if b:
            sections[-1].bullets.append(b.group(1).strip())
        sections[-1].text += line + "\n"
    return [s for s in sections if s.title or s.text.strip()]


def sections_of_kind(sections: list[Section], kind: str) -> list[Section]:
    rx = re.compile(SECTION_KINDS[kind], re.IGNORECASE)
    return [s for s in sections if s.title and rx.search(s.title)]


def tagline(markdown: str) -> str | None:
    """First real sentence/paragraph of a README (skips badges, images, HTML wrappers, headings)."""
    para: list[str] = []
    in_fence = False
    for line in markdown.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        s = html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>", " ", line))).replace("\xa0", " ")  # README intros are often HTML
        s = re.sub(r"\s+", " ", s).strip()
        if in_fence or not s or s.startswith(("#", "<!--", "|", "---", ">")) or _BADGE.match(s):
            if para:
                break
            continue
        para.append(s)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", " ".join(para))
    text = re.sub(r"[*_`]{1,3}", "", text).strip()
    return redact(text[:400]) if len(text) >= 20 else None


SHELL_LANGS = {"bash", "sh", "shell", "console", "zsh", "terminal", "powershell", "cmd", "bat"}


def commands_in(section: Section, limit: int = 8, any_command: bool = False) -> list[str]:
    """Shell-looking lines from a section's fenced code blocks (shown to the user, never run).

    `any_command` accepts every non-comment line of a shell-tagged block, so a project's own CLI (`taskly add`) is
    listed; otherwise only lines starting with a well-known tool are kept."""
    out: list[str] = []
    for lang, lines in section.code_blocks:
        if lang in {"json", "yaml", "yml", "toml", "text", "md", "markdown", "html", "xml", "ini", "diff"}:
            continue
        for line in lines:
            s = line.strip()
            if s and not s.startswith("#") and (_CMD_START.match(s) or (any_command and lang in SHELL_LANGS)):
                out.append(redact(re.sub(r"^\$\s+", "", s))[:200])
    return list(dict.fromkeys(out))[:limit]


def clean_bullet(raw: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", raw)
    return re.sub(r"[*_`]{1,3}", "", text).strip()


def short_bullets(section: Section, limit: int = 10) -> list[str]:
    out = []
    for b in section.bullets:
        text = clean_bullet(b)
        if 6 <= len(text) <= 300:
            out.append(redact(text))
    return out[:limit]


def env_vars_mentioned(text: str) -> list[str]:
    return sorted(set(_ENV_VAR.findall(text)))


def paid_mentions(text: str, limit: int = 4) -> list[str]:
    """Sentences that mention cost, subscriptions or required accounts (quoted, shortened)."""
    out = []
    for line in text.splitlines():
        if line.lstrip().startswith(("#", "|", "```", "<", ">", "Docs:")) or "·" in line:
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            s = re.sub(r"[*_`>|]", "", sentence).strip()
            if 15 <= len(s) <= 300 and len(s.split()) >= 6 and _PAID.search(s):
                out.append(redact(re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)))
    return list(dict.fromkeys(out))[:limit]
