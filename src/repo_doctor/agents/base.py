"""Agent base class and the shared LLM -> validated Finding pipeline."""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ValidationError, field_validator

from repo_doctor.core.models import Finding, ProjectProfile, Severity, Verification
from repo_doctor.core.settings import Settings
from repo_doctor.providers import LLMProvider, ProviderError
from repo_doctor.security.redaction import redact
from repo_doctor.tools.file_reader import RepoReader

JSON_FORMAT = """Reply with ONLY a JSON object, no prose, in this exact shape:
{"findings": [{"category": "<area>/<subtype>", "severity": "critical|high|medium|low|info",
"title": "...", "description": "...", "file_path": "<path exactly as shown>",
"line": <line number shown in the listing>, "evidence": "<exact code line copied verbatim>",
"suggested_fix": "...", "confidence": <0.0-1.0>}]}
Rules: only report problems evident in the code shown; copy evidence verbatim from the listing;
never invent files or line numbers; if there is nothing to report return {"findings": []}."""

_UNVERIFIED_CONFIDENCE_CAP = 0.3
_ws = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _ws.sub(" ", s).strip()


@dataclass
class AgentContext:
    root: Path
    profile: ProjectProfile
    reader: RepoReader
    settings: Settings
    provider: LLMProvider | None = None
    llm_calls: int = 0
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def llm_enabled(self) -> bool:
        return self.provider is not None and self.settings.agents.max_llm_calls_per_agent > 0


class Agent(ABC):
    name: str
    commands: frozenset[str]  # CLI modes this agent takes part in

    @abstractmethod
    def run(self, ctx: AgentContext) -> list[Finding]: ...


class _Raw(BaseModel):
    category: str = ""
    severity: str = "low"
    title: str
    description: str = ""
    file_path: str | None = None
    line: int | None = None
    evidence: str = ""
    suggested_fix: str = ""
    confidence: float = 0.5

    @field_validator("line", mode="before")
    @classmethod
    def _line(cls, v):
        try:
            return int(v) if v not in (None, "", 0) else None
        except (TypeError, ValueError):
            return None

    @field_validator("confidence", mode="before")
    @classmethod
    def _conf(cls, v):
        try:
            return min(1.0, max(0.0, float(v)))
        except (TypeError, ValueError):
            return 0.5

    @field_validator("evidence", "description", "suggested_fix", "category", mode="before")
    @classmethod
    def _str(cls, v):
        return "" if v is None else str(v)


def extract_json(text: str) -> dict | None:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = [fenced.group(1)] if fenced else []
    if "{" in text and "}" in text:
        candidates.append(text[text.index("{") : text.rindex("}") + 1])
    for c in candidates:
        try:
            data = json.loads(c)
            if isinstance(data, dict):
                return data
        except ValueError:
            continue
    return None


def locate_evidence(lines: list[str], evidence: str, near: int | None) -> int | None:
    """1-based line whose text contains the first line of `evidence`; nearest to `near`."""
    first = next((_norm(x) for x in evidence.split("\n") if _norm(x)), "")
    if len(first) < 4:
        return None
    hits = [i for i, ln in enumerate(lines, 1) if first in _norm(ln)]
    if not hits:
        return None
    return min(hits, key=lambda i: abs(i - near)) if near else hits[0]


def _to_finding(raw: _Raw, agent: str, default_cat: str, sent: dict[str, tuple[int, int]],
                reader: RepoReader, require_file: bool) -> Finding | None:
    path = reader.normalize(raw.file_path)
    if raw.file_path and (path is None or path not in sent):
        return None  # invented / unsent file
    if require_file and path is None:
        return None
    line, status, conf = raw.line, Verification.UNVERIFIED, raw.confidence
    evidence = redact(raw.evidence)[:600]
    if path:
        lo, hi = sent[path]
        if line is not None and not lo <= line <= hi:
            line = None
        found = locate_evidence(reader.lines(path), raw.evidence, line) if raw.evidence else None
        if found is not None and lo <= found <= hi:
            line, status = found, Verification.SUPPORTED
        else:
            conf = min(conf, _UNVERIFIED_CONFIDENCE_CAP)
    else:
        conf = min(conf, 0.5)
    try:
        severity = Severity(raw.severity.lower().strip())
    except ValueError:
        severity = Severity.LOW
    if status == Verification.UNVERIFIED and severity in (Severity.CRITICAL, Severity.HIGH):
        severity = Severity.MEDIUM  # unproven claims can't be top severity
    category = _norm(raw.category) or default_cat
    return Finding(
        id="tmp", agent=agent, category=category, severity=severity,
        title=redact(_norm(raw.title))[:200], description=redact(raw.description)[:1500],
        file_path=path, line=line, evidence=evidence,
        suggested_fix=redact(raw.suggested_fix)[:1500], confidence=conf, verification=status,
    )


def ask_findings(ctx: AgentContext, agent: str, default_cat: str, system: str, prompt: str,
                 sent: dict[str, tuple[int, int]], require_file: bool = True) -> list[Finding]:
    """One LLM call (+1 retry on malformed JSON) -> validated findings. May raise ProviderError."""
    assert ctx.provider is not None
    data = None
    for attempt in range(2):
        extra = "" if attempt == 0 else "\n\nYour previous reply was not valid JSON. Reply with ONLY the JSON object."
        ctx.llm_calls += 1
        data = extract_json(ctx.provider.complete(system, prompt + extra))
        if data is not None and isinstance(data.get("findings"), list):
            break
        data = None
    if data is None:
        raise ProviderError(f"{agent}: model did not return valid JSON")
    out, dropped = [], 0
    for item in data["findings"]:
        try:
            f = _to_finding(_Raw.model_validate(item), agent, default_cat, sent, ctx.reader, require_file)
        except (ValidationError, TypeError):
            f = None
        if f is None:
            dropped += 1
        else:
            out.append(f)
    if dropped:
        ctx.notes.append(f"{agent}: dropped {dropped} model finding(s) with invalid or unsent file references.")
    return out


def build_batches(ctx: AgentContext, files: list[str]) -> tuple[list[tuple[str, dict[str, tuple[int, int]]]], int]:
    """Pack file chunks into prompt batches. Returns (batches, files_covered)."""
    budget = ctx.settings.agents.batch_chars
    max_batches = ctx.settings.agents.max_llm_calls_per_agent
    batches: list[tuple[list[str], dict[str, tuple[int, int]]]] = []
    parts: list[str] = []
    sent: dict[str, tuple[int, int]] = {}
    size, covered = 0, set()

    def flush():
        nonlocal parts, sent, size
        if parts:
            batches.append(("\n\n".join(parts), sent))
        parts, sent, size = [], {}, 0

    for rel in files:
        for lo, hi, text in ctx.reader.chunks(rel, budget):
            block = f"### FILE: {rel} (lines {lo}-{hi})\n{text}"
            if parts and size + len(block) > budget:
                flush()
                if len(batches) >= max_batches:
                    return batches, len(covered)
            parts.append(block)
            size += len(block)
            sent[rel] = (min(lo, sent.get(rel, (lo, hi))[0]), max(hi, sent.get(rel, (lo, hi))[1]))
            covered.add(rel)
    flush()
    return batches[:max_batches], len(covered)
