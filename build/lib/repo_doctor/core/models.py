"""Shared data models."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Verification(str, Enum):
    VERIFIED = "verified"        # established by a tool or deterministic check
    SUPPORTED = "supported"      # LLM claim whose quoted evidence exists in the file
    UNVERIFIED = "unverified"    # LLM claim, evidence not located
    SPECULATIVE = "speculative"  # reviewer judged it speculative
    REJECTED = "rejected"        # reviewer or validation rejected it


class Finding(BaseModel):
    """Structured finding (Phase 2+ agents will emit these)."""

    id: str
    agent: str
    category: str
    severity: Severity
    title: str
    description: str
    file_path: str | None = None
    line: int | None = None
    evidence: str = ""
    suggested_fix: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    verification: Verification = Verification.UNVERIFIED
    review_note: str = ""
    explanation: str = ""  # AI plain-language explanation of a tool finding (never alters evidence)


class FileInfo(BaseModel):
    path: str  # POSIX-style, relative to repo root
    size: int
    language: str | None = None
    kind: str = "source"  # source | config | manifest | test | docs | build


class Inventory(BaseModel):
    root: str
    files: list[FileInfo] = Field(default_factory=list)
    skipped: dict[str, int] = Field(default_factory=dict)  # reason -> count
    sensitive_files: list[str] = Field(default_factory=list)  # present but NOT gitignored
    truncated: bool = False


class Dependency(BaseModel):
    name: str
    spec: str = ""
    dev: bool = False
    ecosystem: str  # python | npm
    source: str = ""  # manifest the dependency was declared in


class ProjectProfile(BaseModel):
    name: str
    root: str
    languages: dict[str, int] = Field(default_factory=dict)  # language -> file count
    frameworks: list[str] = Field(default_factory=list)
    package_managers: list[str] = Field(default_factory=list)
    test_frameworks: list[str] = Field(default_factory=list)
    entry_points: list[str] = Field(default_factory=list)
    manifests: list[str] = Field(default_factory=list)
    build_files: list[str] = Field(default_factory=list)
    docs: list[str] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)
    top_level_modules: dict[str, int] = Field(default_factory=dict)  # dir -> file count
    concerns: list[str] = Field(default_factory=list)
    inventory: Inventory


# ---------------------------------------------------------------- project understanding
class Feature(BaseModel):
    text: str
    status: str = "documented"  # in-code | documented | planned
    evidence: list[str] = Field(default_factory=list)  # file paths or "README.md § Section"


class Audience(BaseModel):
    text: str
    basis: str = "inferred"  # documented (quoted from the docs) | inferred (Repo Doctor's reading)


class Tech(BaseModel):
    name: str
    kind: str  # language | framework | library | ai | database | service | tool
    confidence: str  # confirmed (imported/used in source) | tests-only | declared-only | mentioned
    evidence: str = ""


class WorkflowStep(BaseModel):
    title: str
    detail: str = ""
    evidence: list[str] = Field(default_factory=list)


class GettingStarted(BaseModel):
    prerequisites: list[str] = Field(default_factory=list)
    install: list[str] = Field(default_factory=list)
    configure: list[str] = Field(default_factory=list)
    usage: list[str] = Field(default_factory=list)
    paid_or_keys: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class Maturity(BaseModel):
    level: str  # experimental | early development | actively developed | mature-looking
    positives: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    activity: str = ""  # recency evidence, or why it is unknown


class ProjectUnderstanding(BaseModel):
    what: str = ""
    what_source: str = "docs"  # docs | ai | none
    problem: str | None = None
    problem_source: str = "none"  # docs | ai | none
    features: list[Feature] = Field(default_factory=list)
    audience: list[Audience] = Field(default_factory=list)
    technologies: list[Tech] = Field(default_factory=list)
    workflow: list[WorkflowStep] = Field(default_factory=list)
    workflow_source: str = "none"  # docs | components | ai | none
    getting_started: GettingStarted = Field(default_factory=GettingStarted)
    value: str | None = None
    maturity: Maturity | None = None
    evidence_files: list[str] = Field(default_factory=list)
    uncertainty: list[str] = Field(default_factory=list)
    ai_used: bool = False


class ToolRun(BaseModel):
    name: str
    status: str  # ran | skipped | denied | failed
    detail: str = ""
    command: str = ""
    findings: int = 0


class AnalysisResult(BaseModel):
    mode: str
    root: str = ""  # repository that was analysed (used by `fix` to detect mismatches)
    findings: list[Finding] = Field(default_factory=list)
    rejected: list[Finding] = Field(default_factory=list)
    agents_run: list[str] = Field(default_factory=list)
    tools: list[ToolRun] = Field(default_factory=list)
    agent_errors: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    llm_used: bool = False
    llm_calls: int = 0
    duplicates_merged: int = 0
    understanding: ProjectUnderstanding | None = None

    @property
    def counted(self) -> list[Finding]:
        return [f for f in self.findings if f.verification != Verification.SPECULATIVE]

    def severity_counts(self) -> dict[str, int]:
        counts = {s.value: 0 for s in Severity}
        for f in self.counted:
            counts[f.severity.value] += 1
        return counts

    def health(self) -> str:
        c = self.severity_counts()
        if c["critical"]:
            return "Critical"
        if c["high"] or c["medium"] > 5:
            return "Needs Attention"
        if c["medium"] or c["low"]:
            return "Fair"
        return "Healthy"
